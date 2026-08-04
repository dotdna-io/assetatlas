# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SolarWindsConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for BasicAuth, _record_type metadata, SWQL pagination, and module
probing.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.solarwinds_connector import SolarWindsConnector
from src.modules.integrations.base import BasicAuth


SERVER = "solarwinds.corp.com"
PORT = 17774
SWIS_URL = f"https://{SERVER}:{PORT}/SolarWinds/InformationService/v3/Json/Query"


class TestSolarWindsConnector(ConnectorTestCase):
    connector_class = SolarWindsConnector
    mock_config = {
        "server_url": SERVER,
        "port": PORT,
        "username": "admin",
        "password": "secret",
        "verify_ssl": False,
        "page_size": 1000,
        "request_timeout": 10,
        "page_delay": 0,
        "discover_vms": False,
        "discover_endpoints": False,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default SWIS responses for all tests."""
        # Nodes query (single page)
        mock_http.register_from_fixture("POST", SWIS_URL, "solarwinds/nodes_single.json")
        # Empty page to stop pagination
        mock_http.register_json("POST", SWIS_URL, {"results": []})
        # For test_connection: test query + version query
        mock_http.register_from_fixture("POST", SWIS_URL, "solarwinds/test_query.json")
        mock_http.register_from_fixture("POST", SWIS_URL, "solarwinds/version.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_basic(self):
        connector = SolarWindsConnector(self.mock_config)
        assert isinstance(connector.auth, BasicAuth)

    # -------------------------------------------------------------------------
    # Connector-specific: _record_type metadata
    # -------------------------------------------------------------------------

    def test_record_type_node_injected(self, connector, mock_http):
        records = list(connector.fetch_data("batch-rt"))
        assert len(records) > 0
        assert records[0]["_record_type"] == "node"

    # -------------------------------------------------------------------------
    # Connector-specific: source metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_solarwinds(self, connector, mock_http):
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "solarwinds_orion"

    # -------------------------------------------------------------------------
    # Connector-specific: record fields
    # -------------------------------------------------------------------------

    def test_node_fields_extracted(self, connector, mock_http):
        records = list(connector.fetch_data("batch-fields"))
        assert len(records) > 0
        rec = records[0]
        assert rec["NodeID"] == 1
        assert rec["IPAddress"] == "10.0.1.1"
        assert rec["Caption"] == "core-switch-01"
        assert rec["Vendor"] == "Cisco"
        assert rec["SerialNumber"] == "FDO1234A5BC"

    # -------------------------------------------------------------------------
    # Connector-specific: validation
    # -------------------------------------------------------------------------

    def test_missing_server_url_raises(self):
        config = {**self.mock_config, "server_url": ""}
        with pytest.raises(ValueError, match="server URL"):
            SolarWindsConnector(config)

    def test_missing_username_raises(self):
        config = {**self.mock_config, "username": ""}
        with pytest.raises(ValueError, match="username"):
            SolarWindsConnector(config)

    def test_missing_password_raises(self):
        config = {**self.mock_config, "password": ""}
        with pytest.raises(ValueError, match="password"):
            SolarWindsConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: SWIS URL construction
    # -------------------------------------------------------------------------

    def test_swis_url_constructed(self):
        connector = SolarWindsConnector(self.mock_config)
        assert connector.swis_url == SWIS_URL

    def test_custom_port(self):
        config = {**self.mock_config, "port": 17778}
        connector = SolarWindsConnector(config)
        assert ":17778/" in connector.swis_url
