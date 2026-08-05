#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SentinelOne Singularity Platform Connector

Collects endpoint/agent data from SentinelOne via REST API.
Uses API Token authentication with Service User credentials.
"""

from typing import Dict, List, Iterator, Any
import re

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import APIKeyAuth, ConfigSchema, CursorPaginator


@ConnectorRegistry.register
class SentinelOneConnector(BaseConnector):
    """
    SentinelOne Connector

    Fetches endpoint/agent data from SentinelOne.
    Requires Service User with Account-level Viewer access.
    """

    # Connector Metadata
    integration_type = "sentinelone"
    display_name = "SentinelOne"
    category = "EDR"
    description = "Collect endpoint and agent data from SentinelOne"
    logo_url = "/logos/sentinel one.webp"

    # API version
    API_VERSION = "v2.1"

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get('url', '').rstrip('/')
        self.api_token = config.get('api_token')
        self.filters = config.get('filters', {})
        self.page_size = 1000
        self.max_pagination_pages = int(config.get('max_pagination_pages', 500))

        if not self.base_url:
            raise ValueError("SentinelOne URL is required")
        if not self.api_token:
            raise ValueError("SentinelOne API token is required")

        super().__init__(config)

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
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                default=500,
                min_val=1,
                max_val=10000,
                description=(
                    'Safety cap on API pages fetched per collection run — it stops a '
                    'pagination loop that never terminates. At the fixed page size '
                    'of 1,000, the default cap of 500 pages collects up to 500,000 agents.'
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
            self.logger.info("Testing connection to SentinelOne...")

            response = self.auth.request(
                f'/web/api/{self.API_VERSION}/accounts',
                params={'limit': 1}
            )
            accounts_data = response.json()
            accounts = accounts_data.get('data', [])

            agents_response = self.auth.request(
                f'/web/api/{self.API_VERSION}/agents',
                params={'limit': 1}
            )
            agents_data = agents_response.json()
            pagination = agents_data.get('pagination', {})
            total_agents = pagination.get('totalItems', 0)

            return {
                'success': True,
                'message': f'Successfully connected to SentinelOne. Found {total_agents:,} agents.',
                'metadata': {
                    'base_url': self.base_url,
                    'total_agents': total_agents,
                    'accounts_count': len(accounts),
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

    # Fields to drop from raw records
    DROP_FIELDS = ['proxyStates', 'missingPermissions', 'userActionsNeeded', 'locations']

    _MODEL_SEPARATOR_RE = re.compile(r'\s+-\s+')

    @staticmethod
    def _extract_manufacturer_from_model(record: Dict[str, Any]) -> None:
        """
        Extract manufacturer and model from modelName when it uses
        the 'MANUFACTURER - MODEL' format (e.g., 'LENOVO - 21K6S0AP00').

        Adds modelName_manufacturer and modelName_model fields.
        The original modelName is preserved unchanged.
        """
        model_name = record.get('modelName')
        if not model_name or not isinstance(model_name, str):
            return

        parts = SentinelOneConnector._MODEL_SEPARATOR_RE.split(model_name, maxsplit=1)
        if len(parts) < 2:
            return

        mfr = parts[0].strip()
        mdl = parts[1].strip()

        if mfr and mdl:
            record['modelName_manufacturer'] = mfr
            record['modelName_model'] = mdl

    def _normalize_record(self, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize a raw SentinelOne agent record:
        - Drop unwanted nested fields
        - Extract manufacturer/model from modelName when embedded
        - Flatten nested objects into prefixed flat fields
        - mvexpand networkInterfaces (one record per NIC)

        Returns:
            List of normalized records (multiple if networkInterfaces has >1 entry)
        """
        # Drop unwanted fields
        for field in self.DROP_FIELDS:
            record.pop(field, None)

        # Extract manufacturer/model from modelName if pattern matches
        self._extract_manufacturer_from_model(record)

        # Flatten activeDirectory
        ad = record.pop('activeDirectory', None)
        if ad and isinstance(ad, dict):
            for key, value in ad.items():
                if isinstance(value, list):
                    record[f'activeDirectory_{key}'] = ', '.join(str(v) for v in value) if value else ''
                else:
                    record[f'activeDirectory_{key}'] = value

        # Flatten activeProtection (array → comma-separated string)
        ap = record.get('activeProtection')
        if isinstance(ap, list):
            record['activeProtection'] = ', '.join(str(v) for v in ap) if ap else ''

        # Flatten cloudProviders (generic iteration over providers)
        cp = record.pop('cloudProviders', None)
        if cp and isinstance(cp, dict):
            for provider, provider_data in cp.items():
                if isinstance(provider_data, dict):
                    for key, value in provider_data.items():
                        if isinstance(value, list):
                            record[f'cloudProviders_{provider}_{key}'] = ', '.join(str(v) for v in value) if value else ''
                        else:
                            record[f'cloudProviders_{provider}_{key}'] = value

        # Flatten tags (generic iteration)
        tags = record.pop('tags', None)
        if tags and isinstance(tags, dict):
            for tag_source, tag_values in tags.items():
                if isinstance(tag_values, list):
                    record[f'tags_{tag_source}'] = ', '.join(str(v) for v in tag_values) if tag_values else ''
                else:
                    record[f'tags_{tag_source}'] = tag_values

        # mvexpand networkInterfaces — one record per NIC
        nics = record.pop('networkInterfaces', None)
        if nics and isinstance(nics, list) and len(nics) > 0:
            records = []
            for nic in nics:
                expanded = record.copy()
                expanded['networkInterface_name'] = nic.get('name')
                expanded['networkInterface_physical'] = nic.get('physical')
                inet = nic.get('inet', [])
                expanded['networkInterface_inet'] = inet[0] if inet else None
                inet6 = nic.get('inet6', [])
                expanded['networkInterface_inet6'] = inet6[0] if inet6 else None
                records.append(expanded)
            return records
        else:
            # No NICs — single record with empty NIC fields
            record['networkInterface_name'] = None
            record['networkInterface_physical'] = None
            record['networkInterface_inet'] = None
            record['networkInterface_inet6'] = None
            return [record]

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """
        Collect agents from SentinelOne with cursor-based pagination.

        Yields:
            Normalized and expanded agent records with _record_type set.
        """
        for agent in self._fetch_agents():
            for expanded in self._normalize_record(agent):
                expanded['_record_type'] = 'agent'
                yield expanded

    def _fetch_agents_page(self, cursor):
        """Fetch a single page of agents. Returns (records, next_cursor)."""
        params = {
            'limit': self.page_size,
            'sortBy': 'id',
            'sortOrder': 'asc',
        }
        if cursor:
            params['cursor'] = cursor
        params.update(self.filters)

        response = self.auth.request(
            f'/web/api/{self.API_VERSION}/agents',
            params=params,
        )
        data = response.json()
        agents = data.get('data', [])
        next_cursor = data.get('pagination', {}).get('nextCursor')
        return agents, next_cursor

    def _fetch_agents(self) -> Iterator[Dict[str, Any]]:
        """
        Fetch all agents with cursor-based pagination.

        Yields:
            Individual agent records
        """
        paginator = CursorPaginator(max_pages=self.max_pagination_pages)
        for page in paginator.pages(self._fetch_agents_page):
            for agent in page:
                yield agent
        self.warn_if_truncated(paginator, "SentinelOne agents")

    def get_schema(self) -> Dict[str, Any]:
        """Return field schema for mapping configuration."""
        fields = [
            # Core identifiers
            {'name': 'id', 'path': 'id', 'type': 'string', 'description': 'Unique agent identifier', 'required': True},
            {'name': 'computerName', 'path': 'computerName', 'type': 'string', 'description': 'Computer/hostname'},
            {'name': 'uuid', 'path': 'uuid', 'type': 'string', 'description': 'Agent UUID'},
            {'name': 'serialNumber', 'path': 'serialNumber', 'type': 'string', 'description': 'Hardware serial number'},
            {'name': 'domain', 'path': 'domain', 'type': 'string', 'description': 'Domain name'},
            # OS info
            {'name': 'osType', 'path': 'osType', 'type': 'string', 'description': 'OS type (windows, linux, macos)'},
            {'name': 'osName', 'path': 'osName', 'type': 'string', 'description': 'Operating system name'},
            {'name': 'osRevision', 'path': 'osRevision', 'type': 'string', 'description': 'OS version/revision'},
            {'name': 'osArch', 'path': 'osArch', 'type': 'string', 'description': 'OS architecture'},
            # Hardware
            {'name': 'machineType', 'path': 'machineType', 'type': 'string', 'description': 'Machine type (desktop, laptop, server)'},
            {'name': 'modelName', 'path': 'modelName', 'type': 'string', 'description': 'Hardware model name (raw, may contain "Manufacturer - Model")'},
            {'name': 'modelName_manufacturer', 'path': 'modelName_manufacturer', 'type': 'string', 'description': 'Manufacturer extracted from modelName (when "Manufacturer - Model" pattern detected)'},
            {'name': 'modelName_model', 'path': 'modelName_model', 'type': 'string', 'description': 'Model extracted from modelName (when "Manufacturer - Model" pattern detected)'},
            {'name': 'manufacturer', 'path': 'manufacturer', 'type': 'string', 'description': 'Hardware manufacturer (native API field)'},
            {'name': 'cpuId', 'path': 'cpuId', 'type': 'string', 'description': 'CPU identifier'},
            {'name': 'cpuCount', 'path': 'cpuCount', 'type': 'number', 'description': 'Number of CPUs'},
            {'name': 'coreCount', 'path': 'coreCount', 'type': 'number', 'description': 'Number of CPU cores'},
            {'name': 'totalMemory', 'path': 'totalMemory', 'type': 'number', 'description': 'Total memory in MB'},
            # Network (flattened from networkInterfaces mvexpand)
            {'name': 'networkInterface_inet', 'path': 'networkInterface_inet', 'type': 'string', 'description': 'IPv4 address (from NIC)'},
            {'name': 'networkInterface_inet6', 'path': 'networkInterface_inet6', 'type': 'string', 'description': 'IPv6 address (from NIC)'},
            {'name': 'networkInterface_physical', 'path': 'networkInterface_physical', 'type': 'string', 'description': 'MAC address (from NIC)'},
            {'name': 'networkInterface_name', 'path': 'networkInterface_name', 'type': 'string', 'description': 'NIC name'},
            {'name': 'externalIp', 'path': 'externalIp', 'type': 'string', 'description': 'External IP address'},
            {'name': 'lastIpToMgmt', 'path': 'lastIpToMgmt', 'type': 'string', 'description': 'Last IP used to connect to management'},
            # Agent status
            {'name': 'isActive', 'path': 'isActive', 'type': 'boolean', 'description': 'Whether agent is active'},
            {'name': 'isDecommissioned', 'path': 'isDecommissioned', 'type': 'boolean', 'description': 'Whether agent is decommissioned'},
            {'name': 'isUpToDate', 'path': 'isUpToDate', 'type': 'boolean', 'description': 'Whether agent is up to date'},
            {'name': 'infected', 'path': 'infected', 'type': 'boolean', 'description': 'Whether endpoint has active threats'},
            {'name': 'activeThreats', 'path': 'activeThreats', 'type': 'number', 'description': 'Number of active threats'},
            {'name': 'agentVersion', 'path': 'agentVersion', 'type': 'string', 'description': 'SentinelOne agent version'},
            {'name': 'detectionState', 'path': 'detectionState', 'type': 'string', 'description': 'Detection state (full_mode, etc.)'},
            {'name': 'mitigationMode', 'path': 'mitigationMode', 'type': 'string', 'description': 'Mitigation mode'},
            {'name': 'activeProtection', 'path': 'activeProtection', 'type': 'string', 'description': 'Active protection types (comma-separated)'},
            # Timestamps
            {'name': 'lastActiveDate', 'path': 'lastActiveDate', 'type': 'datetime', 'description': 'Last activity timestamp'},
            {'name': 'createdAt', 'path': 'createdAt', 'type': 'datetime', 'description': 'Agent creation timestamp'},
            {'name': 'registeredAt', 'path': 'registeredAt', 'type': 'datetime', 'description': 'Agent registration timestamp'},
            # Organization
            {'name': 'siteName', 'path': 'siteName', 'type': 'string', 'description': 'Site/location name'},
            {'name': 'siteId', 'path': 'siteId', 'type': 'string', 'description': 'Site ID'},
            {'name': 'accountName', 'path': 'accountName', 'type': 'string', 'description': 'Account name'},
            {'name': 'groupName', 'path': 'groupName', 'type': 'string', 'description': 'Group name'},
            # Active Directory (flattened)
            {'name': 'activeDirectory_computerDistinguishedName', 'path': 'activeDirectory_computerDistinguishedName', 'type': 'string', 'description': 'AD computer distinguished name'},
            {'name': 'activeDirectory_computerMemberOf', 'path': 'activeDirectory_computerMemberOf', 'type': 'string', 'description': 'AD computer group memberships (comma-separated)'},
            {'name': 'activeDirectory_lastUserDistinguishedName', 'path': 'activeDirectory_lastUserDistinguishedName', 'type': 'string', 'description': 'AD last user distinguished name'},
            {'name': 'activeDirectory_lastUserMemberOf', 'path': 'activeDirectory_lastUserMemberOf', 'type': 'string', 'description': 'AD last user group memberships (comma-separated)'},
            {'name': 'activeDirectory_userPrincipalName', 'path': 'activeDirectory_userPrincipalName', 'type': 'string', 'description': 'AD user principal name'},
            # Cloud providers (flattened, Azure example)
            {'name': 'cloudProviders_Azure_azureResourceGroup', 'path': 'cloudProviders_Azure_azureResourceGroup', 'type': 'string', 'description': 'Azure resource group'},
            {'name': 'cloudProviders_Azure_cloudAccount', 'path': 'cloudProviders_Azure_cloudAccount', 'type': 'string', 'description': 'Azure subscription/account ID'},
            {'name': 'cloudProviders_Azure_cloudInstanceId', 'path': 'cloudProviders_Azure_cloudInstanceId', 'type': 'string', 'description': 'Azure VM instance ID'},
            {'name': 'cloudProviders_Azure_cloudInstanceSize', 'path': 'cloudProviders_Azure_cloudInstanceSize', 'type': 'string', 'description': 'Azure VM size'},
            {'name': 'cloudProviders_Azure_cloudLocation', 'path': 'cloudProviders_Azure_cloudLocation', 'type': 'string', 'description': 'Azure region'},
            {'name': 'cloudProviders_Azure_cloudImage', 'path': 'cloudProviders_Azure_cloudImage', 'type': 'string', 'description': 'Azure VM image'},
            {'name': 'cloudProviders_Azure_cloudTags', 'path': 'cloudProviders_Azure_cloudTags', 'type': 'string', 'description': 'Azure resource tags (comma-separated)'},
            # Tags (flattened)
            {'name': 'tags_sentinelone', 'path': 'tags_sentinelone', 'type': 'string', 'description': 'SentinelOne tags (comma-separated)'},
        ]

        return {
            'source': 'sentinelone',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3
        }
