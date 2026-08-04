# SPDX-License-Identifier: AGPL-3.0-or-later
"""
CrowdStrike Falcon Connector

Collects endpoint/device inventory data from CrowdStrike Falcon via the
Hosts API (GET /devices/combined/devices/v1). Uses OAuth2 Client Credentials
authentication via the SDK auth strategy.

Scroll-based pagination with offset tokens. Rate-limit aware via
X-RateLimit-* response headers (HeaderRateLimiter). JSON responses.
"""

from typing import Dict, List, Iterator, Any, Optional

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import (
    OAuth2ClientCredentials,
    HeaderRateLimiter,
    CursorPaginator,
    ConfigSchema,
)


# Cloud base URL mapping — CrowdStrike deploys across regional clouds.
# Users must select the correct cloud for their subscription.
CLOUD_BASE_URLS = {
    'US-1': 'https://api.crowdstrike.com',
    'US-2': 'https://api.us-2.crowdstrike.com',
    'EU-1': 'https://api.eu-1.crowdstrike.com',
    'US-GOV-1': 'https://api.laggar.gcw.crowdstrike.com',
    'US-GOV-2': 'https://api.us-gov-2.crowdstrike.com',
}


@ConnectorRegistry.register
class CrowdStrikeConnector(BaseConnector):
    """
    CrowdStrike Falcon Connector

    Fetches endpoint/device inventory from CrowdStrike Falcon via the
    combined Hosts API. Requires API Client credentials with
    Hosts (Read) scope.
    """

    # Connector Metadata
    integration_type = "crowdstrike"
    display_name = "CrowdStrike Falcon"
    category = "EDR"
    description = "Collect endpoint and device data from CrowdStrike Falcon"
    logo_url = "/logos/crowdstrike.png"

    def __init__(self, config: Dict[str, Any]):
        self.page_size = int(config.get('page_size', 5000))

        # Resolve cloud base URL
        cloud = config.get('base_url', 'US-1')
        resolved_url = CLOUD_BASE_URLS.get(cloud)
        if resolved_url:
            self.base_url = resolved_url
            self.cloud = cloud
        elif cloud.startswith('http'):
            # Allow direct URL for testing or private deployments
            self.base_url = cloud.rstrip('/')
            self.cloud = 'CUSTOM'
        else:
            raise ValueError(
                f"Unknown CrowdStrike cloud: {cloud}. "
                f"Valid: {', '.join(CLOUD_BASE_URLS.keys())}"
            )

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return OAuth2ClientCredentials({
            'token_url': f"{self.base_url}/oauth2/token",
            'client_id': self.config.get('client_id'),
            'client_secret': self.config.get('client_secret'),
            'scope': '',  # CrowdStrike doesn't use scope
            'base_url': self.base_url,
        }, self.logger)

    def _build_rate_limiter(self):
        return HeaderRateLimiter(
            remaining_header='X-RateLimit-Remaining',
            retry_after_header='Retry-After',
            threshold=10,
            initial_rpm=100,
        )

    # ------------------------------------------------------------------
    # Config schema (class method -- no instance needed)
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_select(
                'base_url', 'CrowdStrike Cloud',
                options=[
                    {'value': 'US-1', 'label': 'US-1 (api.crowdstrike.com)'},
                    {'value': 'US-2', 'label': 'US-2 (api.us-2.crowdstrike.com)'},
                    {'value': 'EU-1', 'label': 'EU-1 (api.eu-1.crowdstrike.com)'},
                    {'value': 'US-GOV-1', 'label': 'US-GOV-1 (GovCloud)'},
                    {'value': 'US-GOV-2', 'label': 'US-GOV-2 (GovCloud)'},
                ],
                default='US-1',
                required=True,
                description='Select your CrowdStrike Falcon cloud region',
            )
            .add_string(
                'client_id', 'Client ID',
                required=True,
                placeholder='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
                description='OAuth2 API Client ID (32-character hex string)',
            )
            .add_password(
                'client_secret', 'Client Secret',
                required=True,
                description='OAuth2 API Client Secret (will be encrypted)',
            )
            .add_number(
                'page_size', 'Page Size',
                default=5000,
                min_val=100,
                max_val=10000,
                description='Number of device records per API request (5000 recommended)',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info(
                f"Testing connection to CrowdStrike Falcon ({self.cloud})..."
            )

            self.auth.authenticate()

            response = self.auth.request(
                '/devices/combined/devices/v1',
                params={'limit': 1, 'sort': 'hostname.asc'},
            )

            data = response.json()
            resources = data.get('resources', [])
            meta = data.get('meta', {})
            pagination = meta.get('pagination', {})
            total = pagination.get('total', len(resources))

            return {
                'success': True,
                'message': (
                    f'Successfully connected to CrowdStrike Falcon. '
                    f'Found {total:,} devices.'
                ),
                'metadata': {
                    'cloud': self.cloud,
                    'base_url': self.base_url,
                    'total_devices': total,
                },
            }

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e),
            }

    # ------------------------------------------------------------------
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        self.auth.authenticate()

        paginator = CursorPaginator(
            max_pages=self.config.get('max_pagination_pages', 100),
        )

        page_count = 0
        for page in paginator.pages(self._fetch_device_page):
            page_count += 1
            self.logger.info(f"Fetched page {page_count} ({len(page)} devices)")
            for record in page:
                yield self._normalize_record(record)

    # ------------------------------------------------------------------
    # Internal: page fetcher for CursorPaginator
    # ------------------------------------------------------------------

    def _fetch_device_page(self, cursor):
        """
        Fetch one page of devices from the combined endpoint.

        Args:
            cursor: Opaque scroll offset token, or None for the first page.

        Returns:
            (records, next_cursor) tuple for CursorPaginator.
        """
        params: Dict[str, Any] = {
            'limit': self.page_size,
            'sort': 'hostname.asc',
        }
        if cursor:
            params['offset'] = cursor

        response = self.auth.request(
            '/devices/combined/devices/v1',
            params=params,
        )

        data = response.json()
        resources = data.get('resources', [])

        pagination = data.get('meta', {}).get('pagination', {})
        next_cursor = pagination.get('offset')
        total = pagination.get('total')

        # CrowdStrike's combined endpoint keeps ``meta.pagination.offset``
        # populated even past the last page (typically 0 on a single-page
        # sorted result, or the cumulative position equal to ``total``).
        # Normalize those into ``None`` so CursorPaginator terminates.
        if not next_cursor:
            next_cursor = None
        elif (
            isinstance(next_cursor, int)
            and isinstance(total, int)
            and next_cursor >= total
        ):
            next_cursor = None

        return resources, next_cursor

    # ------------------------------------------------------------------
    # Internal: record normalization
    # ------------------------------------------------------------------

    def _normalize_record(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize a raw CrowdStrike device record into a flat dictionary.

        Handles:
        - Extracting key fields with consistent naming
        - Converting lists (tags, policies) to comma-separated strings
        - Flattening nested dicts (device_policies) into prefixed keys
        - Preserving None for missing values
        """
        record: Dict[str, Any] = {
            # Core identifiers
            'device_id': raw.get('device_id'),
            'cid': raw.get('cid'),
            'hostname': raw.get('hostname'),
            'machine_domain': raw.get('machine_domain'),
            'ou': raw.get('ou'),
            'site_name': raw.get('site_name'),

            # Network
            'local_ip': raw.get('local_ip'),
            'external_ip': raw.get('external_ip'),
            'mac_address': raw.get('mac_address'),

            # OS / Platform
            'platform_name': raw.get('platform_name'),
            'platform_id': raw.get('platform_id'),
            'os_version': raw.get('os_version'),
            'os_build': raw.get('os_build'),
            'os_product_name': raw.get('os_product_name'),
            'product_type': raw.get('product_type'),
            'product_type_desc': raw.get('product_type_desc'),

            # Hardware
            'serial_number': raw.get('serial_number'),
            'system_manufacturer': raw.get('system_manufacturer'),
            'system_product_name': raw.get('system_product_name'),
            'chassis_type': raw.get('chassis_type'),
            'chassis_type_desc': raw.get('chassis_type_desc'),
            'bios_manufacturer': raw.get('bios_manufacturer'),
            'bios_version': raw.get('bios_version'),

            # Agent
            'agent_version': raw.get('agent_version'),
            'agent_local_time': raw.get('agent_local_time'),
            'config_id_base': raw.get('config_id_base'),
            'config_id_build': raw.get('config_id_build'),
            'config_id_platform': raw.get('config_id_platform'),
            'status': raw.get('status'),
            'provision_status': raw.get('provision_status'),
            'reduced_functionality_mode': raw.get('reduced_functionality_mode'),

            # Timestamps
            'first_seen': raw.get('first_seen'),
            'last_seen': raw.get('last_seen'),
            'modified_timestamp': raw.get('modified_timestamp'),

            # Cloud instance metadata
            'service_provider': raw.get('service_provider'),
            'service_provider_account_id': raw.get(
                'service_provider_account_id'
            ),
            'instance_id': raw.get('instance_id'),
            'zone_group': raw.get('zone_group'),

            # Grouping
            'group_hash': raw.get('group_hash'),
            'kernel_version': raw.get('kernel_version'),
            'major_version': raw.get('major_version'),
            'minor_version': raw.get('minor_version'),
            'pointer_size': raw.get('pointer_size'),
        }

        # Tags — list of strings to comma-separated
        tags = raw.get('tags')
        if isinstance(tags, list):
            record['tags'] = ', '.join(str(t) for t in tags) if tags else ''
        else:
            record['tags'] = tags

        # Groups — list of dicts/strings
        groups = raw.get('groups')
        if isinstance(groups, list):
            record['groups'] = ', '.join(str(g) for g in groups) if groups else ''
        else:
            record['groups'] = groups

        # Policies — flatten device_policies dict
        policies = raw.get('device_policies')
        if isinstance(policies, dict):
            for policy_type, policy_data in policies.items():
                if isinstance(policy_data, dict):
                    record[f'policy_{policy_type}_id'] = policy_data.get(
                        'policy_id'
                    )
                    record[f'policy_{policy_type}_applied'] = policy_data.get(
                        'applied'
                    )
        elif policies is not None:
            record['device_policies'] = policies

        # Meta — flatten meta dict if present
        meta = raw.get('meta')
        if isinstance(meta, dict):
            record['meta_version'] = meta.get('version')
            record['meta_version_string'] = meta.get('version_string')

        return record

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        fields = [
            # Core identifiers
            {'name': 'device_id', 'path': 'device_id', 'type': 'string', 'description': 'CrowdStrike device ID', 'required': True},
            {'name': 'cid', 'path': 'cid', 'type': 'string', 'description': 'Customer ID'},
            {'name': 'hostname', 'path': 'hostname', 'type': 'string', 'description': 'Device hostname'},
            {'name': 'machine_domain', 'path': 'machine_domain', 'type': 'string', 'description': 'Machine domain'},
            {'name': 'ou', 'path': 'ou', 'type': 'string', 'description': 'Organizational unit'},
            {'name': 'site_name', 'path': 'site_name', 'type': 'string', 'description': 'Site name'},
            # Network
            {'name': 'local_ip', 'path': 'local_ip', 'type': 'string', 'description': 'Local IP address'},
            {'name': 'external_ip', 'path': 'external_ip', 'type': 'string', 'description': 'External IP address'},
            {'name': 'mac_address', 'path': 'mac_address', 'type': 'string', 'description': 'MAC address'},
            # OS / Platform
            {'name': 'platform_name', 'path': 'platform_name', 'type': 'string', 'description': 'Platform name (Windows, Mac, Linux)'},
            {'name': 'os_version', 'path': 'os_version', 'type': 'string', 'description': 'Operating system version'},
            {'name': 'os_build', 'path': 'os_build', 'type': 'string', 'description': 'OS build number'},
            {'name': 'os_product_name', 'path': 'os_product_name', 'type': 'string', 'description': 'OS product name'},
            {'name': 'product_type_desc', 'path': 'product_type_desc', 'type': 'string', 'description': 'Product type description (Workstation, Server, etc.)'},
            # Hardware
            {'name': 'serial_number', 'path': 'serial_number', 'type': 'string', 'description': 'Hardware serial number'},
            {'name': 'system_manufacturer', 'path': 'system_manufacturer', 'type': 'string', 'description': 'System manufacturer'},
            {'name': 'system_product_name', 'path': 'system_product_name', 'type': 'string', 'description': 'System product/model name'},
            {'name': 'chassis_type_desc', 'path': 'chassis_type_desc', 'type': 'string', 'description': 'Chassis type (Laptop, Desktop, etc.)'},
            {'name': 'bios_manufacturer', 'path': 'bios_manufacturer', 'type': 'string', 'description': 'BIOS manufacturer'},
            {'name': 'bios_version', 'path': 'bios_version', 'type': 'string', 'description': 'BIOS version'},
            # Agent
            {'name': 'agent_version', 'path': 'agent_version', 'type': 'string', 'description': 'Falcon sensor version'},
            {'name': 'status', 'path': 'status', 'type': 'string', 'description': 'Device status (normal, contained, etc.)'},
            {'name': 'reduced_functionality_mode', 'path': 'reduced_functionality_mode', 'type': 'string', 'description': 'Reduced functionality mode status'},
            # Timestamps
            {'name': 'first_seen', 'path': 'first_seen', 'type': 'datetime', 'description': 'First seen timestamp'},
            {'name': 'last_seen', 'path': 'last_seen', 'type': 'datetime', 'description': 'Last seen timestamp'},
            {'name': 'modified_timestamp', 'path': 'modified_timestamp', 'type': 'datetime', 'description': 'Last modified timestamp'},
            # Cloud
            {'name': 'service_provider', 'path': 'service_provider', 'type': 'string', 'description': 'Cloud service provider (AWS, Azure, GCP)'},
            {'name': 'instance_id', 'path': 'instance_id', 'type': 'string', 'description': 'Cloud instance ID'},
            # Grouping
            {'name': 'tags', 'path': 'tags', 'type': 'string', 'description': 'Falcon Grouping Tags (comma-separated)'},
            {'name': 'groups', 'path': 'groups', 'type': 'string', 'description': 'Host group IDs (comma-separated)'},
        ]
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
