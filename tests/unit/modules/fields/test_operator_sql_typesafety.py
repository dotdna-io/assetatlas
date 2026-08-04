# SPDX-License-Identifier: AGPL-3.0-or-later
"""DB-free structural guardrail: generated SQL must be type-safe per column type.

The pre-fix registry emitted `LOWER(<numeric>)` and `<numeric> = ''` against real
numeric/date/boolean columns (Postgres type errors), and bare `NOT EXISTS` over a
NULL array for negation ops (wrongly included missing fields). This matrix test
walks every (data_type × operator) the dropdown can produce and asserts the
fragment is structurally safe — without needing a live DB. It is the unit-level
companion to the integration SQL↔match equivalence harness (which needs a DB).
"""

from src.modules.fields.operators import OPERATOR_REGISTRY, OperatorCtx, operators_for
from src.modules.fields.types import FieldDataType


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


def _value_for(op):
    if op.arity == "none":
        return None
    if op.arity == "list":
        return ["a", "b"]
    return "x"


NON_TEXT = (FieldDataType.INTEGER, FieldDataType.FLOAT, FieldDataType.DATE,
            FieldDataType.TIMESTAMP, FieldDataType.BOOLEAN)
NEGATION_ARRAY_OPS = ("neq", "ncontains", "none")


def test_no_lower_or_blank_compare_on_non_text_columns():
    """eq/empty/etc. must not LOWER() or `= ''` a numeric/date/boolean column."""
    for dt in NON_TEXT:
        for op in operators_for(dt, is_array=False):
            ctx = _ctx_for(dt, sql_kind="column")
            frag, _ = op.build_sql("c", _value_for(op), ctx)
            if not frag:
                continue
            assert "LOWER(c)" not in frag, f"{dt.value}/{op.code}: LOWER() on a non-text column -> type error"
            assert "c = ''" not in frag and "c != ''" not in frag, (
                f"{dt.value}/{op.code}: compares a non-text column to '' -> type error"
            )


def test_array_negation_ops_guard_against_null():
    """neq/ncontains/none on an array must exclude a NULL array (IS NOT NULL guard)."""
    for sql_kind, dt in (("column", FieldDataType.STRING_ARRAY),
                         ("jsonb_path", FieldDataType.JSONB_ARRAY)):
        for code in NEGATION_ARRAY_OPS:
            op = OPERATOR_REGISTRY[code]
            ctx = _ctx_for(dt, sql_kind=sql_kind)
            frag, _ = op.build_sql("c", _value_for(op), ctx)
            if not frag:
                continue
            assert "NOT EXISTS" in frag, f"{dt.value}/{code}: expected NOT EXISTS form"
            assert "c IS NOT NULL" in frag, (
                f"{dt.value}/{code}: missing IS NOT NULL guard -> NULL array wrongly matches"
            )


def test_scalar_none_guards_against_null():
    op = OPERATOR_REGISTRY["none"]
    ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
    frag, _ = op.build_sql("c", ["a", "b"], ctx)
    assert "c IS NOT NULL" in frag and "c IS NULL" not in frag


def test_like_family_is_literal_with_escape():
    """contains/ncontains/starts/ends must escape metacharacters + declare ESCAPE."""
    for code in ("contains", "ncontains", "starts", "ends"):
        op = OPERATOR_REGISTRY[code]
        ctx = _ctx_for(FieldDataType.STRING, sql_kind="column")
        frag, _ = op.build_sql("c", "a_b%c", ctx)
        assert "ILIKE" in frag and "ESCAPE" in frag, f"{code}: missing ESCAPE clause"
        # the bound pattern must have escaped both metacharacters
        assert ctx.params["f0_0"].count("\\_") == 1 and ctx.params["f0_0"].count("\\%") == 1, (
            f"{code}: metacharacters not escaped in pattern {ctx.params['f0_0']!r}"
        )
