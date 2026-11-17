# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for GET /api/v1/assets (external query API)."""

import pytest
from unittest.mock import MagicMock

from src.modules.fields import InvalidFieldError
from src.modules.fields.catalog import CatalogEntry
from src.modules.fields.types import FieldDataType

API_KEY_HEADER = {'X-API-Key': 'aa_testkey'}

VALID_KEY_DATA = {
    'id': 'test-key-id',
    'user_id': 'test-user',
    'username': 'tester',
    'role': 'admin',
    'is_active': True,
    'user_is_active': True,
}


@pytest.fixture(autouse=True)
def _mock_api_key_auth(blueprint_app):
    """Configure api_key_service mock so @require_api_key passes."""
    blueprint_app.api_key_service.validate_key.return_value = VALID_KEY_DATA
    # Clear rate limit counters between tests
    from src.auth.api_key_auth import _rate_counters
    _rate_counters.clear()


class TestExternalAssetsQuery:
    def test_query_by_ip(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = (
            [{'id': 'a1', 'hostname': ['web01'], 'ip': ['10.1.2.3']}], 1
        )
        resp = client.get('/api/v1/assets?ip=10.1.2.3', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['count'] == 1

    def test_query_by_hostname(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = ([], 0)
        resp = client.get('/api/v1/assets?hostname=web01', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['data']['count'] == 0

    def test_query_by_free_text(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = ([], 0)
        resp = client.get('/api/v1/assets?q=web01', headers=API_KEY_HEADER)
        assert resp.status_code == 200

    def test_missing_filter_returns_400(self, client):
        resp = client.get('/api/v1/assets', headers=API_KEY_HEADER)
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'filter' in body['message'].lower()

    def test_q_with_specific_filter_returns_400(self, client):
        resp = client.get('/api/v1/assets?q=web&ip=10.1.2.3', headers=API_KEY_HEADER)
        assert resp.status_code == 400

    def test_custom_fields(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = (
            [{'id': 'a1', 'hostname': ['web01'], 'os_normalized': 'Windows'}], 1
        )
        resp = client.get(
            '/api/v1/assets?ip=10.1.2.3&fields=id,hostname,os_normalized',
            headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200

    def test_limit_parameter(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = ([], 0)
        resp = client.get('/api/v1/assets?ip=10.1.2.3&limit=50', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.query_assets.call_args[1]
        assert call_kwargs['limit'] == 50

    def test_limit_capped_at_100(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = ([], 0)
        resp = client.get('/api/v1/assets?ip=10.1.2.3&limit=500', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.query_assets.call_args[1]
        assert call_kwargs['limit'] == 100


def _dynamic_result(assets=None, total=1, page=1, limit=50, pages=1):
    """Shape returned by asset_service.list_assets_dynamic (the same method the
    UI's GET /api/assets uses): top-level success/assets/pagination, asset rows
    keyed by canonical percent-encoded FieldId strings plus ``id``."""
    if assets is None:
        assets = [{
            'id': 'a1',
            'static:Hostname': ['web01'],
            'static:OS%20EOL%20Date': '2025-01-01',
            'static:Confidence%20Score': 0.91,
        }]
    return {
        'success': True,
        'assets': assets,
        'pagination': {'total': total, 'page': page, 'limit': limit, 'pages': pages},
    }


class TestInventoryEndpoint:
    """GET /api/v1/assets/inventory — full paginated inventory for the risk-app
    connector. Must return the IDENTICAL shape as GET /api/assets."""

    def test_requires_api_key_header(self, client):
        resp = client.get('/api/v1/assets/inventory?fields=static:Hostname')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'missing_api_key'

    def test_returns_top_level_assets_and_pagination_pages(self, client, blueprint_app):
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result(pages=4)
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname,static:Category',
            headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        # Contract: identical to GET /api/assets — assets/pagination are
        # TOP-LEVEL, NOT nested under a `data` envelope.
        assert 'data' not in body
        assert body['pagination']['pages'] == 4
        assert body['assets'][0]['id'] == 'a1'
        assert body['assets'][0]['static:OS%20EOL%20Date'] == '2025-01-01'

    def test_missing_fields_returns_400(self, client):
        resp = client.get('/api/v1/assets/inventory', headers=API_KEY_HEADER)
        assert resp.status_code == 400
        assert 'fields' in resp.get_json()['message'].lower()

    def test_limit_capped_at_500(self, client, blueprint_app):
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname&limit=10000',
            headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200
        assert blueprint_app.asset_service.list_assets_dynamic.call_args[1]['limit'] == 500

    def test_forwards_decoded_fields_and_paging(self, client, blueprint_app):
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname,static:OS%20Normalized&page=3&limit=200',
            headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.list_assets_dynamic.call_args[1]
        # %20 must reach the service decoded, exactly like the JWT GET /api/assets path.
        assert kwargs['fields'] == ['static:Hostname', 'static:OS Normalized']
        assert kwargs['page'] == 3
        assert kwargs['limit'] == 200

    def test_readonly_user_role_key_allowed(self, client, blueprint_app):
        # A read-only viewer service account (role 'user', no admin/write) is
        # sufficient — listing assets has no role gate.
        blueprint_app.api_key_service.validate_key.return_value = {
            'id': 'k', 'user_id': 'u', 'username': 'risk-svc', 'role': 'user',
            'is_active': True, 'user_is_active': True,
        }
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname', headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200

    def test_uses_inventory_rate_limit_budget(self, client, blueprint_app, monkeypatch):
        # The inventory path has its own (configurable) budget; exceeding it
        # yields 429 + Retry-After so the connector can back off.
        monkeypatch.setitem(blueprint_app.config, 'EXTERNAL_API_INVENTORY_RATE_LIMIT', 2)
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        for _ in range(2):
            assert client.get(
                '/api/v1/assets/inventory?fields=static:Hostname', headers=API_KEY_HEADER,
            ).status_code == 200
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname', headers=API_KEY_HEADER,
        )
        assert resp.status_code == 429
        assert resp.headers.get('Retry-After')

    def test_unknown_field_returns_400_not_500(self, client, blueprint_app):
        # A well-formed but unknown FieldId makes the resolver raise
        # InvalidFieldError. The endpoint must translate that to a 400 (client
        # error) like the JWT GET /api/assets path — not let it fall through to
        # a generic 500 (InvalidFieldError is not a ValueError).
        blueprint_app.asset_service.list_assets_dynamic.side_effect = InvalidFieldError(
            'static:Nope is not a known field'
        )
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Nope', headers=API_KEY_HEADER,
        )
        assert resp.status_code == 400
        assert 'nope' in resp.get_json()['message'].lower()

    def test_page_floored_to_1(self, client, blueprint_app):
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname&page=0', headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200
        assert blueprint_app.asset_service.list_assets_dynamic.call_args[1]['page'] == 1

    def test_default_limit_is_50(self, client, blueprint_app):
        blueprint_app.asset_service.list_assets_dynamic.return_value = _dynamic_result()
        resp = client.get(
            '/api/v1/assets/inventory?fields=static:Hostname', headers=API_KEY_HEADER,
        )
        assert resp.status_code == 200
        assert blueprint_app.asset_service.list_assets_dynamic.call_args[1]['limit'] == 50


def _field_catalog_entries():
    """One entry per servable kind, plus a filter_only entry that must NEVER
    be served. Display names carry spaces and mixed casing so the
    plain-vs-encoded contract is exercised (the live incident: ext:beszel:cpu
    400s while ext:Beszel:cpu exists)."""
    return [
        CatalogEntry(
            kind_name='core_enrichment', display_name='Geo City',
            data_type=FieldDataType.JSONB_ARRAY, operators=(),
            core_path='AssetAtlas.geo_cities', is_array=True,
        ),
        CatalogEntry(
            kind_name='core_enrichment', display_name='IP Type',
            data_type=FieldDataType.STRING, operators=(),
            core_path='AssetAtlas.ip_type',
        ),
        CatalogEntry(
            kind_name='extended_attribute', display_name='Beszel - cpu',
            data_type=FieldDataType.STRING, operators=(),
            source='Beszel', attribute='cpu',
        ),
        CatalogEntry(
            kind_name='extended_attribute', display_name='My Source - cpu load',
            data_type=FieldDataType.STRING, operators=(),
            source='My Source', attribute='cpu load',
        ),
        CatalogEntry(
            kind_name='integration_group', display_name='Hypervisors - cpu',
            data_type=FieldDataType.STRING, operators=(),
            group='Hypervisors', attribute='cpu',
            member_sources=('Beszel', 'Proxmox'), is_grouped=True,
        ),
        CatalogEntry(
            kind_name='posture', display_name='Bitlocker',
            data_type=FieldDataType.STRING, operators=(),
            posture_category='bitlocker', posture_read='state',
        ),
        CatalogEntry(
            kind_name='posture', display_name='Bitlocker (Output)',
            data_type=FieldDataType.STRING, operators=(),
            posture_category='bitlocker', posture_read='output',
        ),
        CatalogEntry(
            kind_name='static', display_name='Hostname',
            data_type=FieldDataType.STRING_ARRAY, operators=(),
            static_db_column='hostname', is_array=True,
        ),
        CatalogEntry(
            kind_name='static', display_name='OS Normalized',
            data_type=FieldDataType.STRING, operators=(),
            static_db_column='os_normalized',
        ),
        CatalogEntry(
            kind_name='tag', display_name='Tags - vip',
            data_type=FieldDataType.BOOLEAN, operators=(),
            tag_name='vip',
        ),
        CatalogEntry(
            kind_name='virtual', display_name='Asset Label',
            data_type=FieldDataType.STRING, operators=(),
            virtual_name='asset_label',
        ),
        CatalogEntry(
            kind_name='virtual', display_name='Alert',
            data_type=FieldDataType.STRING, operators=(),
            allowed_operator_codes=('eq', 'any'),
            virtual_name='alert', filter_only=True,
        ),
    ]


def _seed_field_catalog(blueprint_app, entries=None):
    fake_catalog = MagicMock()
    fake_catalog.entries.return_value = (
        entries if entries is not None else _field_catalog_entries()
    )
    blueprint_app.field_resolver.catalog.return_value = fake_catalog


class TestExternalFieldsCatalog:
    """GET /api/v1/fields — field-catalog enumeration for machine consumers."""

    # The pre-search keys; ``operators`` (for GET /api/v1/assets/search) is
    # the only addition.
    ORIGINAL_ENTRY_KEYS = {
        'field_id', 'field_id_encoded', 'display_name',
        'kind', 'data_type', 'is_array', 'member_sources',
    }
    PINNED_ENTRY_KEYS = ORIGINAL_ENTRY_KEYS | {'operators'}

    def test_requires_api_key_header(self, client):
        resp = client.get('/api/v1/fields')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'missing_api_key'

    def test_invalid_api_key_returns_401(self, client, blueprint_app):
        blueprint_app.api_key_service.validate_key.return_value = None
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_api_key'

    def test_readonly_user_role_key_allowed(self, client, blueprint_app):
        # Same as the inventory route: a read-only viewer service account
        # (role 'user') is sufficient — catalog enumeration has no role gate.
        blueprint_app.api_key_service.validate_key.return_value = {
            'id': 'k', 'user_id': 'u', 'username': 'reporting-svc', 'role': 'user',
            'is_active': True, 'user_is_active': True,
        }
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        assert resp.status_code == 200

    def test_returns_top_level_envelope_with_exact_entry_keys(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        # Top-level count/fields, NOT nested under a `data` envelope (same
        # shape convention as the inventory route).
        assert 'data' not in body
        assert body['count'] == len(body['fields']) == 11
        for entry in body['fields']:
            assert set(entry) == self.PINNED_ENTRY_KEYS

    def test_sorted_by_kind_then_display_name(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        keys = [(e['kind'], e['display_name']) for e in resp.get_json()['fields']]
        assert keys == sorted(keys)
        # Full expected order across the kind boundary (posture_output sorts
        # before posture_state alphabetically).
        assert keys == [
            ('core', 'Geo City'),
            ('core', 'IP Type'),
            ('ext', 'Beszel - cpu'),
            ('ext', 'My Source - cpu load'),
            ('group', 'Hypervisors - cpu'),
            ('posture_output', 'Bitlocker (Output)'),
            ('posture_state', 'Bitlocker'),
            ('static', 'Hostname'),
            ('static', 'OS Normalized'),
            ('tag', 'Tags - vip'),
            ('virtual', 'Asset Label'),
        ]

    def test_filter_only_entries_never_served(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        served = resp.get_json()['fields']
        assert 'Alert' not in [e['display_name'] for e in served]
        # Not even when its kind is explicitly requested.
        resp = client.get('/api/v1/fields?kind=virtual', headers=API_KEY_HEADER)
        served = resp.get_json()['fields']
        assert [e['display_name'] for e in served] == ['Asset Label']

    def test_plain_and_encoded_ids_differ_only_by_percent_encoding(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        by_encoded = {e['field_id_encoded']: e for e in resp.get_json()['fields']}
        # Spaced static segment.
        assert by_encoded['static:OS%20Normalized']['field_id'] == 'static:OS Normalized'
        # Spaced ext source AND attribute segments; casing preserved in both forms.
        assert by_encoded['ext:My%20Source:cpu%20load']['field_id'] == 'ext:My Source:cpu load'
        # No special characters: the two forms coincide.
        assert by_encoded['ext:Beszel:cpu']['field_id'] == 'ext:Beszel:cpu'

    def test_kind_prefix_matches_field_id_prefix(self, client, blueprint_app):
        # kind IS the FieldId prefix — posture entries split into
        # posture_state / posture_output accordingly.
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        served = resp.get_json()['fields']
        for entry in served:
            assert entry['kind'] == entry['field_id'].split(':', 1)[0]
            assert entry['kind'] in {
                'static', 'core', 'ext', 'group', 'tag',
                'posture_state', 'posture_output', 'virtual',
            }
        assert {e['kind'] for e in served} == {
            'static', 'core', 'ext', 'group', 'tag',
            'posture_state', 'posture_output', 'virtual',
        }

    def test_group_entry_carries_member_sources(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        served = resp.get_json()['fields']
        group = next(e for e in served if e['kind'] == 'group')
        assert group['member_sources'] == ['Beszel', 'Proxmox']
        for entry in served:
            if entry['kind'] != 'group':
                assert entry['member_sources'] is None

    def test_kind_filter_single(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?kind=ext', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['count'] == 2
        assert {e['kind'] for e in body['fields']} == {'ext'}

    def test_kind_filter_csv(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?kind=ext,static', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['count'] == 4
        assert {e['kind'] for e in body['fields']} == {'ext', 'static'}

    def test_unknown_kind_returns_400_naming_valid_set(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?kind=bogus', headers=API_KEY_HEADER)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'validation_error'
        assert 'bogus' in body['message']
        for valid in ('static', 'core', 'ext', 'group', 'tag',
                      'posture_state', 'posture_output', 'virtual'):
            assert valid in body['message']

    def test_empty_kind_param_is_no_filter(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?kind=', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        assert resp.get_json()['count'] == 11

    def test_uses_default_external_rate_limit_budget(self, client, blueprint_app, monkeypatch):
        # Bare @require_api_key: the route rides the default
        # EXTERNAL_API_RATE_LIMIT budget (unlike the inventory route's own).
        monkeypatch.setitem(blueprint_app.config, 'EXTERNAL_API_RATE_LIMIT', 2)
        _seed_field_catalog(blueprint_app)
        for _ in range(2):
            assert client.get('/api/v1/fields', headers=API_KEY_HEADER).status_code == 200
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        assert resp.status_code == 429
        assert resp.headers.get('Retry-After')

    def test_existing_keys_keep_their_values_operators_is_additive(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?kind=static', headers=API_KEY_HEADER)
        os_entry = next(e for e in resp.get_json()['fields']
                        if e['field_id'] == 'static:OS Normalized')
        assert os_entry == {
            'field_id': 'static:OS Normalized',
            'field_id_encoded': 'static:OS%20Normalized',
            'display_name': 'OS Normalized',
            'kind': 'static',
            'data_type': 'string',
            'is_array': False,
            'member_sources': None,
            'operators': [
                'eq', 'neq', 'contains', 'ncontains', 'starts', 'ends',
                'any', 'none', 'empty', 'notempty',
            ],
        }

    def test_entries_carry_their_filter_operators(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields', headers=API_KEY_HEADER)
        by_id = {e['field_id']: e for e in resp.get_json()['fields']}
        # Per data type, in the Explorer dropdown's order.
        assert by_id['static:Hostname']['operators'] == [
            'eq', 'neq', 'contains', 'ncontains', 'starts', 'ends',
            'any', 'all', 'none', 'empty', 'notempty',
        ]
        assert by_id['static:OS Normalized']['operators'] == [
            'eq', 'neq', 'contains', 'ncontains', 'starts', 'ends',
            'any', 'none', 'empty', 'notempty',
        ]
        assert by_id['tag:vip']['operators'] == ['eq', 'empty', 'notempty']

    def test_operators_honour_a_per_field_allow_list(self, client, blueprint_app):
        entries = _field_catalog_entries() + [CatalogEntry(
            kind_name='virtual', display_name='Confidence Level',
            data_type=FieldDataType.STRING, operators=(),
            allowed_operator_codes=('eq', 'any'), virtual_name='confidence_level',
        )]
        _seed_field_catalog(blueprint_app, entries)
        resp = client.get('/api/v1/fields?q=confidence', headers=API_KEY_HEADER)
        (entry,) = resp.get_json()['fields']
        assert entry['operators'] == ['eq', 'any']

    def test_q_filters_display_name_and_field_id_case_insensitively(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        # display_name hit
        resp = client.get('/api/v1/fields?q=CPU', headers=API_KEY_HEADER)
        body = resp.get_json()
        assert resp.status_code == 200
        assert [e['display_name'] for e in body['fields']] == [
            'Beszel - cpu', 'My Source - cpu load', 'Hypervisors - cpu',
        ]
        assert body['count'] == 3
        # field_id hit that the display name lacks ("posture_output:" prefix)
        resp = client.get('/api/v1/fields?q=posture_output', headers=API_KEY_HEADER)
        assert [e['field_id'] for e in resp.get_json()['fields']] == [
            'posture_output:bitlocker',
        ]

    def test_q_combines_with_kind(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?q=cpu&kind=group', headers=API_KEY_HEADER)
        assert [e['field_id'] for e in resp.get_json()['fields']] == ['group:Hypervisors:cpu']

    def test_q_with_no_match_is_an_empty_list(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?q=zzz', headers=API_KEY_HEADER)
        assert resp.status_code == 200
        assert resp.get_json() == {'success': True, 'count': 0, 'fields': []}

    def test_q_over_128_chars_is_400(self, client, blueprint_app):
        _seed_field_catalog(blueprint_app)
        resp = client.get('/api/v1/fields?q=' + 'a' * 129, headers=API_KEY_HEADER)
        assert resp.status_code == 400
        assert '128' in resp.get_json()['message']


# ---------------------------------------------------------------------------
# GET /api/v1/assets/search
# ---------------------------------------------------------------------------
#
# These run the REAL query path — route -> list_assets_query -> AssetRepository
# -> a real FieldResolver over the built-in catalog — against a recording DB
# stand-in. The DB cannot evaluate SQL, so matching behaviour is pinned by
# asserting the generated SQL and bound params (the predicate IS the behaviour),
# alongside the returned body.

import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from urllib.parse import quote  # noqa: E402

from psycopg2 import DataError  # noqa: E402

from src.modules.assets.repositories.asset_repository import _ASSET_SEARCH_EXPR  # noqa: E402
from src.modules.fields import FieldCatalog, FieldResolver  # noqa: E402
from src.mcp.tools._query import _ENCODED_TO_SNAKE  # noqa: E402
from src.api.blueprints.external.search_params import STATIC_ROW_KEYS  # noqa: E402

SEARCH_URL = '/api/v1/assets/search'


class _RecordingDB:
    """Captures every (sql, params) and answers the COUNT and the page query.

    ``rows`` are returned the way psycopg2's RealDictCursor would: keyed ``id``
    plus the canonical encoded FieldId alias the repository selects them as."""

    def __init__(self, rows=None, total=None):
        self.rows = rows or []
        self.total = len(self.rows) if total is None else total
        self.calls = []
        self.raise_on_data = None

    def execute_query(self, sql, params=None, **_kw):
        self.calls.append((sql, dict(params or {})))
        if 'COUNT(*)' in sql:
            return [{'total': self.total}]
        if self.raise_on_data is not None:
            raise self.raise_on_data
        return [dict(r) for r in self.rows]

    @property
    def count_sql(self):
        return next(sql for sql, _ in self.calls if 'COUNT(*)' in sql)

    @property
    def data_call(self):
        return next((sql, p) for sql, p in self.calls if 'COUNT(*)' not in sql)


@pytest.fixture
def search_db(blueprint_app, monkeypatch):
    catalog_db = MagicMock(name='catalog_db')
    # One tag whose own name holds ':' (display 'Tags - env:prod').
    catalog_db.execute_query.side_effect = lambda sql, *_a, **_k: (
        [{'tag_name': 'env:prod'}] if 'asset_manual_tags' in sql else []
    )
    monkeypatch.setattr(
        blueprint_app, 'field_resolver',
        FieldResolver(catalog=FieldCatalog(db_adapter=catalog_db)),
    )
    db = _RecordingDB()
    monkeypatch.setattr(blueprint_app, 'db_adapter', db)
    return db


def _filters(*clauses):
    return quote(json.dumps(list(clauses)))


def _get(client, query):
    return client.get(f'{SEARCH_URL}?{query}', headers=API_KEY_HEADER)


class TestSearchAuthAndBudget:
    def test_missing_key_is_401(self, client, search_db):
        resp = client.get(f'{SEARCH_URL}?search=web')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'missing_api_key'
        assert search_db.calls == []

    def test_bad_key_is_401(self, client, blueprint_app, search_db):
        blueprint_app.api_key_service.validate_key.return_value = None
        resp = _get(client, 'search=web')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_api_key'
        assert search_db.calls == []

    def test_readonly_user_role_key_allowed(self, client, blueprint_app, search_db):
        blueprint_app.api_key_service.validate_key.return_value = {
            'id': 'k', 'user_id': 'u', 'username': 'm21', 'role': 'user',
            'is_active': True, 'user_is_active': True,
        }
        assert _get(client, 'search=web').status_code == 200

    def test_own_search_budget_429_with_retry_after(self, client, blueprint_app, search_db, monkeypatch):
        monkeypatch.setitem(blueprint_app.config, 'EXTERNAL_API_SEARCH_RATE_LIMIT', 2)
        for _ in range(2):
            assert _get(client, 'search=web').status_code == 200
        resp = _get(client, 'search=web')
        assert resp.status_code == 429
        assert resp.get_json()['error'] == 'rate_limit_exceeded'
        assert resp.headers.get('Retry-After')

    def test_search_budget_is_not_the_60_per_minute_default(self, client, blueprint_app, search_db):
        # TestConfig pins EXTERNAL_API_RATE_LIMIT at 5: the search route rides
        # its own (300/min) budget, so a sixth search still answers.
        assert blueprint_app.config['EXTERNAL_API_SEARCH_RATE_LIMIT'] == 300
        for _ in range(6):
            assert _get(client, 'search=web').status_code == 200


class TestSearchRequiredInput:
    @pytest.mark.parametrize('query', ['', 'search=', 'search=%20%20', 'filters=', 'filters=%5B%5D',
                                       'fields=hostname', 'logic=or'])
    def test_neither_search_nor_filters_is_400(self, client, search_db, query):
        resp = _get(client, query)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['error'] == 'validation_error'
        assert '"search"' in body['message'] and '"filters"' in body['message']
        assert search_db.calls == []

    def test_search_over_256_chars_is_400(self, client, search_db):
        resp = _get(client, 'search=' + 'a' * 257)
        assert resp.status_code == 400
        assert '256' in resp.get_json()['message']

    def test_more_than_8_terms_is_400_not_silently_truncated(self, client, search_db):
        # The engine keeps the first 8 AND-ed terms and drops the rest, which
        # WIDENS the result; the route refuses instead.
        resp = _get(client, 'search=' + quote(' '.join(f't{i}' for i in range(9))))
        assert resp.status_code == 400
        assert 'at most 8' in resp.get_json()['message']
        assert _get(client, 'search=' + quote(' '.join(f't{i}' for i in range(8)))).status_code == 200


class TestSearchKeywordSemantics:
    def test_runs_the_trigram_indexed_explorer_expression(self, client, search_db):
        resp = _get(client, 'search=web01')
        assert resp.status_code == 200
        # The exact expression migration 112's pg_trgm GIN index is built on —
        # i.e. _build_search_clause ran, so a 3+ char term is index-served.
        assert f'{_ASSET_SEARCH_EXPR} ILIKE %(search_0)s' in search_db.count_sql
        sql, params = search_db.data_call
        assert f'{_ASSET_SEARCH_EXPR} ILIKE %(search_0)s' in sql
        assert params['search_0'] == '%web01%'

    def test_two_words_must_both_match(self, client, search_db):
        resp = _get(client, 'search=' + quote('Paolo Vallotti'))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert (f'{_ASSET_SEARCH_EXPR} ILIKE %(search_0)s AND '
                f'{_ASSET_SEARCH_EXPR} ILIKE %(search_1)s') in sql
        assert (params['search_0'], params['search_1']) == ('%Paolo%', '%Vallotti%')
        # owner and user_field are both inside the searched text.
        assert 'asset_search_join(owner)' in _ASSET_SEARCH_EXPR
        assert 'asset_search_join(user_field)' in _ASSET_SEARCH_EXPR

    def test_a_partial_ip_matches(self, client, search_db):
        resp = _get(client, 'search=10.20.3')
        assert resp.status_code == 200
        _, params = search_db.data_call
        assert params['search_0'] == '%10.20.3%'   # substring, not equality
        assert 'asset_search_join(ip)' in _ASSET_SEARCH_EXPR

    def test_search_ands_with_filters(self, client, search_db):
        resp = _get(client, 'search=web&filters=' + _filters(
            {'field': 'Category', 'op': 'eq', 'value': 'Server'}))
        assert resp.status_code == 200
        assert ('ILIKE %(search_0)s AND LOWER(category) = LOWER(%(f0_0)s)'
                in search_db.count_sql)


class TestSearchFilters:
    @pytest.mark.parametrize('op', ['eq', '=', 'equals'])
    def test_eq_family(self, client, search_db, op):
        resp = _get(client, 'filters=' + _filters({'field': 'owner', 'op': op, 'value': 'Paolo Vallotti'}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert 'EXISTS (SELECT 1 FROM unnest(owner) AS x WHERE LOWER(x) = LOWER(%(f0_0)s))' in sql
        assert params['f0_0'] == 'Paolo Vallotti'
        assert resp.get_json()['query']['filters'] == [{
            'field': 'static:Owner', 'display_name': 'Owner', 'op': 'eq', 'value': 'Paolo Vallotti',
        }]

    @pytest.mark.parametrize('op', ['contains', '~', 'like'])
    def test_contains_family_is_a_literal_substring(self, client, search_db, op):
        resp = _get(client, 'filters=' + _filters({'field': 'User Field', 'op': op, 'value': 'vallotti_p'}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert "unnest(user_field) AS x WHERE x ILIKE %(f0_0)s ESCAPE '\\'" in sql
        assert params['f0_0'] == '%vallotti\\_p%'   # '_' escaped: literal, not wildcard

    @pytest.mark.parametrize('op', ['starts', 'starts_with', 'starts with', '^'])
    def test_starts_family(self, client, search_db, op):
        resp = _get(client, 'filters=' + _filters({'field': 'ip', 'op': op, 'value': '10.20.'}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert "unnest(ip) AS x WHERE x ILIKE %(f0_0)s ESCAPE '\\'" in sql
        assert params['f0_0'] == '10.20.%'

    @pytest.mark.parametrize('op', ['empty', 'is_empty', 'is empty', 'null'])
    def test_empty_family_ignores_value(self, client, search_db, op):
        resp = _get(client, 'filters=' + _filters({'field': 'Owner', 'op': op}))
        assert resp.status_code == 200
        sql, _ = search_db.data_call
        assert "(owner IS NULL OR owner = '{}')" in sql
        assert resp.get_json()['query']['filters'] == [
            {'field': 'static:Owner', 'display_name': 'Owner', 'op': 'empty'},
        ]

    def test_any_accepts_a_list_or_a_comma_string(self, client, search_db):
        for value in (['Server', 'Workstation'], 'Server, Workstation'):
            search_db.calls.clear()
            resp = _get(client, 'filters=' + _filters({'field': 'category', 'op': 'in', 'value': value}))
            assert resp.status_code == 200
            sql, params = search_db.data_call
            assert 'LOWER(category) IN (LOWER(%(f0_0)s), LOWER(%(f0_1)s))' in sql
            assert (params['f0_0'], params['f0_1']) == ('Server', 'Workstation')

    def test_field_accepts_display_name_plain_and_encoded_field_id(self, client, search_db):
        for field in ('User Field', 'user_field', 'static:User Field', 'static:User%20Field'):
            resp = _get(client, 'filters=' + _filters({'field': field, 'op': 'notempty'}))
            assert resp.status_code == 200, (field, resp.get_json())
            assert resp.get_json()['query']['filters'][0]['field'] == 'static:User%20Field'

    def test_logic_or_groups_the_filters(self, client, search_db):
        resp = _get(client, 'logic=or&search=acme&filters=' + _filters(
            {'field': 'owner', 'op': 'contains', 'value': 'Vallotti'},
            {'field': 'user_field', 'op': 'contains', 'value': 'Vallotti'},
        ))
        assert resp.status_code == 200
        sql = search_db.count_sql
        # one parenthesised OR group, still AND-ed with the keyword term
        assert "ILIKE %(search_0)s AND (EXISTS (SELECT 1 FROM unnest(owner)" in sql
        assert "ESCAPE '\\') OR EXISTS (SELECT 1 FROM unnest(user_field)" in sql
        assert resp.get_json()['query']['logic'] == 'or'

    def test_default_logic_is_and(self, client, search_db):
        resp = _get(client, 'filters=' + _filters(
            {'field': 'owner', 'op': 'notempty'}, {'field': 'location', 'op': 'notempty'},
        ))
        assert resp.status_code == 200
        assert "(owner IS NOT NULL AND owner != '{}') AND (location IS NOT NULL" in search_db.count_sql
        assert resp.get_json()['query']['logic'] == 'and'

    def test_bad_logic_is_400(self, client, search_db):
        resp = _get(client, 'search=x&logic=xor')
        assert resp.status_code == 400
        assert '"and" or "or"' in resp.get_json()['message']


class TestSearchFilterErrors:
    """Every case here would otherwise widen the result or 500. The message is
    what the calling LLM corrects itself from, so its content is pinned."""

    def _err(self, client, *clauses):
        resp = _get(client, 'filters=' + _filters(*clauses))
        assert resp.status_code == 400, resp.get_json()
        body = resp.get_json()
        assert body['error'] == 'validation_error'
        return body['message']

    def test_unknown_field_names_the_nearest_fields(self, client, search_db):
        msg = self._err(client, {'field': 'Ownr', 'op': 'eq', 'value': 'x'})
        assert "Unknown filters[0] field 'Ownr'" in msg
        assert 'GET /api/v1/fields' in msg
        assert "Nearest fields: 'Owner'" in msg
        assert search_db.calls == []

    def test_unknown_encoded_field_is_400(self, client, search_db):
        msg = self._err(client, {'field': 'ext:Nope:attr', 'op': 'eq', 'value': 'x'})
        assert "'ext:Nope:attr'" in msg

    def test_unknown_operator_lists_the_valid_ones(self, client, search_db):
        msg = self._err(client, {'field': 'owner', 'op': 'resembles', 'value': 'x'})
        assert "unknown operator 'resembles'" in msg
        assert "Operators valid for 'Owner': eq, neq, contains" in msg

    def test_operator_invalid_for_the_field_type(self, client, search_db):
        msg = self._err(client, {'field': 'owner', 'op': '>', 'value': 'x'})
        assert "operator '>' (gt) is not valid for 'Owner' (string_array)" in msg
        assert 'Valid: eq, neq' in msg

    def test_binary_operator_without_value_is_400_not_dropped(self, client, search_db):
        # The repo drops a valueless binary clause -> every asset would match.
        for value in (None, '', '   '):
            msg = self._err(client, {'field': 'owner', 'op': 'contains', 'value': value})
            assert "needs a non-empty \"value\"" in msg

    def test_list_value_for_single_value_operator(self, client, search_db):
        msg = self._err(client, {'field': 'owner', 'op': 'eq', 'value': ['a', 'b']})
        assert "Use 'any'" in msg

    def test_between_needs_two_bounds(self, client, search_db):
        msg = self._err(client, {'field': 'Confidence Score', 'op': 'between', 'value': [0.5]})
        assert 'exactly two values' in msg

    def test_number_and_date_values_are_type_checked(self, client, search_db):
        assert 'expected a number' in self._err(
            client, {'field': 'confidence', 'op': '>=', 'value': 'high'})
        assert 'ISO-8601' in self._err(
            client, {'field': 'Last Seen', 'op': '>', 'value': 'last tuesday'})

    def test_unknown_confidence_level_is_400_not_dropped(self, client, search_db):
        msg = self._err(client, {'field': 'Confidence Level', 'op': 'eq', 'value': 'very high'})
        assert "'high', 'medium' or 'low'" in msg

    def test_operator_outside_a_field_allow_list(self, client, search_db):
        # confidence_level's clause builder handles eq/any only.
        msg = self._err(client, {'field': 'Confidence Level', 'op': 'contains', 'value': 'hi'})
        assert 'Valid: eq, any' in msg

    @pytest.mark.parametrize('clause, fragment', [
        ('owner', 'must be an object'),
        ({'field': 'owner', 'operator': 'eq', 'value': 'x'}, "unknown key(s) 'operator'"),
        ({'op': 'eq', 'value': 'x'}, 'missing its "field"'),
        ({'field': 'owner', 'value': 'x'}, 'needs an "op"'),
    ])
    def test_malformed_entries(self, client, search_db, clause, fragment):
        assert fragment in self._err(client, clause)

    @pytest.mark.parametrize('raw, fragment', [
        ('[{"field": "owner"', 'invalid JSON'),
        ('{"field": "owner", "op": "eq", "value": "x"}', 'JSON array'),
    ])
    def test_malformed_json(self, client, search_db, raw, fragment):
        resp = _get(client, 'filters=' + quote(raw))
        assert resp.status_code == 400
        assert fragment in resp.get_json()['message']
        assert search_db.calls == []

    def test_more_than_20_filters_is_400(self, client, search_db):
        msg = self._err(client, *([{'field': 'owner', 'op': 'notempty'}] * 21))
        assert 'maximum is 20' in msg

    def test_filters_over_8kb_is_400(self, client, search_db):
        msg = self._err(client, {'field': 'owner', 'op': 'eq', 'value': 'x' * 8200})
        assert 'maximum is 8192' in msg

    def test_filter_only_field_is_filterable(self, client, search_db):
        resp = _get(client, 'filters=' + _filters({'field': 'Alert', 'op': 'eq', 'value': 'Assets Disappeared'}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert 'assets.id IN (SELECT unnest(al.asset_ids) FROM alerts al' in sql
        assert params['f0_titles'] == ['assets disappeared']

    def test_postgres_data_error_with_filters_is_400(self, client, search_db):
        search_db.raise_on_data = DataError('invalid input syntax for type uuid: "x"\nLINE 3: ...')
        resp = _get(client, 'filters=' + _filters({'field': 'owner', 'op': 'eq', 'value': 'x'}))
        assert resp.status_code == 400
        msg = resp.get_json()['message']
        assert msg.endswith('invalid input syntax for type uuid: "x"')
        assert 'LINE' not in msg

    def test_postgres_data_error_without_filters_stays_a_server_error(self, client, search_db):
        # No caller value was cast, so the fault is in stored data, not input.
        search_db.raise_on_data = DataError('invalid input syntax for type numeric: "n/a"')
        resp = _get(client, 'search=web')
        assert resp.status_code == 500
        assert 'n/a' not in resp.get_json()['message']

    @pytest.mark.parametrize('value, bound', [(1, True), (0, False), ('yes', True), ('FALSE', False), (True, True)])
    def test_boolean_values_bind_as_real_booleans(self, client, search_db, value, bound):
        # An int 1 against a boolean column is `boolean = integer` -> Postgres
        # error -> 500; the validator must hand the engine a bool.
        resp = _get(client, 'filters=' + _filters({'field': 'OS Is LTS', 'op': 'eq', 'value': value}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert 'os_is_lts = %(f0_0)s' in sql
        assert params['f0_0'] is bound

    @pytest.mark.parametrize('op, value, ok', [
        ('eq', '30d', False),          # eq binds as-is: '30d' is no timestamp
        ('gt', '30d', True),
        ('within_last', '12h', True),
        ('eq', '2026-11-01', True),
        ('lt', '2026-11-01T08:00:00Z', True),
        ('gt', '2026-W45-1', False),   # Python reads ISO week dates, Postgres does not
    ])
    def test_date_values_are_checked_per_operator(self, client, search_db, op, value, ok):
        resp = _get(client, 'filters=' + _filters({'field': 'Last Seen', 'op': op, 'value': value}))
        assert (resp.status_code == 200) is ok, resp.get_json()
        if not ok:
            assert 'ISO-8601' in resp.get_json()['message']


class TestSearchInputShapes:
    def test_names_holding_a_colon_resolve(self, client, search_db):
        # Display name and plain field_id as GET /api/v1/fields lists them.
        for field in ('Tags - env:prod', 'tag:env:prod', 'tag:env%3Aprod'):
            search_db.calls.clear()
            resp = _get(client, 'filters=' + _filters({'field': field, 'op': 'eq', 'value': True}))
            assert resp.status_code == 200, (field, resp.get_json())
            assert resp.get_json()['query']['filters'][0]['field'] == 'tag:env%3Aprod'

    @pytest.mark.parametrize('param', ['filter', 'per_page', 'q', 'sort'])
    def test_unknown_parameter_is_400(self, client, search_db, param):
        resp = _get(client, f'search=paolo&{param}=x')
        assert resp.status_code == 400
        msg = resp.get_json()['message']
        assert repr(param) in msg and 'search, filters, logic' in msg
        assert search_db.calls == []

    @pytest.mark.parametrize('query', ['page=two', 'page=2.0', 'limit=all'])
    def test_non_integer_paging_is_400(self, client, search_db, query):
        resp = _get(client, 'search=x&' + query)
        assert resp.status_code == 400
        assert 'must be an integer' in resp.get_json()['message']

    def test_search_terms_match_literally(self, client, search_db):
        _get(client, 'search=' + quote('srv_web 100%'))
        _, params = search_db.data_call
        assert params['search_0'] == '%srv\\_web%'
        assert params['search_1'] == '%100\\%%'
        # echo shows what the caller typed
        search_db.calls.clear()
        assert _get(client, 'search=%25').get_json()['query']['search'] == '%'
        assert search_db.data_call[1]['search_0'] == '%\\%%'

    def test_fields_as_json_array(self, client, search_db):
        resp = _get(client, 'search=x&fields=' + quote(json.dumps(['hostname', 'Tags - env:prod'])))
        assert resp.status_code == 200
        assert resp.get_json()['query']['fields'] == ['static:Hostname', 'tag:env%3Aprod']

    def test_fields_id_only_returns_only_id(self, client, search_db):
        search_db.rows = [{'id': 'a1', 'static:Hostname': ['h1']}]
        resp = _get(client, 'search=x&fields=id')
        assert resp.get_json()['assets'] == [{'id': 'a1'}]
        assert resp.get_json()['query']['fields'] == []

    def test_search_has_its_own_rate_window(self, client, blueprint_app, search_db):
        # TestConfig: the shared default window is 5/min. Six searches must
        # not throttle the next /fields call (and vice versa).
        for _ in range(6):
            assert _get(client, 'search=web').status_code == 200
        assert client.get('/api/v1/fields', headers=API_KEY_HEADER).status_code == 200


class TestSearchConfidenceLevel:
    def test_filter_uses_the_configured_thresholds(self, client, blueprint_app, search_db):
        configured = {'confidence_threshold_medium': 0.3, 'confidence_threshold_high': 0.6}
        blueprint_app.settings_manager.get = (
            lambda _cat, key, default=None: configured.get(key, default)
        )
        resp = _get(client, 'fields=Confidence%20Level&filters=' + _filters(
            {'field': 'Confidence Level', 'op': 'eq', 'value': 'high'}))
        assert resp.status_code == 200
        sql, params = search_db.data_call
        assert 'confidence >= %(f0_eq_h)s' in sql
        assert params['f0_eq_h'] == 0.6          # not the 0.70 default
        # the projected bucket uses the same thresholds
        assert "CASE WHEN confidence >= 0.6 THEN 'high' WHEN confidence >= 0.3" in sql


class TestSearchProjectionSortPaging:
    def test_default_fields_include_owner_and_user(self, client, search_db):
        search_db.rows = [{
            'id': 'a1', 'static:Hostname': ['pc-0042'],
            'static:Owner': ['Paolo Vallotti'], 'static:User%20Field': ['pvallotti'],
        }]
        resp = _get(client, 'search=vallotti')
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'static:Owner' in body['query']['fields']
        assert 'static:User%20Field' in body['query']['fields']
        sql, _ = search_db.data_call
        assert 'owner AS "static:Owner"' in sql
        assert 'user_field AS "static:User%%20Field"' in sql
        row = body['assets'][0]
        assert row['owner'] == ['Paolo Vallotti']
        assert row['user_field'] == ['pvallotti']

    def test_row_keys_contract(self, client, search_db):
        """M21 depends on this: ``id``; static fields by snake_case column
        name; every other kind by its canonical encoded FieldId."""
        search_db.rows = [{
            'id': 'a1',
            'static:Hostname': ['pc-0042'],
            'static:IP%20Address': ['10.20.3.4'],
            'static:User%20Field': ['pvallotti'],
            'static:MAC%20Vendor': ['Dell'],
            'static:Last%20Seen': datetime(2026, 11, 16, 8, 30, tzinfo=timezone.utc),
            'static:Confidence%20Score': Decimal('0.81'),
            'virtual:asset_label': 'pc-0042',
            'core:Geo%20City': ['Milan'],
        }]
        resp = _get(client, 'search=pc&fields=' + quote(
            'hostname,ip,User Field,MAC Vendor,Last Seen,confidence,Asset Label,core:Geo City'))
        assert resp.status_code == 200
        assert resp.get_json()['assets'] == [{
            'id': 'a1',
            'hostname': ['pc-0042'],
            'ip': ['10.20.3.4'],
            'user_field': ['pvallotti'],
            'mac_vendor_enriched': ['Dell'],
            'last_seen': '2026-11-16T08:30:00+00:00',
            'confidence': 0.81,
            'virtual:asset_label': 'pc-0042',
            'core:Geo%20City': ['Milan'],
        }]

    def test_static_row_keys_agree_with_list_assets_query(self):
        # The route finishes list_assets_query's snake-casing; both must name a
        # static field identically or a row would carry two spellings.
        for encoded, snake in _ENCODED_TO_SNAKE.items():
            assert STATIC_ROW_KEYS[encoded] == snake

    def test_fields_unknown_or_filter_only_is_400(self, client, search_db):
        resp = _get(client, 'search=x&fields=hostname,Ownr')
        assert resp.status_code == 400
        assert "Nearest fields: 'Owner'" in resp.get_json()['message']
        resp = _get(client, 'search=x&fields=Alert')
        assert resp.status_code == 400
        assert 'filter-only' in resp.get_json()['message']

    def test_fields_dedupes_and_tolerates_id(self, client, search_db):
        resp = _get(client, 'search=x&fields=id,hostname,Hostname,static:Hostname')
        assert resp.status_code == 200
        assert resp.get_json()['query']['fields'] == ['static:Hostname']

    def test_sort(self, client, search_db):
        resp = _get(client, 'search=x&sort_by=hostname&sort_dir=asc')
        assert resp.status_code == 200
        sql, _ = search_db.data_call
        assert 'ORDER BY hostname[1] ASC NULLS FIRST, id ASC' in sql
        q = resp.get_json()['query']
        assert (q['sort_by'], q['sort_dir']) == ('static:Hostname', 'asc')

    def test_sort_desc_and_default_order(self, client, search_db):
        _get(client, 'search=x&sort_by=Last%20Seen&sort_dir=desc')
        assert 'ORDER BY last_seen DESC NULLS LAST, id ASC' in search_db.data_call[0]
        search_db.calls.clear()
        resp = _get(client, 'search=x')
        assert 'ORDER BY last_time DESC NULLS LAST, id ASC' in search_db.data_call[0]
        q = resp.get_json()['query']
        assert (q['sort_by'], q['sort_dir']) == (None, None)

    @pytest.mark.parametrize('query, fragment', [
        ('sort_by=Hostnme', "Nearest fields: 'Hostname'"),
        ('sort_by=Alert', 'filter-only'),
        ('sort_by=hostname&sort_dir=up', '"asc" or "desc"'),
    ])
    def test_sort_errors(self, client, search_db, query, fragment):
        resp = _get(client, 'search=x&' + query)
        assert resp.status_code == 400
        assert fragment in resp.get_json()['message']

    def test_pagination_reports_the_true_total(self, client, search_db):
        search_db.rows = [{'id': f'a{i}'} for i in range(17)]
        search_db.total = 37
        resp = _get(client, 'search=x&page=2&limit=20')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['pagination'] == {'total': 37, 'page': 2, 'limit': 20, 'pages': 2}
        _, params = search_db.data_call
        assert (params['limit'], params['offset']) == (20, 20)
        # COUNT runs over the same WHERE, without LIMIT
        assert 'LIMIT' not in search_db.count_sql

    def test_default_limit_is_20_and_zero_matches(self, client, search_db):
        resp = _get(client, 'search=x')
        assert resp.get_json()['pagination'] == {'total': 0, 'page': 1, 'limit': 20, 'pages': 0}
        assert resp.get_json()['assets'] == []

    def test_limit_over_100_is_clamped(self, client, search_db):
        resp = _get(client, 'search=x&limit=5000')
        assert resp.status_code == 200
        assert resp.get_json()['pagination']['limit'] == 100
        assert search_db.data_call[1]['limit'] == 100

    def test_page_floor_and_ceiling(self, client, search_db):
        assert _get(client, 'search=x&page=0').get_json()['pagination']['page'] == 1
        resp = _get(client, 'search=x&page=1001')
        assert resp.status_code == 400
        assert '/api/v1/assets/inventory' in resp.get_json()['message']

    def test_response_is_top_level_without_data_envelope(self, client, search_db):
        body = _get(client, 'search=x').get_json()
        assert 'data' not in body
        assert set(body) == {'success', 'assets', 'pagination', 'query'}
        assert body['success'] is True
        assert set(body['query']) == {'search', 'filters', 'logic', 'sort_by', 'sort_dir', 'fields'}

    def test_is_read_only(self, client, search_db):
        _get(client, 'search=web&sort_by=hostname&filters=' + _filters(
            {'field': 'owner', 'op': 'contains', 'value': 'x'},
            {'field': 'Alert', 'op': 'eq', 'value': 'y'},
        ))
        assert len(search_db.calls) == 2
        for sql, _ in search_db.calls:
            assert sql.lstrip().upper().startswith('SELECT')

    def test_request_text_never_reaches_the_sql_string(self, client, search_db):
        evil = "x'); DROP TABLE assets; --"
        _get(client, 'search=' + quote(evil) + '&filters=' + _filters(
            {'field': 'owner', 'op': 'eq', 'value': evil}))
        for sql, params in search_db.calls:
            assert 'DROP TABLE' not in sql
        assert any(evil in str(v) for v in search_db.data_call[1].values())


class TestOldRoutesUnchanged:
    """GET /api/v1/assets and /assets/inventory keep their exact bodies — a
    risk-app connector depends on inventory."""

    def test_query_assets_body(self, client, blueprint_app):
        blueprint_app.asset_service.query_assets.return_value = (
            [{'id': 'a1', 'hostname': ['web01'], 'ip': ['10.1.2.3']}], 1
        )
        resp = client.get('/api/v1/assets?ip=10.1.2.3', headers=API_KEY_HEADER)
        assert resp.get_json() == {
            'success': True,
            'data': {
                'assets': [{'id': 'a1', 'hostname': ['web01'], 'ip': ['10.1.2.3']}],
                'count': 1, 'total': 1, 'query': {'ip': '10.1.2.3'},
            },
        }
        # still exact-match IP via the service, not the search engine
        assert blueprint_app.asset_service.query_assets.call_args[1]['ip'] == '10.1.2.3'

    def test_inventory_body(self, client, blueprint_app):
        result = _dynamic_result(pages=3)
        blueprint_app.asset_service.list_assets_dynamic.return_value = result
        resp = client.get('/api/v1/assets/inventory?fields=static:Hostname', headers=API_KEY_HEADER)
        assert resp.get_json() == result
        # inventory still takes no search/filters
        assert set(blueprint_app.asset_service.list_assets_dynamic.call_args[1]) == {
            'page', 'limit', 'fields',
        }
