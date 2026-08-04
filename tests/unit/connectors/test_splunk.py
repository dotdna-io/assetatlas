# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SplunkConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for search job workflow, pagination, _search_id metadata, and BasicAuth.
"""

import json
import pytest
import responses

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.splunk_connector import SplunkConnector
from src.modules.integrations.base import BasicAuth


BASE_URL = "https://splunk.example.com:8089"
JOBS_URL = f"{BASE_URL}/services/search/jobs"
JOB_STATUS_URL = f"{JOBS_URL}/splunk-search-001"
JOB_RESULTS_URL = f"{JOBS_URL}/splunk-search-001/results"


class TestSplunkConnector(ConnectorTestCase):
    connector_class = SplunkConnector
    mock_config = {
        "url": BASE_URL,
        "username": "admin",
        "password": "changeme",
        "search_query": "search index=assets | table host ip mac serial os",
        "earliest_time": "-24h",
        "latest_time": "now",
        "max_count": 10000,
        "poll_interval": 0,  # no delay in tests
        "poll_timeout": 10,
        "verify_ssl": False,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default search job workflow for all tests."""
        # Create search job
        mock_http.register_from_fixture("POST", JOBS_URL, "splunk/search_job_created.json")
        # Job status — DONE
        mock_http.register_from_fixture("GET", JOB_STATUS_URL, "splunk/search_status_done.json")
        # Single page of results
        mock_http.register_from_fixture("GET", JOB_RESULTS_URL, "splunk/search_results_single.json")
        # Empty page to terminate pagination
        mock_http.register_json("GET", JOB_RESULTS_URL, {"results": []})
        # For test_connection — needs job creation + status
        mock_http.register_from_fixture("POST", JOBS_URL, "splunk/search_job_created.json")
        mock_http.register_from_fixture("GET", JOB_STATUS_URL, "splunk/search_status_done.json")

    # -------------------------------------------------------------------------
    # Connector-specific: auth strategy
    # -------------------------------------------------------------------------

    def test_auth_is_basic(self):
        connector = SplunkConnector(self.mock_config)
        assert isinstance(connector.auth, BasicAuth)

    # -------------------------------------------------------------------------
    # Connector-specific: _search_id metadata
    # -------------------------------------------------------------------------

    def test_search_id_injected(self, connector, mock_http):
        records = list(connector.fetch_data("batch-sid"))
        assert len(records) > 0
        assert records[0]["_search_id"] == "splunk-search-001"

    # -------------------------------------------------------------------------
    # Connector-specific: source metadata
    # -------------------------------------------------------------------------

    def test_metadata_source_is_splunk(self, connector, mock_http):
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "splunk"

    # -------------------------------------------------------------------------
    # Connector-specific: pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_fetches_multiple_pages(self):
        fixtures = FIXTURES_DIR / "splunk"

        # Job creation
        with open(fixtures / "search_job_created.json") as f:
            job = json.load(f)
        with open(fixtures / "search_status_done.json") as f:
            status = json.load(f)
        with open(fixtures / "search_results_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "search_results_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.POST, JOBS_URL, json=job, status=200)
        responses.add(responses.GET, JOB_STATUS_URL, json=status, status=200)
        # page1 has 2 results, page2 has 1
        responses.add(responses.GET, JOB_RESULTS_URL, json=page1, status=200)
        responses.add(responses.GET, JOB_RESULTS_URL, json=page2, status=200)

        config = {**self.mock_config, "page_size": 2}
        connector = SplunkConnector(config)
        records = list(connector.fetch_data("batch-page"))

        assert len(records) == 3
        hosts = {r["host"] for r in records}
        assert "DESKTOP-WIN001" in hosts
        assert "SERVER-LIN001" in hosts
        assert "LAPTOP-MAC001" in hosts

    # -------------------------------------------------------------------------
    # Connector-specific: validation
    # -------------------------------------------------------------------------

    def test_missing_url_raises(self):
        config = {**self.mock_config, "url": ""}
        with pytest.raises(ValueError, match="URL is required"):
            SplunkConnector(config)

    def test_missing_username_raises(self):
        config = {**self.mock_config, "username": ""}
        with pytest.raises(ValueError, match="username is required"):
            SplunkConnector(config)

    def test_missing_password_raises(self):
        config = {**self.mock_config, "password": ""}
        with pytest.raises(ValueError, match="password is required"):
            SplunkConnector(config)

    def test_missing_search_query_raises(self):
        config = {**self.mock_config, "search_query": ""}
        with pytest.raises(ValueError, match="search query is required"):
            SplunkConnector(config)
