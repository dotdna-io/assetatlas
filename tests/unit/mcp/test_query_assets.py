# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``query_assets`` keystone primitive (plan §2.A).

``query_assets`` is the single high-frequency asset tool. It COMPOSES the
existing thin wrappers (``list_assets_query`` / ``aggregate_assets_query`` /
``get_field_values_query``) behind a required ``mode`` enum — it must NOT be a
new query engine, so every test patches ``AssetRepository`` and asserts the
composed call + the shaped (text, artifact) output.

WHY the behaviors matter is encoded per test: a dropped filter/sort silently
*widens* the result (correctness + over-disclosure, §4); an aggregate that can't
be drilled into is a count you can't substantiate (§3.6); a column the user asked
for that doesn't render defeats the whole "column-driven parity" goal (§3.2).
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.mcp.tools.query_assets import query_assets
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.encoding import encode, decode
from src.modules.fields.models import ExtendedAttributeField


def _resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _patch_repo():
    """Patch the AssetRepository the wrappers instantiate."""
    return patch("src.mcp.tools.assets.AssetRepository")


# ===========================================================================
# mode=list
# ===========================================================================

class TestModeList:
    def test_happy_path_returns_text_and_table_artifact(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a1", "hostname": ["host01"], "category": "server"}], 1,
            )
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="list",
            )
        assert "Assets: 1 total" in text
        assert artifact is not None
        assert artifact["type"] == "table"
        assert artifact["meta"]["source_tool"] == "query_assets"

    def test_custom_fields_drive_artifact_columns_including_ext(self):
        """A custom ``fields`` set — incl. an ext: attribute — must render as
        actual columns with catalog labels, not be ignored. This is the whole
        point of column-driven parity (§3.2): any catalog field is selectable."""
        ext_key = encode(ExtendedAttributeField(
            kind="extended_attribute", source="Beszel", attribute="cpu",
        ))
        row = {"id": "a1", "hostname": ["h1"], ext_key: "55%"}
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([row], 1)
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="list",
                fields=["Hostname", ext_key],
            )
        col_keys = [c["key"] for c in artifact["columns"]]
        assert ext_key in col_keys
        # the ext column renders a human label, never the opaque encoded key
        ext_label = next(c["label"] for c in artifact["columns"] if c["key"] == ext_key)
        assert ext_label and ":" not in ext_label

    def test_fields_accepts_comma_string_too(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a1", "hostname": ["h1"], "category": "server"}], 1,
            )
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="list",
                fields="Hostname,Category",
            )
        assert {c["key"] for c in artifact["columns"]} == {"hostname", "category"}

    def test_unknown_filter_field_is_loud_not_dropped(self):
        """A dropped filter widens the result silently — both wrong and an
        over-disclosure risk. query_assets must surface a string error."""
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="list",
                filters=[{"field": "NoSuchField", "op": "eq", "value": "x"}],
            )
        assert artifact is None
        assert "NoSuchField" in text

    def test_unknown_operator_is_loud(self):
        with _patch_repo():
            text, _ = query_assets(
                MagicMock(), _resolver(), mode="list",
                filters=[{"field": "Category", "op": "florbs", "value": "x"}],
            )
        assert "florbs" in text.lower() or "operator" in text.lower()

    def test_unresolvable_sort_by_is_loud(self):
        """A silently-dropped sort returns a DIFFERENT order than asked, with no
        signal (§4) — must be a loud error, not a default-sorted result."""
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            text, _ = query_assets(
                MagicMock(), _resolver(), mode="list", sort_by="NoSuchField",
            )
        assert "NoSuchField" in text

    def test_logic_or_passes_through_to_repo(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = ([], 0)
            query_assets(
                MagicMock(), _resolver(), mode="list", logic="or",
                filters=[
                    {"field": "Category", "op": "eq", "value": "server"},
                    {"field": "Category", "op": "eq", "value": "workstation"},
                ],
            )
        assert MockRepo.return_value.list_assets.call_args[1]["logic"] == "or"


# ===========================================================================
# mode=aggregate
# ===========================================================================

class TestModeAggregate:
    def _1d(self):
        return {
            "rows": ["server", "workstation"], "columns": [],
            "data": {"server": {"count": 3}, "workstation": {"count": 2}},
            "grand_total": 5,
        }

    def test_row_field_required(self):
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
            )
        assert artifact is None
        assert "row_field" in text

    def test_1d_count_text_and_donut_with_drilldown(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = self._1d()
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate", row_field="Category",
            )
        assert "COUNT: 5" in text
        assert artifact["type"] == "donut"
        # every slice carries a re-runnable drilldown (§3.6)
        top = artifact["series"][0]
        assert top["drilldown"]["filters"] == [
            {"field": "Category", "op": "eq", "value": "server"},
        ]

    def test_drilldown_round_trips_to_list(self):
        """The aggregate cell's drilldown, fed back as mode=list filters, must
        return the substantiated rows — the count you can prove."""
        resolver = _resolver()
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = self._1d()
            _, artifact = query_assets(
                MagicMock(), resolver, mode="aggregate", row_field="Category",
            )
        drill = artifact["series"][0]["drilldown"]["filters"]
        with _patch_repo() as MockRepo:
            MockRepo.return_value.list_assets.return_value = (
                [{"id": "a1", "hostname": ["h1"], "category": "server"}], 3,
            )
            text, list_art = query_assets(
                MagicMock(), resolver, mode="list", filters=drill,
            )
        # the drilldown filter reached the repo as a Category=server clause
        sent = MockRepo.return_value.list_assets.call_args[1]["filters"]
        assert sent[0]["field_id"]["name"] == "Category"
        assert list_art is not None

    def test_2d_stacked_bar_with_per_cell_drilldown(self):
        result = {
            "rows": ["server"], "columns": ["Windows", "Linux"],
            "data": {"server": {"Windows": 4, "Linux": 2}}, "grand_total": 6,
        }
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = result
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", col_field="OS Normalized",
            )
        assert artifact["type"] == "stacked_bar"
        stack = artifact["series"][0]["stacks"][0]
        # a 2-D cell drills down on BOTH axes
        fields = {f["field"] for f in stack["drilldown"]["filters"]}
        assert fields == {"Category", "OS Normalized"}

    def test_sum_threads_agg_and_metric_to_repo(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server"], "columns": [],
                "data": {"server": {"count": 12.5}}, "grand_total": 12.5,
            }
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="SUM", metric_field="Confidence Score",
            )
        kw = MockRepo.return_value.aggregate_assets.call_args[1]
        assert kw["agg"] == "SUM"
        assert kw["metric_field"] is not None
        # SUM is additive, so a total is surfaced
        assert artifact["meta"]["total"] == 12.5

    def test_avg_has_no_misleading_sum_total(self):
        """Averages don't sum: a 'total' across AVG cells is meaningless, so it
        must NOT be presented as if it were a real figure."""
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server", "workstation"], "columns": [],
                "data": {"server": {"count": 0.9}, "workstation": {"count": 0.7}},
                "grand_total": 1.6,
            }
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="AVG", metric_field="Confidence Score",
            )
        assert artifact["meta"].get("total") is None

    def test_metric_required_for_sum(self):
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="SUM",
            )
        assert artifact is None
        assert "metric" in text.lower()

    def test_show_percent_total_renders_percentages(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = self._1d()
            text, _ = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", show="percent_total",
            )
        assert "%" in text

    def test_row_limit_caps_and_reports_omitted(self):
        big = {
            "rows": [f"c{i}" for i in range(10)], "columns": [],
            "data": {f"c{i}": {"count": 10 - i} for i in range(10)},
            "grand_total": 55,
        }
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = big
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", row_limit=3,
            )
        # only the top 3 rows survive into the structured matrix
        assert len(artifact["meta"]["matrix"]["rows"]) == 3
        # and the omission is signalled, never silent
        assert "omitted" in text.lower() or "more" in text.lower()


# ===========================================================================
# mode=field_values
# ===========================================================================

class TestModeFieldValues:
    def test_happy_path_values_and_total(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.get_field_values.return_value = (
                [{"value": "server", "count": 7}, {"value": "workstation", "count": 3}],
                2,
            )
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="field_values", field="Category",
            )
        assert "server" in text
        assert artifact is not None and artifact["type"] == "table"

    def test_field_required(self):
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="field_values",
            )
        assert artifact is None
        assert "field" in text.lower()

    def test_limit_capped_at_500(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.get_field_values.return_value = ([], 0)
            query_assets(
                MagicMock(), _resolver(), mode="field_values",
                field="Category", limit=9999,
            )
        assert MockRepo.return_value.get_field_values.call_args[1]["limit"] == 500

    def test_unknown_field_is_loud(self):
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="field_values", field="NoSuchField",
            )
        assert artifact is None
        assert "NoSuchField" in text


# ===========================================================================
# mode gating
# ===========================================================================

class TestMode:
    def test_unknown_mode_is_loud(self):
        with _patch_repo():
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="frobnicate",
            )
        assert artifact is None
        assert "mode" in text.lower()


# ===========================================================================
# registry registration
# ===========================================================================

class TestRegistration:
    def test_query_assets_is_registered_with_mode_enum(self):
        from src.api.blueprints.chat.tool_bindings import build_registry
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        specs = {s.name: s for s in r.for_role("user")}
        assert "query_assets" in specs
        mode_prop = specs["query_assets"].input_schema["properties"]["mode"]
        assert set(mode_prop["enum"]) == {"list", "aggregate", "field_values"}
        assert specs["query_assets"].input_schema["required"] == ["mode"]


# ===========================================================================
# numeric / Decimal safety (regression — code review xhigh)
# ===========================================================================

class TestAggregateNumericSafety:
    """SUM/AVG/MIN/MAX over a NUMERIC column come back as decimal.Decimal
    (psycopg2). The artifact must stay JSON-serializable (the chat repo persists
    it via a plain json.dumps) AND keep fractional values, and MIN/MAX over a
    text column must not crash the Python shaping."""

    def test_decimal_avg_artifact_is_json_safe_and_not_truncated(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server", "workstation"], "columns": [],
                "data": {"server": {"count": Decimal("0.83")},
                         "workstation": {"count": Decimal("0.40")}},
                "grand_total": Decimal("1.23"),
            }
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="AVG", metric_field="Confidence Score",
            )
        # must serialize — a raw Decimal in meta.matrix would raise here
        json.dumps(artifact)
        # fractional AVG preserved (0.83), NOT int()-floored to 0
        assert artifact["meta"]["matrix"]["data"]["server"]["count"] == 0.83

    def test_decimal_sum_donut_value_preserved(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server"], "columns": [],
                "data": {"server": {"count": Decimal("3.7")}},
                "grand_total": Decimal("3.7"),
            }
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="SUM", metric_field="Confidence Score",
            )
        json.dumps(artifact)
        # SUM is additive → donut; the slice value keeps 3.7 (not floored to 3)
        assert artifact["series"][0]["value"] == 3.7

    def test_min_over_text_metric_does_not_crash(self):
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["server"], "columns": [],
                "data": {"server": {"count": "alpha-host"}},
                "grand_total": 0,
            }
            text, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate",
                row_field="Category", agg="MIN", metric_field="Hostname",
            )
        # the string MIN value renders (no float('alpha-host') crash) and the
        # artifact is JSON-safe
        assert "alpha-host" in text
        json.dumps(artifact)

    def test_aggregate_drilldown_keeps_base_filter_scope(self):
        """Drilling a FILTERED aggregate must reproduce the cell's population:
        the base filter is PREPENDED to each cell's drilldown, else the drill
        would return rows outside the original scope (breaks §3.6)."""
        with _patch_repo() as MockRepo:
            MockRepo.return_value.aggregate_assets.return_value = {
                "rows": ["alice"], "columns": [],
                "data": {"alice": {"count": 3}}, "grand_total": 3,
            }
            _, artifact = query_assets(
                MagicMock(), _resolver(), mode="aggregate", row_field="Owner",
                filters=[{"field": "Category", "op": "eq", "value": "server"}],
            )
        drill = artifact["series"][0]["drilldown"]["filters"]
        fields = [f["field"] for f in drill]
        assert "Category" in fields and "Owner" in fields
