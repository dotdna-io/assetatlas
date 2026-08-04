# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Field Mapping Engine

Transforms raw JSONB data to normalized asset fields using field mapping configurations.
"""

import logging
import re
import ipaddress
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, TYPE_CHECKING
import jsonpath_ng
from jsonpath_ng import parse as jsonpath_parse

if TYPE_CHECKING:
    from src.modules.integrations.models import Integration


logger = logging.getLogger(__name__)

# Precompiled patterns for per-record hot paths (map() -> _extract_nested / _convert_to_mac).
# Array-index segments like "addresses[0]" are rare, so callers guard with `'[' in part`
# before invoking _ARRAY_INDEX_RE to skip the regex machinery for the common bracket-free case.
_ARRAY_INDEX_RE = re.compile(r'(\w+)\[(\d+)\]')
_MAC_HEX12_RE = re.compile(r'^[0-9A-F]{12}$')


class FieldMapper:
    """
    Field mapping engine for transforming raw data to normalized fields.

    Supports:
    - Simple key mapping (e.g., "deviceName")
    - Dot notation for nested fields (e.g., "network.ipAddress")
    - JSONPath expressions (e.g., "$.hardware.manufacturer")
    - Field transformations (combine multiple fields with static text)
    - Automatic data type conversion (IP, MAC)
    - Extended fields collection (unmapped data)
    """

    # Standard framework fields
    FRAMEWORK_FIELDS = [
        'hostname', 'fqdn', 'ip', 'mac', 'sn',
        'os', 'manufacturer', 'model', 'vendor_category',
        'first_time', 'last_time', 'user',
    ]

    # Identifier fields always stored as arrays (multi-source capable)
    IDENTIFIER_FIELDS = {'hostname', 'fqdn', 'ip', 'mac', 'sn'}

    def __init__(self, field_mapping: Dict[str, Any], transformations: Dict[str, Any] = None):
        """
        Initialize field mapper.

        Args:
            field_mapping: Dict mapping framework fields to source paths
                Example: {
                    "hostname": "deviceName",
                    "ip": "network.ipAddress",
                    "sn": "$.physicalIds[0]"
                }
            transformations: Dict defining field transformations (separate from mappings)
                Example: {
                    "full_name": {
                        "operations": [
                            {"type": "field", "field": "firstName"},
                            {"type": "static", "value": " "},
                            {"type": "field", "field": "lastName"}
                        ]
                    }
                }
        """
        self.field_mapping = field_mapping or {}
        self.transformations = transformations or {}
        self._jsonpath_cache = {}
        self._virtual_fields_cache = {}  # Cache for computed transformation values

    def get_virtual_fields(self) -> Dict[str, Any]:
        """Return the virtual fields computed by the last map() call."""
        return dict(self._virtual_fields_cache)

    def map(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map raw payload to normalized fields.

        Processing order:
        1. Compute all transformations to create virtual source fields
        2. Apply field mappings (can reference both raw and virtual fields)

        Args:
            raw_payload: Raw JSONB data from source

        Returns:
            Dictionary with mapped fields:
            {
                'hostname': 'DESKTOP-01',
                'ip': '192.168.1.100',
                'mac': '00:0C:29:3E:4F:5A',
                ...
                'extended_fields': {...}  # Unmapped data
            }
        """
        mapped = {}

        # Step 1: Compute all transformations first to create virtual source fields
        self._compute_transformations(raw_payload)

        # Step 2: Map each configured field (can now reference virtual fields)
        for framework_field, source_path in self.field_mapping.items():
            if framework_field not in self.FRAMEWORK_FIELDS:
                continue

            try:
                # Backwards compat: inline transformation objects
                if isinstance(source_path, dict) and source_path.get('type') == 'transform':
                    value = self._apply_transformation(raw_payload, source_path)
                    mapped[framework_field] = value
                    continue

                # Resolve mapping (handles both str and list)
                values = self._resolve_mapping(raw_payload, source_path)

                # Apply type converters element-wise
                if framework_field == 'ip':
                    values = [v for v in (self._convert_to_ip(v) for v in values) if v is not None]
                elif framework_field == 'mac':
                    values = [v for v in (self._convert_to_mac(v) for v in values) if v is not None]
                elif framework_field in ('first_time', 'last_time'):
                    values = [v for v in (self._convert_to_timestamp(v) for v in values) if v is not None]

                # Identifier fields always arrays, non-identifiers stay scalar
                if framework_field in self.IDENTIFIER_FIELDS:
                    mapped[framework_field] = values if values else None
                else:
                    if len(values) == 1:
                        mapped[framework_field] = values[0]
                    elif len(values) > 1:
                        mapped[framework_field] = values
                    else:
                        mapped[framework_field] = None

            except Exception as e:
                logger.debug(f"Failed to map field '{framework_field}' from '{source_path}': {e}")
                mapped[framework_field] = None

        # Collect unmapped fields for extended_fields
        extended_fields = self._collect_extended_fields(raw_payload, mapped)
        mapped['extended_fields'] = extended_fields if extended_fields else None

        return mapped

    def _compute_transformations(self, raw_payload: Dict[str, Any]) -> None:
        """
        Compute all transformations and cache the results as virtual source fields.

        Args:
            raw_payload: Raw JSONB data from source
        """
        self._virtual_fields_cache = {}

        for field_name, transformation in self.transformations.items():
            try:
                # Apply transformation (transformations dict already contains operation list)
                if 'operations' in transformation:
                    value = self._apply_transformation(raw_payload, {'type': 'transform', 'operations': transformation['operations']})
                elif 'type' in transformation:
                    # Already in full format
                    value = self._apply_transformation(raw_payload, transformation)
                else:
                    logger.debug(f"Invalid transformation format for '{field_name}'")
                    value = None

                if value is not None:
                    self._virtual_fields_cache[field_name] = value
                    logger.debug(f"Computed virtual field '{field_name}': {value}")

            except Exception as e:
                logger.debug(f"Failed to compute transformation '{field_name}': {e}")
                self._virtual_fields_cache[field_name] = None

    def _resolve_mapping(self, data, mapping_value):
        """Resolve a mapping value to a list of extracted values.

        Handles both single source (str) and multi-source (list) mappings.
        For multi-source, each path is checked against virtual fields cache
        before falling back to _extract_value().
        Returns list of unique non-null values, or empty list.
        """
        if isinstance(mapping_value, str):
            if mapping_value in self._virtual_fields_cache:
                value = self._virtual_fields_cache[mapping_value]
                return [value] if value is not None else []
            value = self._extract_value(data, mapping_value)
            return [value] if value is not None else []
        elif isinstance(mapping_value, list):
            values = []
            for source_path in mapping_value:
                if isinstance(source_path, str) and source_path in self._virtual_fields_cache:
                    value = self._virtual_fields_cache[source_path]
                else:
                    value = self._extract_value(data, source_path)
                if value is not None:
                    if isinstance(value, list):
                        values.extend(value)
                    else:
                        values.append(value)
            seen = set()
            return [v for v in values if v not in seen and not seen.add(v)]
        return []

    def _extract_value(self, data: Dict[str, Any], path: str) -> Optional[Any]:
        """
        Extract value from data using various path formats.

        Supports:
        - Simple key: "deviceName"
        - Dot notation: "network.ipAddress"
        - JSONPath: "$.hardware.manufacturer"
        - Array index: "addresses[0]"

        Args:
            data: Source data dictionary
            path: Path expression

        Returns:
            Extracted value or None
        """
        if not path or not data:
            return None

        # JSONPath expression (starts with $)
        if path.startswith('$'):
            return self._extract_jsonpath(data, path)

        # Dot notation or simple key
        return self._extract_nested(data, path)

    def _apply_transformation(self, data: Dict[str, Any], transformation: Dict[str, Any]) -> Optional[str]:
        """
        Apply a field transformation to raw data.

        Transforms combine multiple source fields with static text into a single value.
        Supports various operations including field extraction, static text, trim, and case conversion.

        Args:
            data: Source data dictionary
            transformation: Transformation configuration with operations
                Example: {
                    "type": "transform",
                    "operations": [
                        {"type": "field", "field": "operatingSystem"},
                        {"type": "static", "value": " "},
                        {"type": "field", "field": "osVersion"},
                        {"type": "trim"}
                    ]
                }

        Supported operation types:
            - field: Extract value from a field path
            - static: Add literal text
            - current_timestamp: Append the current time as an ISO 8601 UTC string
            - trim: Remove leading/trailing whitespace from accumulated result
            - uppercase: Convert accumulated result to uppercase
            - lowercase: Convert accumulated result to lowercase
            - regex_extract: Extract text using regex pattern (with group)
            - regex_replace: Replace text using regex pattern
            - coalesce: Return first non-null field value or default

        Returns:
            Transformed string value or None if transformation fails or is empty
        """
        try:
            operations = transformation.get('operations', [])
            if not operations:
                logger.debug("Transformation has no operations")
                return None

            result = ""

            for operation in operations:
                op_type = operation.get('type')

                if op_type == 'field':
                    # Extract field value using standard extraction methods
                    field_path = operation.get('field')
                    if field_path:
                        value = self._extract_value(data, field_path)
                        if value is not None:
                            result += str(value)

                elif op_type == 'static':
                    # Use static value directly
                    static_value = operation.get('value')
                    if static_value is not None:
                        result += str(static_value)

                elif op_type == 'current_timestamp':
                    # Stamp the current collection time (ISO 8601 UTC). Lets sources
                    # that produce no timestamp populate last_time / "Last Seen".
                    result += datetime.now(timezone.utc).isoformat()

                elif op_type == 'trim':
                    # Remove leading/trailing whitespace
                    result = result.strip()

                elif op_type == 'uppercase':
                    # Convert to uppercase
                    result = result.upper()

                elif op_type == 'lowercase':
                    # Convert to lowercase
                    result = result.lower()

                elif op_type == 'regex_extract':
                    # Extract using regex pattern
                    pattern = operation.get('pattern')
                    group = operation.get('group', 0)
                    if pattern:
                        result = self._apply_regex_extract(result, pattern, group)

                elif op_type == 'regex_replace':
                    # Replace using regex pattern
                    pattern = operation.get('pattern')
                    replacement = operation.get('replacement', '')
                    if pattern:
                        result = self._apply_regex_replace(result, pattern, replacement)

                elif op_type == 'coalesce':
                    # Return first non-null field value, or default
                    fields = operation.get('fields', [])
                    default_value = operation.get('default', '')

                    found_value = None
                    for field_path in fields:
                        value = self._extract_value(data, field_path)
                        if value is not None and str(value).strip() != '':
                            found_value = str(value)
                            break

                    result += found_value if found_value is not None else default_value

                elif op_type == 'conditional':
                    # Evaluate condition and execute appropriate branch
                    condition = operation.get('condition', {})
                    condition_type = condition.get('type')
                    condition_field = condition.get('field')

                    condition_met = False

                    if condition_type == 'field_exists':
                        value = self._extract_value(data, condition_field)
                        condition_met = value is not None and str(value).strip() != ''
                    elif condition_type == 'field_equals':
                        value = self._extract_value(data, condition_field)
                        expected = condition.get('value')
                        condition_met = str(value) == str(expected) if value is not None else False
                    elif condition_type == 'field_empty':
                        value = self._extract_value(data, condition_field)
                        condition_met = value is None or str(value).strip() == ''
                    elif condition_type == 'field_contains':
                        value = self._extract_value(data, condition_field)
                        search_value = condition.get('value', '')
                        condition_met = search_value in str(value) if value is not None else False

                    branch_ops = operation.get('then', []) if condition_met else operation.get('else', [])
                    for branch_op in branch_ops:
                        branch_type = branch_op.get('type')
                        if branch_type == 'field':
                            field_path = branch_op.get('field')
                            if field_path:
                                value = self._extract_value(data, field_path)
                                if value is not None:
                                    result += str(value)
                        elif branch_type == 'static':
                            static_value = branch_op.get('value')
                            if static_value is not None:
                                result += str(static_value)

            # Return None if result is empty (only whitespace)
            return result if result.strip() else None

        except Exception as e:
            logger.debug(f"Transformation failed: {e}")
            return None

    def _apply_regex_extract(self, text: str, pattern: str, group: int = 0) -> str:
        """
        Extract text using regex pattern.

        Args:
            text: Input text to extract from
            pattern: Regex pattern to match
            group: Capture group to extract (0 = entire match)

        Returns:
            Extracted text or empty string if no match
        """
        try:
            match = re.search(pattern, text)
            if match:
                return match.group(group)
            return ""
        except Exception as e:
            logger.debug(f"Regex extract failed for pattern '{pattern}': {e}")
            return text

    def _apply_regex_replace(self, text: str, pattern: str, replacement: str) -> str:
        """
        Replace text using regex pattern.

        Args:
            text: Input text to replace in
            pattern: Regex pattern to match
            replacement: Replacement string

        Returns:
            Text with replacements applied or original text on error
        """
        try:
            return re.sub(pattern, replacement, text)
        except Exception as e:
            logger.debug(f"Regex replace failed for pattern '{pattern}': {e}")
            return text

    def _extract_jsonpath(self, data: Dict[str, Any], expression: str) -> Optional[Any]:
        """
        Extract value using JSONPath expression.

        Args:
            data: Source data
            expression: JSONPath expression (e.g., "$.hardware.manufacturer")

        Returns:
            Extracted value or None
        """
        try:
            # Cache compiled JSONPath expressions
            if expression not in self._jsonpath_cache:
                self._jsonpath_cache[expression] = jsonpath_parse(expression)

            jsonpath_expr = self._jsonpath_cache[expression]
            matches = jsonpath_expr.find(data)

            if not matches:
                return None

            if len(matches) > 1:
                return [m.value for m in matches]

            value = matches[0].value

            if isinstance(value, list) and value:
                return value
            if isinstance(value, list) and not value:
                return None

            return value

        except Exception as e:
            logger.debug(f"JSONPath extraction failed for '{expression}': {e}")
            return None

    def _extract_nested(self, data: Dict[str, Any], path: str) -> Optional[Any]:
        """
        Extract value using dot notation.

        Supports:
        - Simple key: "deviceName"
        - Nested: "network.ipAddress"
        - Array: "addresses[0]" or "addresses.0"

        Args:
            data: Source data
            path: Dot notation path

        Returns:
            Extracted value or None
        """
        try:
            # Flat key that literally contains dots — e.g. SentinelOne PowerQuery
            # returns columns like "device.hostname" as top-level keys, not nested
            # objects. Prefer the literal key when present before falling back to
            # nested-path traversal.
            if isinstance(data, dict) and path in data:
                current = data[path]
            else:
                current = data
                for part in path.split('.'):
                    if current is None:
                        return None

                    # Handle array index in notation like "addresses[0]".
                    # Skip the regex entirely for the common bracket-free segment.
                    match = _ARRAY_INDEX_RE.match(part) if '[' in part else None
                    if match:
                        key, index = match.groups()
                        current = current.get(key, [])
                        if isinstance(current, list) and int(index) < len(current):
                            current = current[int(index)]
                        else:
                            return None
                    else:
                        # Regular key access
                        if isinstance(current, dict):
                            current = current.get(part)
                        else:
                            return None

            if isinstance(current, list) and current:
                return current
            if isinstance(current, list) and not current:
                return None

            return current

        except Exception as e:
            logger.debug(f"Nested extraction failed for '{path}': {e}")
            return None

    def _convert_to_ip(self, value: Any) -> Optional[str]:
        """
        Convert value to valid IP address string.

        Handles:
        - IPv4 and IPv6
        - Strips whitespace
        - Validates format

        Args:
            value: Raw value (string, number, etc.)

        Returns:
            Valid IP address string or None
        """
        if not value:
            return None

        try:
            # Convert to string and clean
            ip_str = str(value).strip()

            # Validate using ipaddress module
            ip = ipaddress.ip_address(ip_str)
            return str(ip)

        except ValueError:
            logger.debug(f"Invalid IP address: {value}")
            return None

    def _convert_to_mac(self, value: Any) -> Optional[str]:
        """
        Convert value to PostgreSQL macaddr format (xx:xx:xx:xx:xx:xx).

        Handles various formats:
        - 00:0C:29:3E:4F:5A
        - 00-0C-29-3E-4F-5A
        - 000C.293E.4F5A
        - 000C293E4F5A

        Args:
            value: Raw MAC address

        Returns:
            Formatted MAC address or None
        """
        if not value:
            return None

        try:
            # Convert to string
            mac_str = str(value)

            # Strip all non-alphanumeric chars (separators, whitespace), then require 12
            # hex digits. The strip mirrors AssetValidator.is_valid_mac's cleaning, so a
            # MAC the pipeline accepts isn't dropped here for using an unusual separator.
            mac_clean = ''.join(filter(str.isalnum, mac_str)).upper()

            # Validate length (should be 12 hex characters)
            if len(mac_clean) != 12:
                logger.debug(f"Invalid MAC address length: {value}")
                return None

            # Validate hex characters
            if not _MAC_HEX12_RE.match(mac_clean):
                logger.debug(f"Invalid MAC address characters: {value}")
                return None

            # Format as xx:xx:xx:xx:xx:xx
            formatted = ':'.join(mac_clean[i:i+2] for i in range(0, 12, 2))
            return formatted

        except Exception as e:
            logger.debug(f"MAC address conversion failed for '{value}': {e}")
            return None

    # Epoch range: 2000-01-01 to 2050-01-01
    _EPOCH_S_MIN = 946684800
    _EPOCH_S_MAX = 2524608000

    # Fallback strptime formats (tried after fromisoformat fails)
    _TIMESTAMP_FORMATS = [
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y %I:%M:%S %p',
        '%m/%d/%Y',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y',
        '%d-%b-%Y %H:%M:%S',       # 15-Jan-2024 10:30:00
        '%b %d, %Y %H:%M:%S',      # Jan 15, 2024 10:30:00
        '%b %d, %Y',               # Jan 15, 2024
        '%Y/%m/%d %H:%M:%S',
        '%Y/%m/%d',
    ]

    def _convert_to_timestamp(self, value: Any) -> Optional[str]:
        """
        Convert various timestamp formats to ISO 8601 UTC string.

        Handles: ISO 8601, epoch seconds/milliseconds, common date formats.

        Args:
            value: Raw timestamp value (string, int, float)

        Returns:
            ISO 8601 string or None
        """
        if value is None:
            return None

        # Numeric: epoch seconds or milliseconds
        if isinstance(value, (int, float)):
            return self._epoch_to_iso(value)

        if not isinstance(value, str):
            return None

        text = value.strip()
        if not text:
            return None

        # String that looks numeric: might be epoch
        try:
            num = float(text)
            result = self._epoch_to_iso(num)
            if result:
                return result
        except ValueError:
            pass

        # ISO 8601 — Python 3.11+ handles Z, offsets, T/space separator
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass

        # Common date/time formats
        for fmt in self._TIMESTAMP_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue

        logger.debug(f"Could not parse timestamp: {value}")
        return str(value)

    def _epoch_to_iso(self, value: float) -> Optional[str]:
        """Convert epoch seconds or milliseconds to ISO 8601 UTC."""
        try:
            if self._EPOCH_S_MIN <= value <= self._EPOCH_S_MAX:
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            if self._EPOCH_S_MIN * 1000 <= value <= self._EPOCH_S_MAX * 1000:
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OSError, OverflowError):
            pass
        return None

    def _collect_extended_fields(self, raw_payload: Dict[str, Any],
                                 mapped: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Collect fields that are explicitly mapped to extended or data_source fields.

        Only includes fields that appear in the field mapping configuration.
        Unmapped fields are NOT preserved to keep raw_asset_data clean.

        Args:
            raw_payload: Original raw data
            mapped: Already mapped framework fields

        Returns:
            Dictionary of explicitly mapped extended fields or None if empty
        """
        extended = {}

        # Only include fields that are explicitly in the field mapping
        # and NOT already mapped to framework fields
        for target_field, source_path in self.field_mapping.items():
            # Skip framework fields (already in standard columns)
            if target_field in self.FRAMEWORK_FIELDS:
                continue

            # This is an extended/custom field - include it
            try:
                # Check if this references a virtual field
                if isinstance(source_path, str) and source_path in self._virtual_fields_cache:
                    value = self._virtual_fields_cache[source_path]
                # For backwards compatibility: check if this is a transformation object
                elif isinstance(source_path, dict) and source_path.get('type') == 'transform':
                    value = self._apply_transformation(raw_payload, source_path)
                elif isinstance(source_path, list):
                    values = self._resolve_mapping(raw_payload, source_path)
                    if values:
                        extended[target_field] = values
                    continue
                else:
                    value = self._extract_value(raw_payload, source_path)

                if value is not None:
                    extended[target_field] = value
            except Exception as e:
                logger.debug(f"Failed to extract extended field '{target_field}' from '{source_path}': {e}")

        return extended if extended else None


def create_field_mapper(integration: 'Integration') -> FieldMapper:
    """
    Factory function to create field mapper from integration.

    Args:
        integration: Integration model with field_mapping and transformations config

    Returns:
        Configured FieldMapper instance
    """
    transformations = integration.transformations if hasattr(integration, 'transformations') else {}
    return FieldMapper(integration.field_mapping, transformations)
