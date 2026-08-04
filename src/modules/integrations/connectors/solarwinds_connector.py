# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SolarWinds Orion Connector

Collects network device and server asset data from SolarWinds Orion
via the SolarWinds Information Service (SWIS) REST API using SWQL queries.

Auth: BasicAuth (username/password).
Endpoint: POST https://{host}:{port}/SolarWinds/InformationService/v3/Json/Query
Default port: 17774 (modern), 17778 (legacy/deprecated).
Pagination: SWQL WITH ROWS x TO y WITH TOTALROWS.
SSL: Self-signed certs are common in SolarWinds deployments; verify_ssl
defaults to True (fail-closed) and operators must opt out explicitly.
"""

from typing import Dict, List, Iterator, Any, Optional
import time
import urllib3
import requests

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import BasicAuth, ConfigSchema


# Default SWIS API port (modern SolarWinds 2023.1+)
DEFAULT_PORT = 17774

# Pagination defaults
DEFAULT_PAGE_SIZE = 1000

# Request timeout (seconds) -- SWQL queries on large environments can be slow
DEFAULT_REQUEST_TIMEOUT = 120

# Max pagination batches safety cap (1000 * 1000 = 1M records)
MAX_PAGINATION_BATCHES = 1000

# Delay between paginated requests (seconds)
DEFAULT_PAGE_DELAY = 0.1

# ===== SWQL Queries =====

NODE_QUERY = (
    "SELECT NodeID, ObjectSubType, IPAddress, IPAddressType, DynamicIP, "
    "Caption, NodeDescription, Description, DNS, SysName, Vendor, "
    "SysObjectID, Location, Contact, IOSImage, IOSVersion, "
    "StatusDescription, MachineType, IsServer, Severity, LastSync, "
    "LastSystemUpTimePollUtc, CPUCount, CPULoad, MemoryUsed, TotalMemory, "
    "PercentMemoryUsed, Model, SerialNumber, LastBoot, EntityType, Category "
    "FROM Orion.Nodes WHERE Status IS NOT NULL ORDER BY NodeID"
)

# Fallback query -- columns available across all SolarWinds versions
NODE_QUERY_CORE = (
    "SELECT NodeID, ObjectSubType, IPAddress, IPAddressType, DynamicIP, "
    "Caption, NodeDescription, Description, DNS, SysName, Vendor, "
    "SysObjectID, Location, Contact, IOSImage, IOSVersion, "
    "StatusDescription, MachineType, IsServer, Severity, LastSync, "
    "CPUCount, CPULoad, MemoryUsed, TotalMemory, PercentMemoryUsed "
    "FROM Orion.Nodes WHERE Status IS NOT NULL ORDER BY NodeID"
)

VM_QUERY = (
    "SELECT VirtualMachineID, Name, IPAddress, HostName, Os, Status, "
    "DnsName, CpuCount, MemoryConfigured, PowerState, DateCreated, "
    "HostID, ClusterName, StatusDescription "
    "FROM Orion.VIM.VirtualMachines ORDER BY VirtualMachineID"
)

ENDPOINT_QUERY = (
    "SELECT ae.MacAddress AS mac, MAX(ae.Vendor) AS vendor, "
    "MAX(ae.IpAddress) AS ip, ae.NodeId, "
    "ae.ConnectedTo AS asset_connected_to, MAX(ae.DnsName) AS dns, "
    "MAX(ae.UserName) AS user_name, ae.ConnectionType AS connection_type, "
    "MAX(ae.EndpointType) AS endpoint_type, "
    "n.Description AS asset_connected_to_desc, n.Location AS location "
    "FROM Orion.UDT.DeviceInventory ae "
    "LEFT JOIN Orion.Nodes n ON n.NodeId = ae.NodeId "
    "GROUP BY ae.MacAddress, ae.ConnectedTo, ae.ConnectionType, "
    "ae.NodeId, n.Description, n.Location"
)

TEST_QUERY = "SELECT TOP 1 NodeID FROM Orion.Nodes"
VERSION_QUERY = "SELECT TOP 1 Version FROM Orion.ProductVersion"

# Module probe table names
VIM_TABLE = "Orion.VIM.VirtualMachines"
UDT_TABLE = "Orion.UDT.DeviceInventory"


@ConnectorRegistry.register
class SolarWindsConnector(BaseConnector):
    """
    SolarWinds Orion Connector

    Fetches network device and server inventory from SolarWinds Orion
    via the SWIS REST API. Supports paginated SWQL queries for nodes,
    optional VIM (virtual machines) and UDT (device inventory) modules.
    """

    # Connector Metadata
    integration_type = "solarwinds_orion"
    display_name = "SolarWinds Orion"
    category = "Network Monitoring"
    description = "Collect network device and server asset data from SolarWinds Orion"
    logo_url = "/logos/solarwinds.png"

    def __init__(self, config: Dict[str, Any]):
        # Extract configuration before super().__init__
        self.server_url = config.get('server_url', '').rstrip('/')
        self.port = int(config.get('port', DEFAULT_PORT))
        self.verify_ssl = config.get('verify_ssl', True)
        self.page_size = int(config.get('page_size', DEFAULT_PAGE_SIZE))
        self.request_timeout = int(config.get('request_timeout', DEFAULT_REQUEST_TIMEOUT))
        self.discover_vms = config.get('discover_vms', False)
        self.discover_endpoints = config.get('discover_endpoints', False)
        self.page_delay = float(config.get('page_delay', DEFAULT_PAGE_DELAY))

        if not self.server_url:
            raise ValueError("SolarWinds server URL is required")
        if not config.get('username'):
            raise ValueError("SolarWinds username is required")
        if not config.get('password'):
            raise ValueError("SolarWinds password is required")

        # Build SWIS query endpoint URL
        self.swis_url = (
            f"https://{self.server_url}:{self.port}"
            f"/SolarWinds/InformationService/v3/Json/Query"
        )

        super().__init__(config)

        # Configure session for SolarWinds specifics
        self.auth.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        self.auth.session.verify = self.verify_ssl

        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return BasicAuth(
            config={
                'username': self.config.get('username'),
                'password': self.config.get('password'),
                'base_url': f"https://{self.server_url}:{self.port}",
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
                'server_url', 'Server Hostname / IP',
                required=True,
                placeholder='solarwinds.corp.com',
                description='SolarWinds Orion server hostname or IP address (without https:// or port)',
            )
            .add_number(
                'port', 'API Port',
                default=DEFAULT_PORT,
                min_val=1,
                max_val=65535,
                description='SWIS API port (17774 for 2023.1+, 17778 for legacy)',
            )
            .add_string(
                'username', 'Username',
                required=True,
                placeholder='api_user',
                description='SolarWinds API username (service account recommended)',
            )
            .add_password(
                'password', 'Password',
                required=True,
                description='SolarWinds API password (will be encrypted)',
            )
            .add_boolean(
                'verify_ssl', 'Verify SSL Certificate',
                default=True,
                description='Enable SSL certificate verification (disable only for self-signed certs)',
            )
            .add_number(
                'request_timeout', 'Request Timeout (seconds)',
                default=DEFAULT_REQUEST_TIMEOUT,
                min_val=10,
                max_val=600,
                description='Timeout for each API request in seconds',
            )
            .add_number(
                'page_size', 'Page Size',
                default=DEFAULT_PAGE_SIZE,
                min_val=100,
                max_val=10000,
                description='Number of records per SWQL query page',
            )
            .add_boolean(
                'discover_vms', 'Discover Virtual Machines',
                default=False,
                description='Collect VMs from VIM module (requires VIM license)',
            )
            .add_boolean(
                'discover_endpoints', 'Discover Endpoints',
                default=False,
                description='Collect endpoints from UDT module (requires UDT license)',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info(
                f"Testing connection to SolarWinds ({self.server_url}:{self.port})..."
            )

            result = self._execute_swql(TEST_QUERY)

            if not isinstance(result, dict) or 'results' not in result:
                return {
                    'success': False,
                    'message': 'Unexpected response format from SolarWinds',
                    'error': str(result)[:500],
                }

            # Detect version
            version = None
            try:
                version_result = self._execute_swql(VERSION_QUERY)
                if (version_result
                        and 'results' in version_result
                        and len(version_result['results']) > 0):
                    version = version_result['results'][0].get('Version')
            except Exception:
                self.logger.debug("Could not detect SolarWinds version")

            # Probe optional modules
            modules = {'Core': True}
            if self.discover_vms:
                modules['VIM'] = self._check_module_available(VIM_TABLE)
            if self.discover_endpoints:
                modules['UDT'] = self._check_module_available(UDT_TABLE)

            message = 'Successfully connected to SolarWinds Orion'
            if version:
                message += f' (version {version})'

            return {
                'success': True,
                'message': message,
                'metadata': {
                    'server': self.server_url,
                    'port': self.port,
                    'version': version,
                    'modules': modules,
                },
            }

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            if status_code == 401:
                msg = 'Authentication failed. Check username and password.'
            elif status_code == 403:
                msg = 'Access forbidden. Check user permissions.'
            else:
                msg = f'HTTP error: {e}'
            self.logger.error(f"Connection test failed: {msg}")
            return {'success': False, 'message': msg, 'error': str(e)}

        except requests.exceptions.SSLError as e:
            msg = 'SSL error. Try disabling SSL verification for self-signed certificates.'
            self.logger.error(f"Connection test failed: {msg}")
            return {'success': False, 'message': msg, 'error': str(e)}

        except requests.exceptions.ConnectionError as e:
            msg = (
                f'Cannot connect to {self.server_url}:{self.port}. '
                'Check hostname, port, and firewall rules.'
            )
            self.logger.error(f"Connection test failed: {msg}")
            return {'success': False, 'message': msg, 'error': str(e)}

        except requests.exceptions.Timeout as e:
            msg = f'Connection timed out after {self.request_timeout}s.'
            self.logger.error(f"Connection test failed: {msg}")
            return {'success': False, 'message': msg, 'error': str(e)}

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
        # 1. Fetch nodes (always) -- fall back to core columns on query error
        self.logger.info("Fetching Orion.Nodes...")
        try:
            yield from self._fetch_typed_records(NODE_QUERY, 'node')
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                self.logger.warning(
                    "Node query failed (400). Retrying with core columns..."
                )
                yield from self._fetch_typed_records(NODE_QUERY_CORE, 'node')
            else:
                raise

        # 2. Fetch VMs (optional, requires VIM module)
        if self.discover_vms:
            if self._check_module_available(VIM_TABLE):
                self.logger.info("Fetching Orion.VIM.VirtualMachines...")
                yield from self._fetch_typed_records(VM_QUERY, 'virtual_machine')
            else:
                self.logger.warning("VIM module not available, skipping VM discovery")

        # 3. Fetch endpoints (optional, requires UDT module)
        if self.discover_endpoints:
            if self._check_module_available(UDT_TABLE):
                self.logger.info("Fetching Orion.UDT.DeviceInventory...")
                try:
                    result = self._execute_swql(ENDPOINT_QUERY)
                    for row in result.get('results', []):
                        row['_record_type'] = 'endpoint'
                        yield row
                except Exception as e:
                    self.logger.error(f"Error fetching UDT endpoints: {e}")
            else:
                self.logger.warning("UDT module not available, skipping endpoint discovery")

    def _fetch_typed_records(
        self, query: str, record_type: str,
    ) -> Iterator[Dict[str, Any]]:
        """Fetch paginated records and tag each with _record_type."""
        for record in self._fetch_paginated(query, record_type):
            record['_record_type'] = record_type
            yield record

    # ------------------------------------------------------------------
    # Internal: SWQL query execution
    # ------------------------------------------------------------------

    def _execute_swql(self, query: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {'query': query}
        if params:
            payload['parameters'] = params

        call_start = time.time()

        response = self.auth.session.post(
            self.swis_url,
            json=payload,
            timeout=self.request_timeout,
            verify=self.verify_ssl,
        )

        duration_ms = (time.time() - call_start) * 1000

        self.log_api_call(
            endpoint=self.swis_url,
            method='POST',
            status_code=response.status_code,
            duration_ms=duration_ms,
            success=response.ok,
            error_message=None if response.ok else response.text[:500],
        )

        if not response.ok:
            self.logger.error(
                f"SWIS query failed (HTTP {response.status_code}): "
                f"{response.text[:500]}"
            )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Internal: Module availability probe
    # ------------------------------------------------------------------

    def _check_module_available(self, table_name: str) -> bool:
        try:
            probe_query = f"SELECT TOP 1 * FROM {table_name}"
            result = self._execute_swql(probe_query)
            return result is not None and 'results' in result
        except Exception as e:
            self.logger.debug(f"Module {table_name} not available: {e}")
            return False

    # ------------------------------------------------------------------
    # Internal: paginated SWQL fetch
    # ------------------------------------------------------------------

    def _fetch_paginated(
        self, base_query: str, record_type: str,
    ) -> Iterator[Dict[str, Any]]:
        start_row = 1
        batch_count = 0
        total_rows = None

        while True:
            batch_count += 1

            if batch_count > MAX_PAGINATION_BATCHES:
                self.logger.warning(
                    f"Reached max pagination batches ({MAX_PAGINATION_BATCHES}), "
                    f"stopping {record_type} fetch"
                )
                break

            end_row = start_row + self.page_size - 1
            paginated_query = (
                f"{base_query} WITH ROWS {start_row} TO {end_row} WITH TOTALROWS"
            )

            self.logger.debug(
                f"Fetching {record_type} batch {batch_count} "
                f"(rows {start_row}-{end_row})..."
            )

            result = self._execute_swql(paginated_query)
            rows = result.get('results', [])

            if total_rows is None and 'totalRows' in result:
                total_rows = result['totalRows']
                self.logger.info(
                    f"Total {record_type} records available: {total_rows:,}"
                )

            if not rows:
                break

            for row in rows:
                yield row

            if total_rows is not None and (start_row + len(rows) - 1) >= total_rows:
                break

            if len(rows) < self.page_size:
                break

            start_row += self.page_size

            if self.page_delay > 0:
                time.sleep(self.page_delay)

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        fields = [
            {'name': 'NodeID', 'path': 'NodeID', 'type': 'number', 'description': 'SolarWinds node ID', 'required': True},
            {'name': 'ObjectSubType', 'path': 'ObjectSubType', 'type': 'string', 'description': 'Node sub-type (SNMP, ICMP, WMI, Agent)'},
            {'name': 'IPAddress', 'path': 'IPAddress', 'type': 'string', 'description': 'Primary IP address'},
            {'name': 'IPAddressType', 'path': 'IPAddressType', 'type': 'string', 'description': 'IP address type (IPv4/IPv6)'},
            {'name': 'Caption', 'path': 'Caption', 'type': 'string', 'description': 'Node display name'},
            {'name': 'NodeDescription', 'path': 'NodeDescription', 'type': 'string', 'description': 'Node description (SNMP sysDescr)'},
            {'name': 'DNS', 'path': 'DNS', 'type': 'string', 'description': 'DNS hostname'},
            {'name': 'SysName', 'path': 'SysName', 'type': 'string', 'description': 'SNMP system name'},
            {'name': 'Vendor', 'path': 'Vendor', 'type': 'string', 'description': 'Hardware vendor'},
            {'name': 'SysObjectID', 'path': 'SysObjectID', 'type': 'string', 'description': 'SNMP system OID'},
            {'name': 'Location', 'path': 'Location', 'type': 'string', 'description': 'Physical location'},
            {'name': 'Contact', 'path': 'Contact', 'type': 'string', 'description': 'Contact information'},
            {'name': 'IOSImage', 'path': 'IOSImage', 'type': 'string', 'description': 'IOS image name'},
            {'name': 'IOSVersion', 'path': 'IOSVersion', 'type': 'string', 'description': 'IOS/firmware version'},
            {'name': 'StatusDescription', 'path': 'StatusDescription', 'type': 'string', 'description': 'Current status description'},
            {'name': 'MachineType', 'path': 'MachineType', 'type': 'string', 'description': 'Machine type'},
            {'name': 'IsServer', 'path': 'IsServer', 'type': 'boolean', 'description': 'Whether node is a server'},
            {'name': 'Severity', 'path': 'Severity', 'type': 'number', 'description': 'Alert severity level'},
            {'name': 'LastSync', 'path': 'LastSync', 'type': 'datetime', 'description': 'Last synchronization time'},
            {'name': 'CPUCount', 'path': 'CPUCount', 'type': 'number', 'description': 'Number of CPUs'},
            {'name': 'CPULoad', 'path': 'CPULoad', 'type': 'number', 'description': 'Current CPU load percentage'},
            {'name': 'MemoryUsed', 'path': 'MemoryUsed', 'type': 'number', 'description': 'Memory used (bytes)'},
            {'name': 'TotalMemory', 'path': 'TotalMemory', 'type': 'number', 'description': 'Total memory (bytes)'},
            {'name': 'PercentMemoryUsed', 'path': 'PercentMemoryUsed', 'type': 'number', 'description': 'Memory usage percentage'},
            {'name': 'Model', 'path': 'Model', 'type': 'string', 'description': 'Hardware model'},
            {'name': 'SerialNumber', 'path': 'SerialNumber', 'type': 'string', 'description': 'Hardware serial number'},
            {'name': 'LastBoot', 'path': 'LastBoot', 'type': 'datetime', 'description': 'Last boot time'},
            {'name': 'EntityType', 'path': 'EntityType', 'type': 'string', 'description': 'Entity type classification'},
            {'name': 'Category', 'path': 'Category', 'type': 'string', 'description': 'Node category'},
        ]
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
