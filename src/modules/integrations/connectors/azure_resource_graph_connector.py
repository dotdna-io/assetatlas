# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Azure Resource Graph Connector

Collects cloud resource inventory from Azure Resource Graph via KQL queries.
- Resources: VMs, hybrid machines, network interfaces (configurable via KQL)
- Auth: OAuth2 Client Credentials (scope: management.azure.com/.default)
- Pagination: $skipToken in POST body
- Max 1000 results per request
"""

from typing import Any, Dict, Iterator, List, Optional

import requests

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import OAuth2ClientCredentials, ConfigSchema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_VERSION = '2022-10-01'
API_ENDPOINT = f'https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version={API_VERSION}'

MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 1000
MAX_PAGINATION_PAGES = 500  # Safety cap

QUERY_PRESETS = {
    # ---- VMs with IP addresses resolved via NIC + public IP joins ----
    'virtual_machines': """\
Resources
| where type in~ (
    'microsoft.compute/virtualmachines',
    'microsoft.hybridcompute/machines'
)
| extend
    computerName = coalesce(
        tostring(properties.osProfile.computerName),
        name
    ),
    osType = tostring(properties.storageProfile.osDisk.osType),
    osName = tostring(
        properties.extended.instanceView.osName
    ),
    osVersion = tostring(
        properties.extended.instanceView.osVersion
    ),
    vmSize = tostring(properties.hardwareProfile.vmSize),
    powerState = tostring(
        properties.extended.instanceView.powerState.displayStatus
    ),
    provisioningState = tostring(properties.provisioningState),
    vmId = tostring(properties.vmId),
    licenseType = tostring(properties.licenseType),
    publisher = tostring(
        properties.storageProfile.imageReference.publisher
    ),
    offer = tostring(
        properties.storageProfile.imageReference.offer
    ),
    sku = tostring(
        properties.storageProfile.imageReference.sku
    ),
    nics = array_length(
        properties.networkProfile.networkInterfaces
    )
| mv-expand nic = properties.networkProfile.networkInterfaces
| where nics == 1
    or nic.properties.primary =~ "true"
    or isempty(nic)
| extend nicId = tostring(nic.id)
| join kind=leftouter (
    Resources
    | where type =~ "microsoft.network/networkinterfaces"
    | extend ipConfigsCount = array_length(
        properties.ipConfigurations
    )
    | mv-expand ipconfig = properties.ipConfigurations
    | where ipConfigsCount == 1
        or ipconfig.properties.primary =~ "true"
    | project
        nicId = id,
        publicIpId = tostring(
            ipconfig.properties.publicIPAddress.id
        ),
        privateIPAddress = tostring(
            ipconfig.properties.privateIPAddress
        )
) on nicId
| join kind=leftouter (
    Resources
    | where type =~ "microsoft.network/publicipaddresses"
    | project
        publicIpId = id,
        publicIpAddress = tostring(properties.ipAddress)
) on publicIpId
| project-away nicId1, publicIpId1, publicIpId, nicId, nic,
    nics
| project id, name, type, location, resourceGroup,
    subscriptionId, computerName, osType, osName,
    osVersion, vmSize, powerState, provisioningState,
    vmId, licenseType, publisher, offer, sku,
    privateIPAddress, publicIpAddress, tags""",

    # ---- VMs without IP joins (simpler, faster) ----
    'virtual_machines_basic': """\
Resources
| where type in~ (
    'microsoft.compute/virtualmachines',
    'microsoft.hybridcompute/machines'
)
| extend
    computerName = coalesce(
        tostring(properties.osProfile.computerName),
        name
    ),
    osType = tostring(properties.storageProfile.osDisk.osType),
    osName = tostring(
        properties.extended.instanceView.osName
    ),
    osVersion = tostring(
        properties.extended.instanceView.osVersion
    ),
    vmSize = tostring(properties.hardwareProfile.vmSize),
    powerState = tostring(
        properties.extended.instanceView.powerState.displayStatus
    ),
    provisioningState = tostring(properties.provisioningState),
    vmId = tostring(properties.vmId),
    nicCount = array_length(
        properties.networkProfile.networkInterfaces
    )
| project id, name, type, location, resourceGroup,
    subscriptionId, computerName, osType, osName,
    osVersion, vmSize, powerState, provisioningState,
    vmId, nicCount, tags""",

    # ---- Infrastructure resources (networking, VDI, storage, SQL) ----
    'infrastructure': """\
Resources
| where type in~ (
    'microsoft.compute/availabilitysets',
    'microsoft.desktopvirtualization/applicationgroups',
    'microsoft.desktopvirtualization/hostpools',
    'microsoft.desktopvirtualization/workspaces',
    'microsoft.network/networkinterfaces',
    'microsoft.network/networksecuritygroups',
    'microsoft.network/publicipaddresses',
    'microsoft.network/routetables',
    'microsoft.network/virtualnetworks',
    'microsoft.sqlvirtualmachine/sqlvirtualmachines',
    'microsoft.storage/storageaccounts'
)
| project id, name, type, location, resourceGroup,
    subscriptionId, properties, tags""",

    # ---- All resource types (VMs + hybrid + NICs, raw properties) ----
    'all_resources': """\
Resources
| where type in~ (
    'microsoft.compute/virtualmachines',
    'microsoft.hybridcompute/machines',
    'microsoft.network/networkinterfaces'
)
| project id, name, type, location, resourceGroup,
    subscriptionId, properties, tags, identity""",
}

DEFAULT_QUERY_PRESET = 'virtual_machines'


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

@ConnectorRegistry.register
class AzureResourceGraphConnector(BaseConnector):
    """
    Azure Resource Graph Connector

    Queries Azure Resource Graph for cloud resource inventory across
    subscriptions.  Uses OAuth2 client credentials flow with the
    Azure Resource Manager scope.
    """

    # Connector Metadata
    integration_type = 'azure_resource_graph'
    display_name = 'Azure Resource Graph'
    category = 'Cloud Platform'
    description = 'Collect cloud resource inventory from Azure Resource Graph via KQL queries'
    logo_url = '/logos/azure resource graph.png'

    def __init__(self, config: Dict[str, Any]):
        # Parse config before super().__init__ calls _build_auth()
        self.tenant_id = config.get('tenant_id', '')
        self.client_id = config.get('client_id', '')
        self.client_secret = config.get('client_secret', '')

        if not self.tenant_id:
            raise ValueError("Tenant ID is required for Azure Resource Graph")
        if not self.client_id:
            raise ValueError("Client ID is required for Azure Resource Graph")
        if not self.client_secret:
            raise ValueError("Client Secret is required for Azure Resource Graph")

        # Optional subscription filter
        raw_subs = config.get('subscription_ids', '')
        if raw_subs and isinstance(raw_subs, str):
            self.subscription_ids = [s.strip() for s in raw_subs.split(',') if s.strip()]
        elif isinstance(raw_subs, list):
            self.subscription_ids = raw_subs
        else:
            self.subscription_ids = []

        # Query preset or custom KQL
        custom_query = (config.get('resource_query') or '').strip()
        if custom_query:
            self.query_preset = 'custom'
            self.resource_query = custom_query
        else:
            self.query_preset = config.get(
                'query_preset', DEFAULT_QUERY_PRESET,
            )
            self.resource_query = QUERY_PRESETS.get(
                self.query_preset,
                QUERY_PRESETS[DEFAULT_QUERY_PRESET],
            )

        # Page size — clamp to MAX_PAGE_SIZE
        raw_page_size = config.get('page_size')
        if raw_page_size is None:
            self.page_size = DEFAULT_PAGE_SIZE
        else:
            self.page_size = max(1, min(int(raw_page_size), MAX_PAGE_SIZE))

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self) -> OAuth2ClientCredentials:
        return OAuth2ClientCredentials(
            config={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'https://management.azure.com/.default',
                'token_url': f'https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token',
                'base_url': 'https://management.azure.com',
            },
            logger=self.logger,
        )

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
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
            .add_string(
                'subscription_ids', 'Subscription IDs',
                required=False,
                placeholder='sub-id-1, sub-id-2',
                description='Comma-separated subscription IDs (blank = all accessible)',
            )
            .add_select(
                'query_preset', 'Query Preset',
                options=[
                    {
                        'value': 'virtual_machines',
                        'label': 'Virtual Machines with IPs (VMs + NIC/IP join)',
                    },
                    {
                        'value': 'virtual_machines_basic',
                        'label': 'Virtual Machines basic (VMs only, no IP resolution)',
                    },
                    {
                        'value': 'infrastructure',
                        'label': 'Infrastructure (networking, VDI, storage, SQL)',
                    },
                    {
                        'value': 'all_resources',
                        'label': 'All Resources (VMs + hybrid + NICs, raw properties)',
                    },
                ],
                default=DEFAULT_QUERY_PRESET,
                description='Pre-built query for common resource types',
            )
            .add_string(
                'resource_query', 'Custom Query (KQL)',
                required=False,
                placeholder='Resources | where type =~ ...',
                description='Override the preset with a custom KQL query (leave blank to use preset)',
            )
            .add_number(
                'page_size', 'Page Size',
                default=DEFAULT_PAGE_SIZE,
                min_val=1,
                max_val=MAX_PAGE_SIZE,
                description='Results per API request (max 1000)',
            )
            .build()
        )

        schema['tenant_id']['validation'] = uuid_validation
        schema['client_id']['validation'] = uuid_validation

        return schema

    # ------------------------------------------------------------------
    # _collect_records (template method implementation)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """Collect resources from Azure Resource Graph with $skipToken pagination."""
        self.auth.authenticate()

        self.logger.info('Querying Azure Resource Graph...')

        body: Dict[str, Any] = {
            'query': self.resource_query,
            'options': {
                '$top': self.page_size,
                'resultFormat': 'objectArray',
            },
        }

        if self.subscription_ids:
            body['subscriptions'] = self.subscription_ids

        skip_token: Optional[str] = None
        record_count = 0

        for _ in range(MAX_PAGINATION_PAGES):
            if skip_token:
                body['options']['$skipToken'] = skip_token

            response = self.auth.request(
                API_ENDPOINT,
                method='POST',
                json=body,
            )
            result = response.json()

            data = result.get('data', [])
            if not data:
                break

            for resource in data:
                record_count += 1
                if record_count % self.page_size == 0:
                    self.logger.info('Fetched %d resources so far...', record_count)
                yield resource

            skip_token = result.get('$skipToken')
            if not skip_token:
                break

        self.logger.info('Fetch complete: %d resources', record_count)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        """Validate credentials by running the configured query with $top=1."""
        try:
            self.auth.authenticate()

            # Run the actual configured query with $top=1 to validate
            # both auth and query syntax, and read totalRecords
            body: Dict[str, Any] = {
                'query': self.resource_query,
                'options': {
                    '$top': 1,
                    'resultFormat': 'objectArray',
                },
            }
            if self.subscription_ids:
                body['subscriptions'] = self.subscription_ids

            response = self.auth.request(
                API_ENDPOINT, method='POST', json=body,
            )
            result = response.json()

            resource_count = result.get('totalRecords', 0)

            return {
                'success': True,
                'message': (
                    'Connected to Azure Resource Graph.'
                    f' Found {resource_count:,} resources.'
                ),
                'metadata': {
                    'resource_count': resource_count,
                    'tenant_id': self.tenant_id,
                    'subscriptions': self.subscription_ids or 'all',
                    'query_preset': self.query_preset,
                },
            }
        except requests.HTTPError as exc:
            self.logger.error('Connection test failed: %s', exc)
            status = (
                exc.response.status_code
                if exc.response is not None else 0
            )
            if status == 401:
                msg = ('Authentication failed'
                       ' — check tenant_id, client_id,'
                       ' and client_secret')
            elif status == 403:
                msg = ('Insufficient permissions'
                       ' — ensure the app has Reader role'
                       ' on subscriptions')
            else:
                msg = f'HTTP error {status}: {exc}'
            return {'success': False, 'message': msg}
        except Exception as exc:
            self.logger.error('Connection test failed: %s', exc)
            return {
                'success': False,
                'message': f'Connection failed: {exc}',
            }

    def get_schema(self) -> Dict[str, Any]:
        """Field schema for mapping UI."""
        # Core fields present in all presets
        fields = [
            {'name': 'id', 'path': 'id', 'type': 'string',
             'description': 'Azure resource ID'},
            {'name': 'name', 'path': 'name', 'type': 'string',
             'description': 'Resource name'},
            {'name': 'type', 'path': 'type', 'type': 'string',
             'description': 'Resource type'},
            {'name': 'location', 'path': 'location', 'type': 'string',
             'description': 'Azure region'},
            {'name': 'resourceGroup', 'path': 'resourceGroup',
             'type': 'string', 'description': 'Resource group name'},
            {'name': 'subscriptionId', 'path': 'subscriptionId',
             'type': 'string', 'description': 'Subscription ID'},
            {'name': 'tags', 'path': 'tags', 'type': 'object',
             'description': 'Resource tags'},
        ]

        # VM fields shared by both VM presets
        vm_base_fields = [
            {'name': 'computerName', 'path': 'computerName',
             'type': 'string', 'description': 'Computer hostname'},
            {'name': 'osType', 'path': 'osType', 'type': 'string',
             'description': 'OS type (Windows/Linux)'},
            {'name': 'osName', 'path': 'osName', 'type': 'string',
             'description': 'OS name (e.g., Ubuntu 22.04)'},
            {'name': 'osVersion', 'path': 'osVersion',
             'type': 'string', 'description': 'OS version'},
            {'name': 'vmSize', 'path': 'vmSize', 'type': 'string',
             'description': 'VM size (e.g., Standard_D2s_v3)'},
            {'name': 'powerState', 'path': 'powerState',
             'type': 'string',
             'description': 'Power state (e.g., VM running)'},
            {'name': 'provisioningState', 'path': 'provisioningState',
             'type': 'string', 'description': 'Provisioning state'},
            {'name': 'vmId', 'path': 'vmId', 'type': 'string',
             'description': 'Unique VM identifier'},
        ]

        # IP fields (only in VM with IPs preset)
        vm_ip_fields = [
            {'name': 'privateIPAddress', 'path': 'privateIPAddress',
             'type': 'string',
             'description': 'Primary private IP address'},
            {'name': 'publicIpAddress', 'path': 'publicIpAddress',
             'type': 'string',
             'description': 'Public IP address (if assigned)'},
            {'name': 'licenseType', 'path': 'licenseType',
             'type': 'string',
             'description': 'License type (e.g., Windows_Server)'},
            {'name': 'publisher', 'path': 'publisher',
             'type': 'string',
             'description': 'Image publisher (e.g., Canonical)'},
            {'name': 'offer', 'path': 'offer', 'type': 'string',
             'description': 'Image offer (e.g., UbuntuServer)'},
            {'name': 'sku', 'path': 'sku', 'type': 'string',
             'description': 'Image SKU (e.g., 22_04-lts)'},
        ]

        # Basic VM extra fields
        vm_basic_extra = [
            {'name': 'nicCount', 'path': 'nicCount', 'type': 'number',
             'description': 'Number of network interfaces'},
        ]

        # Raw property fields (all_resources / infrastructure)
        raw_fields = [
            {'name': 'properties', 'path': 'properties',
             'type': 'object',
             'description': 'Resource properties (varies by type)'},
        ]
        identity_field = [
            {'name': 'identity', 'path': 'identity',
             'type': 'object',
             'description': 'Managed identity configuration'},
        ]

        if self.query_preset == 'virtual_machines':
            fields.extend(vm_base_fields)
            fields.extend(vm_ip_fields)
        elif self.query_preset == 'virtual_machines_basic':
            fields.extend(vm_base_fields)
            fields.extend(vm_basic_extra)
        elif self.query_preset == 'infrastructure':
            fields.extend(raw_fields)
        elif self.query_preset == 'all_resources':
            fields.extend(raw_fields)
            fields.extend(identity_field)
        else:
            # Custom query — include all possible fields
            fields.extend(vm_base_fields)
            fields.extend(vm_ip_fields)
            fields.extend(raw_fields)

        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': fields,
        }
