# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for AzureResourceGraphConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for $skipToken pagination and query preset handling.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.azure_resource_graph_connector import (
    AzureResourceGraphConnector,
    API_ENDPOINT,
    QUERY_PRESETS,
    DEFAULT_QUERY_PRESET,
)


TOKEN_URL = "https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token"
# The full endpoint includes the api-version query param
RESOURCE_GRAPH_URL = API_ENDPOINT


class TestAzureResourceGraphConnector(ConnectorTestCase):
    connector_class = AzureResourceGraphConnector
    mock_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "query_preset": "virtual_machines",
        "page_size": 1000,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default OAuth token + single-page resource response for all tests."""
        mock_http.register_auth_success(TOKEN_URL)
        mock_http.register_from_fixture(
            "POST", RESOURCE_GRAPH_URL, "azure_resource_graph/resources_single.json"
        )

    # -------------------------------------------------------------------------
    # Connector-specific: $skipToken pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_skip_token(self):
        """Records from both pages are yielded when $skipToken is present."""
        fixtures = FIXTURES_DIR / "azure_resource_graph"

        with open(fixtures / "resources_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "resources_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        # Page 1 — includes $skipToken
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page1, status=200)
        # Page 2 — no $skipToken, terminates pagination
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page2, status=200)

        connector = AzureResourceGraphConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 resources, page2 has 1 → 3 total
        assert len(records) == 3
        names = {r["name"] for r in records}
        assert "vm-web-01" in names
        assert "vm-db-01" in names
        assert "arc-server-01" in names

    @responses.activate
    def test_pagination_single_page_no_skip_token(self):
        """When there is no $skipToken, only one page is fetched."""
        with open(FIXTURES_DIR / "azure_resource_graph" / "resources_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=single, status=200)

        connector = AzureResourceGraphConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: query preset
    # -------------------------------------------------------------------------

    def test_default_query_preset(self):
        """Default query preset resolves to 'virtual_machines'."""
        config = {**self.mock_config}
        config.pop("query_preset", None)
        connector = AzureResourceGraphConnector(config)
        assert connector.query_preset == DEFAULT_QUERY_PRESET
        assert connector.resource_query == QUERY_PRESETS[DEFAULT_QUERY_PRESET]

    def test_custom_query_overrides_preset(self, mock_http):
        """When resource_query is set, query_preset is 'custom'."""
        config = {
            **self.mock_config,
            "resource_query": "Resources | where type =~ 'microsoft.storage/storageaccounts'",
        }
        connector = AzureResourceGraphConnector(config)
        assert connector.query_preset == "custom"
        assert "storageaccounts" in connector.resource_query

    def test_subscription_ids_parsed_from_string(self, mock_http):
        """Comma-separated subscription_ids string is parsed to a list."""
        config = {
            **self.mock_config,
            "subscription_ids": "sub-aaa, sub-bbb, sub-ccc",
        }
        connector = AzureResourceGraphConnector(config)
        assert connector.subscription_ids == ["sub-aaa", "sub-bbb", "sub-ccc"]

    def test_subscription_ids_accepts_list(self, mock_http):
        """subscription_ids accepts a pre-parsed list directly."""
        config = {
            **self.mock_config,
            "subscription_ids": ["sub-aaa", "sub-bbb"],
        }
        connector = AzureResourceGraphConnector(config)
        assert connector.subscription_ids == ["sub-aaa", "sub-bbb"]

    def test_page_size_clamped_to_max(self, mock_http):
        """Page size is clamped to MAX_PAGE_SIZE (1000)."""
        from src.modules.integrations.connectors.azure_resource_graph_connector import MAX_PAGE_SIZE
        config = {**self.mock_config, "page_size": 99999}
        connector = AzureResourceGraphConnector(config)
        assert connector.page_size == MAX_PAGE_SIZE

    # -------------------------------------------------------------------------
    # Connector-specific: missing required config
    # -------------------------------------------------------------------------

    def test_missing_tenant_id_raises(self):
        """ValueError raised when tenant_id is absent."""
        config = {k: v for k, v in self.mock_config.items() if k != "tenant_id"}
        with pytest.raises(ValueError, match="Tenant ID"):
            AzureResourceGraphConnector(config)

    def test_missing_client_id_raises(self):
        """ValueError raised when client_id is absent."""
        config = {k: v for k, v in self.mock_config.items() if k != "client_id"}
        with pytest.raises(ValueError, match="Client ID"):
            AzureResourceGraphConnector(config)

    def test_missing_client_secret_raises(self):
        """ValueError raised when client_secret is absent."""
        config = {k: v for k, v in self.mock_config.items() if k != "client_secret"}
        with pytest.raises(ValueError, match="Client Secret"):
            AzureResourceGraphConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection
    # -------------------------------------------------------------------------

    def test_metadata_source_is_azure_resource_graph(self, connector, mock_http):
        """The _source metadata field must be 'azure_resource_graph'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "azure_resource_graph"

    # -------------------------------------------------------------------------
    # Connector-specific: get_schema varies by preset
    # -------------------------------------------------------------------------

    def test_schema_virtual_machines_includes_ip_fields(self, connector):
        """VM preset schema includes IP address fields."""
        schema = connector.get_schema()
        field_names = {f["name"] for f in schema["fields"]}
        assert "privateIPAddress" in field_names
        assert "publicIpAddress" in field_names

    def test_schema_infrastructure_includes_properties(self, mock_http):
        """Infrastructure preset schema includes raw 'properties' field."""
        config = {**self.mock_config, "query_preset": "infrastructure"}
        connector = AzureResourceGraphConnector(config)
        schema = connector.get_schema()
        field_names = {f["name"] for f in schema["fields"]}
        assert "properties" in field_names

    def test_schema_all_resources_includes_identity(self, mock_http):
        """all_resources preset schema includes 'identity' field."""
        config = {**self.mock_config, "query_preset": "all_resources"}
        connector = AzureResourceGraphConnector(config)
        schema = connector.get_schema()
        field_names = {f["name"] for f in schema["fields"]}
        assert "identity" in field_names
