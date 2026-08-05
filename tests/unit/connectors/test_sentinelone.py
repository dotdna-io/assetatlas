# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SentinelOneConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for cursor pagination, record normalization (NIC expansion, field flattening),
and manufacturer/model extraction.
"""

import json
import pytest
import responses
from urllib.parse import urlparse, parse_qs

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.sentinelone_connector import SentinelOneConnector
from src.modules.integrations.base import APIKeyAuth


BASE_URL = "https://tenant.sentinelone.net"
AGENTS_URL = f"{BASE_URL}/web/api/v2.1/agents"
ACCOUNTS_URL = f"{BASE_URL}/web/api/v2.1/accounts"


class TestSentinelOneConnector(ConnectorTestCase):
    connector_class = SentinelOneConnector
    mock_config = {
        "url": BASE_URL,
        "api_token": "test-api-token",
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default single-page agents response for all tests."""
        mock_http.register_from_fixture("GET", AGENTS_URL, "sentinelone/agents_single.json")
        # test_connection also hits accounts + agents
        mock_http.register_from_fixture("GET", ACCOUNTS_URL, "sentinelone/accounts.json")
        mock_http.register_from_fixture("GET", AGENTS_URL, "sentinelone/agents_single.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_api_key_auth(self):
        """Connector must use APIKeyAuth."""
        connector = SentinelOneConnector(self.mock_config)
        assert isinstance(connector.auth, APIKeyAuth)

    def test_auth_uses_apitoken_prefix(self):
        """Auth must use 'ApiToken ' prefix (not Bearer)."""
        connector = SentinelOneConnector(self.mock_config)
        assert connector.auth.api_key_prefix == 'ApiToken '
        assert connector.auth.api_key_header == 'Authorization'
        assert connector.auth.api_key == 'test-api-token'

    # -------------------------------------------------------------------------
    # Connector-specific: init validation
    # -------------------------------------------------------------------------

    def test_missing_url_raises(self):
        """Missing url raises ValueError."""
        with pytest.raises(ValueError, match="URL is required"):
            SentinelOneConnector({"api_token": "tok"})

    def test_missing_api_token_raises(self):
        """Missing api_token raises ValueError."""
        with pytest.raises(ValueError, match="API token is required"):
            SentinelOneConnector({"url": BASE_URL})

    # -------------------------------------------------------------------------
    # Connector-specific: cursor pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_follows_cursor(self):
        """Records from both pages are yielded when nextCursor is present."""
        fixtures = FIXTURES_DIR / "sentinelone"

        with open(fixtures / "agents_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "agents_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, AGENTS_URL, json=page1, status=200)
        responses.add(responses.GET, AGENTS_URL, json=page2, status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 agents; page2 has 1. Page1 agent-001 has 1 NIC (expands to 1),
        # page1 agent-002 has empty networkInterfaces (expands to 1), page2 has no NICs.
        assert len(records) == 3
        computer_names = {r["computerName"] for r in records}
        assert "DESKTOP-WIN001" in computer_names
        assert "LAPTOP-MAC001" in computer_names
        assert "SERVER-LIN001" in computer_names

    @responses.activate
    def test_pagination_cursor_sent_as_param(self):
        """The cursor from page 1 is passed as 'cursor' query param on page 2."""
        fixtures = FIXTURES_DIR / "sentinelone"

        with open(fixtures / "agents_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "agents_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, AGENTS_URL, json=page1, status=200)
        responses.add(responses.GET, AGENTS_URL, json=page2, status=200)

        connector = SentinelOneConnector(self.mock_config)
        list(connector.fetch_data("batch-cursor-param"))

        agent_calls = [c for c in responses.calls if AGENTS_URL in c.request.url]
        assert len(agent_calls) == 2

        qs2 = parse_qs(urlparse(agent_calls[1].request.url).query)
        assert qs2.get("cursor") == ["cursor-page2"]

    @responses.activate
    def test_pagination_single_page_no_cursor(self):
        """When nextCursor is null, only one page is fetched."""
        with open(FIXTURES_DIR / "sentinelone" / "agents_single.json") as f:
            single = json.load(f)

        responses.add(responses.GET, AGENTS_URL, json=single, status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: NIC expansion (mvexpand)
    # -------------------------------------------------------------------------

    def test_nic_expansion_single_nic(self, connector, mock_http):
        """Agent with one NIC yields exactly one record per agent."""
        records = list(connector.fetch_data("batch-nic"))
        assert len(records) == 1
        rec = records[0]
        assert rec["networkInterface_name"] == "Ethernet"
        assert rec["networkInterface_physical"] == "AA:BB:CC:DD:EE:FF"
        assert rec["networkInterface_inet"] == "10.0.1.100"
        assert rec["networkInterface_inet6"] == "fe80::1"

    @responses.activate
    def test_nic_expansion_multi_nic(self):
        """Agent with multiple NICs yields one record per NIC."""
        agent_data = {
            "data": [
                {
                    "id": "agent-multi-nic",
                    "computerName": "SERVER-MULTI",
                    "networkInterfaces": [
                        {"name": "eth0", "physical": "AA:01:01:01:01:01", "inet": ["10.0.0.1"], "inet6": []},
                        {"name": "eth1", "physical": "AA:02:02:02:02:02", "inet": ["10.0.0.2"], "inet6": []}
                    ]
                }
            ],
            "pagination": {"nextCursor": None, "totalItems": 1}
        }

        responses.add(responses.GET, AGENTS_URL, json=agent_data, status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-multi-nic"))

        assert len(records) == 2
        nic_names = {r["networkInterface_name"] for r in records}
        assert "eth0" in nic_names
        assert "eth1" in nic_names

    @responses.activate
    def test_nic_expansion_no_nic_adds_null_fields(self):
        """Agent with no NICs yields a single record with null NIC fields."""
        agent_data = {
            "data": [
                {
                    "id": "agent-no-nic",
                    "computerName": "SERVER-NONIC",
                    "networkInterfaces": []
                }
            ],
            "pagination": {"nextCursor": None, "totalItems": 1}
        }

        responses.add(responses.GET, AGENTS_URL, json=agent_data, status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("batch-no-nic"))

        assert len(records) == 1
        rec = records[0]
        assert rec["networkInterface_name"] is None
        assert rec["networkInterface_inet"] is None

    # -------------------------------------------------------------------------
    # Connector-specific: field normalization
    # -------------------------------------------------------------------------

    def test_normalize_extracts_manufacturer_from_model(self):
        """modelName in 'MANUFACTURER - MODEL' format is split into separate fields."""
        connector = SentinelOneConnector(self.mock_config)
        record = {"modelName": "LENOVO - ThinkPad T14"}
        connector._extract_manufacturer_from_model(record)

        assert record["modelName_manufacturer"] == "LENOVO"
        assert record["modelName_model"] == "ThinkPad T14"
        assert record["modelName"] == "LENOVO - ThinkPad T14"  # original preserved

    def test_normalize_model_no_separator_not_split(self):
        """modelName without separator pattern is not split."""
        connector = SentinelOneConnector(self.mock_config)
        record = {"modelName": "MacBook Pro 14"}
        connector._extract_manufacturer_from_model(record)

        assert "modelName_manufacturer" not in record
        assert "modelName_model" not in record

    def test_normalize_active_protection_list_to_csv(self):
        """activeProtection list is converted to comma-separated string."""
        connector = SentinelOneConnector(self.mock_config)
        record = {
            "activeProtection": ["agent-policy", "firewall"],
            "networkInterfaces": []
        }
        result = connector._normalize_record(record)
        assert len(result) == 1
        assert result[0]["activeProtection"] == "agent-policy, firewall"

    def test_normalize_drops_excluded_fields(self):
        """DROP_FIELDS are removed from the record."""
        connector = SentinelOneConnector(self.mock_config)
        record = {
            "id": "a-001",
            "computerName": "TEST",
            "proxyStates": [{"some": "data"}],
            "missingPermissions": ["perm1"],
            "userActionsNeeded": [],
            "locations": [{"loc": "A"}],
            "networkInterfaces": []
        }
        result = connector._normalize_record(record)
        assert len(result) == 1
        rec = result[0]
        assert "proxyStates" not in rec
        assert "missingPermissions" not in rec
        assert "userActionsNeeded" not in rec
        assert "locations" not in rec

    def test_normalize_flattens_active_directory(self):
        """activeDirectory dict is flattened with activeDirectory_ prefix."""
        connector = SentinelOneConnector(self.mock_config)
        record = {
            "activeDirectory": {
                "computerDistinguishedName": "CN=PC,DC=corp,DC=com",
                "computerMemberOf": ["CN=Group1,DC=corp,DC=com", "CN=Group2,DC=corp,DC=com"],
                "userPrincipalName": "user@corp.com"
            },
            "networkInterfaces": []
        }
        result = connector._normalize_record(record)
        rec = result[0]

        assert rec["activeDirectory_computerDistinguishedName"] == "CN=PC,DC=corp,DC=com"
        assert rec["activeDirectory_computerMemberOf"] == "CN=Group1,DC=corp,DC=com, CN=Group2,DC=corp,DC=com"
        assert rec["activeDirectory_userPrincipalName"] == "user@corp.com"
        assert "activeDirectory" not in rec

    # -------------------------------------------------------------------------
    # Connector-specific: metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_sentinelone(self, connector, mock_http):
        """The _source metadata field must be 'sentinelone'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "sentinelone"

    def test_record_type_is_agent(self, connector, mock_http):
        """The _record_type field must be 'agent'."""
        records = list(connector.fetch_data("batch-rectype"))
        assert records[0]["_record_type"] == "agent"


# ---------------------------------------------------------------------------
# Issue #1334 — paginator cap-hit surfaces as a collection warning
# ---------------------------------------------------------------------------


def _agents_page(agent_id, next_cursor):
    return {
        "data": [
            {"id": agent_id, "computerName": agent_id, "networkInterfaces": []}
        ],
        "pagination": {"nextCursor": next_cursor},
    }


class TestSentinelOneTruncation:
    """A collection stopped by the page cap must record exactly one warning
    naming the resource and the cap — the pipeline reads a silent shortfall
    as deleted assets."""

    mock_config = {
        "url": BASE_URL,
        "api_token": "test-api-token",
        # Shrink the 500-page cap so a cap-hit takes two mocked pages.
        "max_pagination_pages": 2,
    }

    @responses.activate
    def test_capped_run_records_one_warning(self):
        # Two full pages, each handing back a fresh cursor: more data waiting.
        responses.add(responses.GET, AGENTS_URL, json=_agents_page("agent-1", "cursor-2"), status=200)
        responses.add(responses.GET, AGENTS_URL, json=_agents_page("agent-2", "cursor-3"), status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "SentinelOne agents truncated at the 2-page cap" in warnings[0]

    @responses.activate
    def test_end_of_data_records_no_warning(self):
        """The off-by-one: a final page that fills the cap but hands back no
        cursor is end-of-data, not truncation."""
        responses.add(responses.GET, AGENTS_URL, json=_agents_page("agent-1", "cursor-2"), status=200)
        responses.add(responses.GET, AGENTS_URL, json=_agents_page("agent-2", None), status=200)

        connector = SentinelOneConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        assert connector.collection_warnings == []

    def test_config_schema_declares_max_pagination_pages(self):
        """The cap is operator-settable: declared with the connector's default."""
        field = SentinelOneConnector.get_config_schema()["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["required"] is False
        assert field["default"] == 500
