# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for JuniperMistConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for APIKeyAuth, _record_type metadata, inventory+stats merge,
and org discovery.
"""

import json
import logging
import pytest
import responses
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.juniper_mist_connector import (
    JuniperMistConnector,
    MAX_PAGE_SIZE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGINATION_PAGES,
    DEFAULT_RATE_LIMIT_WAIT,
    DEFAULT_CLIENT_DURATION,
)
from src.modules.integrations.base import APIKeyAuth


BASE_URL = "https://api.mist.com/api/v1"
ORG_ID = "org-001"
ORG_URL = f"{BASE_URL}/orgs/{ORG_ID}"
INVENTORY_URL = f"{ORG_URL}/inventory"
DEVICE_STATS_URL = f"{ORG_URL}/stats/devices"


class TestJuniperMistConnector(ConnectorTestCase):
    connector_class = JuniperMistConnector
    mock_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "page_size": 100,
        "collect_devices": True,
        "collect_clients": False,  # Simpler default for base tests
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default inventory + stats responses for all tests."""
        # Device inventory
        mock_http.register_from_fixture("GET", INVENTORY_URL, "juniper_mist/inventory.json")
        # Device stats
        mock_http.register_from_fixture("GET", DEVICE_STATS_URL, "juniper_mist/device_stats.json")
        # For test_connection
        mock_http.register_from_fixture("GET", ORG_URL, "juniper_mist/org_info.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_api_key(self):
        connector = JuniperMistConnector(self.mock_config)
        assert isinstance(connector.auth, APIKeyAuth)

    def test_auth_header_is_token(self):
        connector = JuniperMistConnector(self.mock_config)
        assert connector.auth.api_key_header == 'Authorization'
        assert connector.auth.api_key_prefix == 'Token '

    # -------------------------------------------------------------------------
    # Connector-specific: source metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_juniper_mist(self, connector, mock_http):
        records = list(connector.fetch_data("batch-meta"))
        assert len(records) > 0
        assert records[0]["_source"] == "juniper_mist"

    # -------------------------------------------------------------------------
    # Connector-specific: _record_type metadata
    # -------------------------------------------------------------------------

    def test_record_type_device(self, connector, mock_http):
        records = list(connector.fetch_data("batch-rt"))
        assert len(records) > 0
        assert records[0]["_record_type"] == "device"

    # -------------------------------------------------------------------------
    # Connector-specific: inventory + stats merge
    # -------------------------------------------------------------------------

    @responses.activate
    def test_merge_inventory_and_stats(self):
        """Device records merge inventory (model, serial) with stats (ip, name)."""
        fixtures = FIXTURES_DIR / "juniper_mist"

        with open(fixtures / "inventory.json") as f:
            inventory = json.load(f)
        with open(fixtures / "device_stats.json") as f:
            stats = json.load(f)

        responses.add(responses.GET, INVENTORY_URL, json=inventory, status=200)
        responses.add(responses.GET, DEVICE_STATS_URL, json=stats, status=200)

        connector = JuniperMistConnector(self.mock_config)
        records = list(connector.fetch_data("batch-merge"))

        # dev-001 has both inventory and stats
        merged = [r for r in records if r["id"] == "dev-001"]
        assert len(merged) == 1
        rec = merged[0]
        # From inventory
        assert rec["model"] == "AP43"
        assert rec["serial"] == "SN-AP001"
        # From stats
        assert rec["name"] == "AP-LOBBY-01"
        assert rec["ip"] == "10.0.1.10"
        assert rec["status"] == "connected"

    @responses.activate
    def test_inventory_only_device_included(self):
        """Devices in inventory but not in stats are still yielded."""
        fixtures = FIXTURES_DIR / "juniper_mist"

        with open(fixtures / "inventory.json") as f:
            inventory = json.load(f)
        with open(fixtures / "device_stats.json") as f:
            stats = json.load(f)

        responses.add(responses.GET, INVENTORY_URL, json=inventory, status=200)
        responses.add(responses.GET, DEVICE_STATS_URL, json=stats, status=200)

        connector = JuniperMistConnector(self.mock_config)
        records = list(connector.fetch_data("batch-inv-only"))

        # dev-002 is in inventory but not in stats
        inv_only = [r for r in records if r["id"] == "dev-002"]
        assert len(inv_only) == 1
        assert inv_only[0]["model"] == "EX4100"
        assert inv_only[0]["serial"] == "SN-SW001"

    # -------------------------------------------------------------------------
    # Connector-specific: test_connection
    # -------------------------------------------------------------------------

    def test_connection_with_org_id(self, connector, mock_http):
        result = connector.test_connection()
        assert result["success"] is True
        assert "Test Organization" in result["message"]

    # -------------------------------------------------------------------------
    # Connector-specific: validation
    # -------------------------------------------------------------------------

    def test_missing_api_token_raises(self):
        config = {**self.mock_config, "api_token": ""}
        with pytest.raises(ValueError, match="API token"):
            JuniperMistConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: region
    # -------------------------------------------------------------------------

    def test_eu_region_url(self):
        config = {**self.mock_config, "region": "eu"}
        connector = JuniperMistConnector(config)
        assert connector.base_url == "https://api.eu.mist.com/api/v1"


class TestJuniperMistTruncation:
    """A _paginate loop stopped by its page cap must say so — otherwise a
    site larger than cap × page_size silently under-reports, and the
    pipeline rebuild reads the shortfall as deleted assets (#1334)."""

    TRUNCATION_CONFIG = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "page_size": 1,
        "max_pagination_pages": 2,
        "collect_devices": True,
        "collect_clients": False,
    }

    @responses.activate
    def test_cap_hit_records_one_warning_naming_the_path(self, caplog):
        # Inventory: two full pages (page_size=1) — the cap ends the loop.
        responses.add(responses.GET, INVENTORY_URL,
                      json=[{"id": "d1", "mac": "aa:bb:cc:00:00:01"}], status=200)
        responses.add(responses.GET, INVENTORY_URL,
                      json=[{"id": "d2", "mac": "aa:bb:cc:00:00:02"}], status=200)
        # Stats: empty first page — genuine end-of-data, must NOT warn.
        responses.add(responses.GET, DEVICE_STATS_URL, json=[], status=200)

        connector = JuniperMistConnector(self.TRUNCATION_CONFIG)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert len(connector.collection_warnings) == 1
        message = connector.collection_warnings[0]
        assert "/orgs/org-001/inventory" in message
        assert "2-page cap" in message
        assert "truncat" in message.lower()
        assert message in caplog.text

    @responses.activate
    def test_end_of_data_records_no_warning(self, caplog):
        """The off-by-one: a final page that fills the cap but signals
        end-of-data (empty page) is not truncation."""
        # Inventory: page 1 full, page 2 empty — the break, not the cap, ends it.
        responses.add(responses.GET, INVENTORY_URL, json=[{"id": "d1"}], status=200)
        responses.add(responses.GET, INVENTORY_URL, json=[], status=200)
        responses.add(responses.GET, DEVICE_STATS_URL, json=[], status=200)

        connector = JuniperMistConnector(self.TRUNCATION_CONFIG)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len(records) == 1
        assert connector.collection_warnings == []

    def test_max_pagination_pages_configurable(self):
        base = {k: v for k, v in self.TRUNCATION_CONFIG.items()
                if k != "max_pagination_pages"}
        connector = JuniperMistConnector(base)
        assert connector.max_pagination_pages == MAX_PAGINATION_PAGES
        connector = JuniperMistConnector({**base, "max_pagination_pages": 25})
        assert connector.max_pagination_pages == 25

    def test_config_schema_exposes_max_pagination_pages(self):
        schema = JuniperMistConnector.get_config_schema()
        field = schema["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["default"] == MAX_PAGINATION_PAGES


# ---------------------------------------------------------------------------
# Coverage ported from the retired flat suite
# (tests/unit/test_juniper_mist_connector.py, #1358)
# ---------------------------------------------------------------------------

SELF_ORGS_URL = f"{BASE_URL}/self/orgs"
SELF_URL = f"{BASE_URL}/self"
SITES_URL = f"{ORG_URL}/sites"
CLIENTS_URL = f"{BASE_URL}/sites/{{site_id}}/stats/clients"


class TestJuniperMistConfigSchemaDetails:
    """Field-level config-schema contracts the base-class shape checks don't pin."""

    def test_required_fields_present(self):
        schema = JuniperMistConnector.get_config_schema()
        for field in ("api_token", "org_id", "region"):
            assert field in schema, f"Missing config field: {field}"

    def test_api_token_is_password_and_sensitive(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema["api_token"]["type"] == "password"
        assert schema["api_token"]["sensitive"] is True

    def test_org_id_is_optional(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema["org_id"]["required"] is False

    def test_region_has_global_and_eu_options(self):
        schema = JuniperMistConnector.get_config_schema()
        values = [o["value"] for o in schema["region"]["options"]]
        assert "global" in values
        assert "eu" in values

    def test_page_size_has_validation(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema["page_size"]["validation"]["min"] == 1
        assert schema["page_size"]["validation"]["max"] == MAX_PAGE_SIZE

    def test_collect_toggles_default_true(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema["collect_devices"]["default"] is True
        assert schema["collect_clients"]["default"] is True

    def test_client_duration_has_options(self):
        schema = JuniperMistConnector.get_config_schema()
        values = [o["value"] for o in schema["client_duration"]["options"]]
        assert "7d" in values
        assert "30d" in values


class TestJuniperMistConstructorDetails:
    base_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
    }

    def _make(self, **overrides):
        return JuniperMistConnector({**self.base_config, **overrides})

    def test_metadata(self):
        assert JuniperMistConnector.integration_type == "juniper_mist"
        assert JuniperMistConnector.display_name == "Juniper Mist"
        assert JuniperMistConnector.category == "Network Infrastructure"

    def test_global_region_resolves_global_url(self):
        assert self._make().base_url == BASE_URL

    def test_empty_org_id_allowed(self):
        assert self._make(org_id="").org_id == ""

    def test_missing_org_id_key_allowed(self):
        config = {k: v for k, v in self.base_config.items() if k != "org_id"}
        assert JuniperMistConnector(config).org_id == ""

    def test_page_size_clamped_to_max(self):
        assert self._make(page_size=5000).page_size == MAX_PAGE_SIZE

    def test_default_page_size(self):
        assert self._make(page_size=None).page_size == DEFAULT_PAGE_SIZE


class TestJuniperMistSchemaDetails:
    base_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
    }

    def test_schema_source_and_device_fields(self):
        schema = JuniperMistConnector(self.base_config).get_schema()
        assert schema["source"] == "juniper_mist"
        names = {f["name"] for f in schema["fields"]}
        for field in (
            "id", "name", "type", "model", "serial", "mac", "ip",
            "version", "status", "last_seen",
        ):
            assert field in names, f"Missing device field: {field}"

    def test_schema_includes_client_fields(self):
        schema = JuniperMistConnector(self.base_config).get_schema()
        names = {f["name"] for f in schema["fields"]}
        for field in (
            "hostname", "ssid", "band", "ap_mac", "ap_name", "manufacture", "os",
        ):
            assert field in names, f"Missing client field: {field}"


class TestJuniperMistRequestLayer:
    """_request rate-limit handling, re-exercised over mocked HTTP (the flat
    suite tested this against the removed ``connector.session``)."""

    mock_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "collect_clients": False,
    }

    @responses.activate
    def test_rate_limit_tracked_from_headers(self):
        responses.add(responses.GET, ORG_URL, json={}, status=200,
                      headers={"X-RateLimit-Remaining": "42"})

        connector = JuniperMistConnector(self.mock_config)
        connector._request("GET", f"/orgs/{ORG_ID}")

        assert connector._rate_limit_remaining == 42

    @responses.activate
    @patch("time.sleep")
    def test_rate_limit_low_triggers_wait(self, mock_sleep):
        responses.add(responses.GET, ORG_URL, json={}, status=200)

        connector = JuniperMistConnector(self.mock_config)
        connector._rate_limit_remaining = 5  # below RATE_LIMIT_THRESHOLD
        connector._request("GET", f"/orgs/{ORG_ID}")

        mock_sleep.assert_called_once_with(DEFAULT_RATE_LIMIT_WAIT)


class TestJuniperMistOrgDiscovery:
    """Org discovery (/self/orgs with the org-token /self fallback), absent
    from this suite until now."""

    no_org_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": "",
        "collect_clients": False,
    }

    def test_get_org_ids_explicit_org_id(self):
        config = {**self.no_org_config, "org_id": ORG_ID}
        connector = JuniperMistConnector(config)
        assert connector._get_org_ids() == [ORG_ID]

    @responses.activate
    def test_discover_orgs_user_token(self):
        """User-level token: /self/orgs returns the org list directly."""
        responses.add(responses.GET, SELF_ORGS_URL, json=[
            {"id": "org-1", "name": "Org One"},
            {"id": "org-2", "name": "Org Two"},
        ], status=200)

        connector = JuniperMistConnector(self.no_org_config)
        orgs = connector._discover_orgs()

        assert len(orgs) == 2
        assert orgs[0]["name"] == "Org One"

    @responses.activate
    def test_discover_orgs_org_token_fallback(self):
        """Org-level token: /self/orgs 404s, privileges from /self are used."""
        responses.add(responses.GET, SELF_ORGS_URL,
                      json={"error": "not found"}, status=404)
        responses.add(responses.GET, SELF_URL, json={
            "privileges": [
                {"scope": "org", "org_id": "org-1", "org_name": "Org One"},
                {"scope": "org", "org_id": "org-2", "org_name": "Org Two"},
            ],
        }, status=200)

        connector = JuniperMistConnector(self.no_org_config)
        orgs = connector._discover_orgs()

        assert orgs == [
            {"id": "org-1", "name": "Org One"},
            {"id": "org-2", "name": "Org Two"},
        ]

    @responses.activate
    def test_discover_orgs_fallback_deduplicates(self):
        responses.add(responses.GET, SELF_ORGS_URL,
                      json={"error": "not found"}, status=404)
        responses.add(responses.GET, SELF_URL, json={
            "privileges": [
                {"scope": "org", "org_id": "org-1", "org_name": "Org One"},
                {"scope": "org", "org_id": "org-1", "org_name": "Org One"},
            ],
        }, status=200)

        connector = JuniperMistConnector(self.no_org_config)
        assert len(connector._discover_orgs()) == 1

    @responses.activate
    def test_discover_orgs_non_list_returns_empty(self):
        responses.add(responses.GET, SELF_ORGS_URL,
                      json={"error": "nope"}, status=200)
        responses.add(responses.GET, SELF_URL, json={}, status=200)

        connector = JuniperMistConnector(self.no_org_config)
        assert connector._discover_orgs() == []

    @responses.activate
    def test_get_org_ids_discovers_and_skips_orgs_without_id(self):
        responses.add(responses.GET, SELF_ORGS_URL, json=[
            {"id": "org-1", "name": "Good"},
            {"name": "No ID"},
        ], status=200)

        connector = JuniperMistConnector(self.no_org_config)
        assert connector._get_org_ids() == ["org-1"]

    @responses.activate
    def test_get_org_ids_none_discovered_warns(self, caplog):
        responses.add(responses.GET, SELF_ORGS_URL, json=[], status=200)
        responses.add(responses.GET, SELF_URL, json={}, status=200)

        connector = JuniperMistConnector(self.no_org_config)
        with caplog.at_level(logging.WARNING):
            assert connector._get_org_ids() == []

        assert "No organizations found" in caplog.text


class TestJuniperMistConnectionDetails:
    """test_connection message/error contracts, exercised over mocked HTTP."""

    mock_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "collect_clients": False,
    }
    no_org_config = {**mock_config, "org_id": ""}

    @responses.activate
    def test_connection_without_org_id_discovers_orgs(self):
        responses.add(responses.GET, SELF_ORGS_URL, json=[
            {"id": "org-1", "name": "Org One"},
            {"id": "org-2", "name": "Org Two"},
        ], status=200)

        result = JuniperMistConnector(self.no_org_config).test_connection()

        assert result["success"] is True
        assert "2 org" in result["message"]
        assert result["metadata"]["org_count"] == 2

    @responses.activate
    def test_connection_no_orgs_discovered_fails(self):
        responses.add(responses.GET, SELF_ORGS_URL, json=[], status=200)
        responses.add(responses.GET, SELF_URL, json={}, status=200)

        result = JuniperMistConnector(self.no_org_config).test_connection()

        assert result["success"] is False
        assert "no organizations" in result["message"].lower()

    @responses.activate
    def test_connection_401_names_invalid_token(self):
        responses.add(responses.GET, ORG_URL,
                      json={"error": "unauthorized"}, status=401)

        result = JuniperMistConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "Invalid" in result["message"]
        assert "token" in result["message"].lower()

    @responses.activate
    def test_connection_403_names_permission_error(self):
        responses.add(responses.GET, ORG_URL,
                      json={"error": "forbidden"}, status=403)

        result = JuniperMistConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "permission" in result["message"].lower()

    @responses.activate
    def test_connection_404_names_org_not_found(self):
        responses.add(responses.GET, ORG_URL,
                      json={"error": "not found"}, status=404)

        result = JuniperMistConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "not found" in result["message"].lower()

    @responses.activate
    def test_connection_network_error_returns_failure(self):
        # Nothing registered — the org call matches no mock, so `responses`
        # raises ConnectionError.
        result = JuniperMistConnector(self.mock_config).test_connection()

        assert result["success"] is False
        assert "Connection failed" in result["message"]


class TestJuniperMistClientCollection:
    """Wireless client collection — sites iteration, record shape, and the
    duration parameter — absent from this suite until now."""

    clients_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "page_size": 100,
        "collect_devices": False,
        "collect_clients": True,
    }

    def _register_sites_and_clients(self):
        responses.add(responses.GET, SITES_URL, json=[
            {"id": "site-001", "name": "HQ"},
            {"id": "site-002", "name": "Branch"},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-001"), json=[
            {"mac": "cc:00:00:00:00:01", "hostname": "laptop1",
             "ip": "10.0.1.10", "ssid": "Corp", "last_seen": 1710000000},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-002"), json=[
            {"mac": "cc:00:00:00:00:02", "hostname": "laptop2",
             "ip": "10.0.2.10", "ssid": "Guest", "last_seen": 1710000000},
        ], status=200)

    @responses.activate
    def test_fetch_clients_iterates_sites_and_shapes_records(self):
        self._register_sites_and_clients()

        connector = JuniperMistConnector(self.clients_config)
        records = list(connector.fetch_data("batch-clients"))

        assert len(records) == 2
        assert all(r["_record_type"] == "client" for r in records)
        by_name = {r["site_name"]: r for r in records}
        # site_name injected from the parent site
        assert by_name["HQ"]["hostname"] == "laptop1"
        assert by_name["Branch"]["hostname"] == "laptop2"
        # 'name' mirrors 'hostname' for field mapping
        for record in records:
            assert record["name"] == record["hostname"]

    @responses.activate
    def test_fetch_clients_skips_sites_with_no_clients(self):
        """A site whose client page is empty contributes zero records."""
        responses.add(responses.GET, SITES_URL, json=[
            {"id": "site-001", "name": "HQ"},
            {"id": "site-002", "name": "Empty Site"},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-001"), json=[
            {"mac": "cc:00:00:00:00:01", "hostname": "laptop1",
             "ip": "10.0.1.10", "ssid": "Corp", "last_seen": 1710000000},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-002"),
                      json=[], status=200)

        connector = JuniperMistConnector(self.clients_config)
        records = list(connector.fetch_data("batch-empty-site"))

        assert len(records) == 1
        assert records[0]["site_name"] == "HQ"

    @responses.activate
    def test_fetch_clients_uses_default_duration(self):
        self._register_sites_and_clients()

        connector = JuniperMistConnector(self.clients_config)
        list(connector.fetch_data("batch-duration"))

        client_calls = [c for c in responses.calls
                        if "/stats/clients" in c.request.url]
        assert len(client_calls) == 2
        for call in client_calls:
            qs = parse_qs(urlparse(call.request.url).query)
            assert qs["duration"] == [DEFAULT_CLIENT_DURATION]

    @responses.activate
    def test_fetch_clients_uses_custom_duration(self):
        self._register_sites_and_clients()

        config = {**self.clients_config, "client_duration": "30d"}
        connector = JuniperMistConnector(config)
        list(connector.fetch_data("batch-duration-30"))

        client_calls = [c for c in responses.calls
                        if "/stats/clients" in c.request.url]
        assert len(client_calls) == 2
        for call in client_calls:
            qs = parse_qs(urlparse(call.request.url).query)
            assert qs["duration"] == ["30d"]

    @responses.activate
    def test_fetch_clients_no_sites_warns_and_yields_nothing(self, caplog):
        responses.add(responses.GET, SITES_URL, json=[], status=200)

        connector = JuniperMistConnector(self.clients_config)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("batch-no-sites"))

        assert records == []
        assert "No sites found" in caplog.text


class TestJuniperMistFetchOrchestration:
    """fetch_data orchestration: collect toggles, multi-org iteration, and the
    device+client union."""

    base_config = {
        "region": "global",
        "api_token": "test-token-abc123",
        "org_id": ORG_ID,
        "page_size": 100,
    }

    def _register_device_endpoints(self, org_url=ORG_URL):
        responses.add(responses.GET, f"{org_url}/inventory", json=[
            {"id": "d1", "mac": "aa:bb:cc:00:00:01", "model": "AP45",
             "serial": "SN1", "type": "ap"},
        ], status=200)
        responses.add(responses.GET, f"{org_url}/stats/devices",
                      json=[], status=200)

    @responses.activate
    def test_fetch_yields_devices_and_clients(self):
        self._register_device_endpoints()
        responses.add(responses.GET, SITES_URL, json=[
            {"id": "site-001", "name": "HQ"},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-001"), json=[
            {"mac": "cc:00:00:00:00:01", "hostname": "laptop1",
             "ip": "10.0.1.10", "ssid": "Corp", "last_seen": 1710000000},
        ], status=200)

        config = {**self.base_config,
                  "collect_devices": True, "collect_clients": True}
        records = list(JuniperMistConnector(config).fetch_data("batch-both"))

        record_types = {r["_record_type"] for r in records}
        assert record_types == {"device", "client"}

    @responses.activate
    def test_skips_devices_when_disabled(self):
        responses.add(responses.GET, SITES_URL, json=[
            {"id": "site-001", "name": "HQ"},
        ], status=200)
        responses.add(responses.GET, CLIENTS_URL.format(site_id="site-001"), json=[
            {"mac": "cc:00:00:00:00:01", "hostname": "laptop1",
             "last_seen": 1710000000},
        ], status=200)

        config = {**self.base_config,
                  "collect_devices": False, "collect_clients": True}
        records = list(JuniperMistConnector(config).fetch_data("batch-no-dev"))

        assert [r["_record_type"] for r in records] == ["client"]
        urls = [c.request.url for c in responses.calls]
        assert not any("/inventory" in url or "/stats/devices" in url
                       for url in urls)

    @responses.activate
    def test_skips_clients_when_disabled(self):
        self._register_device_endpoints()

        config = {**self.base_config,
                  "collect_devices": True, "collect_clients": False}
        records = list(JuniperMistConnector(config).fetch_data("batch-no-cli"))

        assert [r["_record_type"] for r in records] == ["device"]
        urls = [c.request.url for c in responses.calls]
        assert not any("/sites" in url for url in urls)

    @responses.activate
    def test_multi_org_fetch_collects_from_all_orgs(self):
        """Empty org_id discovers orgs and fetches devices from each."""
        responses.add(responses.GET, SELF_ORGS_URL, json=[
            {"id": "org-1", "name": "Org One"},
            {"id": "org-2", "name": "Org Two"},
        ], status=200)
        for org in ("org-1", "org-2"):
            org_url = f"{BASE_URL}/orgs/{org}"
            responses.add(responses.GET, f"{org_url}/inventory", json=[
                {"id": f"d-{org}", "mac": f"aa:bb:cc:00:00:0{org[-1]}",
                 "type": "ap"},
            ], status=200)
            responses.add(responses.GET, f"{org_url}/stats/devices",
                          json=[], status=200)

        config = {**self.base_config, "org_id": "",
                  "collect_devices": True, "collect_clients": False}
        records = list(JuniperMistConnector(config).fetch_data("batch-multi"))

        assert {r["id"] for r in records} == {"d-org-1", "d-org-2"}

    @responses.activate
    def test_no_orgs_discovered_yields_nothing(self):
        responses.add(responses.GET, SELF_ORGS_URL, json=[], status=200)
        responses.add(responses.GET, SELF_URL, json={}, status=200)

        config = {**self.base_config, "org_id": "",
                  "collect_devices": True, "collect_clients": True}
        records = list(JuniperMistConnector(config).fetch_data("batch-no-orgs"))

        assert records == []

    @responses.activate
    def test_stats_only_device_still_yielded(self):
        """A device present in stats but not inventory is still collected."""
        responses.add(responses.GET, INVENTORY_URL, json=[], status=200)
        responses.add(responses.GET, DEVICE_STATS_URL, json=[
            {"id": "d9", "name": "Unknown-AP", "ip": "10.0.1.9",
             "status": "connected", "mac": "ff:ff:ff:00:00:09",
             "last_seen": 1710000000},
        ], status=200)

        config = {**self.base_config,
                  "collect_devices": True, "collect_clients": False}
        records = list(JuniperMistConnector(config).fetch_data("batch-stats"))

        assert len(records) == 1
        assert records[0]["id"] == "d9"
        assert records[0]["name"] == "Unknown-AP"
        assert records[0]["_record_type"] == "device"
