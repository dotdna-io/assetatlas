# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Microsoft Entra ID Device Connector

Collects device information from Microsoft Entra ID via Microsoft Graph API.
Uses OAuth2 client credentials flow with Device.Read.All permission.
"""

from typing import Dict, List, Iterator, Any

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import OAuth2ClientCredentials, LinkPaginator, ConfigSchema


@ConnectorRegistry.register
class AzureADConnector(BaseConnector):
    """
    Microsoft Entra ID Device Connector

    Fetches device information from Entra ID via Microsoft Graph API.
    Requires app registration with Device.Read.All permission.
    """

    # Connector Metadata
    integration_type = "azure_ad"
    display_name = "Microsoft Entra ID"
    category = "Identity Management"
    description = "Collect device information from Microsoft Entra ID via Microsoft Graph API"
    logo_url = "/logos/microsoft-entra-id.png"

    # Field Collections
    ESSENTIAL_FIELDS = [
        "displayName", "accountEnabled", "operatingSystem",
        "operatingSystemVersion", "deviceId",
        "isManaged", "isCompliant", "trustType",
        "approximateLastSignInDateTime", "createdDateTime"
    ]

    COMPREHENSIVE_FIELDS = ESSENTIAL_FIELDS + [
        "deletedDateTime", "deviceCategory", "deviceMetadata",
        "deviceOwnership", "deviceVersion", "domainName",
        "enrollmentProfileName", "enrollmentType",
        "managementType", "manufacturer", "mdmAppId", "model",
        "onPremisesLastSyncDateTime", "onPremisesSyncEnabled",
        "onPremisesSecurityIdentifier", "profileType", "registeredOwners",
        "registeredUsers", "registrationDateTime",
        "isRooted"
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
                    {'value': 'essential', 'label': 'Essential (10 fields)'},
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

        # Add UUID validation to string fields that need it
        schema['tenant_id']['validation'] = uuid_validation
        schema['client_id']['validation'] = uuid_validation

        return schema

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to Microsoft Graph...")

            self.auth.authenticate()

            response = self.auth.request(
                '/devices',
                params={'$top': 1, '$count': 'true'},
                headers={'ConsistencyLevel': 'eventual'},
            )
            data = response.json()

            device_count = data.get('@odata.count', len(data.get('value', [])))

            return {
                'success': True,
                'message': f'Successfully connected to Entra ID. Found {device_count:,} devices.',
                'metadata': {
                    'device_count': device_count,
                    'tenant_id': self.config.get('tenant_id'),
                    'api_endpoint': 'https://graph.microsoft.com/v1.0/devices',
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
                response = self.auth.request('/devices', params=params)
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
            'displayName': {'type': 'string', 'description': 'Device display name'},
            'accountEnabled': {'type': 'boolean', 'description': 'Whether account is enabled'},
            'operatingSystem': {'type': 'string', 'description': 'Operating system name'},
            'operatingSystemVersion': {'type': 'string', 'description': 'OS version'},
            'deviceId': {'type': 'string', 'description': 'Device ID (GUID)'},
            'isManaged': {'type': 'boolean', 'description': 'Whether device is managed'},
            'isCompliant': {'type': 'boolean', 'description': 'Compliance status'},
            'trustType': {'type': 'string', 'description': 'Trust type (e.g., AzureAd, ServerAd)'},
            'approximateLastSignInDateTime': {'type': 'datetime', 'description': 'Last sign-in timestamp'},
            'createdDateTime': {'type': 'datetime', 'description': 'Creation timestamp'},
            'manufacturer': {'type': 'string', 'description': 'Device manufacturer'},
            'model': {'type': 'string', 'description': 'Device model'},
            'managementType': {'type': 'string', 'description': 'Management type'},
        }

        fields = []
        for field_name in self.COMPREHENSIVE_FIELDS:
            meta = field_metadata.get(field_name, {
                'type': 'string',
                'description': f'Entra ID {field_name} field',
            })
            fields.append({
                'name': field_name,
                'path': field_name,
                'type': meta['type'],
                'description': meta.get('description', ''),
                'required': False,
            })

        return {
            'source': 'azure_ad',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
