# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Manual File Upload Connector

Allows manual upload of asset data via CSV or JSON files.
Useful for one-time imports, migrations, or systems without APIs.

Supported formats:
    - CSV: Standard comma-separated values with header row
    - JSON: Array of objects or newline-delimited JSON

No auth — this connector parses local files, not HTTP APIs.
"""

import csv
import json
import io
import logging
import re
from typing import Dict, List, Any, Iterator, Optional

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import ConfigSchema


@ConnectorRegistry.register
class ManualUploadConnector(BaseConnector):
    """
    Manual file upload connector for CSV and JSON data.

    Configuration:
        file_format: 'csv' or 'json'
        file_data: Base64-encoded file content or raw string content
        delimiter: CSV delimiter (default: ',')
        encoding: File encoding (default: 'utf-8')
    """

    # Connector metadata
    integration_type = "manual_upload"
    display_name = "Manual File Upload"
    category = "Data Import"
    description = "Import asset data from CSV or JSON files"
    logo_url = "/logos/assetatlas.png"

    SUPPORTED_FORMATS = ['csv', 'json']

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        # Extract configuration before super().__init__ so _build_auth can use it
        self.file_format = config.get('file_format', 'csv').lower()
        self.file_data = config.get('file_data')
        self.delimiter = config.get('delimiter', ',')
        self.encoding = config.get('encoding', 'utf-8')
        self.file_name = config.get('file_name', 'upload')

        if self.file_format not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {self.file_format}. "
                f"Must be one of: {self.SUPPORTED_FORMATS}"
            )

        # Parsed data cache
        self._cached_data: Optional[List[Dict[str, Any]]] = None

        super().__init__(config, logger)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _get_source(self) -> str:
        return f'manual_upload:{self.file_format}'

    # No _build_auth — base returns None (no auth needed)

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_select(
                'file_format', 'File Format',
                options=[
                    {'value': 'csv', 'label': 'CSV (Comma-Separated Values)'},
                    {'value': 'json', 'label': 'JSON (JavaScript Object Notation)'},
                ],
                default='csv',
                required=True,
                description='Format of the uploaded file',
            )
            .add_string(
                'file_data', 'Upload File',
                required=True,
                description='File content (base64-encoded or raw string)',
            )
            .add_string(
                'delimiter', 'CSV Delimiter',
                required=False,
                placeholder=',',
                description='Delimiter character for CSV files',
            )
            .add_string(
                'encoding', 'File Encoding',
                required=False,
                placeholder='utf-8',
                description='Character encoding of the file',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            records = self._parse_file()
            record_count = len(records)

            if record_count == 0:
                return {
                    'success': False,
                    'message': 'File is empty or contains no valid records',
                    'error': 'No records found',
                }

            field_names = list(records[0].keys())

            return {
                'success': True,
                'message': (
                    f'Successfully parsed {self.file_format.upper()} file. '
                    f'Found {record_count:,} records with {len(field_names)} fields.'
                ),
                'metadata': {
                    'record_count': record_count,
                    'field_count': len(field_names),
                    'fields': field_names[:20],
                    'file_format': self.file_format,
                    'file_name': self.file_name,
                },
            }

        except Exception as e:
            self.logger.error(f"File parsing test failed: {e}")
            return {
                'success': False,
                'message': f'Failed to parse file: {str(e)}',
                'error': str(e),
            }

    # ------------------------------------------------------------------
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        records = self._parse_file()

        for idx, record in enumerate(records):
            record['_record_number'] = idx + 1
            yield record

    # ------------------------------------------------------------------
    # Internal: file parsing
    # ------------------------------------------------------------------

    def _parse_file(self) -> List[Dict[str, Any]]:
        if self._cached_data is not None:
            return self._cached_data

        if self.file_data and isinstance(self.file_data, str):
            if self.file_data.startswith('data:'):
                import base64
                file_data = (
                    self.file_data.split(',', 1)[1]
                    if ',' in self.file_data
                    else self.file_data
                )
                content = base64.b64decode(file_data).decode(self.encoding)
            else:
                content = self.file_data
        else:
            raise ValueError("No file data provided")

        if self.file_format == 'csv':
            records = self._parse_csv(content)
        elif self.file_format == 'json':
            records = self._parse_json(content)
        else:
            raise ValueError(f"Unsupported format: {self.file_format}")

        self._cached_data = records
        return records

    def _parse_csv(self, content: str) -> List[Dict[str, Any]]:
        records = []
        csv_file = io.StringIO(content)
        reader = csv.DictReader(csv_file, delimiter=self.delimiter)

        for row in reader:
            clean_row = {k.strip(): v.strip() for k, v in row.items() if k and v}
            if clean_row:
                records.append(clean_row)

        self.logger.info(f"Parsed {len(records)} records from CSV")
        return records

    def _parse_json(self, content: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(content)

            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = [data]
            else:
                raise ValueError("JSON must be an array or object")

            self.logger.info(f"Parsed {len(records)} records from JSON array")
            return records

        except json.JSONDecodeError:
            records = []
            for line in content.strip().split('\n'):
                if line.strip():
                    records.append(json.loads(line))

            self.logger.info(f"Parsed {len(records)} records from NDJSON")
            return records

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        try:
            records = self._parse_file()

            if not records:
                return {
                    'source': f'manual_upload:{self.file_format}',
                    'version': '1.0',
                    'fields': [],
                    'sample_record_count': 0,
                }

            field_names = list(records[0].keys())

            fields = []
            for field_name in field_names:
                sample_values = [
                    r.get(field_name) for r in records[:10] if field_name in r
                ]
                field_type = self._infer_field_type(sample_values)

                fields.append({
                    'name': field_name,
                    'path': field_name,
                    'type': field_type,
                    'description': f'Field from uploaded {self.file_format.upper()} file',
                    'required': False,
                })

            return {
                'source': f'manual_upload:{self.file_format}',
                'version': '1.0',
                'fields': fields,
                'sample_record_count': min(len(records), 5),
                'file_name': self.file_name,
                'total_records': len(records),
            }

        except Exception as e:
            self.logger.error(f"Error getting schema: {e}")
            return {
                'source': f'manual_upload:{self.file_format}',
                'version': '1.0',
                'fields': [],
                'error': str(e),
            }

    def _infer_field_type(self, values: List[Any]) -> str:
        if not values:
            return 'string'

        try:
            all_numeric = all(
                isinstance(v, (int, float))
                or (isinstance(v, str) and v.replace('.', '', 1).replace('-', '', 1).isdigit())
                for v in values if v
            )
            if all_numeric:
                return 'number'
        except Exception:
            pass

        try:
            bool_values = {'true', 'false', '1', '0', 'yes', 'no'}
            all_bool = all(
                isinstance(v, bool)
                or (isinstance(v, str) and v.lower() in bool_values)
                for v in values if v
            )
            if all_bool:
                return 'boolean'
        except Exception:
            pass

        try:
            date_pattern = re.compile(r'\d{4}-\d{2}-\d{2}')
            all_dates = all(
                date_pattern.search(str(v)) is not None
                for v in values if v
            )
            if all_dates:
                return 'datetime'
        except Exception:
            pass

        return 'string'
