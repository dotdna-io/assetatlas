# SPDX-License-Identifier: AGPL-3.0-or-later
"""Post-unification correctness fixes for src.modules.fields.operators.

Encodes the behaviour decided 2026-06-04 after the multi-agent review of the
operator-unification PRs (#692/#693/#696/#697):

  A-1  gt/lt/gte/lte compare dates chronologically in-memory (were float-only -> False).
  A-2  contains/ncontains/starts/ends are LITERAL everywhere (escape %/_ in SQL).
  A-3  eq/neq normalise numerically/chronologically in-memory, keyed on the
       asset value's runtime type (so a TIMESTAMP/numeric column agrees with SQL
       while a text field stays string-exact).
  A-4  none/neq/ncontains EXCLUDE a NULL field in SQL (align to the in-memory guard).
  B-1  empty/notempty emit `IS NULL`/`IS NOT NULL` (no `= ''`) on numeric/date/bool.
  B-2  eq/neq do not wrap a non-text column in LOWER() (no `lower(numeric|date|boolean)`).
  B-3  is_true/is_false use IS TRUE/IS FALSE on a real boolean column (presence only
       for tag_boolean).

Each test asserts the corrected contract; they fail on the pre-fix registry.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from src.modules.fields.operators import OPERATOR_REGISTRY, OperatorCtx
from src.modules.fields.types import FieldDataType


# --- field/ctx stand-in (mirrors tests/unit/modules/fields/test_operators.py) ---
class _FakeResolved:
    def __init__(self, data_type, sql_kind):
        self.data_type = data_type
        self.sql_kind = sql_kind

    def is_array(self):
        return self.data_type in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)

    def is_native_array(self):
        return self.is_array() and self.sql_kind == "column"

    def is_jsonb_sourced(self):
        return self.sql_kind in ("jsonb_path", "jsonb_coalesce", "tag_boolean", "posture_jsonb")


def _ctx_for(data_type, sql_kind="column"):
    return OperatorCtx(field=_FakeResolved(data_type, sql_kind), param_key="f0", params={})


def _op(code):
    return OPERATOR_REGISTRY[code]


# ============================ A-1: date comparisons ============================
class TestDateComparisonsInMemory:
    def test_gte_iso_string_dates(self):
        assert _op("gte").match("2026-03-01", "2026-01-01", None) is True
        assert _op("gte").match("2025-12-31", "2026-01-01", None) is False

    def test_lt_date_objects(self):
        assert _op("lt").match(date(2025, 1, 1), date(2026, 1, 1), None) is True
        assert _op("lt").match(date(2026, 6, 1), date(2026, 1, 1), None) is False

    def test_gt_datetime_vs_date_string(self):
        assert _op("gt").match(datetime(2026, 3, 1), "2026-01-01", None) is True
        assert _op("lte").match("2025-12-31", "2026-01-01", None) is True

    def test_relative_duration_filter_value(self):
        # The filter value '30d' resolves to NOW()-30d on the in-memory side too.
        far_future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        far_past = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        assert _op("gte").match(far_future, "30d", None) is True
        assert _op("gte").match(far_past, "30d", None) is False

    def test_numeric_comparison_unaffected(self):
        assert _op("gte").match("0.9", "0.8", None) is True
        assert _op("lt").match(2, 3, None) is True


# ============================ A-2: literal contains ============================
class TestContainsLiteral:
    def test_sql_escapes_underscore_and_percent(self):
        ctx = _ctx_for(FieldDataType.STRING)
        frag, _ = _op("contains").build_sql("notes", "a_c", ctx)
        assert "ILIKE" in frag and "ESCAPE" in frag
        assert ctx.params["f0_0"] == "%a\\_c%"  # underscore escaped to a literal

    def test_sql_escapes_backslash(self):
        ctx = _ctx_for(FieldDataType.STRING)
        _op("contains").build_sql("path", "a\\b", ctx)
        assert ctx.params["f0_0"] == "%a\\\\b%"  # backslash doubled

    def test_inmemory_underscore_is_literal(self):
        # already literal in-memory; pinned so SQL is aligned to it
        assert _op("contains").match("aXc", "a_c", None) is False
        assert _op("contains").match("a_c", "a_c", None) is True

    def test_starts_ends_also_escape(self):
        ctx = _ctx_for(FieldDataType.STRING)
        frag_s, _ = _op("starts").build_sql("hostname", "web_", ctx)
        assert "ESCAPE" in frag_s
        assert ctx.params["f0_0"] == "web\\_%"


# ===================== A-3: type-aware eq/neq in-memory ======================
class TestEqNeqRuntimeTypeNormalisation:
    def test_timestamp_value_matches_date_only_string(self):
        assert _op("eq").match(datetime(2026, 1, 1, 0, 0, 0), "2026-01-01", None) is True
        assert _op("neq").match(datetime(2026, 1, 1, 0, 0, 0), "2026-01-01", None) is False

    def test_non_midnight_timestamp_not_equal_to_date(self):
        # mirrors SQL casting '2026-01-01' to midnight: 13:30 != midnight
        assert _op("eq").match(datetime(2026, 1, 1, 13, 30), "2026-01-01", None) is False

    def test_numeric_value_matches_numeric_string(self):
        assert _op("eq").match(5.0, "5", None) is True
        assert _op("eq").match(5, "5.0", None) is True
        assert _op("neq").match(5.0, "6", None) is True

    def test_text_field_stays_string_exact(self):
        # a TEXT value that merely looks numeric must NOT numeric-normalise
        assert _op("eq").match("007", "7", None) is False
        assert _op("eq").match("Windows", "windows", None) is True

    def test_bool_value_string_compares(self):
        assert _op("eq").match(False, "false", None) is True
        assert _op("eq").match(True, "false", None) is False


# ===================== A-4: negation excludes NULL in SQL =====================
class TestNegationExcludesNullInSql:
    def test_scalar_none_requires_not_null(self):
        ctx = _ctx_for(FieldDataType.STRING)
        frag, _ = _op("none").build_sql("category", ["server"], ctx)
        assert "category IS NOT NULL" in frag
        assert "NOT IN (" in frag

    def test_native_array_neq_requires_not_null(self):
        ctx = _ctx_for(FieldDataType.STRING_ARRAY)
        frag, _ = _op("neq").build_sql("data_source", "jamf", ctx)
        assert "IS NOT NULL" in frag and "NOT EXISTS" in frag

    def test_native_array_ncontains_requires_not_null(self):
        ctx = _ctx_for(FieldDataType.STRING_ARRAY)
        frag, _ = _op("ncontains").build_sql("data_source", "jamf", ctx)
        assert "IS NOT NULL" in frag

    def test_native_array_none_requires_not_null(self):
        ctx = _ctx_for(FieldDataType.STRING_ARRAY)
        frag, _ = _op("none").build_sql("data_source", ["jamf"], ctx)
        assert "IS NOT NULL" in frag

    def test_jsonb_array_none_requires_not_null(self):
        ctx = _ctx_for(FieldDataType.JSONB_ARRAY, sql_kind="jsonb_path")
        frag, _ = _op("none").build_sql("ea->'x'", ["jamf"], ctx)
        assert "IS NOT NULL" in frag

    def test_empty_array_still_matches_negation_in_memory(self):
        # only NULL is excluded; an empty (present) array still "has none of"
        assert _op("none").match([], ["jamf"], None) is True
        assert _op("neq").match([], "jamf", None) is True


# ============ J-3: list/range ops strip empty entries (engine parity) ========
class TestListEmptyEntryNormalisation:
    def test_bs_any_strips_empty_list_entries(self):
        ctx = _ctx_for(FieldDataType.STRING_ARRAY)
        _op("any").build_sql("data_source", ["jamf", "", "  ", "sccm"], ctx)
        assert len(ctx.params) == 2  # only jamf + sccm bound, empties dropped

    def test_bs_none_strips_empty_list_entries(self):
        ctx = _ctx_for(FieldDataType.STRING)
        _op("none").build_sql("category", ["a", ""], ctx)
        assert len(ctx.params) == 1

    def test_bs_between_with_empty_bound_drops_clause(self):
        ctx = _ctx_for(FieldDataType.INTEGER)
        frag, _ = _op("between").build_sql("ip_count", ["3", ""], ctx)
        assert frag == ""  # incomplete range -> dropped, never `BETWEEN 3 AND ''`

    def test_match_any_ignores_empty_filter_entries(self):
        assert _op("any").match("jamf", ["", "jamf"], None) is True
        assert _op("any").match("", ["", "jamf"], None) is False


# ================= B-1: empty/notempty type-safe on typed cols ================
class TestEmptyTypeSafe:
    @pytest.mark.parametrize("dt,col", [
        (FieldDataType.FLOAT, "confidence"),
        (FieldDataType.INTEGER, "ip_count"),
        (FieldDataType.DATE, "os_eol_date"),
        (FieldDataType.TIMESTAMP, "last_seen"),
        (FieldDataType.BOOLEAN, "os_is_lts"),
    ])
    def test_empty_non_text_is_null_only(self, dt, col):
        ctx = _ctx_for(dt, sql_kind="column")
        frag, _ = _op("empty").build_sql(col, None, ctx)
        assert frag == f"{col} IS NULL"
        assert "= ''" not in frag

    @pytest.mark.parametrize("dt,col", [
        (FieldDataType.FLOAT, "confidence"),
        (FieldDataType.DATE, "os_eol_date"),
        (FieldDataType.BOOLEAN, "os_is_lts"),
    ])
    def test_notempty_non_text_is_not_null_only(self, dt, col):
        ctx = _ctx_for(dt, sql_kind="column")
        frag, _ = _op("notempty").build_sql(col, None, ctx)
        assert frag == f"{col} IS NOT NULL"
        assert "!= ''" not in frag

    def test_empty_text_still_checks_blank(self):
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, _ = _op("empty").build_sql("category", None, ctx)
        assert "IS NULL" in frag and "= ''" in frag


# =============== B-2: eq/neq do not LOWER() a non-text column ================
class TestEqNeqNoLowerOnTypedColumns:
    @pytest.mark.parametrize("dt,col,val", [
        (FieldDataType.BOOLEAN, "os_is_lts", "true"),
        (FieldDataType.FLOAT, "confidence", "0.85"),
        (FieldDataType.INTEGER, "ip_count", "3"),
        (FieldDataType.DATE, "os_eol_date", "2026-01-01"),
        (FieldDataType.TIMESTAMP, "last_seen", "2026-01-01"),
    ])
    def test_eq_typed_column_no_lower(self, dt, col, val):
        ctx = _ctx_for(dt, sql_kind="column")
        frag, _ = _op("eq").build_sql(col, val, ctx)
        assert "LOWER" not in frag
        assert frag == f"{col} = %(f0_0)s"

    def test_neq_typed_column_no_lower(self):
        ctx = _ctx_for(FieldDataType.FLOAT, sql_kind="column")
        frag, _ = _op("neq").build_sql("confidence", "0.85", ctx)
        assert "LOWER" not in frag
        assert frag == "confidence != %(f0_0)s"

    def test_eq_text_column_still_lowers(self):
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, _ = _op("eq").build_sql("category", "Server", ctx)
        assert frag == "LOWER(category) = LOWER(%(f0_0)s)"


# ============ B-3: is_true/is_false type-aware on real boolean cols ==========
class TestIsTrueIsFalseTyped:
    def test_real_boolean_uses_is_true(self):
        ctx = _ctx_for(FieldDataType.BOOLEAN, sql_kind="column")
        frag, _ = _op("is_true").build_sql("os_is_lts", None, ctx)
        assert frag == "os_is_lts IS TRUE"

    def test_real_boolean_uses_is_false(self):
        # NULL counts as false (matches the in-memory matcher), else SQL and
        # match() diverge on a native boolean column with NULL rows.
        ctx = _ctx_for(FieldDataType.BOOLEAN, sql_kind="column")
        frag, _ = _op("is_false").build_sql("os_is_lts", None, ctx)
        assert frag == "(os_is_lts IS FALSE OR os_is_lts IS NULL)"

    def test_is_false_null_agrees_with_match(self):
        # Cross-engine: match() treats a NULL native boolean as is_false=True, so
        # the SQL must include NULL too (the 120-asset divergence found on real data).
        assert _op("is_false").match(None, None, None) is True
        assert _op("is_false").match(False, None, None) is True
        assert _op("is_false").match(True, None, None) is False

    def test_tag_boolean_stays_presence(self):
        ctx = _ctx_for(FieldDataType.BOOLEAN, sql_kind="tag_boolean")
        frag_t, _ = _op("is_true").build_sql("tags->>'Prod'", None, ctx)
        frag_f, _ = _op("is_false").build_sql("tags->>'Prod'", None, ctx)
        assert frag_t == "tags->>'Prod' IS NOT NULL"
        assert frag_f == "tags->>'Prod' IS NULL"

    def test_jsonb_sourced_boolean_uses_presence_not_is_true(self):
        # Review fix: a JSONB-sourced boolean column is text (ea->>'x'); `text IS
        # TRUE` is a Postgres error, so it must fall back to presence, not IS TRUE.
        ctx = _ctx_for(FieldDataType.BOOLEAN, sql_kind="jsonb_path")
        frag_t, _ = _op("is_true").build_sql("ea->>'flag'", None, ctx)
        frag_f, _ = _op("is_false").build_sql("ea->>'flag'", None, ctx)
        assert frag_t == "ea->>'flag' IS NOT NULL"
        assert frag_f == "ea->>'flag' IS NULL"
