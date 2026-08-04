# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for ArmisConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for cursor pagination, record flattening, ASQ builder, adaptive rate
limiter, and OAuth2TokenExchange auth.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.armis_connector import ArmisConnector
from src.modules.integrations.base import OAuth2TokenExchange, AdaptiveRateLimiter


BASE_URL = "https://test-tenant.armis.com"
TOKEN_URL = f"{BASE_URL}/api/v1/access_token/"
SEARCH_URL = f"{BASE_URL}/api/v1/search/"


class TestArmisConnector(ConnectorTestCase):
    connector_class = ArmisConnector
    mock_config = {
        "tenant_url": BASE_URL,
        "api_key": "test-secret-key",
        "page_size": 1000,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default token exchange + single-page device response for all tests."""
        mock_http.register_from_fixture(
            "POST", TOKEN_URL, "armis/auth_success.json"
        )
        mock_http.register_from_fixture(
            "GET", SEARCH_URL, "armis/devices_single.json"
        )

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_token_exchange(self):
        """The connector uses OAuth2TokenExchange."""
        connector = ArmisConnector(self.mock_config)
        assert isinstance(connector.auth, OAuth2TokenExchange)

    def test_auth_token_url_matches_tenant(self):
        """Token URL is built from the tenant URL."""
        connector = ArmisConnector(self.mock_config)
        assert connector.auth.token_url == TOKEN_URL

    # -------------------------------------------------------------------------
    # Connector-specific: rate limiter
    # -------------------------------------------------------------------------

    def test_rate_limiter_is_adaptive(self):
        """The connector configures an AdaptiveRateLimiter."""
        connector = ArmisConnector(self.mock_config)
        assert isinstance(connector.rate_limiter, AdaptiveRateLimiter)
        assert connector.rate_limiter.min_rpm == 10
        assert connector.rate_limiter.max_rpm == 200

    # -------------------------------------------------------------------------
    # Connector-specific: cursor pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_numeric_offset(self):
        """Records from both pages are yielded when next offset is present."""
        fixtures = FIXTURES_DIR / "armis"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        # Token exchange, then page 1 (next=2), then page 2 (next=null)
        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=page1, status=200)
        responses.add(responses.GET, SEARCH_URL, json=page2, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 devices, page2 has 1 = 3 total
        assert len(records) == 3
        names = {r["name"] for r in records}
        assert "DESKTOP-WIN001" in names
        assert "LAPTOP-MAC002" in names
        assert "SERVER-LIN003" in names

    @responses.activate
    def test_single_page_null_next(self):
        """When next is null, only one page is fetched."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=single, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    @responses.activate
    def test_pagination_terminates_on_zero_next(self):
        """Defense in depth: Armis ``data.next`` is documented as ``null`` at
        end-of-data, but if it ever returns ``0`` (or another falsy value),
        the paginator must still terminate. Without this guarantee the same
        bug that hit CrowdStrike Daytona on production 2026-05-13 would fire — the
        same numeric-offset endpoint pattern is at risk."""
        body = {
            "success": True,
            "data": {
                "count": 2,
                "total": 2,
                "next": 0,
                "results": [
                    {"id": 1, "name": "DEV-A"},
                    {"id": 2, "name": "DEV-B"},
                ],
            },
        }

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-zero-next"))

        assert len(records) == 2, "must not loop on falsy next offset"
        get_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(get_calls) == 1

    @responses.activate
    def test_pagination_offset_passed_as_from_param(self):
        """The numeric offset from page 1 is sent as 'from' query param to page 2."""
        fixtures = FIXTURES_DIR / "armis"

        with open(fixtures / "devices_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "devices_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=page1, status=200)
        responses.add(responses.GET, SEARCH_URL, json=page2, status=200)

        connector = ArmisConnector(self.mock_config)
        list(connector.fetch_data("batch-offset"))

        # The second GET call should include from=2
        from urllib.parse import urlparse, parse_qs
        get_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(get_calls) == 2

        second_url = get_calls[1].request.url
        qs = parse_qs(urlparse(second_url).query)
        assert qs.get("from") == ["2"]

    # -------------------------------------------------------------------------
    # Connector-specific: record flattening
    # -------------------------------------------------------------------------

    @responses.activate
    def test_flatten_site_to_scalar_fields(self):
        """Nested site dict is flattened to siteName and siteLocation."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-flatten"))

        rec = records[0]
        assert rec["siteName"] == "HQ Office"
        assert rec["siteLocation"] == "New York"
        assert "site" not in rec  # nested dict replaced

    @responses.activate
    def test_flatten_sensor_to_scalar_fields(self):
        """Nested sensor dict is flattened to sensorName and sensorType."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-flatten2"))

        rec = records[0]
        assert rec["sensorName"] == "sensor-hq-01"
        assert rec["sensorType"] == "Physical"

    @responses.activate
    def test_flatten_tags_to_comma_separated(self):
        """Tags list is converted to comma-separated string."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-tags"))

        rec = records[0]
        assert "Production" in rec["tags"]
        assert "US-East" in rec["tags"]
        assert ", " in rec["tags"]

    @responses.activate
    def test_flatten_datasources_to_names(self):
        """dataSources list of dicts is flattened to comma-separated names."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-ds"))

        rec = records[0]
        assert "CrowdStrike" in rec["dataSources"]
        assert "Active Directory" in rec["dataSources"]

    @responses.activate
    def test_flatten_custom_properties_prefixed(self):
        """customProperties keys are prefixed with cp_."""
        with open(FIXTURES_DIR / "armis" / "devices_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=body, status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("batch-cp"))

        rec = records[0]
        assert rec["cp_department"] == "Engineering"
        assert rec["cp_costCenter"] == "CC-100"

    # -------------------------------------------------------------------------
    # Connector-specific: ASQ builder
    # -------------------------------------------------------------------------

    def test_asq_default(self):
        """Default ASQ is 'in:devices'."""
        connector = ArmisConnector(self.mock_config)
        assert connector._build_asq() == "in:devices"

    def test_asq_with_filter(self):
        """ASQ includes custom filter."""
        config = {**self.mock_config, "asq_filter": 'timeFrame:"30 Days"'}
        connector = ArmisConnector(config)
        assert connector._build_asq() == 'in:devices timeFrame:"30 Days"'

    # -------------------------------------------------------------------------
    # Connector-specific: validation
    # -------------------------------------------------------------------------

    def test_missing_tenant_url_raises(self):
        """Missing tenant_url raises ValueError."""
        config = {**self.mock_config, "tenant_url": ""}
        with pytest.raises(ValueError, match="Tenant URL"):
            ArmisConnector(config)

    def test_missing_api_key_raises(self):
        """Missing api_key raises ValueError."""
        config = {**self.mock_config, "api_key": ""}
        with pytest.raises(ValueError, match="API secret key"):
            ArmisConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection (via base class)
    # -------------------------------------------------------------------------

    def test_metadata_source_is_armis(self, connector, mock_http):
        """The _source metadata field must be 'armis'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "armis"
