# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP asset tools with mocked DB."""

import json
from unittest.mock import patch

import pytest

from mcp.server.fastmcp import FastMCP

from src.mcp.tools.assets import register_tools


@pytest.fixture
def asset_tools(mock_ctx):
    """Register asset tools on a test server and return tool functions + mock ctx."""
    mcp = FastMCP("test")
    register_tools(mcp)
    tools = mcp._tool_manager._tools
    return tools, mock_ctx


class TestSearchAssets:
    """Test the search_assets tool."""

    def test_returns_results(self, asset_tools):
        tools, ctx = asset_tools

        # Mock the AssetRepository.search_assets
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "hostname": ["web-server-01"],
                    "category": "Server",
                    "match_field": "hostname",
                }
            ]
            fn = tools["search_assets"].fn
            result = fn(query="web-server", limit=10, ctx=ctx)

        assert "web-server-01" in result
        assert "1 asset(s)" in result

    def test_no_results(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = []
            result = tools["search_assets"].fn(query="nonexistent", ctx=ctx)

        assert "No assets found" in result

    def test_limit_clamped(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.search_assets.return_value = []
            tools["search_assets"].fn(query="test", limit=999, ctx=ctx)
            call_args = MockRepo.return_value.search_assets.call_args
            assert call_args[1]["limit"] == 100


class TestGetAsset:
    """Test the get_asset tool."""

    def test_returns_asset_detail(self, asset_tools):
        tools, ctx = asset_tools
        test_uuid = "550e8400-e29b-41d4-a716-446655440000"
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_asset_by_id.return_value = {
                "id": test_uuid,
                "hostname": ["myhost"],
                "ip": ["10.0.0.1"],
                "category": "Desktop",
                "subcategory": "Laptop",
                "confidence": 0.85,
                "data_source": ["Intune", "CrowdStrike"],
            }
            repo.get_asset_tags.return_value = [{"tag_name": "Compliant"}]
            repo.get_asset_providers.return_value = []

            result = tools["get_asset"].fn(asset_id=test_uuid, ctx=ctx)

        assert "myhost" in result
        assert "10.0.0.1" in result
        assert "Desktop" in result
        assert "85%" in result
        assert "Compliant" in result

    def test_not_found(self, asset_tools):
        tools, ctx = asset_tools
        missing_uuid = "00000000-0000-0000-0000-000000000000"
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_asset_by_id.return_value = None
            result = tools["get_asset"].fn(asset_id=missing_uuid, ctx=ctx)

        assert "not found" in result.lower()

    def test_invalid_uuid(self, asset_tools):
        tools, ctx = asset_tools
        result = tools["get_asset"].fn(asset_id="not-a-uuid", ctx=ctx)
        assert "invalid" in result.lower()


class TestListAssets:
    """Test the list_assets tool."""

    def test_returns_paginated_results(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [
                    {"id": "a1", "hostname": ["host1"], "category": "Server"},
                    {"id": "a2", "hostname": ["host2"], "category": "Desktop"},
                ],
                10,  # total count
            )
            result = tools["list_assets"].fn(
                page=1,
                limit=2,
                ctx=ctx,
            )

        assert "10 total" in result
        assert "host1" in result
        assert "page=2" in result

    def test_invalid_filter_json(self, asset_tools):
        tools, ctx = asset_tools
        result = tools["list_assets"].fn(
            filters="not valid json",
            ctx=ctx,
        )
        assert "Error" in result

    def test_filters_not_array(self, asset_tools):
        tools, ctx = asset_tools
        result = tools["list_assets"].fn(
            filters='{"field":"category"}',
            ctx=ctx,
        )
        assert "Error" in result

    def test_valid_filters_passed(self, asset_tools):
        tools, ctx = asset_tools
        # The tool accepts the documented {field, op, value} shape and
        # normalizes it into the repo's internal {field_id: {...}, op, value}
        # form, title-casing the field name via the static-field alias map.
        filters_json = json.dumps(
            [{"field": "category", "op": "eq", "value": "Server"}]
        )
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            tools["list_assets"].fn(filters=filters_json, ctx=ctx)
            call_args = MockRepo.return_value.list_assets.call_args
            assert call_args[1]["filters"] == [
                {"field_id": {"kind": "static", "name": "Category"},
                 "op": "eq", "value": "Server"}
            ]


class TestAggregateAssets:
    """Test the aggregate_assets tool."""

    def test_1d_aggregation(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["Server", "Desktop"],
                "columns": [],
                "data": {
                    "Server": {"count": 100},
                    "Desktop": {"count": 50},
                },
                "grand_total": 150,
            }
            result = tools["aggregate_assets"].fn(
                row_field="category",
                ctx=ctx,
            )

        assert "Server" in result
        assert "Desktop" in result
        assert "150" in result

    def test_2d_aggregation(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["Server"],
                "columns": ["active", "end_of_life"],
                "data": {
                    "Server": {"active": 80, "end_of_life": 20},
                },
                "grand_total": 100,
            }
            result = tools["aggregate_assets"].fn(
                row_field="category",
                col_field="os_eol_status",
                ctx=ctx,
            )

        assert "active" in result
        assert "end_of_life" in result


class TestGetFieldValues:
    """Test the get_field_values tool."""

    def test_returns_values(self, asset_tools):
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_field_values.return_value = (
                [
                    {"value": "Windows 10", "count": 500},
                    {"value": "Windows 11", "count": 300},
                ],
                2,
            )
            result = tools["get_field_values"].fn(
                field="os_normalized",
                ctx=ctx,
            )

        assert "Windows 10" in result
        assert "500" in result
        assert "2 distinct" in result

    def test_no_values(self, asset_tools):
        """Unknown field name short-circuits before the repo is called,
        returning an actionable hint. A bare name that doesn't resolve
        used to reach the repo and print "No values found", which hid the
        real problem (model typed a field the system has no record of)."""
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_field_values.return_value = ([], 0)
            result = tools["get_field_values"].fn(
                field="nonexistent",
                ctx=ctx,
            )

        assert "Unknown field" in result
        # Ensure the hint steers the caller toward a recovery path rather
        # than leaving them to guess — either a known display name or a
        # semantic_suggest round-trip.
        assert "semantic_suggest" in result
        MockRepo.return_value.get_field_values.assert_not_called()

    def test_no_values_for_known_field(self, asset_tools):
        """When the field resolves but the filter set produces zero rows,
        surface the repo's empty-result message so the caller distinguishes
        'field is unknown' from 'field is known but empty here'."""
        tools, ctx = asset_tools
        with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
            MockRepo.return_value.get_field_values.return_value = ([], 0)
            result = tools["get_field_values"].fn(
                field="os_normalized",
                ctx=ctx,
            )

        assert "No values found" in result
