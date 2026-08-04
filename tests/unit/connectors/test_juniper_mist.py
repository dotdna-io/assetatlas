# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for JuniperMistConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for APIKeyAuth, _record_type metadata, inventory+stats merge,
and org discovery.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.juniper_mist_connector import JuniperMistConnector
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
