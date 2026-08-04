# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP resources."""

from unittest.mock import MagicMock, patch

from mcp.server.fastmcp import FastMCP

from src.mcp.resources.schema import (
    STANDARD_FIELDS,
    register_resources as register_schema,
)
from src.mcp.resources.config import register_resources as register_config
from src.mcp.resources.stats import register_resources as register_stats
from src.mcp.resources.docs import register_resources as register_docs


class TestSchemaResources:
    """Test schema resource registration and output."""

    def test_fields_resource_static_content(self):
        """Fields resource returns static field list without DB."""
        mcp = FastMCP("test")
        with patch("src.mcp.resources.schema.get_app_context", return_value=None):
            register_schema(mcp)
        resource = mcp._resource_manager._resources["assetatlas://schema/fields"]
        result = resource.fn()
        assert "Field Reference" in result
        assert "hostname" in result
        assert "string_array" in result

    def test_fields_resource_with_extended_attrs(
        self, mock_db, app_context_with_mock_db
    ):
        """Fields resource includes extended attributes when DB available."""
        mock_db.execute_query = MagicMock(
            side_effect=[
                # Sources
                [{"source_name": "Intune"}],
                # Sample fields for Intune
                [{"field_name": "deviceType_s"}, {"field_name": "complianceState_s"}],
            ]
        )

        mcp = FastMCP("test")
        register_schema(mcp)
        result = mcp._resource_manager._resources["assetatlas://schema/fields"].fn()
        assert "Intune" in result
        assert "deviceType_s" in result

    def test_categories_resource(self, mock_db, app_context_with_mock_db):
        mock_db.execute_query = MagicMock(
            return_value=[
                {"category": "Server", "subcategory": "Physical", "count": 100},
                {"category": "Server", "subcategory": "Virtual Machine", "count": 200},
                {"category": "Desktop", "subcategory": "Laptop", "count": 50},
            ]
        )

        mcp = FastMCP("test")
        register_schema(mcp)
        result = mcp._resource_manager._resources["assetatlas://schema/categories"].fn()
        assert "Server" in result
        assert "300" in result  # total for Server category
        assert "Laptop" in result

    def test_categories_no_db(self):
        mcp = FastMCP("test")
        with patch("src.mcp.resources.schema.get_app_context", return_value=None):
            register_schema(mcp)
        result = mcp._resource_manager._resources["assetatlas://schema/categories"].fn()
        assert "not available" in result.lower()

    def test_operators_resource(self):
        """Operators resource is fully static."""
        mcp = FastMCP("test")
        register_schema(mcp)
        result = mcp._resource_manager._resources["assetatlas://schema/operators"].fn()
        assert "eq" in result
        assert "contains" in result
        assert "string" in result


class TestConfigResources:
    """Test config resources."""

    def test_integrations_resource(self, mock_db, app_context_with_mock_db):
        mock_db.execute_query = MagicMock(
            return_value=[
                {
                    "name": "CrowdStrike",
                    "integration_type": "api",
                    "status": "active",
                    "last_sync_at": "2026-02-28 10:00:00",
                    "group_name": "Security",
                    "asset_count": 500,
                }
            ]
        )

        mcp = FastMCP("test")
        register_config(mcp)
        result = mcp._resource_manager._resources[
            "assetatlas://config/integrations"
        ].fn()
        assert "CrowdStrike" in result
        assert "active" in result
        assert "500" in result

    def test_integrations_no_db(self):
        mcp = FastMCP("test")
        with patch("src.mcp.resources.config.get_app_context", return_value=None):
            register_config(mcp)
        result = mcp._resource_manager._resources[
            "assetatlas://config/integrations"
        ].fn()
        assert "not available" in result.lower()

    def test_tag_rules_resource(self, mock_db, app_context_with_mock_db):
        mock_db.execute_query = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "name": "EOL Detection",
                    "type": "auto",
                    "enabled": True,
                    "description": "Detects end-of-life OS",
                    "tags": {"name": "EOL"},
                    "target_filters": [
                        {"field": "os_eol_status", "op": "eq", "value": "end_of_life"}
                    ],
                    "execution_count": 10,
                    "last_execution_assets_matched": 5,
                }
            ]
        )

        mcp = FastMCP("test")
        register_config(mcp)
        result = mcp._resource_manager._resources["assetatlas://config/tag-rules"].fn()
        assert "EOL Detection" in result
        assert "enabled" in result


class TestStatsResources:
    """Test stats resources."""

    def test_summary_resource(self, mock_db, app_context_with_mock_db):
        mock_db.execute_query = MagicMock(
            side_effect=[
                # Totals
                [
                    {
                        "total_assets": 1000,
                        "high_confidence": 700,
                        "medium_confidence": 200,
                        "low_confidence": 100,
                        "avg_confidence": 0.823,
                    }
                ],
                # Categories
                [
                    {"category": "Server", "count": 400},
                    {"category": "Desktop", "count": 600},
                ],
                # Active sources
                [{"source_count": 5}],
                # Stale
                [{"stale_count": 42}],
            ]
        )

        mcp = FastMCP("test")
        register_stats(mcp)
        result = mcp._resource_manager._resources["assetatlas://stats/summary"].fn()
        assert "1,000" in result
        assert "Server" in result
        assert "42" in result

    def test_data_sources_resource(self, mock_db, app_context_with_mock_db):
        mock_db.execute_query = MagicMock(
            side_effect=[
                # Sources
                [
                    {
                        "name": "Intune",
                        "integration_type": "api",
                        "status": "active",
                        "last_sync_at": "2026-02-28",
                        "asset_count": 800,
                        "last_data": "2026-02-28",
                    }
                ],
                # Total assets
                [{"total": 1000}],
            ]
        )

        mcp = FastMCP("test")
        register_stats(mcp)
        result = mcp._resource_manager._resources[
            "assetatlas://stats/data-sources"
        ].fn()
        assert "Intune" in result
        assert "800" in result
        assert "80.0%" in result


class TestDocsResources:
    """Test docs resources."""

    def test_query_guide_content(self):
        mcp = FastMCP("test")
        register_docs(mcp)
        result = mcp._resource_manager._resources["assetatlas://docs/query-guide"].fn()
        assert "Query Guide" in result
        assert "search_assets" in result
        assert "list_assets" in result
        assert "aggregate_assets" in result
        assert "eq" in result
        assert "hostname" in result

    def test_query_guide_includes_all_standard_fields(self):
        mcp = FastMCP("test")
        register_docs(mcp)
        result = mcp._resource_manager._resources["assetatlas://docs/query-guide"].fn()
        for field_key, _, _ in STANDARD_FIELDS:
            assert field_key in result, f"Missing field: {field_key}"


class TestResourceRegistration:
    """Test that all resources register correctly."""

    def test_all_8_resources_registered(self):
        """The full server registers exactly 8 resources."""
        from src.mcp.server import mcp as server_mcp

        resources = server_mcp._resource_manager._resources
        assert len(resources) == 8

    def test_no_templates(self):
        """No resources should be registered as templates."""
        from src.mcp.server import mcp as server_mcp

        templates = server_mcp._resource_manager._templates
        assert len(templates) == 0

    def test_all_uris_present(self):
        from src.mcp.server import mcp as server_mcp

        resources = server_mcp._resource_manager._resources
        expected = [
            "assetatlas://schema/fields",
            "assetatlas://schema/categories",
            "assetatlas://schema/operators",
            "assetatlas://config/integrations",
            "assetatlas://config/tag-rules",
            "assetatlas://stats/summary",
            "assetatlas://stats/data-sources",
            "assetatlas://docs/query-guide",
        ]
        for uri in expected:
            assert uri in resources, f"Missing resource: {uri}"
