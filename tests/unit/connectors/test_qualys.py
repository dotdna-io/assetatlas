# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for QualysConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for XML parsing, URL-based pagination, rate-limit handling, and
platform URL resolution.
"""

import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, FIXTURES_DIR
from src.modules.integrations.connectors.qualys_connector import QualysConnector

BASE_URL = "https://qualysapi.qualys.com"
HOST_LIST_URL = f"{BASE_URL}/api/2.0/fo/asset/host/"

# Pagination URL extracted from hosts_page1.xml WARNING element
PAGE2_URL = f"{BASE_URL}/api/2.0/fo/asset/host/?action=list&id_min=1003&truncation_limit=2"

FIXTURES = FIXTURES_DIR / "qualys"


def _xml(filename: str) -> bytes:
    return (FIXTURES / filename).read_bytes()


class TestQualysConnector(ConnectorTestCase):
    connector_class = QualysConnector
    mock_config = {
        "platform": "US1",
        "username": "api_user",
        "password": "api_pass",
        "truncation_limit": 100,
        "include_cloud_metadata": True,
        "include_tags": True,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default single-host XML response for inherited tests."""
        mock_http.rsps.add(
            responses.GET,
            HOST_LIST_URL,
            body=_xml("hosts_single.xml"),
            status=200,
            content_type="text/xml",
        )

    # -------------------------------------------------------------------------
    # Connector-specific: platform URL resolution
    # -------------------------------------------------------------------------

    def test_us1_platform_resolves(self):
        connector = QualysConnector(self.mock_config)
        assert connector.base_url == "https://qualysapi.qualys.com"
        assert connector.platform == "US1"

    def test_eu1_platform_resolves(self):
        config = {**self.mock_config, "platform": "EU1"}
        connector = QualysConnector(config)
        assert connector.base_url == "https://qualysapi.qualys.eu"

    def test_custom_platform_uses_api_url(self):
        config = {
            **self.mock_config,
            "platform": "CUSTOM",
            "api_url": "https://qualysapi.example.com",
        }
        connector = QualysConnector(config)
        assert connector.base_url == "https://qualysapi.example.com"

    def test_custom_platform_missing_url_raises(self):
        config = {**self.mock_config, "platform": "CUSTOM", "api_url": ""}
        with pytest.raises(ValueError, match="Custom API URL is required"):
            QualysConnector(config)

    def test_unknown_platform_raises(self):
        config = {**self.mock_config, "platform": "XX9"}
        with pytest.raises(ValueError, match="Unknown platform"):
            QualysConnector(config)

    def test_missing_username_raises(self):
        config = {**self.mock_config, "username": ""}
        with pytest.raises(ValueError, match="username is required"):
            QualysConnector(config)

    def test_missing_password_raises(self):
        config = {**self.mock_config, "password": ""}
        with pytest.raises(ValueError, match="password is required"):
            QualysConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: X-Requested-With header injected
    # -------------------------------------------------------------------------

    def test_xrequested_with_header_set(self):
        connector = QualysConnector(self.mock_config)
        assert connector.auth.session.headers.get("X-Requested-With") == "AssetAtlas"

    # -------------------------------------------------------------------------
    # Connector-specific: XML record parsing
    # -------------------------------------------------------------------------

    @responses.activate
    def test_parse_extracts_key_fields(self):
        """XML parsing extracts all expected fields from a HOST element."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-parse"))

        assert len(records) == 1
        rec = records[0]
        assert rec["id"] == "12345"
        assert rec["asset_id"] == "67890"
        assert rec["ip_address"] == "10.0.0.1"
        assert rec["hostname"] == "server01"  # from DNS_DATA
        assert rec["fqdn"] == "server01.example.com"
        assert rec["domain"] == "example.com"
        assert rec["operating_system"] == "Windows Server 2022"
        assert rec["serial_number"] == "SN-WIN-001"
        assert rec["hardware_uuid"] == "uuid-1111-2222-3333"
        assert rec["agent_status"] == "Running"
        assert rec["cloud_provider"] == "AWS"

    @responses.activate
    def test_parse_timestamps_to_iso(self):
        """Qualys datetime strings are converted to ISO format."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-ts"))
        rec = records[0]

        assert rec["last_scan_datetime"] == "2026-03-18T08:00:00"
        assert rec["last_boot"] == "2026-03-10T06:00:00"
        assert rec["first_found_date"] == "2025-01-01T00:00:00"

    @responses.activate
    def test_parse_tags_as_list(self):
        """Tags are returned as a list of name strings."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-tags"))
        rec = records[0]

        assert isinstance(rec["tags"], list)
        assert "Production" in rec["tags"]
        assert "Windows" in rec["tags"]

    @responses.activate
    def test_parse_asset_groups_as_list(self):
        """Asset group IDs are split into a list."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-groups"))
        rec = records[0]

        assert rec["asset_groups"] == ["101", "102"]

    @responses.activate
    def test_parse_trurisk_and_criticality_scores(self):
        """TruRisk and criticality scores are parsed as integers."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-scores"))
        rec = records[0]

        assert rec["trurisk_score"] == 750
        assert rec["criticality_score"] == 4

    @responses.activate
    def test_parse_cloud_metadata_ec2(self):
        """EC2 cloud metadata is parsed into a nested dict."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-cloud"))
        rec = records[0]

        assert rec["cloud_metadata"] is not None
        assert "EC2" in rec["cloud_metadata"]
        assert rec["cloud_metadata"]["EC2"]["instanceId"] == "i-0123456789abcdef0"
        assert rec["cloud_metadata"]["EC2"]["region"] == "us-east-1"

    # -------------------------------------------------------------------------
    # Connector-specific: URL-based pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_fetches_all_pages(self):
        """Records from both pages are yielded when WARNING URL is present."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_page1.xml"), status=200, content_type="text/xml",
        )
        responses.add(
            responses.GET, PAGE2_URL,
            body=_xml("hosts_page2.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 hosts, page2 has 1 = 3 total
        assert len(records) == 3
        ids = {r["id"] for r in records}
        assert ids == {"1001", "1002", "1003"}

    @responses.activate
    def test_pagination_single_page_no_warning(self):
        """When there is no WARNING element, only one page is fetched."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    @responses.activate
    def test_pagination_page2_uses_full_url(self):
        """The second request uses the full URL from the WARNING element."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_page1.xml"), status=200, content_type="text/xml",
        )
        responses.add(
            responses.GET, PAGE2_URL,
            body=_xml("hosts_page2.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        list(connector.fetch_data("batch-url-check"))

        # Second call should be to the pagination URL (via session.get, not auth.request path)
        calls = [c for c in responses.calls]
        assert len(calls) == 2
        assert PAGE2_URL in calls[1].request.url

    # -------------------------------------------------------------------------
    # Connector-specific: paginator cap-hit → collection warning
    # (the cap is the max_pagination_pages config key — unrelated to the
    # truncation_limit config key, which is Qualys' per-request page size)
    # -------------------------------------------------------------------------

    @responses.activate
    def test_cap_hit_records_one_collection_warning(self):
        """A run stopped by the configured page cap records exactly one warning."""
        # Both pages carry a WARNING with a follow-up URL — only the cap ends
        # the loop.
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_page1.xml"), status=200, content_type="text/xml",
        )
        responses.add(
            responses.GET, PAGE2_URL,
            body=_xml("hosts_page1.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector({**self.mock_config, "max_pagination_pages": 2})
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 4  # 2 hosts per page × 2 pages
        warnings = connector.collection_warnings
        assert len(warnings) == 1
        assert "Qualys hosts" in warnings[0]
        assert "2-page cap" in warnings[0]

    @responses.activate
    def test_end_of_data_at_cap_boundary_records_no_warning(self):
        """A final page that fills the cap but has no WARNING is end-of-data."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_page1.xml"), status=200, content_type="text/xml",
        )
        responses.add(
            responses.GET, PAGE2_URL,
            body=_xml("hosts_page2.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector({**self.mock_config, "max_pagination_pages": 2})
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 3
        assert connector.collection_warnings == []

    def test_config_schema_declares_max_pagination_pages(self):
        """The cap is operator-settable: declared with the connector's default."""
        field = QualysConnector.get_config_schema()["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["required"] is False
        assert field["default"] == 100

    # -------------------------------------------------------------------------
    # Connector-specific: test_connection
    # -------------------------------------------------------------------------

    @responses.activate
    def test_connection_success_with_more_hosts(self):
        """test_connection returns success and reports hosts available when WARNING present."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("connection_test.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()

        assert result["success"] is True
        assert "Hosts available for collection" in result["message"]
        assert result["metadata"]["platform"] == "US1"

    @responses.activate
    def test_connection_failure_api_error(self):
        """test_connection returns failure when Qualys returns a SIMPLE_RETURN error."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("error_auth.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()

        assert result["success"] is False
        assert "Authentication failed" in result["message"]

    @responses.activate
    def test_connection_failure_non_xml(self):
        """test_connection returns failure when the response is not valid XML."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=b"<html>Internal Server Error</html>TRUNCATED",
            status=200, content_type="text/html",
        )

        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()

        assert result["success"] is False

    @responses.activate
    def test_connection_failure_network_error(self):
        """test_connection returns failure dict on network exception."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=ConnectionError("Network unreachable"),
        )

        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()

        assert result["success"] is False
        assert "Connection failed" in result["message"]

    # -------------------------------------------------------------------------
    # Connector-specific: metadata injection
    # -------------------------------------------------------------------------

    @responses.activate
    def test_metadata_source_is_qualys_assets(self):
        """The _source metadata field must be 'qualys_assets'."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
        )

        connector = QualysConnector(self.mock_config)
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "qualys_assets"

    # -------------------------------------------------------------------------
    # Connector-specific: rate limit tracking
    # -------------------------------------------------------------------------

    @responses.activate
    def test_rate_limit_headers_parsed(self):
        """Rate limit headers from the response update internal state."""
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("hosts_single.xml"), status=200, content_type="text/xml",
            headers={"X-RateLimit-Remaining": "42", "X-RateLimit-ToWait-Sec": "10"},
        )

        connector = QualysConnector(self.mock_config)
        list(connector.fetch_data("batch-ratelimit"))

        assert connector._rate_limit_remaining == 42
        assert connector._rate_limit_to_wait == 10

    # -------------------------------------------------------------------------
    # ConnectorTestCase overrides — use @responses.activate to isolate from autouse
    # -------------------------------------------------------------------------

    @responses.activate
    def test_connection_success(self):
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("connection_test.xml"), status=200, content_type="text/xml",
        )
        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()
        assert result["success"] is True
        assert "message" in result

    @responses.activate
    def test_connection_auth_failure(self):
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=_xml("error_auth.xml"), status=200, content_type="text/xml",
        )
        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()
        assert result["success"] is False

    @responses.activate
    def test_connection_network_error(self):
        responses.add(
            responses.GET, HOST_LIST_URL,
            body=ConnectionError("unreachable"),
        )
        connector = QualysConnector(self.mock_config)
        result = connector.test_connection()
        assert result["success"] is False

    @responses.activate
    def test_handles_server_error(self):
        # The retry adapter retries on 500 three times before raising
        for _ in range(4):
            responses.add(responses.GET, HOST_LIST_URL, status=500)
        connector = QualysConnector(self.mock_config)
        with pytest.raises(Exception):
            list(connector.fetch_data("batch-err"))
