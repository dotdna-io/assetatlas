# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP workflow prompts."""

import pytest

from mcp.server.fastmcp import FastMCP

from src.mcp.prompts.workflows import register_prompts


@pytest.fixture
def prompt_server():
    """Register prompts on a test server."""
    mcp = FastMCP("test")
    register_prompts(mcp)
    return mcp._prompt_manager._prompts


class TestPromptRegistration:
    """Test that all prompts register correctly."""

    def test_all_5_prompts_registered(self, prompt_server):
        assert len(prompt_server) == 5

    def test_prompt_names(self, prompt_server):
        expected = [
            "investigate_asset",
            "compliance_report",
            "data_quality_audit",
            "source_health_check",
            "asset_inventory_summary",
        ]
        for name in expected:
            assert name in prompt_server, f"Missing prompt: {name}"


class TestInvestigateAsset:
    """Test investigate_asset prompt."""

    def test_includes_identifier(self, prompt_server):
        prompt = prompt_server["investigate_asset"]
        messages = prompt.fn(identifier="web-server-01")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "web-server-01" in messages[0]["content"]

    def test_references_tools(self, prompt_server):
        prompt = prompt_server["investigate_asset"]
        content = prompt.fn(identifier="test")[0]["content"]
        assert "search_assets" in content
        assert "get_asset" in content
        assert "get_asset_history" in content

    def test_has_arguments(self, prompt_server):
        prompt = prompt_server["investigate_asset"]
        arg_names = [a.name for a in prompt.arguments]
        assert "identifier" in arg_names


class TestComplianceReport:
    """Test compliance_report prompt."""

    def test_default_params(self, prompt_server):
        prompt = prompt_server["compliance_report"]
        messages = prompt.fn()
        content = messages[0]["content"]
        assert "compliance" in content.lower()
        assert "list_tag_rules" in content
        assert "aggregate_assets" in content

    def test_with_category(self, prompt_server):
        prompt = prompt_server["compliance_report"]
        content = prompt.fn(category="Server")[0]["content"]
        assert "Server" in content

    def test_with_scope_untagged(self, prompt_server):
        prompt = prompt_server["compliance_report"]
        content = prompt.fn(scope="untagged")[0]["content"]
        assert "missing" in content.lower() or "untagged" in content.lower()


class TestDataQualityAudit:
    """Test data_quality_audit prompt."""

    def test_references_quality_tools(self, prompt_server):
        prompt = prompt_server["data_quality_audit"]
        content = prompt.fn()[0]["content"]
        assert "get_data_quality" in content
        assert "confidence" in content.lower()
        assert "get_field_values" in content

    def test_with_category(self, prompt_server):
        prompt = prompt_server["data_quality_audit"]
        content = prompt.fn(category="Desktop")[0]["content"]
        assert "Desktop" in content


class TestSourceHealthCheck:
    """Test source_health_check prompt."""

    def test_no_args_required(self, prompt_server):
        prompt = prompt_server["source_health_check"]
        messages = prompt.fn()
        assert len(messages) == 1
        content = messages[0]["content"]
        assert "get_integration_status" in content
        assert "get_pipeline_status" in content

    def test_references_resources(self, prompt_server):
        prompt = prompt_server["source_health_check"]
        content = prompt.fn()[0]["content"]
        assert "assetatlas://config/integrations" in content
        assert "assetatlas://stats/data-sources" in content


class TestAssetInventorySummary:
    """Test asset_inventory_summary prompt."""

    def test_brief_format(self, prompt_server):
        prompt = prompt_server["asset_inventory_summary"]
        content = prompt.fn(format="brief")[0]["content"]
        assert "brief" in content.lower()

    def test_detailed_format(self, prompt_server):
        prompt = prompt_server["asset_inventory_summary"]
        content = prompt.fn(format="detailed")[0]["content"]
        assert "detailed" in content.lower()

    def test_references_resources_and_tools(self, prompt_server):
        prompt = prompt_server["asset_inventory_summary"]
        content = prompt.fn()[0]["content"]
        assert "assetatlas://stats/summary" in content
        assert "aggregate_assets" in content
        assert "get_data_quality" in content
        assert "get_alerts" in content
