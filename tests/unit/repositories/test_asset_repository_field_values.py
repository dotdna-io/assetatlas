# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL-assembly tests for AssetRepository.get_field_values (#1406, #1412).

A MagicMock adapter never evaluates SQL, so a returns-a-row test cannot see a
WHERE-clause regression. These tests capture the exact SQL strings and params
handed to execute_query and assert on them (see .claude/rules/backend.md).

Field coverage (all resolvable without DB hits — the dynamic catalog loads are
answered with [] by the dispatch below):
- ``static:Category``     → scalar branch, sql_kind "column"
- ``static:Hostname``     → native-array branch (text[] column)
- ``core:Private IP``     → JSONB-array branch (jsonb_path with ``->``)
- ``virtual:asset_label`` → scalar branch, sql_kind "virtual_expr" (COALESCE)
"""

from unittest.mock import MagicMock

import pytest

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.encoding import encode
from src.modules.fields.models import (
    CoreEnrichmentField,
    StaticField,
    VirtualField,
)


def _make_repo(rows=None, total=0):
    """Repo over the built-in catalog with a mocked adapter.

    Returns (repo, issued) where issued routes each get_field_values query by
    SQL text into "values" / "count" buckets of (sql, params) pairs. The
    catalog's own dynamic loads (tags, posture, ext attrs) also flow through
    the same mock, so counting raw execute_query calls would mix them in —
    routing on the SQL text keeps only the queries under test.
    """
    db = MagicMock()
    issued = {"values": [], "count": []}

    def _dispatch(sql, params=None):
        if "COUNT(DISTINCT" in sql:
            issued["count"].append((sql, params))
            return [{"total": total}]
        if "COUNT(*) AS count" in sql:
            issued["values"].append((sql, params))
            return list(rows or [])
        return []

    db.execute_query.side_effect = _dispatch
    resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))
    return AssetRepository(db, resolver=resolver), issued


def _static(name):
    return encode(StaticField(kind="static", name=name))


def _core(name):
    return encode(CoreEnrichmentField(kind="core_enrichment", name=name))


def _virtual(name):
    return encode(VirtualField(kind="virtual", name=name))


SCALAR_FIELD = _static("Category")
ARRAY_FIELD = _static("Hostname")
JSONB_ARRAY_FIELD = _core("Private IP")
VIRTUAL_EXPR_FIELD = _virtual("asset_label")


# ===========================================================================
# #1406 — the count query must apply the same search predicate as the
# values query, or total_distinct is wrong whenever search is set
# ===========================================================================

class TestCountQueryHonoursSearch:
    def test_scalar_count_applies_search(self):
        repo, issued = _make_repo(rows=[{"value": "Server", "count": 5}], total=1)
        _, total = repo.get_field_values(SCALAR_FIELD, search="srv")

        values_sql, values_params = issued["values"][0]
        count_sql, count_params = issued["count"][0]
        assert "ILIKE %(val_search)s" in values_sql
        assert "ILIKE %(val_search)s" in count_sql
        assert values_params["val_search"] == "srv%"
        assert count_params["val_search"] == "srv%"
        assert total == 1

    def test_native_array_count_applies_search(self):
        repo, issued = _make_repo(rows=[{"value": "srv-01", "count": 3}], total=1)
        repo.get_field_values(ARRAY_FIELD, search="srv")

        assert "x ILIKE %(val_search)s" in issued["values"][0][0]
        assert "x ILIKE %(val_search)s" in issued["count"][0][0]
        assert issued["count"][0][1]["val_search"] == "srv%"

    def test_jsonb_array_count_applies_search(self):
        repo, issued = _make_repo(rows=[{"value": "10.1.2.3", "count": 2}], total=1)
        repo.get_field_values(JSONB_ARRAY_FIELD, search="10.")

        assert "x.value ILIKE %(val_search)s" in issued["values"][0][0]
        assert "x.value ILIKE %(val_search)s" in issued["count"][0][0]
        assert issued["count"][0][1]["val_search"] == "10.%"

    def test_search_term_is_bound_never_interpolated(self):
        repo, issued = _make_repo()
        repo.get_field_values(SCALAR_FIELD, search="srv'; DROP TABLE assets;--")
        for sql, params in issued["values"] + issued["count"]:
            assert "srv'" not in sql
            assert params["val_search"] == "srv'; DROP TABLE assets;--%"


# ===========================================================================
# #1406 — include_total=False skips the count query entirely
# ===========================================================================

class TestIncludeTotal:
    @pytest.mark.parametrize("field", [SCALAR_FIELD, ARRAY_FIELD, JSONB_ARRAY_FIELD])
    def test_include_total_false_issues_only_the_values_query(self, field):
        repo, issued = _make_repo(rows=[{"value": "Server", "count": 5}])
        values, total = repo.get_field_values(field, include_total=False)

        assert len(issued["values"]) == 1
        assert issued["count"] == []
        assert total is None
        assert values == [{"value": "Server", "count": 5}]

    def test_include_total_default_still_counts(self):
        # Default path is unchanged for existing callers (MCP, chat, UI).
        repo, issued = _make_repo(rows=[{"value": "Server", "count": 5}], total=7)
        values, total = repo.get_field_values(SCALAR_FIELD)

        assert len(issued["values"]) == 1
        assert len(issued["count"]) == 1
        assert total == 7
        assert values == [{"value": "Server", "count": 5}]


def _make_repo_with_union(rows=None, total=0):
    """Same as _make_repo, but the catalog also mints a unified `union` field
    (virtual:pula over static:Hostname + static:FQDN) — the virtual_array
    kind, which routes to the native-array branch with a large generated
    ARRAY(...) expression."""
    from src.modules.fields.types import FieldDataType

    db = MagicMock()
    issued = {"values": [], "count": []}

    def _dispatch(sql, params=None):
        if "COUNT(DISTINCT" in sql:
            issued["count"].append((sql, params))
            return [{"total": total}]
        if "COUNT(*) AS count" in sql:
            issued["values"].append((sql, params))
            return list(rows or [])
        return []

    db.execute_query.side_effect = _dispatch
    catalog = FieldCatalog(db_adapter=db, unified_fields_provider=lambda: [{
        "slug": "pula",
        "name": "PULA",
        "function": "union",
        "sources": ["static:Hostname", "static:FQDN"],
        "data_type": FieldDataType.STRING_ARRAY,
    }])
    resolver = FieldResolver(catalog=catalog)
    return AssetRepository(db, resolver=resolver), issued


# ===========================================================================
# #1412 — the resolved expression is evaluated once per row
# ===========================================================================

class TestSingleEvaluation:
    def test_virtual_expr_appears_once_per_query_behind_fence(self):
        # A virtual_expr COALESCE is expensive; it must appear textually once
        # in the values query and once in the count, inside an OFFSET 0
        # fenced subquery (a plain subquery would be flattened by pull-up).
        repo, issued = _make_repo()
        repo.get_field_values(VIRTUAL_EXPR_FIELD, search="a")

        expr = "COALESCE(NULLIF(asset_label_override, '')"
        values_sql, count_sql = issued["values"][0][0], issued["count"][0][0]
        assert values_sql.count(expr) == 1
        assert count_sql.count(expr) == 1
        assert values_sql.count("OFFSET 0") == 1
        assert count_sql.count("OFFSET 0") == 1

    def test_column_field_keeps_flat_form(self):
        # Bare columns repeat for free and keep index scans; no fence.
        repo, issued = _make_repo()
        repo.get_field_values(SCALAR_FIELD, search="srv")

        values_sql, count_sql = issued["values"][0][0], issued["count"][0][0]
        assert "OFFSET 0" not in values_sql
        assert "OFFSET 0" not in count_sql
        assert "FROM assets" in values_sql
        assert values_sql.count("category") > 1  # repeated bare column is fine

    def test_jsonb_array_drops_redundant_null_guard(self):
        # jsonb_array_elements_text is STRICT; the old AND <expr> IS NOT NULL
        # was a second evaluation of col_expr. No empty-string filter is added.
        repo, issued = _make_repo()
        repo.get_field_values(JSONB_ARRAY_FIELD, search="10.")

        expr = "extended_attributes->'AssetAtlas'->'ip_private'"
        values_sql, count_sql = issued["values"][0][0], issued["count"][0][0]
        assert values_sql.count(expr) == 1
        assert count_sql.count(expr) == 1
        assert "IS NOT NULL" not in values_sql
        assert "!= ''" not in values_sql

    def test_union_field_expression_appears_once(self):
        repo, issued = _make_repo_with_union()
        repo.get_field_values(_virtual("pula"), search="a")

        expr = "ARRAY(SELECT DISTINCT v FROM unnest("
        values_sql, count_sql = issued["values"][0][0], issued["count"][0][0]
        assert values_sql.count(expr) == 1
        assert count_sql.count(expr) == 1

    def test_alias_keeps_native_type(self):
        # SELECT <expr>::text AS v would reorder numeric/timestamp values and
        # re-render booleans; the cast belongs only inside the ILIKE predicate.
        repo, issued = _make_repo()
        repo.get_field_values(VIRTUAL_EXPR_FIELD, search="a")

        values_sql = issued["values"][0][0]
        assert "::text AS v" not in values_sql
        assert "v::text ILIKE %(val_search)s" in values_sql

    def test_contextual_filters_stay_inside_the_fence(self):
        # base_where references asset columns, which the projection hides —
        # it must live inside the subquery, not in the outer WHERE.
        repo, issued = _make_repo()
        repo.get_field_values(
            VIRTUAL_EXPR_FIELD,
            filters=[{
                "field_id": {"kind": "static", "name": "Category"},
                "op": "eq", "value": "Server",
            }],
        )

        for sql, _ in issued["values"] + issued["count"]:
            assert "OFFSET 0" in sql  # without the fence the split below is vacuous
            inner = sql.split("OFFSET 0")[0]
            assert "LOWER(category) = LOWER(%(f0_0)s)" in inner

    @pytest.mark.parametrize("field", [
        SCALAR_FIELD, ARRAY_FIELD, JSONB_ARRAY_FIELD, VIRTUAL_EXPR_FIELD,
    ])
    def test_where_1x1_is_gone(self, field):
        repo, issued = _make_repo()
        repo.get_field_values(field, search="a")
        for sql, _ in issued["values"] + issued["count"]:
            assert "1=1" not in sql

    def test_order_by_tiebreak_preserved(self):
        # ORDER BY count DESC, <value> ASC must survive in every branch —
        # dropping the tie-break makes pagination non-deterministic.
        expectations = [
            (SCALAR_FIELD, "ORDER BY count DESC, category ASC"),
            (ARRAY_FIELD, "ORDER BY count DESC, x ASC"),
            (JSONB_ARRAY_FIELD, "ORDER BY count DESC, x.value ASC"),
            (VIRTUAL_EXPR_FIELD, "ORDER BY count DESC, v ASC"),
        ]
        for field, order_by in expectations:
            repo, issued = _make_repo()
            repo.get_field_values(field)
            assert order_by in issued["values"][0][0]

    def test_native_array_templates_collapsed(self):
        # One template now serves both the search and no-search case (the old
        # code carried three near-identical copies to flip AND/WHERE).
        repo, issued = _make_repo()
        repo.get_field_values(ARRAY_FIELD)
        sql_no_search = issued["values"][0][0]

        repo, issued = _make_repo()
        repo.get_field_values(ARRAY_FIELD, search="a")
        sql_search = issued["values"][0][0]

        assert "WHERE x IS NOT NULL AND x != ''" in sql_no_search
        assert "x IS NOT NULL" in sql_search
        assert "x != ''" in sql_search
        assert "x ILIKE %(val_search)s" in sql_search
