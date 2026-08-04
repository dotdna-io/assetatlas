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

    PINNED_ENTRY_KEYS = {
        'field_id', 'field_id_encoded', 'display_name',
        'kind', 'data_type', 'is_array', 'member_sources',
    }

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
