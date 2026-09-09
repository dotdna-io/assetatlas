# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for AssetService — business logic for asset inventory operations.

Mocks: AssetRepository
"""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.modules.assets.services.asset_service import AssetService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_asset_row(**overrides):
    """Minimal asset row as returned by repository."""
    defaults = {
        'id': str(uuid4()),
        'hostname': ['web-01'],
        'category': 'Server',
        'subcategory': 'Linux Server',
        'os_normalized': 'Ubuntu 22.04',
        'ip': ['10.0.0.1'],
        'mac': ['AA:BB:CC:DD:EE:FF'],
        'fqdn': ['web-01.example.com'],
        'sn': ['SN123'],
        'model': ['PowerEdge R740'],
        'manufacturer': ['Dell'],
        'mac_vendor_enriched': ['Dell Inc.'],
        'confidence': 0.85,
        'confidence_breakdown': {
            'source_count': 3,
            'identifier_type': 'serial_number',
            'consistency_penalty': 1.0,
            'conflicting_fields': [],
            'missing_fields': [],
            'days_since_seen': 2,
        },
        'last_time': None,
        'last_seen': None,
        'first_seen': None,
        'first_time': None,
        'data_source': ['jamf', 'crowdstrike'],
        'owner': None,
        'location': None,
        'extended_attributes': {},
    }
    defaults.update(overrides)
    return defaults


def _make_tag(name='critical', color='#ff0000', tag_type='classification'):
    return {'tag_name': name, 'color': color, 'tag_type': tag_type}


def _make_provider_row(**overrides):
    defaults = {
        'data_source': 'int-001',
        'source_name': 'Jamf Pro',
        'source_type': 'jamf',
        'source_status': 'enabled',
        'source_logo': None,
        'hostname': 'web-01',
        'os': 'macOS 14',
        'ip': '10.0.0.1',
        'mac': 'AA:BB:CC:DD:EE:FF',
        'sn': 'SN123',
        'fqdn': 'web-01.local',
        'model': 'MacBook Pro',
        'manufacturer': 'Apple',
        'vendor_category': None,
        'data_source_fields': {'enrolled': True},
        'last_sync_at': None,
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# Tests
# ===========================================================================


class TestAssetServiceListAssets:
    """Tests for list_assets()."""

    @pytest.fixture
    def service(self):
        repo = MagicMock()
        svc = AssetService(repository=repo)
        return svc, repo

    def test_empty_result(self, service):
        svc, repo = service
        repo.list_assets.return_value = ([], 0)
        repo.get_asset_tags.return_value = []

        result = svc.list_assets(page=1, limit=50)

        assert result.success is True
        assert result.assets == []
        assert result.pagination['total'] == 0
        assert result.pagination['pages'] == 0

    def test_single_page(self, service):
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id)
        repo.list_assets.return_value = ([row], 1)
        repo.get_asset_tags.return_value = [_make_tag()]

        result = svc.list_assets(page=1, limit=50)

        assert result.success is True
        assert len(result.assets) == 1
        assert result.assets[0].hostname == 'web-01'
        assert result.assets[0].category == 'Server'
        assert result.assets[0].confidence == 0.85
        assert len(result.assets[0].tags) == 1
        assert result.assets[0].tags[0].name == 'critical'
        assert result.pagination['total'] == 1
        assert result.pagination['pages'] == 1

    def test_pagination_calculation(self, service):
        svc, repo = service
        # 105 total, 50 per page => 3 pages
        repo.list_assets.return_value = ([], 105)
        result = svc.list_assets(page=1, limit=50)
        assert result.pagination['pages'] == 3

    def test_category_and_search_forwarded(self, service):
        svc, repo = service
        repo.list_assets.return_value = ([], 0)
        svc.list_assets(page=2, limit=25, category='Server', search='web')

        # alert_id kwarg added by the alert drill-down feature (#1010); the
        # service always forwards it, defaulting to None.
        repo.list_assets.assert_called_once_with(2, 25, 'Server', 'web', None, alert_id=None)

    def test_ip_as_postgres_array_string(self, service):
        """PostgreSQL arrays may arrive as '{val1,val2}' strings."""
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id, ip='{10.0.0.1,10.0.0.2}')
        repo.list_assets.return_value = ([row], 1)
        repo.get_asset_tags.return_value = []

        result = svc.list_assets(page=1, limit=50)
        assert result.assets[0].ip == ['10.0.0.1', '10.0.0.2']

    def test_none_hostname(self, service):
        svc, repo = service
        row = _make_asset_row(hostname=None)
        repo.list_assets.return_value = ([row], 1)
        repo.get_asset_tags.return_value = []

        result = svc.list_assets(page=1, limit=50)
        assert result.assets[0].hostname is None

    def test_list_includes_confidence_level(self, service):
        """list_assets() includes confidence_level in each AssetListItem."""
        svc, repo = service
        row = _make_asset_row(confidence=0.85)
        repo.list_assets.return_value = ([row], 1)
        repo.get_asset_tags.return_value = []

        result = svc.list_assets(page=1, limit=50)

        from src.modules.assets.models.asset import ConfidenceLevel
        assert result.assets[0].confidence_level == ConfidenceLevel.HIGH.value

    def test_dynamic_list_includes_confidence_level(self, service):
        """list_assets_dynamic() injects confidence_level into raw dicts."""
        svc, repo = service
        row = {'id': str(uuid4()), 'confidence': 0.30, 'hostname': ['test']}
        repo.list_assets.return_value = ([row], 1)

        result = svc.list_assets_dynamic(page=1, limit=50)

        assert result['assets'][0]['confidence_level'] == 'low'


class TestAssetServiceGetAssetDetail:
    """Tests for get_asset_detail()."""

    @pytest.fixture
    def service(self):
        repo = MagicMock()
        svc = AssetService(repository=repo)
        return svc, repo

    def test_not_found(self, service):
        svc, repo = service
        repo.get_asset_by_id.return_value = None

        result = svc.get_asset_detail('missing-uuid')

        assert result is None
        repo.get_asset_by_id.assert_called_once_with('missing-uuid')

    def test_basic_detail(self, service):
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id)
        repo.get_asset_by_id.return_value = row
        repo.get_asset_tags.return_value = [_make_tag()]
        repo.get_asset_providers.return_value = [_make_provider_row()]
        repo.get_other_sources_for_category.return_value = []

        result = svc.get_asset_detail(asset_id)

        assert result is not None
        assert result.success is True
        assert result.asset.hostname == 'web-01'
        assert result.asset.category == 'Server'
        assert result.asset.confidence == 0.85
        assert result.asset.confidence_level.value == 'high'
        assert len(result.asset.tags) == 1
        assert len(result.providers) == 1
        assert result.providers[0].source_name == 'Jamf Pro'

    def test_exposes_vendor_category_user_ipam_location(self, service):
        """vendor_category, user (user_field), and ipam_location are fetched from the
        DB but were previously dropped from the response — they must now be exposed."""
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(
            id=asset_id,
            vendor_category=['firewall'],
            user_field=['jdoe', 'asmith'],
            ipam_location=['DC-1', 'DC-2'],
        )
        repo.get_asset_by_id.return_value = row
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = []
        repo.get_other_sources_for_category.return_value = []

        result = svc.get_asset_detail(asset_id)

        assert result.asset.vendor_category == 'firewall'
        # user / ipam_location are per-source observables — primary scalar + full array
        # (mirrors owner/location), so multi-source values are preserved, not collapsed.
        assert result.asset.user == 'jdoe'
        assert result.asset.users == ['jdoe', 'asmith']
        assert result.asset.ipam_location == 'DC-1'
        assert result.asset.ipam_locations == ['DC-1', 'DC-2']

    def test_deterministic_uuid5_asset_id_is_served(self, service):
        """Safety-split assets get deterministic uuid5 ids (per hostname/SN), so the
        detail response must accept any UUID version — not just v4. Regression for
        v5-id assets returning HTTP 400 instead of rendering."""
        svc, repo = service
        asset_id = '0da29eac-8768-558c-b2f7-c340a5425a99'  # real safety-split asset, UUID v5
        assert UUID(asset_id).version == 5
        repo.get_asset_by_id.return_value = _make_asset_row(id=asset_id)
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = []
        repo.get_other_sources_for_category.return_value = []

        result = svc.get_asset_detail(asset_id)

        assert result is not None
        assert str(result.asset.id) == asset_id

    def test_providers_grouped_by_data_source(self, service):
        """Multiple rows from the same data_source should be merged."""
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id)
        repo.get_asset_by_id.return_value = row
        repo.get_asset_tags.return_value = []

        # Two rows from the same source
        p1 = _make_provider_row(hostname='web-01', ip='10.0.0.1')
        p2 = _make_provider_row(hostname='web-02', ip='10.0.0.2')
        repo.get_asset_providers.return_value = [p1, p2]
        repo.get_other_sources_for_category.return_value = []

        result = svc.get_asset_detail(asset_id)

        # Merged into one provider entry
        assert len(result.providers) == 1
        assert result.providers[0].record_count == 2

    def test_other_sources_returned(self, service):
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id, category='Server')
        repo.get_asset_by_id.return_value = row
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = [_make_provider_row()]
        repo.get_other_sources_for_category.return_value = [
            {
                'name': 'vCenter',
                'integration_type': 'vcenter',
                'logo': None,
                'asset_count': 42,
            }
        ]

        result = svc.get_asset_detail(asset_id)

        assert len(result.other_sources) == 1
        assert result.other_sources[0].source_name == 'vCenter'
        assert result.other_sources[0].asset_count == 42

    def test_other_source_with_logo(self, service):
        svc, repo = service
        asset_id = str(uuid4())
        row = _make_asset_row(id=asset_id, category='Server')
        repo.get_asset_by_id.return_value = row
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = [_make_provider_row()]
        repo.get_other_sources_for_category.return_value = [
            {
                'name': 'Intune',
                'integration_type': 'intune',
                'logo': 'intune.png',
                'asset_count': 10,
            }
        ]

        result = svc.get_asset_detail(asset_id)
        assert result.other_sources[0].source_icon == '/logos/intune.png'

    @pytest.mark.parametrize('source_status, expected', [
        ('enabled', 'active'),
        ('disabled', 'inactive'),
        ('error', 'active'),
        (None, 'active'),       # LEFT JOIN returned NULL (integration deleted)
    ])
    def test_provider_status_mapping(self, service, source_status, expected):
        """Providers should be 'active' unless integration is explicitly disabled.

        Regression: #415 — NULL status (missing integration) was mapped to
        'inactive', causing the UI to show 0 sources.
        """
        svc, repo = service
        asset_id = str(uuid4())
        repo.get_asset_by_id.return_value = _make_asset_row(id=asset_id)
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = [
            _make_provider_row(source_status=source_status)
        ]
        repo.get_other_sources_for_category.return_value = []

        result = svc.get_asset_detail(asset_id)

        assert result.providers[0].status == expected


class TestAssetServiceConfidenceLevel:
    """Tests for _calculate_confidence_level() with configurable thresholds."""

    @pytest.fixture
    def service(self):
        return AssetService(repository=MagicMock())

    def test_high_default_thresholds(self, service):
        """Without app context, falls back to defaults (0.45/0.70)."""
        from src.modules.assets.models.asset import ConfidenceLevel
        assert service._calculate_confidence_level(0.70) == ConfidenceLevel.HIGH
        assert service._calculate_confidence_level(0.95) == ConfidenceLevel.HIGH

    def test_medium_default_thresholds(self, service):
        from src.modules.assets.models.asset import ConfidenceLevel
        assert service._calculate_confidence_level(0.45) == ConfidenceLevel.MEDIUM
        assert service._calculate_confidence_level(0.69) == ConfidenceLevel.MEDIUM

    def test_low_default_thresholds(self, service):
        from src.modules.assets.models.asset import ConfidenceLevel
        assert service._calculate_confidence_level(0.0) == ConfidenceLevel.LOW
        assert service._calculate_confidence_level(0.44) == ConfidenceLevel.LOW

    def test_custom_thresholds_from_settings(self, service):
        """When app context exists, reads from settings_manager."""
        from unittest.mock import patch, MagicMock
        from src.modules.assets.models.asset import ConfidenceLevel

        mock_app = MagicMock()
        mock_app.settings_manager.get.side_effect = lambda cat, key: {
            ('asset_generation', 'confidence_threshold_high'): 0.80,
            ('asset_generation', 'confidence_threshold_medium'): 0.50,
        }[(cat, key)]

        with patch('src.modules.assets.services.asset_service.current_app', mock_app):
            assert service._calculate_confidence_level(0.80) == ConfidenceLevel.HIGH
            assert service._calculate_confidence_level(0.79) == ConfidenceLevel.MEDIUM
            assert service._calculate_confidence_level(0.50) == ConfidenceLevel.MEDIUM
            assert service._calculate_confidence_level(0.49) == ConfidenceLevel.LOW

    def test_get_confidence_thresholds_default(self, service):
        """Without app context, returns default thresholds."""
        medium, high = service._get_confidence_thresholds()
        assert medium == 0.45
        assert high == 0.70

    def test_get_confidence_thresholds_from_settings(self, service):
        from unittest.mock import patch, MagicMock

        mock_app = MagicMock()
        mock_app.settings_manager.get.side_effect = lambda cat, key: {
            ('asset_generation', 'confidence_threshold_high'): 0.80,
            ('asset_generation', 'confidence_threshold_medium'): 0.50,
        }[(cat, key)]

        with patch('src.modules.assets.services.asset_service.current_app', mock_app):
            medium, high = service._get_confidence_thresholds()
            assert medium == 0.50
            assert high == 0.80


class TestAssetServiceParseArrayField:
    """Tests for _parse_array_field()."""

    @pytest.fixture
    def service(self):
        return AssetService(repository=MagicMock())

    def test_none(self, service):
        assert service._parse_array_field(None) == []

    def test_list(self, service):
        assert service._parse_array_field(['a', 'b']) == ['a', 'b']

    def test_list_with_nones(self, service):
        assert service._parse_array_field(['a', None, 'b']) == ['a', 'b']

    def test_postgres_array_string(self, service):
        assert service._parse_array_field('{10.0.0.1,10.0.0.2}') == ['10.0.0.1', '10.0.0.2']

    def test_plain_string(self, service):
        assert service._parse_array_field('web-01') == ['web-01']

    def test_empty_string(self, service):
        assert service._parse_array_field('') == []

    def test_non_string_non_list(self, service):
        assert service._parse_array_field(42) == []


class TestAssetServiceFieldValues:
    """Tests for get_field_values()."""

    @pytest.fixture
    def service(self):
        repo = MagicMock()
        return AssetService(repository=repo), repo

    def test_confidence_level_returns_static_enum(self, service):
        svc, repo = service
        # Virtual confidence_level short-circuits in the service and does
        # not touch the repository. Encoded form per encoding.py.
        result = svc.get_field_values(field='virtual:confidence_level')
        assert result['field'] == 'virtual:confidence_level'
        assert result['total_distinct'] == 3
        values = [v['value'] for v in result['values']]
        assert 'high' in values
        assert 'medium' in values
        assert 'low' in values
        repo.get_field_values.assert_not_called()

    def test_non_virtual_field_delegates_to_repo(self, service):
        """Non-virtual fields are forwarded to the repository, not short-circuited."""
        svc, repo = service
        repo.get_field_values.return_value = ([{'value': 'Server', 'count': 10}], 1)

        result = svc.get_field_values(field='static:Category', limit=20)

        assert result['total_distinct'] == 1
        repo.get_field_values.assert_called_once_with(
            'static:Category', 20, None, None, include_total=True
        )

    def test_include_total_false_is_forwarded(self, service):
        """include_total=False reaches the repo and nulls total_distinct (#1406)."""
        svc, repo = service
        repo.get_field_values.return_value = ([{'value': 'Server', 'count': 10}], None)

        result = svc.get_field_values(field='static:Category', include_total=False)

        assert result['total_distinct'] is None
        repo.get_field_values.assert_called_once_with(
            'static:Category', 50, None, None, include_total=False
        )

    def test_confidence_level_check_uses_decode_not_string_compare(self, service):
        """The virtual-field check decodes first so it is format-independent.

        Regression: the old code used `if field == 'virtual:confidence_level'`
        which couples the check to the encoding format.  The fix decodes to a
        typed FieldId and uses isinstance + .name.  Verify the short-circuit
        fires via the canonical encoded string to confirm decode is invoked.
        """
        from src.modules.fields.encoding import encode
        from src.modules.fields.models import VirtualField
        svc, repo = service

        # Round-trip: encode a VirtualField → get the canonical string → pass it in.
        encoded = encode(VirtualField(kind='virtual', name='confidence_level'))
        result = svc.get_field_values(field=encoded)

        assert result['total_distinct'] == 3
        repo.get_field_values.assert_not_called()


class TestAssetServiceSearchAssets:
    """Tests for search_assets()."""

    @pytest.fixture
    def service(self):
        repo = MagicMock()
        svc = AssetService(repository=repo)
        return svc, repo

    def test_empty_results(self, service):
        svc, repo = service
        repo.search_assets.return_value = []

        result = svc.search_assets('nonexistent', limit=10)

        assert result.success is True
        assert result.results == []
        assert result.query == 'nonexistent'

    def test_results_with_match(self, service):
        svc, repo = service
        asset_id = str(uuid4())
        repo.search_assets.return_value = [
            {
                'id': asset_id,
                'hostname': ['web-01'],
                'category': 'Server',
                'ip': ['10.0.0.1'],
                'match_field': 'hostname',
            }
        ]

        result = svc.search_assets('web', limit=5)

        assert len(result.results) == 1
        assert result.results[0].hostname == 'web-01'
        assert result.results[0].match_field == 'hostname'
        repo.search_assets.assert_called_once_with('web', 5)


class TestAssetServiceCalculateStatus:
    """Tests for _calculate_asset_status() — per-asset alert_count wiring (#891).

    alert_count must reflect the real active-alert count from the alerts
    repository; is_compliant/is_secure stay None (out of scope, #866).
    """

    def test_alert_count_comes_from_alerts_repo(self):
        alerts_repo = MagicMock()
        alerts_repo.count_active_alerts_for_asset.return_value = 4
        svc = AssetService(repository=MagicMock(), alerts_repository=alerts_repo)
        asset_id = str(uuid4())

        status = svc._calculate_asset_status({'id': asset_id})

        assert status.alert_count == 4
        alerts_repo.count_active_alerts_for_asset.assert_called_once_with(asset_id)
        # The other two fields are deliberately left unpopulated (#866).
        assert status.is_compliant is None
        assert status.is_secure is None

    def test_alert_count_zero_without_alerts_repo(self):
        """Legacy single-repository construction must not crash — reports 0."""
        svc = AssetService(repository=MagicMock())

        status = svc._calculate_asset_status({'id': str(uuid4())})

        assert status.alert_count == 0

    def test_alert_count_zero_and_no_query_when_id_missing(self):
        """A row with no id falls back to 0 and never hits the alerts repo."""
        alerts_repo = MagicMock()
        svc = AssetService(repository=MagicMock(), alerts_repository=alerts_repo)

        status = svc._calculate_asset_status({})

        assert status.alert_count == 0
        alerts_repo.count_active_alerts_for_asset.assert_not_called()
