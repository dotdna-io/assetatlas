# SPDX-License-Identifier: AGPL-3.0-or-later
"""AlertsRepository read-path window/bucket/source extensions (chat parity 2a).

These lock the backward-compatible additions to the alert read methods: a 90d
preset, explicit start/end bounds, the 'all' (no-window) sentinel, and the
timeline's hour bucketing + by-source split — without disturbing the default
output the /api/alerts UI relies on.
"""
import datetime
from unittest.mock import MagicMock

from src.modules.alerts.repository import AlertsRepository


def _repo(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return AlertsRepository(db), db


def test_get_alerts_custom_window_is_parameterized_and_skips_preset():
    repo, db = _repo()
    s = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    e = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
    repo.get_alerts(start=s, end=e)
    sql, params = db.execute_query.call_args.args
    assert "created_at >= %(w_start)s" in sql
    assert "created_at <= %(w_end)s" in sql
    assert params["w_start"] == s and params["w_end"] == e
    # The preset interval must NOT also be applied.
    assert "now() -" not in sql


def test_get_alerts_all_applies_no_window():
    repo, db = _repo()
    repo.get_alerts(time_range="all")
    sql = db.execute_query.call_args.args[0]
    assert "created_at >=" not in sql
    # No conditions → the WHERE guard collapses to TRUE (never empty WHERE).
    assert "WHERE TRUE" in sql


def test_get_alerts_90d_uses_a_90_day_interval():
    repo, db = _repo()
    repo.get_alerts(time_range="90d")
    assert "interval '90 days'" in db.execute_query.call_args.args[0]


def test_default_preset_window_is_unchanged():
    repo, db = _repo()
    repo.get_alerts(time_range="7d")
    assert "created_at >= now() - interval '7 days'" \
        in db.execute_query.call_args.args[0]


def test_summary_forwards_custom_window():
    repo, db = _repo()
    s = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    repo.get_alerts_summary(start=s)
    sql, params = db.execute_query.call_args.args
    assert "created_at >= %(w_start)s" in sql and params["w_start"] == s


def test_count_window_tracks_summary_for_all_and_custom():
    """get_alerts_count is the paginator total; it MUST honor the same window
    as get_alerts/get_alerts_summary, else /api/alerts?time_range=all shows a
    7-day total for an all-time list (mismatched page count)."""
    repo, db = _repo([{"cnt": 5}])
    repo.get_alerts_count(time_range="all")
    assert "created_at >=" not in db.execute_query.call_args.args[0]

    s = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    repo.get_alerts_count(start=s)
    sql, params = db.execute_query.call_args.args
    assert "created_at >= %(w_start)s" in sql and params["w_start"] == s


def test_timeline_default_is_day_bucket_with_unchanged_shape():
    repo, db = _repo([{"bucket": "2026-06-09", "severity": "high", "cnt": 3}])
    out = repo.get_alerts_timeline()
    sql = db.execute_query.call_args.args[0]
    assert "DATE(created_at)" in sql
    assert ", source" not in sql  # no source split by default
    # The /api/alerts UI consumes this exact shape (a 'date' key + by_severity).
    assert out == [{
        "date": "2026-06-09", "total": 3,
        "by_severity": {"critical": 0, "high": 3, "medium": 0, "low": 0},
    }]


def test_timeline_hour_bucket_uses_date_trunc():
    repo, db = _repo()
    repo.get_alerts_timeline(bucket="hour")
    assert "date_trunc('hour', created_at)" in db.execute_query.call_args.args[0]


def test_timeline_split_by_source_groups_and_aggregates_both_dimensions():
    rows = [
        {"bucket": "2026-06-09", "severity": "critical",
         "source": "system", "cnt": 2},
        {"bucket": "2026-06-09", "severity": "high",
         "source": "prometheus", "cnt": 1},
    ]
    repo, db = _repo(rows)
    out = repo.get_alerts_timeline(bucket="day", split_by_source=True)
    sql = db.execute_query.call_args.args[0]
    assert ", source" in sql  # source is in both SELECT and GROUP BY
    assert len(out) == 1
    entry = out[0]
    assert entry["total"] == 3
    assert entry["by_severity"]["critical"] == 2
    assert entry["by_severity"]["high"] == 1
    assert entry["by_source"] == {"system": 2, "prometheus": 1}


# ---------------------------------------------------------------------------
# count_active_alerts_for_asset — per-asset alert_count badge (#891)
# ---------------------------------------------------------------------------


def test_count_active_alerts_for_asset_filters_status_and_membership():
    """The count must be scoped to OPEN alerts (active + acknowledged, never
    resolved) whose asset_ids array contains the asset — the same open-alert
    definition the rest of the repo uses. If the status filter regressed, a
    resolved alert would inflate the badge."""
    repo, db = _repo([{"cnt": 3}])
    aid = "11111111-1111-1111-1111-111111111111"

    result = repo.count_active_alerts_for_asset(aid)

    sql, params = db.execute_query.call_args.args
    assert "status IN ('active', 'acknowledged')" in sql
    assert "'resolved'" not in sql
    # Array membership, parameterized (never string-interpolated).
    assert "= ANY(asset_ids)" in sql
    assert "%(asset_id)s" in sql
    assert params == {"asset_id": aid}
    assert result == 3


def test_count_active_alerts_for_asset_zero_when_none_match():
    """An asset with no open alerts reports 0 — the badge shows 0, not a crash."""
    repo, db = _repo([{"cnt": 0}])
    result = repo.count_active_alerts_for_asset("22222222-2222-2222-2222-222222222222")
    assert result == 0


# ---------------------------------------------------------------------------
# asset_id exact filter — server-side per-asset scope (#903/#908)
#
# The Alerts page can now scope every read to a single asset. The filter reuses
# count_active_alerts_for_asset's proven `%(asset_id)s::uuid = ANY(asset_ids)`
# membership test and must reach ALL FOUR read methods — list, count, summary
# and timeline — or the header count, the list, the pagination total and the
# timeline bars would disagree on which alerts belong to the asset.
# ---------------------------------------------------------------------------

_ASSET_ID = "948e3f69-818b-4649-8f77-362048686475"


def test_get_alerts_asset_id_adds_bound_membership_clause():
    """asset_id narrows the list to alerts whose asset_ids array contains it,
    bound + cast as %(asset_id)s::uuid — never string-interpolated. An inlined
    id would be an injection seam and would 500 on a bad spelling (#973)."""
    repo, db = _repo()
    repo.get_alerts(asset_id=_ASSET_ID)
    sql, params = db.execute_query.call_args.args
    assert "%(asset_id)s::uuid = ANY(asset_ids)" in sql
    assert params["asset_id"] == _ASSET_ID
    assert _ASSET_ID not in sql  # the literal id never appears inline in the SQL


def test_get_alerts_count_asset_id_adds_bound_membership_clause():
    """The paginator total is scoped to the asset too, else the page count would
    be computed from the unscoped universe while the list shows one asset."""
    repo, db = _repo([{"cnt": 4}])
    repo.get_alerts_count(asset_id=_ASSET_ID)
    sql, params = db.execute_query.call_args.args
    assert "%(asset_id)s::uuid = ANY(asset_ids)" in sql
    assert params["asset_id"] == _ASSET_ID
    assert _ASSET_ID not in sql


def test_get_alerts_summary_asset_id_adds_bound_membership_clause():
    """The severity/type summary cards scope to the asset."""
    repo, db = _repo()
    repo.get_alerts_summary(asset_id=_ASSET_ID)
    sql, params = db.execute_query.call_args.args
    assert "%(asset_id)s::uuid = ANY(asset_ids)" in sql
    assert params["asset_id"] == _ASSET_ID
    assert _ASSET_ID not in sql


def test_get_alerts_timeline_asset_id_adds_bound_membership_clause():
    """The timeline bars scope to the asset."""
    repo, db = _repo()
    repo.get_alerts_timeline(asset_id=_ASSET_ID)
    sql, params = db.execute_query.call_args.args
    assert "%(asset_id)s::uuid = ANY(asset_ids)" in sql
    assert params["asset_id"] == _ASSET_ID
    assert _ASSET_ID not in sql


def test_asset_id_and_combines_with_search_and_severity():
    """The asset filter is ANDed onto the existing search + severity clauses, so
    it can only narrow. If a refactor made asset_id replace the WHERE instead of
    extending it, the title-search / severity clauses would vanish — caught here."""
    repo, db = _repo()
    repo.get_alerts(severity="critical", search="disk", asset_id=_ASSET_ID)
    sql, params = db.execute_query.call_args.args
    assert "severity = %(severity)s" in sql
    assert "(title ILIKE %(search)s OR description ILIKE %(search)s)" in sql
    assert "%(asset_id)s::uuid = ANY(asset_ids)" in sql
    assert " AND " in sql  # the clauses are AND-joined, never OR
    assert params["severity"] == "critical"
    assert params["search"] == "%disk%"
    assert params["asset_id"] == _ASSET_ID


def test_no_asset_id_leaves_membership_clause_out():
    """Without asset_id the membership clause must not appear: #908 adds an
    opt-in filter, it never narrows the default (unscoped) list."""
    repo, db = _repo()
    repo.get_alerts()
    sql, params = db.execute_query.call_args.args
    assert "ANY(asset_ids)" not in sql
    assert "asset_id" not in params
