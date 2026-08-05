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
from urllib.parse import urlparse, parse_qs

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.armis_connector import (
    ArmisConnector,
    DEVICE_FIELDS,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)
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


# ---------------------------------------------------------------------------
# Issue #1334 — paginator cap-hit surfaces as a collection warning
# ---------------------------------------------------------------------------


def _devices_page(device_id, next_offset):
    return {
        "success": True,
        "data": {
            "count": 1,
            "total": 100,
            "next": next_offset,
            "results": [{"id": device_id, "name": f"DEV-{device_id}"}],
        },
    }


class TestArmisTruncation:
    """A collection stopped by the page cap must record exactly one warning
    naming the resource and the cap — the pipeline reads a silent shortfall
    as deleted assets."""

    mock_config = {
        "tenant_url": BASE_URL,
        "api_key": "test-secret-key",
        "page_size": 1000,
        "max_pagination_pages": 2,
    }

    @responses.activate
    def test_capped_run_records_one_warning(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        # Two full pages, each handing back a fresh offset: more data waiting.
        responses.add(responses.GET, SEARCH_URL, json=_devices_page(1, 2), status=200)
        responses.add(responses.GET, SEARCH_URL, json=_devices_page(2, 4), status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "Armis devices truncated at the 2-page cap" in warnings[0]

    @responses.activate
    def test_end_of_data_records_no_warning(self):
        """The off-by-one: a final page that fills the cap but returns
        ``next: null`` is end-of-data, not truncation."""
        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json=_devices_page(1, 2), status=200)
        responses.add(responses.GET, SEARCH_URL, json=_devices_page(2, None), status=200)

        connector = ArmisConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert connector.collection_warnings == []

    def test_config_schema_declares_max_pagination_pages(self):
        """The cap is operator-settable: declared with the connector's default."""
        field = ArmisConnector.get_config_schema()["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["required"] is False
        assert field["default"] == 500


# ---------------------------------------------------------------------------
# Coverage ported from the retired flat suite
# (tests/unit/test_armis_connector.py, #1358)
# ---------------------------------------------------------------------------


class TestArmisConfigSchemaDetails:
    """Field-level config-schema contracts the base-class shape checks don't pin."""

    def test_required_fields_present(self):
        schema = ArmisConnector.get_config_schema()
        for field in ("tenant_url", "api_key", "asq_filter", "page_size"):
            assert field in schema, f"Missing config field: {field}"

    def test_no_collection_mode_option(self):
        """Armis collects devices only — no collection_mode knob may appear."""
        schema = ArmisConnector.get_config_schema()
        assert "collection_mode" not in schema

    def test_api_key_is_password_and_sensitive(self):
        schema = ArmisConnector.get_config_schema()
        assert schema["api_key"]["type"] == "password"
        assert schema["api_key"]["sensitive"] is True

    def test_page_size_validation_and_default(self):
        schema = ArmisConnector.get_config_schema()
        assert schema["page_size"]["validation"]["min"] == 1
        assert schema["page_size"]["validation"]["max"] == MAX_PAGE_SIZE
        assert schema["page_size"]["default"] == DEFAULT_PAGE_SIZE


class TestArmisConstructorDetails:
    base_config = {
        "tenant_url": "https://test-tenant.armis.com",
        "api_key": "test-secret-key",
    }

    def test_metadata(self):
        assert ArmisConnector.integration_type == "armis"
        assert ArmisConnector.display_name == "Armis Platform"
        assert ArmisConnector.category == "Asset Discovery"

    def test_strips_trailing_slash_from_url(self):
        connector = ArmisConnector(
            {**self.base_config, "tenant_url": "https://test-tenant.armis.com/"}
        )
        assert connector.base_url == "https://test-tenant.armis.com"

    def test_default_page_size(self):
        assert ArmisConnector(self.base_config).page_size == DEFAULT_PAGE_SIZE

    def test_page_size_passthrough(self):
        connector = ArmisConnector({**self.base_config, "page_size": 100})
        assert connector.page_size == 100

    def test_page_size_clamped_to_max(self):
        connector = ArmisConnector({**self.base_config, "page_size": 99999})
        assert connector.page_size == MAX_PAGE_SIZE


class TestArmisFlattenRecord:
    """Direct unit tests for the still-present _flatten_record static method —
    the shapes the fixture-based flatten tests don't exercise."""

    def test_flattens_ipv6_list(self):
        record = {"id": 1, "ipv6": ["fe80::1", "2001:db8::1"]}
        flat = ArmisConnector._flatten_record(record)
        assert flat["ipv6"] == "fe80::1, 2001:db8::1"

    def test_preserves_scalar_values(self):
        record = {
            "id": 42,
            "name": "server-1",
            "ipAddress": "10.0.0.1",
            "riskLevel": 75,
            "operatingSystem": "Windows",
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat["id"] == 42
        assert flat["name"] == "server-1"
        assert flat["ipAddress"] == "10.0.0.1"
        assert flat["riskLevel"] == 75

    def test_handles_null_site(self):
        flat = ArmisConnector._flatten_record({"id": 1, "site": None})
        assert flat["site"] is None

    def test_handles_empty_data_sources(self):
        flat = ArmisConnector._flatten_record({"id": 1, "dataSources": []})
        assert flat["dataSources"] == ""


class TestArmisSchemaDetails:
    def test_schema_source_and_flattened_fields(self):
        connector = ArmisConnector(TestArmisConstructorDetails.base_config)
        schema = connector.get_schema()
        assert schema["source"] == "armis"
        names = [f["name"] for f in schema["fields"]]
        assert "siteName" in names
        assert "siteLocation" in names
        assert "sensorName" in names
        assert "sensorType" in names
        # Nested objects are flattened away, never exposed raw
        assert "site" not in names

    def test_schema_core_device_fields(self):
        connector = ArmisConnector(TestArmisConstructorDetails.base_config)
        names = [f["name"] for f in connector.get_schema()["fields"]]
        for field in (
            "id", "name", "ipAddress", "macAddress", "operatingSystem",
            "manufacturer", "model", "serialNumber", "type", "category",
            "riskLevel", "firstSeen", "lastSeen", "tags",
        ):
            assert field in names, f"Missing field: {field}"


class TestArmisConnectionDetails:
    """test_connection message/error contracts, exercised over mocked HTTP."""

    mock_config = {
        "tenant_url": BASE_URL,
        "api_key": "test-secret-key",
        "page_size": 1000,
    }

    def _register_token(self):
        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)

    @responses.activate
    def test_connection_reports_device_count(self):
        self._register_token()
        responses.add(responses.GET, SEARCH_URL, json={
            "success": True,
            "data": {"total": 4415, "count": 1, "results": [{}]},
        }, status=200)

        result = ArmisConnector(self.mock_config).test_connection()

        assert result["success"] is True
        assert "4,415" in result["message"]
        assert result["metadata"]["total_devices"] == 4415

    @responses.activate
    def test_connection_auth_failure_returns_failure(self):
        self._register_token()
        responses.add(responses.GET, SEARCH_URL,
                      json={"message": "Unauthorized"}, status=401)

        result = ArmisConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "Connection failed" in result["message"]


class TestArmisRequestParams:
    """Search API request parameters from the removed _request-level tests,
    re-exercised at the HTTP layer."""

    def _run_fetch(self, config):
        responses.add(responses.POST, TOKEN_URL,
                      json={"success": True, "data": {"access_token": "tok"}},
                      status=200)
        responses.add(responses.GET, SEARCH_URL, json={
            "success": True,
            "data": {"count": 0, "next": None, "results": [], "total": 0},
        }, status=200)
        connector = ArmisConnector(config)
        list(connector.fetch_data("batch-params"))
        get_calls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(get_calls) == 1
        return parse_qs(urlparse(get_calls[0].request.url).query)

    @responses.activate
    def test_fetch_sends_fields_orderby_and_asq(self):
        qs = self._run_fetch({
            "tenant_url": BASE_URL,
            "api_key": "test-secret-key",
            "page_size": 1000,
        })
        assert qs["orderBy"] == ["id"]
        assert qs["fields"] == [DEVICE_FIELDS]
        assert qs["aql"] == ["in:devices"]
        for field in ("name", "ipAddress", "macAddress"):
            assert field in qs["fields"][0]

    @responses.activate
    def test_fetch_uses_configured_asq_filter(self):
        qs = self._run_fetch({
            "tenant_url": BASE_URL,
            "api_key": "test-secret-key",
            "page_size": 1000,
            "asq_filter": 'type:"Laptops"',
        })
        assert qs["aql"] == ['in:devices type:"Laptops"']
