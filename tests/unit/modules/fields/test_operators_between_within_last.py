# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 3: between + within_last operators (registry units + cross-engine).

WHY: the two scalar-helper operators must bind their parameters the way the
real Phase-1 OperatorCtx pins them — via ctx.next_param(value), which yields
%(f0_0)s / %(f0_1)s (NOT the f0_lo/f0_hi the design sketch imagined) and
returns (fragment, {}) with params mutated onto ctx.params. between binds two
inclusive params; within_last binds ONE ISO cutoff via the shared
_resolve_relative_duration helper (col >= cutoff, never interval()). match()
must mirror the SQL so Explorer and every rule engine agree, and a relative
duration string ("30d") must resolve identically in build_sql and match.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from src.modules.fields.operators import (
    OPERATOR_REGISTRY,
    OperatorCtx,
    resolve,
)
from src.modules.fields.types import FieldDataType


class _FakeResolved:
    """Minimal ResolvedField stand-in for build_sql routing (mirrors the one in
    test_operators.py). between/within_last are scalar number/date here."""

    def __init__(self, data_type, sql_kind="column"):
        self.data_type = data_type
        self.sql_kind = sql_kind

    def is_array(self):
        return self.data_type in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)

    def is_native_array(self):
        return self.is_array() and self.sql_kind == "column"

    def is_jsonb_sourced(self):
        return self.sql_kind in ("jsonb_path", "jsonb_coalesce", "tag_boolean", "posture_jsonb")


def _ctx(data_type=FieldDataType.FLOAT, sql_kind="column"):
    """Real Phase-1 OperatorCtx: param_key='f0', empty mutable params. Engines
    read params off ctx.params after build_sql; build_sql returns (frag, {})."""
    return OperatorCtx(field=_FakeResolved(data_type, sql_kind), param_key="f0", params={})


# ---------------------------------------------------------------- between ----

def test_between_registered_and_resolvable():
    op = resolve("between")
    assert op.code == "between"
    assert op.arity == "list"
    assert OPERATOR_REGISTRY["between"] is op


def test_between_build_sql_binds_two_inclusive_params():
    op = resolve("between")
    ctx = _ctx()
    sql, params = op.build_sql("confidence", [0.3, 0.8], ctx)
    # Inclusive BETWEEN; params come from next_param -> f0_0 / f0_1 (Phase-1
    # contract — there is no f0_lo/f0_hi naming).
    assert sql == "confidence BETWEEN %(f0_0)s AND %(f0_1)s"
    # build_sql returns ({}); the bounds land on ctx.params.
    assert params == {}
    assert ctx.params == {"f0_0": 0.3, "f0_1": 0.8}


def test_between_accepts_comma_string_from_filter_row():
    # FilterRow serialises its two inputs into one comma string; the builder
    # splits it the same way any/all/none do, so the FilterRow and the
    # Explorer/MCP list form both work.
    op = resolve("between")
    ctx = _ctx()
    sql, _ = op.build_sql("confidence", "0.3,0.8", ctx)
    assert sql == "confidence BETWEEN %(f0_0)s AND %(f0_1)s"
    assert ctx.params == {"f0_0": "0.3", "f0_1": "0.8"}


def test_between_drops_clause_when_fewer_than_two_bounds():
    # A single/empty bound (e.g. a half-filled FilterRow) drops the clause
    # rather than raising — mirrors any/all/none's empty-list drop and keeps the
    # §6.2 "never raises" coverage guarantee.
    op = resolve("between")
    assert op.build_sql("confidence", "0.3", _ctx())[0] == ""
    assert op.build_sql("confidence", [], _ctx())[0] == ""


def test_between_applies_to_numbers_and_dates_only():
    op = resolve("between")
    for dt in (FieldDataType.INTEGER, FieldDataType.FLOAT,
               FieldDataType.DATE, FieldDataType.TIMESTAMP):
        assert dt in op.applies_to
    assert FieldDataType.STRING not in op.applies_to
    assert FieldDataType.STRING_ARRAY not in op.applies_to


def test_between_accepts_relative_date_bounds():
    """Per spec §13 residual: relative strings are accepted as between bounds
    via the SAME _resolve_relative_duration, so a date 'between 30d and 7d'
    resolves BOTH bounds to absolute ISO before binding (never the raw '30d')."""
    op = resolve("between")
    ctx = _ctx(data_type=FieldDataType.TIMESTAMP)
    sql, _ = op.build_sql("last_seen", ["30d", "7d"], ctx)
    assert sql == "last_seen BETWEEN %(f0_0)s AND %(f0_1)s"
    assert "T" in ctx.params["f0_0"] and ctx.params["f0_0"] != "30d"
    assert "T" in ctx.params["f0_1"] and ctx.params["f0_1"] != "7d"


def test_between_match_inclusive_numeric():
    op = resolve("between")
    assert op.match(0.5, [0.3, 0.8], None) is True
    assert op.match(0.3, [0.3, 0.8], None) is True   # inclusive low
    assert op.match(0.8, [0.3, 0.8], None) is True   # inclusive high
    assert op.match(0.9, [0.3, 0.8], None) is False
    # numeric, not lexicographic: 9 is NOT between 10 and 20.
    assert op.match(9, [10, 20], None) is False
    assert op.match(15, [10, 20], None) is True


def test_between_match_dates_iso():
    op = resolve("between")
    lo = "2026-01-01T00:00:00+00:00"
    hi = "2026-06-01T00:00:00+00:00"
    assert op.match("2026-03-01T00:00:00+00:00", [lo, hi], None) is True
    assert op.match("2026-07-01T00:00:00+00:00", [lo, hi], None) is False


def test_between_match_null_is_no_match():
    # The Operator.match NULL guard returns False for a missing value (between
    # is not null-aware).
    assert resolve("between").match(None, [0.3, 0.8], None) is False


# ----------------------------------------------------------- within_last ----

def test_within_last_registered_arity_one_date_only():
    op = resolve("within last")  # spaced label resolves
    assert op.code == "within_last"
    assert op.arity == "one"
    assert FieldDataType.DATE in op.applies_to
    assert FieldDataType.TIMESTAMP in op.applies_to
    # within_last is date-only: never on numeric types.
    assert FieldDataType.FLOAT not in op.applies_to
    assert FieldDataType.INTEGER not in op.applies_to


def test_within_last_aliases_resolve():
    for spelling in ("within_last", "within last", "in the last", "withinlast"):
        assert resolve(spelling).code == "within_last"


def test_within_last_build_sql_binds_single_iso_cutoff():
    op = resolve("within_last")
    ctx = _ctx(data_type=FieldDataType.TIMESTAMP)
    sql, params = op.build_sql("last_seen", "30d", ctx)
    # col >= cutoff, ONE param, NEVER interval(v).
    assert sql == "last_seen >= %(f0_0)s"
    assert "interval" not in sql.lower()
    assert params == {}
    # Resolved to an absolute ISO cutoff, not the raw "30d".
    assert "T" in ctx.params["f0_0"] and ctx.params["f0_0"] != "30d"


def test_within_last_match_uses_cutoff():
    """match() resolves the cutoff and keeps rows at/after it (in-memory parity
    with the SQL >= cutoff)."""
    op = resolve("within_last")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert op.match(recent, "30d", None) is True
    assert op.match(old, "30d", None) is False


def test_within_last_match_null_is_no_match():
    assert resolve("within_last").match(None, "30d", None) is False


# --- DB-driver value shapes: match() must mirror the SQL path (cross-engine) --
# psycopg2 returns datetime.date for DATE columns (e.g. os_eol_date) and a NAIVE
# datetime for plain TIMESTAMP columns; the relative-duration cutoff is always
# tz-aware. match() must compare these consistently, not swallow a TypeError and
# silently return False (which would mark a row failing a condition the
# equivalent SQL filter includes).

def test_between_match_accepts_date_objects():
    op = resolve("between")
    lo, hi = "2026-01-01", "2026-06-01"
    assert op.match(date(2026, 3, 1), [lo, hi], None) is True
    assert op.match(date(2026, 7, 1), [lo, hi], None) is False
    # date object bounds too (both sides as dates).
    assert op.match(date(2026, 3, 1), [date(2026, 1, 1), date(2026, 6, 1)], None) is True


def test_within_last_match_accepts_date_objects():
    op = resolve("within_last")
    assert op.match(date.today() - timedelta(days=2), "30d", None) is True
    assert op.match(date.today() - timedelta(days=60), "30d", None) is False


def test_within_last_match_accepts_naive_datetime():
    op = resolve("within_last")
    # plain TIMESTAMP column -> naive datetime; must still compare to the
    # tz-aware cutoff (assume UTC) rather than raising and returning False.
    assert op.match(datetime.now() - timedelta(days=2), "30d", None) is True
    assert op.match(datetime.now() - timedelta(days=60), "30d", None) is False


def test_between_match_accepts_naive_datetime():
    op = resolve("between")
    lo, hi = "2026-01-01", "2026-12-31"
    assert op.match(datetime(2026, 6, 1), [lo, hi], None) is True
    assert op.match(datetime(2025, 6, 1), [lo, hi], None) is False


def test_within_last_in_date_dropdown_order_after_between():
    from src.modules.fields.operators import operators_for
    codes = [op.code for op in operators_for(FieldDataType.TIMESTAMP, is_array=False)]
    assert codes == ["eq", "neq", "gt", "lt", "gte", "lte",
                     "between", "within_last", "empty", "notempty"]
