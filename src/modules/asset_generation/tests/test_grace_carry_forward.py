# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grace carry-forward partition logic (#795)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pandas as pd

from src.modules.asset_generation.pipeline.stages.s15c_grace_carry_forward import (
    compute_grace_partition,
)

NOW = datetime(2026, 6, 24, 5, 0, 0, tzinfo=timezone.utc)


def _old(rows):
    return pd.DataFrame(rows)


def _new(ids):
    return pd.DataFrame({'id': ids, 'hostname': [['h']] * len(ids)})


def test_present_assets_are_not_carried_or_removed():
    old = _old([{'id': 'a', 'missing_since': None}, {'id': 'b', 'missing_since': None}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new(['a', 'b']), 30, NOW)
    assert carry_ids == [] and removed == [] and carried.empty


def test_newly_absent_enters_grace_and_is_carried():
    old = _old([{'id': 'a', 'missing_since': None}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new([]), 30, NOW)
    assert carry_ids == ['a'] and removed == []
    # missing_since stamped to now on first absence
    assert pd.Timestamp(carried.iloc[0]['missing_since']).to_pydatetime() == NOW


def test_absent_within_grace_keeps_prior_missing_since():
    ten_days_ago = NOW - timedelta(days=10)
    old = _old([{'id': 'a', 'missing_since': ten_days_ago}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new([]), 30, NOW)
    assert carry_ids == ['a'] and removed == []
    assert pd.Timestamp(carried.iloc[0]['missing_since']).to_pydatetime() == ten_days_ago


def test_absent_at_or_past_grace_is_removed_not_carried():
    thirty_days_ago = NOW - timedelta(days=30)
    old = _old([{'id': 'a', 'missing_since': thirty_days_ago}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new([]), 30, NOW)
    assert carry_ids == [] and removed == ['a'] and carried.empty


def test_uuid_ids_are_normalized_before_diff():
    # old_assets_df holds UUID objects (psycopg2); scored_df holds strings.
    uid = UUID('11111111-1111-1111-1111-111111111111')
    old = _old([{'id': uid, 'missing_since': None}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new([str(uid)]), 30, NOW)
    # Same logical asset → present → not absent.
    assert carry_ids == [] and removed == [] and carried.empty


def test_empty_old_df_is_safe():
    carried, carry_ids, removed, _ = compute_grace_partition(pd.DataFrame(), _new(['a']), 30, NOW)
    assert carry_ids == [] and removed == [] and (carried is None or carried.empty)


def test_asset_returning_from_grace_is_not_carried():
    """An asset with a prior missing_since that reappears in scored_df must not
    be carried or removed — it returns via the normal pipeline path (no
    missing_since), which clears the counter on persistence."""
    ten_days_ago = NOW - timedelta(days=10)
    old = _old([{'id': 'a', 'missing_since': ten_days_ago}])
    carried, carry_ids, removed, _ = compute_grace_partition(old, _new(['a']), 30, NOW)
    assert carry_ids == [] and removed == [] and carried.empty


def test_all_three_states_in_single_call():
    # p: present in scored_df (not absent at all)
    # g: absent, missing_since=5 days ago (within 30-day grace → carry)
    # r: absent, missing_since=40 days ago (past 30-day grace → remove)
    five_days_ago = NOW - timedelta(days=5)
    forty_days_ago = NOW - timedelta(days=40)
    old = _old([
        {'id': 'p', 'missing_since': None},
        {'id': 'g', 'missing_since': five_days_ago},
        {'id': 'r', 'missing_since': forty_days_ago},
    ])
    carried, carry_ids, removed_ids, _ = compute_grace_partition(old, _new(['p']), 30, NOW)
    assert carry_ids == ['g']
    assert removed_ids == ['r']
    assert 'p' not in carry_ids and 'p' not in removed_ids


def _resolver(mapping):
    """resolver(id) -> current root. Unknown ids resolve to themselves (the
    UnionFind state where an id is its own root == genuinely absent, not merged).
    """
    return lambda i: mapping.get(str(i), str(i))


def test_merged_away_absent_asset_is_not_carried_and_reported():
    """An absent asset whose identity now resolves to a DIFFERENT (present) asset
    was merged away, not lost — it must be dropped immediately and reported as a
    merge pair, never parked under the grace window."""
    old = _old([{'id': 'a', 'missing_since': None}])
    carried, carry_ids, removed, merged = compute_grace_partition(
        old, _new(['b']), 30, NOW, resolver=_resolver({'a': 'b'}),
    )
    assert carry_ids == [] and removed == []
    assert carried.empty
    assert merged == {'a': 'b'}


def test_merged_away_outranks_a_fresh_grace_window():
    """Even an asset that *just* went absent (would normally be carried) is
    dropped when its identity has been absorbed by another asset."""
    old = _old([{'id': 'a', 'missing_since': None}])
    carried, carry_ids, removed, merged = compute_grace_partition(
        old, _new(['b']), 30, NOW, resolver=_resolver({'a': 'b'}),
    )
    assert carry_ids == [] and merged == {'a': 'b'} and carried.empty


def test_merged_away_requires_a_present_survivor():
    """If the resolved root is NOT present this run (e.g. an SN-split left the old
    root an orphan absent from scored_df), the asset was not cleanly merged into a
    live asset — fall back to grace carry-forward rather than dropping it and
    recording a dangling merge link."""
    old = _old([{'id': 'a', 'missing_since': None}])
    # 'a' resolves to 'ghost', but 'ghost' is absent from scored_df.
    carried, carry_ids, removed, merged = compute_grace_partition(
        old, _new([]), 30, NOW, resolver=_resolver({'a': 'ghost'}),
    )
    assert merged == {}
    assert carry_ids == ['a'] and removed == []


def test_genuinely_absent_with_resolver_is_still_carried():
    """resolver maps the id to itself → not merged → normal grace carry-forward."""
    old = _old([{'id': 'a', 'missing_since': None}])
    carried, carry_ids, removed, merged = compute_grace_partition(
        old, _new([]), 30, NOW, resolver=_resolver({}),
    )
    assert carry_ids == ['a'] and removed == [] and merged == {}


def test_resolver_returning_none_is_treated_as_genuinely_absent():
    """find(id, allow_missing=True) returns None when the id is unknown to
    UnionFind (its mappings expired) — that is genuinely absent, so carry it."""
    old = _old([{'id': 'a', 'missing_since': None}])
    carried, carry_ids, removed, merged = compute_grace_partition(
        old, _new([]), 30, NOW, resolver=lambda i: None,
    )
    assert carry_ids == ['a'] and merged == {}


def test_four_way_partition_present_carried_removed_merged():
    """All four buckets in one call are mutually exclusive and complete:
    present (p), within-grace carry (g), past-grace remove (r), merged-away (m)."""
    five_days_ago = NOW - timedelta(days=5)
    forty_days_ago = NOW - timedelta(days=40)
    old = _old([
        {'id': 'p', 'missing_since': None},
        {'id': 'g', 'missing_since': five_days_ago},
        {'id': 'r', 'missing_since': forty_days_ago},
        {'id': 'm', 'missing_since': None},
    ])
    # 'm' resolves to present 'p'; the others resolve to themselves.
    carried, carry_ids, removed_ids, merged = compute_grace_partition(
        old, _new(['p']), 30, NOW, resolver=_resolver({'m': 'p'}),
    )
    assert carry_ids == ['g']
    assert removed_ids == ['r']
    assert merged == {'m': 'p'}
    # mutually exclusive: no id appears in two buckets
    assert {'p'}.isdisjoint(carry_ids + removed_ids + list(merged))


def test_no_resolver_returns_empty_merged_pairs():
    """Back-compat: without a resolver the merge bucket is always empty."""
    old = _old([{'id': 'a', 'missing_since': None}])
    result = compute_grace_partition(old, _new([]), 30, NOW)
    assert len(result) == 4
    carried, carry_ids, removed, merged = result
    assert carry_ids == ['a'] and merged == {}
