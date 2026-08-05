#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Active Directory (LDAP) Connector

Collects computer objects from on-premises Active Directory via LDAP protocol
using the ldap3 library. Supports LDAPS (636), LDAP+StartTLS (389), and
Global Catalog (3269/3268) connections.

Authentication: LDAP Simple Bind (default) or NTLM.
Pagination: LDAP Simple Paged Results (AD hard limit 1000/response).
"""

from typing import Dict, List, Iterator, Any, Optional
from datetime import datetime, timezone
import ssl

try:
    from ldap3 import Server, Connection, ALL, NTLM, SIMPLE, SUBTREE, Tls
    from ldap3.core.exceptions import LDAPException
except ImportError:
    Server = Connection = ALL = NTLM = SIMPLE = SUBTREE = Tls = None  # type: ignore
    LDAPException = Exception  # type: ignore

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import LDAPAuth, ConfigSchema


# Default LDAP connection timeout (seconds)
DEFAULT_CONNECT_TIMEOUT = 30

# Maximum pages before stopping (default safety cap; overridable via
# max_pagination_pages config)
MAX_PAGINATION_PAGES = 2000

# LDAP paged results control OID
PAGED_RESULTS_OID = '1.2.840.113556.1.4.319'

# Full attribute list for computer objects
COMPUTER_ATTRIBUTES = [
    'accountExpires', 'cn', 'countryCode', 'description',
    'distinguishedName', 'dNSHostName', 'userAccountControl', 'location',
    'managedBy', 'memberOf', 'name', 'objectGUID', 'objectSid',
    'operatingSystem', 'operatingSystemVersion', 'primaryGroupID',
    'sAMAccountName', 'sAMAccountType', 'whenCreated',
    'lastLogonTimestamp', 'pwdLastSet',
]

# Core attribute fallback — if full list causes schema errors, retry with these
COMPUTER_ATTRIBUTES_CORE = [
    'cn', 'distinguishedName', 'dNSHostName', 'userAccountControl',
    'name', 'objectGUID', 'objectSid', 'operatingSystem',
    'sAMAccountName', 'whenCreated',
]


@ConnectorRegistry.register
class ActiveDirectoryConnector(BaseConnector):
    """
    Active Directory (LDAP) Connector

    Fetches computer objects from on-premises Active Directory via LDAP.
    Supports Simple Bind and NTLM authentication, LDAPS/StartTLS encryption,
    and Global Catalog queries for multi-domain forests.
    """

    # Connector Metadata
    integration_type = "active_directory"
    display_name = "Active Directory (LDAP)"
    category = "Identity Management"
    description = "Collect computer inventory from on-premises Active Directory via LDAP"
    logo_url = "/logos/MS-active-directory.png"

    def __init__(self, config: Dict[str, Any]):
        # Extract configuration before calling super().__init__() so that
        # _build_auth() (called by super) can access these attributes.
        self.server_address = config.get('server')
        self.bind_dn = config.get('bind_dn')
        self.bind_password = config.get('bind_password')
        self.search_base = config.get('search_base')
        self.domain = config.get('domain', '')
        self.use_ssl = config.get('use_ssl', True)
        self.verify_cert = config.get('verify_cert', True)
        self.auth_method = config.get('auth_method', 'SIMPLE')
        self.page_size = int(config.get('page_size', 500))
        self.max_pagination_pages = int(
            config.get('max_pagination_pages', MAX_PAGINATION_PAGES)
        )
        self.search_filter = config.get('search_filter', '(objectClass=computer)')
        self.connect_timeout = int(config.get('connect_timeout', DEFAULT_CONNECT_TIMEOUT))
        self.use_global_catalog = config.get('use_global_catalog', False)
        self.search_all_domains = config.get('search_all_domains', False)
        self.additional_base_dns = config.get('additional_base_dns', '')

        # Resolve port
        default_port = self._resolve_default_port()
        self.port = int(config.get('port') or default_port)

        # Derive search_base from domain if not explicitly provided
        if not self.search_base and self.domain:
            self.search_base = self._derive_search_base(self.domain)

        # Validate required fields
        if not self.server_address:
            raise ValueError("Active Directory server address is required")
        if not self.bind_dn:
            raise ValueError("Bind DN (username) is required")
        if not self.bind_password:
            raise ValueError("Bind password is required")
        # search_base not required when using GC with forest-wide search
        if not self.search_base and not (self.use_global_catalog and self.search_all_domains):
            raise ValueError(
                "Search base is required. Provide search_base or domain, "
                "or enable Global Catalog with Search All Domains."
            )

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK framework hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return LDAPAuth(self.config, self.logger)

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            # -- Endpoint --
            .add_string(
                'server', 'Server Address',
                required=True,
                placeholder='dc01.corp.com',
                description='LDAP server hostname or IP (e.g., dc01.corp.com)',
            )
            .add_number(
                'port', 'Port',
                required=False,
                default=636,
                min_val=1,
                max_val=65535,
                description='LDAP port (636=LDAPS, 389=LDAP, 3269/3268=Global Catalog)',
            )
            # -- Authentication --
            .add_select(
                'auth_method', 'Authentication Method',
                options=[
                    {'value': 'SIMPLE', 'label': 'Simple Bind (DN + password)'},
                    {'value': 'NTLM', 'label': 'NTLM (DOMAIN\\\\user)'},
                ],
                default='SIMPLE',
                required=False,
                description='LDAP authentication mechanism',
            )
            .add_string(
                'bind_dn', 'Bind DN / Username',
                required=True,
                placeholder='CN=svc_ldap,OU=Service Accounts,DC=corp,DC=com',
                description=(
                    'Distinguished name or username for LDAP bind. '
                    'NTLM: DOMAIN\\\\user or user@domain.com. '
                    'Simple: CN=svc_account,OU=Service Accounts,DC=corp,DC=com'
                ),
            )
            .add_password(
                'bind_password', 'Bind Password',
                required=True,
                description='Password for LDAP bind (will be encrypted)',
            )
            # -- Connection options --
            .add_boolean(
                'use_ssl', 'Use SSL/TLS (LDAPS)',
                default=True,
                required=False,
                description='Connect over LDAPS (port 636). Recommended for production.',
            )
            .add_boolean(
                'verify_cert', 'Verify SSL Certificate',
                default=True,
                required=False,
                description='Validate the server SSL certificate. Disable for self-signed certs.',
            )
            .add_number(
                'connect_timeout', 'Connection Timeout (s)',
                required=False,
                default=30,
                min_val=5,
                max_val=120,
                description='LDAP connection timeout in seconds',
            )
            # -- Scope --
            .add_string(
                'domain', 'Domain Name',
                required=False,
                placeholder='corp.com',
                description=(
                    'AD domain name (e.g., corp.com). Used to derive search '
                    'base if not provided explicitly.'
                ),
            )
            .add_string(
                'search_base', 'Search Base DN',
                required=False,
                placeholder='DC=corp,DC=com',
                description=(
                    'Base DN for LDAP search (e.g., DC=corp,DC=com). '
                    'If empty, derived from domain.'
                ),
            )
            # -- Query / Filter --
            .add_string(
                'search_filter', 'Search Filter',
                required=False,
                placeholder='(objectClass=computer)',
                description='LDAP search filter for computer objects',
            )
            # -- Performance --
            .add_number(
                'page_size', 'Page Size',
                required=False,
                default=500,
                min_val=10,
                max_val=1000,
                description=(
                    'Number of records per LDAP page (AD max is 1000). '
                    'Default 500.'
                ),
            )
            .add_number(
                'max_pagination_pages', 'Max Pagination Pages',
                required=False,
                default=MAX_PAGINATION_PAGES,
                min_val=1,
                max_val=100000,
                description=(
                    'Safety cap on LDAP pages fetched per search base '
                    '(cap × page size records max).'
                ),
            )
            # -- Feature toggles --
            .add_boolean(
                'use_global_catalog', 'Use Global Catalog',
                default=False,
                required=False,
                description=(
                    'Query via Global Catalog (ports 3268/3269) for '
                    'multi-domain forest visibility.'
                ),
            )
            .add_boolean(
                'search_all_domains', 'Search All Domains',
                default=False,
                required=False,
                description=(
                    'Search the entire AD forest. With Global Catalog enabled, '
                    'queries all domains in one request. Without GC, iterates '
                    'over additional base DNs.'
                ),
            )
            .add_string(
                'additional_base_dns', 'Additional Base DNs',
                required=False,
                placeholder='DC=emea,DC=corp,DC=com, DC=apac,DC=corp,DC=com',
                description=(
                    'Comma-separated list of additional base DNs to search '
                    '(used when Search All Domains is enabled without Global Catalog). '
                    'Example: DC=emea,DC=corp,DC=com,DC=apac,DC=corp,DC=com'
                ),
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info(
                f"Testing connection to AD ({self.server_address}:{self.port})..."
            )

            conn = self._create_connection()
            try:
                # Use forest-wide base if GC + search_all_domains
                test_base = self._get_search_bases()[0]
                conn.search(
                    search_base=test_base,
                    search_filter=self.search_filter,
                    search_scope=SUBTREE,
                    attributes=['cn'],
                    size_limit=1,
                )
                result_count = len(conn.entries)

                base_desc = test_base if test_base else '(forest-wide)'
                return {
                    'success': True,
                    'message': (
                        f'Successfully connected to Active Directory. '
                        f'Found computer objects in {base_desc}.'
                    ),
                    'metadata': {
                        'server': self.server_address,
                        'port': self.port,
                        'search_base': base_desc,
                        'auth_method': self.auth_method,
                        'use_global_catalog': self.use_global_catalog,
                        'search_all_domains': self.search_all_domains,
                        'sample_count': result_count,
                    },
                }

            finally:
                conn.unbind()

        except LDAPException as e:
            self.logger.error(f"LDAP connection test failed: {e}")
            return {
                'success': False,
                'message': f'LDAP connection failed: {str(e)}',
                'error': str(e),
            }
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e),
            }

    # ------------------------------------------------------------------
    # _collect_records (SDK entry point — called by fetch_data template)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        self.logger.info(f"Starting AD computer fetch (batch: {batch_id})")

        yielded_count = 0
        failed_count = 0
        dedup_count = 0
        seen: set = set()  # (computer_name, domain) for deduplication

        for entry_dict in self._paged_search():
            try:
                record = self._process_entry(entry_dict)
            except Exception as e:
                self.logger.error(f"Error processing LDAP entry: {e}")
                if hasattr(self, '_stats'):
                    self._stats["errors"] += 1
                failed_count += 1
                continue

            # Deduplicate by (name, domain) — relevant for multi-base searches
            dedup_key = (
                (record.get('cn') or record.get('name', '')).lower(),
                record.get('domain', '').lower(),
            )
            if dedup_key in seen:
                dedup_count += 1
                continue
            seen.add(dedup_key)

            yield record
            yielded_count += 1

            fetched_so_far = yielded_count + failed_count
            if fetched_so_far % 1000 == 0 and fetched_so_far:
                self.logger.info(f"Fetched {fetched_so_far:,} computer records...")

        if dedup_count:
            self.logger.info(f"Deduplicated {dedup_count:,} duplicate entries")
        self.logger.info(
            f"Completed AD fetch ({failed_count} entries failed processing)"
        )

    # ------------------------------------------------------------------
    # Internal: paged LDAP search with core-attribute fallback
    # ------------------------------------------------------------------

    def _paged_search(self) -> Iterator[Dict[str, Any]]:
        """
        Perform paged LDAP search yielding raw entry dicts.

        Iterates over all configured search bases (single base, GC forest-wide,
        or multiple base DNs). Falls back to core attributes if the full
        attribute list causes schema errors on the server.
        """
        search_bases = self._get_search_bases()
        conn = self._create_connection()

        for search_base in search_bases:
            base_desc = search_base if search_base else '(forest-wide)'
            self.logger.info(f"Searching base: {base_desc}")

            try:
                yield from self._execute_paged_search(
                    conn, COMPUTER_ATTRIBUTES, search_base
                )
            except LDAPException as e:
                error_msg = str(e).lower()
                if 'invalid attribute' in error_msg or 'undefined attribute' in error_msg:
                    self.logger.warning(
                        "Some attributes not available in AD schema. "
                        "Retrying with core attributes only."
                    )
                    # Reconnect — previous connection may be in error state
                    try:
                        conn.unbind()
                    except Exception:
                        pass
                    conn = self._create_connection()
                    yield from self._execute_paged_search(
                        conn, COMPUTER_ATTRIBUTES_CORE, search_base
                    )
                else:
                    raise

        conn.unbind()

    def _get_search_bases(self) -> List[str]:
        """
        Determine the list of search bases to query.

        - Default: [self.search_base] (single domain)
        - GC + search_all_domains: [""] (empty string = forest-wide)
        - No GC + search_all_domains: [self.search_base] + additional_base_dns
        """
        if self.search_all_domains:
            if self.use_global_catalog:
                # Empty base DN triggers forest-wide search on Global Catalog
                return [""]
            else:
                # Iterate over primary + additional base DNs
                bases = []
                if self.search_base:
                    bases.append(self.search_base)
                if self.additional_base_dns:
                    for dn in self.additional_base_dns.split(','):
                        dn = dn.strip()
                        if dn and dn not in bases:
                            bases.append(dn)
                return bases if bases else [self.search_base]

        return [self.search_base]

    def _execute_paged_search(
        self, conn: 'Connection', attributes: List[str],
        search_base: str = None,
    ) -> Iterator[Dict[str, Any]]:
        """Execute a paged LDAP search and yield raw entry attribute dicts."""
        if search_base is None:
            search_base = self.search_base
        page_count = 0

        conn.search(
            search_base=search_base,
            search_filter=self.search_filter,
            search_scope=SUBTREE,
            attributes=attributes,
            paged_size=self.page_size,
        )

        for entry in conn.entries:
            yield self._entry_to_dict(entry)
        page_count += 1

        # Get paged cookie
        cookie = self._get_paged_cookie(conn)

        while cookie:
            if page_count >= self.max_pagination_pages:
                base_desc = search_base if search_base else '(forest-wide)'
                self.record_truncation_warning(
                    f'Active Directory computers in {base_desc}',
                    self.max_pagination_pages,
                )
                break

            conn.search(
                search_base=search_base,
                search_filter=self.search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
                paged_size=self.page_size,
                paged_cookie=cookie,
            )

            for entry in conn.entries:
                yield self._entry_to_dict(entry)
            page_count += 1

            self.logger.debug(
                f"Page {page_count}: {len(conn.entries)} entries"
            )

            cookie = self._get_paged_cookie(conn)

        self.logger.info(f"Paged search completed: {page_count} pages")

    @staticmethod
    def _get_paged_cookie(conn: 'Connection') -> Optional[bytes]:
        """Extract the paged results cookie from LDAP response controls."""
        try:
            controls = conn.result.get('controls', {})
            paged = controls.get(PAGED_RESULTS_OID, {})
            cookie = paged.get('value', {}).get('cookie')
            return cookie if cookie else None
        except (AttributeError, KeyError, TypeError):
            return None

    @staticmethod
    def _entry_to_dict(entry: Any) -> Dict[str, Any]:
        """Convert an ldap3 entry object to a plain dict of attribute values."""
        result = {}
        for attr in entry.entry_attributes:
            value = entry[attr].value
            result[attr] = value
        return result

    # ------------------------------------------------------------------
    # Internal: entry processing and field derivation
    # ------------------------------------------------------------------

    def _process_entry(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert raw LDAP entry dict to a flat, normalized record.

        Handles binary attributes (SID, GUID), FileTime timestamps,
        multi-valued attributes, and derives enabled/domain/ou_path.
        """
        record = {}

        for attr, value in raw.items():
            attr_lower = attr.lower()

            # Binary SID
            if attr_lower == 'objectsid' and isinstance(value, bytes):
                record[attr] = self._sid_to_string(value)
            # Binary GUID
            elif attr_lower == 'objectguid' and isinstance(value, bytes):
                record[attr] = self._guid_to_string(value)
            # FileTime timestamps (stored as 64-bit ints)
            elif attr_lower in (
                'lastlogontimestamp', 'pwdlastset', 'accountexpires',
            ):
                if isinstance(value, int):
                    converted = self._filetime_to_datetime(value)
                    record[attr] = converted if converted else None
                elif isinstance(value, datetime):
                    record[attr] = value.strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    record[attr] = value if value is not None else None
            # whenCreated — AD returns as datetime or string
            elif attr_lower == 'whencreated':
                if isinstance(value, datetime):
                    record[attr] = value.strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    record[attr] = value if value is not None else None
            # Multi-valued attributes (e.g., memberOf)
            elif isinstance(value, list):
                record[attr] = ';'.join(str(v) for v in value) if value else ''
            else:
                record[attr] = value if value is not None else ''

        # Derive 'enabled' from userAccountControl
        uac_value = raw.get('userAccountControl')
        if uac_value is not None:
            record['enabled'] = self._derive_enabled(uac_value)

        # Derive domain and ou_path from distinguishedName
        dn = raw.get('distinguishedName', '')
        if dn:
            record['domain'] = self._derive_domain(dn)
            record['ou_path'] = self._derive_ou_path(dn)

        return record

    # ------------------------------------------------------------------
    # Binary converters
    # ------------------------------------------------------------------

    @staticmethod
    def _sid_to_string(sid_bytes: bytes) -> str:
        """Convert binary SID to string representation (S-1-5-...)."""
        try:
            revision = sid_bytes[0]
            sub_authority_count = sid_bytes[1]
            identifier_authority = int.from_bytes(
                sid_bytes[2:8], byteorder='big'
            )

            sid = f"S-{revision}-{identifier_authority}"

            for i in range(sub_authority_count):
                offset = 8 + (i * 4)
                sub_authority = int.from_bytes(
                    sid_bytes[offset:offset + 4], byteorder='little'
                )
                sid += f"-{sub_authority}"

            return sid
        except Exception:
            return str(sid_bytes)

    @staticmethod
    def _guid_to_string(guid_bytes: bytes) -> str:
        """Convert binary objectGUID to UUID string (xxxxxxxx-xxxx-...)."""
        try:
            if len(guid_bytes) != 16:
                return str(guid_bytes)

            parts = [
                guid_bytes[3::-1].hex(),      # First 4 bytes reversed
                guid_bytes[5:3:-1].hex(),      # Next 2 bytes reversed
                guid_bytes[7:5:-1].hex(),      # Next 2 bytes reversed
                guid_bytes[8:10].hex(),        # Next 2 bytes as-is
                guid_bytes[10:16].hex(),       # Last 6 bytes as-is
            ]
            return '-'.join(parts)
        except Exception:
            return str(guid_bytes)

    @staticmethod
    def _filetime_to_datetime(filetime: int) -> Optional[str]:
        """Convert Windows FILETIME (100-ns intervals since 1601-01-01) to ISO 8601."""
        if not filetime or filetime == 0:
            return None

        # Max int64 or "never expires"
        if filetime >= 9223372036854775807:
            return None

        try:
            # 116444736000000000 = difference between 1601 and 1970 in 100-ns ticks
            unix_timestamp = (filetime - 116444736000000000) / 10000000
            dt = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
            return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, OSError, OverflowError):
            return None

    # ------------------------------------------------------------------
    # Field derivation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_enabled(uac_value: Any) -> bool:
        """Derive enabled status from userAccountControl bitmask."""
        try:
            if isinstance(uac_value, str):
                uac = int(uac_value) if uac_value.isdigit() else 0
            elif isinstance(uac_value, int):
                uac = uac_value
            else:
                uac = 0
            # Bit 0x0002 = ACCOUNTDISABLE
            return not bool(uac & 0x0002)
        except (ValueError, TypeError):
            return True  # default to enabled if we cannot parse

    @staticmethod
    def _derive_domain(dn: str) -> str:
        """Extract domain name from distinguished name (DC= components)."""
        try:
            parts = dn.split(',')
            dc_parts = [
                p.split('=')[1]
                for p in parts
                if p.strip().upper().startswith('DC=')
            ]
            return '.'.join(dc_parts) if dc_parts else ''
        except (IndexError, ValueError):
            return ''

    @staticmethod
    def _derive_ou_path(dn: str) -> str:
        """Extract OU path from distinguished name."""
        try:
            parts = dn.split(',')
            ou_parts = [
                p.split('=')[1]
                for p in parts
                if p.strip().upper().startswith('OU=')
            ]
            return '/'.join(ou_parts) if ou_parts else ''
        except (IndexError, ValueError):
            return ''

    @staticmethod
    def _derive_search_base(domain: str) -> str:
        """Derive search base DN from domain name (e.g., corp.com -> DC=corp,DC=com)."""
        return ','.join(f'DC={dc}' for dc in domain.split('.'))

    # ------------------------------------------------------------------
    # Connection factory
    # ------------------------------------------------------------------

    def _create_connection(self) -> 'Connection':
        """Create and bind a new LDAP connection."""
        # TLS configuration
        tls_config = None
        if self.use_ssl or self.port in (636, 3269):
            validate = ssl.CERT_REQUIRED if self.verify_cert else ssl.CERT_NONE
            tls_config = Tls(validate=validate)

        server = Server(
            self.server_address,
            port=self.port,
            use_ssl=self.use_ssl,
            get_info=ALL,
            tls=tls_config,
            connect_timeout=self.connect_timeout,
        )

        # Authentication
        if self.auth_method.upper() == 'NTLM':
            auth = NTLM
        else:
            auth = SIMPLE

        conn = Connection(
            server,
            user=self.bind_dn,
            password=self.bind_password,
            authentication=auth,
            auto_bind=True,
        )

        return conn

    def _resolve_default_port(self) -> int:
        """Determine default port based on SSL and Global Catalog settings."""
        if self.use_global_catalog:
            return 3269 if self.use_ssl else 3268
        return 636 if self.use_ssl else 389

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        fields = [
            {'name': 'cn', 'path': 'cn', 'type': 'string', 'description': 'Common name', 'required': True},
            {'name': 'name', 'path': 'name', 'type': 'string', 'description': 'Computer name'},
            {'name': 'distinguishedName', 'path': 'distinguishedName', 'type': 'string', 'description': 'Full distinguished name'},
            {'name': 'dNSHostName', 'path': 'dNSHostName', 'type': 'string', 'description': 'DNS hostname (FQDN)'},
            {'name': 'sAMAccountName', 'path': 'sAMAccountName', 'type': 'string', 'description': 'SAM account name'},
            {'name': 'operatingSystem', 'path': 'operatingSystem', 'type': 'string', 'description': 'Operating system'},
            {'name': 'operatingSystemVersion', 'path': 'operatingSystemVersion', 'type': 'string', 'description': 'OS version'},
            {'name': 'objectGUID', 'path': 'objectGUID', 'type': 'string', 'description': 'Object GUID (UUID format)'},
            {'name': 'objectSid', 'path': 'objectSid', 'type': 'string', 'description': 'Object SID (S-1-5-... format)'},
            {'name': 'userAccountControl', 'path': 'userAccountControl', 'type': 'number', 'description': 'Account control flags bitmask'},
            {'name': 'enabled', 'path': 'enabled', 'type': 'boolean', 'description': 'Whether the account is enabled (derived from UAC)'},
            {'name': 'description', 'path': 'description', 'type': 'string', 'description': 'Computer description'},
            {'name': 'location', 'path': 'location', 'type': 'string', 'description': 'Physical location'},
            {'name': 'managedBy', 'path': 'managedBy', 'type': 'string', 'description': 'Managed by (DN)'},
            {'name': 'memberOf', 'path': 'memberOf', 'type': 'string', 'description': 'Group memberships (semicolon-separated DNs)'},
            {'name': 'whenCreated', 'path': 'whenCreated', 'type': 'datetime', 'description': 'Object creation timestamp'},
            {'name': 'lastLogonTimestamp', 'path': 'lastLogonTimestamp', 'type': 'datetime', 'description': 'Last logon timestamp (replicated)'},
            {'name': 'pwdLastSet', 'path': 'pwdLastSet', 'type': 'datetime', 'description': 'Password last set timestamp'},
            {'name': 'accountExpires', 'path': 'accountExpires', 'type': 'datetime', 'description': 'Account expiration timestamp'},
            {'name': 'primaryGroupID', 'path': 'primaryGroupID', 'type': 'number', 'description': 'Primary group RID'},
            {'name': 'sAMAccountType', 'path': 'sAMAccountType', 'type': 'number', 'description': 'SAM account type code'},
            {'name': 'countryCode', 'path': 'countryCode', 'type': 'number', 'description': 'Country code'},
            {'name': 'domain', 'path': 'domain', 'type': 'string', 'description': 'Domain name (derived from DN)'},
            {'name': 'ou_path', 'path': 'ou_path', 'type': 'string', 'description': 'OU path (derived from DN)'},
        ]
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
