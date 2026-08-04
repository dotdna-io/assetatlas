# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration guardrail: SQL build_sql() and in-memory match() qualify the SAME rows.

This is the test the unification was missing — the pre-existing
test_engine_equivalence.py only compares the two SQL *builders* to each other and
never crosses the SQL↔in-memory boundary, which is why a whole cluster of
divergences (date gt/lt, literal contains, typed empty/eq, NULL negation) shipped
green. This harness seeds a real table with one row per scenario, executes the
registry's SQL, and asserts the matching id-set equals the set selected by match()
over the same Python values.

Requires a live Postgres (DATABASE_URL / :5434). Run with `pytest -m integration`.
"""

import os
from datetime import datetime, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.modules.fields.operators import OPERATOR_REGISTRY, OperatorCtx
from src.modules.fields.types import FieldDataType

pytestmark = pytest.mark.integration


# (data_type, sql_kind, pg column type, python asset values keyed by id)
SCENARIOS = {
    "text": (FieldDataType.STRING, "column", "text",
             {1: "Windows", 2: "win_srv", 3: "winXsrv", 4: "", 5: None}),
    "num": (FieldDataType.FLOAT, "column", "numeric",
            {1: 0.9, 2: 0.5, 3: 0.0, 4: None}),
    "date": (FieldDataType.DATE, "column", "date",
             {1: "2026-03-01", 2: "2025-12-31", 3: "2026-01-01", 4: None}),
    # Use real datetimes (what psycopg2 yields from a timestamptz column) so the
    # in-memory match() compares the same value the SQL path does — passing the
    # ISO string would make match() string-compare and falsely diverge.
    "ts": (FieldDataType.TIMESTAMP, "column", "timestamptz",
           {1: datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            2: datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc), 3: None}),
    "bool": (FieldDataType.BOOLEAN, "column", "boolean",
             {1: True, 2: False, 3: None}),
    "arr": (FieldDataType.STRING_ARRAY, "column", "text[]",
            {1: ["jamf", "sccm"], 2: ["ad"], 3: [], 4: None}),
}

# (scenario, operator code, filter value)
CASES = [
    ("text", "eq", "windows"), ("text", "neq", "windows"),
    ("text", "contains", "win_srv"), ("text", "ncontains", "win_srv"),
    ("text", "none", ["windows", "ad"]), ("text", "empty", None),
    ("num", "gt", "0.6"), ("num", "lt", "0.6"), ("num", "gte", "0.5"),
    ("num", "eq", "0.5"), ("num", "empty", None), ("num", "notempty", None),
    ("date", "gt", "2026-01-01"), ("date", "lte", "2026-01-01"),
    ("date", "eq", "2026-01-01"), ("date", "empty", None),
    ("ts", "eq", "2026-01-01"), ("ts", "gt", "2026-01-01"), ("ts", "empty", None),
    ("bool", "eq", "true"), ("bool", "is_true", None), ("bool", "is_false", None),
    ("bool", "empty", None),
    ("arr", "eq", "jamf"), ("arr", "neq", "jamf"), ("arr", "any", ["jamf"]),
    ("arr", "all", ["jamf", "sccm"]), ("arr", "none", ["jamf"]),
    ("arr", "contains", "jam"), ("arr", "empty", None),
]


class _Field:
    def __init__(self, data_type, sql_kind):
        self.data_type = data_type
        self.sql_kind = sql_kind

    def is_array(self):
        return self.data_type in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)

    def is_native_array(self):
        return self.is_array() and self.sql_kind == "column"

    def is_jsonb_sourced(self):
        return self.sql_kind in ("jsonb_path", "jsonb_coalesce", "tag_boolean", "posture_jsonb")


@pytest.fixture(scope="module")
def conn():
    url = os.environ.get("DATABASE_URL", "postgresql://test:test@localhost:5434/assetatlas_test")
    try:
        c = psycopg2.connect(url)
    except Exception as exc:  # pragma: no cover - skip when no DB
        pytest.skip(f"no test DB: {exc}")
    yield c
    c.close()


def _expected_via_match(op, rows, fv):
    return {rid for rid, av in rows.items() if op.match(av, fv, None)}


@pytest.mark.parametrize("scenario,code,fv", CASES)
def test_sql_matches_inmemory(conn, scenario, code, fv):
    data_type, sql_kind, pg_type, rows = SCENARIOS[scenario]
    op = OPERATOR_REGISTRY[code]
    table = f"op_equiv_{scenario}"

    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE TEMP TABLE {table} (id int primary key, val {pg_type})")
        for rid, av in rows.items():
            cur.execute(f"INSERT INTO {table} (id, val) VALUES (%s, %s)", (rid, av))

        ctx = OperatorCtx(field=_Field(data_type, sql_kind), param_key="f0", params={})
        frag, _ = op.build_sql("val", fv, ctx)
        if not frag:
            pytest.skip("operator dropped the clause for this value")

        cur.execute(f"SELECT id FROM {table} WHERE {frag}", ctx.params)
        sql_ids = {r[0] for r in cur.fetchall()}

    expected = _expected_via_match(op, rows, fv)
    assert sql_ids == expected, (
        f"{scenario}/{code} fv={fv!r}: SQL selected {sorted(sql_ids)} but "
        f"match() selected {sorted(expected)}\n  fragment: {frag}\n  params: {ctx.params}"
    )
    conn.rollback()
