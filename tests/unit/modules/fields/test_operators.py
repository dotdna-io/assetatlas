# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry units for src.modules.fields.operators."""

import pytest
from pydantic import ValidationError

from src.api.models.requests import AssetFilter
from src.modules.fields.operators import (
    Operator,
    OperatorCtx,
    OPERATOR_REGISTRY,
    OperatorResolutionError,
    resolve,
    operators_for,
)
from src.modules.fields.types import FieldDataType, FilterOperator


# The canonical codes Phase 1 ships (between/within_last are Phase 3).
PHASE1_CODES = {
    "eq", "neq", "contains", "ncontains", "starts", "ends",
    "gt", "gte", "lt", "lte", "any", "all", "none",
    "empty", "notempty", "is_true", "is_false",
}


def test_registry_keyed_by_code():
    for code, op in OPERATOR_REGISTRY.items():
        assert isinstance(op, Operator)
        assert op.code == code


def test_phase1_codes_present():
    assert PHASE1_CODES.issubset(set(OPERATOR_REGISTRY))


def test_every_operator_has_full_metadata():
    for op in OPERATOR_REGISTRY.values():
        assert op.scalar_label and isinstance(op.scalar_label, str)
        assert op.array_label and isinstance(op.array_label, str)
        assert op.arity in ("none", "one", "list")
        assert op.value_type in ("text", "number", "date", "bool")
        assert isinstance(op.aliases, list)
        # applies_to is an ordered list of FieldDataType (may be empty for is_true/is_false)
        assert isinstance(op.applies_to, list)
        for dt in op.applies_to:
            assert isinstance(dt, FieldDataType)


def test_default_operator_per_type_is_eq():
    # operators_for returns ordered list; element 0 is the seeded default.
    for dt in (FieldDataType.STRING, FieldDataType.STRING_ARRAY,
               FieldDataType.INTEGER, FieldDataType.FLOAT,
               FieldDataType.DATE, FieldDataType.TIMESTAMP,
               FieldDataType.JSONB_ARRAY):
        ordered = operators_for(dt, is_array=(dt in (FieldDataType.STRING_ARRAY,
                                                     FieldDataType.JSONB_ARRAY)))
        assert ordered[0].code == "eq"


def test_string_order_matches_legacy():
    # Legacy STRING order plus `none`: STRING gains `none` over the pure legacy
    # list (set-exclusion is valid on scalar text too, per §5.2).
    codes = [op.code for op in operators_for(FieldDataType.STRING, is_array=False)]
    assert codes == ["eq", "neq", "contains", "ncontains", "starts", "ends",
                     "any", "none", "empty", "notempty"]


def test_string_array_order_matches_legacy_plus_all_none():
    # STRING_ARRAY gains all/none after any (set-oriented additions).
    codes = [op.code for op in operators_for(FieldDataType.STRING_ARRAY, is_array=True)]
    assert codes[:7] == ["eq", "neq", "contains", "ncontains", "starts", "ends", "any"]
    assert "all" in codes and "none" in codes
    assert codes[-2:] == ["empty", "notempty"]


def test_boolean_order_matches_legacy():
    codes = [op.code for op in operators_for(FieldDataType.BOOLEAN, is_array=False)]
    assert codes == ["eq", "empty", "notempty"]


def test_number_order_matches_legacy():
    # Phase 3 inserts `between` after lte (spec §5.2), before empty/notempty.
    codes = [op.code for op in operators_for(FieldDataType.INTEGER, is_array=False)]
    assert codes == ["eq", "neq", "gt", "lt", "gte", "lte", "between", "empty", "notempty"]


class TestResolver:
    def test_codes_are_fixed_points(self):
        for code in OPERATOR_REGISTRY:
            assert resolve(code).code == code

    def test_spaced_labels_resolve(self):
        assert resolve("equals").code == "eq"
        assert resolve("not equals").code == "neq"
        assert resolve("any of").code == "any"
        assert resolve("is empty").code == "empty"
        assert resolve("is not empty").code == "notempty"

    def test_underscored_labels_resolve(self):
        assert resolve("not_equals").code == "neq"
        assert resolve("is_empty").code == "empty"
        assert resolve("is_not_empty").code == "notempty"
        assert resolve("starts_with").code == "starts"

    def test_empty_symbols_resolve(self):
        # The Explorer search bar inserts the operator SYMBOL, so EMPTY / !EMPTY
        # must resolve like every other operator's symbol does. '!empty'
        # (notempty's symbol, lowercased) was missing from its aliases while
        # 'empty' was present, so picking "is not empty" produced an unresolvable
        # token. EMPTY worked only because it lowercases to the 'empty' alias.
        assert resolve("EMPTY").code == "empty"
        assert resolve("!EMPTY").code == "notempty"
        assert resolve("!empty").code == "notempty"

    def test_legacy_in_collisions(self):
        # §9: 'in' -> any; 'in array' -> eq; 'not in array' -> neq
        assert resolve("in").code == "any"
        assert resolve("in array").code == "eq"
        assert resolve("in_array").code == "eq"
        assert resolve("not in array").code == "neq"
        assert resolve("not_in_array").code == "neq"

    def test_array_label_is_not_an_alias(self):
        # 'has' is contains' alias, NOT eq's array_label folded in.
        assert resolve("has").code == "contains"

    def test_unknown_raises(self):
        with pytest.raises(OperatorResolutionError):
            resolve("no_such_op")
        with pytest.raises(OperatorResolutionError):
            resolve(None)

    def test_superset_of_all_live_matcher_literals(self):
        # Every literal operator string the four current engines accept must
        # resolve. If this fails, a persisted/wire spelling would misfire.
        live_literals = {
            # FilterOperator enum .value (compiler + query_engine, spaced)
            "equals", "not equals", "contains", "not contains",
            "starts with", "ends with", "greater than", "less than",
            "greater than or equal", "less than or equal",
            "any of", "is empty", "is not empty",
            # asset_query_compiler array branch extras
            "in array", "not in array", "in",
            # Explorer short codes (AssetFilter)
            "eq", "neq", "gt", "gte", "lt", "lte", "contains", "ncontains",
            "starts", "ends", "empty", "notempty", "any",
            # data_filters underscored
            "is_empty", "is_not_empty", "is_true", "is_false",
            "not_equals", "not_contains", "starts_with", "ends_with",
            "greater_than", "less_than",
        }
        for lit in live_literals:
            resolve(lit)  # raises if any is unmapped


class _FakeResolved:
    """Minimal ResolvedField stand-in for build_sql routing."""
    def __init__(self, data_type, sql_kind):
        self.data_type = data_type
        self.sql_kind = sql_kind

    def is_array(self):
        return self.data_type in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)

    def is_native_array(self):
        return self.is_array() and self.sql_kind == "column"

    def is_jsonb_sourced(self):
        return self.sql_kind in ("jsonb_path", "jsonb_coalesce", "tag_boolean", "posture_jsonb")


def _ctx_for(data_type, sql_kind="column", col="col"):
    return OperatorCtx(field=_FakeResolved(data_type, sql_kind), param_key="f0", params={})


def _normalized_value(op, dt):
    if op.arity == "none":
        return None
    if op.arity == "list":
        return ["A", "B"]
    if op.value_type == "number":
        return 5
    return "x"


class TestBuildSqlCoverage:
    """Every operator in every type's applies_to yields non-empty SQL, never raises.

    Runs both the native-array (column) and jsonb-array (jsonb_path) shapes so
    re-pointing the compiler cannot turn a JSONB_ARRAY op into a runtime error.
    """

    def test_every_type_every_op_non_empty(self):
        for dt in (FieldDataType.STRING, FieldDataType.STRING_ARRAY,
                   FieldDataType.JSONB_ARRAY, FieldDataType.INTEGER,
                   FieldDataType.FLOAT, FieldDataType.DATE,
                   FieldDataType.TIMESTAMP, FieldDataType.BOOLEAN):
            is_arr = dt in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)
            sql_kind = "column" if dt != FieldDataType.JSONB_ARRAY else "jsonb_path"
            if dt == FieldDataType.BOOLEAN:
                sql_kind = "tag_boolean"
            for op in operators_for(dt, is_array=is_arr):
                ctx = _ctx_for(dt, sql_kind=sql_kind)
                frag, params = op.build_sql("col", _normalized_value(op, dt), ctx)
                assert frag and isinstance(frag, str), f"{dt}/{op.code} empty fragment"

    def test_empty_value_list_drops_clause(self):
        # any/all/none with empty list -> ('', {}) so caller drops it (no IN ()).
        for code in ("any", "all", "none"):
            op = OPERATOR_REGISTRY[code]
            ctx = _ctx_for(FieldDataType.STRING_ARRAY, sql_kind="column")
            frag, params = op.build_sql("data_source", [], ctx)
            assert frag == ""

    def test_eq_scalar_is_exact_lower(self):
        op = OPERATOR_REGISTRY["eq"]
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, params = op.build_sql("category", "Server", ctx)
        assert "LOWER(category) = LOWER(" in frag
        assert "ILIKE" not in frag

    def test_eq_native_array_uses_unnest_exact(self):
        op = OPERATOR_REGISTRY["eq"]
        ctx = _ctx_for(FieldDataType.STRING_ARRAY, sql_kind="column")
        frag, params = op.build_sql("data_source", "Jamf", ctx)
        assert "unnest(data_source)" in frag
        assert "LOWER(x) = LOWER(" in frag

    def test_eq_jsonb_array_uses_elements_text_exact(self):
        op = OPERATOR_REGISTRY["eq"]
        ctx = _ctx_for(FieldDataType.JSONB_ARRAY, sql_kind="jsonb_path")
        frag, params = op.build_sql("extended_attributes->'AssetAtlas'->'ip_private'", "10.0.0.1", ctx)
        assert "jsonb_array_elements_text" in frag
        assert "LOWER(x.value) = LOWER(" in frag

    def test_all_native_array_is_and_of_exists(self):
        op = OPERATOR_REGISTRY["all"]
        ctx = _ctx_for(FieldDataType.STRING_ARRAY, sql_kind="column")
        frag, params = op.build_sql("data_source", ["jamf", "sccm"], ctx)
        # AND-of-EXISTS (each value must be present), case-insensitive.
        assert frag.count("EXISTS") == 2
        assert " AND " in frag
        assert "LOWER" in frag

    def test_none_native_array_is_not_exists_in(self):
        op = OPERATOR_REGISTRY["none"]
        ctx = _ctx_for(FieldDataType.STRING_ARRAY, sql_kind="column")
        frag, params = op.build_sql("data_source", ["jamf", "sccm"], ctx)
        # A NULL array is excluded (decision 2026-06-04), so the NOT EXISTS is
        # guarded by IS NOT NULL.
        assert "data_source IS NOT NULL" in frag
        assert "NOT EXISTS" in frag
        assert "IN (" in frag

    def test_none_scalar_excludes_null(self):
        # scalar none-of must EXCLUDE NULL rows: a missing value is treated as
        # "field absent". The in-memory matcher's NULL guard already returns
        # False for None, and the SQL adds `IS NOT NULL` so both engines agree
        # (decision 2026-06-04: a missing field does not satisfy negation ops).
        op = OPERATOR_REGISTRY["none"]
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, params = op.build_sql("category", ["server", "workstation"], ctx)
        assert "category IS NOT NULL" in frag
        assert "NOT IN (" in frag

    def test_contains_value_metachars_are_literal(self):
        # contains is a LITERAL substring match: %/_ in the value are escaped so
        # they match themselves, not act as ILIKE wildcards (decision 2026-06-04:
        # literal everywhere; the in-memory matcher is already literal). The value
        # is still a bound param, with an explicit ESCAPE clause.
        op = OPERATOR_REGISTRY["contains"]
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, _ = op.build_sql("hostname", "web%", ctx)
        assert "ILIKE" in frag
        assert "ESCAPE" in frag
        assert ctx.params["f0_0"] == "%web\\%%"  # user '%' escaped to a literal

    def test_empty_scalar_is_null_or_blank(self):
        op = OPERATOR_REGISTRY["empty"]
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, params = op.build_sql("owner", None, ctx)
        assert "IS NULL" in frag and "= ''" in frag

    def test_boolean_eq_truthy_is_not_null(self):
        op = OPERATOR_REGISTRY["eq"]
        ctx = _ctx_for(FieldDataType.BOOLEAN, sql_kind="tag_boolean")
        frag_t, _ = op.build_sql("tags->>'Prod'", "true", ctx)
        assert frag_t == "tags->>'Prod' IS NOT NULL"
        ctx2 = _ctx_for(FieldDataType.BOOLEAN, sql_kind="tag_boolean")
        frag_f, _ = op.build_sql("tags->>'Prod'", "false", ctx2)
        assert frag_f == "tags->>'Prod' IS NULL"


class TestMatch:
    def test_eq_scalar_case_insensitive(self):
        assert OPERATOR_REGISTRY["eq"].match("Windows", "windows", None) is True
        assert OPERATOR_REGISTRY["eq"].match("Win_NT", "WinXNT", None) is False

    def test_eq_array_case_insensitive(self):
        assert OPERATOR_REGISTRY["eq"].match(["Jamf", "SCCM"], "jamf", None) is True
        assert OPERATOR_REGISTRY["eq"].match(["SCCM"], "jamf", None) is False

    def test_any_scalar_and_array(self):
        assert OPERATOR_REGISTRY["any"].match("jamf", ["jamf", "sccm"], None) is True
        assert OPERATOR_REGISTRY["any"].match(["sccm"], ["jamf", "sccm"], None) is True
        assert OPERATOR_REGISTRY["any"].match(["ad"], ["jamf", "sccm"], None) is False

    def test_all_array(self):
        assert OPERATOR_REGISTRY["all"].match(["jamf", "sccm", "ad"], ["jamf", "sccm"], None) is True
        assert OPERATOR_REGISTRY["all"].match(["jamf"], ["jamf", "sccm"], None) is False

    def test_none_array(self):
        assert OPERATOR_REGISTRY["none"].match(["sccm"], ["jamf", "sccm"], None) is False
        assert OPERATOR_REGISTRY["none"].match(["ad"], ["jamf", "sccm"], None) is True

    def test_gte_lte_scalar_numeric(self):
        # §6.1: in-memory tag gained gte/lte (was silently False).
        assert OPERATOR_REGISTRY["gte"].match("0.9", "0.8", None) is True
        assert OPERATOR_REGISTRY["lte"].match("0.5", "0.8", None) is True

    def test_starts_ends_array(self):
        assert OPERATOR_REGISTRY["starts"].match(["server-01"], "server", None) is True
        assert OPERATOR_REGISTRY["ends"].match(["a@b.com"], "@b.com", None) is True

    def test_empty_scalar_blank_counts(self):
        # §6.1: scalar '' counts as empty everywhere.
        assert OPERATOR_REGISTRY["empty"].match("", None, None) is True
        assert OPERATOR_REGISTRY["empty"].match(None, None, None) is True
        assert OPERATOR_REGISTRY["empty"].match("x", None, None) is False
        assert OPERATOR_REGISTRY["empty"].match([], None, None) is True

    def test_is_true_is_false_coercion(self):
        # Reproduces data_filters.py:42-53 exactly.
        assert OPERATOR_REGISTRY["is_true"].match(True, None, None) is True
        assert OPERATOR_REGISTRY["is_true"].match("true", None, None) is True
        assert OPERATOR_REGISTRY["is_true"].match("nope", None, None) is False
        assert OPERATOR_REGISTRY["is_false"].match(False, None, None) is True
        assert OPERATOR_REGISTRY["is_false"].match("false", None, None) is True

    def test_null_asset_value_never_spuriously_matches(self):
        # OP-1 regression: a missing/NULL field must NOT stringify to 'None' and
        # satisfy negation/text operators. Both engines now agree that a missing
        # field is EXCLUDED from negation ops: in-memory via this guard, and SQL
        # via the `IS NOT NULL` guard on none/neq/ncontains (decision 2026-06-04).
        # A record missing the field stays excluded.
        for code, fv in [
            ("eq", "none"), ("neq", "jamf"),
            ("contains", "none"), ("ncontains", "jamf"),
            ("starts", "n"), ("ends", "e"),
            ("any", ["none"]), ("all", ["none"]), ("none", ["jamf"]),
            ("gt", "0"), ("lt", "9"),
        ]:
            assert OPERATOR_REGISTRY[code].match(None, fv, None) is False, code
        # Only the null-aware operators define their own None behavior.
        assert OPERATOR_REGISTRY["empty"].match(None, None, None) is True
        assert OPERATOR_REGISTRY["notempty"].match(None, None, None) is False
        assert OPERATOR_REGISTRY["is_true"].match(None, None, None) is False
        assert OPERATOR_REGISTRY["is_false"].match(None, None, None) is True


class TestFilterOperatorParsesCodes:
    def test_codes_parse_to_enum(self):
        assert FilterOperator("eq") is FilterOperator.EQUALS
        assert FilterOperator("neq") is FilterOperator.NOT_EQUALS
        assert FilterOperator("any") is FilterOperator.IN
        assert FilterOperator("empty") is FilterOperator.IS_NULL
        assert FilterOperator("notempty") is FilterOperator.IS_NOT_NULL
        assert FilterOperator("starts") is FilterOperator.STARTS_WITH
        assert FilterOperator("ncontains") is FilterOperator.NOT_CONTAINS

    def test_legacy_spellings_still_parse(self):
        assert FilterOperator("is_empty") is FilterOperator.IS_NULL
        assert FilterOperator("in") is FilterOperator.IN
        assert FilterOperator("equals") is FilterOperator.EQUALS

    def test_value_stays_spaced_label(self):
        assert FilterOperator.EQUALS.value == "equals"
        assert FilterOperator("eq").value == "equals"

    def test_array_and_boolean_codes_parse_to_enum(self):
        # Phase 2 adds FilterOperator members for all/none/is_true/is_false so
        # the tag/posture write path accepts every code operator_specs emits
        # (FilterRow now surfaces "has all of"/"has none of").
        assert FilterOperator("all") is FilterOperator.ALL_OF
        assert FilterOperator("none") is FilterOperator.NONE_OF
        assert FilterOperator("is_true") is FilterOperator.IS_TRUE
        assert FilterOperator("is_false") is FilterOperator.IS_FALSE


class TestAssetFilterValidator:
    """AssetFilter.validate_operator accepts every registry code (incl. the new
    all/none) and rejects unknown spellings. The old validator hardcoded a set
    that omitted all/none, making them phantoms that failed on submit (spec §1.3)."""

    def test_all_none_now_valid(self):
        f = AssetFilter(field_id={"kind": "static", "name": "Data Source"},
                        op="none", value=["jamf"])
        assert f.op == "none"
        g = AssetFilter(field_id={"kind": "static", "name": "Data Source"},
                        op="all", value=["jamf", "sccm"])
        assert g.op == "all"

    def test_unknown_op_rejected(self):
        with pytest.raises(ValidationError):
            AssetFilter(field_id={"kind": "static", "name": "Category"},
                        op="no_such_op", value="x")
