# SPDX-License-Identifier: AGPL-3.0-or-later
"""Armis Centrix integration connector.

Fetches device inventory from Armis via the Search API with ASQ filtering.
Uses /api/v1/search/?aql=in:devices with field selection and cursor pagination.
Auth: secret_key -> temporary access_token (15 min TTL) via OAuth2TokenExchange.
"""

from typing import Any, Dict, Iterator, List, Optional

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import (
    OAuth2TokenExchange,
    AdaptiveRateLimiter,
    CursorPaginator,
    ConfigSchema,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields to request from the Armis Search API (camelCase as in ASQ).
# Based on the Armis API v4.1 default device response + Splunk TA-armis fields.
DEVICE_FIELDS = ','.join([
    'id', 'name', 'type', 'category',
    'ipAddress', 'ipv6', 'macAddress',
    'manufacturer', 'model', 'serialNumber',
    'operatingSystem', 'operatingSystemVersion',
    'firstSeen', 'lastSeen',
    'riskLevel', 'purdueLevel',
    'site', 'sensor', 'tags', 'boundaries',
    'accessSwitch', 'dataSources', 'businessImpact',
    'customProperties', 'visibility',
])

MAX_PAGE_SIZE = 5000
DEFAULT_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

@ConnectorRegistry.register
class ArmisConnector(BaseConnector):
    """Connector for Armis Centrix agentless device security platform."""

    integration_type = 'armis'
    display_name = 'Armis Platform'
    category = 'Asset Discovery'
    description = 'Collect device inventory from Armis Centrix agentless security platform'
    logo_url = '/logos/armis.png'

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get('tenant_url', '').rstrip('/')
        if not self.base_url:
            raise ValueError('Tenant URL is required')

        self.api_key = config.get('api_key', '')
        if not self.api_key:
            raise ValueError('API secret key is required')

        self.asq_filter = config.get('asq_filter', '')
        self.page_size = min(int(config.get('page_size', DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return OAuth2TokenExchange({
            'token_url': f"{self.base_url}/api/v1/access_token/",
            'secret_key': self.api_key,
            'secret_field': 'secret_key',
            'token_path': 'data.access_token',
            'base_url': self.base_url,
        }, self.logger)

    def _build_rate_limiter(self):
        return AdaptiveRateLimiter(
            initial_rpm=100,
            min_rpm=10,
            max_rpm=200,
            decrease_factor=0.5,
            increase_factor=1.1,
        )

    # ------------------------------------------------------------------
    # Config schema (class method -- no instance needed)
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_url(
                'tenant_url', 'Tenant URL',
                required=True,
                placeholder='https://company.armis.com',
                description='Your Armis Centrix tenant URL',
            )
            .add_password(
                'api_key', 'API Secret Key',
                required=True,
                description='API secret key from Settings > API Management',
            )
            .add_string(
                'asq_filter', 'ASQ Filter',
                required=False,
                placeholder='timeFrame:"7 Days"',
                description='Optional ASQ filter conditions (e.g., timeFrame:"30 Days" type:"Laptops")',
            )
            .add_number(
                'page_size', 'Page Size',
                default=DEFAULT_PAGE_SIZE,
                min_val=1,
                max_val=MAX_PAGE_SIZE,
                description='Number of records per API request (max 5000)',
            )
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                default=500,
                min_val=1,
                max_val=10000,
                description=(
                    'Safety cap on API pages fetched per collection run — it stops a '
                    'pagination loop that never terminates. At the default page size '
                    'of 1,000, the default cap of 500 pages collects up to 500,000 devices.'
                ),
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        """Validate API key and report device count."""
        try:
            self.auth.authenticate()

            response = self.auth.request(
                '/api/v1/search/',
                params={
                    'aql': self._build_asq(),
                    'length': '1',
                },
            )
            data = response.json()
            total = data.get('data', {}).get('total', 0)

            return {
                'success': True,
                'message': f'Connected to Armis. {total:,} devices available.',
                'metadata': {
                    'total_devices': total,
                    'tenant_url': self.base_url,
                },
            }
        except Exception as exc:
            return {'success': False, 'message': f'Connection failed: {exc}'}

    # ------------------------------------------------------------------
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        self.auth.authenticate()

        paginator = CursorPaginator(
            max_pages=self.config.get('max_pagination_pages', 500),
        )

        page_count = 0
        for page in paginator.pages(self._fetch_device_page):
            page_count += 1
            self.logger.info(f"Fetched page {page_count} ({len(page)} devices)")
            for record in page:
                yield self._flatten_record(record)
        self.warn_if_truncated(paginator, "Armis devices")

    # ------------------------------------------------------------------
    # Internal: page fetcher for CursorPaginator
    # ------------------------------------------------------------------

    def _fetch_device_page(self, cursor):
        """
        Fetch one page of devices from the Search API.

        Args:
            cursor: Numeric offset, or None for the first page.

        Returns:
            (records, next_cursor) tuple for CursorPaginator.
        """
        params = {
            'aql': self._build_asq(),
            'length': str(self.page_size),
            'from': str(cursor or 0),
            'orderBy': 'id',
            'fields': DEVICE_FIELDS,
        }

        response = self.auth.request('/api/v1/search/', params=params)
        data = response.json()

        results = data.get('data', {}).get('results', [])
        next_offset = data.get('data', {}).get('next')

        return results, next_offset

    # ------------------------------------------------------------------
    # ASQ builder
    # ------------------------------------------------------------------

    def _build_asq(self) -> str:
        """Build ASQ query string."""
        base = 'in:devices'
        if self.asq_filter:
            base += f' {self.asq_filter}'
        return base

    # ------------------------------------------------------------------
    # Record flattening
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten nested objects so every value is a scalar or list of scalars.

        Armis returns nested JSON for site, sensor, dataSources, customProperties,
        and tags. These cause '[object Object]' in the mapping UI if stored raw.
        """
        flat = {}
        for key, value in record.items():
            if key == 'site' and isinstance(value, dict):
                flat['siteName'] = value.get('name', '')
                flat['siteLocation'] = value.get('location', '')
            elif key == 'sensor' and isinstance(value, dict):
                flat['sensorName'] = value.get('name', '')
                flat['sensorType'] = value.get('type', '')
            elif key == 'customProperties' and isinstance(value, dict):
                # Flatten each custom property into its own field
                for cp_key, cp_val in value.items():
                    flat[f'cp_{cp_key}'] = cp_val
            elif key == 'dataSources' and isinstance(value, list):
                # Extract data source names as a comma-separated string
                names = []
                for ds in value:
                    if isinstance(ds, dict):
                        names.append(ds.get('name', ''))
                    else:
                        names.append(str(ds))
                flat['dataSources'] = ', '.join(n for n in names if n)
            elif key == 'tags' and isinstance(value, list):
                flat['tags'] = ', '.join(str(t) for t in value)
            elif key == 'ipv6' and isinstance(value, list):
                flat['ipv6'] = ', '.join(str(ip) for ip in value)
            elif isinstance(value, (dict, list)):
                # Catch-all: stringify remaining complex types
                flat[key] = str(value) if value else ''
            else:
                flat[key] = value
        return flat

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        """Field schema for mapping UI."""
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': [
                {'name': 'id', 'path': 'id', 'type': 'number', 'description': 'Armis device ID'},
                {'name': 'name', 'path': 'name', 'type': 'string', 'description': 'Device name'},
                {'name': 'type', 'path': 'type', 'type': 'string', 'description': 'Device type (e.g., Laptops, Servers)'},
                {'name': 'category', 'path': 'category', 'type': 'string', 'description': 'Device category (e.g., Computers)'},
                {'name': 'ipAddress', 'path': 'ipAddress', 'type': 'string', 'description': 'IPv4 address(es)'},
                {'name': 'ipv6', 'path': 'ipv6', 'type': 'string', 'description': 'IPv6 address(es)'},
                {'name': 'macAddress', 'path': 'macAddress', 'type': 'string', 'description': 'MAC address(es)'},
                {'name': 'manufacturer', 'path': 'manufacturer', 'type': 'string', 'description': 'Device manufacturer/brand'},
                {'name': 'model', 'path': 'model', 'type': 'string', 'description': 'Device model'},
                {'name': 'serialNumber', 'path': 'serialNumber', 'type': 'string', 'description': 'Serial number'},
                {'name': 'operatingSystem', 'path': 'operatingSystem', 'type': 'string', 'description': 'Operating system'},
                {'name': 'operatingSystemVersion', 'path': 'operatingSystemVersion', 'type': 'string', 'description': 'OS version'},
                {'name': 'firstSeen', 'path': 'firstSeen', 'type': 'datetime', 'description': 'First seen timestamp'},
                {'name': 'lastSeen', 'path': 'lastSeen', 'type': 'datetime', 'description': 'Last seen timestamp'},
                {'name': 'riskLevel', 'path': 'riskLevel', 'type': 'number', 'description': 'Risk level (1-100)'},
                {'name': 'purdueLevel', 'path': 'purdueLevel', 'type': 'number', 'description': 'Purdue model level'},
                {'name': 'siteName', 'path': 'siteName', 'type': 'string', 'description': 'Site name'},
                {'name': 'siteLocation', 'path': 'siteLocation', 'type': 'string', 'description': 'Site location'},
                {'name': 'sensorName', 'path': 'sensorName', 'type': 'string', 'description': 'Last sensor name'},
                {'name': 'sensorType', 'path': 'sensorType', 'type': 'string', 'description': 'Last sensor type'},
                {'name': 'tags', 'path': 'tags', 'type': 'string', 'description': 'Device tags'},
                {'name': 'boundaries', 'path': 'boundaries', 'type': 'string', 'description': 'Armis boundaries'},
                {'name': 'accessSwitch', 'path': 'accessSwitch', 'type': 'string', 'description': 'Access switch name'},
                {'name': 'dataSources', 'path': 'dataSources', 'type': 'string', 'description': 'Contributing data sources'},
                {'name': 'businessImpact', 'path': 'businessImpact', 'type': 'string', 'description': 'Business impact level'},
                {'name': 'visibility', 'path': 'visibility', 'type': 'string', 'description': 'Device visibility'},
            ],
        }
