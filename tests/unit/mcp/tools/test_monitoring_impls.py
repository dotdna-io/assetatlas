# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke tests for the monitoring *_impl helpers.

Each test mocks db.execute_query so the impl can render its summary text
without a live database. These are smoke tests, not behaviour tests — they
verify the function does not crash and emits a recognisable header.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.monitoring import (
    get_alerts_impl,
    get_asset_history_impl,
    get_dashboard_impl,
    get_data_quality_impl,
    get_integration_status_impl,
    get_pipeline_status_impl,
    list_tag_rules_impl,
)


# A kitchen-sink row that satisfies every .get() / [] lookup the impls do.
_KITCHEN_SINK_ROW = {
    "id": str(uuid4()),
    "total": 1,
    "cnt": 0,
    "category": "server",
    "count": 1,
    "percentage": 100,
    "level": "high",
    "avg_pct": 50,
    "stale_30d": 0,
    "stale_90d": 0,
    "never_seen": 0,
    "asset_count": 0,
    "avg_confidence": 0,
    "name": "jamf",
    "integration_type": "edr",
    "status": "enabled",
    "hours_since_sync": 1,
    "last_connection_test_status": None,
    "last_connection_test_message": None,
    "group_name": None,
    "type": "static",
    "enabled": True,
    "description": None,
    "execution_count": None,
    "last_executed_at": None,
    "last_execution_duration_ms": None,
    "last_execution_assets_matched": None,
    "last_execution_assets_passed": None,
    "last_execution_assets_failed": None,
    "title": "x",
    "title_text": "x",
    "severity": "high",
    "asset_ids": [],
    "current_stage": None,
    "started_at": None,
    "completed_at": None,
    "duration_seconds": None,
    "triggered_by": "manual",
    "total_assets": None,
    "assets_created": None,
    "batches": None,
    "error_message": None,
    "has_hostname": 0,
    "has_ip": 0,
    "has_mac": 0,
    "has_serial": 0,
    "has_os": 0,
    "has_manufacturer": 0,
    "has_owner": 0,
    "has_category": 0,
    "created_at": "2026-04-22T00:00:00Z",
    "updated_at": "2026-04-22T00:00:00Z",
    "hostname": ["host01"],
    "ip": ["10.0.0.1"],
    "time": "2026-04-22T00:00:00Z",
    "event_type": "update",
    "changed_attributes": None,
    "attribute_changes": None,
}


def _kitchen_sink_db():
    db = MagicMock()
    db.execute_query.return_value = [dict(_KITCHEN_SINK_ROW)]
    return db


def test_get_dashboard_impl_returns_summary():
    db = _kitchen_sink_db()
    out = get_dashboard_impl(db)
    assert db.execute_query.called
    assert "Dashboard" in out


def test_get_alerts_impl_returns_summary():
    db = _kitchen_sink_db()
    out = get_alerts_impl(db, time_range="7d", limit=5)
    assert db.execute_query.called
    assert "Alerts" in out


def test_get_alerts_impl_always_shows_severity_breakdown_in_header():
    """The severity breakdown must be visible even when total=0, otherwise
    'how many alerts by severity?' forces a second round-trip."""
    from unittest.mock import MagicMock
    repo = MagicMock()
    repo.get_alerts_summary.return_value = {
        "total": 0,
        "by_type": {"security": 0, "compliance": 0, "operational": 0,
                    "data_quality": 0},
        "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
    }
    repo.get_alerts.return_value = []
    out = get_alerts_impl(MagicMock(), time_range="7d", repo=repo)
    assert "critical: 0" in out
    assert "high: 0" in out
    assert "medium: 0" in out
    assert "low: 0" in out


def test_get_alerts_impl_rejects_unknown_time_range_but_accepts_90d():
    """Silent fallback to 7d turned 'last 90 days' into 'last 7 days' unnoticed.
    Parity ADDS 90d as a valid preset and keeps rejecting genuinely unknown
    ranges loudly, before any query."""
    from unittest.mock import MagicMock
    db = MagicMock()
    out = get_alerts_impl(db, time_range="5y")
    assert "Unknown time_range" in out
    assert "'90d'" in out  # the new valid set is advertised
    db.execute_query.assert_not_called()

    repo = MagicMock()
    repo.get_alerts_summary.return_value = {
        "total": 0, "by_type": {}, "by_severity": {}}
    repo.get_alerts.return_value = []
    out_ok = get_alerts_impl(MagicMock(), time_range="90d", repo=repo)
    assert "Unknown time_range" not in out_ok


def test_get_asset_history_impl_rejects_unknown_time_range():
    from unittest.mock import MagicMock
    from uuid import uuid4
    db = MagicMock()
    out = get_asset_history_impl(db, asset_id=str(uuid4()),
                                 time_range="6months")
    assert "Unknown time_range" in out


def test_get_alerts_impl_surfaces_alert_uuid_for_each_row():
    """get_alert_detail needs the UUID; the model can only see what
    get_alerts prints. Without this, the model passes the alert `type` slug
    (e.g. 'data-source-sync-failures') and the detail call rejects it."""
    from unittest.mock import MagicMock
    repo = MagicMock()
    repo.get_alerts_summary.return_value = {
        "total": 1,
        "by_type": {"operational": 1},
        "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0},
    }
    repo.get_alerts.return_value = [{
        "id": "11111111-2222-3333-4444-555555555555",
        "type": "operational", "severity": "critical",
        "title": "Data source sync failures",
        "status": "active", "asset_count": 3, "source": "system",
        "created_at": "2026-04-22", "updated_at": "2026-04-22"}]
    out = get_alerts_impl(MagicMock(), time_range="30d", repo=repo)
    assert "id=11111111-2222-3333-4444-555555555555" in out


def test_get_pipeline_status_impl_returns_status():
    db = MagicMock()
    # No active run → IDLE branch; no history rows either.
    db.execute_query.return_value = []
    out = get_pipeline_status_impl(db, include_history=True, limit=3)
    assert "Pipeline Status" in out
    assert "IDLE" in out


def test_get_pipeline_status_impl_splits_created_and_returned():
    """The idle 'last run' line reports New (asset_created) and Returned
    (asset_readded) separately, matching the Run History split — not the
    combined total_assets_created (which would double-count the readded)."""
    db = MagicMock()
    db.execute_query.side_effect = [
        [],  # no active run → IDLE
        [{   # last completed run
            "id": "11111111-1111-1111-1111-111111111111",
            "status": "completed", "completed_at": "2026-04-20T10:05:00Z",
            "duration_seconds": 12.0, "triggered_by": "schedule",
            "total_assets": 1000, "assets_created": 1403,  # combined
            "chronicle_events": {"asset_created": 319, "asset_readded": 1084},
        }],
        [],  # history
    ]
    out = get_pipeline_status_impl(db, include_history=True, limit=3)
    assert "319 created" in out
    assert "1084 returned" in out
    assert "1403 created" not in out


def test_get_integration_status_impl_returns_list():
    db = _kitchen_sink_db()
    out = get_integration_status_impl(db)
    assert "Integrations" in out
    assert "jamf" in out


def test_get_integration_status_impl_surfaces_uuid():
    """get_integration_diagnostics needs the UUID; it must appear in the
    get_integration_status output so the LLM can chain the two calls."""
    from unittest.mock import MagicMock
    db = MagicMock()
    db.execute_query.return_value = [{
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "SentinelOne",
        "integration_type": "sentinelone",
        "status": "enabled",
        "last_sync_at": None,
        "last_connection_test_status": "success",
        "last_connection_test_message": None,
        "group_name": None,
        "asset_count": 42,
        "hours_since_sync": 2.5,
    }]
    out = get_integration_status_impl(db)
    assert "id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in out


def test_get_integration_status_impl_surfaces_partial_sync():
    """A truncated collection (#1334) is not a failure, but "are my
    integrations healthy?" must not answer with a misleading all-clear:
    the partial run gets its own count, bucket, and last_error line."""
    from unittest.mock import MagicMock
    db = MagicMock()
    db.execute_query.return_value = [{
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "Databricks",
        "integration_type": "databricks",
        "status": "enabled",
        "last_sync_status": "partial",
        "last_error": "Tables truncated at the 500-page cap",
        "last_sync_at": None,
        "last_connection_test_status": "success",
        "last_connection_test_message": None,
        "group_name": None,
        "asset_count": 10,
        "hours_since_sync": 1.0,
    }]
    out = get_integration_status_impl(db)
    assert "1 partial" in out                      # header count, not "healthy"
    assert "[PARTIAL] Databricks" in out           # per-integration bucket
    assert "Last Sync Status: partial" in out
    assert "Tables truncated at the 500-page cap" in out


def test_list_tag_rules_impl_returns_list():
    db = _kitchen_sink_db()
    out = list_tag_rules_impl(db, enabled_only=False)
    assert "Tag Rules" in out


def test_get_asset_history_impl_with_valid_uuid():
    db = _kitchen_sink_db()
    aid = str(uuid4())
    out = get_asset_history_impl(db, asset_id=aid, time_range="7d", limit=10)
    assert "History for" in out


def test_get_data_quality_impl_returns_report():
    db = _kitchen_sink_db()
    out = get_data_quality_impl(db, category=None)
    assert "Data Quality Report" in out
