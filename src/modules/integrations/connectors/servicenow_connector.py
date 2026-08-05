# SPDX-License-Identifier: AGPL-3.0-or-later
"""
ServiceNow CMDB Connector

Collects Configuration Management Database (CMDB) items from ServiceNow.
Uses Basic Authentication with ServiceNow Table API and offset-based
pagination via the SDK OffsetPaginator.

Features:
- Fetches CMDB configuration items (cmdb_ci table)
- Enriches data with lookup tables (location, model, manufacturer)
- Configurable enrichment depth
- Field selection and filtering
"""

from typing import Dict, List, Iterator, Any, Optional, Tuple
import re

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import BasicAuth, OffsetPaginator, ConfigSchema


@ConnectorRegistry.register
class ServiceNowConnector(BaseConnector):
    """
    ServiceNow CMDB Connector

    Fetches CMDB configuration items from ServiceNow via Table API.
    Requires service account with read access to cmdb_ci and related tables.
    """

    # Connector Metadata
    integration_type = "servicenow_cmdb"
    display_name = "ServiceNow CMDB"
    category = "Configuration Management"
    description = "Collect configuration items from ServiceNow CMDB"
    logo_url = "/logos/servicenow.png"

    # CI classes representing physical or logical devices — only these are collected.
    # Uses an allowlist (IN) instead of a blocklist (NOT IN) because:
    # 1. ServiceNow CMDB has hundreds of non-device CI classes (NICs, disks, RAM,
    #    routing rules, software, etc.) that would need to be individually blocked.
    # 2. Custom class names (e.g., dscy_* from the Discovery plugin) bypass standard
    #    cmdb_ci_* naming, making a blocklist incomplete by design.
    # 3. A blocklist with 81 entries exceeded ServiceNow's URL length limit (400 error).
    #
    # If a new device class appears in ServiceNow, add it here.
    # Verified against ServiceNow production data (Mar 2026, 22K device records).
    ALLOWED_CI_CLASSES = (
        "cmdb_ci_computer",                 # Workstations, laptops, desktops (19K)
        "cmdb_ci_win_server",               # Windows servers (2.1K)
        "cmdb_ci_linux_server",             # Linux servers
        "cmdb_ci_aix_server",               # AIX servers
        "cmdb_ci_esx_server",               # ESXi hypervisors
        "cmdb_ci_hyper_v_server",           # Hyper-V hosts
        "cmdb_ci_vm_instance",              # Virtual machines (generic)
        "cmdb_ci_hyper_v_instance",         # Hyper-V VMs
        "cmdb_ci_ip_switch",                # Network switches
        "cmdb_ci_ip_router",                # Routers
        "cmdb_ci_printer",                  # Printers
        "cmdb_ci_storage_device",           # Storage arrays
        "cmdb_ci_ip_firewall",              # Firewalls (generic)
        "cmdb_ci_firewall_device_palo_alto",  # Palo Alto firewalls
    )

    # Field Collections
    ESSENTIAL_FIELDS = [
        "sys_id", "name", "asset_tag", "serial_number", "model_id",
        "manufacturer.name", "location", "assigned_to", "ip_address",
        "mac_address", "sys_class_name", "sys_created_on", "sys_updated_on"
    ]

    COMPREHENSIVE_FIELDS = ESSENTIAL_FIELDS + [
        "company", "department", "cost_center", "purchase_date",
        "warranty_expiration", "install_status", "operational_status",
        "managed_by", "owned_by", "supported_by", "u_business_unit",
        "u_environment", "comments", "short_description"
    ]

    # Lookup table configuration.
    # Only tables needed for asset generation are loaded.  `assigned_to`
    # (sys_user, 78K rows, ~2 min load) was removed — too large.
    LOOKUP_TABLES = {
        'model_id': {
            'table': 'cmdb_model',
            'fields': ['sys_id', 'name', 'display_name', 'manufacturer', 'model_number'],
            'description': 'Model information',
            'foreign_keys': {'manufacturer': 'manufacturer'},  # sub_key -> lookup_cache key
        },
        'location': {
            'table': 'cmn_location',
            'fields': ['sys_id', 'name', 'full_name', 'street', 'city', 'state', 'country'],
            'description': 'Location details'
        },
        'manufacturer': {
            'table': 'core_company',
            'fields': ['sys_id', 'name'],
            'description': 'Manufacturer/company information'
        },
    }

    def __init__(self, config: Dict[str, Any]):
        self.page_size = int(config.get('page_size', 5000))
        self.field_mode = config.get('field_mode', 'comprehensive').lower()

        # Parse instance from instance_url or use instance directly
        instance_url = config.get('instance_url')
        instance = config.get('instance')

        if instance_url:
            match = re.search(r'https?://([^.]+)\.service-now\.com', instance_url)
            if match:
                self.instance = match.group(1)
                self.base_url = instance_url.rstrip('/')
            else:
                self.instance = instance_url.replace('https://', '').replace('http://', '').split('.')[0]
                self.base_url = f"https://{self.instance}.service-now.com"
        elif instance:
            self.instance = instance
            self.base_url = f"https://{self.instance}.service-now.com"
        else:
            raise ValueError("Either 'instance_url' or 'instance' must be provided")

        # Enrichment configuration
        self.enable_enrichment = config.get('enable_enrichment', True)
        self.enrichment_fields = config.get('enrichment_fields', list(self.LOOKUP_TABLES.keys()))

        # Lookup cache (populated during enrichment)
        self.lookup_cache: Dict[str, Dict[str, Dict]] = {}

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return BasicAuth({
            'username': self.config.get('username'),
            'password': self.config.get('password'),
            'base_url': self.base_url,
        }, self.logger)

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_string(
                'instance', 'ServiceNow Instance',
                required=True,
                placeholder='dev12345',
                description='Instance name (e.g., dev12345 from dev12345.service-now.com)',
            )
            .add_string(
                'username', 'Username',
                required=True,
                placeholder='integration.user',
                description='ServiceNow service account username',
            )
            .add_password(
                'password', 'Password',
                required=True,
                description='ServiceNow password (will be encrypted)',
            )
            .add_select(
                'field_mode', 'Field Collection Mode',
                options=[
                    {'value': 'essential', 'label': 'Essential (13 fields)'},
                    {'value': 'comprehensive', 'label': 'Comprehensive (all fields)'},
                ],
                default='comprehensive',
                description='Choose which fields to collect',
            )
            .add_number(
                'page_size', 'Page Size',
                default=5000,
                min_val=100,
                max_val=10000,
                description='Records to fetch per API request',
            )
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                default=500,
                min_val=1,
                max_val=10000,
                description=(
                    'Safety cap on API pages fetched per collection run — it stops a '
                    'pagination loop that never terminates. Applies to both the CMDB CI '
                    'collection and each enrichment lookup table. At the default page '
                    'size of 5,000, the default cap of 500 pages collects up to '
                    '2,500,000 records.'
                ),
            )
            .add_boolean(
                'enable_enrichment', 'Enable Data Enrichment',
                default=True,
                description='Enrich CMDB data with lookup tables (location, model, manufacturer)',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info(f"Testing connection to ServiceNow ({self.instance})...")

            # Test with a simple query (get 1 record)
            response = self.auth.request(
                '/api/now/table/cmdb_ci',
                params={
                    'sysparm_limit': 1,
                    'sysparm_query': 'sys_class_nameIN' + ','.join(self.ALLOWED_CI_CLASSES),
                },
            )
            data = response.json()

            if 'result' in data:
                # Get actual count via stats API
                count_response = self.auth.request(
                    '/api/now/stats/cmdb_ci',
                    params={
                        'sysparm_count': 'true',
                        'sysparm_query': 'sys_class_nameIN' + ','.join(self.ALLOWED_CI_CLASSES),
                    },
                )
                count_data = count_response.json()

                ci_count = 0
                if 'result' in count_data and 'stats' in count_data['result']:
                    ci_count = int(count_data['result']['stats'].get('count', 0))

                return {
                    'success': True,
                    'message': f'Successfully connected to ServiceNow. Found {ci_count:,} configuration items.',
                    'metadata': {
                        'ci_count': ci_count,
                        'instance': self.instance,
                        'base_url': self.base_url,
                        'api_endpoint': '/api/now/table/cmdb_ci',
                    },
                }
            else:
                return {
                    'success': False,
                    'message': 'Unexpected response format from ServiceNow',
                    'error': 'Invalid response structure',
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
        # Load lookup tables first if enrichment is enabled
        self._load_lookup_tables()

        # Build base query parameters
        params = {
            'sysparm_query': 'sys_class_nameIN' + ','.join(self.ALLOWED_CI_CLASSES),
        }

        if self.field_mode == 'essential':
            params['sysparm_fields'] = ','.join(self.ESSENTIAL_FIELDS)
        elif self.field_mode == 'comprehensive':
            params['sysparm_fields'] = ','.join(self.COMPREHENSIVE_FIELDS)

        paginator = OffsetPaginator(
            page_size=self.page_size,
            max_pages=self.config.get('max_pagination_pages', 500),
        )

        page_count = 0
        for page in paginator.pages(lambda offset, limit: self._fetch_page(
            '/api/now/table/cmdb_ci', params, offset, limit
        )):
            page_count += 1
            self.logger.info(f"Fetched CI page {page_count} ({len(page)} records)")

            for ci_record in page:
                # Rename dot-walked field back to the standard field name so
                # downstream code (field mapper, enrichment) sees "manufacturer".
                if 'manufacturer.name' in ci_record:
                    ci_record['manufacturer'] = ci_record.pop('manufacturer.name')

                ci_record = self._flatten_reference_fields(ci_record)

                if self.enable_enrichment:
                    ci_record, _ = self._enrich_record(ci_record)

                yield ci_record

        self.warn_if_truncated(paginator, "ServiceNow CMDB CIs")

    # ------------------------------------------------------------------
    # Internal: page fetcher for OffsetPaginator
    # ------------------------------------------------------------------

    def _fetch_page(
        self,
        endpoint: str,
        base_params: Dict[str, Any],
        offset: int,
        limit: int,
    ) -> Tuple[List[dict], Optional[int]]:
        """
        Fetch one page from a ServiceNow Table API endpoint.

        Builds sysparm_offset/sysparm_limit into the request params and
        returns (records, total_or_none) for OffsetPaginator.

        Used for both CI data and lookup table fetches.
        """
        params = {**base_params, 'sysparm_offset': offset, 'sysparm_limit': limit}

        response = self.auth.request(endpoint, params=params)
        data = response.json()

        records = data.get('result', [])

        # ServiceNow returns X-Total-Count header when available
        total = None
        total_header = response.headers.get('X-Total-Count')
        if total_header is not None:
            try:
                total = int(total_header)
            except (ValueError, TypeError):
                pass

        return records, total

    # ------------------------------------------------------------------
    # Lookup table enrichment
    # ------------------------------------------------------------------

    def _fetch_lookup_table(self, field_name: str, lookup_config: Dict[str, Any]) -> Dict[str, Dict]:
        """
        Fetch a lookup table from ServiceNow using OffsetPaginator.

        Args:
            field_name: Name of the field being looked up
            lookup_config: Lookup table configuration

        Returns:
            Dictionary mapping sys_id to record data
        """
        table_name = lookup_config['table']
        fields = lookup_config['fields']

        self.logger.info(f"Fetching lookup table: {table_name} for {field_name}")

        lookup_data = {}
        base_params = {'sysparm_fields': ','.join(fields)}

        try:
            paginator = OffsetPaginator(
                page_size=self.page_size,
                max_pages=self.config.get('max_pagination_pages', 500),
            )

            for page in paginator.pages(lambda offset, limit: self._fetch_page(
                f'/api/now/table/{table_name}', base_params, offset, limit
            )):
                for record in page:
                    sys_id = record.get('sys_id')
                    if sys_id:
                        lookup_data[sys_id] = record

            # A truncated lookup table loses no assets — it under-enriches them.
            self.warn_if_truncated(
                paginator,
                f"ServiceNow enrichment lookup table {table_name}",
            )

            self.logger.info(f"Loaded {len(lookup_data)} records from {table_name}")

        except Exception as e:
            self.logger.warning(f"Failed to fetch lookup table {table_name}: {e}")

        return lookup_data

    def _load_lookup_tables(self):
        """Load all enabled lookup tables into cache."""
        if not self.enable_enrichment:
            self.logger.info("Enrichment disabled, skipping lookup tables")
            return
        if self.lookup_cache:
            self.logger.info("Lookup tables already loaded, skipping reload")
            return

        self.logger.info("Loading lookup tables for enrichment...")

        for field_name in self.enrichment_fields:
            if field_name in self.LOOKUP_TABLES:
                lookup_config = self.LOOKUP_TABLES[field_name]
                self.lookup_cache[field_name] = self._fetch_lookup_table(field_name, lookup_config)

        total_lookups = sum(len(table) for table in self.lookup_cache.values())
        self.logger.info(f"Loaded {len(self.lookup_cache)} lookup tables with {total_lookups:,} total records")

    @staticmethod
    def _flatten_reference_fields(record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flatten ServiceNow reference field objects to plain strings.

        Without sysparm_display_value, reference fields are objects:
          {"link": "...", "value": "sys_id"}
        With sysparm_display_value=all (legacy), ALL fields are wrapped:
          {"display_value": "Dell", "value": "sys_id", "link": "..."}

        This method collapses those dicts to a single string so downstream
        code (enrichment, storage, preview UI) never sees raw wrapper objects.
        """
        flat = {}
        for key, value in record.items():
            if isinstance(value, dict):
                if 'display_value' in value:
                    # sysparm_display_value=all wrapper — use display_value
                    flat[key] = value['display_value'] if value['display_value'] else value.get('value', '')
                elif 'value' in value and 'link' in value:
                    # Reference object — use sys_id (will be resolved by _enrich_record)
                    flat[key] = value.get('value', '')
                else:
                    flat[key] = value
            else:
                flat[key] = value
        return flat

    def _enrich_record(self, record: Dict[str, Any]):
        """
        Resolve reference field sys_ids using cached lookup tables.

        Called AFTER _flatten_reference_fields.  For each field with a
        cached lookup table, the raw sys_id is replaced with the display
        name and the lookup record fields are flattened as
        {field}_enriched.{subfield} top-level keys for direct field mapping.

        Args:
            record: Flattened CMDB record (reference fields are sys_id strings)

        Returns:
            (enriched_record, was_enriched) tuple
        """
        if not self.enable_enrichment or not self.lookup_cache:
            return record, False

        enriched = record.copy()
        was_enriched = False

        for field_name, lookup_table in self.lookup_cache.items():
            ref_value = record.get(field_name)

            if ref_value and isinstance(ref_value, str) and ref_value in lookup_table:
                lookup_record = lookup_table[ref_value]
                # Replace sys_id with human-readable name
                enriched[field_name] = (
                    lookup_record.get('display_name')
                    or lookup_record.get('name')
                    or lookup_record.get('full_name')
                    or ref_value
                )
                # Flatten lookup fields as top-level dot-notation keys
                fk_config = self.LOOKUP_TABLES.get(field_name, {}).get('foreign_keys', {})
                for sub_key, sub_value in lookup_record.items():
                    if sub_key == 'sys_id':
                        continue
                    # Resolve nested foreign key sys_ids via configured lookup
                    fk_cache_key = fk_config.get(sub_key)
                    if fk_cache_key and sub_value and isinstance(sub_value, str):
                        fk_cache = self.lookup_cache.get(fk_cache_key, {})
                        fk_record = fk_cache.get(sub_value)
                        if fk_record:
                            sub_value = fk_record.get('name', sub_value)
                    enriched[f'{field_name}_enriched.{sub_key}'] = sub_value
                was_enriched = True

        return enriched, was_enriched

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        # Define field types and descriptions
        field_metadata = {
            'sys_id': {'type': 'string', 'description': 'Unique system identifier', 'required': True},
            'name': {'type': 'string', 'description': 'Configuration item name', 'required': False},
            'asset_tag': {'type': 'string', 'description': 'Asset tag number', 'required': False},
            'serial_number': {'type': 'string', 'description': 'Serial number', 'required': False},
            'model_id': {'type': 'string', 'description': 'Model reference', 'required': False},
            'manufacturer': {'type': 'string', 'description': 'Manufacturer reference', 'required': False},
            'location': {'type': 'string', 'description': 'Location reference', 'required': False},
            'assigned_to': {'type': 'string', 'description': 'Assigned user reference', 'required': False},
            'ip_address': {'type': 'string', 'description': 'IP address', 'required': False},
            'mac_address': {'type': 'string', 'description': 'MAC address', 'required': False},
            'sys_class_name': {'type': 'string', 'description': 'CI class type', 'required': False},
            'sys_created_on': {'type': 'datetime', 'description': 'Creation timestamp', 'required': False},
            'sys_updated_on': {'type': 'datetime', 'description': 'Last update timestamp', 'required': False},
            'company': {'type': 'string', 'description': 'Company reference', 'required': False},
            'install_status': {'type': 'string', 'description': 'Installation status', 'required': False},
            'operational_status': {'type': 'string', 'description': 'Operational status', 'required': False},
        }

        fields = []
        for field_name in self.COMPREHENSIVE_FIELDS:
            field_info = field_metadata.get(field_name, {
                'type': 'string',
                'description': f'ServiceNow {field_name} field',
                'required': False,
            })
            fields.append({
                'name': field_name,
                'path': field_name,
                'type': field_info['type'],
                'description': field_info.get('description', ''),
                'required': field_info.get('required', False),
            })

        # Add flattened enrichment fields if enabled
        if self.enable_enrichment:
            for field_name, lookup_config in self.LOOKUP_TABLES.items():
                for sub_field in lookup_config['fields']:
                    if sub_field == 'sys_id':
                        continue
                    fields.append({
                        'name': f'{field_name}_enriched.{sub_field}',
                        'path': f'{field_name}_enriched.{sub_field}',
                        'type': 'string',
                        'description': f'{sub_field} from {lookup_config["description"]}',
                        'required': False,
                    })

        return {
            'source': 'servicenow_cmdb',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
            'enrichment_available': self.enable_enrichment,
            'lookup_tables': list(self.LOOKUP_TABLES.keys()) if self.enable_enrichment else [],
        }
