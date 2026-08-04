# SPDX-License-Identifier: AGPL-3.0-or-later
"""`Alert` as an ordinary Explorer filter, addressable by NAME or by id.

Both spellings exist because one alert row is not one firing.
AlertService._evaluate_match_result keeps at most ONE open alert per rule and
updates it in place; resolved rows with the same title accumulate (the dev DB
holds 17 'Assets Disappeared' rows, none of them open). So:

- a NAME can only mean "the alert currently open for that rule", hence the
  status predicate. Drop it and one token unions years of history.
- a drilldown from a RESOLVED alert card cannot use the name at all, so the id
  spelling is not a convenience — it is the only way to address history.

The value never reaches SQL as text. Filters arrive as JSON and bypass the
blueprint's ensure_uuid(), so a malformed uuid must land among the TITLES and
match nothing; reach Postgres as a bad ::uuid cast instead and psycopg2 raises
something handle_errors cannot read as a ValueError, turning the 400 into the
500 that #973/#999 fixed.
"""

from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository

ALERT_ID = "948e3f69-818b-4649-8f77-362048686475"
ALERT_FIELD = {"kind": "virtual", "name": "alert"}


def _repo():
    return AssetRepository(MagicMock(), MagicMock())


def _clause(op, value):
    repo = _repo()
    params = {}
    clauses, built = repo._build_filter_clauses(
        [{"field_id": ALERT_FIELD, "op": op, "value": value}]
    )
    return (clauses[0] if clauses else None), built


def test_name_matches_only_the_open_alert():
    sql, params = _clause("eq", "Assets Disappeared")
    assert "assets.id IN (SELECT unnest(al.asset_ids) FROM alerts al" in sql
    assert "al.status IN ('active','acknowledged')" in sql
    assert params["f0_titles"] == ["assets disappeared"]
    assert params["f0_ids"] == []


def test_the_name_is_matched_case_insensitively():
    # The token is typed by hand as often as it is clicked.
    _sql, params = _clause("eq", "ASSETS Disappeared")
    assert params["f0_titles"] == ["assets disappeared"]


def test_a_uuid_addresses_that_alert_row_whatever_its_status():
    sql, params = _clause("eq", ALERT_ID)
    assert params["f0_ids"] == [ALERT_ID]
    assert params["f0_titles"] == []
    # The id half carries no status predicate: a resolved alert must resolve.
    assert "al.id = ANY(%(f0_ids)s::uuid[])" in sql


def test_any_splits_a_mixed_list_across_both_halves():
    _sql, params = _clause("any", ["Data Source Stale", ALERT_ID])
    assert params["f0_ids"] == [ALERT_ID]
    assert params["f0_titles"] == ["data source stale"]


def test_values_are_bound_never_interpolated():
    sql, params = _clause("any", ["Data Source Stale", ALERT_ID])
    assert ALERT_ID not in sql
    assert "Data Source Stale" not in sql
    assert "data source stale" not in sql
    assert "%(f0_ids)s" in sql and "%(f0_titles)s" in sql


def test_a_malformed_uuid_becomes_a_title_and_matches_nothing():
    # NOT a 500. This is the whole reason for the try/except split.
    _sql, params = _clause("eq", "948e3f69-not-a-uuid")
    assert params["f0_ids"] == []
    assert params["f0_titles"] == ["948e3f69-not-a-uuid"]


def test_an_empty_half_is_a_valid_no_match():
    # `= ANY('{}')` is false, so neither half needs a special case when every
    # value lands on the other side.
    sql, params = _clause("eq", ALERT_ID)
    assert params["f0_titles"] == []
    assert "%(f0_titles)s" in sql


def test_unsupported_operators_produce_no_clause():
    # Only eq/any are offered by the catalog; anything else must not silently
    # widen the result to the whole fleet.
    for op in ("contains", "neq", "empty", "notempty", "any_of_nonsense"):
        sql, _params = _clause(op, "Assets Disappeared")
        assert sql is None, f"{op} produced a clause"


def test_a_blank_value_produces_no_clause():
    for value in ("", "   ", [], None):
        sql, _params = _clause("eq", value)
        assert sql is None


def test_none_nested_in_a_list_produces_no_clause():
    # str(None) == "None", a non-blank string that used to survive the
    # per-element blank check and get titled as "none" — a live but
    # always-false clause for a filter that really means "nothing selected".
    sql, _params = _clause("any", [None])
    assert sql is None


def test_none_mixed_into_a_list_is_dropped_not_titled():
    sql, params = _clause("any", ["Data Source Stale", None])
    assert sql is not None
    assert params["f0_titles"] == ["data source stale"]


def test_the_alerts_side_is_never_correlated_with_assets():
    """The clause must not re-plan into a per-asset-row array rescan.

    A correlated `EXISTS (... WHERE assets.id = ANY(al.asset_ids) ...)` measured
    25,043 ms against the dev DB's widest open alert (35,136 assets) versus 56 ms
    for the uncorrelated form — and the frontend aborts at 30 s, so that shape
    makes the drilldown fail outright. Every other test here asserts on bound
    params and substrings, which the slow shape satisfied perfectly; only this
    one can fail when the join flips.
    """
    sql, _params = _clause("eq", "Assets Disappeared")
    assert "assets.id = ANY(al.asset_ids)" not in sql, (
        "correlated reference to assets inside the alerts subquery — this is "
        "the shape measured at 25,043 ms against the widest open alert"
    )
    assert "unnest(al.asset_ids)" in sql, (
        "the alerts side must resolve asset_ids uncorrelated from assets, so "
        "the planner cannot flip assets into the outer relation"
    )
