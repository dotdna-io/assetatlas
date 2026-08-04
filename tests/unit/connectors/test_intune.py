# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for IntuneConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for @odata.nextLink pagination and field_mode selection.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.intune_connector import IntuneConnector


TOKEN_URL = "https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token"
DEVICES_URL = "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices"
DEVICES_PAGE2_URL = (
    "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?$skiptoken=page2token"
)


class TestIntuneConnector(ConnectorTestCase):
    connector_class = IntuneConnector
    mock_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "field_mode": "comprehensive",
        "page_size": 100,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default OAuth token + single-page device response for all tests."""
        mock_http.register_auth_success(TOKEN_URL)
        mock_http.register_from_fixture("GET", DEVICES_URL, "intune/devices_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_next_link(self):
        """Records from both pages are yielded when @odata.nextLink is present."""
        fixtures = FIXTURES_DIR / "intune"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        # Page 1 — includes @odata.nextLink
        responses.add(responses.GET, DEVICES_URL, json=page1, status=200)
        # Page 2 — no nextLink, terminates pagination
        responses.add(responses.GET, DEVICES_PAGE2_URL, json=page2, status=200)

        connector = IntuneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 devices, page2 has 1 → 3 total
        assert len(records) == 3
        names = {r["deviceName"] for r in records}
        assert "DESKTOP-WIN001" in names
        assert "LAPTOP-MAC002" in names
        assert "IPHONE-MOB003" in names

    @responses.activate
    def test_pagination_single_page_no_next_link(self):
        """When there is no @odata.nextLink, only one page is fetched."""
        with open(FIXTURES_DIR / "intune" / "devices_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=single, status=200)

        connector = IntuneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: field mode
    # -------------------------------------------------------------------------

    @responses.activate
    def test_field_mode_essential_sends_select_param(self):
        """In 'essential' mode the $select param includes only ESSENTIAL_FIELDS."""
        with open(FIXTURES_DIR / "intune" / "devices_essential.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        config = {**self.mock_config, "field_mode": "essential"}
        connector = IntuneConnector(config)
        records = list(connector.fetch_data("batch-essential"))

        assert len(records) > 0

        device_calls = [c for c in responses.calls if DEVICES_URL in c.request.url]
        assert device_calls, "No GET /managedDevices call recorded"

        from urllib.parse import urlparse, parse_qs, unquote
        decoded_url = unquote(device_calls[0].request.url)
        assert "$select=" in decoded_url

        qs = parse_qs(urlparse(decoded_url).query)
        selected = set(qs["$select"][0].split(","))
        assert selected == set(IntuneConnector.ESSENTIAL_FIELDS)

    @responses.activate
    def test_field_mode_comprehensive_sends_select_param(self):
        """In 'comprehensive' mode the $select param includes COMPREHENSIVE_FIELDS."""
        with open(FIXTURES_DIR / "intune" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        config = {**self.mock_config, "field_mode": "comprehensive"}
        connector = IntuneConnector(config)
        list(connector.fetch_data("batch-comprehensive"))

        device_calls = [c for c in responses.calls if DEVICES_URL in c.request.url]
        assert device_calls

        from urllib.parse import urlparse, parse_qs, unquote
        decoded_url = unquote(device_calls[0].request.url)
        assert "$select=" in decoded_url

        qs = parse_qs(urlparse(decoded_url).query)
        selected = set(qs["$select"][0].split(","))
        assert selected == set(IntuneConnector.COMPREHENSIVE_FIELDS)

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection (via base class)
    # -------------------------------------------------------------------------

    def test_metadata_source_is_intune(self, connector, mock_http):
        """The _source metadata field must be 'intune'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "intune"
