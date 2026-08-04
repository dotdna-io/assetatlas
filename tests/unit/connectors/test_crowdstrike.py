# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for CrowdStrikeConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for scroll pagination, record normalization, and rate limiter configuration.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.crowdstrike_connector import CrowdStrikeConnector
from src.modules.integrations.base import HeaderRateLimiter


# CrowdStrike uses a direct base_url for testing (not a cloud region key)
BASE_URL = "https://api.crowdstrike.com"
TOKEN_URL = f"{BASE_URL}/oauth2/token"
DEVICES_URL = f"{BASE_URL}/devices/combined/devices/v1"


class TestCrowdStrikeConnector(ConnectorTestCase):
    connector_class = CrowdStrikeConnector
    mock_config = {
        "base_url": "US-1",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "page_size": 100,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default OAuth token + single-page device response for all tests."""
        mock_http.register_auth_success(TOKEN_URL)
        mock_http.register_from_fixture("GET", DEVICES_URL, "crowdstrike/devices_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: scroll pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_scroll_offset(self):
        """Records from both pages are yielded when scroll offset is present."""
        fixtures = FIXTURES_DIR / "crowdstrike"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        # Page 1 — includes scroll offset in meta.pagination.offset
        responses.add(responses.GET, DEVICES_URL, json=page1, status=200)
        # Page 2 — no offset, terminates pagination
        responses.add(responses.GET, DEVICES_URL, json=page2, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 devices, page2 has 1 = 3 total
        assert len(records) == 3
        hostnames = {r["hostname"] for r in records}
        assert "DESKTOP-WIN001" in hostnames
        assert "LAPTOP-MAC002" in hostnames
        assert "SERVER-LIN003" in hostnames

    @responses.activate
    def test_pagination_single_page_no_offset(self):
        """When there is no scroll offset, only one page is fetched."""
        with open(FIXTURES_DIR / "crowdstrike" / "devices_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=single, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    @responses.activate
    def test_pagination_offset_passed_as_param(self):
        """The scroll offset from page 1 is sent as 'offset' query param to page 2."""
        fixtures = FIXTURES_DIR / "crowdstrike"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=page1, status=200)
        responses.add(responses.GET, DEVICES_URL, json=page2, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        list(connector.fetch_data("batch-offset"))

        # The second GET call should include offset=scroll-token-page2
        device_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(device_calls) == 2

        from urllib.parse import urlparse, parse_qs
        second_url = device_calls[1].request.url
        qs = parse_qs(urlparse(second_url).query)
        assert qs.get("offset") == ["scroll-token-page2"]

    @responses.activate
    def test_pagination_terminates_on_zero_offset(self):
        """Regression (production, 2026-05-13):

        CrowdStrike's /devices/combined/devices/v1 returns
        ``meta.pagination.offset`` even at end-of-data — typically as ``0``
        on a single-page result with ``sort``. The pre-SDK connector used
        ``if not offset: break`` (Python falsy) which caught this. The new
        ``CursorPaginator`` only broke on ``next_cursor is None``, so the
        same request was issued ``max_pages`` (=100) times, producing
        124 unique devices × 100 = 12,400 duplicated raw rows. This test
        pins the falsy-offset terminator so it can't regress again.
        """
        body = {
            "meta": {"pagination": {"offset": 0, "total": 2}},
            "resources": [
                {"device_id": "a", "hostname": "host-a"},
                {"device_id": "b", "hostname": "host-b"},
            ],
            "errors": [],
        }

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-zero-offset"))

        assert len(records) == 2, "must not loop and duplicate the page"
        # Exactly one device-list GET (plus the OAuth POST).
        get_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(get_calls) == 1

    @responses.activate
    def test_pagination_terminates_when_offset_reaches_total(self):
        """If the API returns ``meta.pagination.offset == total`` (cumulative
        position past the dataset), the connector signals end-of-data so
        the paginator stops without making another request."""
        body = {
            "meta": {"pagination": {"offset": 2, "total": 2}},
            "resources": [
                {"device_id": "a", "hostname": "host-a"},
                {"device_id": "b", "hostname": "host-b"},
            ],
            "errors": [],
        }

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-total-reached"))

        assert len(records) == 2
        get_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(get_calls) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: record normalization
    # -------------------------------------------------------------------------

    @responses.activate
    def test_normalize_extracts_key_fields(self):
        """Normalization extracts hostname, device_id, platform_name, etc."""
        with open(FIXTURES_DIR / "crowdstrike" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-norm"))

        assert len(records) == 1
        rec = records[0]
        assert rec["device_id"] == "dev-001"
        assert rec["hostname"] == "DESKTOP-WIN001"
        assert rec["platform_name"] == "Windows"
        assert rec["serial_number"] == "SN-001"
        assert rec["status"] == "normal"

    @responses.activate
    def test_normalize_tags_to_comma_separated(self):
        """Tags list is converted to comma-separated string."""
        with open(FIXTURES_DIR / "crowdstrike" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-tags"))
        rec = records[0]

        assert "SensorGroupingTags/Production" in rec["tags"]
        assert "SensorGroupingTags/US-East" in rec["tags"]
        assert ", " in rec["tags"]

    @responses.activate
    def test_normalize_flattens_device_policies(self):
        """Device policies dict is flattened into policy_<type>_id and policy_<type>_applied keys."""
        with open(FIXTURES_DIR / "crowdstrike" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, DEVICES_URL, json=body, status=200)

        connector = CrowdStrikeConnector(self.mock_config)
        records = list(connector.fetch_data("batch-policies"))
        rec = records[0]

        assert rec["policy_prevention_id"] == "pol-prev-001"
        assert rec["policy_prevention_applied"] is True
        assert rec["policy_sensor_update_id"] == "pol-sensor-001"
        assert rec["policy_sensor_update_applied"] is True

    # -------------------------------------------------------------------------
    # Connector-specific: rate limiter configuration
    # -------------------------------------------------------------------------

    def test_rate_limiter_is_header_based(self):
        """The connector configures a HeaderRateLimiter."""
        connector = CrowdStrikeConnector(self.mock_config)
        assert isinstance(connector.rate_limiter, HeaderRateLimiter)
        assert connector.rate_limiter.remaining_header == "X-RateLimit-Remaining"
        assert connector.rate_limiter.retry_after_header == "Retry-After"
        assert connector.rate_limiter.threshold == 10

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection (via base class)
    # -------------------------------------------------------------------------

    def test_metadata_source_is_crowdstrike(self, connector, mock_http):
        """The _source metadata field must be 'crowdstrike'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "crowdstrike"

    # -------------------------------------------------------------------------
    # Connector-specific: cloud URL resolution
    # -------------------------------------------------------------------------

    def test_cloud_resolves_us1_url(self):
        """US-1 cloud resolves to api.crowdstrike.com."""
        connector = CrowdStrikeConnector(self.mock_config)
        assert connector.base_url == "https://api.crowdstrike.com"
        assert connector.cloud == "US-1"

    def test_cloud_resolves_custom_url(self):
        """Direct HTTP URL is accepted as custom cloud."""
        config = {**self.mock_config, "base_url": "https://custom.api.example.com"}
        connector = CrowdStrikeConnector(config)
        assert connector.base_url == "https://custom.api.example.com"
        assert connector.cloud == "CUSTOM"

    def test_invalid_cloud_raises(self):
        """Unknown cloud region raises ValueError."""
        config = {**self.mock_config, "base_url": "INVALID-REGION"}
        with pytest.raises(ValueError, match="Unknown CrowdStrike cloud"):
            CrowdStrikeConnector(config)
