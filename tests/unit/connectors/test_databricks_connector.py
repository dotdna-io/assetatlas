# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Databricks connector."""
import json
import logging
import time

import pytest
import requests
import responses
from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP
from src.modules.integrations.connectors.databricks_connector import DatabricksConnector
from src.modules.integrations.base import APIKeyAuth, OAuth2ClientCredentials


# Base URL used in all mocks
WORKSPACE_URL = "https://test-workspace.cloud.databricks.com"

# Minimal config for PAT auth (used by most tests)
PAT_CONFIG = {
    "workspace_url": WORKSPACE_URL,
    "auth_type": "pat",
    "token": "dapi-test-token-1234567890",
    "page_size": 100,
    "collect_unity_catalog": False,
}

OAUTH_CONFIG = {
    "workspace_url": WORKSPACE_URL,
    "auth_type": "oauth",
    "client_id": "11111111-2222-3333-4444-555555555555",
    "client_secret": "test-secret",
    "page_size": 100,
    "collect_unity_catalog": False,
}


class TestDatabricksConnector(ConnectorTestCase):
    connector_class = DatabricksConnector
    mock_config = PAT_CONFIG

    @pytest.fixture
    def connector(self):
        return DatabricksConnector(PAT_CONFIG)

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http: MockHTTP):
        """Register default mocks so inherited ConnectorTestCase tests pass."""
        # Clusters (no next_page_token = single page)
        mock_http.register_json(
            "GET",
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            {
                "clusters": [
                    {
                        "cluster_id": "0123-456789-abc",
                        "cluster_name": "test-cluster",
                        "state": "TERMINATED",
                        "spark_version": "13.3.x-scala2.12",
                        "node_type_id": "i3.xlarge",
                        "num_workers": 4,
                        "creator_user_name": "user@example.com",
                        "start_time": 1700000000000,
                    }
                ]
            },
        )
        # Jobs (no next_page_token, has_more=False)
        mock_http.register_json(
            "GET",
            f"{WORKSPACE_URL}/api/2.1/jobs/list",
            {
                "jobs": [
                    {
                        "job_id": 111,
                        "created_time": 1700000000000,
                        "creator_user_name": "user@example.com",
                        "settings": {"name": "ETL-daily"},
                    }
                ],
                "has_more": False,
            },
        )
        # SQL Warehouses
        mock_http.register_json(
            "GET",
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses",
            {
                "warehouses": [
                    {
                        "id": "wh-001",
                        "name": "default-warehouse",
                        "cluster_size": "Small",
                        "state": "RUNNING",
                        "creator_name": "user@example.com",
                        "warehouse_type": "PRO",
                    }
                ]
            },
        )

    @responses.activate
    def test_connection_auth_failure(self, connector, mock_http):
        responses.reset()
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "PERMISSION_DENIED"},
            status=403,
        )
        result = connector.test_connection()
        assert result["success"] is False

    @responses.activate
    def test_connection_network_error(self, connector, mock_http):
        responses.reset()
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            body=ConnectionError("Connection refused"),
        )
        result = connector.test_connection()
        assert result["success"] is False

    @responses.activate
    def test_handles_rate_limit(self, connector, mock_http):
        """Rate limiter handles 429 via urllib3 retry in auth strategy."""
        responses.reset()
        # urllib3 Retry(total=3) consumes up to 4 attempts; register enough 429s then success
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=429, headers={"Retry-After": "0"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=429, headers={"Retry-After": "0"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=429, headers={"Retry-After": "0"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c1", "cluster_name": "c", "state": "RUNNING"}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})
        records = list(connector.fetch_data("test-batch"))
        assert len(records) >= 1

    @responses.activate
    def test_handles_server_error(self, connector, mock_http):
        """Server errors are retried via urllib3 retry in auth strategy."""
        responses.reset()
        # urllib3 Retry(total=3) consumes up to 4 attempts; register enough 503s then success
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=503)
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=503)
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", status=503)
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c1", "cluster_name": "c", "state": "RUNNING"}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})
        records = list(connector.fetch_data("test-batch"))
        assert len(records) >= 1


class TestDatabricksAuth:
    def test_pat_auth_builds_api_key_strategy(self):
        connector = DatabricksConnector(PAT_CONFIG)
        assert connector.auth is not None
        assert isinstance(connector.auth, APIKeyAuth)

    def test_oauth_auth_builds_oauth2_strategy(self):
        config = {
            "workspace_url": WORKSPACE_URL,
            "auth_type": "oauth",
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }
        connector = DatabricksConnector(config)
        assert connector.auth is not None
        assert isinstance(connector.auth, OAuth2ClientCredentials)

    def test_pat_auth_missing_token_raises(self):
        config = {
            "workspace_url": WORKSPACE_URL,
            "auth_type": "pat",
        }
        with pytest.raises(ValueError, match="token"):
            DatabricksConnector(config)

    def test_oauth_missing_client_id_raises(self):
        config = {
            "workspace_url": WORKSPACE_URL,
            "auth_type": "oauth",
            "client_secret": "secret",
        }
        with pytest.raises(ValueError, match="client_id"):
            DatabricksConnector(config)

    def test_oauth_missing_client_secret_raises(self):
        config = {
            "workspace_url": WORKSPACE_URL,
            "auth_type": "oauth",
            "client_id": "id",
        }
        with pytest.raises(ValueError, match="client_secret"):
            DatabricksConnector(config)


class TestDatabricksTestConnection:
    @responses.activate
    def test_connection_success(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={
                "clusters": [
                    {"cluster_id": "c1", "cluster_name": "cluster-1"},
                    {"cluster_id": "c2", "cluster_name": "cluster-2"},
                ]
            },
            status=200,
        )
        connector = DatabricksConnector(PAT_CONFIG)
        result = connector.test_connection()
        assert result["success"] is True
        assert "2" in result["message"]
        assert result["metadata"]["cluster_count"] == 2

    @responses.activate
    def test_connection_auth_failure(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "PERMISSION_DENIED", "message": "Invalid token"},
            status=403,
        )
        connector = DatabricksConnector(PAT_CONFIG)
        result = connector.test_connection()
        assert result["success"] is False
        assert "message" in result

    @responses.activate
    def test_connection_network_error(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            body=ConnectionError("DNS resolution failed"),
        )
        connector = DatabricksConnector(PAT_CONFIG)
        result = connector.test_connection()
        assert result["success"] is False


class TestDatabricksCollectClusters:
    @responses.activate
    def test_collects_clusters(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "0123-456789-abc", "cluster_name": "analytics-cluster", "state": "RUNNING", "spark_version": "13.3.x-scala2.12", "node_type_id": "i3.xlarge", "num_workers": 4, "creator_user_name": "user@example.com", "start_time": 1700000000000}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))

        clusters = [r for r in records if r.get("resource_type") == "cluster"]
        assert len(clusters) == 1
        assert clusters[0]["cluster_id"] == "0123-456789-abc"
        assert clusters[0]["cluster_name"] == "analytics-cluster"
        assert clusters[0]["state"] == "RUNNING"
        assert clusters[0]["resource_type"] == "cluster"

    @responses.activate
    def test_cluster_pagination(self):
        # Page 1 with next_page_token
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c1", "cluster_name": "cluster-1", "state": "RUNNING"}], "next_page_token": "page2-token"})
        # Page 2 without next_page_token (last page)
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c2", "cluster_name": "cluster-2", "state": "TERMINATED"}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        clusters = [r for r in records if r.get("resource_type") == "cluster"]
        assert len(clusters) == 2

    @responses.activate
    def test_cluster_normalization_flattens_tags(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c1", "cluster_name": "tagged-cluster", "state": "RUNNING", "custom_tags": {"Env": "prod", "Team": "analytics"}, "default_tags": {"Vendor": "Databricks"}}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        clusters = [r for r in records if r.get("resource_type") == "cluster"]
        assert clusters[0]["custom_tags"] == "Env=prod, Team=analytics"


class TestDatabricksCollectJobs:
    @responses.activate
    def test_collects_jobs(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [{"job_id": 111, "created_time": 1700000000000, "creator_user_name": "user@example.com", "settings": {"name": "ETL-daily-ingest", "description": "Daily pipeline"}}], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        jobs = [r for r in records if r.get("resource_type") == "job"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 111
        assert jobs[0]["job_name"] == "ETL-daily-ingest"
        assert jobs[0]["resource_type"] == "job"

    @responses.activate
    def test_job_pagination_with_has_more(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [{"job_id": 1, "settings": {"name": "job-1"}}], "has_more": True, "next_page_token": "token-page2"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [{"job_id": 2, "settings": {"name": "job-2"}}], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        jobs = [r for r in records if r.get("resource_type") == "job"]
        assert len(jobs) == 2

    @responses.activate
    def test_job_normalizes_settings(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [{"job_id": 1, "settings": {"name": "scheduled-job", "description": "A job", "max_concurrent_runs": 3, "schedule": {"quartz_cron_expression": "0 0 * * * ?", "timezone_id": "US/Pacific"}}}], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        jobs = [r for r in records if r.get("resource_type") == "job"]
        assert jobs[0]["schedule_cron"] == "0 0 * * * ?"
        assert jobs[0]["schedule_timezone"] == "US/Pacific"
        assert jobs[0]["max_concurrent_runs"] == 3


class TestDatabricksCollectWarehouses:
    @responses.activate
    def test_collects_warehouses(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": [{"id": "wh-abc-123", "name": "analytics-warehouse", "cluster_size": "Medium", "state": "RUNNING", "creator_name": "user@example.com", "warehouse_type": "PRO"}]})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        warehouses = [r for r in records if r.get("resource_type") == "sql_warehouse"]
        assert len(warehouses) == 1
        assert warehouses[0]["warehouse_id"] == "wh-abc-123"
        assert warehouses[0]["warehouse_name"] == "analytics-warehouse"
        assert warehouses[0]["state"] == "RUNNING"

    @responses.activate
    def test_warehouse_tags_flattened(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": [{"id": "wh-1", "name": "wh", "state": "STOPPED", "tags": {"custom_tags": [{"key": "Env", "value": "dev"}, {"key": "Team", "value": "ops"}]}}]})

        connector = DatabricksConnector(PAT_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        warehouses = [r for r in records if r.get("resource_type") == "sql_warehouse"]
        assert warehouses[0]["tags"] == "Env=dev, Team=ops"


UC_CONFIG = {
    **PAT_CONFIG,
    "collect_unity_catalog": True,
}


class TestDatabricksCollectUnityCatalog:
    @responses.activate
    def test_collects_catalogs_schemas_tables(self):
        # Core resources (empty)
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})
        # UC
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/unity-catalog/catalogs", json={"catalogs": [{"name": "main", "catalog_type": "MANAGED_CATALOG", "comment": "Main catalog", "owner": "admin@example.com", "created_at": 1700000000000, "created_by": "admin@example.com", "metastore_id": "ms-abc-123", "isolation_mode": "OPEN", "full_name": "main"}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/unity-catalog/schemas", json={"schemas": [{"name": "default", "full_name": "main.default", "catalog_name": "main", "catalog_type": "MANAGED_CATALOG", "owner": "admin@example.com", "created_at": 1700000000000, "created_by": "admin@example.com", "schema_id": "schema-001"}]})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/unity-catalog/tables", json={"tables": [{"name": "customers", "full_name": "main.default.customers", "catalog_name": "main", "schema_name": "default", "table_type": "MANAGED", "data_source_format": "DELTA", "owner": "admin@example.com", "created_at": 1700000000000, "created_by": "admin@example.com", "table_id": "table-001"}]})

        connector = DatabricksConnector(UC_CONFIG)
        records = list(connector.fetch_data("test-batch"))

        catalogs = [r for r in records if r.get("resource_type") == "catalog"]
        schemas = [r for r in records if r.get("resource_type") == "schema"]
        tables = [r for r in records if r.get("resource_type") == "table"]

        assert len(catalogs) == 1
        assert catalogs[0]["catalog_name"] == "main"
        assert len(schemas) == 1
        assert schemas[0]["schema_full_name"] == "main.default"
        assert len(tables) == 1
        assert tables[0]["table_full_name"] == "main.default.customers"

    @responses.activate
    def test_skips_uc_when_disabled(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)  # collect_unity_catalog=False
        records = list(connector.fetch_data("test-batch"))
        uc_records = [r for r in records if r.get("resource_type") in ("catalog", "schema", "table")]
        assert len(uc_records) == 0

    @responses.activate
    def test_uc_graceful_on_permission_error(self):
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/unity-catalog/catalogs", json={"error_code": "PERMISSION_DENIED", "message": "No UC access"}, status=403)

        connector = DatabricksConnector(UC_CONFIG)
        records = list(connector.fetch_data("test-batch"))
        # Should not raise, just skip UC resources
        uc_records = [r for r in records if r.get("resource_type") in ("catalog", "schema", "table")]
        assert len(uc_records) == 0


# ---------------------------------------------------------------------------
# Issue #1318 — helpers shared by the hardening tests below
# ---------------------------------------------------------------------------

UC_URL = f"{WORKSPACE_URL}/api/2.1/unity-catalog"


def register_empty_core_endpoints():
    """Clusters/jobs/warehouses return nothing, so a UC test sees only UC traffic."""
    responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": []})
    responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
    responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})


def uc_connector(**page_caps):
    """A UC-enabled connector with per-level page caps shrunk for the test.

    The production caps are 100/100/500; driving truncation through the real
    ones would mean mocking hundreds of HTTP pages.
    """
    connector = DatabricksConnector(UC_CONFIG)
    connector._max_pages.update(page_caps)
    return connector


def warnings_logged(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def truncation_warnings(caplog):
    return [m for m in warnings_logged(caplog) if "truncat" in m.lower()]


class TestDatabricksPageCaps:
    def test_production_page_caps(self):
        """The shrunk caps the truncation tests use must not hide a regression
        in the real ones."""
        caps = DatabricksConnector(PAT_CONFIG)._max_pages
        assert caps == {
            "clusters": 100,
            "jobs": 500,
            "warehouses": 100,
            "catalogs": 100,
            "schemas": 100,
            "tables": 500,
        }


class TestDatabricksUnityCatalogTruncation:
    """A UC level that stops on its page cap must say so — CursorPaginator
    stops silently, so "collected everything" and "collected the first N
    pages" are otherwise indistinguishable."""

    @responses.activate
    def test_truncated_tables_warn_with_level_scope_and_cap(self, caplog):
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "main"}]})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": [{"name": "sales", "catalog_name": "main"}]})
        # Two full pages, each handing back a fresh cursor: more data waiting.
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": [{"name": "t1"}], "next_page_token": "p2"})
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": [{"name": "t2"}], "next_page_token": "p3"})

        connector = uc_connector(tables=2)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len([r for r in records if r["resource_type"] == "table"]) == 2
        assert len(truncation_warnings(caplog)) == 1, warnings_logged(caplog)
        message = truncation_warnings(caplog)[0]
        assert "tables" in message
        assert "main.sales" in message
        assert "2-page cap" in message

    @responses.activate
    def test_exactly_exhausted_tables_do_not_warn(self, caplog):
        """The off-by-one: a final page that fills the cap but hands back no
        cursor is end-of-data, not truncation."""
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "main"}]})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": [{"name": "sales", "catalog_name": "main"}]})
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": [{"name": "t1"}], "next_page_token": "p2"})
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": [{"name": "t2"}]})

        connector = uc_connector(tables=2)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len([r for r in records if r["resource_type"] == "table"]) == 2
        assert truncation_warnings(caplog) == []

    @responses.activate
    def test_truncated_schemas_name_the_catalog(self, caplog):
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "main"}]})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": [{"name": "s1", "catalog_name": "main"}], "next_page_token": "p2"})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": [{"name": "s2", "catalog_name": "main"}], "next_page_token": "p3"})
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": []})

        connector = uc_connector(schemas=2)
        with caplog.at_level(logging.WARNING):
            list(connector.fetch_data("test-batch"))

        assert len(truncation_warnings(caplog)) == 1, warnings_logged(caplog)
        message = truncation_warnings(caplog)[0]
        assert "schemas" in message
        assert "main" in message
        assert "2-page cap" in message

    @responses.activate
    def test_truncated_catalogs_warn(self, caplog):
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "c1"}], "next_page_token": "p2"})
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "c2"}], "next_page_token": "p3"})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": []})

        connector = uc_connector(catalogs=2)
        with caplog.at_level(logging.WARNING):
            list(connector.fetch_data("test-batch"))

        assert len(truncation_warnings(caplog)) == 1, warnings_logged(caplog)
        message = truncation_warnings(caplog)[0]
        assert "catalogs" in message
        assert "2-page cap" in message

    @responses.activate
    def test_truncated_core_resource_warns_too(self, caplog):
        """Clusters/jobs/warehouses ride the same silent cap."""
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c1"}], "next_page_token": "p2"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/clusters/list", json={"clusters": [{"cluster_id": "c2"}], "next_page_token": "p3"})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.1/jobs/list", json={"jobs": [], "has_more": False})
        responses.add(responses.GET, f"{WORKSPACE_URL}/api/2.0/sql/warehouses", json={"warehouses": []})

        connector = DatabricksConnector(PAT_CONFIG)
        connector._max_pages["clusters"] = 2
        with caplog.at_level(logging.WARNING):
            list(connector.fetch_data("test-batch"))

        assert len(truncation_warnings(caplog)) == 1, warnings_logged(caplog)
        assert "clusters" in truncation_warnings(caplog)[0]

    @responses.activate
    def test_unreadable_schema_still_degrades_not_aborts(self, caplog):
        """The per-level degradation stays: one 403 catalog must not take the
        rest of the collection with it."""
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "locked"}, {"name": "open"}]})
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"error_code": "PERMISSION_DENIED", "message": "no"}, status=403)
        responses.add(responses.GET, f"{UC_URL}/schemas", json={"schemas": [{"name": "sales", "catalog_name": "open"}]})
        responses.add(responses.GET, f"{UC_URL}/tables", json={"tables": [{"name": "orders"}]})

        connector = DatabricksConnector(UC_CONFIG)
        with caplog.at_level(logging.WARNING):
            records = list(connector.fetch_data("test-batch"))

        assert len([r for r in records if r["resource_type"] == "catalog"]) == 2
        assert len([r for r in records if r["resource_type"] == "table"]) == 1
        assert any("locked" in m for m in warnings_logged(caplog))


class TestDatabricksCredentialDiagnostics:
    """test_connection()'s message lands verbatim in integrations.last_error,
    so it has to name the cause rather than echo an HTTP status line."""

    @pytest.mark.parametrize("status", [401, 403])
    @responses.activate
    def test_rejected_pat_names_expiry_and_revocation(self, status):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "PERMISSION_DENIED", "message": "Invalid access token."},
            status=status,
        )
        result = DatabricksConnector(PAT_CONFIG).test_connection()

        assert result["success"] is False
        message = result["message"]
        assert "Personal Access Token" in message
        assert "expired" in message.lower()
        assert "revoked" in message.lower()
        assert str(status) in message
        # The API's own words survive — that is what makes it actionable.
        assert "PERMISSION_DENIED" in message
        assert "Invalid access token." in message

    @responses.activate
    def test_oauth_secret_rejection_is_not_a_token_expiry_message(self):
        responses.add(
            responses.POST,
            f"{WORKSPACE_URL}/oidc/v1/token",
            json={"error": "invalid_client", "error_description": "Client authentication failed"},
            status=401,
        )
        result = DatabricksConnector(OAUTH_CONFIG).test_connection()

        assert result["success"] is False
        message = result["message"]
        assert "client secret" in message.lower()
        assert "invalid_client" in message
        assert "Client authentication failed" in message
        # A rejected client secret is not an expired token.
        assert "expired" not in message.lower()

    @responses.activate
    def test_not_found_is_not_a_credential_message(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "ENDPOINT_NOT_FOUND", "message": "No API found for 'GET /clusters/list'"},
            status=404,
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        assert "404" in message
        assert "workspace URL" in message
        assert WORKSPACE_URL in message
        assert "ENDPOINT_NOT_FOUND" in message
        assert "expired" not in message.lower()
        assert "revoked" not in message.lower()

    @responses.activate
    def test_unclassified_status_still_carries_the_api_message(self):
        """400 is Databricks' most common client error (a workspace with no
        metastore answers it for every Unity Catalog call) and str(exc) is
        only the status line — so the API's own words are the whole
        diagnosis and must survive the unclassified branch."""
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "METASTORE_DOES_NOT_EXIST", "message": "No metastore assigned for the current workspace."},
            status=400,
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        assert "METASTORE_DOES_NOT_EXIST" in message
        assert "No metastore assigned for the current workspace." in message

    @responses.activate
    def test_unrecognised_error_body_is_not_dropped(self):
        """A gateway in front of the workspace answers its own JSON shape;
        the server's words still have to reach the operator."""
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"detail": "Forbidden by gateway policy"},
            status=403,
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        assert "Forbidden by gateway policy" in message

    @responses.activate
    def test_retried_server_error_is_not_a_credential_message(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "TEMPORARILY_UNAVAILABLE", "message": "busy"},
            status=503,
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        # 503 is in the session's retry status_forcelist, so the transport
        # gives up with a RetryError and there is no response left to read.
        assert "503" in message
        assert "credentials are not the problem" in message
        assert "expired" not in message.lower()
        assert "revoked" not in message.lower()

    @responses.activate
    def test_unretried_server_error_reports_the_status(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "INTERNAL_ERROR", "message": "boom"},
            status=501,
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        assert "501" in message
        assert "server error" in message.lower()
        assert "INTERNAL_ERROR" in message
        assert "expired" not in message.lower()

    @responses.activate
    def test_network_failure_is_distinct_from_a_credential_failure(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            body=ConnectionError("DNS resolution failed"),
        )
        message = DatabricksConnector(PAT_CONFIG).test_connection()["message"]

        assert "reach" in message.lower()
        assert WORKSPACE_URL in message
        assert "DNS resolution failed" in message
        assert "expired" not in message.lower()
        assert "revoked" not in message.lower()


class TestDatabricksMidRunCredentialFailure:
    """An integration that starts fine and dies on an expired token must fail
    the run — with a last_error that names the cause."""

    @responses.activate
    def test_pat_expiring_mid_run_fails_the_run_with_a_named_cause(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"clusters": [{"cluster_id": "c1", "cluster_name": "c"}], "next_page_token": "p2"},
        )
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "PERMISSION_DENIED", "message": "Invalid access token."},
            status=401,
        )

        connector = DatabricksConnector(PAT_CONFIG)
        with pytest.raises(requests.HTTPError) as excinfo:
            list(connector.fetch_data("test-batch"))

        # str(exc) is what integration_service stores as last_error.
        message = str(excinfo.value)
        assert "Personal Access Token" in message
        assert "expired" in message.lower()
        assert "revoked" in message.lower()
        assert "Invalid access token." in message

    @responses.activate
    def test_oauth_token_rejected_mid_run_names_the_service_principal(self):
        responses.add(responses.POST, f"{WORKSPACE_URL}/oidc/v1/token", json={"access_token": "at", "expires_in": 3600})
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/clusters/list",
            json={"error_code": "PERMISSION_DENIED", "message": "Workspace access revoked"},
            status=403,
        )

        connector = DatabricksConnector(OAUTH_CONFIG)
        with pytest.raises(requests.HTTPError) as excinfo:
            list(connector.fetch_data("test-batch"))

        message = str(excinfo.value)
        assert "service principal" in message.lower()
        assert "Personal Access Token" not in message
        assert "Workspace access revoked" in message

    @responses.activate
    def test_credential_rejection_during_uc_aborts_instead_of_degrading(self):
        """A 401 is not "one unreadable catalog" — it means we hold no working
        credential, so finishing would publish a partial inventory as a
        complete one."""
        register_empty_core_endpoints()
        responses.add(responses.GET, f"{UC_URL}/catalogs", json={"catalogs": [{"name": "main"}]})
        responses.add(
            responses.GET,
            f"{UC_URL}/schemas",
            json={"error_code": "PERMISSION_DENIED", "message": "Invalid access token."},
            status=401,
        )

        connector = DatabricksConnector(UC_CONFIG)
        with pytest.raises(requests.HTTPError) as excinfo:
            list(connector.fetch_data("test-batch"))

        assert "expired" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# get_schema() coverage — raw payloads exercising every normalizer branch
# ---------------------------------------------------------------------------

RAW_CLUSTER = {
    "cluster_id": "0123-456789-abc",
    "cluster_name": "analytics-cluster",
    "state": "RUNNING",
    "state_message": "Waiting for cloud provider capacity",
    "spark_version": "13.3.x-scala2.12",
    "node_type_id": "i3.xlarge",
    "driver_node_type_id": "i3.2xlarge",
    "num_workers": 4,
    "cluster_cores": 16.0,
    "cluster_memory_mb": 65536,
    "creator_user_name": "user@example.com",
    "cluster_source": "UI",
    "start_time": 1700000000000,
    "terminated_time": 1700003600000,
    "last_state_loss_time": 1700002000000,
    "autotermination_minutes": 30,
    "enable_elastic_disk": True,
    "data_security_mode": "USER_ISOLATION",
    "runtime_engine": "PHOTON",
    "is_pinned": False,
    "instance_pool_id": "pool-abc-123",
    "autoscale": {"min_workers": 2, "max_workers": 8},
    "custom_tags": {"Env": "prod"},
    "default_tags": {"Vendor": "Databricks"},
}

RAW_JOB = {
    "job_id": 111,
    "created_time": 1700000000000,
    "creator_user_name": "user@example.com",
    "run_as_user_name": "svc-etl@example.com",
    "settings": {
        "name": "ETL-daily-ingest",
        "description": "Daily pipeline",
        "format": "MULTI_TASK",
        "max_concurrent_runs": 3,
        "schedule": {"quartz_cron_expression": "0 0 * * * ?", "timezone_id": "US/Pacific"},
    },
}

RAW_WAREHOUSE = {
    "id": "wh-abc-123",
    "name": "analytics-warehouse",
    "cluster_size": "Medium",
    "state": "RUNNING",
    "creator_name": "user@example.com",
    "warehouse_type": "PRO",
    "enable_photon": True,
    "enable_serverless_compute": False,
    "auto_stop_mins": 10,
    "min_num_clusters": 1,
    "max_num_clusters": 4,
    "num_clusters": 2,
    "num_active_sessions": 3,
    "spot_instance_policy": "COST_OPTIMIZED",
    "health": {"status": "HEALTHY", "message": "Warehouse is healthy"},
    "tags": {"custom_tags": [{"key": "Env", "value": "dev"}]},
}

RAW_CATALOG = {
    "name": "main",
    "catalog_type": "MANAGED_CATALOG",
    "comment": "Main catalog",
    "owner": "admin@example.com",
    "created_at": 1700000000000,
    "created_by": "admin@example.com",
    "metastore_id": "ms-abc-123",
    "isolation_mode": "OPEN",
    "full_name": "main",
}

RAW_SCHEMA = {
    "name": "default",
    "full_name": "main.default",
    "catalog_name": "main",
    "catalog_type": "MANAGED_CATALOG",
    "comment": "Default schema",
    "owner": "admin@example.com",
    "created_at": 1700000000000,
    "created_by": "admin@example.com",
    "schema_id": "schema-001",
}

RAW_TABLE = {
    "name": "customers",
    "full_name": "main.default.customers",
    "catalog_name": "main",
    "schema_name": "default",
    "table_type": "EXTERNAL",
    "data_source_format": "DELTA",
    "owner": "admin@example.com",
    "created_at": 1700000000000,
    "created_by": "admin@example.com",
    "table_id": "table-001",
    "storage_location": "s3://bucket/main/default/customers",
}


def emitted_field_names():
    """Union of the keys every normalizer can produce."""
    connector = DatabricksConnector(PAT_CONFIG)
    emitted = set()
    for normalize, raw in (
        (connector._normalize_cluster, RAW_CLUSTER),
        (connector._normalize_job, RAW_JOB),
        (connector._normalize_warehouse, RAW_WAREHOUSE),
        (connector._normalize_catalog, RAW_CATALOG),
        (connector._normalize_schema, RAW_SCHEMA),
        (connector._normalize_table, RAW_TABLE),
    ):
        emitted |= set(normalize(raw))
    return emitted


class TestDatabricksSchemaCoverage:
    """A normalizer key missing from get_schema() is invisible to the wizard's
    field mapping — the field is collected and then unusable."""

    def test_every_normalizer_key_is_declared(self):
        declared = {f["name"] for f in DatabricksConnector(PAT_CONFIG).get_schema()["fields"]}
        missing = sorted(emitted_field_names() - declared)
        assert not missing, f"normalizers emit fields get_schema() does not declare: {missing}"

    def test_no_declared_field_is_unreachable(self):
        declared = {f["name"] for f in DatabricksConnector(PAT_CONFIG).get_schema()["fields"]}
        orphaned = sorted(declared - emitted_field_names())
        assert not orphaned, f"get_schema() declares fields no normalizer emits: {orphaned}"

    def test_declared_fields_are_unique_and_described(self):
        fields = DatabricksConnector(PAT_CONFIG).get_schema()["fields"]
        names = [f["name"] for f in fields]
        assert len(names) == len(set(names)), "duplicate field names in get_schema()"
        for field in fields:
            assert field["path"] == field["name"], field
            assert field["type"] in ("string", "number", "boolean"), field
            assert field.get("description"), f"{field['name']} has no description"

    @pytest.mark.parametrize(
        "name",
        [
            "state_message",
            "autoscale_min_workers",
            "autoscale_max_workers",
            "custom_tags",
            "default_tags",
            "health_status",
            "health_message",
            "auto_stop_mins",
            "num_clusters",
            "tags",
            "run_as_user_name",
            "owner",
            "comment",
            "storage_location",
        ],
    )
    def test_named_gaps_from_the_review_are_closed(self, name):
        declared = {f["name"] for f in DatabricksConnector(PAT_CONFIG).get_schema()["fields"]}
        assert name in declared


# ---------------------------------------------------------------------------
# SQL source mode (Statement Execution API) — #1318
# ---------------------------------------------------------------------------

SQL_CONFIG = {
    "workspace_url": WORKSPACE_URL,
    "auth_type": "pat",
    "token": "dapi-test-token-1234567890",
    "mode": "sql",
    "warehouse_id": "wh-sql-001",
    "sql_statement": "SELECT hostname, ip FROM inventory.assets",
    # 0s interval keeps the poll loop instant in tests.
    "poll_interval": 0,
    "poll_timeout": 60,
}

STATEMENTS_URL = f"{WORKSPACE_URL}/api/2.0/sql/statements"
STATEMENT_ID = "01ef1234-5678-4abc-9def-0123456789ab"


def succeeded_body(columns=("hostname", "ip"), rows=(), next_link=None):
    """A SUCCEEDED statement response: column manifest + first result chunk."""
    result = {
        "chunk_index": 0,
        "data_array": [list(row) for row in rows],
        "row_count": len(rows),
        "row_offset": 0,
    }
    if next_link:
        result["next_chunk_index"] = 1
        result["next_chunk_internal_link"] = next_link
    return {
        "statement_id": STATEMENT_ID,
        "status": {"state": "SUCCEEDED"},
        "manifest": {
            "format": "JSON_ARRAY",
            "schema": {
                "column_count": len(columns),
                "columns": [
                    {"name": name, "position": i, "type_name": "STRING", "type_text": "STRING"}
                    for i, name in enumerate(columns)
                ],
            },
            "chunks": [{"chunk_index": 0, "row_count": len(rows), "row_offset": 0}],
            "total_chunk_count": 1,
            "total_row_count": len(rows),
            "truncated": False,
        },
        "result": result,
    }


def pending_body(state="PENDING"):
    return {"statement_id": STATEMENT_ID, "status": {"state": state}}


def failed_body():
    return {
        "statement_id": STATEMENT_ID,
        "status": {
            "state": "FAILED",
            "error": {
                "error_code": "BAD_REQUEST",
                "message": "[PARSE_SYNTAX_ERROR] Syntax error at or near 'SELEC': line 1 pos 0",
            },
        },
    }


class TestDatabricksSqlConfigValidation:
    def test_inventory_is_the_default_mode(self):
        connector = DatabricksConnector(PAT_CONFIG)
        assert connector.mode == "inventory"

    def test_sql_mode_needs_no_inventory_fields(self):
        # page_size / collect_unity_catalog absent — SQL mode must not care.
        connector = DatabricksConnector(SQL_CONFIG)
        assert connector.mode == "sql"

    def test_sql_mode_requires_warehouse_id(self):
        config = {k: v for k, v in SQL_CONFIG.items() if k != "warehouse_id"}
        with pytest.raises(ValueError, match="warehouse_id"):
            DatabricksConnector(config)

    def test_sql_mode_requires_a_statement(self):
        config = {**SQL_CONFIG, "sql_statement": "   "}
        with pytest.raises(ValueError, match="sql_statement"):
            DatabricksConnector(config)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unsupported mode 'bogus'"):
            DatabricksConnector({**PAT_CONFIG, "mode": "bogus"})

    def test_config_schema_defaults_to_inventory(self):
        schema = DatabricksConnector.get_config_schema()
        assert schema["mode"]["default"] == "inventory"
        assert {o["value"] for o in schema["mode"]["options"]} == {"inventory", "sql"}
        # Not schema-required: existing inventory integrations have none of
        # these — the constructor enforces them in SQL mode instead.
        assert schema["warehouse_id"]["required"] is False
        assert schema["sql_statement"]["required"] is False


class TestDatabricksSqlCollect:
    @responses.activate
    def test_submits_statement_and_yields_manifest_named_rows(self):
        responses.add(
            responses.POST,
            STATEMENTS_URL,
            json=succeeded_body(rows=[["host-a", "10.0.0.1"], ["host-b", "10.0.0.2"]]),
        )

        records = list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

        assert records[0]["hostname"] == "host-a"
        assert records[0]["ip"] == "10.0.0.1"
        assert records[1]["hostname"] == "host-b"
        # Values pass through verbatim — the rows are pre-normalized, the
        # connector renames and infers nothing.
        assert set(records[0]) >= {"hostname", "ip", "_batch_id", "_source"}
        assert records[0]["_batch_id"] == "test-batch"

        body = json.loads(responses.calls[0].request.body)
        assert body["statement"] == SQL_CONFIG["sql_statement"]
        assert body["warehouse_id"] == "wh-sql-001"
        assert body["disposition"] == "INLINE"
        assert body["format"] == "JSON_ARRAY"
        assert body["on_wait_timeout"] == "CONTINUE"
        assert "catalog" not in body
        assert "schema" not in body

    @responses.activate
    def test_default_catalog_and_schema_are_sent_when_configured(self):
        responses.add(responses.POST, STATEMENTS_URL, json=succeeded_body(rows=[]))

        config = {**SQL_CONFIG, "catalog": "main", "schema": "inventory"}
        list(DatabricksConnector(config).fetch_data("test-batch"))

        body = json.loads(responses.calls[0].request.body)
        assert body["catalog"] == "main"
        assert body["schema"] == "inventory"

    @responses.activate
    def test_polls_until_the_statement_succeeds(self):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("RUNNING"))
        responses.add(
            responses.GET,
            f"{STATEMENTS_URL}/{STATEMENT_ID}",
            json=succeeded_body(rows=[["host-a", "10.0.0.1"]]),
        )

        records = list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

        assert [r["hostname"] for r in records] == ["host-a"]
        polls = [c for c in responses.calls if c.request.method == "GET"]
        assert len(polls) == 2

    @responses.activate
    def test_collects_rows_beyond_the_first_chunk(self):
        chunk1_link = f"/api/2.0/sql/statements/{STATEMENT_ID}/result/chunks/1?row_offset=2"
        responses.add(
            responses.POST,
            STATEMENTS_URL,
            json=succeeded_body(
                rows=[["host-a", "10.0.0.1"], ["host-b", "10.0.0.2"]],
                next_link=chunk1_link,
            ),
        )
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}{chunk1_link}",
            json={
                "chunk_index": 1,
                "data_array": [["host-c", "10.0.0.3"]],
                "row_count": 1,
                "row_offset": 2,
            },
        )

        records = list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

        assert [r["hostname"] for r in records] == ["host-a", "host-b", "host-c"]
        assert records[2]["ip"] == "10.0.0.3"

    @responses.activate
    def test_empty_result_set_yields_nothing(self):
        responses.add(responses.POST, STATEMENTS_URL, json=succeeded_body(rows=[]))
        records = list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))
        assert records == []

    @responses.activate
    def test_pending_means_warehouse_starting_and_does_not_fail(self, caplog):
        """A cold warehouse is the normal case: PENDING is surfaced as a
        legible 'warehouse is starting' state, then the run succeeds."""
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(
            responses.GET,
            f"{STATEMENTS_URL}/{STATEMENT_ID}",
            json=succeeded_body(rows=[["host-a", "10.0.0.1"]]),
        )

        with caplog.at_level(logging.INFO):
            records = list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

        assert len(records) == 1
        assert any(
            "warehouse is starting" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]


class TestDatabricksSqlStatementFailures:
    @responses.activate
    def test_failed_statement_raises_with_the_apis_own_words(self):
        """Invalid SQL comes back as HTTP 200 with status.state FAILED — the
        error code and message from status.error are what makes it fixable,
        so they surface verbatim, not as a bare HTTP status."""
        responses.add(responses.POST, STATEMENTS_URL, json=failed_body())

        with pytest.raises(RuntimeError) as excinfo:
            list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

        message = str(excinfo.value)
        assert "BAD_REQUEST" in message
        assert "[PARSE_SYNTAX_ERROR] Syntax error at or near 'SELEC'" in message

    @responses.activate
    def test_failure_during_polling_is_equally_explicit(self):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=failed_body())

        with pytest.raises(RuntimeError, match="PARSE_SYNTAX_ERROR"):
            list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

    @responses.activate
    def test_cancelled_elsewhere_is_named(self):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("CANCELED"))

        with pytest.raises(RuntimeError, match="CANCELED"):
            list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))

    @responses.activate
    def test_missing_statement_id_raises_instead_of_keying_out(self):
        responses.add(responses.POST, STATEMENTS_URL, json={"status": {"state": "PENDING"}})

        with pytest.raises(RuntimeError, match="no statement_id"):
            list(DatabricksConnector(SQL_CONFIG).fetch_data("test-batch"))


def virtual_clock(monkeypatch, connector):
    """A virtual clock for the poll-timeout tests.

    ``time.time()`` reads the clock; ``time.sleep()`` advances it instantly
    by the slept amount, so the poll loop's elapsed time only moves when it
    sleeps between polls — exactly the behaviour under test, with no real
    waiting. The rate limiter's token bucket is re-stamped against the
    virtual clock: it was initialised with the real (epoch) clock at
    construction, and reading a small virtual time afterwards would compute
    a huge negative elapsed time and sleep for hours.

    Returns the mutable ``now`` cell so a test can assert how far the clock
    advanced — the observable difference between the request-path bound and
    the full poll_timeout.
    """
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(
        time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    connector.rate_limiter.last_update = now[0]
    return now


class TestDatabricksSqlPollTimeout:
    # poll_interval > poll_timeout: one poll is observed, the sleep after it
    # pushes the virtual clock past the timeout, and the next check cancels.
    TIMEOUT_CONFIG = {
        **SQL_CONFIG,
        "poll_interval": 31,
        "poll_timeout": 30,
    }

    @responses.activate
    def test_timeout_cancels_server_side_and_says_the_warehouse_was_starting(self, monkeypatch):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})

        connector = DatabricksConnector(self.TIMEOUT_CONFIG)
        virtual_clock(monkeypatch, connector)

        with pytest.raises(TimeoutError) as excinfo:
            list(connector.fetch_data("test-batch"))

        message = str(excinfo.value)
        assert "cancelled server-side" in message
        assert "warehouse was still starting" in message
        # "Still starting" is a different operator action from "your SQL is
        # wrong" — the message must not read like a query error.
        assert "optimise the statement" not in message

        cancels = [
            c for c in responses.calls
            if c.request.method == "POST" and c.request.url.endswith("/cancel")
        ]
        assert len(cancels) == 1

    @responses.activate
    def test_timeout_while_running_names_the_query_not_the_warehouse(self, monkeypatch):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("RUNNING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})

        connector = DatabricksConnector(self.TIMEOUT_CONFIG)
        virtual_clock(monkeypatch, connector)

        with pytest.raises(TimeoutError) as excinfo:
            list(connector.fetch_data("test-batch"))

        message = str(excinfo.value)
        assert "query was still executing" in message
        assert "warehouse was still starting" not in message

        cancels = [
            c for c in responses.calls
            if c.request.method == "POST" and c.request.url.endswith("/cancel")
        ]
        assert len(cancels) == 1

    @responses.activate
    def test_a_failed_cancel_does_not_mask_the_timeout(self, monkeypatch, caplog):
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(
            responses.POST,
            f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel",
            body=ConnectionError("connection reset"),
        )

        connector = DatabricksConnector(self.TIMEOUT_CONFIG)
        virtual_clock(monkeypatch, connector)

        with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
            list(connector.fetch_data("test-batch"))

        assert any(
            "Could not cancel" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]


class TestDatabricksSqlTestConnection:
    @responses.activate
    def test_sql_mode_proves_the_path_end_to_end(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"id": "wh-sql-001", "name": "sql-endpoint", "state": "RUNNING"},
        )
        responses.add(responses.POST, STATEMENTS_URL, json=succeeded_body(columns=("1",), rows=[("1",)]))

        result = DatabricksConnector(SQL_CONFIG).test_connection()

        assert result["success"] is True
        assert "sql-endpoint" in result["message"]
        assert "RUNNING" in result["message"]
        assert "test statement executed" in result["message"]
        assert result["metadata"]["warehouse_state"] == "RUNNING"

    @responses.activate
    def test_sql_mode_reports_a_stopped_warehouse_as_a_cold_start_not_an_error(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"id": "wh-sql-001", "name": "sql-endpoint", "state": "STOPPED"},
        )
        responses.add(responses.POST, STATEMENTS_URL, json=succeeded_body(columns=("1",), rows=[("1",)]))

        result = DatabricksConnector(SQL_CONFIG).test_connection()

        assert result["success"] is True
        assert "was STOPPED and started automatically" in result["message"]

    @responses.activate
    def test_sql_mode_names_a_missing_warehouse(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "Warehouse wh-sql-001 does not exist."},
            status=404,
        )

        result = DatabricksConnector(SQL_CONFIG).test_connection()

        assert result["success"] is False
        message = result["message"]
        assert "wh-sql-001" in message
        assert "not found" in message
        assert "CAN USE" in message
        # The API's own words survive.
        assert "RESOURCE_DOES_NOT_EXIST" in message
        # And it is not misread as a workspace-URL problem.
        assert "workspace URL" not in message

    @responses.activate
    def test_sql_mode_surfaces_a_failing_test_statement(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"id": "wh-sql-001", "name": "sql-endpoint", "state": "RUNNING"},
        )
        responses.add(responses.POST, STATEMENTS_URL, json=failed_body())

        result = DatabricksConnector(SQL_CONFIG).test_connection()

        assert result["success"] is False
        assert "BAD_REQUEST" in result["message"]
        assert "PARSE_SYNTAX_ERROR" in result["message"]


class TestDatabricksSqlRequestPathBound:
    """Statements executed on an HTTP request path — test_connection() and
    the sample-data preview — poll under REQUEST_PATH_POLL_TIMEOUT, never
    the configured poll_timeout, because the browser abandons the request
    after 30s. Hitting the bound cancels the statement server-side
    immediately; the scheduled path keeps the full poll_timeout."""

    # poll_timeout=900 is what a scheduled run would wait out; the request
    # path must not. poll_interval=31 exceeds the 15s bound, so only the
    # clamped sleep keeps the exchange inside the browser budget.
    BROWSER_CONFIG = {
        **SQL_CONFIG,
        "poll_interval": 31,
        "poll_timeout": 900,
    }

    @staticmethod
    def _register_cold_warehouse():
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"id": "wh-sql-001", "name": "sql-endpoint", "state": "STOPPED"},
        )
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})

    @staticmethod
    def _polls_and_cancels():
        polls = [
            c for c in responses.calls
            if c.request.method == "GET"
            and c.request.url == f"{STATEMENTS_URL}/{STATEMENT_ID}"
        ]
        cancels = [
            c for c in responses.calls
            if c.request.method == "POST" and c.request.url.endswith("/cancel")
        ]
        return polls, cancels

    @responses.activate
    def test_cold_warehouse_test_connection_returns_inside_the_browser_budget(self, monkeypatch):
        self._register_cold_warehouse()
        connector = DatabricksConnector(self.BROWSER_CONFIG)
        now = virtual_clock(monkeypatch, connector)

        result = connector.test_connection()

        # Cancelled at the request-path bound — the configured 900s would
        # have outlived the browser by half an hour.
        assert now[0] - 1000.0 == DatabricksConnector.REQUEST_PATH_POLL_TIMEOUT
        polls, cancels = self._polls_and_cancels()
        assert len(polls) == 1
        assert len(cancels) == 1

        # success=False is deliberate (see the except block in
        # _test_connection_sql): the path was cancelled before it could be
        # proven, and success=True would have sent the wizard's sample-data
        # fetch into the same wall.
        assert result["success"] is False
        message = result["message"]
        assert "cancelled server-side" in message
        assert "warehouse was still starting" in message
        assert "Nothing is known to be wrong" in message
        assert "scheduled run carries no such limit" in message
        # The scheduled-timeout advice would be wrong here — the configured
        # timeout never fired.
        assert "raise poll_timeout" not in message
        assert "optimise the statement" not in message

    @responses.activate
    def test_running_statement_is_cancelled_and_named_differently(self, monkeypatch):
        """The warehouse was up and the query still going — the message must
        not blame a cold start that didn't happen."""
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"id": "wh-sql-001", "name": "sql-endpoint", "state": "RUNNING"},
        )
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("RUNNING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})
        connector = DatabricksConnector(self.BROWSER_CONFIG)
        now = virtual_clock(monkeypatch, connector)

        result = connector.test_connection()

        assert now[0] - 1000.0 == DatabricksConnector.REQUEST_PATH_POLL_TIMEOUT
        _, cancels = self._polls_and_cancels()
        assert len(cancels) == 1
        assert result["success"] is False
        assert "still executing" in result["message"]
        assert "never began executing" not in result["message"]
        assert "Nothing is known to be wrong" in result["message"]

    @responses.activate
    def test_rejected_credential_is_not_misread_as_a_cold_start(self):
        responses.add(
            responses.GET,
            f"{WORKSPACE_URL}/api/2.0/sql/warehouses/wh-sql-001",
            json={"error_code": "PERMISSION_DENIED", "message": "Invalid access token."},
            status=401,
        )

        result = DatabricksConnector(self.BROWSER_CONFIG).test_connection()

        assert result["success"] is False
        message = result["message"]
        assert "Personal Access Token" in message
        assert "expired" in message.lower()
        assert "revoked" in message.lower()
        assert "warehouse was still starting" not in message
        assert "scheduled run" not in message
        # The credential failed before any statement was submitted.
        statements = [c for c in responses.calls if STATEMENTS_URL in c.request.url]
        assert statements == []

    @responses.activate
    def test_sample_data_is_bounded_and_cancels_the_statement(self, monkeypatch):
        """The wizard fetches sample data on the same 30s budget, so the
        preview run of the real statement gets the request-path bound too —
        batch_id "sample" is how the connector tells it from the scheduler.
        (No warehouse lookup on this path: it goes straight to the statement.)"""
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})
        connector = DatabricksConnector(self.BROWSER_CONFIG)
        now = virtual_clock(monkeypatch, connector)

        samples = connector.get_sample_data(limit=10)

        # The preview could not finish, so there are no samples — but the
        # call returned at the request-path bound and the abandoned
        # statement was cancelled instead of polling on for 900s.
        assert samples == []
        assert now[0] - 1000.0 == DatabricksConnector.REQUEST_PATH_POLL_TIMEOUT
        polls, cancels = self._polls_and_cancels()
        assert len(polls) == 1
        assert len(cancels) == 1

    @responses.activate
    def test_scheduled_collect_keeps_the_full_configured_poll_timeout(self, monkeypatch):
        """The pin that keeps the two paths apart: with poll_timeout=30 —
        beyond the request-path bound — a scheduled run waits out its own
        timeout and gets the scheduled message, not the request-path one."""
        responses.add(responses.POST, STATEMENTS_URL, json=pending_body("PENDING"))
        responses.add(responses.GET, f"{STATEMENTS_URL}/{STATEMENT_ID}", json=pending_body("PENDING"))
        responses.add(responses.POST, f"{STATEMENTS_URL}/{STATEMENT_ID}/cancel", json={})
        config = {**SQL_CONFIG, "poll_interval": 31, "poll_timeout": 30}
        connector = DatabricksConnector(config)
        now = virtual_clock(monkeypatch, connector)

        with pytest.raises(TimeoutError) as excinfo:
            list(connector.fetch_data("test-batch"))

        # 30 > REQUEST_PATH_POLL_TIMEOUT: the scheduled path waited past the
        # bound a browser path would have stopped at.
        assert DatabricksConnector.REQUEST_PATH_POLL_TIMEOUT < 30
        assert now[0] - 1000.0 == 30.0
        message = str(excinfo.value)
        assert "30s poll timeout" in message
        assert "raise poll_timeout" in message
        assert "limit applied to connection tests" not in message
        _, cancels = self._polls_and_cancels()
        assert len(cancels) == 1


class TestDatabricksSqlSchema:
    def test_sql_mode_schema_is_the_via_siem_hint_list(self):
        schema = DatabricksConnector(SQL_CONFIG).get_schema()
        names = [f["name"] for f in schema["fields"]]
        assert names == [
            "hostname", "fqdn", "ip", "mac", "sn", "os",
            "manufacturer", "model", "vendor_category",
            "first_time", "last_time",
        ]
        assert "notes" in schema
        assert "depend on your SQL statement" in schema["notes"]

    def test_inventory_mode_schema_is_unchanged(self):
        schema = DatabricksConnector(PAT_CONFIG).get_schema()
        names = {f["name"] for f in schema["fields"]}
        assert "cluster_id" in names
        assert "warehouse_id" in names
        assert schema["version"] == "1.1"
        assert "notes" not in schema


class TestDatabricksInventoryModeIsolation:
    @responses.activate
    def test_inventory_mode_never_touches_the_statement_api(self):
        register_empty_core_endpoints()

        list(DatabricksConnector(PAT_CONFIG).fetch_data("test-batch"))

        statement_calls = [
            c for c in responses.calls if "/api/2.0/sql/statements" in c.request.url
        ]
        assert statement_calls == []
