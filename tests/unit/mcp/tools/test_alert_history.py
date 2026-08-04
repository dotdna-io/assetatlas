# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_alert_history MCP tool (#939 / #905).

Mirrors test_alert_rules.py: the impl wraps AlertsService.get_alert_history, so
the service is a MagicMock and we assert the rendered text + the arguments the
tool forwards (UUID validation + days clamping)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.alerts import get_alert_history_impl

_ALERT_ID = "550e8400-e29b-41d4-a716-446655440000"


def _svc(history):
    return MagicMock(
        get_alert_history=MagicMock(
            return_value={"alert_id": _ALERT_ID, "history": history}))


def _point(**overrides):
    base = {
        "pipeline_run_id": "11111111-1111-1111-1111-111111111111",
        "status": "firing",
        "asset_count": 7,
        "severity": "high",
        "created_at": "2026-04-20T10:00:00",
        "started_at": "2026-04-20T09:59:00",
        "completed_at": "2026-04-20T10:01:00",
    }
    base.update(overrides)
    return base


def test_none_service_returns_unavailable():
    assert get_alert_history_impl(None, alert_id=_ALERT_ID) == (
        "Alerts service not available.")


def test_invalid_uuid_is_rejected_without_hitting_service():
    svc = _svc([])
    out = get_alert_history_impl(svc, alert_id="not-a-uuid")
    assert "Invalid alert ID" in out
    svc.get_alert_history.assert_not_called()


def test_empty_history_reports_zero():
    out = get_alert_history_impl(_svc([]), alert_id=_ALERT_ID, days=30)
    assert out.startswith("COUNT: 0 firing-history events")
    assert "last 30 days" in out


def test_history_rows_rendered_with_severity_and_asset_count():
    # Distinctive asset counts (17 / 42) that can't collide with the dates or
    # ids, so the assertion proves the matched-asset column actually renders.
    history = [
        _point(started_at="2026-04-20T09:00:00", asset_count=17, severity="low"),
        _point(started_at="2026-04-21T09:00:00", asset_count=42,
               severity="critical", status="resolved"),
    ]
    out = get_alert_history_impl(_svc(history), alert_id=_ALERT_ID)
    assert "COUNT: 2 firing-history event(s)" in out
    # both runs, both severities, and the asset counts appear
    assert "2026-04-20T09:00:00" in out and "2026-04-21T09:00:00" in out
    assert "critical" in out and "low" in out
    assert "resolved" in out
    assert "17" in out and "42" in out
    # the pipeline run id is surfaced for drilldown
    assert "11111111-1111-1111-1111-111111111111" in out


def test_days_are_clamped_before_the_service_call():
    svc = _svc([])
    get_alert_history_impl(svc, alert_id=_ALERT_ID, days=99999)
    # 99999 -> clamped to the 730-day ceiling the repo/service accept.
    svc.get_alert_history.assert_called_once_with(_ALERT_ID, days=730)


def test_service_exception_is_contained():
    svc = MagicMock()
    svc.get_alert_history.side_effect = RuntimeError("boom")
    out = get_alert_history_impl(svc, alert_id=_ALERT_ID)
    assert "Alert history unavailable:" in out and "boom" in out


def test_dense_history_is_capped_with_a_hint():
    # 60 events > the 50-row text cap: show the most recent 50 and note the
    # earlier ones so a dense alert can't blow the context window.
    history = [_point(started_at=f"2026-04-{(i % 28) + 1:02d}T09:00:00")
               for i in range(60)]
    out = get_alert_history_impl(_svc(history), alert_id=_ALERT_ID)
    assert "COUNT: 60 firing-history event(s)" in out
    # canonical overflow_note wording: "[N more <noun> not shown — <hint>.]"
    assert "10 more earlier events not shown" in out
