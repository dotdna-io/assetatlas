# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for LogAnalyticsConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for OAuth2 auth, KQL query execution, _workspace_id metadata,
and column-to-dict row conversion.
"""

import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.log_analytics_connector import LogAnalyticsConnector
from src.modules.integrations.base import OAuth2ClientCredentials


TENANT_ID = "test-tenant-id"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
API_BASE = "https://api.loganalytics.azure.com"
WORKSPACE_ID = "test-workspace-id"
QUERY_URL = f"{API_BASE}/v1/workspaces/{WORKSPACE_ID}/query"


class TestLogAnalyticsConnector(ConnectorTestCase):
    connector_class = LogAnalyticsConnector
    mock_config = {
        "tenant_id": TENANT_ID,
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "workspace_id": WORKSPACE_ID,
        "kql_query": "Heartbeat | summarize by Computer, ComputerIP, OSType",
        "timespan": "P1D",
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default OAuth token + query responses for all tests."""
        # OAuth2 token
        mock_http.register_auth_success(TOKEN_URL)
        # Single-row query result
        mock_http.register_from_fixture("POST", QUERY_URL, "log_analytics/query_results_single.json")
        # For test_connection: heartbeat + count query
        mock_http.register_auth_success(TOKEN_URL)
        mock_http.register_from_fixture("POST", QUERY_URL, "log_analytics/heartbeat_test.json")
        mock_http.register_json("POST", QUERY_URL, {
            "tables": [{"name": "PrimaryResult", "columns": [{"name": "Count", "type": "long"}], "rows": [[42]]}]
        })

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_oauth2_client_credentials(self):
        connector = LogAnalyticsConnector(self.mock_config)
        assert isinstance(connector.auth, OAuth2ClientCredentials)

    # -------------------------------------------------------------------------
    # Connector-specific: _workspace_id metadata
    # -------------------------------------------------------------------------

    def test_workspace_id_injected(self, connector, mock_http):
        records = list(connector.fetch_data("batch-ws"))
        assert len(records) > 0
        assert records[0]["_workspace_id"] == WORKSPACE_ID

    # -------------------------------------------------------------------------
    # Connector-specific: source metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_log_analytics(self, connector, mock_http):
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "log_analytics"

    # -------------------------------------------------------------------------
    # Connector-specific: column-to-dict conversion
    # -------------------------------------------------------------------------

    @responses.activate
    def test_columns_mapped_to_record_keys(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(
            responses.POST, QUERY_URL,
            json={
                "tables": [{
                    "name": "PrimaryResult",
                    "columns": [
                        {"name": "Computer", "type": "string"},
                        {"name": "ComputerIP", "type": "string"},
                        {"name": "OSType", "type": "string"},
                    ],
                    "rows": [
                        ["SRV-001", "10.0.1.1", "Linux"],
                    ],
                }],
            },
            status=200,
        )

        connector = LogAnalyticsConnector(self.mock_config)
        records = list(connector.fetch_data("batch-cols"))

        assert len(records) == 1
        assert records[0]["Computer"] == "SRV-001"
        assert records[0]["ComputerIP"] == "10.0.1.1"
        assert records[0]["OSType"] == "Linux"

    # -------------------------------------------------------------------------
    # Connector-specific: multi-row results
    # -------------------------------------------------------------------------

    @responses.activate
    def test_multi_row_results(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(
            responses.POST, QUERY_URL,
            json={
                "tables": [{
                    "name": "PrimaryResult",
                    "columns": [
                        {"name": "Computer", "type": "string"},
                        {"name": "OSType", "type": "string"},
                    ],
                    "rows": [
                        ["PC-001", "Windows"],
                        ["PC-002", "Linux"],
                        ["PC-003", "macOS"],
                    ],
                }],
            },
            status=200,
        )

        connector = LogAnalyticsConnector(self.mock_config)
        records = list(connector.fetch_data("batch-multi"))

        assert len(records) == 3
        computers = {r["Computer"] for r in records}
        assert computers == {"PC-001", "PC-002", "PC-003"}

    # -------------------------------------------------------------------------
    # Connector-specific: validation
    # -------------------------------------------------------------------------

    def test_missing_tenant_id_raises(self):
        config = {**self.mock_config, "tenant_id": ""}
        with pytest.raises(ValueError, match="tenant ID"):
            LogAnalyticsConnector(config)

    def test_missing_workspace_id_raises(self):
        config = {**self.mock_config, "workspace_id": ""}
        with pytest.raises(ValueError, match="workspace ID"):
            LogAnalyticsConnector(config)

    def test_missing_kql_query_raises(self):
        config = {**self.mock_config, "kql_query": ""}
        with pytest.raises(ValueError, match="KQL query"):
            LogAnalyticsConnector(config)
