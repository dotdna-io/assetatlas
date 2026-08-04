# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Microsoft Intune Connector

Collects managed device information from Microsoft Intune via Microsoft Graph API.
Uses OAuth2 client credentials flow with DeviceManagementManagedDevices.Read.All permission.
"""

from typing import Dict, List, Iterator, Any

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import OAuth2ClientCredentials, LinkPaginator, ConfigSchema


@ConnectorRegistry.register
class IntuneConnector(BaseConnector):
    """
    Microsoft Intune Device Connector

    Fetches managed device information from Microsoft Intune via Microsoft Graph API.
    Requires app registration with DeviceManagementManagedDevices.Read.All permission.
    """

    # Connector Metadata
    integration_type = "intune"
    display_name = "Microsoft Intune"
    category = "Mobile Device Management"
    description = "Collect managed device information from Microsoft Intune"
    logo_url = "/logos/intune.png"

    # Field Collections
    ESSENTIAL_FIELDS = [
        "id", "deviceName", "userPrincipalName", "managedDeviceOwnerType",
        "serialNumber", "manufacturer", "model", "operatingSystem", "osVersion",
        "complianceState", "lastSyncDateTime", "enrolledDateTime"
    ]

    COMPREHENSIVE_FIELDS = ESSENTIAL_FIELDS + [
        "userDisplayName", "azureADDeviceId",
        "deviceRegistrationState", "emailAddress", "ethernetMacAddress",
        "isEncrypted", "isSupervised", "freeStorageSpaceInBytes",
        "totalStorageSpaceInBytes", "imei", "phoneNumber", "deviceCategoryDisplayName",
        "exchangeAccessState", "exchangeLastSuccessfulSyncDateTime",
        "deviceEnrollmentType", "managementAgent"
    ]

    def __init__(self, config: Dict[str, Any]):
        self.field_mode = config.get('field_mode', 'comprehensive').lower()
        self.page_size = int(config.get('page_size', 999))
        super().__init__(config)

    def _build_auth(self):
        tenant_id = self.config.get('tenant_id')
        return OAuth2ClientCredentials(
            config={
                'tenant_id': tenant_id,
                'client_id': self.config.get('client_id'),
                'client_secret': self.config.get('client_secret'),
                'scope': 'https://graph.microsoft.com/.default',
                'token_url': f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
                'base_url': 'https://graph.microsoft.com/v1.0',
            },
            logger=self.logger,
        )

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
                description='Entra ID Tenant ID (Directory ID)',
            )
            .add_string(
                'client_id', 'Application (Client) ID',
                required=True,
                placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                description='Registered application client ID',
            )
            .add_password(
                'client_secret', 'Client Secret',
                required=True,
                description='Application client secret (will be encrypted)',
            )
            .add_select(
                'field_mode', 'Field Collection Mode',
                options=[
                    {'value': 'essential', 'label': 'Essential (12 fields)'},
                    {'value': 'comprehensive', 'label': 'Comprehensive (all fields)'},
                ],
                default='comprehensive',
                description='Choose which fields to collect',
            )
            .add_number(
                'page_size', 'Page Size',
                default=999,
                min_val=1,
                max_val=999,
                description='Records to fetch per API request',
            )
            .build()
        )

        schema['tenant_id']['validation'] = uuid_validation
        schema['client_id']['validation'] = uuid_validation

        return schema

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to Microsoft Graph (Intune)...")

            self.auth.authenticate()

            response = self.auth.request(
                '/deviceManagement/managedDevices',
                params={'$top': 1, '$count': 'true'},
                headers={'ConsistencyLevel': 'eventual'},
            )
            data = response.json()

            device_count = data.get('@odata.count', len(data.get('value', [])))

            return {
                'success': True,
                'message': f'Successfully connected to Intune. Found {device_count:,} managed devices.',
                'metadata': {
                    'device_count': device_count,
                    'tenant_id': self.config.get('tenant_id'),
                    'api_endpoint': 'https://graph.microsoft.com/v1.0/deviceManagement/managedDevices',
                },
            }
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e),
            }

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        self.auth.authenticate()

        params = {'$top': self.page_size}

        if self.field_mode == 'essential':
            params['$select'] = ','.join(self.ESSENTIAL_FIELDS)
        elif self.field_mode == 'comprehensive':
            params['$select'] = ','.join(self.COMPREHENSIVE_FIELDS)

        paginator = LinkPaginator(max_pages=500)

        def fetch_page(url):
            if url is None:
                response = self.auth.request('/deviceManagement/managedDevices', params=params)
            else:
                response = self.auth.request(url)
            data = response.json()
            records = data.get('value', [])
            next_url = data.get('@odata.nextLink')
            return records, next_url

        page_count = 0
        for page in paginator.pages(fetch_page):
            page_count += 1
            self.logger.info(f"Fetched page {page_count} ({len(page)} devices)")
            yield from page

    def get_schema(self) -> Dict[str, Any]:
        field_metadata = {
            'id': {'type': 'string', 'description': 'Unique device identifier', 'required': True},
            'deviceName': {'type': 'string', 'description': 'Device name'},
            'userPrincipalName': {'type': 'string', 'description': 'User principal name (UPN)'},
            'managedDeviceOwnerType': {'type': 'string', 'description': 'Device owner type (company, personal)'},
            'serialNumber': {'type': 'string', 'description': 'Device serial number'},
            'manufacturer': {'type': 'string', 'description': 'Device manufacturer'},
            'model': {'type': 'string', 'description': 'Device model'},
            'operatingSystem': {'type': 'string', 'description': 'Operating system name'},
            'osVersion': {'type': 'string', 'description': 'OS version'},
            'complianceState': {'type': 'string', 'description': 'Compliance state'},
            'lastSyncDateTime': {'type': 'datetime', 'description': 'Last sync timestamp'},
            'enrolledDateTime': {'type': 'datetime', 'description': 'Enrollment timestamp'},
            'ethernetMacAddress': {'type': 'string', 'description': 'Ethernet MAC address'},
            'azureADDeviceId': {'type': 'string', 'description': 'Entra ID device ID'},
            'imei': {'type': 'string', 'description': 'IMEI number'},
        }

        fields = []
        for field_name in self.COMPREHENSIVE_FIELDS:
            meta = field_metadata.get(field_name, {
                'type': 'string',
                'description': f'Intune {field_name} field',
            })
            fields.append({
                'name': field_name,
                'path': field_name,
                'type': meta['type'],
                'description': meta.get('description', ''),
                'required': meta.get('required', False),
            })

        return {
            'source': 'intune',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
