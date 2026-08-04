# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for MergeService — merging two asset records.

Mocks: PostgreSQLAdapter (db_adapter)
"""

from unittest.mock import MagicMock, call
from uuid import uuid4

import pytest

from src.modules.assets.services.merge_service import (
    MergeService,
    ARRAY_FIELDS,
    SCALAR_FIELDS,
    VALID_STRATEGIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _asset_row(asset_id=None, **overrides):
    """Minimal asset dict as returned by _fetch_asset()."""
    defaults = {
        'id': asset_id or str(uuid4()),
        'hostname': ['web-01'],
        'category': 'Server',
        'subcategory': 'Linux Server',
        'ip': ['10.0.0.1'],
        'mac': ['AA:BB:CC:DD:EE:FF'],
        'data_source': ['jamf'],
        'sn': ['SN001'],
        'os_normalized': 'Ubuntu 22.04',
        'fqdn': ['web-01.example.com'],
        'manufacturer': ['Dell'],
        'model': ['R740'],
        'mac_vendor_enriched': ['Dell Inc.'],
        'confidence': 0.8,
        'owner': None,
        'location': None,
        'last_seen': '2026-01-15T00:00:00+00:00',
        'merged_into': None,
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# Tests
# ===========================================================================


class TestPreviewMerge:
    """Tests for preview_merge()."""

    @pytest.fixture
    def service(self):
        db = MagicMock()
        svc = MergeService(db_adapter=db)
        return svc, db

    def test_returns_preview_structure(self, service):
        svc, db = service
        pid, sid = str(uuid4()), str(uuid4())
        primary = _asset_row(asset_id=pid)
        secondary = _asset_row(asset_id=sid, hostname=['web-02'], ip=['10.0.0.2'])

        # _fetch_asset calls execute_query for each asset
        db.execute_query.side_effect = [
            [primary],   # fetch primary
            [secondary], # fetch secondary
        ]

        result = svc.preview_merge(pid, sid)

        assert 'primary' in result
        assert 'secondary' in result
        assert 'preview' in result
        assert result['primary']['id'] == pid
        assert result['secondary']['id'] == sid
        # Preview should contain merged arrays
        assert 'web-01' in result['preview']['hostname']
        assert 'web-02' in result['preview']['hostname']

    def test_missing_primary_raises(self, service):
        svc, db = service
        db.execute_query.return_value = []  # no rows

        with pytest.raises(LookupError, match="Asset not found"):
            svc.preview_merge(str(uuid4()), str(uuid4()))

    def test_missing_secondary_raises(self, service):
        svc, db = service
        pid = str(uuid4())
        primary = _asset_row(asset_id=pid)
        db.execute_query.side_effect = [
            [primary],  # primary found
            [],         # secondary not found
        ]

        with pytest.raises(LookupError, match="Asset not found"):
            svc.preview_merge(pid, str(uuid4()))

    def test_already_merged_primary_raises(self, service):
        svc, db = service
        pid, sid = str(uuid4()), str(uuid4())
        primary = _asset_row(asset_id=pid, merged_into='some-other-uuid')
        secondary = _asset_row(asset_id=sid)
        db.execute_query.side_effect = [[primary], [secondary]]

        with pytest.raises(ValueError, match="already been merged"):
            svc.preview_merge(pid, sid)

    def test_already_merged_secondary_raises(self, service):
        svc, db = service
        pid, sid = str(uuid4()), str(uuid4())
        primary = _asset_row(asset_id=pid)
        secondary = _asset_row(asset_id=sid, merged_into='some-other-uuid')
        db.execute_query.side_effect = [[primary], [secondary]]

        with pytest.raises(ValueError, match="already been merged"):
            svc.preview_merge(pid, sid)


class TestExecuteMerge:
    """Tests for execute_merge()."""

    @pytest.fixture
    def service(self):
        db = MagicMock()
        svc = MergeService(db_adapter=db)
        return svc, db

    def test_same_asset_raises(self, service):
        svc, db = service
        same_id = str(uuid4())

        with pytest.raises(ValueError, match="Cannot merge the same asset"):
            svc.execute_merge(same_id, same_id, 'prefer_primary', 'admin')

    def test_invalid_strategy_raises(self, service):
        svc, db = service

        with pytest.raises(ValueError, match="Invalid strategy"):
            svc.execute_merge(str(uuid4()), str(uuid4()), 'invalid', 'admin')

    def test_prefer_primary_strategy(self, service):
        svc, db = service
        pid, sid = str(uuid4()), str(uuid4())
        primary = _asset_row(
            asset_id=pid, category='Server', hostname=['web-01'],
            ip=['10.0.0.1'], data_source=['jamf'],
        )
        secondary = _asset_row(
            asset_id=sid, category='Workstation', hostname=['web-02'],
            ip=['10.0.0.2'], data_source=['crowdstrike'],
        )

        # Side effects for the sequence of DB calls:
        # 1. _fetch_asset(primary), 2. _fetch_asset(secondary),
        # 3. _update_primary_asset, 4. _set_merged_into,
        # 5. _get_mappings, 6. _reassign_mappings,
        # 7. _insert_merge_record
        db.execute_query.side_effect = [
            [primary],    # fetch primary
            [secondary],  # fetch secondary
            None,         # update primary
            None,         # set merged_into
            [],           # get_mappings
            None,         # reassign_mappings
            None,         # insert merge record
        ]
        db.bulk_write_chronicle_events = MagicMock()

        result = svc.execute_merge(pid, sid, 'prefer_primary', 'admin')

        assert result['merged_asset']['id'] == pid
        assert result['removed_asset_id'] == sid
        assert result['merged_asset']['merged_by'] == 'admin'
        # Chronicle events written for both assets
        db.bulk_write_chronicle_events.assert_called_once()
        events = db.bulk_write_chronicle_events.call_args[0][0]
        assert len(events) == 2

    def test_prefer_latest_picks_newer(self, service):
        """prefer_latest should pick secondary value when secondary.last_seen > primary.last_seen."""
        svc, db = service
        pid, sid = str(uuid4()), str(uuid4())
        primary = _asset_row(
            asset_id=pid, category='Server',
            last_seen='2026-01-01T00:00:00+00:00',
        )
        secondary = _asset_row(
            asset_id=sid, category='Workstation',
            last_seen='2026-02-01T00:00:00+00:00',
        )

        db.execute_query.side_effect = [
            [primary], [secondary],
            None, None, [], None, None,
        ]
        db.bulk_write_chronicle_events = MagicMock()

        result = svc.execute_merge(pid, sid, 'prefer_latest', 'admin')
        assert result['merged_asset']['id'] == pid

    def test_missing_asset_in_execute_raises(self, service):
        svc, db = service
        db.execute_query.return_value = []

        with pytest.raises(LookupError, match="Asset not found"):
            svc.execute_merge(str(uuid4()), str(uuid4()), 'prefer_primary', 'admin')


class TestResolveFields:
    """Tests for _resolve_fields() field merging logic."""

    @pytest.fixture
    def service(self):
        return MergeService(db_adapter=MagicMock())

    def test_array_fields_unioned(self, service):
        primary = {'ip': ['10.0.0.1'], 'mac': ['AA'], 'data_source': ['jamf'],
                    'sn': [], 'hostname': ['h1'], 'fqdn': [], 'manufacturer': [],
                    'model': [], 'mac_vendor_enriched': [],
                    'category': None, 'subcategory': None, 'os_normalized': None,
                    'confidence': None, 'owner': None, 'location': None,
                    'last_seen': ''}
        secondary = {'ip': ['10.0.0.2', '10.0.0.1'], 'mac': ['BB'], 'data_source': ['cs'],
                      'sn': ['SN1'], 'hostname': ['h2'], 'fqdn': ['h2.local'],
                      'manufacturer': ['Dell'], 'model': ['R740'],
                      'mac_vendor_enriched': [],
                      'category': None, 'subcategory': None, 'os_normalized': None,
                      'confidence': None, 'owner': None, 'location': None,
                      'last_seen': ''}

        merged, resolutions = service._resolve_fields(primary, secondary, 'prefer_primary')

        # Union: 10.0.0.1 appears once, then 10.0.0.2
        assert merged['ip'] == ['10.0.0.1', '10.0.0.2']
        assert merged['mac'] == ['AA', 'BB']
        assert merged['hostname'] == ['h1', 'h2']

    def test_prefer_primary_scalars(self, service):
        primary = {f: None for f in ARRAY_FIELDS}
        primary.update({'category': 'Server', 'subcategory': None,
                        'os_normalized': 'Ubuntu', 'confidence': 0.9,
                        'last_seen': ''})
        secondary = {f: None for f in ARRAY_FIELDS}
        secondary.update({'category': 'Workstation', 'subcategory': 'Laptop',
                          'os_normalized': 'macOS', 'confidence': 0.7,
                          'last_seen': ''})

        merged, resolutions = service._resolve_fields(primary, secondary, 'prefer_primary')

        assert merged['category'] == 'Server'      # primary non-None
        assert merged['subcategory'] == 'Laptop'    # primary None, falls back to secondary
        assert merged['os_normalized'] == 'Ubuntu'
        assert resolutions['category']['source'] == 'primary'
        assert resolutions['subcategory']['source'] == 'secondary'

    def test_array_fields_unioned_for_owner_location(self, service):
        """owner/location/user_field/ipam_location are arrays — multi-source
        merges should preserve every value rather than picking a winner."""
        primary = {f: [] for f in ARRAY_FIELDS}
        primary.update({'owner': ['alice'], 'location': ['SF'],
                        'user_field': ['eng-team'], 'ipam_location': ['10.0.0.0/24'],
                        'category': None, 'subcategory': None,
                        'os_normalized': None, 'confidence': None,
                        'last_seen': ''})
        secondary = {f: [] for f in ARRAY_FIELDS}
        secondary.update({'owner': ['bob'], 'location': ['NYC'],
                          'user_field': [], 'ipam_location': ['10.0.0.0/24'],
                          'category': None, 'subcategory': None,
                          'os_normalized': None, 'confidence': None,
                          'last_seen': ''})

        merged, _ = service._resolve_fields(primary, secondary, 'prefer_primary')

        assert merged['owner'] == ['alice', 'bob']
        assert merged['location'] == ['SF', 'NYC']
        assert merged['user_field'] == ['eng-team']
        assert merged['ipam_location'] == ['10.0.0.0/24']  # deduped

    def test_prefer_latest_when_secondary_newer(self, service):
        primary = {f: [] for f in ARRAY_FIELDS}
        primary.update({'category': 'Server', 'subcategory': None,
                        'os_normalized': 'Ubuntu', 'confidence': 0.9,
                        'last_seen': '2026-01-01T00:00:00+00:00'})
        secondary = {f: [] for f in ARRAY_FIELDS}
        secondary.update({'category': 'Workstation', 'subcategory': 'Desktop',
                          'os_normalized': 'macOS', 'confidence': 0.7,
                          'last_seen': '2026-02-01T00:00:00+00:00'})

        merged, resolutions = service._resolve_fields(primary, secondary, 'prefer_latest')

        # Secondary is newer, so its non-None values are preferred
        assert merged['category'] == 'Workstation'
        assert resolutions['category']['source'] == 'secondary'


class TestUnionArrays:
    """Tests for _union_arrays() helper."""

    @pytest.fixture
    def service(self):
        return MergeService(db_adapter=MagicMock())

    def test_basic_union(self, service):
        assert service._union_arrays(['a', 'b'], ['b', 'c']) == ['a', 'b', 'c']

    def test_preserves_order(self, service):
        assert service._union_arrays(['x', 'a'], ['z', 'a']) == ['x', 'a', 'z']

    def test_empty_lists(self, service):
        assert service._union_arrays([], []) == []

    def test_one_empty(self, service):
        assert service._union_arrays(['a'], []) == ['a']
        assert service._union_arrays([], ['b']) == ['b']


# ===========================================================================
# asset_label override carry-over (#639)
# ===========================================================================


class TestLabelOverrideCarryOver:
    """Override on secondary moves to primary on merge if primary has none."""

    @pytest.fixture
    def db(self):
        return MagicMock()

    @pytest.fixture
    def label_repo(self):
        return MagicMock()

    @pytest.fixture
    def service(self, db, label_repo):
        return MergeService(db_adapter=db, label_override_repo=label_repo)

    def test_carryover_records_resolution(self, service, label_repo):
        primary_id, secondary_id = str(uuid4()), str(uuid4())
        label_repo.get_override.side_effect = [
            None,  # primary has none
            {"label": "Lab Printer", "overridden_by": "alice"},  # secondary has one
        ]

        resolution = service._resolve_label_override(primary_id, secondary_id)

        assert resolution == {
            "carried_over_from": secondary_id,
            "label": "Lab Printer",
        }
        label_repo.transfer_override.assert_called_once_with(secondary_id, primary_id)

    def test_primary_wins_when_both_have_override(self, service, label_repo):
        label_repo.get_override.side_effect = [
            {"label": "Primary"},
            {"label": "Secondary"},
        ]
        resolution = service._resolve_label_override("p", "s")
        assert resolution == {"primary_kept": "Primary"}
        label_repo.transfer_override.assert_not_called()

    def test_no_resolution_when_neither_has_override(self, service, label_repo):
        label_repo.get_override.side_effect = [None, None]
        resolution = service._resolve_label_override("p", "s")
        assert resolution is None
        label_repo.transfer_override.assert_not_called()

    def test_no_op_when_repo_is_None(self, db):
        # Backwards compat: services constructed without the repo should not crash.
        service = MergeService(db_adapter=db, label_override_repo=None)
        assert service._resolve_label_override("p", "s") is None


class TestLabelOverrideUnmergeRestore:
    """If field_resolutions records a carry-over, return the override on unmerge."""

    @pytest.fixture
    def db(self):
        return MagicMock()

    @pytest.fixture
    def label_repo(self):
        return MagicMock()

    @pytest.fixture
    def service(self, db, label_repo):
        return MergeService(db_adapter=db, label_override_repo=label_repo)

    def test_restores_to_secondary(self, service, label_repo):
        secondary_id = str(uuid4())
        field_resolutions = {
            "asset_label_override": {
                "carried_over_from": secondary_id,
                "label": "Lab Printer",
            },
        }

        service._restore_label_override(field_resolutions, primary_id="p", secondary_id=secondary_id)

        label_repo.transfer_override.assert_called_once_with("p", secondary_id)

    def test_no_op_when_no_carryover(self, service, label_repo):
        service._restore_label_override({}, primary_id="p", secondary_id="s")
        label_repo.transfer_override.assert_not_called()

    def test_no_op_when_primary_kept(self, service, label_repo):
        service._restore_label_override(
            {"asset_label_override": {"primary_kept": "X"}},
            primary_id="p", secondary_id="s",
        )
        label_repo.transfer_override.assert_not_called()

    def test_no_op_when_repo_is_None(self, db):
        service = MergeService(db_adapter=db, label_override_repo=None)
        service._restore_label_override(
            {"asset_label_override": {"carried_over_from": "s", "label": "X"}},
            primary_id="p", secondary_id="s",
        )
        # No assertion needed — must just not crash.
