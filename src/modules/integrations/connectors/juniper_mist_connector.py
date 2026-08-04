# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Juniper Mist Connector

Collects network device inventory and wireless client data from Juniper Mist.
- Devices: merged from /orgs/{org_id}/inventory + /orgs/{org_id}/stats/devices
- Clients: per-site from /sites/{site_id}/stats/clients (configurable lookback)
- Auth: APIKeyAuth with header "Authorization: Token {api_token}"
- Pagination: page + limit params
"""

from typing import Any, Dict, Iterator, List, Optional
import time

import requests

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import APIKeyAuth, ConfigSchema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIST_REGIONS = {
    'global': 'https://api.mist.com/api/v1',
    'eu': 'https://api.eu.mist.com/api/v1',
}

MAX_PAGINATION_PAGES = 100
MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100
API_TIMEOUT = 120
RATE_LIMIT_THRESHOLD = 10
DEFAULT_RATE_LIMIT_WAIT = 60
DEFAULT_CLIENT_DURATION = '7d'


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

@ConnectorRegistry.register
class JuniperMistConnector(BaseConnector):
    """
    Juniper Mist Connector

    Collects network device inventory and wireless client data from the
    Juniper Mist cloud platform. Uses token-based authentication
    (Authorization: Token <api_token>).
    """

    # Connector Metadata
    integration_type = 'juniper_mist'
    display_name = 'Juniper Mist'
    category = 'Network Infrastructure'
    description = 'Collect network devices and wireless clients from Juniper Mist'
    logo_url = '/logos/juniper.png'

    def __init__(self, config: Dict[str, Any]):
        # Extract configuration before super().__init__
        self.api_token = config.get('api_token', '')
        if not self.api_token:
            raise ValueError("API token is required for Juniper Mist")

        self.org_id = config.get('org_id', '')

        # Collection toggles
        self.collect_devices = config.get('collect_devices', True)
        self.collect_clients = config.get('collect_clients', True)
        self.client_duration = config.get('client_duration', DEFAULT_CLIENT_DURATION)

        # Page size
        raw_page_size = config.get('page_size')
        if raw_page_size is None:
            self.page_size = DEFAULT_PAGE_SIZE
        else:
            self.page_size = min(int(raw_page_size), MAX_PAGE_SIZE)

        # Resolve base URL from region
        self.region = config.get('region', 'global')
        self.base_url = MIST_REGIONS.get(self.region, MIST_REGIONS['global'])

        # Rate limit tracking
        self._rate_limit_remaining: Optional[int] = None

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return APIKeyAuth(
            config={
                'api_key': self.api_token,
                'api_key_header': 'Authorization',
                'api_key_prefix': 'Token ',
                'base_url': self.base_url,
            },
            logger=self.logger,
        )

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_select(
                'region', 'Mist Region',
                options=[
                    {'value': 'global', 'label': 'Global (api.mist.com)'},
                    {'value': 'eu', 'label': 'EU (api.eu.mist.com)'},
                ],
                default='global',
                required=True,
                description='Select the Juniper Mist cloud region',
            )
            .add_password(
                'api_token', 'API Token',
                required=True,
                description='Juniper Mist API token (will be encrypted)',
            )
            .add_string(
                'org_id', 'Organization ID',
                required=False,
                placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                description='Juniper Mist organization UUID (leave empty to auto-discover all orgs)',
            )
            .add_number(
                'page_size', 'Page Size',
                default=DEFAULT_PAGE_SIZE,
                min_val=1,
                max_val=MAX_PAGE_SIZE,
                description='Number of records per API request',
            )
            .add_boolean(
                'collect_devices', 'Collect Devices',
                default=True,
                description='Collect network device inventory',
            )
            .add_boolean(
                'collect_clients', 'Collect Wireless Clients',
                default=True,
                description='Collect wireless client data',
            )
            .add_select(
                'client_duration', 'Client Lookback Window',
                options=[
                    {'value': '1d', 'label': '1 day'},
                    {'value': '7d', 'label': '7 days'},
                    {'value': '14d', 'label': '14 days'},
                    {'value': '30d', 'label': '30 days'},
                ],
                default=DEFAULT_CLIENT_DURATION,
                description='How far back to look for wireless clients',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # HTTP layer
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """Make an authenticated request to the Mist API."""
        # Proactive rate limit wait
        if (
            self._rate_limit_remaining is not None
            and self._rate_limit_remaining <= RATE_LIMIT_THRESHOLD
        ):
            self.logger.warning(
                'Rate limit low (%d remaining), waiting %ds',
                self._rate_limit_remaining,
                DEFAULT_RATE_LIMIT_WAIT,
            )
            time.sleep(DEFAULT_RATE_LIMIT_WAIT)

        start = time.time()
        response = self.auth.request(path, method=method, params=params, **kwargs)
        duration_ms = (time.time() - start) * 1000

        # Track rate limits
        remaining = response.headers.get('X-RateLimit-Remaining')
        if remaining is not None:
            self._rate_limit_remaining = int(remaining)

        self.log_api_call(
            path, method, response.status_code,
            duration_ms=duration_ms, success=response.ok,
        )

        return response.json() if response.content else {}

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    def _paginate(self, path: str, params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Generic page+limit pagination. Yields individual records."""
        params = dict(params or {})
        params['limit'] = self.page_size

        for page in range(MAX_PAGINATION_PAGES):
            params['page'] = page + 1
            data = self._request('GET', path, params=params)

            # Mist returns a plain list for most endpoints
            records = data if isinstance(data, list) else data.get('results', data.get('data', []))
            if not records:
                break

            yield from records

            if len(records) < self.page_size:
                break

    # ------------------------------------------------------------------
    # Organization discovery
    # ------------------------------------------------------------------

    def _discover_orgs(self) -> List[Dict[str, Any]]:
        """Discover all organizations the API token has access to."""
        try:
            data = self._request('GET', '/self/orgs')
            if isinstance(data, list) and data:
                return data
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                self.logger.debug('/self/orgs returned 404 -- trying /self (org-level token)')
            else:
                raise

        # Fallback for org-level API tokens: extract from privileges
        data = self._request('GET', '/self')
        if isinstance(data, dict):
            privileges = data.get('privileges') or []
            org_ids_seen = set()
            orgs = []
            for priv in privileges:
                if priv.get('scope') == 'org' and priv.get('org_id'):
                    oid = priv['org_id']
                    if oid not in org_ids_seen:
                        org_ids_seen.add(oid)
                        orgs.append({'id': oid, 'name': priv.get('org_name', oid)})
            return orgs
        return []

    def _get_org_ids(self) -> List[str]:
        """Return list of org IDs to collect from."""
        if self.org_id:
            return [self.org_id]

        orgs = self._discover_orgs()
        if not orgs:
            self.logger.warning('No organizations found for this API token')
            return []

        org_ids = [o['id'] for o in orgs if o.get('id')]
        self.logger.info(
            'Discovered %d org(s): %s',
            len(org_ids),
            ', '.join(o.get('name', o['id']) for o in orgs if o.get('id')),
        )
        return org_ids

    # ------------------------------------------------------------------
    # Device collection
    # ------------------------------------------------------------------

    def _fetch_inventory(self, org_id: str) -> Iterator[Dict[str, Any]]:
        yield from self._paginate(f'/orgs/{org_id}/inventory')

    def _fetch_device_stats(self, org_id: str) -> Iterator[Dict[str, Any]]:
        yield from self._paginate(f'/orgs/{org_id}/stats/devices')

    @staticmethod
    def _make_device_record(
        device_id: str,
        inv: Dict[str, Any],
        stat: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        s = stat or {}
        return {
            'id': device_id,
            'name': s.get('name', ''),
            'type': inv.get('type', ''),
            'model': inv.get('model', ''),
            'serial': inv.get('serial', ''),
            'mac': s.get('mac', inv.get('mac', '')),
            'ip': s.get('ip', ''),
            'version': s.get('version', ''),
            'status': s.get('status', ''),
            'last_seen': s.get('last_seen', ''),
            'uptime': s.get('uptime', ''),
            'site_id': inv.get('site_id', ''),
            'site_name': s.get('site_name', ''),
            'num_clients': s.get('num_clients', ''),
            '_record_type': 'device',
        }

    def _fetch_devices(self, org_id: str) -> Iterator[Dict[str, Any]]:
        """Merge inventory + stats, yield device records."""
        # Build inventory lookup
        inventory_by_id: Dict[str, Dict[str, Any]] = {}
        for item in self._fetch_inventory(org_id):
            device_id = item.get('id') or item.get('mac', '')
            inventory_by_id[device_id] = item

        # Stream stats, merge with inventory
        for stat in self._fetch_device_stats(org_id):
            device_id = stat.get('id') or stat.get('mac', '')
            inv = inventory_by_id.pop(device_id, {})
            yield self._make_device_record(device_id, inv, stat)

        # Inventory-only devices (unmatched remainder)
        for device_id, inv in inventory_by_id.items():
            yield self._make_device_record(device_id, inv)

    # ------------------------------------------------------------------
    # Client collection
    # ------------------------------------------------------------------

    def _fetch_sites(self, org_id: str) -> Iterator[Dict[str, Any]]:
        yield from self._paginate(f'/orgs/{org_id}/sites')

    def _fetch_clients(self, org_id: str) -> Iterator[Dict[str, Any]]:
        sites = list(self._fetch_sites(org_id))
        if not sites:
            self.logger.warning(
                'No sites found in org %s -- skipping client collection', org_id,
            )
            return

        for site in sites:
            site_id = site.get('id', '')
            site_name = site.get('name', '')

            self.logger.info('Fetching clients for site: %s (%s)', site_name, site_id)

            for client in self._paginate(
                f'/sites/{site_id}/stats/clients',
                params={'duration': self.client_duration},
            ):
                hostname = client.get('hostname', '')
                yield {
                    'name': hostname,
                    'mac': client.get('mac', ''),
                    'hostname': hostname,
                    'ip': client.get('ip', ''),
                    'ssid': client.get('ssid', ''),
                    'band': client.get('band', ''),
                    'ap_mac': client.get('ap_mac', ''),
                    'ap_name': client.get('ap_name', ''),
                    'site_name': site_name,
                    'last_seen': client.get('last_seen', ''),
                    'manufacture': client.get('manufacture', ''),
                    'os': client.get('os', ''),
                    '_record_type': 'client',
                }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            if self.org_id:
                data = self._request('GET', f'/orgs/{self.org_id}')
                org_name = data.get('name', 'Unknown')
                return {
                    'success': True,
                    'message': f'Connected to Juniper Mist org: {org_name}',
                    'metadata': {
                        'org_id': self.org_id,
                        'org_name': org_name,
                        'region': self.region,
                    },
                }
            else:
                orgs = self._discover_orgs()
                if not orgs:
                    return {
                        'success': False,
                        'message': 'Token valid but no organizations found',
                    }
                org_names = [o.get('name', o.get('id', '?')) for o in orgs]
                return {
                    'success': True,
                    'message': f'Connected -- discovered {len(orgs)} org(s): {", ".join(org_names)}',
                    'metadata': {
                        'org_count': len(orgs),
                        'org_names': org_names,
                        'region': self.region,
                    },
                }
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 401:
                msg = 'Invalid API token'
            elif status == 403:
                msg = 'Insufficient permissions -- check org-level API token scope'
            elif status == 404:
                msg = 'Organization not found -- verify org_id'
            else:
                msg = f'HTTP error {status}'
            return {'success': False, 'message': msg}
        except Exception as exc:
            self.logger.error('Mist connection test failed: %s', exc)
            return {'success': False, 'message': f'Connection failed: {exc}'}

    # ------------------------------------------------------------------
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        org_ids = self._get_org_ids()

        for org_id in org_ids:
            if self.collect_devices:
                self.logger.info('Collecting devices for org %s...', org_id)
                yield from self._fetch_devices(org_id)

            if self.collect_clients:
                self.logger.info('Collecting clients for org %s...', org_id)
                yield from self._fetch_clients(org_id)

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': [
                # Device fields
                {'name': 'id', 'path': 'id', 'type': 'string', 'description': 'Device or client unique ID'},
                {'name': 'name', 'path': 'name', 'type': 'string', 'description': 'Device name'},
                {'name': 'type', 'path': 'type', 'type': 'string', 'description': 'Device type (ap, switch, gateway)'},
                {'name': 'model', 'path': 'model', 'type': 'string', 'description': 'Hardware model'},
                {'name': 'serial', 'path': 'serial', 'type': 'string', 'description': 'Serial number'},
                {'name': 'mac', 'path': 'mac', 'type': 'string', 'description': 'MAC address'},
                {'name': 'ip', 'path': 'ip', 'type': 'string', 'description': 'IP address'},
                {'name': 'version', 'path': 'version', 'type': 'string', 'description': 'Firmware version'},
                {'name': 'status', 'path': 'status', 'type': 'string', 'description': 'Device status'},
                {'name': 'last_seen', 'path': 'last_seen', 'type': 'datetime', 'description': 'Last activity timestamp'},
                {'name': 'uptime', 'path': 'uptime', 'type': 'number', 'description': 'Uptime in seconds'},
                {'name': 'site_id', 'path': 'site_id', 'type': 'string', 'description': 'Site UUID'},
                {'name': 'site_name', 'path': 'site_name', 'type': 'string', 'description': 'Site name'},
                {'name': 'num_clients', 'path': 'num_clients', 'type': 'number', 'description': 'Connected client count'},
                # Client fields
                {'name': 'hostname', 'path': 'hostname', 'type': 'string', 'description': 'Client hostname'},
                {'name': 'ssid', 'path': 'ssid', 'type': 'string', 'description': 'Connected SSID'},
                {'name': 'band', 'path': 'band', 'type': 'string', 'description': 'Wifi band'},
                {'name': 'ap_mac', 'path': 'ap_mac', 'type': 'string', 'description': 'Connected AP MAC'},
                {'name': 'ap_name', 'path': 'ap_name', 'type': 'string', 'description': 'Connected AP name'},
                {'name': 'manufacture', 'path': 'manufacture', 'type': 'string', 'description': 'Vendor (Mist OUI lookup)'},
                {'name': 'os', 'path': 'os', 'type': 'string', 'description': 'Detected OS'},
                # Metadata
                {'name': '_record_type', 'path': '_record_type', 'type': 'string', 'description': 'Record type (device or client)'},
            ],
        }
