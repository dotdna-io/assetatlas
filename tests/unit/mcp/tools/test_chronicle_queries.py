# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the chronicle MCP tool list_recent_changes (§2.G).

These pin the parity/correctness changes: custom start/end window, the
attribute "warn on unknown" guard, the lifted min(limit, 10) sample sub-cap,
and the merged_into filter that keeps removed-asset lifecycle events.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.chronicle_queries import list_recent_changes_impl as f


def _happy_db(distinct=7):
    db = MagicMock()
    db.execute_query.side_effect = [
        # totals
        [{"event_type": "attribute_updated", "n": 12, "assets": 5},
         {"event_type": "asset_created", "n": 3, "assets": 3}],
        [{"n": distinct}],                      # distinct assets
        [{"attr": "owner", "n": 8}],            # top attributes
        [{"time": "2026-06-19T10:00:00Z", "event_type": "attribute_updated",
          "changed_attributes": ["owner"], "hostname": ["h1"],
          "category": "server"}],               # sample
    ]
    return db


def test_happy_path_counts_and_sections():
    out = f(_happy_db(), time_range="7d")
    assert "COUNT: 15 change events in window 7d across 7 distinct assets." in out
    assert "Events by type:" in out
    assert "Top 1 most-changed attributes:" in out
    assert "Most recent 1 events:" in out


def test_sample_overflow_is_signalled_against_real_total():
    # The rendered events are the most-recent slice of grand_total; when more
    # exist than are shown, the tail must be announced so the feed isn't taken
    # for the whole window (§4/§8-#2). _happy_db reports 15 events, 1 sample.
    out = f(_happy_db(), time_range="7d")
    assert "Most recent 1 events:" in out
    assert "14 more events not shown" in out   # 15 total - 1 rendered


def test_no_overflow_when_all_events_shown():
    # The signal must NOT fire when the sample already covers the window — a
    # spurious "more not shown" would push the model to needlessly re-query.
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"event_type": "asset_created", "n": 1, "assets": 1}],  # totals → 1
        [{"n": 1}],                                              # distinct
        [],                                                      # top attrs
        [{"time": "2026-06-19T10:00:00Z", "event_type": "asset_created",
          "changed_attributes": [], "hostname": ["h1"],
          "category": "server"}],                               # 1 sample
    ]
    out = f(db, time_range="7d")
    assert "not shown" not in out


def test_unknown_time_range_is_loud():
    # A silent fallback to a default window answers for the WRONG window — worse
    # than an error (§4).
    out = f(MagicMock(), time_range="5y")
    assert "Unknown time_range '5y'" in out


def test_unknown_event_type_is_loud():
    out = f(MagicMock(), event_type="frobnicate")
    assert "Unknown event_type 'frobnicate'" in out


def test_invalid_start_date_is_loud():
    out = f(MagicMock(), start="not-a-date")
    assert "Invalid start date" in out


def test_custom_window_overrides_preset_and_binds_params():
    db = _happy_db()
    out = f(db, time_range="7d", start="2026-01-01", end="2026-02-01")
    assert "2026-01-01 → 2026-02-01" in out
    # The window is a bound param, not an interpolated INTERVAL.
    totals_call = db.execute_query.call_args_list[0]
    params = totals_call.args[1]
    assert "win_start" in params and "win_end" in params
    assert "INTERVAL" not in totals_call.args[0]


def test_attribute_unknown_warns_instead_of_silent_zero():
    # The vocabulary query only runs on the zero-count path (a matched
    # attribute is valid by construction), so the order is totals → distinct →
    # vocab.
    db = MagicMock()
    db.execute_query.side_effect = [
        [],                                     # totals → 0
        [{"n": 0}],                             # distinct
        [{"attr": "owner"}, {"attr": "ip"}],   # vocab (no 'frob')
    ]
    out = f(db, attribute="frob")
    assert "⚠ attribute 'frob' did not change" in out
    assert "owner" in out and "ip" in out
    # Still reports the (honest) zero result below the warning.
    assert "COUNT: 0 events" in out


def test_valid_attribute_skips_vocab_query():
    # A matched attribute is valid by construction — no extra vocab round-trip
    # (totals + distinct + top-attrs-skipped-when-attribute-set + sample).
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"event_type": "attribute_updated", "n": 4, "assets": 3}],  # totals
        [{"n": 3}],                                                  # distinct
        [{"time": "2026-06-19T10:00:00Z", "event_type": "attribute_updated",
          "changed_attributes": ["owner"], "hostname": ["h1"],
          "category": "server"}],                                    # sample
    ]
    out = f(db, attribute="owner")
    assert "⚠" not in out
    assert "COUNT: 4 change events" in out
    # No vocab query: totals, distinct, sample only (the top-attrs section is
    # skipped when an attribute filter is set).
    assert db.execute_query.call_count == 3


def test_sample_cap_lifted_to_full_limit():
    # Regression for the min(limit, 10) sub-cap that hid the feed tail (§4).
    db = _happy_db()
    f(db, time_range="30d", limit=40)
    sample_call = db.execute_query.call_args_list[-1]
    assert sample_call.args[1]["limit"] == 40   # was clamped to 10


def test_merged_filter_keeps_removed_asset_events():
    # merged_into IS NULL drops merge shadows, but `OR a.id IS NULL` keeps
    # removed/retired events whose asset has left `assets` — without it the
    # lifecycle feed would lose the very events it exists to show.
    db = _happy_db()
    f(db, time_range="7d")
    totals_sql = db.execute_query.call_args_list[0].args[0]
    assert "LEFT JOIN assets a" in totals_sql
    assert "a.merged_into IS NULL OR a.id IS NULL" in totals_sql
