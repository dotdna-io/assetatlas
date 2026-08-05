# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SentinelOneRangerConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for cursor pagination, scope resolution, and scope metadata fields.
"""

import json
import pytest
import responses
from urllib.parse import urlparse, parse_qs

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.sentinelone_ranger_connector import SentinelOneRangerConnector
from src.modules.integrations.base import APIKeyAuth


BASE_URL = "https://tenant.sentinelone.net"
ACCOUNTS_URL = f"{BASE_URL}/web/api/v2.1/accounts"
RANGER_URL = f"{BASE_URL}/web/api/v2.1/ranger/table-view"


class TestSentinelOneRangerConnector(ConnectorTestCase):
    connector_class = SentinelOneRangerConnector
    mock_config = {
        "url": BASE_URL,
        "api_token": "test-api-token",
        "account_ids": "acct-001",
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default single-page ranger response for all tests."""
        mock_http.register_from_fixture("GET", RANGER_URL, "sentinelone_ranger/devices_single.json")
        # test_connection hits accounts then ranger
        mock_http.register_from_fixture("GET", ACCOUNTS_URL, "sentinelone_ranger/accounts.json")
        mock_http.register_from_fixture("GET", RANGER_URL, "sentinelone_ranger/devices_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_api_key_auth(self):
        """Connector must use APIKeyAuth."""
        connector = SentinelOneRangerConnector(self.mock_config)
        assert isinstance(connector.auth, APIKeyAuth)

    def test_auth_uses_apitoken_prefix(self):
        """Auth must use 'ApiToken ' prefix."""
        connector = SentinelOneRangerConnector(self.mock_config)
        assert connector.auth.api_key_prefix == 'ApiToken '
        assert connector.auth.api_key_header == 'Authorization'
        assert connector.auth.api_key == 'test-api-token'

    # -------------------------------------------------------------------------
    # Connector-specific: init validation
    # -------------------------------------------------------------------------

    def test_missing_url_raises(self):
        """Missing url raises ValueError."""
        with pytest.raises(ValueError, match="URL is required"):
            SentinelOneRangerConnector({"api_token": "tok"})

    def test_missing_api_token_raises(self):
        """Missing api_token raises ValueError."""
        with pytest.raises(ValueError, match="API token is required"):
            SentinelOneRangerConnector({"url": BASE_URL})

    # -------------------------------------------------------------------------
    # Connector-specific: scope parsing
    # -------------------------------------------------------------------------

    def test_parse_csv_single_value(self):
        """Single account ID is parsed correctly."""
        connector = SentinelOneRangerConnector(self.mock_config)
        result = connector._parse_csv("acct-001")
        assert result == ["acct-001"]

    def test_parse_csv_multiple_values(self):
        """Multiple comma-separated IDs are parsed and stripped."""
        connector = SentinelOneRangerConnector(self.mock_config)
        result = connector._parse_csv("acct-001, acct-002 , acct-003")
        assert result == ["acct-001", "acct-002", "acct-003"]

    def test_parse_csv_empty_string(self):
        """Empty string returns empty list."""
        connector = SentinelOneRangerConnector(self.mock_config)
        assert connector._parse_csv("") == []

    def test_account_ids_parsed_from_config(self):
        """account_ids config is parsed into a list."""
        connector = SentinelOneRangerConnector(self.mock_config)
        assert connector.account_ids == ["acct-001"]

    def test_site_ids_parsed_from_config(self):
        """site_ids config is parsed into a list."""
        config = {**self.mock_config, "site_ids": "site-001, site-002"}
        del config["account_ids"]
        connector = SentinelOneRangerConnector(config)
        assert connector.site_ids == ["site-001", "site-002"]

    # -------------------------------------------------------------------------
    # Connector-specific: scope resolution
    # -------------------------------------------------------------------------

    def test_resolve_scopes_uses_account_ids_when_set(self):
        """_resolve_scopes returns accountIds scopes when account_ids is configured."""
        connector = SentinelOneRangerConnector(self.mock_config)
        scopes = connector._resolve_scopes()
        assert scopes == [{"accountIds": "acct-001"}]

    def test_resolve_scopes_uses_site_ids_when_set(self):
        """_resolve_scopes returns siteIds scopes when site_ids is configured."""
        config = {**self.mock_config, "site_ids": "site-001", "account_ids": ""}
        connector = SentinelOneRangerConnector(config)
        scopes = connector._resolve_scopes()
        assert scopes == [{"siteIds": "site-001"}]

    # -------------------------------------------------------------------------
    # Connector-specific: cursor pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_cursor(self):
        """Records from both pages are yielded when nextCursor is present."""
        fixtures = FIXTURES_DIR / "sentinelone_ranger"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, RANGER_URL, json=page1, status=200)
        responses.add(responses.GET, RANGER_URL, json=page2, status=200)

        connector = SentinelOneRangerConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        assert len(records) == 3
        device_ids = {r["id"] for r in records}
        assert "device-001" in device_ids
        assert "device-002" in device_ids
        assert "device-003" in device_ids

    @responses.activate
    def test_pagination_cursor_sent_as_param(self):
        """The cursor from page 1 is passed as 'cursor' query param on page 2."""
        fixtures = FIXTURES_DIR / "sentinelone_ranger"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, RANGER_URL, json=page1, status=200)
        responses.add(responses.GET, RANGER_URL, json=page2, status=200)

        connector = SentinelOneRangerConnector(self.mock_config)
        list(connector.fetch_data("batch-cursor"))

        ranger_calls = [c for c in responses.calls if RANGER_URL in c.request.url]
        assert len(ranger_calls) == 2

        qs2 = parse_qs(urlparse(ranger_calls[1].request.url).query)
        assert qs2.get("cursor") == ["cursor-page2"]

    # -------------------------------------------------------------------------
    # Connector-specific: scope metadata fields
    # -------------------------------------------------------------------------

    def test_scope_account_id_injected(self, connector, mock_http):
        """_scope_account_id is set from the scope accountIds."""
        records = list(connector.fetch_data("batch-scope"))
        assert len(records) > 0
        assert records[0]["_scope_account_id"] == "acct-001"
        assert records[0]["_scope_site_id"] is None

    @responses.activate
    def test_scope_site_id_injected_for_site_scope(self):
        """_scope_site_id is set from the scope siteIds."""
        with open(FIXTURES_DIR / "sentinelone_ranger" / "devices_single.json") as f:
            single = json.load(f)

        responses.add(responses.GET, RANGER_URL, json=single, status=200)

        config = {**self.mock_config, "site_ids": "site-001", "account_ids": ""}
        connector = SentinelOneRangerConnector(config)
        records = list(connector.fetch_data("batch-site-scope"))

        assert len(records) > 0
        assert records[0]["_scope_site_id"] == "site-001"
        assert records[0]["_scope_account_id"] is None

    # -------------------------------------------------------------------------
    # Connector-specific: metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_sentinelone_ranger(self, connector, mock_http):
        """The _source metadata field must be 'sentinelone_ranger'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "sentinelone_ranger"

    def test_record_type_is_ranger_device(self, connector, mock_http):
        """The _record_type field must be 'ranger_device'."""
        records = list(connector.fetch_data("batch-rectype"))
        assert records[0]["_record_type"] == "ranger_device"


# ---------------------------------------------------------------------------
# Issue #1334 — paginator cap-hit surfaces as a collection warning
# ---------------------------------------------------------------------------


def _devices_page(device_id, next_cursor):
    return {
        "data": [{"id": device_id}],
        "pagination": {"nextCursor": next_cursor},
    }


class TestSentinelOneRangerTruncation:
    """Each scope paginates independently, so a cap-hit must be relayed
    per scope, naming the scope — an operator with 20 scopes needs to know
    which one truncated."""

    mock_config = {
        "url": BASE_URL,
        "api_token": "test-api-token",
        "account_ids": "acct-001",
        # Shrink the 500-page cap so a cap-hit takes two mocked pages.
        "max_pagination_pages": 2,
    }

    @responses.activate
    def test_capped_run_records_one_warning_naming_scope(self):
        # Two full pages, each handing back a fresh cursor: more data waiting.
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-1", "cursor-2"), status=200)
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-2", "cursor-3"), status=200)

        connector = SentinelOneRangerConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "SentinelOne Ranger devices (account acct-001)" in warnings[0]
        assert "2-page cap" in warnings[0]

    @responses.activate
    def test_end_of_data_records_no_warning(self):
        """The off-by-one: a final page that fills the cap but hands back no
        cursor is end-of-data, not truncation."""
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-1", "cursor-2"), status=200)
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-2", None), status=200)

        connector = SentinelOneRangerConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert connector.collection_warnings == []

    def test_config_schema_declares_max_pagination_pages(self):
        """The cap is operator-settable: declared with the connector's default."""
        field = SentinelOneRangerConnector.get_config_schema()["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["required"] is False
        assert field["default"] == 500

    @responses.activate
    def test_only_the_truncated_scope_warns(self):
        """Two scopes, one truncated: exactly one warning, naming the right
        scope. Catches a paginator instance hoisted out of the scope loop or
        a relay placed after it — both read only the last scope's flag."""
        # Scope acct-001 truncates: two full pages with fresh cursors.
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-1", "cursor-2"), status=200)
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-2", "cursor-3"), status=200)
        # Scope acct-002 reaches end of data in one page.
        responses.add(responses.GET, RANGER_URL, json=_devices_page("dev-3", None), status=200)

        config = {**self.mock_config, "account_ids": "acct-001,acct-002"}
        connector = SentinelOneRangerConnector(config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 3
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "account acct-001" in warnings[0]
        assert "acct-002" not in warnings[0]
