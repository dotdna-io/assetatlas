# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_integration_diagnostics MCP tool + the chat get_integration tool."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

from src.modules.integrations.models.integration import (
    Integration,
    IntegrationStatus,
)
from src.mcp.tools import integration_admin
from src.mcp.tools.integration_diagnostics import (
    get_integration_diagnostics_impl,
    get_integration_impl,
)


_INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"


def _insights_payload():
    return {
        "integration_id": _INTEGRATION_ID,
        "integration_name": "Jamf Pro",
        "indicators": {
            "stale_assets": {
                "count": 12,
                "total": 100,
                "percentage": 12.0,
                "threshold_days": 30,
            },
            "missing_attributes": {
                "fields": {
                    "serial_number": {"gap": 5.0},
                    "os_version": {"gap": 40.0},
                    "mac_address": {"gap": 10.0},
                    "hostname": {"gap": 2.0},
                },
                "worst_gap_field": "os_version",
                "worst_gap_pct": 40.0,
            },
            "conflicting_attributes": [
                {
                    "field_name": "owner",
                    "distinct_count": 3,
                    "examples": ["alice", "bob", "carol"],
                }
            ],
            "missing_expected_assets": {
                "categories": [
                    {"category": "laptop", "estimated_missing": 15},
                    {"category": "desktop", "estimated_missing": 3},
                ],
                "total_estimated_missing": 18,
            },
        },
    }


def _timeline_payload():
    return {
        "integration_id": _INTEGRATION_ID,
        "range": "30d",
        "granularity": "daily",
        "data": [
            {"date": "2026-04-20", "assets_seen": 1234},
            {"date": "2026-04-21", "assets_seen": 1240},
        ],
    }


def test_insights_renders_full_shape():
    svc = MagicMock()
    svc.get_insights.return_value = _insights_payload()

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="insights"
    )

    assert "Jamf Pro" in out
    assert "12.0" in out  # stale pct
    assert "os_version" in out  # worst gap field
    assert "40.0" in out  # worst gap pct
    assert "owner" in out  # conflict field
    assert "laptop" in out
    svc.get_timeline.assert_not_called()


def test_insights_renders_aggregate_conflicting_attributes_shape():
    """Some integrations return conflicting_attributes as an aggregate dict
    ({total_conflicts, by_field}) rather than a list of per-field records.
    Previously the formatter iterated the dict's string keys and crashed
    with 'str has no attribute get' — kept the whole diagnostics call from
    returning anything."""
    svc = MagicMock()
    payload = _insights_payload()
    payload["indicators"]["conflicting_attributes"] = {
        "total_conflicts": 846,
        "by_field": {"hostname": 112, "sn": 734},
    }
    svc.get_insights.return_value = payload

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="insights"
    )
    assert "846" in out            # total_conflicts is surfaced
    assert "hostname" in out        # per-field rows present
    assert "sn" in out


def test_insights_not_found():
    svc = MagicMock()
    svc.get_insights.return_value = {
        "success": False, "error": "NOT_FOUND", "message": "Integration not found"
    }

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="insights"
    )

    assert f"Integration {_INTEGRATION_ID} not found." in out


def test_timeline_renders_data_points():
    svc = MagicMock()
    svc.get_timeline.return_value = _timeline_payload()

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="timeline"
    )

    assert "2026-04-20" in out
    assert "1234" in out
    assert "2026-04-21" in out
    assert "1240" in out
    svc.get_insights.assert_not_called()


def test_timeline_reports_total_points_and_signals_omitted_tail():
    """A long window yields more points than the 30-point render cap. The header
    must state the TRUE point count and the omission must be announced — without
    it the model reads the most-recent 30 as the whole series (§8-#2). Earlier
    points are the ones dropped (the cap takes the most recent)."""
    from src.mcp.tools.integration_diagnostics import _MAX_TIMELINE_POINTS
    svc = MagicMock()
    n = _MAX_TIMELINE_POINTS + 9
    svc.get_timeline.return_value = {
        "integration_id": _INTEGRATION_ID, "range": "90d", "granularity": "daily",
        "data": [{"date": f"2026-{(i % 12) + 1:02d}-01", "assets_seen": i}
                 for i in range(n)],
    }
    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="timeline"
    )
    assert f"({n} data points)" in out                      # real total in header
    assert "9 earlier data point(s) not shown" in out       # omitted tail signalled


def test_timeline_not_found():
    svc = MagicMock()
    svc.get_timeline.return_value = {
        "success": False, "error": "NOT_FOUND", "message": "Integration not found"
    }

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="timeline"
    )

    assert f"Integration {_INTEGRATION_ID} not found." in out


def test_both_renders_both_sections():
    svc = MagicMock()
    svc.get_insights.return_value = _insights_payload()
    svc.get_timeline.return_value = _timeline_payload()

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="both"
    )

    assert "# Insights" in out
    assert "# Timeline" in out
    assert "Jamf Pro" in out
    assert "2026-04-20" in out


def test_both_insights_raises_but_timeline_renders():
    svc = MagicMock()
    svc.get_insights.side_effect = RuntimeError("boom")
    svc.get_timeline.return_value = _timeline_payload()

    out = get_integration_diagnostics_impl(
        svc, integration_id=_INTEGRATION_ID, kind="both"
    )

    assert "unavailable:" in out.lower()
    assert "2026-04-20" in out


def test_invalid_uuid_does_not_call_service():
    svc = MagicMock()

    out = get_integration_diagnostics_impl(
        svc, integration_id="not-a-uuid", kind="both"
    )

    assert "Invalid integration ID" in out
    svc.get_insights.assert_not_called()
    svc.get_timeline.assert_not_called()


# ===========================================================================
# get_integration (chat tool, plan §2.E) — status/config/insights/timeline/
# schedule/stats for one integration, resolved by name or id, redacted.
# ===========================================================================


def _integ(name="Beszel", iid=_INTEGRATION_ID, **kw):
    return Integration(
        name=name,
        integration_type=kw.get("integration_type", "custom_http"),
        category=kw.get("category", "Monitoring"),
        config=kw.get("config", {"base_url": "https://x", "api_key": "secret"}),
        field_mapping=kw.get("field_mapping", {}),
        transformations=kw.get("transformations"),
        data_filters=kw.get("data_filters", []),
        id=uuid.UUID(iid) if isinstance(iid, str) else iid,
        status=kw.get("status", IntegrationStatus.ENABLED),
        source_method=kw.get("source_method", "direct"),
        last_sync_at=kw.get("last_sync_at"),
        last_sync_status=kw.get("last_sync_status"),
        last_error=kw.get("last_error"),
        last_connection_test_status=kw.get("last_connection_test_status"),
        last_connection_test_message=kw.get("last_connection_test_message"),
        schedule_cron=kw.get("schedule_cron"),
        schedule_timezone=kw.get("schedule_timezone", "UTC"),
        is_running=kw.get("is_running", False),
        group_name=kw.get("group_name"),
    )


def _data_sources(iid=_INTEGRATION_ID, name="Beszel"):
    return {
        "count": 1,
        "sources": [{
            "id": iid, "name": name, "assetCount": 1234, "status": "healthy",
            "lastSyncDisplay": "2 hours ago", "lastRunDisplay": "3 hours ago",
            "confidence": 88,
        }],
    }


def test_get_integration_unknown_view_is_loud_error():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="bogus")

    assert "Valid values" in out


def test_get_integration_unknown_integration_is_loud_error():
    """A missing integration must hand back the known names, not an empty view
    that reads as 'this integration is fine / has no data'."""
    svc = MagicMock()
    svc.get_integration.return_value = None
    svc.list_integrations.return_value = [_integ(name="Beszel")]

    out = get_integration_impl(svc, MagicMock(), integration="Nope")

    assert "Beszel" in out


def test_get_integration_resolves_by_case_insensitive_name():
    svc = MagicMock()
    svc.list_integrations.return_value = [_integ(name="Beszel")]
    ds = MagicMock()
    ds.get_data_sources.return_value = _data_sources()

    out = get_integration_impl(svc, ds, integration="beszel", view="status")

    assert "Beszel" in out


def test_status_uses_dashboard_freshness_matched_by_id():
    """Freshness/asset-count come from the SAME service the homepage uses
    (matched by integration id), so the chat numbers equal the UI's — not a
    bespoke join that could drift."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        last_sync_status="success",
    )
    ds = MagicMock()
    ds.get_data_sources.return_value = _data_sources()

    out = get_integration_impl(svc, ds, integration=_INTEGRATION_ID,
                               view="status")

    assert "1,234" in out             # assetCount from get_data_sources
    assert "healthy" in out           # status from get_data_sources
    assert "2 hours ago" in out       # lastSyncDisplay
    ds.get_data_sources.assert_called_once_with(view="individual")


def test_status_surfaces_failure_and_drilldown_hint():
    """A failed sync + its error must be surfaced (don't bury a broken
    integration), and the asset count must carry a re-runnable filter so the
    model can substantiate it (§4)."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        name="Jamf Pro",
        last_sync_status="failed",
        last_error="401 Unauthorized from /JSSResource",
        last_connection_test_status="failed",
        last_connection_test_message="bad credentials",
    )
    ds = MagicMock()
    ds.get_data_sources.return_value = {"count": 0, "sources": []}

    out = get_integration_impl(svc, ds, integration=_INTEGRATION_ID,
                               view="status")

    assert "failed" in out.lower()
    assert "401 Unauthorized" in out          # the actual sync error
    assert "bad credentials" in out           # connection-test error
    # asset count is unavailable here (no matching source), but the tool still
    # resolved — it should not crash.
    assert "Jamf Pro" in out


def test_status_drilldown_hint_present_when_fresh():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    ds = MagicMock()
    ds.get_data_sources.return_value = _data_sources()

    out = get_integration_impl(svc, ds, integration=_INTEGRATION_ID,
                               view="status")

    # the count is substantiated by a re-runnable query_assets filter
    assert "query_assets" in out
    assert "Data Source" in out and "Beszel" in out


def test_status_without_dashboard_service_degrades_not_crashes():
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        last_sync_at=datetime.fromisoformat("2026-06-19 10:00:00"),
    )

    out = get_integration_impl(svc, None, integration=_INTEGRATION_ID,
                               view="status")

    assert "Beszel" in out
    assert "2026-06-19" in out            # falls back to the object's last_sync_at


def test_config_view_redacts_credentials_and_shows_structure():
    """Config must show the connection shape + field mapping, but NEVER the
    secret values — credentials are always server-redacted, and the tool never
    decrypts (include_credentials is not exposed to chat)."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        config={
            "base_url": "https://beszel.example",
            "api_key": "supersecret",
            "webhook_password": "hunter2",   # non-standard secret key
            "verify_ssl": True,
        },
        field_mapping={"computer.name": "hostname"},
        transformations={"os": "lowercase"},
        data_filters=[{"field": "status", "op": "eq", "value": "active"}],
    )

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="config")

    # secrets gone
    assert "supersecret" not in out
    assert "hunter2" not in out
    # structure present
    assert "api_key" in out                       # the key name is still shown
    assert "***" in out                           # masked marker
    assert "base_url" in out and "https://beszel.example" in out
    assert "computer.name" in out and "hostname" in out   # field mapping
    assert "os" in out                            # transformation
    assert "status" in out                        # data filter


def test_config_view_redacts_nested_header_credentials():
    """A custom_http integration can hand-roll auth via a STATIC header
    (config['headers']['Authorization']) — stored plaintext at rest, under a key
    that SENSITIVE_FIELDS/is_sensitive_key don't recognize. The config view must
    redact it at any depth; a shallow top-level scan would render it verbatim."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        config={
            "base_url": "https://api.example",
            "auth_type": "none",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-supersecret-token",
                "X-API-Key": "key-abc-123",
            },
            "nested": {"deep": {"password": "burrowed"}},
        },
    )

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="config")

    # secrets gone at every nesting depth
    assert "sk-supersecret-token" not in out
    assert "key-abc-123" not in out
    assert "burrowed" not in out
    # non-secret structure still visible
    assert "base_url" in out and "https://api.example" in out
    assert "Content-Type" in out          # benign header retained
    assert "headers" in out               # the container key still shown
    assert "***redacted***" in out


def test_insights_view_delegates_to_insights():
    svc = MagicMock()
    svc.get_integration.return_value = _integ(name="Jamf Pro")
    svc.get_insights.return_value = _insights_payload()

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="insights")

    assert "Jamf Pro" in out
    assert "os_version" in out                     # worst gap field surfaced
    svc.get_timeline.assert_not_called()


def test_timeline_view_passes_custom_window():
    """timeline view must honour the requested look-back, not the 30d default —
    asking for 7 days and silently getting 30 is the wrong answer."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.get_timeline.return_value = _timeline_payload()

    get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                         view="timeline", timeline_days=7)

    svc.get_timeline.assert_called_once()
    assert svc.get_timeline.call_args.kwargs.get("days") == 7


def test_schedule_view_reuses_schedule_renderer(monkeypatch):
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        schedule_cron="0 * * * *", schedule_timezone="UTC",
    )
    monkeypatch.setattr(integration_admin.cron_validator, "calculate_next_runs",
                        lambda cron, tz, count: [
                            datetime.fromisoformat("2026-06-20 16:00:00")])
    monkeypatch.setattr(integration_admin.cron_validator, "get_cron_description",
                        lambda cron: "Every hour")

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="schedule")

    assert "0 * * * *" in out
    assert "Every hour" in out
    assert "2026-06-20 16:00:00" in out


def test_stats_view_renders_batch_aggregates():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.get_batch_stats.return_value = {
        "total_batches": 50, "completed": 47, "failed": 2, "running": 0,
        "partial": 1, "total_records": 12000, "total_mapped": 11800,
        "total_assets_created": 4000, "total_assets_updated": 500,
        "success_rate": 94.0,
        "last_successful_batch": {
            "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "completed_at": "2026-06-20 09:00:00",
            "raw_records_count": 240,
        },
    }

    out = get_integration_impl(svc, MagicMock(), integration=_INTEGRATION_ID,
                               view="stats")

    assert "50" in out                # total batches
    assert "94.0%" in out             # success rate
    assert "12000" in out             # raw records
    assert "4000" in out              # assets created
    svc.batch_repository.get_batch_stats.assert_called_once()
