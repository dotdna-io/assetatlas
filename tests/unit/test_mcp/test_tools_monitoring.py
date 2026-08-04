# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP monitoring tools with mocked DB."""

from unittest.mock import MagicMock

import pytest

from mcp.server.fastmcp import FastMCP

from src.mcp.tools.monitoring import register_tools


@pytest.fixture
def monitoring_tools(mock_ctx):
    """Register monitoring tools and return tool functions + mock ctx."""
    mcp = FastMCP("test")
    register_tools(mcp)
    tools = mcp._tool_manager._tools
    return tools, mock_ctx


class TestGetDashboard:
    """Test the get_dashboard tool."""

    def test_returns_summary(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # Dashboard makes 5 queries: total, today, categories, confidence, sources
        db.execute_query = MagicMock(
            side_effect=[
                [{"total": 1000}],
                [{"cnt": 5}],
                [{"category": "Server", "count": 400, "percentage": 40.0}],
                [{"level": "high", "count": 700, "percentage": 70.0}],
                [
                    {
                        "name": "Intune",
                        "integration_type": "api",
                        "status": "enabled",
                        "asset_count": 800,
                        "avg_confidence": 82,
                        "hours_since_sync": 2.5,
                    }
                ],
            ]
        )

        result = tools["get_dashboard"].fn(ctx=ctx)
        assert "1,000" in result
        assert "Server" in result
        assert "Dashboard" in result

    def test_empty_db(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(return_value=[])

        result = tools["get_dashboard"].fn(ctx=ctx)
        assert isinstance(result, str)
        assert "Dashboard" in result


class TestGetAlerts:
    """Test the get_alerts tool."""

    def test_returns_alerts(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # get_alerts now wraps AlertsRepository: 2 queries — the summary
        # (GROUP BY type, severity → cnt; its total is the window total) and
        # the alert page.
        db.execute_query = MagicMock(
            side_effect=[
                [{"type": "operational", "severity": "high", "cnt": 1}],
                [
                    {
                        "id": 1,
                        "type": "operational",
                        "severity": "high",
                        "title": "Stale assets detected",
                        "status": "active",
                        "asset_count": 42,
                        "source": "system",
                        "created_at": "2026-02-28 10:00:00",
                        "updated_at": None,
                    }
                ],
            ]
        )

        result = tools["get_alerts"].fn(ctx=ctx)
        assert "Stale assets" in result or "1 total" in result

    def test_no_alerts(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(
            side_effect=[
                [],  # empty summary → 0 total
                [],  # empty alert page
            ]
        )

        result = tools["get_alerts"].fn(ctx=ctx)
        assert "No alerts" in result or "0 total" in result


class TestGetPipelineStatus:
    """Test the get_pipeline_status tool."""

    def test_idle_status(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # include_history=False: active check + last run (2 queries)
        db.execute_query = MagicMock(
            side_effect=[
                [],  # No active run
                [
                    {  # Last completed run
                        "id": 1,
                        "status": "completed",
                        "completed_at": "2026-02-28 09:05:00",
                        "duration_seconds": 300,
                        "triggered_by": "scheduled",
                        "total_assets": 1000,
                        "assets_created": 50,
                    }
                ],
            ]
        )

        result = tools["get_pipeline_status"].fn(include_history=False, ctx=ctx)
        assert "IDLE" in result
        assert "completed" in result.lower()

    def test_with_history(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # With history: active + last + history
        db.execute_query = MagicMock(
            side_effect=[
                [],  # No active run
                [
                    {  # Last completed
                        "id": 1,
                        "status": "completed",
                        "completed_at": "2026-02-28",
                        "duration_seconds": 300,
                        "triggered_by": "scheduled",
                        "total_assets": 1000,
                        "assets_created": 50,
                    }
                ],
                [
                    {  # History
                        "id": 1,
                        "status": "completed",
                        "triggered_by": "scheduled",
                        "started_at": "2026-02-28 09:00",
                        "completed_at": "2026-02-28 09:05",
                        "duration_seconds": 300,
                        "error_message": None,
                        "total_assets": 1000,
                        "batches": 5,
                    }
                ],
            ]
        )

        result = tools["get_pipeline_status"].fn(include_history=True, ctx=ctx)
        assert "Recent Runs" in result


class TestListTagRules:
    """Test the list_tag_rules tool."""

    def test_returns_rules(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "name": "Mark EOL Assets",
                    "type": "auto",
                    "enabled": True,
                    "description": "Tags assets with EOL operating systems",
                    "execution_count": 42,
                    "last_execution_assets_matched": 15,
                    "tags": {"name": "EOL"},
                    "target_filters": [
                        {"field": "os_eol_status", "op": "eq", "value": "end_of_life"}
                    ],
                }
            ]
        )

        result = tools["list_tag_rules"].fn(ctx=ctx)
        assert "Mark EOL" in result
        assert "enabled" in result.lower()

    def test_no_rules(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(return_value=[])

        result = tools["list_tag_rules"].fn(ctx=ctx)
        assert "No tag rule" in result or "no" in result.lower()


class TestGetDataQuality:
    """Test the get_data_quality tool."""

    def test_returns_quality_report(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # get_data_quality (no category): total, confidence, coverage, stale, cat_conf
        db.execute_query = MagicMock(
            side_effect=[
                [{"total": 1000}],
                [
                    {"level": "high", "count": 700, "avg_pct": 85.2},
                    {"level": "medium", "count": 200, "avg_pct": 55.1},
                    {"level": "low", "count": 100, "avg_pct": 30.5},
                ],
                [
                    {
                        "has_hostname": 950,
                        "has_ip": 900,
                        "has_mac": 800,
                        "has_serial": 600,
                        "has_os": 850,
                        "has_manufacturer": 700,
                        "has_owner": 400,
                        "has_category": 990,
                        "total": 1000,
                    }
                ],
                [{"stale_30d": 45, "stale_90d": 12, "never_seen": 3}],
                [{"category": "Unknown", "count": 50, "avg_confidence": 35.2}],
            ]
        )

        result = tools["get_data_quality"].fn(ctx=ctx)
        assert "Data Quality" in result
        assert "1,000" in result
        assert "Confidence" in result
        assert "Field Coverage" in result
        assert "Stale" in result

    def test_with_category(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        # With category: 4 queries (no cat_conf)
        db.execute_query = MagicMock(
            side_effect=[
                [{"total": 400}],
                [{"level": "high", "count": 350, "avg_pct": 86.0}],
                [
                    {
                        "has_hostname": 400,
                        "has_ip": 380,
                        "has_mac": 350,
                        "has_serial": 300,
                        "has_os": 400,
                        "has_manufacturer": 390,
                        "has_owner": 200,
                        "has_category": 400,
                        "total": 400,
                    }
                ],
                [{"stale_30d": 10, "stale_90d": 2, "never_seen": 0}],
            ]
        )

        result = tools["get_data_quality"].fn(category="Server", ctx=ctx)
        assert "Server" in result

    def test_empty_data(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(return_value=[{"total": 0}])

        result = tools["get_data_quality"].fn(ctx=ctx)
        assert "No assets" in result


class TestGetIntegrationStatus:
    """Test the get_integration_status tool."""

    def test_returns_integrations(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "name": "Intune",
                    "integration_type": "api",
                    "status": "enabled",
                    "last_sync_at": "2026-02-28 09:00:00",
                    "last_connection_test_status": "success",
                    "last_connection_test_message": None,
                    "group_name": "MDM",
                    "asset_count": 800,
                    "hours_since_sync": 2.5,
                },
                {
                    "id": 2,
                    "name": "CrowdStrike",
                    "integration_type": "api",
                    "status": "enabled",
                    "last_sync_at": "2026-02-27 12:00:00",
                    "last_connection_test_status": "success",
                    "last_connection_test_message": None,
                    "group_name": "Security",
                    "asset_count": 600,
                    "hours_since_sync": 23.0,
                },
            ]
        )

        result = tools["get_integration_status"].fn(ctx=ctx)
        assert "Intune" in result
        assert "CrowdStrike" in result
        assert "800" in result
        assert "MDM" in result

    def test_single_integration(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "name": "Jamf",
                    "integration_type": "api",
                    "status": "enabled",
                    "last_sync_at": None,
                    "last_connection_test_status": "failed",
                    "last_connection_test_message": "Connection refused",
                    "group_name": None,
                    "asset_count": 0,
                    "hours_since_sync": None,
                }
            ]
        )

        result = tools["get_integration_status"].fn(
            integration_name="Jamf", ctx=ctx
        )
        assert "Jamf" in result
        assert "never" in result.lower()
        assert "Connection refused" in result

    def test_not_found(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(return_value=[])

        result = tools["get_integration_status"].fn(
            integration_name="Nonexistent", ctx=ctx
        )
        assert "not found" in result.lower()


class TestGetAssetHistory:
    """Test the get_asset_history tool."""

    _ASSET_ROW = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "hostname": ["server01"],
        "category": "Server",
        "ip": ["10.0.0.1"],
    }

    def test_returns_changes(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            side_effect=[
                # Asset lookup
                [self._ASSET_ROW],
                # Chronicle events
                [
                    {
                        "id": 1,
                        "time": "2026-02-28 10:00:00",
                        "event_type": "attribute_change",
                        "changed_attributes": ["category", "confidence"],
                        "attribute_changes": {
                            "category": {"old": "Unknown", "new": "Server"},
                            "confidence": {"old": 0.45, "new": 0.82},
                        },
                    }
                ],
                # Alert events (now interleaved into the timeline)
                [],
            ]
        )

        result = tools["get_asset_history"].fn(
            asset_id="550e8400-e29b-41d4-a716-446655440000", ctx=ctx
        )
        assert "server01" in result
        assert "category" in result
        assert "Unknown" in result
        assert "Server" in result

    def test_asset_not_found(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db
        db.execute_query = MagicMock(return_value=[])

        result = tools["get_asset_history"].fn(
            asset_id="550e8400-e29b-41d4-a716-446655440000", ctx=ctx
        )
        assert "not found" in result.lower()

    def test_invalid_uuid(self, monitoring_tools):
        tools, ctx = monitoring_tools

        result = tools["get_asset_history"].fn(asset_id="not-a-uuid", ctx=ctx)
        assert "Invalid" in result

    def test_no_changes(self, monitoring_tools):
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            side_effect=[
                # Asset exists
                [self._ASSET_ROW],
                # No chronicle events
                [],
                # No alert events
                [],
            ]
        )

        result = tools["get_asset_history"].fn(
            asset_id="550e8400-e29b-41d4-a716-446655440000", ctx=ctx
        )
        assert "No changes or alerts" in result

    def test_event_type_from_correct_event(self, monitoring_tools):
        """Regression: attr fallback must use per-change type, not last loop var (#198)."""
        tools, ctx = monitoring_tools
        db = ctx.request_context.lifespan_context.db

        db.execute_query = MagicMock(
            side_effect=[
                [self._ASSET_ROW],
                # Two events with different event_types and no attr in the second
                [
                    {
                        "id": 1,
                        "time": "2026-02-28 10:00:00",
                        "event_type": "first_seen",
                        "changed_attributes": None,
                        "attribute_changes": None,
                    },
                    {
                        "id": 2,
                        "time": "2026-02-28 11:00:00",
                        "event_type": "attribute_change",
                        "changed_attributes": None,
                        "attribute_changes": None,
                    },
                ],
                # Alert events (now interleaved into the timeline)
                [],
            ]
        )

        result = tools["get_asset_history"].fn(
            asset_id="550e8400-e29b-41d4-a716-446655440000", ctx=ctx
        )
        # The first event should show "first_seen", not "attribute_change"
        assert "first_seen" in result
