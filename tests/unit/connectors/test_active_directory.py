# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for ActiveDirectoryConnector.

Inherits the standard ConnectorTestCase contract and adds connector-specific
tests for LDAP paged search, record normalization, binary attribute conversion,
field derivation, and deduplication.

ldap3 may not be installed in the test environment. All tests mock the ldap3
Connection so they run without a real LDAP server or ldap3 import.
"""

import logging
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from tests.unit.connectors.conftest import ConnectorTestCase
from src.modules.integrations.base import LDAPAuth

# Skip the entire module if the connector itself cannot be imported
# (e.g., if ldap3 is absent and the import fails for some other reason).
try:
    from src.modules.integrations.connectors.active_directory_connector import (
        ActiveDirectoryConnector,
        COMPUTER_ATTRIBUTES,
        COMPUTER_ATTRIBUTES_CORE,
        MAX_PAGINATION_PAGES,
        PAGED_RESULTS_OID,
    )
except ImportError as _import_err:
    pytest.skip(
        f"active_directory_connector could not be imported: {_import_err}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Shared test config
# ---------------------------------------------------------------------------

BASE_CONFIG = {
    'server': 'dc01.corp.com',
    'bind_dn': 'CN=svc_ldap,OU=Service Accounts,DC=corp,DC=com',
    'bind_password': 'secret',
    'search_base': 'DC=corp,DC=com',
    'use_ssl': False,
    'verify_cert': False,
    'page_size': 100,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(attributes: dict) -> MagicMock:
    """Build a mock ldap3 entry with the given attribute dict.

    ldap3 entries support attribute access via entry[attr_name].value.
    MagicMock.__getitem__ returns a new mock each call, so we use a
    side_effect dict to return stable per-attribute mocks.
    """
    entry = MagicMock()
    entry.entry_attributes = list(attributes.keys())

    attr_mocks: dict = {}
    for attr, value in attributes.items():
        attr_mock = MagicMock()
        attr_mock.value = value
        attr_mocks[attr] = attr_mock

    entry.__getitem__ = lambda self, key: attr_mocks[key]
    return entry


def _make_conn(entries=None, cookie=None) -> MagicMock:
    """
    Build a mock ldap3 Connection.

    entries: list of mock entry objects returned by conn.entries
    cookie:  bytes cookie for paged results (None = no more pages)
    """
    conn = MagicMock()
    conn.entries = entries or []
    conn.result = {
        'controls': {
            PAGED_RESULTS_OID: {
                'value': {'cookie': cookie},
            }
        }
    }
    return conn


# ---------------------------------------------------------------------------
# ConnectorTestCase subclass
# ---------------------------------------------------------------------------

class TestActiveDirectoryConnector(ConnectorTestCase):
    connector_class = ActiveDirectoryConnector
    mock_config = BASE_CONFIG

    # ------------------------------------------------------------------
    # Override connector fixture — patch _create_connection so all
    # ConnectorTestCase tests work without a real LDAP server.
    # ------------------------------------------------------------------

    @pytest.fixture
    def connector(self):
        entry = _make_entry({
            'cn': 'DESKTOP-WIN001',
            'name': 'DESKTOP-WIN001',
            'distinguishedName': 'CN=DESKTOP-WIN001,OU=Workstations,DC=corp,DC=com',
            'dNSHostName': 'DESKTOP-WIN001.corp.com',
            'sAMAccountName': 'DESKTOP-WIN001$',
            'operatingSystem': 'Windows 11 Pro',
            'userAccountControl': 4096,
        })
        conn = _make_conn(entries=[entry], cookie=None)

        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            yield ActiveDirectoryConnector(self.mock_config)

    # ------------------------------------------------------------------
    # Override base tests that use mock_http (N/A for LDAP)
    # ------------------------------------------------------------------

    def test_connection_success(self, connector):
        """test_connection returns success dict with expected keys."""
        result = connector.test_connection()
        assert isinstance(result, dict)
        assert result['success'] is True
        assert 'message' in result

    def test_fetch_yields_records(self, connector):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0

    def test_fetch_records_are_flat_dicts(self, connector):
        records = list(connector.fetch_data("test-batch"))
        for record in records:
            assert isinstance(record, dict)
            for key in record:
                assert isinstance(key, str)

    def test_fetch_metadata_injected(self, connector):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0
        record = records[0]
        assert record["_batch_id"] == "test-batch"
        assert "_collected_at" in record
        assert "_source" in record

    # ------------------------------------------------------------------
    # Connector-specific: auth strategy
    # ------------------------------------------------------------------

    def test_auth_is_ldap(self):
        """_build_auth() must return an LDAPAuth instance."""
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(self.mock_config)
        assert isinstance(connector.auth, LDAPAuth)

    # ------------------------------------------------------------------
    # Connector-specific: validation
    # ------------------------------------------------------------------

    def test_missing_server_raises(self):
        config = {**BASE_CONFIG, 'server': ''}
        with pytest.raises(ValueError, match="server address"):
            ActiveDirectoryConnector(config)

    def test_missing_bind_dn_raises(self):
        config = {**BASE_CONFIG, 'bind_dn': ''}
        with pytest.raises(ValueError, match="Bind DN"):
            ActiveDirectoryConnector(config)

    def test_missing_bind_password_raises(self):
        config = {**BASE_CONFIG, 'bind_password': ''}
        with pytest.raises(ValueError, match="[Bb]ind password"):
            ActiveDirectoryConnector(config)

    def test_missing_search_base_no_domain_raises(self):
        config = {**BASE_CONFIG, 'search_base': '', 'domain': ''}
        with pytest.raises(ValueError, match="[Ss]earch base"):
            ActiveDirectoryConnector(config)

    def test_search_base_derived_from_domain(self):
        config = {**BASE_CONFIG, 'search_base': '', 'domain': 'corp.com'}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector.search_base == 'DC=corp,DC=com'

    # ------------------------------------------------------------------
    # Connector-specific: port resolution
    # ------------------------------------------------------------------

    def test_default_port_ssl(self):
        config = {**BASE_CONFIG, 'use_ssl': True}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector.port == 636

    def test_default_port_no_ssl(self):
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
        assert connector.port == 389

    def test_default_port_global_catalog_ssl(self):
        config = {**BASE_CONFIG, 'use_ssl': True, 'use_global_catalog': True, 'search_all_domains': True}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector.port == 3269

    def test_default_port_global_catalog_no_ssl(self):
        config = {**BASE_CONFIG, 'use_ssl': False, 'use_global_catalog': True, 'search_all_domains': True}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector.port == 3268

    def test_explicit_port_overrides_default(self):
        config = {**BASE_CONFIG, 'port': 3268}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector.port == 3268

    # ------------------------------------------------------------------
    # Connector-specific: paged search — pagination
    # ------------------------------------------------------------------

    def test_paged_search_follows_cookie(self):
        """Records from two LDAP pages are both yielded."""
        entry1 = _make_entry({'cn': 'PC-001', 'distinguishedName': 'CN=PC-001,DC=corp,DC=com'})
        entry2 = _make_entry({'cn': 'PC-002', 'distinguishedName': 'CN=PC-002,DC=corp,DC=com'})

        cookie_value = b'page2cookie'

        # conn.search is called twice; entries differ per call
        conn = MagicMock()
        call_count = [0]

        def search_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                conn.entries = [entry1]
                conn.result = {
                    'controls': {
                        PAGED_RESULTS_OID: {'value': {'cookie': cookie_value}}
                    }
                }
            else:
                conn.entries = [entry2]
                conn.result = {
                    'controls': {
                        PAGED_RESULTS_OID: {'value': {'cookie': None}}
                    }
                }

        conn.search.side_effect = search_side_effect

        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
            records = list(connector._collect_records('test-batch'))

        assert len(records) == 2
        cns = {r['cn'] for r in records}
        assert 'PC-001' in cns
        assert 'PC-002' in cns

    def test_paged_search_no_cookie_single_page(self):
        """When no cookie is returned, only one page is fetched."""
        entry = _make_entry({'cn': 'PC-001', 'distinguishedName': 'CN=PC-001,DC=corp,DC=com'})
        conn = _make_conn(entries=[entry], cookie=None)

        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
            records = list(connector._collect_records('test-batch'))

        assert len(records) == 1
        # conn.search called exactly once
        assert conn.search.call_count == 1

    # ------------------------------------------------------------------
    # Connector-specific: deduplication
    # ------------------------------------------------------------------

    def test_deduplication_removes_duplicate_cn_domain(self):
        """Duplicate (cn, domain) entries are de-duplicated."""
        dn = 'CN=PC-001,OU=Workstations,DC=corp,DC=com'
        entry1 = _make_entry({'cn': 'PC-001', 'distinguishedName': dn})
        entry2 = _make_entry({'cn': 'PC-001', 'distinguishedName': dn})

        conn = _make_conn(entries=[entry1, entry2], cookie=None)

        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
            records = list(connector._collect_records('test-batch'))

        assert len(records) == 1

    # ------------------------------------------------------------------
    # Connector-specific: record normalization
    # ------------------------------------------------------------------

    def test_process_entry_enabled_true_for_normal_uac(self):
        """UAC value without ACCOUNTDISABLE bit yields enabled=True."""
        connector = ActiveDirectoryConnector.__new__(ActiveDirectoryConnector)
        raw = {'userAccountControl': 4096, 'distinguishedName': ''}
        record = connector._process_entry(raw)
        assert record['enabled'] is True

    def test_process_entry_enabled_false_for_disabled_uac(self):
        """UAC with ACCOUNTDISABLE bit (0x0002) yields enabled=False."""
        connector = ActiveDirectoryConnector.__new__(ActiveDirectoryConnector)
        raw = {'userAccountControl': 4098, 'distinguishedName': ''}  # 4096 | 0x0002
        record = connector._process_entry(raw)
        assert record['enabled'] is False

    def test_process_entry_memberof_joined_with_semicolon(self):
        """Multi-valued memberOf list is joined with semicolons."""
        connector = ActiveDirectoryConnector.__new__(ActiveDirectoryConnector)
        raw = {
            'memberOf': ['CN=Group1,DC=corp,DC=com', 'CN=Group2,DC=corp,DC=com'],
            'distinguishedName': '',
        }
        record = connector._process_entry(raw)
        assert record['memberOf'] == 'CN=Group1,DC=corp,DC=com;CN=Group2,DC=corp,DC=com'

    def test_process_entry_derives_domain_from_dn(self):
        """Domain is extracted from distinguishedName DC= components."""
        connector = ActiveDirectoryConnector.__new__(ActiveDirectoryConnector)
        raw = {'distinguishedName': 'CN=PC-001,OU=Workstations,DC=corp,DC=com'}
        record = connector._process_entry(raw)
        assert record['domain'] == 'corp.com'

    def test_process_entry_derives_ou_path_from_dn(self):
        """OU path is extracted from distinguishedName OU= components."""
        connector = ActiveDirectoryConnector.__new__(ActiveDirectoryConnector)
        raw = {'distinguishedName': 'CN=PC-001,OU=Workstations,OU=Computers,DC=corp,DC=com'}
        record = connector._process_entry(raw)
        assert record['ou_path'] == 'Workstations/Computers'

    # ------------------------------------------------------------------
    # Connector-specific: binary attribute converters
    # ------------------------------------------------------------------

    def test_sid_to_string(self):
        """Binary SID is converted to S-1-5-... string."""
        # Minimal valid SID: revision=1, sub_count=1, authority=5, sub=500
        sid_bytes = (
            b'\x01'              # revision
            b'\x01'              # sub_authority_count
            b'\x00\x00\x00\x00\x00\x05'  # identifier_authority = 5
            b'\xf4\x01\x00\x00'  # sub_authority[0] = 500 (LE)
        )
        result = ActiveDirectoryConnector._sid_to_string(sid_bytes)
        assert result == 'S-1-5-500'

    def test_guid_to_string(self):
        """16-byte binary GUID is converted to UUID-style string."""
        guid_bytes = bytes(range(16))  # 00010203-...-0f
        result = ActiveDirectoryConnector._guid_to_string(guid_bytes)
        assert len(result) == 36
        assert result.count('-') == 4

    def test_filetime_to_datetime_zero_returns_none(self):
        assert ActiveDirectoryConnector._filetime_to_datetime(0) is None

    def test_filetime_to_datetime_max_returns_none(self):
        assert ActiveDirectoryConnector._filetime_to_datetime(9223372036854775807) is None

    def test_filetime_to_datetime_valid(self):
        # 132000000000000000 = 2019-01-01T00:00:00Z (approx)
        result = ActiveDirectoryConnector._filetime_to_datetime(132000000000000000)
        assert result is not None
        assert 'T' in result
        assert result.endswith('Z')

    # ------------------------------------------------------------------
    # Connector-specific: search base logic
    # ------------------------------------------------------------------

    def test_get_search_bases_default(self):
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
        assert connector._get_search_bases() == ['DC=corp,DC=com']

    def test_get_search_bases_global_catalog_forest_wide(self):
        config = {**BASE_CONFIG, 'use_global_catalog': True, 'search_all_domains': True}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        assert connector._get_search_bases() == ['']

    def test_get_search_bases_additional_base_dns(self):
        # additional_base_dns is split on ',' — since LDAP DNs contain commas,
        # each "additional base" must be a single token with no internal commas
        # (i.e., a top-level DC= component). This reflects the existing behavior.
        config = {
            **BASE_CONFIG,
            'search_all_domains': True,
            'use_global_catalog': False,
            'additional_base_dns': 'DC=emea, DC=apac',
        }
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(config)
        bases = connector._get_search_bases()
        assert 'DC=corp,DC=com' in bases
        assert 'DC=emea' in bases
        assert 'DC=apac' in bases

    # ------------------------------------------------------------------
    # Connector-specific: metadata source field
    # ------------------------------------------------------------------

    def test_metadata_source_is_active_directory(self, connector):
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "active_directory"

    # ------------------------------------------------------------------
    # Connector-specific: config schema
    # ------------------------------------------------------------------

    def test_config_schema_has_required_fields(self):
        schema = ActiveDirectoryConnector.get_config_schema()
        assert 'server' in schema
        assert 'bind_dn' in schema
        assert 'bind_password' in schema
        assert schema['server']['required'] is True
        assert schema['bind_dn']['required'] is True
        assert schema['bind_password']['required'] is True

    def test_config_schema_bind_password_is_sensitive(self):
        schema = ActiveDirectoryConnector.get_config_schema()
        assert schema['bind_password']['type'] == 'password'
        assert schema['bind_password'].get('sensitive') is True


# ---------------------------------------------------------------------------
# Pagination cap-hit warnings (#1334)
# ---------------------------------------------------------------------------

class TestActiveDirectoryTruncation:
    """The LDAP paged-search cap must reach the recorded collection
    warnings, not just the log — otherwise a search base larger than
    cap × page_size silently under-reports, and the pipeline rebuild reads
    the shortfall as deleted computers."""

    def test_cap_hit_records_one_warning_naming_the_search_base(self, caplog):
        entry = _make_entry({'cn': 'PC-001', 'distinguishedName': 'CN=PC-001,DC=corp,DC=com'})
        # The cookie never clears — the directory always has another page.
        conn = _make_conn(entries=[entry], cookie=b'page-cookie')

        config = {**BASE_CONFIG, 'max_pagination_pages': 1}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(config)
            with caplog.at_level(logging.WARNING):
                records = list(connector.fetch_data('test-batch'))

        assert len(records) == 1
        assert len(connector.collection_warnings) == 1
        message = connector.collection_warnings[0]
        assert 'DC=corp,DC=com' in message
        assert '1-page cap' in message
        assert 'truncat' in message.lower()
        assert message in caplog.text
        # The old unrecorded log line is gone.
        assert 'Reached maximum page count' not in caplog.text

    def test_cap_hit_after_following_cookies(self, caplog):
        """The loop paginates genuinely, then caps: two full pages fetched,
        cookie still set on the second."""
        entry1 = _make_entry({'cn': 'PC-001', 'distinguishedName': 'CN=PC-001,DC=corp,DC=com'})
        entry2 = _make_entry({'cn': 'PC-002', 'distinguishedName': 'CN=PC-002,DC=corp,DC=com'})

        conn = MagicMock()
        call_count = [0]

        def search_side_effect(**kwargs):
            call_count[0] += 1
            conn.entries = [entry1] if call_count[0] == 1 else [entry2]
            conn.result = {
                'controls': {
                    PAGED_RESULTS_OID: {'value': {'cookie': b'always-more'}}
                }
            }

        conn.search.side_effect = search_side_effect

        config = {**BASE_CONFIG, 'max_pagination_pages': 2}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(config)
            with caplog.at_level(logging.WARNING):
                records = list(connector.fetch_data('test-batch'))

        assert len(records) == 2
        assert conn.search.call_count == 2
        assert len(connector.collection_warnings) == 1
        message = connector.collection_warnings[0]
        assert 'DC=corp,DC=com' in message
        assert '2-page cap' in message

    def test_end_of_data_records_no_warning(self, caplog):
        """The off-by-one: the page that fills the cap returns no cookie —
        the data, not the cap, ended the search."""
        entry = _make_entry({'cn': 'PC-001', 'distinguishedName': 'CN=PC-001,DC=corp,DC=com'})
        conn = _make_conn(entries=[entry], cookie=None)

        config = {**BASE_CONFIG, 'max_pagination_pages': 1}
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=conn):
            connector = ActiveDirectoryConnector(config)
            with caplog.at_level(logging.WARNING):
                records = list(connector.fetch_data('test-batch'))

        assert len(records) == 1
        assert connector.collection_warnings == []

    def test_max_pagination_pages_configurable(self):
        with patch.object(ActiveDirectoryConnector, '_create_connection', return_value=_make_conn()):
            connector = ActiveDirectoryConnector(BASE_CONFIG)
            assert connector.max_pagination_pages == MAX_PAGINATION_PAGES
            connector = ActiveDirectoryConnector(
                {**BASE_CONFIG, 'max_pagination_pages': 50}
            )
            assert connector.max_pagination_pages == 50

    def test_config_schema_exposes_max_pagination_pages(self):
        schema = ActiveDirectoryConnector.get_config_schema()
        field = schema['max_pagination_pages']
        assert field['type'] == 'number'
        assert field['default'] == MAX_PAGINATION_PAGES
