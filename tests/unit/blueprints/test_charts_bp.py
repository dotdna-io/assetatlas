# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the charts blueprint.

Endpoints tested:
- POST /api/charts/query   — unified chart query endpoint (JWT-protected)
- GET  /api/charts/fields  — field catalog (JWT-protected)
- GET  /api/charts/health  — charts service health (public)
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers: minimal valid request payloads (use typed FieldId dicts)
# ---------------------------------------------------------------------------

def _static(name):
    return {"kind": "static", "name": name}


def _distribution_query(**overrides):
    """Build a minimal valid distribution query."""
    payload = {
        "queryType": "distribution",
        "from": {"table": "assets"},
        "select": {"dimension": _static("Category")},
    }
    payload.update(overrides)
    return payload


def _metric_query(**overrides):
    payload = {
        "queryType": "metric",
        "from": {"table": "assets"},
        "select": {"metric": {"agg": "COUNT"}},
    }
    payload.update(overrides)
    return payload


def _trend_query(**overrides):
    payload = {
        "queryType": "trend",
        "from": {"table": "assets"},
        "select": {"timeField": _static("First Seen"), "dimension": _static("Category")},
        "options": {"interval": "month"},
    }
    payload.update(overrides)
    return payload


def _comparison_query(**overrides):
    payload = {
        "queryType": "comparison",
        "from": {"table": "assets"},
        "select": {"xAxis": _static("Category"), "yAxis": _static("OS Normalized")},
    }
    payload.update(overrides)
    return payload


def _correlation_query(**overrides):
    payload = {
        "queryType": "correlation",
        "from": {"table": "assets"},
        "select": {"sourceField": _static("Category"), "targetField": _static("OS Normalized")},
    }
    payload.update(overrides)
    return payload


class TestChartsHealth:
    """GET /api/charts/health -- lightweight health check."""

    def test_health_returns_200(self, client):
        resp = client.get("/api/charts/health")
        assert resp.status_code == 200

    def test_health_body(self, client):
        resp = client.get("/api/charts/health")
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "healthy"
        assert data["service"] == "charts-api"


class TestChartsQueryValidation:
    """POST /api/charts/query -- Pydantic model validation."""

    def test_no_body_returns_error(self, client, authed_headers):
        """Missing or empty JSON body returns 4xx or 5xx error."""
        resp = client.post(
            "/api/charts/query",
            content_type="application/json",
            data="null",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_REQUEST"

    def test_missing_query_type_returns_400(self, client, authed_headers):
        """Missing required queryType returns validation error."""
        resp = client.post(
            "/api/charts/query",
            json={"from": {"table": "assets"}, "select": {"dimension": _static("Category")}},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_invalid_query_type_returns_400(self, client, authed_headers):
        """Unknown queryType value returns validation error."""
        resp = client.post(
            "/api/charts/query",
            json=_distribution_query(queryType="invalid_type"),
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_invalid_table_returns_400(self, client, authed_headers):
        """Non-whitelisted table returns validation error."""
        payload = _distribution_query()
        payload["from"]["table"] = "users"
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_malformed_field_id_returns_400(self, client, authed_headers):
        """Structurally invalid FieldId (unknown discriminator) → Pydantic VALIDATION_ERROR."""
        payload = _distribution_query()
        payload["select"]["dimension"] = {"kind": "bogus", "name": "foo"}
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_distribution_requires_dimension(self, client, authed_headers):
        """Distribution query without dimension returns validation error."""
        payload = {
            "queryType": "distribution",
            "from": {"table": "assets"},
            "select": {},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_metric_requires_metric(self, client, authed_headers):
        """Metric query without metric field returns validation error."""
        payload = {
            "queryType": "metric",
            "from": {"table": "assets"},
            "select": {"dimension": _static("Category")},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_trend_requires_time_field(self, client, authed_headers):
        """Trend query without timeField returns validation error."""
        payload = {
            "queryType": "trend",
            "from": {"table": "assets"},
            "select": {"dimension": _static("Category")},
            "options": {"interval": "month"},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_trend_requires_interval(self, client, authed_headers):
        """Trend query without interval option returns validation error."""
        payload = {
            "queryType": "trend",
            "from": {"table": "assets"},
            "select": {"timeField": _static("First Seen"), "dimension": _static("Category")},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_comparison_requires_both_axes(self, client, authed_headers):
        """Comparison query without xAxis and yAxis returns validation error."""
        payload = {
            "queryType": "comparison",
            "from": {"table": "assets"},
            "select": {"xAxis": _static("Category")},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_correlation_requires_both_fields(self, client, authed_headers):
        """Correlation query without sourceField/targetField returns validation error."""
        payload = {
            "queryType": "correlation",
            "from": {"table": "assets"},
            "select": {"sourceField": _static("Category")},
        }
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_too_many_filters_returns_400(self, client, authed_headers):
        """More than MAX_FILTERS returns validation error."""
        filters = [
            {"field_id": _static("Category"), "operator": "equals", "value": f"val{i}"}
            for i in range(11)
        ]
        payload = _distribution_query(filters=filters)
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_too_many_joins_returns_400(self, client, authed_headers):
        """More than MAX_JOINS returns validation error."""
        joins = [
            {"table": "asset_providers", "type": "LEFT", "on": "assets.id = asset_providers.asset_id"}
            for _ in range(4)
        ]
        payload = _distribution_query()
        payload["from"]["joins"] = joins
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_filter_requires_value_for_equals(self, client, authed_headers):
        """Filter with 'equals' operator but no value returns validation error."""
        payload = _distribution_query(
            filters=[{"field_id": _static("Category"), "operator": "equals"}]
        )
        resp = client.post("/api/charts/query", json=payload, headers=authed_headers("admin"))
        assert resp.status_code == 400

    def test_filter_is_null_no_value_ok(self, client, blueprint_app, authed_headers):
        """Filter with 'is empty' operator doesn't require a value."""
        payload = _distribution_query(
            filters=[{"field_id": _static("Category"), "operator": "is empty"}]
        )
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = ("SELECT 1", {})
            blueprint_app.db_adapter.execute_query.return_value = []
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.return_value = ([], 5)
                mock_repo.transform_results.return_value = []
                mock_repo_cls.return_value = mock_repo
                resp = client.post(
                    "/api/charts/query", json=payload, headers=authed_headers("admin")
                )
        assert resp.status_code == 200


class TestChartsQueryExecution:
    """POST /api/charts/query -- successful query execution."""

    def test_valid_distribution_query(self, client, blueprint_app, authed_headers):
        """Valid distribution query returns 200 with data."""
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = (
                "SELECT category, count(*) FROM assets GROUP BY category",
                {},
            )
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.return_value = (
                    [{"label": "Server", "value": 100}],
                    15,
                )
                mock_repo.transform_results.return_value = [
                    {"label": "Server", "value": 100, "percentage": 100.0}
                ]
                mock_repo_cls.return_value = mock_repo

                resp = client.post(
                    "/api/charts/query",
                    json=_distribution_query(),
                    headers=authed_headers("admin"),
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["queryType"] == "distribution"
        assert len(data["data"]) == 1
        assert "metadata" in data
        assert data["metadata"]["total"] == 1

    def test_query_builder_error_returns_500(self, client, blueprint_app, authed_headers):
        """An unexpected (non-ValueError) query builder exception returns 500
        with QUERY_BUILD_ERROR. ValueError is handled separately (see
        test_query_build_value_error_returns_400) since it signals a
        deliberate validation rejection, not an internal crash."""
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.side_effect = RuntimeError("Unsupported query")
            resp = client.post(
                "/api/charts/query",
                json=_distribution_query(),
                headers=authed_headers("admin"),
            )

        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"]["code"] == "QUERY_BUILD_ERROR"

    def test_query_build_value_error_returns_400(self, client, blueprint_app, authed_headers):
        """A ValueError from build_query (e.g. virtual field in a select slot,
        or a non-time timeField) is a client error — 400 INVALID_QUERY,
        not 500."""
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.side_effect = ValueError(
                "Virtual field 'confidence_level' cannot be used as dimension."
            )
            resp = client.post(
                "/api/charts/query",
                json=_distribution_query(),
                headers=authed_headers("admin"),
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "INVALID_QUERY"

    def test_invalid_field_error_returns_400(self, client, blueprint_app, authed_headers):
        """InvalidFieldError from resolver surfaces as 400 INVALID_FIELD."""
        from src.modules.fields import InvalidFieldError

        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.side_effect = InvalidFieldError(
                "FieldId StaticField(kind='static', name='Nope') does not resolve"
            )
            resp = client.post(
                "/api/charts/query",
                json=_distribution_query(),
                headers=authed_headers("admin"),
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "INVALID_FIELD"

    def test_database_error_returns_500(self, client, blueprint_app, authed_headers):
        """Database execution error returns 500 with DATABASE_ERROR."""
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = ("SELECT 1", {})
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.side_effect = RuntimeError("connection lost")
                mock_repo_cls.return_value = mock_repo

                resp = client.post(
                    "/api/charts/query",
                    json=_distribution_query(),
                    headers=authed_headers("admin"),
                )

        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"]["code"] == "DATABASE_ERROR"

    def test_valid_metric_query(self, client, blueprint_app, authed_headers):
        """Valid metric query passes validation and reaches execution."""
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = ("SELECT count(*) FROM assets", {})
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.return_value = ([{"value": 6042}], 8)
                mock_repo.transform_results.return_value = [{"value": 6042}]
                mock_repo_cls.return_value = mock_repo

                resp = client.post(
                    "/api/charts/query", json=_metric_query(), headers=authed_headers("admin")
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["queryType"] == "metric"

    def test_valid_query_with_filters(self, client, blueprint_app, authed_headers):
        """Distribution query with valid filters passes validation."""
        payload = _distribution_query(
            filters=[
                {"field_id": _static("Confidence Score"), "operator": "gte", "value": 80},
                {"field_id": _static("Category"), "operator": "equals", "value": "Server"},
            ]
        )
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = ("SELECT 1", {})
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.return_value = ([], 3)
                mock_repo.transform_results.return_value = []
                mock_repo_cls.return_value = mock_repo

                resp = client.post(
                    "/api/charts/query", json=payload, headers=authed_headers("admin")
                )

        assert resp.status_code == 200

    def test_valid_query_with_join(self, client, blueprint_app, authed_headers):
        """Distribution query with a valid JOIN passes validation."""
        payload = _distribution_query()
        payload["from"]["joins"] = [
            {
                "table": "asset_providers",
                "type": "LEFT",
                "on": "assets.id = asset_providers.asset_id",
            }
        ]
        with patch(
            "src.api.blueprints.charts.routes.QueryBuilder"
        ) as mock_qb_cls:
            mock_qb_cls.return_value.build_query.return_value = ("SELECT 1", {})
            with patch(
                "src.api.blueprints.charts.routes.ChartRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.execute_query.return_value = ([], 3)
                mock_repo.transform_results.return_value = []
                mock_repo_cls.return_value = mock_repo

                resp = client.post(
                    "/api/charts/query", json=payload, headers=authed_headers("admin")
                )

        assert resp.status_code == 200


class TestChartsFieldsEndpoint:
    """GET /api/charts/fields -- field catalog endpoint."""

    def test_fields_requires_auth(self, client):
        resp = client.get("/api/charts/fields")
        assert resp.status_code == 401

    def test_fields_returns_catalog(self, client, blueprint_app, authed_headers):
        """Authenticated GET /api/charts/fields invokes build_catalog_response."""
        with patch("src.api.blueprints.charts.routes.build_catalog_response") as mock_build:
            mock_build.return_value = {"entries": [{"display_name": "Category"}]}
            blueprint_app.field_resolver.catalog.return_value = MagicMock()
            resp = client.get(
                "/api/charts/fields", headers=authed_headers("admin")
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "entries" in body
        mock_build.assert_called_once()
