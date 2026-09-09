# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parity harness: stored unified_values must equal the live merge expression (#1405).

For every temporary unified definition, runs refresh_unified_values over
``assets`` and then asserts, per field, that no row exists where the live
``build_merge_sql`` expression differs (IS DISTINCT FROM) from the typed read of
the materialized bag:

  text    unified_values->>'slug'
  number  (unified_values->>'slug')::numeric
  date    (unified_values->>'slug')::timestamptz
  union   COALESCE(ARRAY(SELECT jsonb_array_elements_text(
              CASE WHEN jsonb_typeof(unified_values->'slug') = 'array'
                   THEN unified_values->'slug' END)), '{}'::text[])
          — both sides compared as sorted arrays

Part 2 (read flip): the same fields are also resolved through the REAL
FieldResolver — before stamping the resolver must emit the live merge SQL;
after set_materialized_at + catalog invalidation it must emit exactly the
typed reference above, and that flipped where_expr() must match the live
expression on every row.

Synthetic rows with NULL / empty-string / '{}'::jsonb / missing-key / garbage
edge cases are inserted when (and only when) the assets table is empty. All
definitions, bag keys, and synthetic rows are removed again at teardown.

Requires a disposable database (DATABASE_URL) — refuses the shared dev DB the
way the grace-period fixtures do. Run with:

    DATABASE_URL=postgresql://test:test@localhost:5434/assetatlas_test \
        .venv/bin/pytest tests/integration/test_unified_values_parity.py -m integration -q
"""

import os

import pytest

from src.modules.fields.models import VirtualField
from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.definition import type_class
from src.modules.fields.unified.materialize import (
    refresh_unified_values,
    remove_unified_value,
)
from src.modules.fields.unified.merge_sql import build_merge_sql
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.shared.field_resolver_factory import build_field_resolver

_FIELDS = [
    {"slug": "parity_text", "name": "Parity Text", "function": "coalesce",
     "sources": ["static:Category", "static:Subcategory"], "data_type": "string"},
    # The real-world shape: ext JSONB extraction coalesced with a static column.
    {"slug": "parity_ext_text", "name": "Parity Ext Text", "function": "coalesce",
     "sources": ["ext:parity:scalar", "static:Category"], "data_type": "string"},
    # static:Confidence Score is the only number-class valid source in the
    # catalog today (ext/group sources always catalog as STRING, virtuals are
    # rejected as sources), so number functions compose it with itself — still
    # a two-member COALESCE/GREATEST/LEAST through the numeric storage path.
    {"slug": "parity_num_coalesce", "name": "Parity Num Coalesce", "function": "coalesce",
     "sources": ["static:Confidence Score", "static:Confidence Score"], "data_type": "float"},
    {"slug": "parity_num_max", "name": "Parity Num Max", "function": "max",
     "sources": ["static:Confidence Score", "static:Confidence Score"], "data_type": "float"},
    {"slug": "parity_num_min", "name": "Parity Num Min", "function": "min",
     "sources": ["static:Confidence Score", "static:Confidence Score"], "data_type": "float"},
    {"slug": "parity_date_coalesce", "name": "Parity Date Coalesce", "function": "coalesce",
     "sources": ["static:OS EOL Date", "static:First Seen"], "data_type": "timestamp"},
    {"slug": "parity_date_max", "name": "Parity Date Max", "function": "max",
     "sources": ["static:First Seen", "static:Last Seen"], "data_type": "timestamp"},
    {"slug": "parity_date_min", "name": "Parity Date Min", "function": "min",
     "sources": ["static:First Seen", "static:Last Seen"], "data_type": "timestamp"},
    {"slug": "parity_union", "name": "Parity Union", "function": "union",
     "sources": ["static:Hostname", "static:Data Source", "ext:parity:scalar"],
     "data_type": "string_array"},
]

_SLUGS = [f["slug"] for f in _FIELDS]

_SYNTHETIC_ROWS = """
    INSERT INTO assets (id, category, subcategory, confidence,
                        first_seen, last_seen, os_eol_date,
                        hostname, data_source, extended_attributes)
    VALUES
      -- fully populated
      (gen_random_uuid(), 'server', 'linux', 0.90,
       '2026-01-01T10:00:00+00', '2026-09-01T10:00:00+00', '2030-10-14',
       ARRAY['web-1', 'web-1-canonical'], ARRAY['Intune', 'SCCM'],
       '{"parity": {"num2": "5", "scalar": "alpha"}}'::jsonb),
      -- NULLs everywhere
      (gen_random_uuid(), NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL),
      -- empty strings and empty containers
      (gen_random_uuid(), '', '', NULL, NULL, NULL, NULL,
       '{}', '{}', '{}'::jsonb),
      -- parity source present, num2 key missing, scalar empty
      (gen_random_uuid(), 'desktop', NULL, 0.10,
       NULL, '2026-05-05T05:05:05+00', NULL,
       '{}', ARRAY['SCCM'], '{"parity": {"scalar": ""}}'::jsonb),
      -- garbage numeric (aa_try_numeric -> NULL)
      (gen_random_uuid(), NULL, 'windows', NULL,
       '2020-02-02T02:02:02+00', NULL, '2025-10-14',
       ARRAY['ws-9'], '{}',
       '{"parity": {"num2": "not-a-number", "scalar": "beta"}}'::jsonb)
    RETURNING id
"""


def _db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("unified-values parity needs a disposable DB via DATABASE_URL")
    if "192.0.2.10" in db_url:
        pytest.skip(
            "refusing to run the unified-values parity harness against the shared dev DB"
        )
    from src.database.postgres_adapter import PostgreSQLAdapter
    try:
        adapter = PostgreSQLAdapter()
        adapter.execute_query("SELECT 1")
    except Exception as exc:
        pytest.skip(f"Cannot reach database: {exc}")
    return adapter


def _slug_lit(slug):
    return "'" + slug.replace("'", "''") + "'"


def _typed_read(field):
    """The materialized value, read back with the field's stored type.

    Byte-identical to what FieldResolver emits for a stamped field (part 2) —
    keep the two in sync.
    """
    lit = _slug_lit(field["slug"])
    if field["function"] == "union":
        return ("COALESCE(ARRAY(SELECT jsonb_array_elements_text("
                f"CASE WHEN jsonb_typeof(unified_values->{lit})='array' "
                f"THEN unified_values->{lit} END)), '{{}}'::text[])")
    cls = type_class(FieldDataType(field["data_type"]))
    if cls == "number":
        return f"(unified_values->>{lit})::numeric"
    if cls == "date":
        return f"(unified_values->>{lit})::timestamptz"
    return f"unified_values->>{lit}"


def _sorted_array(expr):
    return (f"(SELECT COALESCE(array_agg(v ORDER BY v), '{{}}'::text[]) "
            f"FROM unnest({expr}) AS v)")


@pytest.fixture(scope="module")
def parity_setup():
    db = _db()
    repo = UnifiedFieldsRepository(db)
    resolver = build_field_resolver(db)

    inserted_asset_ids = []
    created_field_ids = []
    try:
        count = (db.execute_query("SELECT count(*) AS n FROM assets") or [{}])[0]["n"]
        if int(count) == 0:
            rows = db.execute_query(_SYNTHETIC_ROWS) or []
            inserted_asset_ids = [str(r["id"]) for r in rows]

        for f in _FIELDS:
            row = repo.create(slug=f["slug"], name=f["name"], function=f["function"],
                              sources=f["sources"], data_type=f["data_type"])
            created_field_ids.append(row["id"])

        # (1) BEFORE stamping: the real resolver must emit the live merge SQL.
        resolver.catalog().invalidate()
        unstamped = {
            f["slug"]: resolver.resolve(VirtualField(kind="virtual", name=f["slug"]))
            for f in _FIELDS
        }

        result = refresh_unified_values(
            db, resolver, repo.list_for_catalog(), slugs=_SLUGS)
        assert result["fields"] == len(_FIELDS), result

        # (2) AFTER the full refresh stamped materialized_at: the same resolver
        # (catalog invalidated) must emit the typed materialized reference.
        resolver.catalog().invalidate()
        stamped = {
            f["slug"]: resolver.resolve(VirtualField(kind="virtual", name=f["slug"]))
            for f in _FIELDS
        }

        yield {"db": db, "resolver": resolver, "refresh": result,
               "unstamped": unstamped, "stamped": stamped}

    finally:
        for slug in _SLUGS:
            remove_unified_value(db, slug)
        for field_id in created_field_ids:
            repo.delete(field_id)
        if inserted_asset_ids:
            db.execute_query(
                "DELETE FROM assets WHERE id = ANY(%(ids)s::uuid[])",
                {"ids": inserted_asset_ids}, fetch=False)


@pytest.mark.integration
@pytest.mark.parametrize("field", _FIELDS, ids=lambda f: f["slug"])
def test_materialized_value_equals_the_live_expression(parity_setup, field):
    db, resolver = parity_setup["db"], parity_setup["resolver"]
    live = build_merge_sql(resolver, field["function"], field["sources"])
    stored = _typed_read(field)
    if field["function"] == "union":
        comparison = f"{_sorted_array(live)} IS DISTINCT FROM {_sorted_array(stored)}"
    else:
        comparison = f"({live}) IS DISTINCT FROM {stored}"
    rows = db.execute_query(
        f"SELECT count(*) AS n FROM assets WHERE {comparison}") or []
    mismatches = int(rows[0]["n"])
    print(f"\n{field['slug']}: {mismatches} mismatches")
    assert mismatches == 0


@pytest.mark.integration
@pytest.mark.parametrize("field", _FIELDS, ids=lambda f: f["slug"])
def test_unstamped_resolver_emits_the_live_expression(parity_setup, field):
    """Read flip, gate closed: before stamping, where_expr() IS the live SQL."""
    resolver = parity_setup["resolver"]
    rf = parity_setup["unstamped"][field["slug"]]
    live = build_merge_sql(resolver, field["function"], field["sources"])
    assert rf.where_expr() == live
    expected_kind = "virtual_array" if field["function"] == "union" else "virtual_expr"
    assert rf.sql_kind == expected_kind


@pytest.mark.integration
@pytest.mark.parametrize("field", _FIELDS, ids=lambda f: f["slug"])
def test_stamped_resolver_emits_the_typed_reference(parity_setup, field):
    """Read flip, gate open: after stamping, where_expr() IS the typed reference."""
    rf = parity_setup["stamped"][field["slug"]]
    assert rf.where_expr() == _typed_read(field)
    expected_kind = "virtual_array" if field["function"] == "union" else "virtual_expr"
    assert rf.sql_kind == expected_kind
    assert rf.is_native_array() is (field["function"] == "union")
    assert rf.is_jsonb_sourced() is False


@pytest.mark.integration
@pytest.mark.parametrize("field", _FIELDS, ids=lambda f: f["slug"])
def test_flipped_where_expr_matches_live_on_every_row(parity_setup, field):
    """The flipped resolver output returns identical results to the live SQL."""
    db, resolver = parity_setup["db"], parity_setup["resolver"]
    live = build_merge_sql(resolver, field["function"], field["sources"])
    flipped = parity_setup["stamped"][field["slug"]].where_expr()
    if field["function"] == "union":
        comparison = f"{_sorted_array(live)} IS DISTINCT FROM {_sorted_array(flipped)}"
    else:
        comparison = f"({live}) IS DISTINCT FROM {flipped}"
    rows = db.execute_query(
        f"SELECT count(*) AS n FROM assets WHERE {comparison}") or []
    mismatches = int(rows[0]["n"])
    print(f"\n{field['slug']}: {mismatches} mismatches (live vs flipped resolver)")
    assert mismatches == 0


@pytest.mark.integration
def test_full_refresh_stamps_materialized_at(parity_setup):
    rows = parity_setup["db"].execute_query(
        "SELECT slug, materialized_at FROM unified_fields WHERE slug = ANY(%(slugs)s)",
        {"slugs": _SLUGS}) or []
    assert len(rows) == len(_SLUGS)
    assert all(r["materialized_at"] is not None for r in rows)


@pytest.mark.integration
def test_refresh_reported_the_table(parity_setup):
    total = (parity_setup["db"].execute_query(
        "SELECT count(*) AS n FROM assets") or [{}])[0]["n"]
    assert parity_setup["refresh"]["rows"] == int(total)
    assert parity_setup["refresh"]["chunks"] == 1
