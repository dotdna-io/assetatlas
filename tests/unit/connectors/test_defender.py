# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for DefenderConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for @odata.nextLink pagination and Defender-specific behaviour.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.defender_connector import DefenderConnector


TOKEN_URL = "https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token"
MACHINES_URL = "https://api.securitycenter.microsoft.com/api/machines"
MACHINES_PAGE2_URL = (
    "https://api.securitycenter.microsoft.com/api/machines"
    "?$top=10000&$skiptoken=page2token"
)


class TestDefenderConnector(ConnectorTestCase):
    connector_class = DefenderConnector
    mock_config = {
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "page_size": 10000,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default OAuth token + single-page machines response for all tests."""
        mock_http.register_auth_success(TOKEN_URL)
        mock_http.register_from_fixture("GET", MACHINES_URL, "defender/machines_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: @odata.nextLink pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_next_link(self):
        """Records from both pages are yielded when @odata.nextLink is present."""
        fixtures = FIXTURES_DIR / "defender"

        with open(fixtures / "machines_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "machines_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        # Page 1 — includes @odata.nextLink
        responses.add(responses.GET, MACHINES_URL, json=page1, status=200)
        # Page 2 — no nextLink, terminates pagination
        responses.add(responses.GET, MACHINES_PAGE2_URL, json=page2, status=200)

        connector = DefenderConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 machines, page2 has 1 → 3 total
        assert len(records) == 3
        names = {r["computerDnsName"] for r in records}
        assert "DESKTOP-CORP001.contoso.com" in names
        assert "LAPTOP-DEV002.contoso.com" in names
        assert "SERVER-PROD003.contoso.com" in names

    @responses.activate
    def test_pagination_single_page_no_next_link(self):
        """When there is no @odata.nextLink, only one page is fetched."""
        with open(FIXTURES_DIR / "defender" / "machines_single.json") as f:
            single = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, MACHINES_URL, json=single, status=200)

        connector = DefenderConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: machine fields
    # -------------------------------------------------------------------------

    def test_expected_fields_present(self, connector, mock_http):
        """Core Defender machine fields are present in fetched records."""
        records = list(connector.fetch_data("batch-fields"))
        assert len(records) > 0
        record = records[0]
        for field in ("id", "computerDnsName", "osPlatform", "healthStatus"):
            assert field in record, f"Expected field '{field}' missing from record"

    def test_schema_has_all_machine_fields(self, connector):
        """get_schema() returns all MACHINE_FIELDS."""
        schema = connector.get_schema()
        schema_field_names = {f["name"] for f in schema["fields"]}
        for field in DefenderConnector.MACHINE_FIELDS:
            assert field in schema_field_names, f"'{field}' missing from schema"

    # -------------------------------------------------------------------------
    # Connector-specific: computed network fields (ip_list, mac_list)
    # -------------------------------------------------------------------------

    def test_compute_network_fields_filters_by_operational_status(self):
        """Only interfaces with operationalStatus 'Up' are included."""
        record = {
            'ipAddresses': [
                {'ipAddress': '10.0.1.1', 'macAddress': 'AABBCCDDEEFF', 'operationalStatus': 'Up', 'type': 'Ethernet'},
                {'ipAddress': '169.254.1.1', 'macAddress': '112233445566', 'operationalStatus': 'Down', 'type': 'Ethernet'},
                {'ipAddress': '10.0.1.2', 'macAddress': 'FFEEDDCCBBAA', 'operationalStatus': 'Up', 'type': 'Ethernet'},
            ]
        }
        DefenderConnector._compute_network_fields(record)
        assert record['ip_list'] == ['10.0.1.1', '10.0.1.2']
        assert record['mac_list'] == ['AABBCCDDEEFF', 'FFEEDDCCBBAA']

    def test_compute_network_fields_empty_ip_addresses(self):
        """Empty or missing ipAddresses yields None for both fields."""
        record = {'ipAddresses': []}
        DefenderConnector._compute_network_fields(record)
        assert record['ip_list'] is None
        assert record['mac_list'] is None

        record_no_key = {}
        DefenderConnector._compute_network_fields(record_no_key)
        assert record_no_key['ip_list'] is None
        assert record_no_key['mac_list'] is None

    def test_compute_network_fields_all_down(self):
        """All interfaces Down → None for both fields."""
        record = {
            'ipAddresses': [
                {'ipAddress': '169.254.1.1', 'macAddress': '112233445566', 'operationalStatus': 'Down', 'type': 'Ethernet'},
            ]
        }
        DefenderConnector._compute_network_fields(record)
        assert record['ip_list'] is None
        assert record['mac_list'] is None

    def test_compute_network_fields_deduplicates(self):
        """Duplicate IPs/MACs across Up interfaces are kept only once."""
        record = {
            'ipAddresses': [
                {'ipAddress': '10.0.1.1', 'macAddress': 'AABBCCDDEEFF', 'operationalStatus': 'Up', 'type': 'Ethernet'},
                {'ipAddress': '10.0.1.1', 'macAddress': 'AABBCCDDEEFF', 'operationalStatus': 'Up', 'type': 'Other'},
            ]
        }
        DefenderConnector._compute_network_fields(record)
        assert record['ip_list'] == ['10.0.1.1']
        assert record['mac_list'] == ['AABBCCDDEEFF']

    def test_computed_fields_present_in_fetched_records(self, connector, mock_http):
        """ip_list and mac_list are present in fetched records."""
        records = list(connector.fetch_data("batch-computed"))
        record = records[0]
        assert 'ip_list' in record
        assert 'mac_list' in record
        assert record['ip_list'] == ['10.0.1.50']
        assert record['mac_list'] == ['001A2B3C4D5E']

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection
    # -------------------------------------------------------------------------

    def test_metadata_source_is_defender(self, connector, mock_http):
        """The _source metadata field must be 'defender'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "defender"

    # -------------------------------------------------------------------------
    # Connector-specific: page_size sent to API
    # -------------------------------------------------------------------------

    @responses.activate
    def test_page_size_param_sent_on_first_request(self):
        """$top parameter matching page_size is sent on the first request."""
        with open(FIXTURES_DIR / "defender" / "machines_single.json") as f:
            body = json.load(f)

        responses.add(responses.POST, TOKEN_URL,
                      json={"access_token": "tok", "expires_in": 3600}, status=200)
        responses.add(responses.GET, MACHINES_URL, json=body, status=200)

        config = {**self.mock_config, "page_size": 500}
        connector = DefenderConnector(config)
        list(connector.fetch_data("batch-pagesize"))

        machine_calls = [c for c in responses.calls if MACHINES_URL in c.request.url]
        assert machine_calls, "No GET /machines call recorded"

        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(machine_calls[0].request.url).query)
        assert "$top" in qs
        assert qs["$top"][0] == "500"
