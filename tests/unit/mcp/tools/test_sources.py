# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the source-coverage MCP tool get_source_coverage (§2.G).

The headline fix: warn when EITHER source matches zero (was: only when BOTH
did — a typo on one side silently read as a real "no overlap" answer). Plus
the by_category view and per-bucket drilldowns.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.sources import get_source_coverage_impl as f


def _two_source_db(a_total, b_total, a_only=0, b_only=0, both=0, all_assets=100):
    db = MagicMock()
    db.execute_query.return_value = [{
        "a_only": a_only, "b_only": b_only, "both": both,
        "a_total": a_total, "b_total": b_total, "all_assets": all_assets,
    }]
    return db


def test_warns_when_source_a_matches_zero():
    # The confirmed bug: a typo'd source_a (0 matches) used to produce a silent
    # "A only=0" with no signal. It must warn loudly.
    db = _two_source_db(a_total=0, b_total=50, b_only=50)
    out = f(db, source_a="Intue", source_b="Defender")
    assert "⚠ source 'Intue' matched 0 assets" in out
    # The breakdown still renders below the warning.
    assert "Set breakdown:" in out


def test_warns_when_source_b_matches_zero():
    db = _two_source_db(a_total=30, b_total=0, a_only=30)
    out = f(db, source_a="Intune", source_b="Defendr")
    assert "⚠ source 'Defendr' matched 0 assets" in out


def test_both_zero_returns_neither_matched():
    db = _two_source_db(a_total=0, b_total=0)
    out = f(db, source_a="x", source_b="y")
    assert "Neither 'x' nor 'y' matched any assets" in out


def test_both_nonzero_no_warning():
    db = _two_source_db(a_total=30, b_total=25, a_only=10, b_only=5, both=20)
    out = f(db, source_a="Intune", source_b="Defender")
    assert "⚠" not in out
    assert "in both A and B:" in out
    assert "Set breakdown:" in out


def test_single_source_zero_warns():
    db = MagicMock()
    db.execute_query.return_value = [{"n": 0}]
    out = f(db, source_a="Typo")
    assert "⚠ source 'Typo' matched 0 assets" in out


def test_case_insensitive_match_in_sql():
    db = MagicMock()
    db.execute_query.return_value = [{"n": 5}]
    f(db, source_a="intune")
    assert "LOWER(s) = LOWER(%(a)s)" in db.execute_query.call_args_list[0].args[0]


def test_two_source_drilldowns_are_set_algebra():
    db = _two_source_db(a_total=30, b_total=25, a_only=10, b_only=5, both=20)
    text, art = f(db, source_a="Intune", source_b="Defender",
                  with_artifact=True)
    by_label = {s["label"]: s for s in art["series"]}
    a_only = by_label["Intune only"]["drilldown"]["filters"]
    # A-only = in A AND not in B.
    assert {"field": "Data Source", "op": "any", "value": ["Intune"]} in a_only
    assert {"field": "Data Source", "op": "none", "value": ["Defender"]} in a_only
    both = by_label["in both"]["drilldown"]["filters"]
    assert {"field": "Data Source", "op": "any", "value": ["Defender"]} in both


def test_distribution_drilldowns_use_data_source_count():
    db = MagicMock()
    db.execute_query.return_value = [
        {"n_sources": 1, "n_assets": 300},
        {"n_sources": 2, "n_assets": 150},
    ]
    _, art = f(db, with_artifact=True)
    single = [s for s in art["series"] if s["label"] == "single-source"][0]
    assert single["drilldown"]["filters"] == [
        {"field": "Data Source Count", "op": "eq", "value": 1}]


def test_by_category_view_and_drilldown():
    db = MagicMock()
    db.execute_query.return_value = [
        {"category": "server", "assets": 100, "distinct_sources": 4,
         "multi_source": 70},
        {"category": "workstation", "assets": 500, "distinct_sources": 3,
         "multi_source": 400},
    ]
    text, art = f(db, by_category=True, with_artifact=True)
    assert "Per-category source coverage" in text
    assert art["meta"]["mode"] == "by_category"
    assert art["series"][0]["drilldown"]["filters"] == [
        {"field": "Category", "op": "eq", "value": "server"}]


def test_by_category_caps_text_rows_with_overflow_signal():
    # Many asset categories must not all dump into context: the text is capped
    # and the omission is announced, while the artifact keeps every category so
    # the model can still drill the full set (§3.5/§8-#2).
    from src.mcp.tools._common import CHAT_TEXT_ROW_CAP
    db = MagicMock()
    rows = [
        {"category": f"cat{i}", "assets": i, "distinct_sources": 1,
         "multi_source": 0}
        for i in range(CHAT_TEXT_ROW_CAP + 5)
    ]
    db.execute_query.return_value = rows
    text, art = f(db, by_category=True, with_artifact=True)
    # Only the cap renders in text; the tail is signalled, not silently dropped.
    assert "cat0" in text and f"cat{CHAT_TEXT_ROW_CAP - 1}" in text
    assert f"cat{CHAT_TEXT_ROW_CAP}" not in text
    assert "5 more categories not shown" in text
    # The artifact is complete — nothing is actually lost.
    assert len(art["series"]) == CHAT_TEXT_ROW_CAP + 5


def test_by_category_not_blocked_by_stray_source_b():
    # by_category is a standalone mode; a stray source_b (without source_a) must
    # not trip the "source_b requires source_a" guard and hide the view.
    db = MagicMock()
    db.execute_query.return_value = [
        {"category": "server", "assets": 10, "distinct_sources": 2,
         "multi_source": 5}]
    out = f(db, by_category=True, source_b="Defender")
    assert "Per-category source coverage" in out
    assert "requires source_a" not in out


def test_str_path_returns_plain_string():
    # MCP back-compat: without with_artifact the impl returns a str, not a tuple.
    db = _two_source_db(a_total=30, b_total=25, both=20)
    out = f(db, source_a="A", source_b="B")
    assert isinstance(out, str)
