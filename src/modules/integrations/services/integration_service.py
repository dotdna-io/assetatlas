# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Service

Business logic layer for integration management.
"""

import logging
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from src.database.postgres_connection import get_connection_manager
from src.modules.integrations import ConnectorRegistry
from src.modules.integrations.models import Integration, IntegrationStatus
from src.modules.integrations.repositories import IntegrationRepository
from src.modules.integrations.repositories.batch_repository import BatchRepository
from src.modules.integrations.repositories.raw_data_repository import RawDataRepository
from src.modules.integrations.services.mapping_service import MappingService
from src.modules.integrations.shared.encryption import is_encrypted
from src.modules.integrations.shared.scheduler_signal import signal_schedule_change
from src.api.utils.metrics import track_integration_execution


logger = logging.getLogger(__name__)

# Base offset for per-integration advisory lock IDs.
# Each integration gets lock_id = _LOCK_ID_BASE + (uuid_int % _LOCK_ID_RANGE)
# to stay in the positive 32-bit range required by pg_try_advisory_lock.
_LOCK_ID_BASE = 100_000
_LOCK_ID_RANGE = 2_000_000_000

WIZARD_TEMP_PREFIX = '__test_'




class IntegrationService:
    """
    Service layer for integration management.

    Handles connector lifecycle, testing, execution, and status management.
    """

    def __init__(self, repository: Optional[IntegrationRepository] = None):
        """
        Initialize service.

        Args:
            repository: Integration repository (creates default if not provided)
        """
        self.repository = repository or IntegrationRepository()
        self.batch_repository = BatchRepository()
        self.raw_data_repository = RawDataRepository()
        self.mapping_service = MappingService()
        self.conn_manager = get_connection_manager()

    @staticmethod
    def _lock_id_for(integration_id: uuid.UUID) -> int:
        """Derive a stable advisory lock ID from an integration UUID."""
        if not isinstance(integration_id, uuid.UUID):
            integration_id = uuid.UUID(str(integration_id))
        return _LOCK_ID_BASE + (integration_id.int % _LOCK_ID_RANGE)

    def cleanup_stale_running_flags(self) -> int:
        """Clear ``is_running`` flags whose advisory lock is unheld.

        ``is_running`` is a UI hint; the advisory lock held by
        ``run_integration`` is the real source of truth. A crashed
        process releases its lock but leaves the flag set, stranding
        the integration at "Already running" forever. Probing the lock
        tells us whether a flag is stale (acquirable) or live (held by
        another session). Uses a dedicated autocommit connection because
        advisory locks are session-scoped — leaking one back into the
        pool would confuse the next request that reuses the connection.

        Returns: number of stale flags cleared.
        """
        cleared = 0
        lock_conn = None
        try:
            lock_conn = psycopg2.connect(
                self.conn_manager.database_url, connect_timeout=10
            )
            lock_conn.autocommit = True
            with lock_conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM integrations WHERE is_running = TRUE"
                )
                rows = cursor.fetchall()
                for (raw_id,) in rows:
                    try:
                        iid = uuid.UUID(str(raw_id))
                    except (ValueError, AttributeError):
                        continue
                    lock_id = self._lock_id_for(iid)
                    cursor.execute(
                        "SELECT pg_try_advisory_lock(%s)", (lock_id,)
                    )
                    acquired = cursor.fetchone()[0]
                    if not acquired:
                        continue
                    try:
                        cursor.execute(
                            "UPDATE integrations SET is_running = FALSE WHERE id = %s",
                            (str(iid),),
                        )
                        cleared += 1
                    finally:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s)", (lock_id,)
                        )
            if cleared > 0:
                logger.warning(
                    f"Cleared {cleared} stale is_running flag(s) on startup"
                )
        except Exception as e:
            logger.error(f"Failed to clear stale is_running flags: {e}")
        finally:
            # Closing the connection releases any session locks we
            # might've leaked above.
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception as close_err:
                    logger.debug(f"Failed to close cleanup connection: {close_err}")
        return cleared

    def get_available_connectors(self) -> List[Dict[str, Any]]:
        """
        Get list of available connector types.

        Returns:
            List of connector metadata
        """
        return ConnectorRegistry.get_all_connectors()

    def get_connector_config_schema(self, integration_type: str) -> Optional[Dict[str, Any]]:
        """
        Get configuration schema for a connector type.

        Args:
            integration_type: Connector integration type

        Returns:
            Configuration schema if connector exists, None otherwise
        """
        try:
            connector_class = ConnectorRegistry._connectors.get(integration_type)
            if not connector_class:
                return None

            return connector_class.get_config_schema()
        except Exception as e:
            logger.error(f"Error getting config schema for {integration_type}: {e}")
            return None

    @staticmethod
    def merge_candidate_config(
        candidate: Optional[Dict[str, Any]],
        saved_decrypted: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Merge a wizard-supplied candidate config with the saved decrypted one.

        Non-sensitive keys come from the candidate so query/URL edits in the
        wizard apply. Sensitive keys fall back to the saved value whenever the
        candidate doesn't carry a fresh plaintext (missing, empty, or still
        encrypted — which is what the wizard sees because edit-mode load
        doesn't decrypt credentials).
        """
        if not candidate:
            return saved_decrypted
        merged: Dict[str, Any] = dict(candidate)
        for field in IntegrationRepository.SENSITIVE_FIELDS:
            value = merged.get(field)
            unchanged = (
                value in (None, '')
                or (isinstance(value, str) and is_encrypted(value))
            )
            if unchanged and field in saved_decrypted:
                merged[field] = saved_decrypted[field]
        return merged

    def build_connector(
        self,
        integration: Integration,
        candidate_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Build a connector for the integration, optionally merging the
        wizard's unsaved edits over the saved config. See
        :meth:`merge_candidate_config` for the merge semantics.
        """
        effective_config = self.merge_candidate_config(
            candidate_config, integration.config
        )
        return ConnectorRegistry.create_connector(
            integration.effective_connector_type, effective_config
        )

    def test_connection(
        self,
        integration_id: uuid.UUID,
        candidate_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Test connection for an integration.

        Args:
            integration_id: Integration UUID
            candidate_config: Optional config from the wizard. When provided,
                non-sensitive keys override the saved config and sensitive
                keys fall back to the saved decrypted value if the candidate
                hasn't supplied a fresh plaintext.

        Returns:
            Test result with success status and message
        """
        # Get integration with decrypted credentials
        integration = self.repository.get_by_id(integration_id, decrypt=True)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        try:
            connector = self.build_connector(integration, candidate_config)
            result = connector.test_connection()
            self._persist_test_outcome(integration_id, result, candidate_config)
            return result

        except Exception as e:
            logger.error(f"Error testing integration {integration_id}: {e}", exc_info=True)
            failure = {
                'success': False,
                'message': f'Connection test failed: {str(e)}',
                'error': 'EXCEPTION',
                'details': str(e),
            }
            self._persist_test_outcome(integration_id, failure, candidate_config)
            return failure

    def _persist_test_outcome(
        self,
        integration_id: uuid.UUID,
        result: Dict[str, Any],
        candidate_config: Optional[Dict[str, Any]],
    ) -> None:
        # Skip persistence for candidate-config tests — their outcome
        # describes unsaved edits, not the config that runs on the schedule.
        # The integration.status field is left alone either way; flipping it
        # to 'error' would make is_enabled() False and break the schedule.
        if candidate_config is not None:
            return
        success = result['success']
        message = result.get(
            'message',
            'Connection successful' if success else 'Connection test failed',
        )
        self.repository.update(integration_id, {
            'last_error': None if success else message,
            'last_connection_test_at': datetime.now(timezone.utc),
            'last_connection_test_status': 'success' if success else 'error',
            'last_connection_test_message': message,
        })

    def get_sample_data(
        self,
        integration_id: uuid.UUID,
        limit: int = 5,
        candidate_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Get sample data from an integration.

        Args:
            integration_id: Integration UUID
            limit: Number of sample records to fetch
            candidate_config: Optional config from the wizard. Same merge
                semantics as :meth:`test_connection` — lets the field-mapping
                step preview the source schema for unsaved query edits.

        Returns:
            Dictionary with success status and sample data
        """
        # Get integration with decrypted credentials
        integration = self.repository.get_by_id(integration_id, decrypt=True)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        try:
            connector = self.build_connector(integration, candidate_config)
            samples = connector.get_sample_data(limit=limit)

            return {
                'success': True,
                'sample_count': len(samples),
                'samples': samples
            }

        except Exception as e:
            logger.error(f"Error getting sample data for {integration_id}: {e}", exc_info=True)

            return {
                'success': False,
                'message': f'Failed to get sample data: {str(e)}',
                'error': 'EXCEPTION',
                'details': str(e)
            }

    def get_field_schema(self, integration_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get field schema for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with success status and schema
        """
        # Get integration with decrypted credentials
        integration = self.repository.get_by_id(integration_id, decrypt=True)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        try:
            # Create connector instance
            connector = ConnectorRegistry.create_connector(
                integration.integration_type,
                integration.config
            )

            # Get schema
            schema = connector.get_schema()

            return {
                'success': True,
                'schema': schema
            }

        except Exception as e:
            logger.error(f"Error getting field schema for {integration_id}: {e}", exc_info=True)

            return {
                'success': False,
                'message': f'Failed to get field schema: {str(e)}',
                'error': 'EXCEPTION',
                'details': str(e)
            }

    def run_integration(self, integration_id: uuid.UUID) -> Dict[str, Any]:
        """
        Run an integration to collect data.

        Creates a batch and fetches data from the connector.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with success status and batch_id
        """
        # Get integration with decrypted credentials
        integration = self.repository.get_by_id(integration_id, decrypt=True)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        if not integration.is_enabled():
            return {
                'success': False,
                'message': 'Integration is not enabled',
                'error': 'DISABLED'
            }

        # Acquire per-integration advisory lock to prevent concurrent runs.
        # Uses a direct connection (not from pool) so the lock is held for
        # the full run without depleting the connection pool.  The lock is
        # auto-released when the connection is closed (in the finally block)
        # or if the process crashes.
        lock_id = self._lock_id_for(integration_id)
        lock_conn = psycopg2.connect(self.conn_manager.database_url, connect_timeout=10)
        lock_conn.autocommit = True
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
                acquired = cur.fetchone()[0]
            if not acquired:
                lock_conn.close()
                lock_conn = None
                logger.warning(
                    f"Integration {integration.name} ({integration_id}) is already running, skipping"
                )
                return {
                    'success': False,
                    'message': f'Integration "{integration.name}" is already running',
                    'error': 'ALREADY_RUNNING'
                }
        except Exception as e:
            lock_conn.close()
            lock_conn = None
            raise

        # Mark as running for UI visibility
        self.repository.update(integration_id, {'is_running': True})

        # Create batch record in database. record_count / run_status /
        # run_started are initialised before the try so the Prometheus emit in
        # `finally` can report them even if the run fails before any records
        # are fetched (#922).
        batch_id = None
        record_count = 0
        run_status = 'failed'
        run_started = time.monotonic()

        try:
            # Create batch
            batch_id = self.batch_repository.create_batch(integration_id)

            logger.info(f"Starting integration run {integration.name} (batch: {batch_id})")

            connector_config = integration.config.copy()
            connector_config['data_filters'] = integration.data_filters or []
            connector = ConnectorRegistry.create_connector(
                integration.effective_connector_type,
                connector_config
            )

            # Fetch data and store records
            buffer = []
            buffer_size = 100  # Bulk insert every 100 records

            for record in connector.fetch_data(str(batch_id)):
                buffer.append(record)
                record_count += 1

                # Bulk insert when buffer is full
                if len(buffer) >= buffer_size:
                    self.raw_data_repository.store_records_batch(
                        batch_id,
                        integration_id,
                        buffer
                    )
                    # Update batch record count
                    self.batch_repository.increment_raw_count(batch_id, len(buffer))
                    buffer = []

                    logger.debug(f"Stored {record_count} records so far...")

            # Store remaining records in buffer
            if buffer:
                self.raw_data_repository.store_records_batch(
                    batch_id,
                    integration_id,
                    buffer
                )
                self.batch_repository.increment_raw_count(batch_id, len(buffer))

            # Complete batch
            self.batch_repository.complete_batch(batch_id, record_count)

            # Update sync status
            self.repository.update_sync_status(
                integration_id,
                status='success',
                error=None
            )
            run_status = 'success'

            logger.info(f"Integration run completed: {record_count} records fetched and stored")

            # Automatically trigger mapping if field_mapping is configured
            mapping_result = None
            if integration.field_mapping:
                logger.info(f"Starting automatic mapping for batch {batch_id}")
                try:
                    mapping_result = self.mapping_service.map_batch(batch_id)
                    logger.info(f"Mapping completed: {mapping_result.get('mapped_count', 0)} records mapped")
                except Exception as e:
                    logger.error(f"Automatic mapping failed: {e}", exc_info=True)
                    mapping_result = {
                        'success': False,
                        'error': str(e)
                    }

            return {
                'success': True,
                'message': f'Successfully fetched {record_count} records',
                'batch_id': str(batch_id),
                'record_count': record_count,
                'mapping': mapping_result
            }

        except Exception as e:
            logger.error(f"Error running integration {integration_id}: {e}", exc_info=True)

            # Fail batch if it was created
            if batch_id:
                self.batch_repository.fail_batch(batch_id, str(e))

            # Update sync status with error — never auto-disable,
            # let the user decide whether to disable the integration.
            self.repository.update_sync_status(
                integration_id,
                status='failed',
                error=str(e)
            )

            return {
                'success': False,
                'message': f'Integration run failed: {str(e)}',
                'error': str(type(e).__name__),
                'details': str(e),
                'batch_id': str(batch_id) if batch_id else None
            }

        finally:
            # Clear running flag for UI.
            # Use pool connection first; fall back to the dedicated lock
            # connection (which is always available and auto-commits) so the
            # flag is cleared even when the pool is temporarily exhausted.
            try:
                self.repository.update(integration_id, {'is_running': False})
            except Exception as e:
                logger.error(f"Failed to clear is_running via pool for {integration_id}: {e}")
                if lock_conn is not None:
                    try:
                        with lock_conn.cursor() as cur:
                            cur.execute(
                                "UPDATE integrations SET is_running = FALSE WHERE id = %s",
                                (str(integration_id),)
                            )
                    except Exception as fallback_err:
                        logger.error(
                            f"Fallback is_running clear also failed for {integration_id}: {fallback_err}"
                        )
            # Close the dedicated lock connection (automatically releases the
            # advisory lock since it is session-level).
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception as close_err:
                    logger.warning(f"Failed to close lock connection for {integration_id}: {close_err}")

            # Prometheus: one integration-execution metric per run, regardless
            # of outcome. Best-effort and after the critical lock cleanup — a
            # metrics failure must never affect the run (#922).
            try:
                track_integration_execution(
                    integration_id=integration_id,
                    integration_name=integration.name,
                    connector_type=integration.effective_connector_type,
                    duration_seconds=time.monotonic() - run_started,
                    status=run_status,
                    records_processed=record_count,
                )
            except Exception:
                logger.debug(
                    "integration metric emit failed (non-fatal)", exc_info=True
                )

    def create_integration(self, name: str, integration_type: str,
                          config: Dict[str, Any],
                          field_mapping: Optional[Dict[str, Any]] = None,
                          transformations: Optional[Dict[str, Any]] = None,
                          data_filters: Optional[list] = None,
                          preserve_fields: Optional[list] = None,
                          category: Optional[str] = None,
                          logo: Optional[str] = None,
                          description: Optional[str] = None,
                          last_connection_test_status: Optional[str] = None,
                          last_connection_test_message: Optional[str] = None,
                          source_method: str = 'direct') -> Integration:
        """
        Create a new integration.

        Args:
            name: Integration name
            integration_type: Connector type
            config: Configuration dictionary
            field_mapping: Field mapping configuration
            transformations: Field transformation definitions (separate from mappings)
            data_filters: Data filter definitions for pre-fetch filtering
            preserve_fields: Data-source-specific (DSS) field names; wizard-only
                source-of-truth for DSS-vs-mapping classification (#809)
            category: Data source category (uses connector default if not provided)
            logo: Logo filename (optional)
            description: Integration description (optional)
            last_connection_test_status: Connection test status if already tested (optional)
            last_connection_test_message: Connection test message if already tested (optional)

        Returns:
            Created integration

        Raises:
            ValueError: If integration type doesn't exist
            psycopg2.IntegrityError: If name already exists
        """
        # Validate connector type exists
        connector_metadata = next(
            (c for c in self.get_available_connectors() if c['integration_type'] == integration_type),
            None
        )

        if not connector_metadata:
            raise ValueError(f"Unknown integration type: {integration_type}")

        # Use provided category or fall back to connector default
        final_category = category if category else connector_metadata['category']

        # Set connection test timestamp if status was provided
        last_connection_test_at = None
        if last_connection_test_status:
            last_connection_test_at = datetime.now(timezone.utc)

        # Create integration
        integration = Integration(
            name=name,
            integration_type=integration_type,
            category=final_category,
            config=config,
            field_mapping=field_mapping or {},
            transformations=transformations or {},
            data_filters=data_filters or [],
            preserve_fields=preserve_fields or [],
            status=IntegrationStatus.ENABLED,
            logo=logo,
            description=description,
            last_connection_test_at=last_connection_test_at,
            last_connection_test_status=last_connection_test_status,
            last_connection_test_message=last_connection_test_message,
            source_method=source_method
        )

        return self.repository.create(integration)

    def update_integration(self, integration_id: uuid.UUID,
                          updates: Dict[str, Any]) -> Optional[Integration]:
        """Update integration; signal scheduler if schedule fields change."""
        result = self.repository.update(integration_id, updates)
        if result is not None and any(
            key in updates for key in (
                'schedule_cron', 'schedule_timezone', 'schedule_enabled'
            )
        ):
            signal_schedule_change(integration_id)
        return result

    def delete_integration(self, integration_id: uuid.UUID) -> bool:
        """
        Delete integration.

        Args:
            integration_id: Integration UUID

        Returns:
            True if deleted, False if not found
        """
        return self.repository.delete(integration_id)

    def get_integration(self, integration_id: uuid.UUID,
                       include_credentials: bool = False) -> Optional[Integration]:
        """
        Get integration by ID.

        Args:
            integration_id: Integration UUID
            include_credentials: Whether to decrypt credentials

        Returns:
            Integration if found, None otherwise
        """
        return self.repository.get_by_id(integration_id, decrypt=include_credentials)

    def list_integrations(self, status: Optional[IntegrationStatus] = None,
                         integration_type: Optional[str] = None,
                         include_wizard_temp: bool = False) -> List[Integration]:
        """List integrations.

        Wizard temp rows (created by the Test Connection step and
        sometimes leaked when the user closes the browser mid-flow) are
        hidden by default; ``cleanup_wizard_temp_integrations`` sweeps
        them on a periodic schedule.
        """
        integrations = self.repository.list_all(
            status=status, integration_type=integration_type, decrypt=False
        )
        if not include_wizard_temp:
            integrations = [
                i for i in integrations
                if not i.name.startswith(WIZARD_TEMP_PREFIX)
            ]
        return integrations

    def cleanup_wizard_temp_integrations(self, max_age_seconds: int = 3600) -> int:
        """Delete leaked wizard-temp integrations older than max_age_seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        deleted = 0
        try:
            with self.repository._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, name FROM integrations
                        WHERE name LIKE %s ESCAPE '\\'
                        AND created_at < %s
                        """,
                        (f"{WIZARD_TEMP_PREFIX}%", cutoff),
                    )
                    rows = cur.fetchall()
            for raw_id, name in rows:
                try:
                    if self.repository.delete(uuid.UUID(str(raw_id))):
                        deleted += 1
                        logger.info(
                            f"Swept orphan wizard temp integration: {name} ({raw_id})"
                        )
                except Exception as e:
                    logger.warning(f"Failed to delete orphan {name}: {e}")
        except Exception as e:
            logger.error(f"wizard-temp sweep failed: {e}")
        return deleted

    def enable_integration(self, integration_id: uuid.UUID) -> bool:
        """Enable an integration; clear last_error; refresh next_run_at; signal scheduler."""
        integration = self.repository.get_by_id(integration_id)
        if not integration:
            return False

        updates: Dict[str, Any] = {
            'status': IntegrationStatus.ENABLED,
            'last_error': None,
        }

        if integration.schedule_enabled and integration.schedule_cron:
            from src.modules.integrations.shared.cron_validator import (
                calculate_next_run,
            )
            next_run = calculate_next_run(
                integration.schedule_cron, integration.schedule_timezone
            )
            if next_run:
                updates['next_run_at'] = next_run

        result = self.repository.update(integration_id, updates)
        if result is not None:
            signal_schedule_change(integration_id)
        return result is not None

    def disable_integration(self, integration_id: uuid.UUID) -> bool:
        """Disable an integration; clear next_run_at; signal scheduler to drop the job."""
        result = self.repository.update(integration_id, {
            'status': IntegrationStatus.DISABLED,
            'next_run_at': None,
        })
        if result is not None:
            signal_schedule_change(integration_id)
        return result is not None

    def get_integration_stats(self) -> Dict[str, Any]:
        """
        Get statistics about integrations.

        Returns:
            Dictionary with integration statistics
        """
        return {
            'total': self.repository.count(),
            'enabled': self.repository.count(IntegrationStatus.ENABLED),
            'disabled': self.repository.count(IntegrationStatus.DISABLED),
            'error': self.repository.count(IntegrationStatus.ERROR),
            'available_connectors': ConnectorRegistry.get_connector_count()
        }

    def get_insights(self, integration_id: uuid.UUID, medium: float = None, high: float = None) -> Dict[str, Any]:
        integration = self.repository.get_by_id(integration_id)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        ds = integration.name
        threshold_days = 30
        stale = self.repository.get_stale_assets_count(
            ds, threshold_days=threshold_days
        )
        coverage = self.repository.get_attribute_coverage(ds)
        conflicts = self.repository.get_attribute_conflicts(ds)
        all_categories = self.repository.get_category_coverage(ds)
        confidence = self.repository.get_confidence_distribution(ds, medium=medium, high=high)

        # Categories where the source has at least 30% share — these are
        # the ones for which a "you're missing N assets" reading is
        # actionable. Lower-share categories still count toward
        # completeness below.
        gap_categories = [
            c for c in all_categories if c.get('coverage_pct', 0) >= 30
        ]

        total = stale['total_count']
        stale_pct = round(stale['stale_count'] / total * 100, 1) if total else 0.0

        worst_field = None
        worst_gap = 0.0
        for field_name, info in coverage.items():
            if info['gap'] > worst_gap:
                worst_gap = info['gap']
                worst_field = field_name

        total_missing = sum(
            c.get('estimated_missing', 0) for c in gap_categories
        )

        # Source with no assets gets visibility=0, not a vacuous 100.
        visibility = max(0, round(100 - stale_pct)) if total else 0

        high = confidence.get('high', 0)
        medium = confidence.get('medium', 0)
        low = confidence.get('low', 0)
        total_conf = high + medium + low
        quality = (
            round((100 * high + 50 * medium) / total_conf)
            if total_conf else 0
        )

        coverages = [c.get('coverage_pct', 0) for c in all_categories]
        completeness = (
            round(sum(coverages) / len(coverages)) if coverages else 0
        )

        return {
            'integration_id': str(integration_id),
            'integration_name': integration.name,
            'indicators': {
                'stale_assets': {
                    'count': stale['stale_count'],
                    'total': stale['total_count'],
                    'percentage': stale_pct,
                    'threshold_days': threshold_days
                },
                'missing_attributes': {
                    'fields': coverage,
                    'worst_gap_field': worst_field,
                    'worst_gap_pct': worst_gap
                },
                'conflicting_attributes': conflicts,
                'missing_expected_assets': {
                    'categories': gap_categories,
                    'total_estimated_missing': total_missing
                }
            },
            'posture_scores': {
                'visibility': visibility,
                'quality': quality,
                'completeness': completeness,
            }
        }

    def get_timeline(self, integration_id: uuid.UUID, days: int = 30) -> Dict[str, Any]:
        integration = self.repository.get_by_id(integration_id)
        if not integration:
            return {
                'success': False,
                'message': 'Integration not found',
                'error': 'NOT_FOUND'
            }

        data = self.repository.get_asset_timeline(integration.name, days=days)

        return {
            'integration_id': str(integration_id),
            'range': f'{days}d',
            'granularity': 'daily',
            'data': data
        }
