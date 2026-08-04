# SPDX-License-Identifier: AGPL-3.0-or-later
"""get_alerts window, validation and paging contract.

The headline bug (#735 / parity §4): filtering by status='active' used to
NULLIFY the time window, so "active alerts in the last 7 days" silently became
"every active alert ever". get_alerts now wraps AlertsRepository and the status
filter must NOT touch the window — only time_range='all' or a custom start/end
changes it. These tests assert the repo is invoked with the right window/filters
so they fail if the override bug ever returns.
"""
from unittest.mock import MagicMock

from src.mcp.tools import monitoring


def _mock_repo(*, total=1, items=None, timeline=None, by_severity=None):
    """A repo double whose read methods return UI-shaped data, so the tests
    assert on the CALL (window/filter/paging intent) not on raw SQL strings."""
    repo = MagicMock()
    repo.get_alerts_summary.return_value = {
        "total": total,
        "by_type": {"security": 0, "compliance": 0,
                    "operational": total, "data_quality": 0},
        "by_severity": by_severity
        or {"critical": 0, "high": 0, "medium": total, "low": 0},
    }
    repo.get_alerts.return_value = items if items is not None else [{
        "id": "11111111-1111-1111-1111-111111111111", "type": "operational",
        "severity": "medium", "title": "Stale Assets", "status": "active",
        "asset_count": 9, "source": "system",
        "created_at": "2026-06-09", "updated_at": "2026-06-09",
    }]
    repo.get_alerts_timeline.return_value = timeline or []
    return repo


def test_active_status_keeps_the_time_window():
    """THE regression guard: status must filter WITHOUT widening the window."""
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), status="active", repo=repo)
    # Both the count(summary) and the page(list) run over the SAME window.
    for call in (repo.get_alerts_summary.call_args, repo.get_alerts.call_args):
        assert call.kwargs["time_range"] == "7d"   # window preserved
        assert call.kwargs["start"] is None         # not a custom range
        assert call.kwargs["status"] == "active"    # status still applied


def test_all_time_range_passes_all_to_the_repo():
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), time_range="all", repo=repo)
    # The repo applies no window for 'all'; the tool must forward it verbatim.
    assert repo.get_alerts.call_args.kwargs["time_range"] == "all"


def test_default_window_is_7d():
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), repo=repo)
    assert repo.get_alerts.call_args.kwargs["time_range"] == "7d"
    assert repo.get_alerts.call_args.kwargs["start"] is None


def test_active_status_is_case_insensitive_and_keeps_window():
    """The LLM may emit 'Active'; DB statuses are lowercase. The status is
    lowercased for the filter AND the window is still applied."""
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), status="Active", repo=repo)
    assert repo.get_alerts.call_args.kwargs["status"] == "active"
    assert repo.get_alerts.call_args.kwargs["time_range"] == "7d"


def test_custom_start_end_overrides_preset():
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), time_range="7d",
                               start="2026-01-01", end="2026-02-01", repo=repo)
    kw = repo.get_alerts.call_args.kwargs
    assert kw["start"] is not None and kw["end"] is not None
    assert kw["start"].year == 2026 and kw["start"].month == 1
    # A date-only end extends to end-of-day so the final day isn't dropped (§4).
    assert kw["end"].hour == 23 and kw["end"].minute == 59


def test_90d_is_now_accepted():
    """get_alerts historically rejected 90d; parity adds it."""
    repo = _mock_repo()
    out = monitoring.get_alerts_impl(MagicMock(), time_range="90d", repo=repo)
    assert "Unknown time_range" not in out
    assert repo.get_alerts.call_args.kwargs["time_range"] == "90d"


def test_unknown_time_range_is_rejected_loudly_without_querying():
    """A silent fallback would answer the wrong window; reject loudly instead."""
    repo = _mock_repo()
    out = monitoring.get_alerts_impl(MagicMock(), time_range="5y", repo=repo)
    assert "Unknown time_range" in out and "'90d'" in out
    repo.get_alerts.assert_not_called()


def test_unknown_enum_values_are_rejected_loudly():
    for kwargs in ({"severity": "sev9"}, {"alert_type": "bogus"},
                   {"status": "snoozed"}, {"bucket": "minute"}):
        repo = _mock_repo()
        out = monitoring.get_alerts_impl(MagicMock(), repo=repo, **kwargs)
        assert "Unknown" in out, kwargs
        repo.get_alerts.assert_not_called()


def test_invalid_custom_date_is_rejected_without_querying():
    repo = _mock_repo()
    out = monitoring.get_alerts_impl(MagicMock(), start="not-a-date", repo=repo)
    assert "Invalid start date" in out
    repo.get_alerts.assert_not_called()


def test_paging_reports_real_total_and_pages():
    repo = _mock_repo(total=45)
    out = monitoring.get_alerts_impl(MagicMock(), per_page=20, page=2, repo=repo)
    assert repo.get_alerts.call_args.kwargs["per_page"] == 20
    assert repo.get_alerts.call_args.kwargs["page"] == 2
    assert "45 total" in out
    assert "Page 2/3" in out  # ceil(45 / 20) == 3 — real total, not page len


def test_per_page_cap_is_raised_to_200_but_bounded():
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), per_page=500, repo=repo)
    assert repo.get_alerts.call_args.kwargs["per_page"] == 200


def test_legacy_limit_maps_to_per_page():
    """MCP back-compat: the old single-arg `limit` still sizes the page."""
    repo = _mock_repo()
    monitoring.get_alerts_impl(MagicMock(), limit=5, repo=repo)
    assert repo.get_alerts.call_args.kwargs["per_page"] == 5


def test_include_summary_emits_by_type_breakdown():
    repo = _mock_repo(total=3)
    out = monitoring.get_alerts_impl(MagicMock(), include_summary=True,
                                     repo=repo)
    assert "By type" in out and "operational: 3" in out


def test_bucket_requests_a_source_split_timeline():
    repo = _mock_repo(
        total=2,
        timeline=[{"date": "2026-06-09", "total": 2,
                   "by_severity": {"critical": 1, "high": 0,
                                   "medium": 1, "low": 0},
                   "by_source": {"system": 2}}],
    )
    out = monitoring.get_alerts_impl(MagicMock(), bucket="day", repo=repo)
    kw = repo.get_alerts_timeline.call_args.kwargs
    assert kw["bucket"] == "day"
    assert kw["split_by_source"] is True
    assert "Timeline (day buckets)" in out
    # The requested system/custom/prometheus split is actually surfaced.
    assert "src[system:2]" in out


def test_timeline_source_split_maps_user_to_custom():
    """The raw alerts.source 'user' must surface as 'custom' in the timeline
    split too — matching the list/detail labelling, so one response never
    labels the same source two ways."""
    repo = _mock_repo(
        total=3,
        timeline=[{"date": "2026-06-09", "total": 3,
                   "by_severity": {"critical": 0, "high": 1,
                                   "medium": 2, "low": 0},
                   "by_source": {"user": 2, "system": 1}}],
    )
    out = monitoring.get_alerts_impl(MagicMock(), bucket="day", repo=repo)
    assert "custom:2" in out
    assert "user:" not in out


def test_return_data_yields_a_valid_table_artifact_with_drill_ids():
    """return_data feeds the chat binding; the alert list must render as a valid
    table whose rows each carry alert_id (the get_alert_detail drill key)."""
    from src.api.blueprints.chat.artifacts import (
        build_table_from_alerts, validate_envelope,
    )
    repo = _mock_repo(total=1)
    text, data = monitoring.get_alerts_impl(MagicMock(), repo=repo,
                                            return_data=True)
    assert data["total_items"] == 1 and data["total_pages"] == 1
    art = build_table_from_alerts(data["items"], total=data["total_items"])
    assert validate_envelope(art, tool_name="get_alerts") is not None
    assert all(r.get("alert_id") for r in art["series"])
    assert "Stale Assets" in text
