# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``export_assets`` tool (plan §2.A).

export_assets is the whole-set / column-chosen CSV download. It must NOT pull
the full set into context — it returns a small preview + a table artifact whose
``meta.query`` wires the frontend Download button to the parameterized CSV
route. WHY: the chosen ``fields`` become the EXACT CSV columns (parity with what
the user saw); the aggregate target carries the pivot params so its matrix CSV
matches the on-screen table.
"""

from unittest.mock import MagicMock, patch

from src.mcp.tools.export_assets import export_assets
from src.mcp.tools._query import CHAT_LIST_FIELDS_ENCODED
from src.modules.fields import FieldCatalog, FieldResolver


def _resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _patch_repo():
    return patch("src.mcp.tools.assets.AssetRepository")


class TestExportList:
    def test_columns_equal_chosen_fields(self):
        """The exported columns must be exactly the requested fields — that
        parity (CSV == what you picked) is the entire point of the tool."""
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a1", "hostname": ["h1"], "category": "server"}], 42,
            )
            text, artifact = export_assets(
                MagicMock(), _resolver(), target="list",
                fields=["Hostname", "Category"],
            )
        assert artifact["meta"]["source_tool"] == "export_assets"
        assert [c["key"] for c in artifact["columns"]] == ["hostname", "category"]
        # the download query carries the chosen fields so the route emits them
        assert artifact["meta"]["query"]["fields"] == "Hostname,Category"
        assert "42" in text  # the full total, not the preview length

    def test_default_fields_used_when_unspecified(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a1", "hostname": ["h1"], "category": "server"}], 1,
            )
            _, artifact = export_assets(MagicMock(), _resolver(), target="list")
        assert artifact["meta"]["query"]["fields"] == CHAT_LIST_FIELDS_ENCODED

    def test_unknown_filter_field_is_loud(self):
        with _patch_repo():
            text, artifact = export_assets(
                MagicMock(), _resolver(), target="list",
                filters=[{"field": "Nope", "op": "eq", "value": "x"}],
            )
        assert artifact is None
        assert "Nope" in text


class TestExportAggregate:
    def test_matrix_artifact_wired_for_aggregate_download(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server"], "columns": [],
                "data": {"server": {"count": 3}}, "grand_total": 3,
            }
            text, artifact = export_assets(
                MagicMock(), _resolver(), target="aggregate",
                row_field="Category",
            )
        assert artifact["meta"]["source_tool"] == "export_assets"
        # 'aggregate' tells the frontend to use the matrix CSV route
        assert artifact["meta"]["layout"] == "aggregate"
        assert artifact["meta"]["query"]["row_field"] == "Category"
        assert "matrix" in text.lower()

    def test_row_field_required(self):
        with _patch_repo():
            text, artifact = export_assets(
                MagicMock(), _resolver(), target="aggregate",
            )
        assert artifact is None
        assert "row_field" in text

    def test_sum_threads_agg_and_metric(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server"], "columns": [],
                "data": {"server": {"count": 9.0}}, "grand_total": 9.0,
            }
            _, artifact = export_assets(
                MagicMock(), _resolver(), target="aggregate",
                row_field="Category", agg="SUM", metric_field="Confidence Score",
            )
        kw = MockRepo.return_value.aggregate_assets.call_args[1]
        assert kw["agg"] == "SUM" and kw["metric_field"] is not None
        assert artifact["meta"]["query"]["agg"] == "SUM"


class TestExportTarget:
    def test_unknown_target_is_loud(self):
        with _patch_repo():
            text, artifact = export_assets(
                MagicMock(), _resolver(), target="frobnicate",
            )
        assert artifact is None
        assert "target" in text.lower()


class TestRegistration:
    def test_export_assets_registered(self):
        from src.api.blueprints.chat.tool_bindings import build_registry
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        names = {s.name for s in r.for_role("user")}
        assert "export_assets" in names
