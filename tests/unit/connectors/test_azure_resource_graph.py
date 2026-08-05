# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for AzureResourceGraphConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for $skipToken pagination and query preset handling.
"""

import json
import logging
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.azure_resource_graph_connector import (
    AzureResourceGraphConnector,
    API_ENDPOINT,
    MAX_PAGE_SIZE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGINATION_PAGES,
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


class TestAzureResourceGraphTruncation:
    """A $skipToken chain stopped by the page cap must say so — otherwise an
    estate larger than cap × page_size silently under-reports, and the
    pipeline rebuild reads the shortfall as deleted assets (#1334)."""

    TRUNCATION_CONFIG = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "query_preset": "virtual_machines",
        "page_size": 1000,
        "max_pagination_pages": 2,
    }

    def _register_token(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)

    @responses.activate
    def test_cap_hit_records_one_warning_naming_the_preset(self, caplog):
        self._register_token()
        # Every page hands back a fresh $skipToken: more data waiting.
        page = {
            "totalRecords": 3, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
            "$skipToken": "next",
        }
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page, status=200)
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page, status=200)

        connector = AzureResourceGraphConnector(self.TRUNCATION_CONFIG)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert len(connector.collection_warnings) == 1
        message = connector.collection_warnings[0]
        assert "virtual_machines" in message
        assert "2-page cap" in message
        assert "truncat" in message.lower()
        assert message in caplog.text

    @responses.activate
    def test_custom_query_warning_names_custom_query(self, caplog):
        self._register_token()
        page = {
            "totalRecords": 3, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
            "$skipToken": "next",
        }
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page, status=200)
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json=page, status=200)

        config = {**self.TRUNCATION_CONFIG,
                  "resource_query": "Resources | project id, name"}
        connector = AzureResourceGraphConnector(config)
        with caplog.at_level(logging.WARNING):
            list(connector.fetch_data("test-batch"))

        assert len(connector.collection_warnings) == 1
        assert "custom query" in connector.collection_warnings[0]

    @responses.activate
    def test_end_of_data_records_no_warning(self, caplog):
        """The off-by-one: the page that fills the cap clears $skipToken —
        the data, not the cap, ended the loop."""
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 2, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
            "$skipToken": "page-2",
        }, status=200)
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 2, "count": 1,
            "data": [{"id": "/sub/rg/vm2", "name": "vm2"}],
        }, status=200)

        connector = AzureResourceGraphConnector(self.TRUNCATION_CONFIG)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert connector.collection_warnings == []

    def test_max_pagination_pages_configurable(self):
        config = {k: v for k, v in self.TRUNCATION_CONFIG.items()
                  if k != "max_pagination_pages"}
        connector = AzureResourceGraphConnector(config)
        assert connector.max_pagination_pages == MAX_PAGINATION_PAGES
        connector = AzureResourceGraphConnector({**config, "max_pagination_pages": 10})
        assert connector.max_pagination_pages == 10

    def test_config_schema_exposes_max_pagination_pages(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        field = schema["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["default"] == MAX_PAGINATION_PAGES


# ---------------------------------------------------------------------------
# Coverage ported from the retired flat suite
# (tests/unit/test_azure_resource_graph_connector.py, #1358)
# ---------------------------------------------------------------------------


class TestAzureResourceGraphConfigSchemaDetails:
    """Field-level config-schema contracts the base-class shape checks don't pin."""

    def test_required_auth_fields_present(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        for field in ("tenant_id", "client_id", "client_secret"):
            assert field in schema, f"Missing config field: {field}"
        assert schema["client_secret"]["type"] == "password"
        assert schema["client_secret"]["sensitive"] is True

    def test_tenant_id_has_uuid_validation(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert "pattern" in schema["tenant_id"]["validation"]

    def test_subscription_ids_and_resource_query_optional(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema["subscription_ids"]["required"] is False
        assert schema["resource_query"]["required"] is False

    def test_query_preset_has_all_options(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        values = [o["value"] for o in schema["query_preset"]["options"]]
        for preset in (
            "virtual_machines",
            "virtual_machines_basic",
            "infrastructure",
            "all_resources",
        ):
            assert preset in values

    def test_page_size_has_validation(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema["page_size"]["validation"]["min"] == 1
        assert schema["page_size"]["validation"]["max"] == MAX_PAGE_SIZE


class TestAzureResourceGraphConstructorDetails:
    base_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
    }

    def _make(self, **overrides):
        return AzureResourceGraphConnector({**self.base_config, **overrides})

    def test_metadata(self):
        assert AzureResourceGraphConnector.integration_type == "azure_resource_graph"
        assert AzureResourceGraphConnector.display_name == "Azure Resource Graph"
        assert AzureResourceGraphConnector.category == "Cloud Platform"

    def test_vm_query_contains_ip_join(self):
        """Default VM preset joins NICs and public IPs."""
        query = self._make().resource_query
        assert "networkinterfaces" in query.lower()
        assert "publicipaddresses" in query.lower()
        assert "privateIPAddress" in query
        assert "publicIpAddress" in query
        assert "join kind=leftouter" in query

    def test_infrastructure_preset_query(self):
        query = self._make(query_preset="infrastructure").resource_query
        assert "storageaccounts" in query
        assert "networksecuritygroups" in query
        assert "desktopvirtualization" in query

    def test_all_resources_preset_query(self):
        connector = self._make(query_preset="all_resources")
        assert connector.resource_query == QUERY_PRESETS["all_resources"]

    def test_custom_query_schema_includes_all_fields(self):
        """Custom query exposes both VM and raw fields for mapping."""
        connector = self._make(resource_query="Resources | project id, name")
        names = {f["name"] for f in connector.get_schema()["fields"]}
        assert "computerName" in names
        assert "properties" in names

    def test_subscription_ids_empty_when_blank(self):
        assert self._make().subscription_ids == []

    def test_page_size_minimum_is_one(self):
        assert self._make(page_size=0).page_size == 1

    def test_page_size_default_when_unset(self):
        assert self._make().page_size == DEFAULT_PAGE_SIZE

    def test_oauth2_scope_is_management_azure(self):
        connector = self._make()
        assert connector.auth.scope == "https://management.azure.com/.default"


class TestAzureResourceGraphSchemaDetails:
    base_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
    }

    def test_schema_source_and_core_fields(self):
        connector = AzureResourceGraphConnector(self.base_config)
        schema = connector.get_schema()
        assert schema["source"] == "azure_resource_graph"
        names = {f["name"] for f in schema["fields"]}
        for field in (
            "id", "name", "type", "location", "resourceGroup", "subscriptionId",
        ):
            assert field in names, f"Missing field: {field}"

    def test_vm_basic_preset_has_nic_count_no_ip_fields(self):
        connector = AzureResourceGraphConnector(
            {**self.base_config, "query_preset": "virtual_machines_basic"}
        )
        names = {f["name"] for f in connector.get_schema()["fields"]}
        assert "nicCount" in names
        assert "computerName" in names
        # The basic preset does not run the NIC/public-IP joins
        assert "privateIPAddress" not in names

    def test_infrastructure_preset_excludes_vm_fields(self):
        connector = AzureResourceGraphConnector(
            {**self.base_config, "query_preset": "infrastructure"}
        )
        names = {f["name"] for f in connector.get_schema()["fields"]}
        assert "properties" in names
        assert "computerName" not in names

    def test_all_resources_preset_has_raw_properties(self):
        connector = AzureResourceGraphConnector(
            {**self.base_config, "query_preset": "all_resources"}
        )
        names = {f["name"] for f in connector.get_schema()["fields"]}
        assert "properties" in names
        assert "identity" in names
        assert "computerName" not in names


class TestAzureResourceGraphConnectionDetails:
    """test_connection message/error contracts, exercised over mocked HTTP."""

    mock_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "query_preset": "virtual_machines",
        "page_size": 1000,
    }

    def _register_token(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)

    @responses.activate
    def test_connection_reports_resource_count(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 1247, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
        }, status=200)

        result = AzureResourceGraphConnector(self.mock_config).test_connection()

        assert result["success"] is True
        assert "1,247" in result["message"]
        assert result["metadata"]["resource_count"] == 1247

    @responses.activate
    def test_connection_zero_resources(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 0, "count": 0, "data": [],
        }, status=200)

        result = AzureResourceGraphConnector(self.mock_config).test_connection()

        assert result["success"] is True
        assert result["metadata"]["resource_count"] == 0

    @responses.activate
    def test_connection_sends_configured_query_top_and_subscriptions(self):
        """test_connection runs the configured query with $top=1."""
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 3, "count": 1, "data": [{"id": "r1"}],
        }, status=200)

        config = {**self.mock_config, "subscription_ids": "sub-aaa, sub-bbb"}
        connector = AzureResourceGraphConnector(config)
        result = connector.test_connection()

        assert result["success"] is True
        body = json.loads(responses.calls[1].request.body)
        assert body["query"] == connector.resource_query
        assert body["options"]["$top"] == 1
        assert body["options"]["resultFormat"] == "objectArray"
        assert body["subscriptions"] == ["sub-aaa", "sub-bbb"]

    @responses.activate
    def test_connection_401_names_auth_failure(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL,
                      json={"error": "unauthorized"}, status=401)

        result = AzureResourceGraphConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "Authentication failed" in result["message"]

    @responses.activate
    def test_connection_403_names_permission_error(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL,
                      json={"error": "forbidden"}, status=403)

        result = AzureResourceGraphConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "permission" in result["message"].lower()

    @responses.activate
    def test_connection_network_error_returns_failure(self):
        # Only the token endpoint is mocked — the Resource Graph call matches
        # no registered response, so `responses` raises ConnectionError.
        self._register_token()

        result = AzureResourceGraphConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "Connection failed" in result["message"]


class TestAzureResourceGraphRequestBody:
    """Request-shape contracts from the removed ``_query_resource_graph`` unit
    tests, re-exercised at the HTTP layer."""

    mock_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "query_preset": "virtual_machines",
        "page_size": 500,
        "subscription_ids": "sub-aaa, sub-bbb",
    }

    def _register_token(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)

    @responses.activate
    def test_fetch_body_has_subscriptions_top_and_format(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 1, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
        }, status=200)

        connector = AzureResourceGraphConnector(self.mock_config)
        records = list(connector.fetch_data("batch-body"))

        assert len(records) == 1
        body = json.loads(responses.calls[1].request.body)
        assert body["subscriptions"] == ["sub-aaa", "sub-bbb"]
        assert body["options"]["$top"] == 500
        assert body["options"]["resultFormat"] == "objectArray"
        # First page of a query must not carry a $skipToken
        assert "$skipToken" not in body["options"]

    @responses.activate
    def test_empty_data_terminates_fetch(self):
        """An empty first page ends the loop after exactly one request."""
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 0, "count": 0, "data": [],
        }, status=200)

        connector = AzureResourceGraphConnector(self.mock_config)
        records = list(connector.fetch_data("batch-empty"))

        assert records == []
        post_calls = [c for c in responses.calls if c.request.method == "POST"
                      and "ResourceGraph" in c.request.url]
        assert len(post_calls) == 1

    @responses.activate
    def test_skip_token_sent_only_from_second_page(self):
        self._register_token()
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 2, "count": 1,
            "data": [{"id": "/sub/rg/vm1", "name": "vm1"}],
            "$skipToken": "token-page-2",
        }, status=200)
        responses.add(responses.POST, RESOURCE_GRAPH_URL, json={
            "totalRecords": 2, "count": 1,
            "data": [{"id": "/sub/rg/vm2", "name": "vm2"}],
        }, status=200)

        config = {k: v for k, v in self.mock_config.items()
                  if k != "subscription_ids"}
        connector = AzureResourceGraphConnector(config)
        records = list(connector.fetch_data("batch-skip"))

        assert len(records) == 2
        first_body = json.loads(responses.calls[1].request.body)
        second_body = json.loads(responses.calls[2].request.body)
        assert "$skipToken" not in first_body["options"]
        assert second_body["options"]["$skipToken"] == "token-page-2"
        # No subscriptions configured — key must be absent from the body
        assert "subscriptions" not in first_body
