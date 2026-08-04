# SPDX-License-Identifier: AGPL-3.0-or-later
"""GraceCarryForwardStage wiring + guards (#795)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from src.modules.asset_generation.pipeline.stages.s15c_grace_carry_forward import (
    GraceCarryForwardStage,
)


def _ctx(old_ids, new_ids, track_changes=True):
    return SimpleNamespace(
        old_assets_df=pd.DataFrame({'id': old_ids, 'missing_since': [None] * len(old_ids)}),
        scored_df=pd.DataFrame({'id': new_ids, 'hostname': [['h']] * len(new_ids)}),
        track_changes=track_changes,
        stats={'stages': {}},
        merge_pairs={},
        generation_run_id='11111111-1111-1111-1111-111111111111',
    )


def _service(grace_days):
    svc = MagicMock()
    svc._get_setting.side_effect = lambda cat, key: (
        grace_days if key == 'removal_grace_period_days' else 30
    )
    # Default: every id is its own UnionFind root → genuinely absent, not merged.
    svc.identifier.union_find.find.side_effect = lambda i, allow_missing=False: str(i)
    return svc


def test_absent_asset_is_appended_to_scored_df():
    svc = _service(30)
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b'], new_ids=['a'])  # b vanished
    stage._execute(ctx)
    assert set(ctx.scored_df['id'].astype(str)) == {'a', 'b'}
    svc.mapping_repo.refresh_mappings_for_assets.assert_called_once_with(['b'])
    assert ctx.stats['stages']['grace_carry_forward']['carried_forward'] == 1
    carried_row = ctx.scored_df[ctx.scored_df['id'].astype(str) == 'b']
    assert carried_row.iloc[0]['last_generation_run_id'] == '11111111-1111-1111-1111-111111111111'


def test_merged_away_asset_is_removed_not_carried_and_recorded_as_merge():
    """An absent asset whose identity resolves to a present, different asset was
    merged away — it must NOT be carried forward (no 30-day orphan) and must be
    recorded in ctx.merge_pairs so the diff/chronicle log it as a merge."""
    svc = _service(30)
    # 'b' was absorbed into present 'a'; 'a' stays its own root.
    svc.identifier.union_find.find.side_effect = lambda i, allow_missing=False: (
        'a' if str(i) == 'b' else str(i)
    )
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b'], new_ids=['a'])
    stage._execute(ctx)
    # b dropped, not parked
    assert set(ctx.scored_df['id'].astype(str)) == {'a'}
    # recorded as a merge for chronicle linkage
    assert ctx.merge_pairs.get('b') == 'a'
    # not kept alive as a carried asset
    svc.mapping_repo.refresh_mappings_for_assets.assert_not_called()
    stats = ctx.stats['stages']['grace_carry_forward']
    assert stats['carried_forward'] == 0
    assert stats['merged_away'] == 1


def test_genuinely_absent_still_carried_alongside_a_merged_away_asset():
    """Mixed run: 'b' merged into present 'a', 'c' genuinely vanished. 'c' is
    carried (grace), 'b' is dropped + recorded as a merge."""
    svc = _service(30)
    svc.identifier.union_find.find.side_effect = lambda i, allow_missing=False: (
        'a' if str(i) == 'b' else str(i)
    )
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b', 'c'], new_ids=['a'])
    stage._execute(ctx)
    assert set(ctx.scored_df['id'].astype(str)) == {'a', 'c'}  # c carried, b dropped
    assert ctx.merge_pairs.get('b') == 'a'
    svc.mapping_repo.refresh_mappings_for_assets.assert_called_once_with(['c'])
    stats = ctx.stats['stages']['grace_carry_forward']
    assert stats['carried_forward'] == 1 and stats['merged_away'] == 1


def test_absent_asset_unknown_to_unionfind_is_carried_not_merged():
    """find(id, allow_missing=True) returns None for an id whose mappings expired
    / isn't in UnionFind — that is genuinely absent, so carry it, never merge it."""
    svc = _service(30)
    svc.identifier.union_find.find.side_effect = lambda i, allow_missing=False: (
        None if str(i) == 'b' else str(i)
    )
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b'], new_ids=['a'])  # b vanished, unknown to UF
    stage._execute(ctx)
    assert set(ctx.scored_df['id'].astype(str)) == {'a', 'b'}  # b carried
    assert ctx.merge_pairs == {}
    stats = ctx.stats['stages']['grace_carry_forward']
    assert stats['carried_forward'] == 1 and stats['merged_away'] == 0


def test_disabled_when_grace_is_zero():
    svc = _service(0)
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b'], new_ids=['a'])
    stage._execute(ctx)
    assert set(ctx.scored_df['id'].astype(str)) == {'a'}  # b NOT carried
    svc.mapping_repo.refresh_mappings_for_assets.assert_not_called()


def test_noop_when_track_changes_false():
    svc = _service(30)
    stage = GraceCarryForwardStage(svc)
    ctx = _ctx(old_ids=['a', 'b'], new_ids=['a'], track_changes=False)
    stage._execute(ctx)
    assert set(ctx.scored_df['id'].astype(str)) == {'a'}
    svc.mapping_repo.refresh_mappings_for_assets.assert_not_called()


def test_effective_grace_capped_by_mapping_expiration():
    # grace=60 but mapping_expiration=30 → effective 30; an asset absent 45d is removed.
    from datetime import datetime, timedelta, timezone
    svc = MagicMock()
    svc._get_setting.side_effect = lambda cat, key: (
        60 if key == 'removal_grace_period_days' else 30
    )
    # 'a' is its own root → genuinely absent (tests the grace-cap removal path).
    svc.identifier.union_find.find.side_effect = lambda i, allow_missing=False: str(i)
    stage = GraceCarryForwardStage(svc)
    old = pd.DataFrame({'id': ['a'], 'missing_since': [datetime.now(timezone.utc) - timedelta(days=45)]})
    ctx = SimpleNamespace(old_assets_df=old, scored_df=pd.DataFrame({'id': [], 'hostname': []}),
                          track_changes=True, stats={'stages': {}}, merge_pairs={},
                          generation_run_id='11111111-1111-1111-1111-111111111111')
    stage._execute(ctx)
    assert ctx.stats['stages']['grace_carry_forward']['carried_forward'] == 0
