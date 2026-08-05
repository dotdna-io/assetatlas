# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Microsoft Defender for Endpoint Connector

Collects machine/device information from Microsoft Defender for Endpoint API.
Uses OAuth2 client credentials flow with Machine.Read.All permission.
"""

from typing import Dict, List, Iterator, Any

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import OAuth2ClientCredentials, HeaderRateLimiter, LinkPaginator, ConfigSchema


@ConnectorRegistry.register
class DefenderConnector(BaseConnector):
    """
    Microsoft Defender for Endpoint Connector

    Fetches machine information from Microsoft Defender for Endpoint API.
    Requires app registration with Machine.Read.All permission.
    """

    # Connector Metadata
    integration_type = "defender"
    display_name = "Microsoft Defender for Endpoint"
    category = "EDR"
    description = "Collect machine information from Microsoft Defender for Endpoint"
    logo_url = "/logos/MS-defender-endpoint.png"

    # Fields from the Defender for Endpoint Machine entity
    MACHINE_FIELDS = [
        "id", "computerDnsName", "firstSeen", "lastSeen",
        "osPlatform", "version", "osBuild", "osArchitecture",
        "lastIpAddress", "lastExternalIpAddress",
        "healthStatus", "onboardingStatus",
        "riskScore", "exposureLevel", "deviceValue",
        "aadDeviceId", "isAadJoined",
        "rbacGroupId", "rbacGroupName",
        "machineTags", "ipAddresses",
        # Computed from ipAddresses (snake_case to distinguish from raw API fields)
        "ip_list", "mac_list",
    ]

    def __init__(self, config: Dict[str, Any]):
        self.tenant_id = config.get('tenant_id', '')
        self.page_size = int(config.get('page_size', 10000))
        self.max_pagination_pages = int(config.get('max_pagination_pages', 500))
        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self) -> OAuth2ClientCredentials:
        return OAuth2ClientCredentials(
            config={
                'client_id': self.config.get('client_id'),
                'client_secret': self.config.get('client_secret'),
                'scope': 'https://api.securitycenter.microsoft.com/.default',
                'token_url': f'https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token',
                'base_url': 'https://api.securitycenter.microsoft.com/api',
            },
            logger=self.logger,
        )

    def _build_rate_limiter(self) -> HeaderRateLimiter:
        # Rate limiter: 100 calls/min per Microsoft docs.
        # Use conservative 90/min to stay safely under the per-minute limit.
        return HeaderRateLimiter(
            initial_rpm=90,
            min_rpm=10,
            max_rpm=100,
            logger=self.logger,
        )

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        uuid_pattern = '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        uuid_validation = {'pattern': uuid_pattern, 'message': 'Must be a valid UUID'}

        schema = (
            ConfigSchema()
            .add_string(
                'tenant_id', 'Tenant ID',
                required=True,
                placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                description='Microsoft Entra ID Tenant ID (Directory ID)',
            )
            .add_string(
                'client_id', 'Application (Client) ID',
                required=True,
                placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                description='App Registration Client ID',
            )
            .add_password(
                'client_secret', 'Client Secret',
                required=True,
                description='App Registration Client Secret (will be encrypted)',
            )
            .add_number(
                'page_size', 'Page Size',
                default=10000,
                min_val=1,
                max_val=10000,
                description='Records to fetch per API request (max 10,000)',
            )
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                default=500,
                min_val=1,
                max_val=10000,
                description=(
                    'Safety cap on API pages fetched per collection run — it stops a '
                    'pagination loop that never terminates. At the default page size '
                    'of 10,000, the default cap of 500 pages collects up to 5,000,000 machines.'
                ),
            )
            .build()
        )

        schema['tenant_id']['validation'] = uuid_validation
        schema['client_id']['validation'] = uuid_validation

        return schema

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_network_fields(record: Dict[str, Any]) -> None:
        """
        Extract ip_list and mac_list from ipAddresses where operationalStatus is Up.

        Modifies the record in-place.
        """
        ip_addresses = record.get('ipAddresses') or []
        ip_list = []
        mac_list = []

        for entry in ip_addresses:
            if not isinstance(entry, dict):
                continue
            if entry.get('operationalStatus') != 'Up':
                continue

            ip = entry.get('ipAddress')
            mac = entry.get('macAddress')

            if ip and ip not in ip_list:
                ip_list.append(ip)
            if mac and mac not in mac_list:
                mac_list.append(mac)

        record['ip_list'] = ip_list if ip_list else None
        record['mac_list'] = mac_list if mac_list else None

    # ------------------------------------------------------------------
    # _collect_records (template method implementation)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """Fetch machines from Defender for Endpoint with @odata.nextLink pagination."""
        self.auth.authenticate()

        paginator = LinkPaginator(max_pages=self.max_pagination_pages)

        page_count = 0

        def fetch_page(url):
            if url is None:
                response = self.auth.request('/machines', params={'$top': self.page_size})
            else:
                response = self.auth.request(url)
            data = response.json()
            records = data.get('value', [])
            next_url = data.get('@odata.nextLink')
            return records, next_url

        for page in paginator.pages(fetch_page):
            page_count += 1
            self.logger.info('Fetched page %d (%d records)', page_count, len(page))
            for record in page:
                self._compute_network_fields(record)
                yield record
        self.warn_if_truncated(paginator, "Defender for Endpoint machines")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Defender for Endpoint."""
        try:
            self.logger.info("Testing connection to Microsoft Defender for Endpoint...")

            self.auth.authenticate()

            # Test with a minimal query (Defender API does not support $count)
            response = self.auth.request('/machines', params={'$top': 1})
            data = response.json()
            machines = data.get('value', [])

            return {
                'success': True,
                'message': (
                    f'Successfully connected to Defender for Endpoint.'
                    f' Received {len(machines)} sample machine(s).'
                ),
                'metadata': {
                    'tenant_id': self.tenant_id,
                    'api_endpoint': 'https://api.securitycenter.microsoft.com/api/machines',
                },
            }
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e),
            }

    def get_schema(self) -> Dict[str, Any]:
        """Return field schema for mapping configuration."""
        field_metadata = {
            'id': {'type': 'string', 'description': 'Machine identity'},
            'computerDnsName': {'type': 'string', 'description': 'Machine fully qualified name'},
            'firstSeen': {'type': 'datetime', 'description': 'First observed by Defender for Endpoint'},
            'lastSeen': {'type': 'datetime', 'description': 'Last device report timestamp'},
            'osPlatform': {'type': 'string', 'description': 'Operating system platform (e.g., Windows10, Linux)'},
            'version': {'type': 'string', 'description': 'Operating system version'},
            'osBuild': {'type': 'number', 'description': 'Operating system build number'},
            'osArchitecture': {'type': 'string', 'description': 'OS architecture (32-bit, 64-bit)'},
            'lastIpAddress': {'type': 'string', 'description': 'Last IP on local NIC'},
            'lastExternalIpAddress': {'type': 'string', 'description': 'Last external/public IP'},
            'healthStatus': {'type': 'string', 'description': 'Machine health status (Active, Inactive, etc.)'},
            'onboardingStatus': {'type': 'string', 'description': 'Onboarding status (onboarded, CanBeOnboarded, etc.)'},
            'riskScore': {'type': 'string', 'description': 'Risk score (None, Informational, Low, Medium, High)'},
            'exposureLevel': {'type': 'string', 'description': 'Exposure level (None, Low, Medium, High)'},
            'deviceValue': {'type': 'string', 'description': 'Device value (Normal, Low, High)'},
            'aadDeviceId': {'type': 'string', 'description': 'Microsoft Entra Device ID (GUID)'},
            'isAadJoined': {'type': 'boolean', 'description': 'Whether device is Azure AD joined'},
            'rbacGroupId': {'type': 'string', 'description': 'Machine group ID'},
            'rbacGroupName': {'type': 'string', 'description': 'Machine group name'},
            'machineTags': {'type': 'array', 'description': 'Set of machine tags'},
            'ipAddresses': {'type': 'array', 'description': 'IP address collection (ipAddress, macAddress, type)'},
            'ip_list': {'type': 'array', 'description': 'IP addresses from active interfaces (operationalStatus Up)'},
            'mac_list': {'type': 'array', 'description': 'MAC addresses from active interfaces (operationalStatus Up)'},
        }

        fields = []
        for field_name in self.MACHINE_FIELDS:
            info = field_metadata.get(field_name, {
                'type': 'string',
                'description': f'Defender {field_name} field',
            })
            fields.append({
                'name': field_name,
                'path': field_name,
                'type': info['type'],
                'description': info.get('description', ''),
                'required': False,
            })

        return {
            'source': 'defender',
            'version': '1.0',
            'fields': fields,
        }
