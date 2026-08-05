# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Base Connector Abstract Class

Provides the foundation for all data source connectors in AssetAtlas.
All connectors must inherit from this class and implement the required abstract methods.

Created: 2025-11-04
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Iterator, Any
from pathlib import Path
import logging
import time
from datetime import datetime, timezone

from src.modules.integrations.shared import data_filters


class BaseConnector(ABC):
    """
    Abstract base class for all data source connectors.

    Provides common functionality for:
    - Configuration management
    - Authentication
    - Data fetching with pagination
    - Schema discovery
    - Test connection
    - Sample data retrieval

    Connector metadata (class attributes):
    - integration_type: Unique identifier (e.g., "azure_ad", "intune")
    - display_name: Human-readable name (e.g., "Microsoft Entra ID")
    - category: Grouping category (e.g., "Identity Management", "MDM")
    - description: Short description for wizard
    - logo_url: Path to logo image (e.g., "/svg/azure-ad-logo.svg")
    """

    # ===== Connector Metadata (must be defined in subclass) =====
    integration_type: str = ""
    display_name: str = ""
    category: str = ""
    description: str = ""
    logo_url: str = ""
    connector_role: str = "technology"

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        """
        Initialize connector with configuration.

        Args:
            config: Configuration dictionary with connector-specific settings
            logger: Optional logger instance (will create one if not provided)
        """
        self.config = config
        self.logger = logger or self._setup_default_logger()

        # Per-run collection warnings (e.g. paginator cap-hit). Re-initialised
        # at the top of fetch_data() so each run starts clean; readable via
        # the collection_warnings property afterwards.
        self._collection_warnings: List[str] = []

        # Initialize components
        self._validate_metadata()
        self.auth = self._build_auth()
        self.rate_limiter = self._build_rate_limiter()

        # Wire rate limiter into auth if both exist
        if self.auth and self.rate_limiter:
            self.auth.rate_limiter = self.rate_limiter

    # ===== Abstract Methods (MUST be implemented by subclasses) =====

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test connectivity and validate credentials.

        This method should:
        1. Attempt to authenticate
        2. Make a minimal API call to verify access
        3. Return detailed status

        Returns:
            {
                'success': bool,           # True if connection successful
                'message': str,            # Human-readable status message
                'details': dict (optional) # Additional info (e.g., record count)
            }

        Example:
            {
                'success': True,
                'message': 'Successfully connected to Entra ID',
                'details': {
                    'total_devices': 1247,
                    'api_version': 'v1.0'
                }
            }
        """
        pass

    def fetch_data(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """
        Template method: fetch data from source with metadata injection,
        filtering, logging, and stats tracking.

        Connectors implement _collect_records() instead of overriding this.

        Args:
            batch_id: UUID of the ingestion batch (for tracking)

        Yields:
            Individual records as dictionaries with injected metadata
        """
        self._stats = {"total": 0, "filtered": 0, "errors": 0}
        self._collection_warnings = []
        start_time = self.log_execution_start(batch_id)

        try:
            for record in self._collect_records(batch_id):
                self._inject_metadata(record, batch_id)

                if not self._should_include_record(record):
                    self._stats["filtered"] += 1
                    continue

                self._stats["total"] += 1
                yield record

            self.log_sync_metrics(
                batch_id,
                records_fetched=self._stats["total"] + self._stats["filtered"],
                records_processed=self._stats["total"],
                records_failed=self._stats["errors"],
                records_skipped=self._stats["filtered"],
            )
            self.log_execution_end(batch_id, start_time, success=True)

        except Exception as e:
            self.log_execution_end(
                batch_id, start_time, success=False, error_message=str(e)
            )
            raise

    # ===== Collection Warnings =====

    def record_collection_warning(self, message: str) -> None:
        """Log a collection problem at WARNING and record it for this run.

        Recorded warnings survive the fetch loop — integration_service holds
        the connector instance across the run, so anything appended here is
        readable via collection_warnings afterwards.

        A message already recorded for this run is logged again but NOT
        recorded twice: a per-catalog/per-schema paginator (e.g. Databricks
        Unity Catalog) can hit the same cap thousands of times, and the
        recorded list feeds operator-facing state.
        """
        self.logger.warning(message)
        if message not in self._collection_warnings:
            self._collection_warnings.append(message)

    @property
    def collection_warnings(self) -> List[str]:
        """Warnings recorded during the current/last fetch_data() run.

        Returns a copy — callers must not be able to mutate connector state.
        """
        return list(self._collection_warnings)

    def record_truncation_warning(self, label: str, max_pages) -> None:
        """Record that `label` stopped on a page cap rather than on end-of-data.

        For connectors that paginate with their own loop instead of a
        base/pagination.py paginator; warn_if_truncated delegates here so the
        wording lives in exactly one place.
        """
        cap_text = f"the {max_pages}-page cap" if max_pages is not None else "its page cap"
        self.record_collection_warning(
            f"{label} truncated at {cap_text} — more pages were "
            f"available and the remaining records were not collected."
        )

    def warn_if_truncated(self, paginator, label: str) -> None:
        """Relay a paginator's cap-hit into a collection warning.

        Call after exhausting ``paginator.pages(...)`` — the flag is only
        meaningful once the generator is done. A genuine end-of-data leaves
        ``truncated`` False and emits nothing; an abandoned generator (e.g.
        the sample-data preview) is not a cap-hit either.

        Both attributes are read defensively: this is a *warning* helper, and
        an AttributeError raised here would propagate out of the connector's
        record generator and fail the whole run.
        """
        if getattr(paginator, "truncated", False):
            self.record_truncation_warning(
                label, getattr(paginator, "max_pages", None)
            )

    @abstractmethod
    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """
        Collect raw records from the data source.

        This is the method connectors implement. It replaces the old
        abstract fetch_data() — same signature, same responsibility:
        1. Authenticate if needed
        2. Handle pagination automatically
        3. Yield one record at a time (memory efficient)
        4. Handle rate limiting

        The framework (fetch_data) handles metadata injection, filtering,
        logging lifecycle, and stats tracking.

        Args:
            batch_id: UUID of the ingestion batch (for tracking)

        Yields:
            Individual records as dictionaries (raw, without framework metadata)
        """
        pass

    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """
        Get available fields from the data source.

        This method should:
        1. Return all fields available from the source
        2. Include field metadata (type, description, etc.)
        3. Use JSONPath or dot notation for nested fields

        Returns:
            {
                'fields': [
                    {
                        'name': str,           # Field name in source
                        'path': str,           # JSONPath or dot notation
                        'type': str,           # 'string', 'number', 'boolean', 'object', 'array'
                        'description': str,    # Human-readable description
                        'required': bool,      # Whether field is always present
                        'sample_values': list  # Optional: example values
                    },
                    ...
                ]
            }

        Example:
            {
                'fields': [
                    {
                        'name': 'id',
                        'path': 'id',
                        'type': 'string',
                        'description': 'Unique device identifier',
                        'required': True
                    },
                    {
                        'name': 'displayName',
                        'path': 'displayName',
                        'type': 'string',
                        'description': 'Device display name',
                        'required': False,
                        'sample_values': ['DESKTOP-ABC123', 'LAPTOP-XYZ789']
                    }
                ]
            }
        """
        pass

    @classmethod
    @abstractmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """
        Get configuration form schema for the wizard.

        This method defines the form fields needed to configure this connector.
        The wizard will dynamically generate a form based on this schema.

        Returns:
            {
                'field_name': {
                    'type': str,           # 'string', 'password', 'url', 'number', 'select', 'boolean'
                    'label': str,          # Display label
                    'required': bool,      # Whether field is required
                    'default': any,        # Default value (optional)
                    'options': list,       # Options for 'select' type (optional)
                    'help_text': str,      # Help text/tooltip (optional)
                    'placeholder': str,    # Placeholder text (optional)
                    'validation': dict     # Validation rules (optional)
                },
                ...
            }

        Example:
            {
                'tenant_id': {
                    'type': 'string',
                    'label': 'Tenant ID',
                    'required': True,
                    'help_text': 'Entra ID tenant ID (GUID format)',
                    'placeholder': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
                },
                'client_id': {
                    'type': 'string',
                    'label': 'Client ID',
                    'required': True,
                    'help_text': 'Application (client) ID from Azure portal'
                },
                'client_secret': {
                    'type': 'password',
                    'label': 'Client Secret',
                    'required': True,
                    'help_text': 'Application client secret value'
                },
                'page_size': {
                    'type': 'number',
                    'label': 'Page Size',
                    'required': False,
                    'default': 999,
                    'help_text': 'Number of records per API request',
                    'validation': {
                        'min': 1,
                        'max': 999
                    }
                }
            }
        """
        pass

    # ===== Common Methods (provided by base class) =====

    def get_sample_data(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get sample records for field mapping preview.

        Uses fetch_data() but limits results. Strips framework metadata
        (keys starting with _) from returned records.

        Args:
            limit: Maximum number of sample records to return

        Returns:
            List of sample records (up to limit), empty list on error
        """
        self.logger.info(f"Fetching {limit} sample records for preview")

        samples = []
        try:
            for record in self.fetch_data(batch_id="sample"):
                clean = {k: v for k, v in record.items() if not k.startswith("_")}
                samples.append(clean)
                if len(samples) >= limit:
                    break

            self.logger.info(f"Retrieved {len(samples)} sample records")
            return samples

        except Exception as e:
            self.logger.error(f"Error fetching sample data: {e}")
            return []

    def validate_config(self) -> Dict[str, Any]:
        """
        Validate connector configuration against schema.

        Checks:
        - All required fields are present
        - Field types are correct
        - Values pass validation rules

        Returns:
            {
                'valid': bool,
                'errors': list of error messages
            }
        """
        errors = []
        schema = self.get_config_schema()

        # Check required fields
        for field_name, field_spec in schema.items():
            if field_spec.get('required', False):
                if field_name not in self.config or not self.config[field_name]:
                    errors.append(f"Missing required field: {field_name}")

        # Check field types (basic validation)
        for field_name, value in self.config.items():
            if field_name in schema:
                field_spec = schema[field_name]
                field_type = field_spec.get('type')

                # Type validation
                if field_type == 'number' and not isinstance(value, (int, float)):
                    try:
                        int(value)
                    except (ValueError, TypeError):
                        errors.append(f"Field '{field_name}' must be a number")

                # Validation rules
                validation = field_spec.get('validation', {})
                if 'min' in validation and isinstance(value, (int, float)):
                    if value < validation['min']:
                        errors.append(f"Field '{field_name}' must be >= {validation['min']}")
                if 'max' in validation and isinstance(value, (int, float)):
                    if value > validation['max']:
                        errors.append(f"Field '{field_name}' must be <= {validation['max']}")

        return {
            'valid': len(errors) == 0,
            'errors': errors
        }

    def get_metadata(self) -> Dict[str, str]:
        """
        Get connector metadata.

        Returns:
            {
                'integration_type': str,
                'display_name': str,
                'category': str,
                'description': str,
                'logo_url': str
            }
        """
        return {
            'integration_type': self.integration_type,
            'display_name': self.display_name,
            'category': self.category,
            'description': self.description,
            'logo_url': self.logo_url
        }

    # ===== Internal Helper Methods =====

    def _validate_metadata(self):
        """Validate that required metadata is defined"""
        required_fields = ['integration_type', 'display_name', 'category', 'description']
        missing = []

        for field in required_fields:
            if not getattr(self, field, None):
                missing.append(field)

        if missing:
            raise ValueError(
                f"Connector {self.__class__.__name__} is missing required metadata: {', '.join(missing)}"
            )

    def _setup_default_logger(self) -> logging.Logger:
        """Setup default logger if none provided"""
        logger = logging.getLogger(f"connector.{self.integration_type}")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _build_auth(self) -> Optional[Any]:
        """
        Build and return an authentication strategy.

        Connectors override this to return an AuthStrategy instance.
        Default returns None (no auth).
        """
        return None

    def _build_rate_limiter(self) -> Optional[Any]:
        """
        Build and return a rate limiter.

        Connectors override this to return a RateLimiter instance.
        Default returns None (no rate limiting).
        """
        return None

    def _get_source(self) -> str:
        """
        Return the source identifier for metadata injection.

        Default returns integration_type. Connectors may override
        for more specific source identifiers (e.g., "manual_upload:csv").
        """
        return self.integration_type

    def _inject_metadata(self, record: Dict[str, Any], batch_id: str) -> None:
        """
        Inject framework metadata into a record (mutates in-place).

        Sets _batch_id, _collected_at (timezone-aware UTC ISO string),
        and _source.
        """
        record["_batch_id"] = batch_id
        record["_collected_at"] = datetime.now(timezone.utc).isoformat()
        record["_source"] = self._get_source()

    # ===== Execution Logging Methods =====

    def log_execution_start(self, batch_id: str, metadata: Optional[Dict[str, Any]] = None) -> float:
        """
        Log the start of connector execution.

        Args:
            batch_id: UUID of the ingestion batch
            metadata: Optional connector-specific metadata

        Returns:
            Start timestamp for duration calculation
        """
        start_time = time.time()

        log_extra = {
            'connector_type': self.integration_type,
            'connector_name': self.display_name,
            'batch_id': batch_id,
            'event_type': 'connector_execution_start',
            'category': self.category
        }

        if metadata:
            log_extra['metadata'] = metadata

        self.logger.info(
            f"Connector execution started: {self.display_name}",
            extra=log_extra
        )

        return start_time

    def log_execution_end(
        self,
        batch_id: str,
        start_time: float,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log the end of connector execution.

        Args:
            batch_id: UUID of the ingestion batch
            start_time: Start timestamp from log_execution_start()
            success: Whether execution was successful
            error_message: Error message if failed
            metadata: Optional connector-specific metadata
        """
        duration_ms = (time.time() - start_time) * 1000

        log_extra = {
            'connector_type': self.integration_type,
            'connector_name': self.display_name,
            'batch_id': batch_id,
            'event_type': 'connector_execution_end',
            'category': self.category,
            'duration_ms': round(duration_ms, 2),
            'success': success
        }

        if error_message:
            log_extra['error_message'] = error_message

        if metadata:
            log_extra['metadata'] = metadata

        if success:
            self.logger.info(
                f"Connector execution completed: {self.display_name} ({round(duration_ms, 2)}ms)",
                extra=log_extra
            )
        else:
            self.logger.error(
                f"Connector execution failed: {self.display_name} ({round(duration_ms, 2)}ms)",
                extra=log_extra
            )

    def log_sync_metrics(
        self,
        batch_id: str,
        records_fetched: int = 0,
        records_processed: int = 0,
        records_failed: int = 0,
        records_skipped: int = 0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log data synchronization metrics.

        Args:
            batch_id: UUID of the ingestion batch
            records_fetched: Total records fetched from source
            records_processed: Successfully processed records
            records_failed: Records that failed processing
            records_skipped: Records skipped (duplicates, invalid, etc.)
            metadata: Optional additional metrics
        """
        log_extra = {
            'connector_type': self.integration_type,
            'connector_name': self.display_name,
            'batch_id': batch_id,
            'event_type': 'sync_metrics',
            'records_fetched': records_fetched,
            'records_processed': records_processed,
            'records_failed': records_failed,
            'records_skipped': records_skipped,
            'success_rate': round(records_processed / max(records_fetched, 1) * 100, 2)
        }

        if metadata:
            log_extra.update(metadata)

        # Determine log level based on failure rate
        if records_failed > 0 and records_failed / max(records_fetched, 1) > 0.1:
            # More than 10% failure rate - warning
            self.logger.warning(
                f"Sync metrics for {self.display_name}: "
                f"fetched={records_fetched}, processed={records_processed}, "
                f"failed={records_failed}, skipped={records_skipped}",
                extra=log_extra
            )
        else:
            self.logger.info(
                f"Sync metrics for {self.display_name}: "
                f"fetched={records_fetched}, processed={records_processed}, "
                f"failed={records_failed}, skipped={records_skipped}",
                extra=log_extra
            )

    def log_api_call(
        self,
        endpoint: str,
        method: str = 'GET',
        status_code: Optional[int] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log external API call details.

        Args:
            endpoint: API endpoint called (URL or path)
            method: HTTP method used
            status_code: HTTP response status code
            duration_ms: Call duration in milliseconds
            success: Whether the call was successful
            error_message: Error message if failed
            metadata: Optional additional info (headers, params, etc.)
        """
        log_extra = {
            'connector_type': self.integration_type,
            'connector_name': self.display_name,
            'event_type': 'external_api_call',
            'endpoint': endpoint,
            'method': method,
            'status_code': status_code,
            'duration_ms': round(duration_ms, 2) if duration_ms else None,
            'success': success
        }

        if error_message:
            log_extra['error_message'] = error_message

        if metadata:
            # Filter sensitive data from metadata
            safe_metadata = self._filter_sensitive_metadata(metadata)
            log_extra['metadata'] = safe_metadata

        if success:
            self.logger.debug(
                f"API call to {endpoint}: {method} -> {status_code} ({duration_ms}ms)",
                extra=log_extra
            )
        else:
            self.logger.warning(
                f"API call failed to {endpoint}: {method} -> {status_code}",
                extra=log_extra
            )

    def log_retry_attempt(
        self,
        operation: str,
        attempt: int,
        max_attempts: int,
        wait_seconds: float,
        error_message: str
    ) -> None:
        """
        Log retry attempt for error recovery.

        Args:
            operation: Description of the operation being retried
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            wait_seconds: Wait time before next retry
            error_message: Error that triggered the retry
        """
        self.logger.warning(
            f"Retry attempt {attempt}/{max_attempts} for {operation}",
            extra={
                'connector_type': self.integration_type,
                'connector_name': self.display_name,
                'event_type': 'retry_attempt',
                'operation': operation,
                'attempt': attempt,
                'max_attempts': max_attempts,
                'wait_seconds': wait_seconds,
                'error_message': error_message,
                'will_retry': attempt < max_attempts
            }
        )

    def _filter_sensitive_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Filter sensitive data from metadata for logging.

        Args:
            metadata: Metadata dictionary that may contain sensitive values

        Returns:
            Filtered metadata with sensitive values redacted
        """
        sensitive_keys = {
            'password', 'secret', 'token', 'key', 'authorization',
            'client_secret', 'api_key', 'access_token', 'refresh_token',
            'bearer', 'credential', 'auth'
        }

        filtered = {}
        for key, value in metadata.items():
            key_lower = key.lower()
            # Check if key contains any sensitive term
            if any(term in key_lower for term in sensitive_keys):
                filtered[key] = '[REDACTED]'
            elif isinstance(value, dict):
                # Recursively filter nested dicts
                filtered[key] = self._filter_sensitive_metadata(value)
            elif isinstance(value, str) and len(value) > 500:
                # Truncate very long strings
                filtered[key] = value[:500] + '... [TRUNCATED]'
            else:
                filtered[key] = value

        return filtered

    # ===== Data Filter Engine =====

    def _should_include_record(self, record: Dict[str, Any]) -> bool:
        """Check if record passes configured data filters."""
        filters = self.config.get('data_filters', [])
        if not filters:
            return True
        return data_filters.matches_filters(record, filters)

    def __repr__(self) -> str:
        """String representation"""
        return f"<{self.__class__.__name__}(type={self.integration_type}, name={self.display_name})>"
