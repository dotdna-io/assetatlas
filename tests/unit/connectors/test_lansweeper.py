# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for LansweeperConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for GraphQL pagination, multi-site collection, dual auth modes, and
record flattening.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.lansweeper_connector import (
    LansweeperConnector,
    API_URL,
    TOKEN_URL,
)
from src.modules.integrations.base import APIKeyAuth, OAuth2RefreshToken


# PAT config used for default test suite
PAT_CONFIG = {
    "auth_method": "pat",
    "personal_access_token": "test-pat-token",
    "page_size": 500,
}

# OAuth2 config
OAUTH2_CONFIG = {
    "auth_method": "oauth2",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "refresh_token": "test-refresh-token",
    "page_size": 500,
}


class TestLansweeperConnector(ConnectorTestCase):
    connector_class = LansweeperConnector
    mock_config = {
        **PAT_CONFIG,
        "site_id": "site-001",
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default single-page asset response for all tests."""
        mock_http.register_from_fixture(
            "POST", API_URL, "lansweeper/assets_single.json"
        )

    # -------------------------------------------------------------------------
    # Connector-specific: dual auth modes
    # -------------------------------------------------------------------------

    def test_pat_auth_builds_apikey_strategy(self):
        """PAT auth mode produces an APIKeyAuth strategy."""
        connector = LansweeperConnector(PAT_CONFIG)
        assert isinstance(connector.auth, APIKeyAuth)
        assert connector.auth.api_key_header == "Authorization"
        assert connector.auth.api_key_prefix == "Token "

    def test_oauth2_auth_builds_refresh_token_strategy(self):
        """OAuth2 auth mode produces an OAuth2RefreshToken strategy."""
        connector = LansweeperConnector(OAUTH2_CONFIG)
        assert isinstance(connector.auth, OAuth2RefreshToken)

    def test_default_auth_is_pat(self):
        """When auth_method is not specified, default to PAT."""
        config = {"personal_access_token": "tok"}
        connector = LansweeperConnector(config)
        assert connector.auth_method == "pat"
        assert isinstance(connector.auth, APIKeyAuth)

    # -------------------------------------------------------------------------
    # Connector-specific: cursor pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_cursor(self):
        """Records from both pages are yielded when cursor is present."""
        fixtures = FIXTURES_DIR / "lansweeper"

        with open(fixtures / "assets_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "assets_page2.json") as f:
            page2 = json.load(f)

        # Page 1 (has next cursor), then page 2 (no cursor — terminates)
        responses.add(responses.POST, API_URL, json=page1, status=200)
        responses.add(responses.POST, API_URL, json=page2, status=200)

        config = {**PAT_CONFIG, "site_id": "site-001"}
        connector = LansweeperConnector(config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 assets, page2 has 1 = 3 total
        assert len(records) == 3
        names = {r["assetBasicInfo_name"] for r in records}
        assert "DESKTOP-WIN001" in names
        assert "LAPTOP-MAC002" in names
        assert "SERVER-LIN003" in names

    @responses.activate
    def test_single_page_no_cursor(self):
        """When there is no next cursor, only one page is fetched."""
        with open(FIXTURES_DIR / "lansweeper" / "assets_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, API_URL, json=single, status=200)

        config = {**PAT_CONFIG, "site_id": "site-001"}
        connector = LansweeperConnector(config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: multi-site collection
    # -------------------------------------------------------------------------

    @responses.activate
    def test_multi_site_collection(self):
        """Without site_id, connector discovers sites and collects from each."""
        fixtures = FIXTURES_DIR / "lansweeper"

        with open(fixtures / "sites.json") as f:
            sites_body = json.load(f)
        with open(fixtures / "assets_single.json") as f:
            single = json.load(f)

        # Sites query, then one page per site (2 sites)
        responses.add(responses.POST, API_URL, json=sites_body, status=200)
        responses.add(responses.POST, API_URL, json=single, status=200)
        responses.add(responses.POST, API_URL, json=single, status=200)

        config = {**PAT_CONFIG}  # No site_id
        connector = LansweeperConnector(config)
        records = list(connector.fetch_data("batch-multi"))

        # 1 asset per site * 2 sites = 2 records
        assert len(records) == 2

    @responses.activate
    def test_single_site_skips_discovery(self):
        """With site_id set, connector skips site discovery."""
        with open(FIXTURES_DIR / "lansweeper" / "assets_single.json") as f:
            single = json.load(f)

        # No sites query needed — just one asset page
        responses.add(responses.POST, API_URL, json=single, status=200)

        config = {**PAT_CONFIG, "site_id": "site-001"}
        connector = LansweeperConnector(config)
        records = list(connector.fetch_data("batch-site"))

        assert len(records) == 1
        # Only 1 POST call (no sites query)
        assert len(responses.calls) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: record flattening
    # -------------------------------------------------------------------------

    @responses.activate
    def test_flatten_nested_objects(self):
        """Nested assetBasicInfo/operatingSystem/assetCustom are flattened."""
        with open(FIXTURES_DIR / "lansweeper" / "assets_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, API_URL, json=body, status=200)

        config = {**PAT_CONFIG, "site_id": "site-001"}
        connector = LansweeperConnector(config)
        records = list(connector.fetch_data("batch-flatten"))

        assert len(records) == 1
        rec = records[0]
        # Nested fields are flattened with underscore separator
        assert rec["assetBasicInfo_name"] == "DESKTOP-WIN001"
        assert rec["assetBasicInfo_ipAddress"] == "10.0.1.10"
        assert rec["operatingSystem_caption"] == "Microsoft Windows 11 Enterprise"
        assert rec["assetCustom_manufacturer"] == "Dell Inc."
        assert rec["assetCustom_serialNumber"] == "SN-001"
        # Top-level fields preserved
        assert rec["key"] == "asset-001"
        assert rec["siteId"] == "site-001"

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection (via base class)
    # -------------------------------------------------------------------------

    def test_metadata_source_is_lansweeper(self, connector, mock_http):
        """The _source metadata field must be 'lansweeper'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "lansweeper"

    # -------------------------------------------------------------------------
    # Connector-specific: GraphQL error handling
    # -------------------------------------------------------------------------

    @responses.activate
    def test_graphql_errors_raise_runtime_error(self):
        """GraphQL errors in 200 response raise RuntimeError."""
        error_body = {
            "errors": [{"message": "Field limit exceeded"}],
        }
        responses.add(responses.POST, API_URL, json=error_body, status=200)

        config = {**PAT_CONFIG, "site_id": "site-001"}
        connector = LansweeperConnector(config)
        with pytest.raises(RuntimeError, match="GraphQL errors"):
            list(connector.fetch_data("batch-gql-err"))
