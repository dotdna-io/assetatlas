# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ConfigSchema builder."""
import pytest
from src.modules.integrations.base.config_schema import ConfigSchema


class TestConfigSchema:
    def test_add_url_field(self):
        schema = ConfigSchema()
        schema.add_url("base_url", "API Base URL", placeholder="https://api.example.com")
        result = schema.build()
        assert "base_url" in result
        assert result["base_url"]["type"] == "url"
        assert result["base_url"]["label"] == "API Base URL"
        assert result["base_url"]["required"] is True
        assert result["base_url"]["placeholder"] == "https://api.example.com"

    def test_add_password_field(self):
        schema = ConfigSchema()
        schema.add_password("api_key", "API Key")
        result = schema.build()
        assert result["api_key"]["type"] == "password"
        assert result["api_key"]["sensitive"] is True
        assert result["api_key"]["required"] is True

    def test_add_string_field(self):
        schema = ConfigSchema()
        schema.add_string("tenant_id", "Tenant ID", required=True, description="Your tenant GUID")
        result = schema.build()
        assert result["tenant_id"]["type"] == "string"
        assert result["tenant_id"]["description"] == "Your tenant GUID"

    def test_add_number_field_with_validation(self):
        schema = ConfigSchema()
        schema.add_number("page_size", "Page Size", default=100, min_val=1, max_val=1000)
        result = schema.build()
        assert result["page_size"]["type"] == "number"
        assert result["page_size"]["default"] == 100
        assert result["page_size"]["required"] is False
        assert result["page_size"]["validation"]["min"] == 1
        assert result["page_size"]["validation"]["max"] == 1000

    def test_add_select_field(self):
        schema = ConfigSchema()
        schema.add_select("region", "Region",
                         options=[{"value": "us", "label": "US"}, {"value": "eu", "label": "EU"}],
                         default="us")
        result = schema.build()
        assert result["region"]["type"] == "select"
        assert len(result["region"]["options"]) == 2
        assert result["region"]["default"] == "us"

    def test_add_boolean_field(self):
        schema = ConfigSchema()
        schema.add_boolean("include_disabled", "Include Disabled", default=False,
                          description="Include disabled devices")
        result = schema.build()
        assert result["include_disabled"]["type"] == "boolean"
        assert result["include_disabled"]["default"] is False

    def test_build_preserves_insertion_order(self):
        schema = ConfigSchema()
        schema.add_url("base_url", "URL")
        schema.add_string("tenant_id", "Tenant")
        schema.add_password("secret", "Secret")
        result = schema.build()
        assert list(result.keys()) == ["base_url", "tenant_id", "secret"]

    def test_build_output_matches_existing_format(self):
        schema = ConfigSchema()
        schema.add_url("base_url", "API Base URL", required=True,
                      placeholder="https://api.example.com", description="The API endpoint")
        schema.add_password("client_secret", "Client Secret", required=True)
        schema.add_number("page_size", "Page Size", default=100, min_val=1, max_val=999,
                         description="Records per page")
        result = schema.build()
        assert isinstance(result, dict)
        for field_name, field_spec in result.items():
            assert "type" in field_spec
            assert "label" in field_spec
            assert "required" in field_spec

    def test_description_key_used_not_help_text(self):
        schema = ConfigSchema()
        schema.add_string("field", "Field", description="Help info")
        result = schema.build()
        assert "description" in result["field"]
        assert "help_text" not in result["field"]
