# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Azure Log Analytics Workspaces Connector

Queries asset data from Azure Log Analytics via KQL (Kusto Query Language).
Uses OAuth2 Client Credentials authentication with Microsoft Entra ID.

Auth: OAuth2ClientCredentials (Azure AD token endpoint).
Endpoint: POST https://api.loganalytics.azure.com/v1/workspaces/{id}/query
"""

from typing import Dict, List, Iterator, Any, Optional

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import OAuth2ClientCredentials, ConfigSchema


@ConnectorRegistry.register
class LogAnalyticsConnector(BaseConnector):
    """
    Azure Log Analytics Workspaces Connector

    Fetches asset data from Log Analytics via KQL queries.
    Requires App Registration with Log Analytics API permissions.
    """

    # Connector Metadata
    integration_type = "log_analytics"
    display_name = "Azure Log Analytics"
    category = "SIEM / Logging"
    description = "Query asset data from Log Analytics Workspaces via KQL"
    logo_url = "/logos/microsoft sentinel.png"
    connector_role = "transport"

    # API endpoints
    API_BASE = "https://api.loganalytics.azure.com"
    API_VERSION = "v1"

    def __init__(self, config: Dict[str, Any]):
        self.tenant_id = config.get('tenant_id')
        self.workspace_id = config.get('workspace_id')
        self.kql_query = config.get('kql_query')
        self.timespan = config.get('timespan', 'P1D')

        if not self.tenant_id:
            raise ValueError("Entra ID tenant ID is required")
        if not config.get('client_id'):
            raise ValueError("Application client ID is required")
        if not config.get('client_secret'):
            raise ValueError("Application client secret is required")
        if not self.workspace_id:
            raise ValueError("Log Analytics workspace ID is required")
        if not self.kql_query:
            raise ValueError("KQL query is required")

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return OAuth2ClientCredentials(
            config={
                'tenant_id': self.tenant_id,
                'client_id': self.config.get('client_id'),
                'client_secret': self.config.get('client_secret'),
                'scope': 'https://api.loganalytics.io/.default',
                'token_url': f'https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token',
                'base_url': self.API_BASE,
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
            .add_string(
                'workspace_id', 'Workspace ID',
                required=True,
                placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                description='Log Analytics workspace ID',
            )
            .add_string(
                'kql_query', 'KQL Query',
                required=True,
                placeholder='Heartbeat | summarize by Computer, OSType, ComputerIP',
                description='Kusto Query Language query to retrieve asset data',
            )
            .add_string(
                'timespan', 'Timespan',
                required=False,
                placeholder='P1D',
                description='Query time range in ISO 8601 duration format (e.g., P1D, PT24H, P7D)',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to Azure Log Analytics...")

            self.auth.authenticate()

            # Lightweight connectivity check
            test_query = "Heartbeat | take 1"
            response = self._execute_query(test_query, timespan="PT1H")

            tables = response.get('tables', [])
            if not tables:
                return {
                    'success': True,
                    'message': 'Successfully connected to Log Analytics workspace (no data returned by test query).',
                    'metadata': {
                        'workspace_id': self.workspace_id,
                        'tenant_id': self.tenant_id,
                    },
                }

            # Get record count from configured KQL query
            if self.kql_query:
                try:
                    count_query = f"({self.kql_query}) | count"
                    count_response = self._execute_query(count_query, timespan="P7D")
                    count_tables = count_response.get('tables', [])
                    if count_tables and count_tables[0].get('rows'):
                        total_records = count_tables[0]['rows'][0][0]
                        return {
                            'success': True,
                            'message': f'Successfully connected. Found {total_records:,} records matching your query (last 7 days).',
                            'metadata': {
                                'workspace_id': self.workspace_id,
                                'tenant_id': self.tenant_id,
                                'api_base': self.API_BASE,
                            },
                        }
                except Exception as e:
                    self.logger.warning(f"Count query failed, falling back to simple check: {e}")

            return {
                'success': True,
                'message': 'Successfully connected to Log Analytics workspace.',
                'metadata': {
                    'workspace_id': self.workspace_id,
                    'tenant_id': self.tenant_id,
                    'api_base': self.API_BASE,
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

        response = self._execute_query(self.kql_query, self.timespan)

        tables = response.get('tables', [])
        if not tables:
            self.logger.warning("Query returned no tables")
            return

        for table in tables:
            columns = table.get('columns', [])
            rows = table.get('rows', [])

            column_names = [col['name'] for col in columns]

            for row in rows:
                record = {}
                for i, value in enumerate(row):
                    if i < len(column_names):
                        record[column_names[i]] = value

                record['_workspace_id'] = self.workspace_id
                yield record

    # ------------------------------------------------------------------
    # Internal: KQL query execution
    # ------------------------------------------------------------------

    def _execute_query(self, query: str, timespan: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {'query': query}

        if timespan:
            body['timespan'] = timespan

        response = self.auth.request(
            f'/{self.API_VERSION}/workspaces/{self.workspace_id}/query',
            method='POST',
            json=body,
        )

        data = response.json()

        if 'error' in data:
            error_info = data['error']
            error_message = error_info.get('message', 'Unknown error')
            raise RuntimeError(f"Query failed: {error_message}")

        return data

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        common_fields = [
            {'name': 'Computer', 'path': 'Computer', 'type': 'string', 'description': 'Computer name', 'required': False},
            {'name': 'ComputerIP', 'path': 'ComputerIP', 'type': 'string', 'description': 'Computer IP address', 'required': False},
            {'name': 'OSType', 'path': 'OSType', 'type': 'string', 'description': 'Operating system type (Linux/Windows)', 'required': False},
            {'name': 'OSName', 'path': 'OSName', 'type': 'string', 'description': 'Operating system name', 'required': False},
            {'name': 'OSMajorVersion', 'path': 'OSMajorVersion', 'type': 'string', 'description': 'OS major version', 'required': False},
            {'name': 'OSMinorVersion', 'path': 'OSMinorVersion', 'type': 'string', 'description': 'OS minor version', 'required': False},
            {'name': 'ComputerEnvironment', 'path': 'ComputerEnvironment', 'type': 'string', 'description': 'Computer environment (Azure/Non-Azure)', 'required': False},
            {'name': 'ResourceId', 'path': 'ResourceId', 'type': 'string', 'description': 'Azure resource ID', 'required': False},
            {'name': 'SourceSystem', 'path': 'SourceSystem', 'type': 'string', 'description': 'Source system', 'required': False},
            {'name': 'TimeGenerated', 'path': 'TimeGenerated', 'type': 'datetime', 'description': 'Event timestamp', 'required': False},
        ]

        return {
            'source': 'log_analytics',
            'version': '1.0',
            'fields': common_fields,
            'sample_record_count': 3,
            'notes': 'Fields returned depend on your KQL query. Common tables: Heartbeat, Syslog, SecurityEvent, W3CIISLog',
        }
