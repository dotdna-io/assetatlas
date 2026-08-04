# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``run_chart`` / ``run_report`` (plan §2.B + §3.4).

``run_chart`` is the ONLY chat path to ``POST /api/charts/query``. It is a
PASSTHROUGH to the existing charts engine (``QueryBuilder`` + ``ChartRepository``
+ ``transform_results``) — NOT a new compiler. These tests assert:

- the §3.4 standard-grammar → charts ``FilterOperator`` wire translation (the
  model speaks ONE filter grammar everywhere; the seam is hidden here);
- loud-error-over-silent-drop (§4) on unknown/untranslatable field/op — a
  dropped or coerced filter runs a DIFFERENT query than asked;
- the request shaped correctly per query_type and driven through the REAL
  engine (only the DB boundary is mocked);
- the (text, artifact) output reuses the validated donut/stacked_bar/table
  envelope with a re-runnable per-bucket drilldown (§3.6).
"""

from unittest.mock import MagicMock

import pytest

from src.mcp.tools.assets import FieldResolutionError
from src.mcp.tools.charts_tool import (
    _resolve_select_field,
    _to_charts_operator,
    _translate_filters,
)
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.encoding import encode
from src.modules.fields.models import StaticField
from src.modules.fields.operators import OperatorResolutionError


def _resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


# ===========================================================================
# §3.4 operator translation
# ===========================================================================

class TestOperatorTranslation:
    def test_canonical_code_maps_to_charts_wire(self):
        assert _to_charts_operator("eq") == "equals"
        assert _to_charts_operator("neq") == "not_equals"
        assert _to_charts_operator("ncontains") == "not_contains"
        assert _to_charts_operator("starts") == "starts_with"
        assert _to_charts_operator("any") == "in"
        assert _to_charts_operator("none") == "not_in"
        assert _to_charts_operator("empty") == "is empty"
        assert _to_charts_operator("notempty") == "is not empty"

    def test_alias_resolves_then_maps(self):
        """The model may pass an alias spelling; it must resolve to canonical
        and then to the charts wire form, not be rejected."""
        assert _to_charts_operator("any of") == "in"
        assert _to_charts_operator(">=") == "gte"

    def test_charts_wire_value_passes_through(self):
        """run_report replays STORED charts-format ops, including ``not_in``
        which OPERATOR_REGISTRY doesn't know — it must pass through, not error."""
        assert _to_charts_operator("not_in") == "not_in"
        assert _to_charts_operator("is not empty") == "is not empty"
        assert _to_charts_operator("equals") == "equals"

    def test_untranslatable_operator_is_loud(self):
        """all/within_last/is_true/is_false have NO charts equivalent. Silently
        coercing/dropping would run a DIFFERENT query than asked (§4)."""
        for op in ("all", "within_last", "is_true", "is_false"):
            with pytest.raises(ValueError):
                _to_charts_operator(op)

    def test_unknown_operator_is_loud(self):
        with pytest.raises(OperatorResolutionError):
            _to_charts_operator("florbs")


class TestFilterTranslation:
    def test_standard_grammar_becomes_charts_conditions(self):
        out = _translate_filters(
            [{"field": "Category", "op": "eq", "value": "server"}], _resolver(),
        )
        assert out == [{
            "field_id": {"kind": "static", "name": "Category"},
            "operator": "equals",
            "value": "server",
        }]

    def test_any_value_list_maps_to_in(self):
        out = _translate_filters(
            [{"field": "Data Source", "op": "any", "value": ["A", "B"]}],
            _resolver(),
        )
        assert out[0]["operator"] == "in"
        assert out[0]["value"] == ["A", "B"]

    def test_unknown_field_is_loud_not_dropped(self):
        """A dropped filter silently WIDENS the chart's population — both wrong
        and an over-disclosure risk (§4)."""
        with pytest.raises(FieldResolutionError):
            _translate_filters(
                [{"field": "NoSuchField", "op": "eq", "value": "x"}], _resolver(),
            )

    def test_untranslatable_op_names_the_alternative(self):
        with pytest.raises(ValueError) as exc:
            _translate_filters(
                [{"field": "Hostname", "op": "all", "value": ["a"]}], _resolver(),
            )
        msg = str(exc.value).lower()
        assert "all" in msg and "query_assets" in msg

    def test_empty_input_is_empty_list(self):
        assert _translate_filters(None, _resolver()) == []
        assert _translate_filters([], _resolver()) == []


class TestSelectFieldResolution:
    def test_bare_name_resolves_to_field_id_dict(self):
        assert _resolve_select_field("Category", _resolver()) == {
            "kind": "static", "name": "Category",
        }

    def test_snake_case_name_resolves(self):
        assert _resolve_select_field("os_normalized", _resolver()) == {
            "kind": "static", "name": "OS Normalized",
        }

    def test_encoded_id_decodes_to_dict(self):
        enc = encode(StaticField(kind="static", name="Owner"))
        assert _resolve_select_field(enc, _resolver()) == {
            "kind": "static", "name": "Owner",
        }

    def test_none_returns_none(self):
        assert _resolve_select_field(None, _resolver()) is None

    def test_unknown_bare_name_is_loud(self):
        with pytest.raises(FieldResolutionError):
            _resolve_select_field("NoSuchField", _resolver())


# ===========================================================================
# drift guards — the hardcoded charts-operator set must track the live enum
# ===========================================================================

def test_charts_operator_values_match_engine_enum():
    """``_CHARTS_OPERATOR_VALUES`` is hardcoded so charts_tool stays free of the
    charts blueprint import cycle. This guards against drift if the engine ever
    adds/renames an operator — the seam would silently mis-translate otherwise."""
    from src.api.blueprints.charts.models import FilterOperator
    from src.mcp.tools.charts_tool import _CHARTS_OPERATOR_VALUES
    assert set(_CHARTS_OPERATOR_VALUES) == {op.value for op in FilterOperator}


def test_code_map_targets_are_valid_charts_operators():
    from src.api.blueprints.charts.models import FilterOperator
    from src.mcp.tools.charts_tool import _CODE_TO_CHARTS_OP
    valid = {op.value for op in FilterOperator}
    assert set(_CODE_TO_CHARTS_OP.values()) <= valid


# ===========================================================================
# run_chart — drives the REAL engine; only the DB boundary is mocked
# ===========================================================================

def _db(rows):
    db = MagicMock()
    db.execute_query.return_value = rows
    return db


class TestRunChartDistribution:
    def test_distribution_builds_donut_with_drilldown(self):
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"label": "server", "value": 5},
                  {"label": "workstation", "value": 3}])
        text, art = run_chart(db, _resolver(), None,
                              query_type="distribution", dimension="Category")
        assert art is not None and art["type"] == "donut"
        labels = [s["label"] for s in art["series"]]
        assert "server" in labels and "workstation" in labels
        # every concrete slice carries a re-runnable eq drilldown (§3.6): a count
        # the model emits must be substantiable with one query_assets re-run.
        server = next(s for s in art["series"] if s["label"] == "server")
        assert server["drilldown"]["filters"][-1]["op"] == "eq"
        assert server["drilldown"]["filters"][-1]["value"] == "server"
        assert "server" in text

    def test_distribution_requires_dimension(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([]), _resolver(), None,
                              query_type="distribution")
        assert art is None and "dimension" in text.lower()

    def test_standard_filter_translates_and_reaches_engine(self):
        """End-to-end §3.4: a standard-grammar `contains` filter must become an
        ILIKE in the SQL the engine runs — proving the seam constrains the chart,
        not silently widens it."""
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"label": "server", "value": 5}])
        run_chart(db, _resolver(), None, query_type="distribution",
                  dimension="Category",
                  filters=[{"field": "Owner", "op": "contains", "value": "alice"}])
        sql, params = db.execute_query.call_args[0]
        assert "ILIKE" in sql.upper()
        assert any("alice" in str(v) for v in params.values())

    def test_untranslatable_filter_op_is_loud(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(
            _db([]), _resolver(), None, query_type="distribution",
            dimension="Category",
            filters=[{"field": "Hostname", "op": "all", "value": ["x"]}],
        )
        assert art is None
        assert "all" in text.lower() and "query_assets" in text

    def test_json_string_filters_are_parsed_not_dropped(self):
        """The chat often serialises `filters` as a JSON STRING; it must be
        parsed and APPLIED, not silently dropped — a dropped filter charts an
        UNFILTERED population (§4 widening) and disagrees with the drilldown
        base (which parse_filters_arg would have parsed)."""
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"label": "server", "value": 5}])
        run_chart(
            db, _resolver(), None, query_type="distribution",
            dimension="Category",
            filters='[{"field": "Owner", "op": "contains", "value": "alice"}]',
        )
        sql, params = db.execute_query.call_args[0]
        assert "ILIKE" in sql.upper()
        assert any("alice" in str(v) for v in params.values())

    def test_bad_json_string_filters_is_loud(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(
            _db([]), _resolver(), None, query_type="distribution",
            dimension="Category", filters="{not valid json",
        )
        assert art is None and "json" in text.lower()

    def test_unknown_filter_field_is_loud(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(
            _db([]), _resolver(), None, query_type="distribution",
            dimension="Category",
            filters=[{"field": "NoSuchField", "op": "eq", "value": "x"}],
        )
        assert art is None and "NoSuchField" in text


class TestRunChartOtherTypes:
    def test_comparison_builds_stacked_bar_with_2d_drilldown(self):
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"x_axis": "server", "y_axis": "win", "value": 2},
                  {"x_axis": "server", "y_axis": "linux", "value": 3}])
        text, art = run_chart(db, _resolver(), None, query_type="comparison",
                              dimension="Category", segment="OS Normalized")
        assert art["type"] == "stacked_bar"
        x = art["series"][0]
        assert x["x_label"] == "server"
        stack = x["stacks"][0]
        # a 2-D cell drilldown pins BOTH the row and the column bucket
        f = stack["drilldown"]["filters"]
        assert len(f) == 2 and all(c["op"] == "eq" for c in f)

    def test_comparison_requires_both_fields(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([]), _resolver(), None, query_type="comparison",
                              dimension="Category")
        assert art is None and "segment" in text.lower()

    def test_correlation_builds_table_with_pair_drilldown(self):
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"source": "server", "target": "Windows 11", "value": 7}])
        text, art = run_chart(db, _resolver(), None, query_type="correlation",
                              dimension="Category", segment="OS Normalized")
        assert art["type"] == "table"
        row = art["series"][0]
        assert row["source"] == "server" and row["target"] == "Windows 11"
        assert "drilldown" in row

    def test_correlation_list_target_does_not_crash(self):
        """The charts transform can emit a LIST target; the drilldown guard must
        check isinstance(list) BEFORE the frozenset membership test (a list in a
        frozenset raises TypeError: unhashable type)."""
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"source": "server", "target": ["Windows 11", "macOS"],
                   "value": 7}])
        text, art = run_chart(db, _resolver(), None, query_type="correlation",
                              dimension="Category", segment="OS Normalized")
        assert art is not None and art["type"] == "table"
        # a list target has no single eq value → no drilldown, but must not crash
        assert "drilldown" not in art["series"][0]

    def test_trend_builds_table(self):
        from src.mcp.tools.charts_tool import run_chart
        db = _db([{"timestamp": "2026-01-01T00:00:00", "value": 10},
                  {"timestamp": "2026-02-01T00:00:00", "value": 12}])
        text, art = run_chart(db, _resolver(), None, query_type="trend",
                              time_field="First Seen", interval="month")
        assert art["type"] == "table"
        assert len(art["series"]) == 2

    def test_trend_requires_time_field_and_interval(self):
        from src.mcp.tools.charts_tool import run_chart
        _, a1 = run_chart(_db([]), _resolver(), None, query_type="trend",
                          interval="month")
        assert a1 is None
        t2, a2 = run_chart(_db([]), _resolver(), None, query_type="trend",
                           time_field="First Seen")
        assert a2 is None and "interval" in t2.lower()

    def test_metric_single_value(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([{"value": 42}]), _resolver(), None,
                              query_type="metric", aggregate="COUNT")
        assert "42" in text
        assert art["type"] == "table"

    def test_metric_avg_requires_metric_field(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([]), _resolver(), None, query_type="metric",
                              aggregate="AVG")
        assert art is None and "metric_field" in text.lower()

    def test_metric_percentage_labels_as_percent_not_aggregate(self):
        """percentage mode computes a COUNT ratio — the engine IGNORES the
        agg/metric_field — so the output must NOT mislabel the ratio as
        'AVG of <field>' (a confidently-wrong number)."""
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(
            _db([{"value": 42}]), _resolver(), None, query_type="metric",
            aggregate="AVG", metric_field="Confidence Score", percentage=True,
            comparison_filters=[{"field": "Category", "op": "eq",
                                 "value": "server"}],
        )
        assert "AVG of" not in text
        assert "%" in text

    def test_metric_null_value_is_not_a_fabricated_zero(self):
        """AVG/MIN/MAX/SUM over zero matching rows returns NULL — render '—',
        not '0' (which the model would report as a real average of 0)."""
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([{"value": None}]), _resolver(), None,
                              query_type="metric", aggregate="AVG",
                              metric_field="Confidence Score")
        assert ": 0" not in text
        assert "—" in text


def test_fmt_handles_non_finite_floats():
    """A NUMERIC 'NaN'/'Infinity' aggregate reaches _fmt as a Python nan/inf;
    `nan != int(nan)` raises ValueError, so _fmt must short-circuit them."""
    from src.mcp.tools.charts_tool import _fmt
    assert "nan" in _fmt(float("nan")).lower()
    assert _fmt(float("inf"))  # no raise

    def test_unknown_query_type_is_loud(self):
        from src.mcp.tools.charts_tool import run_chart
        text, art = run_chart(_db([]), _resolver(), None, query_type="bogus")
        assert art is None and "bogus" in text


# ===========================================================================
# run_report — replays a saved report's stored chart queryConfigs
# ===========================================================================

_UUID = "11111111-1111-1111-1111-111111111111"


def _qc(query_type, **over):
    base = {"queryType": query_type, "select": {"dimension": "static:Category"},
            "filters": [], "options": {}}
    base.update(over)
    return base


class TestRunReport:
    def _report_db(self, charts, chart_rows_list):
        """db.execute_query: 1st call loads the report, the rest feed each
        replayed chart's ChartRepository.execute_query in order."""
        db = MagicMock()
        db.execute_query.side_effect = (
            [[{"id": "r1", "title": "My Report", "charts": charts}]]
            + chart_rows_list
        )
        return db

    def test_replays_stored_distribution_chart(self):
        from src.mcp.tools.charts_tool import run_report
        chart = {"type": "donut", "title": "By Category",
                 "queryConfig": _qc("distribution")}
        db = self._report_db(
            [chart], [[{"label": "server", "value": 5},
                       {"label": "workstation", "value": 3}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        assert "My Report" in text and "By Category" in text
        assert art is not None and art["type"] == "donut"
        assert any(s["label"] == "server" for s in art["series"])

    def test_encoded_string_select_decodes(self):
        """The report editor stores select slots as encoded_key STRINGS; replay
        must decode them to field_id dicts or ChartQueryRequest rejects them."""
        from src.mcp.tools.charts_tool import run_report
        chart = {"title": "X", "queryConfig": _qc(
            "distribution", select={"dimension": "static:Owner"})}
        db = self._report_db([chart], [[{"label": "alice", "value": 2}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        assert art is not None and art["type"] == "donut"

    def test_stored_charts_wire_operator_replays(self):
        """A stored filter op may already be a charts-wire form like ``not_in``
        (which OPERATOR_REGISTRY does NOT know) — it must pass through, not
        crash the replay."""
        from src.mcp.tools.charts_tool import run_report
        chart = {"title": "Filtered", "queryConfig": _qc(
            "distribution",
            filters=[{"field_id": {"kind": "static", "name": "Category"},
                      "operator": "not_in", "value": ["server"]}])}
        db = self._report_db([chart], [[{"label": "workstation", "value": 3}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        assert art is not None and "workstation" in text

    def test_drilldown_includes_report_filter_scope(self):
        """Drilling a replayed bucket must re-apply the report's OWN filters,
        else 'show me those N' returns a wider set than the slice (§3.6)."""
        from src.mcp.tools.charts_tool import run_report
        chart = {"title": "Alice's, by Category", "queryConfig": _qc(
            "distribution",
            filters=[{"field_id": {"kind": "static", "name": "Owner"},
                      "operator": "eq", "value": "alice"}])}
        db = self._report_db([chart], [[{"label": "server", "value": 5}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        slice0 = next(s for s in art["series"] if s["label"] == "server")
        dd_fields = [str(f.get("field")) for f in slice0["drilldown"]["filters"]]
        assert any("Owner" in f for f in dd_fields)
        assert any("Category" in f for f in dd_fields)

    def test_replays_comparison_stored_as_dimension_segment(self):
        """A comparison chart saved in the UI's dimension/segment form must
        replay. The renderer reads xAxis/yAxis, so the coercion must alias
        dimension→xAxis / segment→yAxis — else _render_comparison KeyErrors and
        (since render is per-chart) the whole report fails."""
        from src.mcp.tools.charts_tool import run_report
        chart = {"title": "Cat × OS", "queryConfig": {
            "queryType": "comparison",
            "select": {"dimension": "static:Category",
                       "segment": "static:OS%20Normalized"},
            "filters": [], "options": {}}}
        db = self._report_db(
            [chart], [[{"x_axis": "server", "y_axis": "win", "value": 2}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        assert art is not None and art["type"] == "stacked_bar"

    def test_chart_index_selects_single_chart(self):
        from src.mcp.tools.charts_tool import run_report
        charts = [
            {"title": "A", "queryConfig": _qc("distribution")},
            {"title": "B", "queryConfig": _qc(
                "distribution", select={"dimension": "static:Owner"})},
        ]
        db = self._report_db(charts, [[{"label": "x", "value": 1}]])
        text, art = run_report(db, _resolver(), None, report_id=_UUID,
                               chart_index=1)
        assert "B" in text

    def test_report_not_found_is_loud(self):
        from src.mcp.tools.charts_tool import run_report
        db = MagicMock()
        db.execute_query.return_value = []
        text, art = run_report(db, _resolver(), None, report_id=_UUID)
        assert art is None and "not found" in text.lower()

    def test_chart_index_out_of_range_is_loud(self):
        from src.mcp.tools.charts_tool import run_report
        charts = [{"title": "A", "queryConfig": _qc("distribution")}]
        db = MagicMock()
        db.execute_query.return_value = [
            {"id": "r1", "title": "R", "charts": charts}]
        text, art = run_report(db, _resolver(), None, report_id=_UUID,
                               chart_index=5)
        assert art is None and "out of range" in text.lower()

    def test_invalid_report_id_is_loud(self):
        from src.mcp.tools.charts_tool import run_report
        db = MagicMock()
        text, art = run_report(db, _resolver(), None, report_id="not-a-uuid")
        assert art is None
        # actually loud: names the bad id + the expected shape, and never
        # touches the DB with a malformed id
        assert "not-a-uuid" in text and "uuid" in text.lower()
        db.execute_query.assert_not_called()
