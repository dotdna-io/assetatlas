# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the assets blueprint (auth required).

Endpoints tested:
- GET  /api/assets              — list assets with pagination / filtering
- GET  /api/assets/<id>         — asset detail
- GET  /api/assets/<id>/history — asset change history
- GET  /api/assets/search       — fuzzy search
- GET  /api/assets/fields       — explorer column metadata
- GET  /api/assets/values       — distinct field values
- GET  /api/assets/aggregate    — pivot-table aggregation
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestAssetsAuthRequired:
    """All assets endpoints must return 401 without a valid JWT."""

    @pytest.mark.parametrize("path", [
        "/api/assets",
        "/api/assets/some-uuid",
        "/api/assets/some-uuid/history",
        "/api/assets/search?q=test",
        "/api/assets/fields",
        "/api/assets/values?field=static:Category",
        "/api/assets/aggregate?row_field=static:Category",
    ])
    def test_no_auth_returns_401(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/assets — list
# ---------------------------------------------------------------------------

class TestListAssets:
    """GET /api/assets — paginated asset list."""

    def test_list_assets_success(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "items": [], "total": 0, "page": 1, "limit": 50,
        }
        blueprint_app.asset_service.list_assets.return_value = mock_result

        resp = client.get("/api/assets", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["items"] == []

    def test_list_assets_pagination_params(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "items": [], "total": 100, "page": 3, "limit": 10,
        }
        blueprint_app.asset_service.list_assets.return_value = mock_result

        resp = client.get(
            "/api/assets?page=3&limit=10", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.list_assets.assert_called_once_with(
            page=3, limit=10, category=None, search=None, alert_id=None,
        )

    def test_list_assets_with_search_filter(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"items": [], "total": 0, "page": 1, "limit": 50}
        blueprint_app.asset_service.list_assets.return_value = mock_result

        resp = client.get(
            "/api/assets?search=workstation", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.list_assets.assert_called_once_with(
            page=1, limit=50, category=None, search="workstation", alert_id=None,
        )

    def test_list_assets_with_asset_type(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"items": [], "total": 0, "page": 1, "limit": 50}
        blueprint_app.asset_service.list_assets.return_value = mock_result

        resp = client.get(
            "/api/assets?asset_type=Server", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.list_assets.assert_called_once_with(
            page=1, limit=50, category="Server", search=None, alert_id=None,
        )

    def test_list_assets_with_dynamic_fields(self, client, authed_headers, blueprint_app):
        """When fields= is provided, list_assets_dynamic is called."""
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "items": [], "total": 0, "page": 1, "limit": 50,
        }

        # Encoded FieldId strings; Flask URL-decodes the query string before
        # it reaches the blueprint, so the forwarded list contains the
        # decoded (post-unquote) form.
        resp = client.get(
            "/api/assets?fields=static:Hostname,static:IP%20Address",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.list_assets_dynamic.assert_called_once()
        call_kwargs = blueprint_app.asset_service.list_assets_dynamic.call_args.kwargs
        assert call_kwargs["fields"] == ["static:Hostname", "static:IP Address"]

    def test_list_assets_with_alert_id(self, client, authed_headers, blueprint_app):
        """?alert_id= reaches the service canonically, on the Explorer path (#907).

        The alert drilldown is NOT a field predicate — no catalog entry holds an
        alert id — so it rides its own param and is resolved against
        alerts.asset_ids server-side. One uuid in the URL however many assets the
        alert names.
        """
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "items": [], "total": 0, "page": 1, "limit": 50,
        }

        resp = client.get(
            "/api/assets?fields=static:Hostname"
            "&alert_id=948E3F69-818B-4649-8F77-362048686475",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.list_assets_dynamic.call_args.kwargs
        assert call_kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    @pytest.mark.parametrize("sent", [
        # The two that matter: Python's UUID() parses these, Postgres rejects
        # them at ::uuid (checked against the dev DB). Only canonicalising —
        # not merely validating — keeps them from reaching the cast.
        "urn:uuid:948e3f69-818b-4649-8f77-362048686475",
        "9-48e3f69818b46498f77362048686475",    # hyphen after a group of one
        # Postgres happens to accept these two as well; they pin that the
        # service is handed one stable spelling whatever the caller sent.
        "948E3F69-818B-4649-8F77-362048686475",
        "{948e3f69-818b-4649-8f77-362048686475}",
    ])
    def test_alert_id_reaches_the_service_canonically_spelled(
        self, client, authed_headers, blueprint_app, sent,
    ):
        # ensure_uuid RETURNS the canonical spelling rather than merely checking,
        # and that return value is what must travel on. Echo the caller's
        # spelling instead and the 500 moves rather than goes away — which is
        # why #999 built it this way, and why this asserts the value passed on,
        # not just the status code.
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "items": [], "total": 0, "page": 1, "limit": 50,
        }

        resp = client.get(
            f"/api/assets?fields=static:Hostname&alert_id={sent}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.list_assets_dynamic.call_args.kwargs
        assert call_kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    @pytest.mark.parametrize("alert_id", [
        "not-a-uuid",
        "948e3f69-818b-4649-8f77-36204868647",   # 31 hex digits — the near miss
        "0",
    ])
    def test_malformed_alert_id_is_400_not_500(
        self, client, authed_headers, blueprint_app, alert_id,
    ):
        # The repository casts the id straight to %(alert_id)s::uuid. Postgres
        # raises on a spelling it cannot parse and psycopg2's error is not a
        # ValueError, so handle_errors' catch-all would answer a client typo
        # with a 500 (#973/#999). ensure_uuid rejects it in the handler first.
        # The mocked service cannot raise that DB error, so "rejected before the
        # service" is what stands in for it here.
        resp = client.get(
            f"/api/assets?fields=static:Hostname&alert_id={alert_id}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.list_assets_dynamic.assert_not_called()
        blueprint_app.asset_service.list_assets.assert_not_called()

    def test_export_csv_rejects_alert_id_rather_than_widening_silently(
        self, client, authed_headers,
    ):
        # export.csv runs through list_assets_query (the MCP compiler), which
        # has no alert entry point — so it cannot honour ?alert_id=. Streaming
        # the whole fleet under a param the caller believes narrowed it is the
        # one failure an export cannot signal: the 200 is already sent by the
        # time the rows are wrong. Refuse loudly instead.
        resp = client.get(
            "/api/assets/export.csv?fields=static:Hostname"
            "&alert_id=948e3f69-818b-4649-8f77-362048686475",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400
        assert "alert_id" in resp.get_json()["message"]

    def test_list_assets_with_structured_filters(self, client, authed_headers, blueprint_app):
        """Structured JSON filters are parsed and forwarded."""
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "items": [], "total": 0, "page": 1, "limit": 50,
        }
        filters = json.dumps([{
            "field_id": {"kind": "static", "name": "Category"},
            "op": "eq",
            "value": "Server",
        }])

        resp = client.get(
            f"/api/assets?fields=static:Hostname&filters={filters}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.list_assets_dynamic.call_args.kwargs
        assert call_kwargs["filters"] is not None
        assert call_kwargs["filters"][0]["field_id"]["name"] == "Category"
        assert call_kwargs["filters"][0]["field_id"]["kind"] == "static"

    def test_list_assets_invalid_filters_json(self, client, authed_headers, blueprint_app):
        """Malformed filters JSON returns 400."""
        resp = client.get(
            "/api/assets?filters=not-json",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/<asset_id> — detail
# ---------------------------------------------------------------------------

class TestGetAssetDetail:
    """GET /api/assets/<asset_id>"""

    def test_asset_detail_success(self, client, authed_headers, blueprint_app):
        mock_detail = MagicMock()
        mock_detail.model_dump.return_value = {
            "id": "abc-123", "hostname": "srv-01",
        }
        blueprint_app.asset_service.get_asset_detail.return_value = mock_detail

        resp = client.get("/api/assets/abc-123", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["hostname"] == "srv-01"

    def test_asset_detail_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_asset_detail.return_value = None

        resp = client.get("/api/assets/nonexistent", headers=authed_headers("user"))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/assets/<asset_id>/history
# ---------------------------------------------------------------------------

class TestGetAssetHistory:
    """GET /api/assets/<asset_id>/history"""

    def test_history_success(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_history_service.get_asset_history.return_value = {
            "events": [], "total": 0,
        }

        resp = client.get(
            "/api/assets/abc-123/history", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_history_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_history_service.get_asset_history.return_value = None

        resp = client.get(
            "/api/assets/abc-123/history", headers=authed_headers("user"),
        )
        assert resp.status_code == 404

    def test_history_time_range_param(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_history_service.get_asset_history.return_value = {"events": []}

        resp = client.get(
            "/api/assets/abc-123/history?time_range=7d&limit=100",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_history_service.get_asset_history.assert_called_once_with(
            "abc-123", time_range="7d", limit=100,
        )


# ---------------------------------------------------------------------------
# GET /api/assets/search
# ---------------------------------------------------------------------------

class TestSearchAssets:
    """GET /api/assets/search"""

    def test_search_success(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "results": [{"id": "a1", "hostname": "srv-01"}],
            "count": 1,
            "query": "srv",
        }
        blueprint_app.asset_service.search_assets.return_value = mock_result

        resp = client.get(
            "/api/assets/search?q=srv", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["count"] == 1

    def test_search_missing_q_returns_400(self, client, authed_headers):
        resp = client.get("/api/assets/search", headers=authed_headers("user"))
        assert resp.status_code == 400

    def test_search_with_field_filter(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"results": [], "count": 0, "query": "10.0"}
        blueprint_app.asset_service.search_assets.return_value = mock_result

        resp = client.get(
            "/api/assets/search?q=10.0&field=static:IP%20Address&limit=5",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.search_assets.assert_called_once_with(
            query="10.0", field="static:IP Address", limit=5,
        )


# ---------------------------------------------------------------------------
# GET /api/assets/fields — explorer column metadata
# ---------------------------------------------------------------------------

class TestExplorerFields:
    """GET /api/assets/fields — returns the FieldCatalog `entries` payload."""

    def test_fields_success(self, client, authed_headers, blueprint_app):
        """Endpoint delegates to build_catalog_response via FieldResolver."""
        from unittest.mock import MagicMock as _MM
        fake_catalog = _MM()
        fake_catalog.entries.return_value = []
        blueprint_app.field_resolver.catalog.return_value = fake_catalog

        resp = client.get("/api/assets/fields", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # build_catalog_response shape: {"entries": [...]}
        assert "entries" in body["data"]
        assert isinstance(body["data"]["entries"], list)


class TestExplorerFieldsFilterOnly:
    """The filter-only opt-in on GET /api/assets/fields.

    Default-off is the safety property: every consumer that has not asked for
    filter-only fields — reporting, unified fields, MCP, the column picker — is
    protected without being touched. Flip the default and each of them can
    offer a field that hangs on projection, pivot and sort.
    """

    @staticmethod
    def _seed(blueprint_app):
        """Catalog holding one ordinary entry and one filter-only entry.

        Both are `virtual` so they travel the identical serializer path and the
        only variable between them is the flag under test.
        """
        from unittest.mock import MagicMock as _MM
        from src.modules.fields.catalog import CatalogEntry
        from src.modules.fields.types import FieldDataType

        fake_catalog = _MM()
        fake_catalog.entries.return_value = [
            CatalogEntry(
                kind_name="virtual",
                display_name="Asset Label",
                data_type=FieldDataType.STRING,
                operators=(),
                virtual_name="asset_label",
            ),
            CatalogEntry(
                kind_name="virtual",
                display_name="Alert",
                data_type=FieldDataType.STRING,
                operators=(),
                allowed_operator_codes=("eq", "any"),
                virtual_name="alert",
                filter_only=True,
            ),
        ]
        blueprint_app.field_resolver.catalog.return_value = fake_catalog

    @staticmethod
    def _names(resp):
        return [e["display_name"] for e in resp.get_json()["data"]["entries"]]

    def test_fields_excludes_filter_only_by_default(self, client, authed_headers, blueprint_app):
        self._seed(blueprint_app)
        resp = client.get("/api/assets/fields", headers=authed_headers("user"))
        assert resp.status_code == 200
        names = self._names(resp)
        assert "Asset Label" in names      # the catalog was read at all
        assert "Alert" not in names        # …and the filter-only one was dropped

    def test_fields_includes_filter_only_on_request(self, client, authed_headers, blueprint_app):
        self._seed(blueprint_app)
        resp = client.get(
            "/api/assets/fields?include_filter_only=true", headers=authed_headers("user")
        )
        assert resp.status_code == 200
        assert "Alert" in self._names(resp)

    def test_filter_only_flag_is_on_the_wire(self, client, authed_headers, blueprint_app):
        # The Explorer opts in and then strips these from its own pickers, so it
        # needs the flag per entry, not merely their presence.
        self._seed(blueprint_app)
        resp = client.get(
            "/api/assets/fields?include_filter_only=true", headers=authed_headers("user")
        )
        by_name = {e["display_name"]: e for e in resp.get_json()["data"]["entries"]}
        assert by_name["Alert"]["filter_only"] is True
        assert by_name["Asset Label"]["filter_only"] is False

    def test_an_unparseable_flag_is_rejected_not_guessed(self, client, authed_headers, blueprint_app):
        # Validation errors are 400 in this harness, not 422. Refusing beats
        # guessing: 'maybe' silently meaning false would hide the opt-in.
        self._seed(blueprint_app)
        resp = client.get(
            "/api/assets/fields?include_filter_only=maybe", headers=authed_headers("user")
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/values — distinct field values
# ---------------------------------------------------------------------------

class TestFieldValues:
    """GET /api/assets/values"""

    def test_values_success(self, client, authed_headers, blueprint_app):
        # Service returns a dict; response_wrapper wraps it as {"success": True, "data": ...}
        blueprint_app.asset_service.get_field_values.return_value = {
            "values": [
                {"value": "Server", "count": 100},
                {"value": "Desktop", "count": 50},
            ],
        }

        resp = client.get(
            "/api/assets/values?field=static:Category",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_values_missing_field_returns_400(self, client, authed_headers):
        resp = client.get("/api/assets/values", headers=authed_headers("user"))
        assert resp.status_code == 400

    def test_values_with_search_prefix(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_field_values.return_value = []

        resp = client.get(
            "/api/assets/values?field=static:Hostname&search=srv&limit=10",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.get_field_values.assert_called_once_with(
            field="static:Hostname", limit=10, search="srv", filters=None,
            include_total=True,
        )

    def test_values_with_context_filters(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_field_values.return_value = []
        filters = json.dumps([{
            "field_id": {"kind": "static", "name": "Category"},
            "op": "eq",
            "value": "Server",
        }])

        resp = client.get(
            f"/api/assets/values?field=static:Hostname&filters={filters}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.get_field_values.call_args.kwargs
        assert call_kwargs["filters"] is not None

    def test_values_include_total_false(self, client, authed_headers, blueprint_app):
        """?include_total=false is parsed to a bool and threaded through (#1406)."""
        blueprint_app.asset_service.get_field_values.return_value = {
            "field": "static:Hostname", "values": [], "total_distinct": None,
        }

        resp = client.get(
            "/api/assets/values?field=static:Hostname&include_total=false",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.get_field_values.assert_called_once_with(
            field="static:Hostname", limit=50, search=None, filters=None,
            include_total=False,
        )
        assert resp.get_json()["data"]["total_distinct"] is None

    def test_values_include_total_defaults_true(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_field_values.return_value = []

        resp = client.get(
            "/api/assets/values?field=static:Hostname",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.get_field_values.call_args.kwargs
        assert call_kwargs["include_total"] is True


# ---------------------------------------------------------------------------
# GET /api/assets/aggregate — pivot table
# ---------------------------------------------------------------------------

class TestAggregateAssets:
    """GET /api/assets/aggregate"""

    def test_aggregate_success(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.aggregate_assets.return_value = {
            "rows": ["Server"], "columns": [], "data": [[100]], "grand_total": 100,
        }

        resp = client.get(
            "/api/assets/aggregate?row_field=static:Category",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_aggregate_missing_row_field_returns_400(self, client, authed_headers):
        resp = client.get("/api/assets/aggregate", headers=authed_headers("user"))
        assert resp.status_code == 400

    def test_aggregate_two_dimensions(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.aggregate_assets.return_value = {
            "rows": ["Server"], "columns": ["Windows"], "data": [[42]], "grand_total": 42,
        }

        resp = client.get(
            "/api/assets/aggregate?row_field=static:Category&col_field=static:OS%20Normalized",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.asset_service.aggregate_assets.assert_called_once_with(
            row_field="static:Category",
            col_field="static:OS Normalized",
            filters=None,
            alert_id=None,
        )

    def test_aggregate_with_filters(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.aggregate_assets.return_value = {
            "rows": [], "columns": [], "data": [], "grand_total": 0,
        }
        filters = json.dumps([{
            "field_id": {"kind": "static", "name": "OS Normalized"},
            "op": "eq",
            "value": "Windows",
        }])

        resp = client.get(
            f"/api/assets/aggregate?row_field=static:Category&filters={filters}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.aggregate_assets.call_args.kwargs
        assert call_kwargs["filters"] is not None

    @pytest.mark.parametrize("sent", [
        # Python's UUID() parses these; Postgres rejects the first two at ::uuid.
        # Only canonicalising — not merely validating — keeps them off the cast.
        "urn:uuid:948e3f69-818b-4649-8f77-362048686475",
        "9-48e3f69818b46498f77362048686475",    # hyphen after a group of one
        "948E3F69-818B-4649-8F77-362048686475",
        "{948e3f69-818b-4649-8f77-362048686475}",
    ])
    def test_aggregate_alert_id_reaches_the_service_canonically_spelled(
        self, client, authed_headers, blueprint_app, sent,
    ):
        # The pivot export narrows through this endpoint (#1013). ensure_uuid
        # RETURNS the canonical spelling and THAT is what must travel on: echo the
        # caller's spelling instead and the 500 the ::uuid cast throws on a
        # Postgres-rejected form moves rather than goes away (#973/#999) — so this
        # asserts the value passed on, not just the status.
        blueprint_app.asset_service.aggregate_assets.return_value = {
            "rows": [], "columns": [], "data": [], "grand_total": 0,
        }

        resp = client.get(
            f"/api/assets/aggregate?row_field=static:Category&alert_id={sent}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_kwargs = blueprint_app.asset_service.aggregate_assets.call_args.kwargs
        assert call_kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    @pytest.mark.parametrize("alert_id", [
        "not-a-uuid",
        "948e3f69-818b-4649-8f77-36204868647",   # 31 hex digits — the near miss
        "0",
    ])
    def test_aggregate_malformed_alert_id_is_400_not_500(
        self, client, authed_headers, blueprint_app, alert_id,
    ):
        # The repository casts the id straight to %(alert_id)s::uuid, and
        # psycopg2's error on a typo is not a ValueError, so handle_errors'
        # catch-all would answer a client typo with a 500 (#973/#999). ensure_uuid
        # rejects it in the handler first — the mocked service cannot raise that DB
        # error, so "rejected before the service" stands in for it here.
        resp = client.get(
            f"/api/assets/aggregate?row_field=static:Category&alert_id={alert_id}",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.aggregate_assets.assert_not_called()


class TestDataSourceDeduplication:
    """GET /api/assets with fields= — Data Source arrays should be collapsed.

    Response dict keys are in canonical percent-encoded form
    (`static:Data%20Source`) regardless of Flask URL decoding, because the
    repository re-encodes aliases from the resolved FieldId.
    """

    @patch("src.api.blueprints.assets.get_cached_group_mapping")
    def test_data_source_collapsed_in_dynamic_list(
        self, mock_mapping, client, authed_headers, blueprint_app
    ):
        mock_mapping.return_value = {"Entra - A": "Entra", "Entra - B": "Entra"}
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "success": True,
            "assets": [
                {
                    "id": "a1",
                    "static:Data%20Source": ["Entra - A", "Entra - B", "Intune"],
                    "static:Hostname": "ws-001",
                }
            ],
            "pagination": {"total": 1, "page": 1, "limit": 50, "pages": 1},
        }

        resp = client.get(
            "/api/assets?fields=static:Data%20Source,static:Hostname&page=1&limit=50",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assets = body["assets"]
        assert assets[0]["static:Data%20Source"] == ["Entra", "Intune"]

    @patch("src.api.blueprints.assets.get_cached_group_mapping")
    def test_no_groups_data_source_unchanged(
        self, mock_mapping, client, authed_headers, blueprint_app
    ):
        mock_mapping.return_value = {}
        blueprint_app.asset_service.list_assets_dynamic.return_value = {
            "success": True,
            "assets": [{"id": "a1", "static:Data%20Source": ["Entra - A", "Intune"]}],
            "pagination": {"total": 1, "page": 1, "limit": 50, "pages": 1},
        }

        resp = client.get(
            "/api/assets?fields=static:Data%20Source&page=1&limit=50",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assets = body["assets"]
        assert assets[0]["static:Data%20Source"] == ["Entra - A", "Intune"]


# ---------------------------------------------------------------------------
# Sort-by InvalidFieldError propagation (regression for silent fallback)
# ---------------------------------------------------------------------------

class TestSortByInvalidFieldPropagates:
    """Previously, an unknown sort_by silently fell back to last_time DESC.
    Now, InvalidFieldError must propagate through the service to the blueprint,
    which converts it to a 400 ValidationError.
    """

    def test_list_assets_with_nonexistent_sort_by_returns_400(
        self, client, authed_headers, blueprint_app
    ):
        """list_assets_dynamic raising InvalidFieldError surfaces as 400."""
        from src.modules.fields import InvalidFieldError

        blueprint_app.asset_service.list_assets_dynamic.side_effect = InvalidFieldError(
            "FieldId does not resolve to any live catalog entry"
        )

        resp = client.get(
            "/api/assets?fields=static:Hostname&sort_by=ext:ghost_source:ghost_attr",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/export.csv — streamed full-set CSV download
# ---------------------------------------------------------------------------

class TestExportAssetsCsv:
    """GET /api/assets/export.csv — streamed full-set CSV download."""

    def test_requires_auth(self, client):
        assert client.get("/api/assets/export.csv").status_code == 401

    def test_streams_filtered_set_with_headers(self, client, authed_headers):
        rows = [
            {"hostname": "web01", "ip": ["10.0.0.1"], "category": "server",
             "subcategory": "linux", "os_normalized": "Ubuntu 22.04",
             "owner": "ops", "data_source": ["Defender", "Intune"],
             "last_seen": "2026-06-18T00:00:00Z"},
        ]
        with patch(
            "src.api.blueprints.assets.list_assets_query",
            return_value=(rows, 1),
        ) as mock_q:
            resp = client.get(
                '/api/assets/export.csv?search=ubuntu&sort_by=hostname&sort_dir=asc',
                headers=authed_headers("user"),
            )
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert "attachment" in resp.headers["Content-Disposition"]
        assert ".csv" in resp.headers["Content-Disposition"]
        body = resp.get_data(as_text=True)
        assert body.startswith(
            "Hostname,IP,Category,Subcategory,OS,Owner,Data Source,Last Seen\r\n"
        )
        assert "web01,10.0.0.1,server,linux,Ubuntu 22.04,ops,Defender; Intune," \
               "2026-06-18T00:00:00Z\r\n" in body
        # search / sort forwarded to the shared query path
        _, kwargs = mock_q.call_args
        assert kwargs["search"] == "ubuntu"
        assert kwargs["sort_by"] == "hostname"
        assert kwargs["sort_dir"] == "asc"
        # The export must request ip/owner/data_source explicitly; the repo's
        # default field set omits them, which would leave those CSV columns
        # blank for every row.
        from src.api.blueprints.assets import _EXPORT_FIELDS_ENCODED
        assert kwargs["fields"] == _EXPORT_FIELDS_ENCODED
        assert "static:IP%20Address" in kwargs["fields"]

    def test_paginates_until_total_covered(self, client, authed_headers):
        page1 = [{"hostname": f"h{i}"} for i in range(200)]
        page2 = [{"hostname": f"h{i}"} for i in range(200, 250)]
        with patch(
            "src.api.blueprints.assets.list_assets_query",
            side_effect=[(page1, 250), (page2, 250)],
        ) as mock_q:
            resp = client.get("/api/assets/export.csv", headers=authed_headers("user"))
        body = resp.get_data(as_text=True)
        assert mock_q.call_count == 2
        # 8-column rows render trailing commas, so match "<host>," not "<host>\r\n".
        assert "h0," in body and "h249," in body
        calls = mock_q.call_args_list
        assert calls[0].kwargs["page"] == 1
        assert calls[1].kwargs["page"] == 2

    def test_respects_max_rows_bound(self, client, authed_headers, monkeypatch):
        monkeypatch.setattr("src.api.blueprints.assets._EXPORT_MAX_ROWS", 2)
        rows = [{"hostname": f"h{i}"} for i in range(10)]
        with patch("src.api.blueprints.assets.list_assets_query",
                   return_value=(rows, 10)):
            resp = client.get("/api/assets/export.csv", headers=authed_headers("user"))
        body = resp.get_data(as_text=True)
        assert "h0," in body and "h1," in body
        assert "h2," not in body

    def test_export_columns_match_chat_table(self):
        # Drift guard: the export CSV must show the same columns as the chat
        # list table, or the downloaded file won't match what the user saw.
        from src.api.blueprints.assets import _EXPORT_COLUMNS
        from src.api.blueprints.chat.artifacts import _LIST_ASSET_COLUMNS
        assert _EXPORT_COLUMNS == _LIST_ASSET_COLUMNS

    def test_forwards_filters_param_verbatim(self, client, authed_headers):
        filters_json = '[{"field":"OS","op":"eq","value":"Ubuntu"}]'
        with patch(
            "src.api.blueprints.assets.list_assets_query",
            return_value=([{"hostname": "web01"}], 1),
        ) as mock_q:
            resp = client.get(
                "/api/assets/export.csv",
                query_string={"filters": filters_json},
                headers=authed_headers("user"),
            )
        assert resp.status_code == 200
        _, kwargs = mock_q.call_args
        assert kwargs["filters"] == filters_json

    def test_empty_result_yields_header_only(self, client, authed_headers):
        with patch("src.api.blueprints.assets.list_assets_query",
                   return_value=([], 0)):
            resp = client.get("/api/assets/export.csv", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert body == (
            "Hostname,IP,Category,Subcategory,OS,Owner,Data Source,Last Seen\r\n"
        )

    def test_malformed_filters_returns_400(self, client, authed_headers):
        # A malformed filters param must 400 — not silently stream a
        # header-only CSV (list_assets_query swallows the parse error → ([],0)).
        resp = client.get(
            "/api/assets/export.csv",
            query_string={"filters": "NOT-JSON"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400

    def test_unresolvable_filter_field_returns_400_not_truncated_stream(
        self, client, authed_headers,
    ):
        # An unresolvable filter field/operator/sort now RAISES inside
        # list_assets_query. The route resolves page 1 up front, so it is a
        # clean 400 BEFORE the 200 + headers are flushed — never a truncated
        # CSV mid-stream (the failure mode the up-front resolve prevents).
        from src.mcp.tools.assets import FieldResolutionError
        with patch(
            "src.api.blueprints.assets.list_assets_query",
            side_effect=FieldResolutionError("Unknown filter field 'nope'."),
        ):
            resp = client.get(
                "/api/assets/export.csv",
                query_string={
                    "filters": '[{"field":"nope","op":"eq","value":"x"}]',
                },
                headers=authed_headers("user"),
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/export.csv?fields= — column-driven CSV (plan §3.2)
# ---------------------------------------------------------------------------

class TestExportAssetsCsvFields:
    """A download must contain EXACTLY the columns the user picked; the legacy
    fixed 8-column set ignored `fields`, so a chosen column never reached the
    file. This is the column-driven export parity fix."""

    def test_fields_param_drives_csv_columns(self, client, authed_headers):
        rows = [{"hostname": "web01", "category": "server"}]
        with patch("src.api.blueprints.assets.list_assets_query",
                   return_value=(rows, 1)) as mock_q:
            resp = client.get(
                "/api/assets/export.csv?fields=static:Hostname,static:Category",
                headers=authed_headers("user"),
            )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # exactly the two chosen columns, in order, with catalog labels
        assert body.startswith("Hostname,Category\r\n")
        assert "web01,server\r\n" in body
        # and the chosen projection is forwarded to the shared query path
        assert mock_q.call_args.kwargs["fields"] == "static:Hostname,static:Category"

    def test_no_fields_keeps_legacy_default_columns(self, client, authed_headers):
        # Back-compat: absent `fields` → the drift-tested 8-column default.
        with patch("src.api.blueprints.assets.list_assets_query",
                   return_value=([], 0)):
            resp = client.get("/api/assets/export.csv",
                              headers=authed_headers("user"))
        body = resp.get_data(as_text=True)
        assert body == (
            "Hostname,IP,Category,Subcategory,OS,Owner,Data Source,Last Seen\r\n"
        )


# ---------------------------------------------------------------------------
# GET /api/assets/aggregate.csv — pivot matrix CSV (plan §2.A)
# ---------------------------------------------------------------------------

class TestExportAggregateCsv:
    """GET /api/assets/aggregate.csv — the full aggregate matrix as CSV."""

    def test_requires_row_field(self, client, authed_headers):
        resp = client.get("/api/assets/aggregate.csv",
                          headers=authed_headers("user"))
        assert resp.status_code == 400

    def test_1d_matrix_streams_with_grand_total_row(self, client, authed_headers):
        # COUNT is additive → the export appends a grand-total row so the
        # downloaded file is self-contained (not just the per-row counts).
        result = {"rows": ["server", "workstation"], "columns": [],
                  "data": {"server": {"count": 3}, "workstation": {"count": 2}},
                  "grand_total": 5}
        with patch("src.api.blueprints.assets.aggregate_assets_query",
                   return_value=(result, None)):
            resp = client.get("/api/assets/aggregate.csv?row_field=Category",
                              headers=authed_headers("user"))
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        body = resp.get_data(as_text=True)
        assert body.startswith("Category,Count\r\n")
        assert "server,3\r\n" in body
        assert "Total,5\r\n" in body

    def test_2d_matrix_has_col_headers_and_totals(self, client, authed_headers):
        result = {"rows": ["server"], "columns": ["Windows", "Linux"],
                  "data": {"server": {"Windows": 4, "Linux": 2}},
                  "grand_total": 6}
        with patch("src.api.blueprints.assets.aggregate_assets_query",
                   return_value=(result, None)):
            resp = client.get(
                "/api/assets/aggregate.csv"
                "?row_field=Category&col_field=OS%20Normalized",
                headers=authed_headers("user"),
            )
        body = resp.get_data(as_text=True)
        assert body.startswith("Category,Windows,Linux,Total\r\n")
        assert "server,4,2,6\r\n" in body

    def test_invalid_aggregate_returns_400(self, client, authed_headers):
        # A bad agg/metric combo surfaces as a clean 400, not a broken stream.
        with patch("src.api.blueprints.assets.aggregate_assets_query",
                   return_value=(None, "Aggregate SUM needs a metric_field.")):
            resp = client.get("/api/assets/aggregate.csv?row_field=Category&agg=SUM",
                              headers=authed_headers("user"))
        assert resp.status_code == 400
