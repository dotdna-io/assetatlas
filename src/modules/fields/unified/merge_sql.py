# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate merge SQL for a Unified Field by composing each source's
already-resolved, already-escaped SQL with a fixed function template.
No raw SQL is accepted — sources are validated FieldIds resolved by the
canonical FieldResolver."""

from typing import List, Sequence

from src.modules.fields.encoding import decode
from src.modules.fields.models import InvalidFieldError
from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.definition import type_class


def _numeric_member(rf) -> str:
    if rf.sql_kind == "column":
        return rf.where_expr()
    return f"aa_try_numeric(NULLIF({rf.where_expr()}, ''))"


def _ts_member(rf) -> str:
    if rf.sql_kind == "column":
        return rf.where_expr()
    return f"aa_try_ts(NULLIF({rf.where_expr()}, ''))"


def _coalesce_member(rf) -> str:
    cls = type_class(rf.data_type)
    if cls == "number":
        return _numeric_member(rf)
    if cls == "date":
        return _ts_member(rf)
    return f"NULLIF({rf.where_expr()}, '')"  # text


def _ordinal_member(rf) -> str:
    # max/min only apply to number/date.
    return _ts_member(rf) if type_class(rf.data_type) == "date" else _numeric_member(rf)


def _union_member(rf) -> str:
    """A text[] expression of this source's value(s); union always yields text[]."""
    if rf.is_native_array():
        return f"COALESCE({rf.where_expr()}, '{{}}'::text[])"
    if rf.data_type == FieldDataType.JSONB_ARRAY:
        return (f"COALESCE(ARRAY(SELECT jsonb_array_elements_text({rf.where_expr()})), "
                f"'{{}}'::text[])")
    expr_text = f"({rf.where_expr()})::text"
    return (f"(CASE WHEN {expr_text} IS NULL OR {expr_text} = '' "
            f"THEN '{{}}'::text[] ELSE ARRAY[{expr_text}] END)")


def _resolve_live(resolver, sources: Sequence[str]) -> List:
    out = []
    for key in sources:
        try:
            rf = resolver.resolve(decode(key))
        except (InvalidFieldError, ValueError):
            continue  # source removed/renamed -> skip (graceful degradation)
        out.append(rf)
    return out


def build_merge_sql(resolver, function: str, sources: Sequence[str]) -> str:
    resolved = _resolve_live(resolver, sources)
    if not resolved:
        return "NULL::text[]" if function == "union" else "NULL"
    if function == "coalesce":
        return f"COALESCE({', '.join(_coalesce_member(rf) for rf in resolved)})"
    if function in ("max", "min"):
        fn = "GREATEST" if function == "max" else "LEAST"
        return f"{fn}({', '.join(_ordinal_member(rf) for rf in resolved)})"
    if function == "union":
        members = " || ".join(_union_member(rf) for rf in resolved)
        return (f"ARRAY(SELECT DISTINCT v FROM unnest({members}) AS v "
                f"WHERE v IS NOT NULL AND v <> '')")
    raise ValueError(f"unknown function: {function}")
