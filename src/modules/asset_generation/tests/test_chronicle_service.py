# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for ChronicleService.

Tests:
- _normalize_to_list: NaN, pd.NA, pd.NaT, and None are all treated as empty,
  preventing phantom 'attribute_updated' events during chronicle comparison.
- Pipeline merge linkage: merge events are linked with shared operation_id.
"""

import pytest
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.asset_generation.services.chronicle_service import ChronicleService


class TestNormalizeToList:
    """Test _normalize_to_list handles missing-value variants correctly."""

    def test_none_returns_empty_list(self):
        """None (DB NULL) should normalize to []."""
        assert ChronicleService._normalize_to_list(None) == []

    def test_float_nan_returns_empty_list(self):
        """float('nan') (DataFrame NaN) should normalize to []."""
        assert ChronicleService._normalize_to_list(float('nan')) == []

    def test_pd_na_returns_empty_list(self):
        """pd.NA (nullable integer NaN) should normalize to []."""
        assert ChronicleService._normalize_to_list(pd.NA) == []

    def test_pd_nat_returns_empty_list(self):
        """pd.NaT (missing timestamp) should normalize to []."""
        assert ChronicleService._normalize_to_list(pd.NaT) == []

    def test_string_returns_wrapped_list(self):
        """A plain string should be wrapped in a list."""
        assert ChronicleService._normalize_to_list("hello") == ["hello"]

    def test_int_returns_wrapped_list(self):
        """An integer should be wrapped in a list."""
        assert ChronicleService._normalize_to_list(42) == [42]

    def test_list_returns_same_list(self):
        """A list should be returned as-is."""
        assert ChronicleService._normalize_to_list([1, 2]) == [1, 2]

    def test_dict_returns_wrapped_list(self):
        """A dict should be wrapped in a list (not iterated)."""
        assert ChronicleService._normalize_to_list({"key": "val"}) == [{"key": "val"}]


class TestMergeLinkedEvents:
    """Test that pipeline merge events are linked via shared operation_id."""

    @pytest.fixture
    def chronicle(self):
        return ChronicleService(db_adapter=MagicMock())

    @pytest.fixture
    def event_time(self):
        return datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)

    def test_merge_generates_linked_events(self, chronicle, event_time):
        """Merged assets should produce linked update + removal events."""
        current = {
            'asset-a': {'hostname': 'srv-01', 'ip': '10.0.0.1'},
            'asset-b': {'hostname': 'srv-02', 'ip': '10.0.0.2'},
        }
        new = {
            'asset-a': {
                'hostname': 'srv-01',
                'ip': '10.0.0.1',
                'mac': 'aa:bb:cc:dd:ee:ff',
            },
        }
        merge_pairs = {'asset-b': 'asset-a'}

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time, merge_pairs=merge_pairs,
        )

        update_events = [
            e for e in events
            if e['event_type'] == 'attribute_updated'
        ]
        removal_events = [
            e for e in events
            if e['event_type'] == 'asset_removed'
        ]

        assert len(removal_events) == 1
        removal = removal_events[0]

        # Removal event must be tagged as a pipeline merge
        assert removal['asset_id'] == 'asset-b'
        assert removal['causation'] == 'pipeline_merge'
        assert removal['related_asset_id'] == 'asset-a'
        assert removal['operation_id'] is not None

        # Surviving asset's update must share the same operation_id
        op_id = removal['operation_id']
        linked_updates = [
            e for e in update_events
            if e['asset_id'] == 'asset-a'
            and e['operation_id'] == op_id
        ]
        assert len(linked_updates) == 1
        assert linked_updates[0]['causation'] == 'pipeline_merge'
        assert linked_updates[0]['related_asset_id'] == 'asset-b'

    def test_non_merge_removal_has_no_operation_id(
        self, chronicle, event_time
    ):
        """No operation_id when merge_pairs is absent."""
        current = {
            'asset-a': {'hostname': 'srv-01'},
            'asset-b': {'hostname': 'srv-02'},
        }
        new = {
            'asset-a': {'hostname': 'srv-01'},
        }

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time,
        )

        removal_events = [
            e for e in events
            if e['event_type'] == 'asset_removed'
        ]
        assert len(removal_events) == 1
        removal = removal_events[0]
        assert removal['operation_id'] is None
        assert removal['causation'] == 'scheduled_scan'


class TestChangeTrackingGuard:
    """s18 should raise if chronicle prerequisites are missing."""

    def test_raises_when_old_assets_not_loaded(self):
        from src.modules.asset_generation.pipeline.stages.s18_change_tracking import ChangeTrackingStage
        from src.modules.asset_generation.pipeline.context import PipelineContext
        import pandas as pd

        stage = ChangeTrackingStage(service=MagicMock())
        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        ctx.track_changes = True
        ctx.scored_df = pd.DataFrame({"id": ["a1"]})
        ctx.old_assets_df = None  # Not loaded!
        ctx.historical_ids = ["some-id"]

        with pytest.raises(RuntimeError, match="old_assets_df"):
            stage._execute(ctx)

    def test_raises_when_historical_ids_not_loaded(self):
        from src.modules.asset_generation.pipeline.stages.s18_change_tracking import ChangeTrackingStage
        from src.modules.asset_generation.pipeline.context import PipelineContext
        import pandas as pd

        service_mock = MagicMock()
        service_mock.db.execute_query.return_value = []
        stage = ChangeTrackingStage(service=service_mock)
        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        ctx.track_changes = True
        ctx.scored_df = pd.DataFrame({"id": ["a1"]})
        ctx.old_assets_df = pd.DataFrame()
        ctx.historical_ids = None  # Not loaded!

        with pytest.raises(RuntimeError, match="historical_ids"):
            stage._execute(ctx)


class TestPersistedFieldsInjection:
    """s18 must inject DB-only fields (tags, posture_rules) into scored_df before
    chronicle compare — they are written outside scored_df by stages 9.5/9.6, so
    without injection chronicle sees populated → empty on every run."""

    def _make_stage(self, tags_rows=None, posture_rows=None, ext_attrs_rows=None):
        from src.modules.asset_generation.pipeline.stages.s18_change_tracking import ChangeTrackingStage

        service_mock = MagicMock()

        def execute_query(sql, params):
            if 'extended_attributes' in sql:
                return ext_attrs_rows or []
            if 'posture_rules' in sql:
                return posture_rows or []
            if 'tags' in sql:
                return tags_rows or []
            return []

        service_mock.db.execute_query.side_effect = execute_query
        # _track_changes is exercised separately; here we only verify scored_df
        # was populated correctly before delegation.
        service_mock._track_changes.return_value = {'event_type_counts': {}}
        return ChangeTrackingStage(service=service_mock), service_mock

    def test_posture_rules_injected_from_db_into_scored_df(self):
        from src.modules.asset_generation.pipeline.context import PipelineContext

        posture_value = {
            'secured': {'state': 'n/a', 'output': None},
            'monitored': {'state': 'n/a', 'output': None},
        }
        stage, service_mock = self._make_stage(
            posture_rows=[{'id': 'a1', 'posture_rules': posture_value}]
        )

        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        ctx.track_changes = True
        ctx.scored_df = pd.DataFrame({'id': ['a1'], 'hostname': ['host1']})
        ctx.old_assets_df = pd.DataFrame({
            'id': ['a1'], 'hostname': ['host1'], 'posture_rules': [posture_value],
        })
        ctx.historical_ids = []
        ctx.providers_df_prepared = None
        ctx.merge_pairs = None

        stage._execute(ctx)

        # _track_changes must receive a scored_df with the posture_rules column
        # populated, otherwise chronicle would see [dict] vs [] for every asset.
        scored_arg = service_mock._track_changes.call_args[0][1]
        assert 'posture_rules' in scored_arg.columns
        assert scored_arg.loc[0, 'posture_rules'] == posture_value

    def test_extended_attributes_reloaded_from_db_replaces_scored_df_value(self):
        """s21 CSV enrichment merges 'enrichment' sub-dict into the DB after
        persistence wipes it. s18 must re-read so chronicle sees the post-
        enrichment value, not the bare scored_df value."""
        from src.modules.asset_generation.pipeline.context import PipelineContext

        bare_value = {
            'AssetAtlas': {'ip_type': 'none', 'ip_public': [], 'ip_private': []},
        }
        enriched_value = {
            'AssetAtlas': {'ip_type': 'none', 'ip_public': [], 'ip_private': []},
            'enrichment': {'business_unit': 'Acme'},
        }
        stage, service_mock = self._make_stage(
            ext_attrs_rows=[{'id': 'a1', 'extended_attributes': enriched_value}],
        )

        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        ctx.track_changes = True
        ctx.scored_df = pd.DataFrame({
            'id': ['a1'], 'hostname': ['host1'], 'extended_attributes': [bare_value],
        })
        ctx.old_assets_df = pd.DataFrame({
            'id': ['a1'], 'hostname': ['host1'],
            'extended_attributes': [enriched_value],
        })
        ctx.historical_ids = []
        ctx.providers_df_prepared = None
        ctx.merge_pairs = None

        stage._execute(ctx)

        scored_arg = service_mock._track_changes.call_args[0][1]
        assert scored_arg.loc[0, 'extended_attributes'] == enriched_value


class TestTrackChangesMergeCount:
    """_track_changes must count asset_removed/pipeline_merge events separately so
    the run summary can split identity consolidations from genuine removals."""

    def _service(self, events):
        from src.modules.asset_generation.services.asset_generation_service import (
            AssetGenerationService,
        )
        svc = AssetGenerationService.__new__(AssetGenerationService)
        svc.chronicle_service = MagicMock()
        svc.chronicle_service.excluded_fields = set()
        svc.chronicle_service.compare_and_generate_events.return_value = events
        svc.chronicle_service.write_events.return_value = len(events)
        return svc

    def test_counts_pipeline_merge_removals_separately(self):
        events = [
            {'event_type': 'asset_created'},
            {'event_type': 'asset_removed', 'causation': 'scheduled_scan'},
            {'event_type': 'asset_removed', 'causation': 'pipeline_merge'},
            {'event_type': 'asset_removed', 'causation': 'pipeline_merge'},
        ]
        svc = self._service(events)
        old = pd.DataFrame({'id': ['a', 'b', 'c']})
        new = pd.DataFrame({'id': ['a']})

        result = svc._track_changes(old, new, 'run-1', historical_ids=[])

        assert result['merged_assets'] == 2
        # gross asset_removed count is left intact (1 genuine + 2 merges)
        assert result['event_type_counts']['asset_removed'] == 3

    def test_no_merges_yields_zero(self):
        events = [{'event_type': 'asset_removed', 'causation': 'scheduled_scan'}]
        svc = self._service(events)
        result = svc._track_changes(
            pd.DataFrame({'id': ['a']}), pd.DataFrame({'id': []}),
            'run-1', historical_ids=[],
        )
        assert result['merged_assets'] == 0


class TestChangeTrackingMergeSplit:
    """s18 surfaces merged_assets and nets it out of removed_assets."""

    def _stage_ctx(self, chronicle_stats):
        from src.modules.asset_generation.pipeline.stages.s18_change_tracking import (
            ChangeTrackingStage,
        )
        from src.modules.asset_generation.pipeline.context import PipelineContext

        service_mock = MagicMock()
        service_mock.db.execute_query.return_value = []
        service_mock._track_changes.return_value = chronicle_stats
        stage = ChangeTrackingStage(service=service_mock)
        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        ctx.track_changes = True
        ctx.scored_df = pd.DataFrame({'id': ['a1'], 'hostname': ['host1']})
        ctx.old_assets_df = pd.DataFrame({'id': ['a1'], 'hostname': ['host1']})
        ctx.historical_ids = []
        ctx.providers_df_prepared = None
        ctx.merge_pairs = {}
        return stage, ctx

    def test_removed_excludes_merges_and_merged_is_surfaced(self):
        stage, ctx = self._stage_ctx({
            'event_type_counts': {'asset_removed': 3, 'asset_created': 1},
            'merged_assets': 2,
        })
        stage._execute(ctx)
        assert ctx.stats['merged_assets'] == 2
        assert ctx.stats['removed_assets'] == 1   # 3 gross - 2 merged
        assert ctx.stats['new_assets'] == 1

    def test_no_merges_leaves_removed_gross(self):
        stage, ctx = self._stage_ctx({
            'event_type_counts': {'asset_removed': 4},
            'merged_assets': 0,
        })
        stage._execute(ctx)
        assert ctx.stats['merged_assets'] == 0
        assert ctx.stats['removed_assets'] == 4
