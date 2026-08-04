# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator registry — the single source of truth for filter operators.

Replaces four divergent definitions: Explorer short codes
(AssetRepository._build_single_filter), Rules/Posture/Alert spaced labels
(FilterOperator enum + AssetQueryCompiler), integration underscored codes
(data_filters), and the frontend parser triplet. Every engine delegates to
one entry's build_sql (SQL builders) / match (in-memory evaluators).

No DB / Flask imports — this is a pure leaf module.
"""

from __future__ import annotations

import operator as _pyoperator
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from src.modules.fields.types import FieldDataType


class OperatorResolutionError(ValueError):
    """Raised by resolve() when a spelling maps to no registry operator."""


Arity = Literal["none", "one", "list"]
ValueType = Literal["text", "number", "date", "bool"]


# Operators that define their own behavior for a missing/NULL asset value; every
# other operator treats ``asset_value is None`` as "no match" (see Operator.match).
_NULL_AWARE_MATCH_CODES = frozenset({"empty", "notempty", "is_true", "is_false"})


@dataclass(frozen=True)
class Operator:
    code: str
    scalar_label: str
    array_label: str
    symbol: Optional[str]
    aliases: List[str]
    arity: Arity
    value_type: ValueType
    applies_to: List[FieldDataType]            # ORDERED — reproduces dropdown order
    _build_sql: Optional[Callable[[str, Any, Any], Tuple[str, Dict[str, Any]]]] = field(repr=False, default=None)
    _match: Optional[Callable[[Any, Any, Any], bool]] = field(repr=False, default=None)

    def build_sql(self, col: str, value: Any, ctx: Any) -> Tuple[str, Dict[str, Any]]:
        if self._build_sql is None:
            raise NotImplementedError(f"build_sql not wired for operator {self.code!r}")
        return self._build_sql(col, value, ctx)

    def match(self, asset_value: Any, filter_value: Any, ctx: Any) -> bool:
        if self._match is None:
            raise NotImplementedError(f"match not wired for operator {self.code!r}")
        if asset_value is None and self.code not in _NULL_AWARE_MATCH_CODES:
            # A missing/NULL field. Never let None reach the matchers, which would
            # str(None) -> 'none' and spuriously match text/negation ops
            # (contains/ncontains/starts/ends/neq/none/any/all). The in-memory
            # engines pass ctx=None and route on asset_value's runtime type, so we
            # cannot tell a NULL scalar from a NULL array here; mirror SQL's scalar
            # three-valued logic (and the engines' historical `value is None ->
            # False` guard): no match. empty/notempty/is_true/is_false define their
            # own None behavior and are excluded above.
            return False
        return self._match(asset_value, filter_value, ctx)


@dataclass
class OperatorCtx:
    """Carries resolver state + param-naming machinery into build_sql.

    `field` is a ResolvedField (or a stand-in exposing data_type, sql_kind,
    is_native_array(), is_jsonb_sourced()).
    """
    field: Any
    param_key: str
    params: Dict[str, Any]
    _counter: int = 0

    def next_param(self, value: Any) -> str:
        name = f"{self.param_key}_{self._counter}"
        self._counter += 1
        self.params[name] = value
        return name


# FieldDataType groups for applies_to construction (mirrors catalog.OPERATORS_BY_DATA_TYPE).
_TEXT_SCALAR = [FieldDataType.STRING]
_STR_ARRAYS = [FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY]
_NUMBERS = [FieldDataType.INTEGER, FieldDataType.FLOAT]
_DATES = [FieldDataType.DATE, FieldDataType.TIMESTAMP]


# Per-operator applies_to. ORDER inside each type's list is fixed by
# operators_for() iterating OPERATOR_REGISTRY in insertion order below, so the
# registry insertion order IS the canonical dropdown order. Legacy reference:
#   STRING:      eq neq contains ncontains starts ends any empty notempty
#   STRING_ARRAY/JSONB_ARRAY: + all none (after any, before empty)
#   INTEGER/FLOAT/DATE/TIMESTAMP: eq neq gt lt gte lte empty notempty
#   BOOLEAN:     eq empty notempty
#   JSONB:       contains ncontains empty notempty
_ALL_FILTERABLE = (
    _TEXT_SCALAR + _STR_ARRAYS + _NUMBERS + [FieldDataType.BOOLEAN] + _DATES
)

_OPERATORS: List[Operator] = [
    Operator(code="eq", scalar_label="equals", array_label="has", symbol="=",
             aliases=["=", "==", "is", "equals", "in array", "in_array"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS + _NUMBERS
                        + [FieldDataType.BOOLEAN] + _DATES + [FieldDataType.JSONB]),
    Operator(code="neq", scalar_label="not equals", array_label="doesn't have", symbol="!=",
             aliases=["!=", "<>", "ne", "not", "not equals", "not_equals",
                      "not in array", "not_in_array"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS + _NUMBERS + _DATES),
    Operator(code="contains", scalar_label="contains", array_label="contains text", symbol="~",
             aliases=["~", "*", "like", "has", "contains"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS + [FieldDataType.JSONB]),
    Operator(code="ncontains", scalar_label="not contains", array_label="doesn't contain", symbol="!~",
             aliases=["!~", "not contains", "not_contains"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS + [FieldDataType.JSONB]),
    Operator(code="starts", scalar_label="starts with", array_label="starts with", symbol="^",
             aliases=["^", "startswith", "starts", "starts with", "starts_with"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS),
    Operator(code="ends", scalar_label="ends with", array_label="ends with", symbol="$",
             aliases=["$", "endswith", "ends", "ends with", "ends_with"],
             arity="one", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS),
    Operator(code="gt", scalar_label="greater than", array_label="greater than", symbol=">",
             aliases=[">", "gt", "greater than", "greater_than"],
             arity="one", value_type="number",
             applies_to=_NUMBERS + _DATES),
    Operator(code="lt", scalar_label="less than", array_label="less than", symbol="<",
             aliases=["<", "lt", "less than", "less_than"],
             arity="one", value_type="number",
             applies_to=_NUMBERS + _DATES),
    Operator(code="gte", scalar_label="greater than or equal", array_label="greater than or equal", symbol=">=",
             aliases=[">=", "gte", "greater than or equal", "greater_than_or_equal"],
             arity="one", value_type="number",
             applies_to=_NUMBERS + _DATES),
    Operator(code="lte", scalar_label="less than or equal", array_label="less than or equal", symbol="<=",
             aliases=["<=", "lte", "less than or equal", "less_than_or_equal"],
             arity="one", value_type="number",
             applies_to=_NUMBERS + _DATES),
    Operator(code="between", scalar_label="between", array_label="between", symbol="BETWEEN",
             aliases=["between", "btw"],
             arity="list", value_type="number",
             applies_to=_NUMBERS + _DATES),
    Operator(code="within_last", scalar_label="in the last", array_label="in the last", symbol="WITHIN",
             aliases=["within", "within_last", "within last", "in the last", "withinlast"],
             arity="one", value_type="date",
             applies_to=_DATES),
    Operator(code="any", scalar_label="any of", array_label="has any of", symbol="IN",
             aliases=["in", "any", "oneof", "any of", "any_of"],
             arity="list", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS),
    Operator(code="all", scalar_label="all of", array_label="has all of", symbol="ALL",
             aliases=["all", "every", "all of", "all_of"],
             arity="list", value_type="text",
             applies_to=_STR_ARRAYS),
    Operator(code="none", scalar_label="none of", array_label="has none of", symbol="NONE",
             aliases=["none", "without", "none of", "none_of"],
             arity="list", value_type="text",
             applies_to=_TEXT_SCALAR + _STR_ARRAYS),
    Operator(code="empty", scalar_label="is empty", array_label="is empty", symbol="EMPTY",
             aliases=["empty", "isempty", "is empty", "is_empty", "is null", "is_null",
                      "null", "blank"],
             arity="none", value_type="text",
             applies_to=_ALL_FILTERABLE + [FieldDataType.JSONB]),
    Operator(code="notempty", scalar_label="is not empty", array_label="is not empty", symbol="!EMPTY",
             aliases=["!empty", "notempty", "isnotempty", "is not empty", "is_not_empty",
                      "is not null", "is_not_null", "notnull", "exists"],
             arity="none", value_type="text",
             applies_to=_ALL_FILTERABLE + [FieldDataType.JSONB]),
    Operator(code="is_true", scalar_label="is true", array_label="is true", symbol="TRUE",
             aliases=["is_true", "is true"],
             arity="none", value_type="bool",
             applies_to=[FieldDataType.BOOLEAN]),
    Operator(code="is_false", scalar_label="is false", array_label="is false", symbol="FALSE",
             aliases=["is_false", "is false"],
             arity="none", value_type="bool",
             applies_to=[FieldDataType.BOOLEAN]),
]

OPERATOR_REGISTRY: Dict[str, Operator] = {op.code: op for op in _OPERATORS}


# ---- ordering: reproduce OPERATORS_BY_DATA_TYPE exactly ----
# Per-type explicit order. operators_for filters _OPERATORS by membership and
# the per-type sequence below, so any future reorder is a one-line change.
_ORDER_BY_TYPE: Dict[FieldDataType, List[str]] = {
    FieldDataType.STRING: ["eq", "neq", "contains", "ncontains", "starts", "ends",
                           "any", "none", "empty", "notempty"],
    FieldDataType.STRING_ARRAY: ["eq", "neq", "contains", "ncontains", "starts", "ends",
                                 "any", "all", "none", "empty", "notempty"],
    FieldDataType.JSONB_ARRAY: ["eq", "neq", "contains", "ncontains", "starts", "ends",
                                "any", "all", "none", "empty", "notempty"],
    FieldDataType.INTEGER: ["eq", "neq", "gt", "lt", "gte", "lte", "between", "empty", "notempty"],
    FieldDataType.FLOAT: ["eq", "neq", "gt", "lt", "gte", "lte", "between", "empty", "notempty"],
    FieldDataType.DATE: ["eq", "neq", "gt", "lt", "gte", "lte", "between", "within_last", "empty", "notempty"],
    FieldDataType.TIMESTAMP: ["eq", "neq", "gt", "lt", "gte", "lte", "between", "within_last", "empty", "notempty"],
    FieldDataType.BOOLEAN: ["eq", "empty", "notempty"],
    FieldDataType.JSONB: ["contains", "ncontains", "empty", "notempty"],
}


def operators_for(data_type: FieldDataType, is_array: bool) -> List[Operator]:
    """Ordered operators for a field type (replaces OPERATORS_BY_DATA_TYPE).

    `is_array` selects the display label downstream but does not change the
    operator set — the set is keyed by data_type. STRING gains `none` over the
    pure legacy list (set-exclusion is valid on scalar text too, per §5.2).
    """
    order = _ORDER_BY_TYPE.get(data_type, [])
    return [OPERATOR_REGISTRY[c] for c in order if c in OPERATOR_REGISTRY]


# Alias index — built once from each operator's code + aliases. Every typeable
# spelling is listed explicitly in `aliases`, in both spaced and underscored
# forms (e.g. "is empty"/"is_empty", "not in array"/"not_in_array"), so code +
# aliases are the single source of resolution keys (these aliases are also what
# ships to the frontend via operator_specs). Legacy §9 collisions resolve here:
#   'in' -> any, 'in array' -> eq, 'not in array' -> neq.
# array_label is intentionally NOT registered (display-only — would make 'has'
# collide between eq and contains).
_ALIAS_INDEX: Dict[str, Operator] = {}


def _register_alias(spelling: str, op: Operator) -> None:
    _ALIAS_INDEX[spelling.lower().strip()] = op


for _op in _OPERATORS:
    _register_alias(_op.code, _op)
    for _a in _op.aliases:
        _register_alias(_a, _op)


def resolve(spelling: str) -> Operator:
    """Resolve any spelling (code, spaced label, underscored label, alias) to an
    Operator. Raises OperatorResolutionError on an unknown spelling.
    """
    if spelling is None:
        raise OperatorResolutionError("operator spelling is None")
    key = str(spelling).lower().strip()
    op = _ALIAS_INDEX.get(key)
    if op is None:
        raise OperatorResolutionError(f"unknown operator spelling: {spelling!r}")
    return op


# ---- build_sql: one implementation per operator (used by BOTH SQL builders) ----
# Each function returns (fragment, {}) — params are mutated onto ctx.params via
# next_param(), so the returned dict is always {} and engines read ctx.params.


def _routing(ctx):
    f = ctx.field
    is_native_array = f.is_native_array()
    is_jsonb = f.is_jsonb_sourced()
    is_jsonb_array = (f.data_type == FieldDataType.JSONB_ARRAY)
    is_tag_boolean = (getattr(f, "sql_kind", None) == "tag_boolean")
    return is_native_array, is_jsonb, is_jsonb_array, is_tag_boolean


def _value_list(value):
    """Normalise a list/range value (Python list OR legacy comma string) to its
    non-empty members, dropping blank entries. Member TYPES are preserved (a
    numeric bound stays a float so between binds it as a number, not '0.3'); only
    string members are trimmed. Shared by every list/range build_sql op so
    Explorer, the compiler, and match() agree on empty-entry handling (J-3).
    """
    items = value if isinstance(value, list) else str(value).split(",")
    out = []
    for v in items:
        if isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s)
        elif v is not None:
            out.append(v)
    return out


def _is_text_scalar(ctx):
    """True when a scalar column compares as text (case-insensitive, blank-aware).

    STRING and JSONB-extracted (``->>``) values are text; numeric/date/boolean
    columns are NOT and must compare with their native type (no LOWER(), no
    ``= ''``). Used by eq/neq/empty/notempty to stay type-safe (B-1/B-2).
    """
    dt = getattr(ctx.field, "data_type", None)
    return dt in (FieldDataType.STRING, FieldDataType.JSONB) or dt is None


def _escape_like(value):
    """Escape ILIKE metacharacters so contains/starts/ends are LITERAL matches.

    ``%``/``_``/``\\`` in the user value are escaped (with ``ESCAPE '\\'`` on the
    SQL side) so they match themselves rather than acting as wildcards — aligning
    the SQL path with the always-literal in-memory matcher (decision 2026-06-04).
    """
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bs_eq(col, value, ctx):
    na, jb, jba, tag = _routing(ctx)
    if tag:
        truthy = str(value).lower() in ("true", "1", "yes") if value is not None else False
        return (f"{col} IS NOT NULL" if truthy else f"{col} IS NULL"), {}
    p = ctx.next_param(value)
    if jba:
        if isinstance(value, str):
            return (f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                    f"WHERE LOWER(x.value) = LOWER(%({p})s))"), {}
        return (f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                f"WHERE x.value = %({p})s)"), {}
    if na:
        if isinstance(value, str):
            return (f"EXISTS (SELECT 1 FROM unnest({col}) AS x "
                    f"WHERE LOWER(x) = LOWER(%({p})s))"), {}
        return f"EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE x = %({p})s)", {}
    # Scalar: LOWER only for genuine text columns. A numeric/date/boolean column
    # must compare with its native type (LOWER(numeric|date|boolean) is a Postgres
    # error); the param is still bound and Postgres casts it (B-2).
    if isinstance(value, str) and _is_text_scalar(ctx):
        return f"LOWER({col}) = LOWER(%({p})s)", {}
    return f"{col} = %({p})s", {}


def _bs_neq(col, value, ctx):
    na, jb, jba, tag = _routing(ctx)
    p = ctx.next_param(value)
    # Array negation: a NULL/absent array is EXCLUDED (decision 2026-06-04), so
    # guard with IS NOT NULL — bare NOT EXISTS over unnest(NULL) is TRUE and would
    # wrongly include a missing field, diverging from the in-memory NULL guard.
    if jba:
        inner = (f"NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                 f"WHERE LOWER(x.value) = LOWER(%({p})s))" if isinstance(value, str)
                 else f"NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                      f"WHERE x.value = %({p})s)")
        return f"({col} IS NOT NULL AND {inner})", {}
    if na:
        inner = (f"NOT EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE LOWER(x) = LOWER(%({p})s))"
                 if isinstance(value, str)
                 else f"NOT EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE x = %({p})s)")
        return f"({col} IS NOT NULL AND {inner})", {}
    # Scalar: NULL is already excluded (NULL != x -> NULL). LOWER only for text.
    if isinstance(value, str) and _is_text_scalar(ctx):
        return f"LOWER({col}) != LOWER(%({p})s)", {}
    return f"{col} != %({p})s", {}


def _bs_like(template):
    # contains/starts/ends use ILIKE (substring/prefix/suffix) as a LITERAL match:
    # %/_/\\ in the value are escaped via _escape_like + ESCAPE '\\' so they match
    # themselves, not act as wildcards (decision 2026-06-04 — aligns with the
    # always-literal in-memory matcher). The value is still a bound param.
    def _inner(col, value, ctx):
        na, jb, jba, tag = _routing(ctx)
        p = ctx.next_param(template.format(v=_escape_like(value)))
        if jba:
            return (f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                    f"WHERE x.value ILIKE %({p})s ESCAPE '\\')"), {}
        if na:
            return (f"EXISTS (SELECT 1 FROM unnest({col}) AS x "
                    f"WHERE x ILIKE %({p})s ESCAPE '\\')"), {}
        return f"{col} ILIKE %({p})s ESCAPE '\\'", {}
    return _inner


def _bs_nlike(template):
    # ncontains; literal match (see _bs_like). Array negation excludes a NULL
    # array via IS NOT NULL (decision 2026-06-04); scalar NOT ILIKE already
    # excludes NULL (NULL NOT ILIKE x -> NULL).
    def _inner(col, value, ctx):
        na, jb, jba, tag = _routing(ctx)
        p = ctx.next_param(template.format(v=_escape_like(value)))
        if jba:
            return (f"({col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
                    f"jsonb_array_elements_text({col}) x "
                    f"WHERE x.value ILIKE %({p})s ESCAPE '\\'))"), {}
        if na:
            return (f"({col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unnest({col}) AS x "
                    f"WHERE x ILIKE %({p})s ESCAPE '\\'))"), {}
        return f"{col} NOT ILIKE %({p})s ESCAPE '\\'", {}
    return _inner


# Relative-duration parsing for date/number comparisons ("30d" -> ISO cutoff),
# co-located here because operators.py is its sole caller (keeps this a leaf).
_RELATIVE_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_RELATIVE_SECONDS_PER_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _resolve_relative_duration(value):
    """If ``value`` is a relative duration string like ``"30d"``/``"24h"``,
    return an ISO-8601 UTC timestamp at ``NOW() - duration``; otherwise return
    ``value`` unchanged. The regex is narrow enough that absolute ISO values
    (``"2026-04-22T00:00:00Z"``) pass through untouched.
    """
    if not isinstance(value, str):
        return value
    m = _RELATIVE_DURATION_RE.match(value)
    if not m:
        return value
    seconds = _RELATIVE_SECONDS_PER_UNIT[m.group(2)] * int(m.group(1))
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _bs_cmp(sql_op):
    def _inner(col, value, ctx):
        na, jb, jba, tag = _routing(ctx)
        p = ctx.next_param(_resolve_relative_duration(value))
        if na:
            return f"EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE x {sql_op} %({p})s)", {}
        if jb:
            return f"({col})::text {sql_op} %({p})s", {}
        return f"{col} {sql_op} %({p})s", {}
    return _inner


def _bs_between(col, value, ctx):
    # Inclusive number/date range bound as two params. Accepts a [lo, hi] list
    # (Explorer/MCP) or a "lo,hi" comma string (the FilterRow serialisation),
    # normalised the same way as any/all/none. Each bound passes through
    # _resolve_relative_duration like _bs_cmp, so a relative "30d" binds as an
    # absolute ISO cutoff (never interval(v)) and a numeric/absolute value is
    # untouched. Fewer than two bounds drops the clause (no half-range).
    na, jb, jba, tag = _routing(ctx)
    values = _value_list(value)
    if len(values) < 2:
        return "", {}
    target = f"({col})::text" if jb else col
    p_lo = ctx.next_param(_resolve_relative_duration(values[0]))
    p_hi = ctx.next_param(_resolve_relative_duration(values[1]))
    return f"{target} BETWEEN %({p_lo})s AND %({p_hi})s", {}


def _bs_within_last(col, value, ctx):
    # "in the last N units" -> col >= (NOW() - duration), the cutoff resolved to
    # an absolute ISO timestamp via the shared _resolve_relative_duration. NEVER
    # emit interval(v): binding a literal cutoff lets the planner use the same
    # plan as a literal ISO bound. Date-only operator.
    na, jb, jba, tag = _routing(ctx)
    p = ctx.next_param(_resolve_relative_duration(value))
    if jb:
        return f"({col})::text >= %({p})s", {}
    return f"{col} >= %({p})s", {}


def _bs_any(col, value, ctx):
    na, jb, jba, tag = _routing(ctx)
    values = _value_list(value)
    if not values:
        return "", {}
    all_str = all(isinstance(v, str) for v in values)
    placeholders = []
    for v in values:
        p = ctx.next_param(v)
        placeholders.append(f"LOWER(%({p})s)" if all_str else f"%({p})s")
    in_clause = ", ".join(placeholders)
    if all_str:
        if jba:
            return (f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                    f"WHERE LOWER(x.value) IN ({in_clause}))"), {}
        if na:
            return f"EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE LOWER(x) IN ({in_clause}))", {}
        return f"LOWER({col}) IN ({in_clause})", {}
    if jba:
        return (f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                f"WHERE x.value IN ({in_clause}))"), {}
    if na:
        return f"EXISTS (SELECT 1 FROM unnest({col}) AS x WHERE x IN ({in_clause}))", {}
    return f"{col} IN ({in_clause})", {}


def _bs_all(col, value, ctx):
    # "has all of [A,B]" = EXISTS elem=A AND EXISTS elem=B (listed ⊆ array).
    # all/none only apply to text arrays (STRING_ARRAY / JSONB_ARRAY), so wrapping
    # each value in LOWER(%(..)s) is always valid.
    na, jb, jba, tag = _routing(ctx)
    values = _value_list(value)
    if not values:
        return "", {}
    parts = []
    for v in values:
        p = ctx.next_param(v)
        if jba:
            parts.append(f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({col}) x "
                         f"WHERE LOWER(x.value) = LOWER(%({p})s))")
        else:
            parts.append(f"EXISTS (SELECT 1 FROM unnest({col}) AS x "
                         f"WHERE LOWER(x) = LOWER(%({p})s))")
    return "(" + " AND ".join(parts) + ")", {}


def _bs_none(col, value, ctx):
    # "has none of [A,B]" = no element/value in the list, case-insensitive.
    # `none` applies to text (scalar STRING or STRING_ARRAY/JSONB_ARRAY), so
    # wrapping each value in LOWER(%(..)s) is always valid.
    na, jb, jba, tag = _routing(ctx)
    values = _value_list(value)
    if not values:
        return "", {}
    placeholders = []
    for v in values:
        p = ctx.next_param(v)
        placeholders.append(f"LOWER(%({p})s)")
    in_clause = ", ".join(placeholders)
    # A NULL/missing field is EXCLUDED from none-of (decision 2026-06-04 — aligns
    # with the in-memory NULL guard), so guard every branch with IS NOT NULL.
    if jba:
        return (f"({col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
                f"jsonb_array_elements_text({col}) x WHERE LOWER(x.value) IN ({in_clause})))"), {}
    if na:
        return (f"({col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unnest({col}) AS x "
                f"WHERE LOWER(x) IN ({in_clause})))"), {}
    return f"({col} IS NOT NULL AND LOWER({col}) NOT IN ({in_clause}))", {}


def _bs_empty(col, value, ctx):
    na, jb, jba, tag = _routing(ctx)
    if jba:
        return f"({col} IS NULL OR jsonb_array_length({col}) = 0)", {}
    if na:
        return f"({col} IS NULL OR {col} = '{{}}')", {}
    if tag:
        return f"{col} IS NULL", {}
    # Text columns can be NULL OR '' ; numeric/date/boolean columns only NULL
    # (col = '' is a Postgres type error on a typed column — B-1).
    if _is_text_scalar(ctx):
        return f"({col} IS NULL OR {col} = '')", {}
    return f"{col} IS NULL", {}


def _bs_notempty(col, value, ctx):
    na, jb, jba, tag = _routing(ctx)
    if jba:
        return f"({col} IS NOT NULL AND jsonb_array_length({col}) > 0)", {}
    if na:
        return f"({col} IS NOT NULL AND {col} != '{{}}')", {}
    if tag:
        return f"{col} IS NOT NULL", {}
    if _is_text_scalar(ctx):
        return f"({col} IS NOT NULL AND {col} != '')", {}
    return f"{col} IS NOT NULL", {}


def _is_native_boolean(ctx):
    # Only a genuine boolean COLUMN compares with IS TRUE/IS FALSE. A tag_boolean
    # or any JSONB-sourced field (col is `ea->>'x'`, i.e. text) must use presence,
    # else `text IS TRUE` is a Postgres type error.
    na, jb, jba, tag = _routing(ctx)
    return (not tag and not jb and not na
            and getattr(ctx.field, "data_type", None) == FieldDataType.BOOLEAN)


def _bs_is_true(col, value, ctx):
    # A real boolean column compares by truth value; a tag_boolean / JSONB-sourced
    # (text) field by presence (B-3).
    if _is_native_boolean(ctx):
        return f"{col} IS TRUE", {}
    return f"{col} IS NOT NULL", {}


def _bs_is_false(col, value, ctx):
    # NULL counts as "false" to match the in-memory matcher (_m_is_false(None) is
    # True) and tag_boolean presence — without `OR col IS NULL`, a native boolean
    # column with NULL rows diverges from match() (verified on real data: SQL
    # `IS FALSE` selected 15 rows vs match() 135 on a 162-asset DB). is_true stays
    # `col IS TRUE` (NULL excluded), symmetric with _m_is_true(None) -> False.
    if _is_native_boolean(ctx):
        return f"({col} IS FALSE OR {col} IS NULL)", {}
    return f"{col} IS NULL", {}


_BUILD_SQL = {
    "eq": _bs_eq, "neq": _bs_neq,
    "contains": _bs_like("%{v}%"), "ncontains": _bs_nlike("%{v}%"),
    "starts": _bs_like("{v}%"), "ends": _bs_like("%{v}"),
    "gt": _bs_cmp(">"), "gte": _bs_cmp(">="), "lt": _bs_cmp("<"), "lte": _bs_cmp("<="),
    "between": _bs_between, "within_last": _bs_within_last,
    "any": _bs_any, "all": _bs_all, "none": _bs_none,
    "empty": _bs_empty, "notempty": _bs_notempty,
    "is_true": _bs_is_true, "is_false": _bs_is_false,
}
for _code, _fn in _BUILD_SQL.items():
    object.__setattr__(OPERATOR_REGISTRY[_code], "_build_sql", _fn)


# ---- match: in-memory superset of all four engines (used by BOTH evaluators) ----


def _as_list(v):
    # Match path: members are compared via str().lower(), so stringify + strip
    # (drops blanks like _value_list, but coerces to str for the matchers).
    return [str(x).strip() for x in _value_list(v)]


def _eq_scalar(av, fv):
    """Type-aware scalar equality keyed on the asset value's RUNTIME type.

    A numeric column yields a number and a TIMESTAMP column a datetime (psycopg2),
    so eq normalises those to compare like SQL (5.0 == '5', midnight datetime ==
    date-only string). A text column yields a str and stays string-exact, so a
    text value that merely looks numeric ('007' != '7') is not coerced. bool is
    excluded from the numeric branch (bool is an int subclass). (A-3)
    """
    if isinstance(av, bool):
        return str(av).lower() == str(fv).lower()
    if isinstance(av, (int, float, Decimal)):
        try:
            return float(av) == float(fv)
        except (ValueError, TypeError):
            return str(av).lower() == str(fv).lower()
    if isinstance(av, (datetime, date)):
        a = _to_comparable(av)
        b = _to_comparable(fv)
        if isinstance(a, datetime) and isinstance(b, datetime):
            return a == b
        return str(av).lower() == str(fv).lower()
    return str(av).lower() == str(fv).lower()


def _m_eq(av, fv, ctx):
    if isinstance(av, list):
        return any(_eq_scalar(x, fv) for x in av)
    return _eq_scalar(av, fv)


def _m_neq(av, fv, ctx):
    return not _m_eq(av, fv, ctx)


def _m_contains(av, fv, ctx):
    if isinstance(av, list):
        return any(str(fv).lower() in str(x).lower() for x in av)
    return str(fv).lower() in str(av).lower()


def _m_ncontains(av, fv, ctx):
    return not _m_contains(av, fv, ctx)


def _m_starts(av, fv, ctx):
    if isinstance(av, list):
        return any(str(x).lower().startswith(str(fv).lower()) for x in av)
    return str(av).lower().startswith(str(fv).lower())


def _m_ends(av, fv, ctx):
    if isinstance(av, list):
        return any(str(x).lower().endswith(str(fv).lower()) for x in av)
    return str(av).lower().endswith(str(fv).lower())


def _m_cmp(pyop):
    # gt/lt/gte/lte compare numbers numerically AND dates chronologically: route
    # both operands through _to_comparable (float | tz-normalised datetime) and
    # resolve a relative-duration filter value ('30d' -> cutoff) the same way the
    # SQL builder does, so a date-threshold rule no longer silently returns False
    # in-memory (A-1). Non-comparable operands -> no match.
    def _inner(av, fv, ctx):
        a = _to_comparable(av)
        b = _to_comparable(_resolve_relative_duration(fv))
        try:
            return pyop(a, b)
        except TypeError:
            return False
    return _inner


def _to_comparable(v):
    """Coerce a value to something orderable so between/within_last compare
    numbers numerically and dates chronologically in-memory (the generic _m_cmp
    only float()s). Date/datetime values are normalised to a single shape —
    datetime.date -> datetime, naive datetime -> UTC — so a DATE column value
    (psycopg2 yields datetime.date), a naive TIMESTAMP, an ISO string, and the
    always-tz-aware relative-duration cutoff all compare consistently and match
    the SQL path (rather than raising a TypeError that silently returns False).
    Non-date, non-numeric values pass through unchanged."""
    try:
        return float(v)
    except (ValueError, TypeError):
        pass
    dt = None
    if isinstance(v, datetime):           # NB: datetime is a subclass of date,
        dt = v                            # so check datetime BEFORE date.
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    elif isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    if dt is None:
        return v
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _m_between(av, fv, ctx):
    values = _value_list(fv)
    if len(values) < 2:
        return False
    lo = _to_comparable(_resolve_relative_duration(values[0]))
    hi = _to_comparable(_resolve_relative_duration(values[1]))
    a = _to_comparable(av)
    try:
        return lo <= a <= hi
    except TypeError:
        return False


def _m_within_last(av, fv, ctx):
    cutoff = _to_comparable(_resolve_relative_duration(fv))
    a = _to_comparable(av)
    try:
        return a >= cutoff
    except TypeError:
        return False


def _m_any(av, fv, ctx):
    wanted = {s.lower() for s in _as_list(fv)}
    vals = av if isinstance(av, list) else [av]
    return any(str(x).lower() in wanted for x in vals)


def _m_all(av, fv, ctx):
    wanted = {s.lower() for s in _as_list(fv)}
    have = {str(x).lower() for x in (av if isinstance(av, list) else [av])}
    return wanted.issubset(have)


def _m_none(av, fv, ctx):
    return not _m_any(av, fv, ctx)


def _m_empty(av, fv, ctx):
    return av is None or av == "" or av == []


def _m_notempty(av, fv, ctx):
    return not _m_empty(av, fv, ctx)


def _m_is_true(av, fv, ctx):
    if isinstance(av, bool):
        return av is True
    if isinstance(av, str):
        return av.lower() == "true"
    return bool(av)


def _m_is_false(av, fv, ctx):
    if isinstance(av, bool):
        return av is False
    if isinstance(av, str):
        return av.lower() == "false"
    return not bool(av)


_MATCH = {
    "eq": _m_eq, "neq": _m_neq,
    "contains": _m_contains, "ncontains": _m_ncontains,
    "starts": _m_starts, "ends": _m_ends,
    "gt": _m_cmp(_pyoperator.gt), "gte": _m_cmp(_pyoperator.ge),
    "lt": _m_cmp(_pyoperator.lt), "lte": _m_cmp(_pyoperator.le),
    "between": _m_between, "within_last": _m_within_last,
    "any": _m_any, "all": _m_all, "none": _m_none,
    "empty": _m_empty, "notempty": _m_notempty,
    "is_true": _m_is_true, "is_false": _m_is_false,
}
for _code, _fn in _MATCH.items():
    object.__setattr__(OPERATOR_REGISTRY[_code], "_match", _fn)
