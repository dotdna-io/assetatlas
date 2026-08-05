#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SentinelOne Ranger Connector

Collects network-discovered device data from SentinelOne Ranger via REST API.
Uses API Token authentication with Service User credentials.
"""

from typing import Dict, List, Iterator, Any

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import APIKeyAuth, ConfigSchema, CursorPaginator


@ConnectorRegistry.register
class SentinelOneRangerConnector(BaseConnector):
    """
    SentinelOne Ranger Connector

    Fetches network-discovered device data from SentinelOne Ranger.
    Requires Service User with Account-level Viewer access.
    """

    # Connector Metadata
    integration_type = "sentinelone_ranger"
    display_name = "SentinelOne Ranger"
    category = "Network Discovery"
    description = "Collect network-discovered device data from SentinelOne Ranger"
    logo_url = "/logos/sentinel one.webp"

    # API version
    API_VERSION = "v2.1"

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get('url', '').rstrip('/')
        self.api_token = config.get('api_token')
        self.page_size = 1000
        self.max_pagination_pages = int(config.get('max_pagination_pages', 500))

        if not self.base_url:
            raise ValueError("SentinelOne Ranger URL is required")
        if not self.api_token:
            raise ValueError("SentinelOne Ranger API token is required")

        # Parse optional scoping before super().__init__ so _build_auth has them
        self.account_ids = self._parse_csv(config.get('account_ids', ''))
        self.site_ids = self._parse_csv(config.get('site_ids', ''))

        super().__init__(config)

    @staticmethod
    def _parse_csv(value: str) -> List[str]:
        """Parse a comma-separated string into a list of stripped, non-empty values."""
        if not value:
            return []
        return [v.strip() for v in value.split(',') if v.strip()]

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_string(
                'url', 'SentinelOne URL',
                required=True,
                placeholder='https://your-tenant.sentinelone.net',
                description='SentinelOne instance URL (e.g., https://your-tenant.sentinelone.net)',
            )
            .add_password(
                'api_token', 'API Token',
                required=True,
                description='Service User API token with Account-level Viewer access (will be encrypted)',
            )
            .add_string(
                'account_ids', 'Account IDs',
                required=False,
                placeholder='e.g., 123456789, 987654321',
                description='Comma-separated account IDs to scope data collection (optional)',
            )
            .add_string(
                'site_ids', 'Site IDs',
                required=False,
                placeholder='e.g., 123456789, 987654321',
                description='Comma-separated site IDs to scope data collection (optional)',
            )
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                default=500,
                min_val=1,
                max_val=10000,
                description=(
                    'Safety cap on API pages fetched per scope per collection run — it '
                    'stops a pagination loop that never terminates. At the fixed page '
                    'size of 1,000, the default cap of 500 pages collects up to '
                    '500,000 devices per scope.'
                ),
            )
            .build()
        )

    def _build_auth(self) -> APIKeyAuth:
        return APIKeyAuth(
            {
                'api_key': self.api_token,
                'api_key_header': 'Authorization',
                'api_key_prefix': 'ApiToken ',
                'base_url': self.base_url,
            },
            self.logger,
        )

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to SentinelOne Ranger...")

            response = self.auth.request(
                f'/web/api/{self.API_VERSION}/accounts',
                params={'limit': 1}
            )
            accounts_data = response.json()
            accounts = accounts_data.get('data', [])

            scopes = self._resolve_scopes()
            total_devices = 0
            for scope in scopes:
                params = {'limit': 1}
                params.update(scope)
                ranger_response = self.auth.request(
                    f'/web/api/{self.API_VERSION}/ranger/table-view',
                    params=params
                )
                ranger_data = ranger_response.json()
                total_devices += ranger_data.get('pagination', {}).get('totalItems', 0)

            return {
                'success': True,
                'message': (
                    f'Successfully connected to SentinelOne Ranger. '
                    f'Found {len(accounts)} account(s), {total_devices:,} devices.'
                ),
                'metadata': {
                    'base_url': self.base_url,
                    'accounts_count': len(accounts),
                    'total_devices': total_devices,
                    'api_version': self.API_VERSION
                }
            }

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e)
            }

    def _resolve_scopes(self) -> List[Dict[str, str]]:
        """
        Determine which account/site scopes to query.

        - If account_ids configured: use those directly.
        - If site_ids configured: use those directly.
        - Otherwise: auto-discover via API probing.
        """
        if self.account_ids:
            return [{"accountIds": aid} for aid in self.account_ids]
        if self.site_ids:
            return [{"siteIds": sid} for sid in self.site_ids]
        return self._auto_discover_scopes()

    def _auto_discover_scopes(self) -> List[Dict[str, str]]:
        """
        Auto-discover accessible scopes by probing accounts, then falling
        back to site-level access when account-level Ranger returns 403.
        """
        from requests.exceptions import HTTPError

        response = self.auth.request(
            f'/web/api/{self.API_VERSION}/accounts',
            params={'limit': 100}
        )
        accounts = response.json().get('data', [])

        scopes: List[Dict[str, str]] = []
        for account in accounts:
            account_id = str(account['id'])
            try:
                probe = self.auth.request(
                    f'/web/api/{self.API_VERSION}/ranger/table-view',
                    params={'accountIds': account_id, 'limit': 1}
                )
                data = probe.json().get('data', [])
                if data:
                    scopes.append({"accountIds": account_id})
            except HTTPError as e:
                if (hasattr(e, 'response') and e.response is not None
                        and e.response.status_code == 403):
                    scopes.extend(self._probe_sites(account_id))
                else:
                    raise

        return scopes

    def _probe_sites(self, account_id: str) -> List[Dict[str, str]]:
        """
        Probe individual sites within an account for Ranger access.

        Falls back here when account-level Ranger access returns 403.
        Sites that also return 403 are silently skipped.
        """
        from requests.exceptions import HTTPError

        response = self.auth.request(
            f'/web/api/{self.API_VERSION}/sites',
            params={'accountId': account_id, 'limit': 200}
        )
        sites = response.json().get('data', {}).get('sites', [])

        scopes: List[Dict[str, str]] = []
        for site in sites:
            site_id = str(site['id'])
            try:
                probe = self.auth.request(
                    f'/web/api/{self.API_VERSION}/ranger/table-view',
                    params={'siteIds': site_id, 'limit': 1}
                )
                data = probe.json().get('data', [])
                if data:
                    scopes.append({"siteIds": site_id})
            except HTTPError as e:
                if (hasattr(e, 'response') and e.response is not None
                        and e.response.status_code == 403):
                    continue
                raise

        return scopes

    def _make_fetch_devices_page(self, scope: Dict[str, str]):
        """Return a fetch function bound to the given scope."""
        def fetch_page(cursor):
            params = {'limit': self.page_size}
            params.update(scope)
            if cursor:
                params['cursor'] = cursor

            response = self.auth.request(
                f'/web/api/{self.API_VERSION}/ranger/table-view',
                params=params,
            )
            data = response.json()
            devices = data.get('data', [])
            next_cursor = data.get('pagination', {}).get('nextCursor')
            return devices, next_cursor
        return fetch_page

    def _fetch_devices(self, scope: Dict[str, str]) -> Iterator[Dict[str, Any]]:
        """
        Fetch devices from Ranger table-view with cursor-based pagination.

        Args:
            scope: Scope parameters (e.g. {"accountIds": "123"} or {"siteIds": "456"})

        Yields:
            Individual device records
        """
        paginator = CursorPaginator(max_pages=self.max_pagination_pages)
        for page in paginator.pages(self._make_fetch_devices_page(scope)):
            for device in page:
                yield device
        scope_parts = []
        if scope.get('accountIds'):
            scope_parts.append(f"account {scope['accountIds']}")
        if scope.get('siteIds'):
            scope_parts.append(f"site {scope['siteIds']}")
        self.warn_if_truncated(
            paginator,
            f"SentinelOne Ranger devices ({', '.join(scope_parts)})",
        )

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """
        Collect device data from SentinelOne Ranger across all resolved scopes.

        Yields:
            Device records with connector-specific metadata fields set.
        """
        scopes = self._resolve_scopes()

        for scope in scopes:
            scope_account = scope.get('accountIds')
            scope_site = scope.get('siteIds')

            for device in self._fetch_devices(scope):
                device['_record_type'] = 'ranger_device'
                device['_scope_account_id'] = scope_account
                device['_scope_site_id'] = scope_site
                yield device

    def get_schema(self) -> Dict[str, Any]:
        """Return field schema for mapping configuration."""
        fields = [
            # Core identifiers
            {'name': 'id', 'path': 'id', 'type': 'string', 'description': 'Unique device identifier', 'required': True},
            {'name': 'localIp', 'path': 'localIp', 'type': 'string', 'description': 'Local IP address'},
            {'name': 'macAddress', 'path': 'macAddress', 'type': 'string', 'description': 'MAC address'},
            {'name': 'hostnames', 'path': 'hostnames', 'type': 'array', 'description': 'List of hostnames'},
            {'name': 'ipAddresses', 'path': 'ipAddresses', 'type': 'array', 'description': 'List of IP addresses'},

            # Classification
            {'name': 'deviceType', 'path': 'deviceType', 'type': 'string', 'description': 'Device type classification'},
            {'name': 'deviceFunction', 'path': 'deviceFunction', 'type': 'string',
             'description': 'Device function or role'},
            {'name': 'manufacturer', 'path': 'manufacturer', 'type': 'string', 'description': 'Device manufacturer'},

            # OS fingerprint
            {'name': 'osName', 'path': 'osName', 'type': 'string', 'description': 'Operating system name'},
            {'name': 'osType', 'path': 'osType', 'type': 'string', 'description': 'Operating system type'},
            {'name': 'osVersion', 'path': 'osVersion', 'type': 'string', 'description': 'Operating system version'},
            {'name': 'previousOsType', 'path': 'previousOsType', 'type': 'string',
             'description': 'Previous OS type'},
            {'name': 'previousOsVersion', 'path': 'previousOsVersion', 'type': 'string',
             'description': 'Previous OS version'},
            {'name': 'previousDeviceFunction', 'path': 'previousDeviceFunction', 'type': 'string',
             'description': 'Previous device function'},

            # Network context
            {'name': 'domain', 'path': 'domain', 'type': 'string', 'description': 'Domain name'},
            {'name': 'networkName', 'path': 'networkName', 'type': 'string', 'description': 'Network name'},
            {'name': 'siteName', 'path': 'siteName', 'type': 'string', 'description': 'Site name'},
            {'name': 'subnetAddress', 'path': 'subnetAddress', 'type': 'string', 'description': 'Subnet address'},
            {'name': 'gatewayIpAddress', 'path': 'gatewayIpAddress', 'type': 'string',
             'description': 'Gateway IP address'},
            {'name': 'gatewayMacAddress', 'path': 'gatewayMacAddress', 'type': 'string',
             'description': 'Gateway MAC address'},
            {'name': 'externalIp', 'path': 'externalIp', 'type': 'string', 'description': 'External IP address'},

            # Timestamps
            {'name': 'firstSeen', 'path': 'firstSeen', 'type': 'datetime', 'description': 'First discovery timestamp'},
            {'name': 'lastSeen', 'path': 'lastSeen', 'type': 'datetime', 'description': 'Last seen timestamp'},

            # Management status
            {'name': 'agentId', 'path': 'agentId', 'type': 'string', 'description': 'Associated SentinelOne agent ID'},
            {'name': 'managedState', 'path': 'managedState', 'type': 'string',
             'description': 'Management state'},
            {'name': 'deviceReview', 'path': 'deviceReview', 'type': 'string', 'description': 'Device review status'},
            {'name': 'deviceReviewLog', 'path': 'deviceReviewLog', 'type': 'array',
             'description': 'Device review history log'},

            # Discovery details
            {'name': 'discoveryMethods', 'path': 'discoveryMethods', 'type': 'array',
             'description': 'Discovery methods used'},
            {'name': 'hasIdentity', 'path': 'hasIdentity', 'type': 'boolean',
             'description': 'Has identity information'},
            {'name': 'hasUserLabel', 'path': 'hasUserLabel', 'type': 'boolean',
             'description': 'Has user-assigned label'},
            {'name': 'labelUpdatedAt', 'path': 'labelUpdatedAt', 'type': 'datetime',
             'description': 'Label last updated timestamp'},
            {'name': 'labelUserName', 'path': 'labelUserName', 'type': 'string',
             'description': 'Label last updated by'},

            # Network details
            {'name': 'networks', 'path': 'networks', 'type': 'array', 'description': 'Network interface details'},
            {'name': 'tags', 'path': 'tags', 'type': 'object', 'description': 'Device tags'},
            {'name': 'tcpPorts', 'path': 'tcpPorts', 'type': 'array', 'description': 'Open TCP ports'},
            {'name': 'udpPorts', 'path': 'udpPorts', 'type': 'array', 'description': 'Open UDP ports'},
        ]

        return {
            'source': 'sentinelone_ranger',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3
        }
