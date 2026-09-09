# SPDX-License-Identifier: AGPL-3.0-or-later
"""refresh_unified_values / remove_unified_values — SQL shape with a fake DB.

The fake never evaluates the SQL, so these tests assert the generated statement
text (and params), not just the returned dict. The resolver is a stub whose
ResolvedFields render ``expr(<encoded source>)`` — build_merge_sql (the single
source of truth for merge semantics) composes them into the real expression.

The fake also answers the migration-149 information_schema probe (see
``_FakeDb.present``): the probe conversation is recorded separately from the
materialization SQL, and the pre-149 degrade paths are covered at the bottom.
"""

import logging

import pytest
from unittest.mock import MagicMock

from src.modules.fields.encoding import encode
from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.materialize import (
    column_present,
    is_materializable,
    materialization_available,
    outside_request_scope,
    refresh_for_sources,
    refresh_unified_values,
    remove_unified_value,
)


def _resolver():
    """(resolver, catalog) — resolve() renders each source as expr(<key>)."""
    catalog = MagicMock()
    resolver = MagicMock()
    resolver.catalog.return_value = catalog

    def _resolve(fid):
        rf = MagicMock()
        rf.sql_kind = "expr"
        rf.data_type = FieldDataType.STRING
        rf.is_native_array.return_value = False
        rf.where_expr.return_value = f"expr({encode(fid)})"
        return rf

    resolver.resolve.side_effect = _resolve
    return resolver, catalog


def _def(slug, function="coalesce", sources=("static:Category", "ext:A:x")):
    return {"slug": slug, "name": slug, "function": function,
            "sources": list(sources), "data_type": FieldDataType.STRING,
            "materialized_at": None}


class _FakeDb:
    """Records statements; answers information_schema probes per `present`.

    `present` is True/False for all columns, or a dict of (table, column) ->
    bool for partial presence. Probes are recorded separately in `probes` so
    `calls` holds exactly the materialization SQL — indexing it is unaffected
    by the probe, the way a real database's conversation would read.
    """

    def __init__(self, present=True, result=None, fail_on=None):
        self.present = present
        self.result = [{"n": 7}] if result is None else result
        self.fail_on = fail_on
        self.calls = []    # non-probe (sql, params)
        self.probes = []   # probe (sql, params)

    def execute_query(self, sql, params=None):
        if "information_schema.columns" in sql:
            self.probes.append((sql, params))
            present = self.present
            if isinstance(present, dict):
                present = present.get((params["t"], params["c"]), False)
            return [{"?column?": 1}] if present else []
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        return list(self.result)


def _db(present=True, **kwargs):
    return _FakeDb(present=present, **kwargs)


# ── is_materializable ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sources", [
    ["static:Category", "posture_state:secured"],
    ["posture_output:monitored"],
    ["tag:critical", "ext:A:x"],
    ["posture:secured:state"],  # logical spelling, defensive
])
def test_posture_and_tag_sourced_fields_are_not_materializable(sources):
    assert not is_materializable(_def("f", sources=sources))


def test_static_ext_group_enrichment_sources_are_materializable():
    assert is_materializable(_def("f", sources=["static:Category", "ext:A:x"]))
    assert is_materializable(_def("f", sources=["ext:enrichment:legal_entity", "group:g:a"]))


# ── The UPDATE ────────────────────────────────────────────────────────────────

def test_update_merges_each_slugs_merge_sql_into_the_bag():
    db = _db()
    resolver, _ = _resolver()
    out = refresh_unified_values(db, resolver, [
        _def("lentity"),
        _def("owner_u", function="union"),
    ])

    sql, params = db.calls[0]
    assert "UPDATE assets" in sql
    assert "SET unified_values = COALESCE(unified_values, '{}'::jsonb)" in sql
    assert "|| jsonb_build_object(" in sql
    # Each slug appears as a string literal key next to its own merge expression.
    assert "'lentity', COALESCE(NULLIF(expr(static:Category), ''), NULLIF(expr(ext:A:x), ''))" in sql
    assert "'owner_u', ARRAY(SELECT DISTINCT v FROM unnest(" in sql
    assert "WHERE id = ANY" not in sql
    assert params is None or "ids" not in (params or {})
    assert out == {"fields": 2, "rows": 7, "chunks": 1}


def test_chunking_at_51_fields_emits_two_statements():
    db = _db()
    resolver, _ = _resolver()
    defs = [_def(f"f{i:02d}") for i in range(51)]

    out = refresh_unified_values(db, resolver, defs)

    updates = [c for c in db.calls if "UPDATE assets" in c[0]]
    assert len(updates) == 2
    # "'<slug>', COALESCE(" appears once per key/value pair in the chunk.
    assert updates[0][0].count("', COALESCE(") == 50
    assert updates[1][0].count("', COALESCE(") == 1
    assert "'f00'" in updates[0][0] and "'f49'" in updates[0][0]
    assert "'f50'" in updates[1][0] and "'f49'" not in updates[1][0]
    assert out["chunks"] == 2
    assert out["fields"] == 51


def test_asset_ids_add_a_where_clause_with_canonical_lowercase_ids():
    db = _db()
    resolver, _ = _resolver()
    refresh_unified_values(db, resolver, [_def("lentity")], asset_ids=[
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",          # uppercase
        "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}",        # braced duplicate
        "11111111-2222-3333-4444-555555555555",
    ])

    sql, params = db.calls[0]
    assert "WHERE id = ANY(%(ids)s::uuid[])" in sql
    assert params["ids"] == [
        "11111111-2222-3333-4444-555555555555",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]


def test_a_malformed_asset_id_raises_instead_of_being_dropped():
    db = _db()
    resolver, _ = _resolver()
    with pytest.raises(ValueError):
        refresh_unified_values(db, resolver, [_def("lentity")], asset_ids=["not-a-uuid"])
    # Input validation precedes any SQL — not even the probe has run.
    assert db.calls == [] and db.probes == []


def test_posture_and_tag_sourced_definitions_are_skipped():
    db = _db()
    resolver, _ = _resolver()
    out = refresh_unified_values(db, resolver, [
        _def("lentity"),
        _def("posture_mix", sources=["static:Category", "posture_state:secured"]),
        _def("tag_mix", sources=["static:Category", "tag:critical"]),
    ])

    sql = db.calls[0][0]
    assert "'lentity'" in sql
    assert "posture_mix" not in sql
    assert "tag_mix" not in sql
    assert out["fields"] == 1


def test_nothing_materializable_means_no_sql_at_all():
    db = _db()
    resolver, catalog = _resolver()
    out = refresh_unified_values(db, resolver, [
        _def("p", sources=["posture_state:secured", "static:Category"]),
    ])
    assert out == {"fields": 0, "rows": 0, "chunks": 0}
    # The no-work early exit precedes any SQL — not even the probe has run.
    assert db.calls == [] and db.probes == []
    catalog.invalidate.assert_not_called()


def test_slugs_filter_selects_a_subset():
    db = _db()
    resolver, _ = _resolver()
    out = refresh_unified_values(db, resolver,
                                 [_def("a"), _def("b"), _def("c")],
                                 slugs=["b"])
    sql = db.calls[0][0]
    assert "'b'" in sql and "'a'" not in sql and "'c'" not in sql
    assert out["fields"] == 1


# ── The stamp ─────────────────────────────────────────────────────────────────

def test_a_full_assets_refresh_stamps_materialized_at():
    db = _db()
    resolver, _ = _resolver()
    refresh_unified_values(db, resolver, [_def("lentity"), _def("owner_u", function="union")])

    stamps = [c for c in db.calls if "UPDATE unified_fields" in c[0]]
    assert len(stamps) == 1
    sql, params = stamps[0]
    assert "SET materialized_at = NOW()" in sql
    assert "WHERE slug = ANY(%(slugs)s)" in sql
    assert params["slugs"] == ["lentity", "owner_u"]


def test_a_per_asset_refresh_does_not_stamp():
    db = _db()
    resolver, _ = _resolver()
    refresh_unified_values(db, resolver, [_def("lentity")],
                           asset_ids=["11111111-2222-3333-4444-555555555555"])
    assert not [c for c in db.calls if "UPDATE unified_fields" in c[0]]


def test_stamp_false_and_non_assets_tables_do_not_stamp():
    db = _db()
    resolver, _ = _resolver()
    refresh_unified_values(db, resolver, [_def("lentity")], stamp=False)
    refresh_unified_values(db, resolver, [_def("lentity")], table="assets_history")
    assert not [c for c in db.calls if "UPDATE unified_fields" in c[0]]
    history_updates = [c for c in db.calls if "UPDATE assets_history" in c[0]]
    assert len(history_updates) == 1


# ── Catalog invalidation ──────────────────────────────────────────────────────

def test_the_catalog_is_invalidated_before_the_merge_sql_is_built():
    """A stale catalog snapshot bakes values from a degraded source resolution
    (build_merge_sql silently skips unresolvable sources), so invalidation must
    happen strictly before any resolve() call."""
    db = _db()
    resolver, catalog = _resolver()
    order = []
    catalog.invalidate.side_effect = lambda: order.append("invalidate")
    real_resolve = resolver.resolve.side_effect
    resolver.resolve.side_effect = lambda fid: (order.append("resolve"), real_resolve(fid))[1]

    refresh_unified_values(db, resolver, [_def("lentity")])

    assert order[0] == "invalidate"
    assert "resolve" in order


# ── Slug literal escaping ─────────────────────────────────────────────────────

def test_a_single_quote_in_a_slug_is_escaped_in_the_sql_literal():
    db = _db()
    resolver, _ = _resolver()
    refresh_unified_values(db, resolver, [_def("o'brien")])

    sql = db.calls[0][0]
    assert "jsonb_build_object('o''brien'," in sql


# ── remove_unified_value ──────────────────────────────────────────────────────

def test_remove_unified_value_deletes_the_key_only_where_present():
    db = _db(result=[{"?column?": 1}, {"?column?": 1}, {"?column?": 1}])

    n = remove_unified_value(db, "lentity")

    sql, params = db.calls[0]
    assert "UPDATE assets SET unified_values = unified_values - %(slug)s" in sql
    assert "WHERE unified_values ? %(slug)s" in sql
    assert params == {"slug": "lentity"}
    assert n == 3


# ── refresh_for_sources (#1405 part 3) ────────────────────────────────────────

def test_refresh_for_sources_selects_only_prefix_matching_slugs_and_stamps():
    db = _db()
    resolver, _ = _resolver()
    out = refresh_for_sources(db, resolver, [
        _def("legal_entity", sources=["ext:enrichment:legal_entity", "static:Category"]),
        _def("grouped", sources=["group:s1:site", "static:Category"]),
        _def("plain", sources=["static:Category"]),
    ], ("ext:enrichment:",))

    sql = db.calls[0][0]
    assert "'legal_entity'" in sql
    assert "'grouped'" not in sql and "'plain'" not in sql
    assert out["fields"] == 1
    # full-table refresh → the selected fields are stamped
    stamps = [c for c in db.calls if "UPDATE unified_fields" in c[0]]
    assert len(stamps) == 1
    assert "SET materialized_at = NOW()" in stamps[0][0]
    assert stamps[0][1]["slugs"] == ["legal_entity"]


def test_refresh_for_sources_skips_non_materializable_fields_even_when_prefixed():
    db = _db()
    resolver, _ = _resolver()
    out = refresh_for_sources(db, resolver, [
        _def("mixed", sources=["ext:enrichment:x", "posture_state:secured"]),
    ], ("ext:enrichment:",))

    assert out == {"fields": 0, "rows": 0, "chunks": 0}
    assert db.calls == [] and db.probes == []


def test_refresh_for_sources_with_no_matching_source_runs_no_sql():
    db = _db()
    resolver, catalog = _resolver()
    out = refresh_for_sources(db, resolver, [_def("plain")], ("group:",))

    assert out == {"fields": 0, "rows": 0, "chunks": 0}
    assert db.calls == [] and db.probes == []
    catalog.invalidate.assert_not_called()


def test_refresh_for_sources_failure_clears_the_stamps_and_never_raises():
    db = _db(fail_on="UPDATE assets", result=[{"slug": "legal_entity"}])
    resolver, _ = _resolver()

    out = refresh_for_sources(db, resolver, [
        _def("legal_entity", sources=["ext:enrichment:legal_entity"]),
        _def("plain"),
    ], ("ext:enrichment:",))

    assert out == {"fields": 0, "rows": 0, "chunks": 0}
    clear = db.calls[1]
    assert "UPDATE unified_fields" in clear[0]
    assert "SET materialized_at = NULL" in clear[0]
    assert clear[1]["slugs"] == ["legal_entity"]


# ── outside_request_scope (#1428 round 2) ─────────────────────────────────────
# Under Flask a request is one scope/transaction, poisoned (rolled back at
# teardown) by the first DB error inside it. Hooks must commit the caller's
# writes FIRST, then run outside the scope, then re-balance it for teardown.

def test_outside_request_scope_commits_then_runs_then_rebalances():
    cm = MagicMock()
    cm.is_scope_active.return_value = True
    order = MagicMock()
    order.attach_mock(cm.end_scope, "end_scope")
    order.attach_mock(cm.begin_scope, "begin_scope")

    with outside_request_scope(cm):
        order.body()

    assert [c[0] for c in order.mock_calls] == ["end_scope", "body", "begin_scope"]


def test_outside_request_scope_re_begins_the_scope_even_when_the_body_raises():
    cm = MagicMock()
    cm.is_scope_active.return_value = True

    with pytest.raises(RuntimeError):
        with outside_request_scope(cm):
            raise RuntimeError("hook boom")

    cm.end_scope.assert_called_once_with()
    cm.begin_scope.assert_called_once_with()


def test_outside_request_scope_is_a_noop_when_no_scope_is_active():
    """Scheduler/CLI/tests: no request scope — the body runs as unwrapped."""
    cm = MagicMock()
    cm.is_scope_active.return_value = False

    with outside_request_scope(cm):
        pass

    cm.end_scope.assert_not_called()
    cm.begin_scope.assert_not_called()


# ── Pre-migration-149 tolerance ───────────────────────────────────────────────
# The probe: True cached for the process, False re-probed; the helpers degrade
# to a no-op with one WARNING per process instead of raising UndefinedColumn.

def test_probe_sql_asks_information_schema_for_the_current_schema():
    db = _db(present=False)
    assert column_present(db, "assets", "unified_values") is False
    sql, params = db.probes[0]
    assert "SELECT 1 FROM information_schema.columns" in sql
    assert "table_schema = current_schema()" in sql
    assert "table_name = %(t)s AND column_name = %(c)s" in sql
    assert params == {"t": "assets", "c": "unified_values"}


def test_a_present_probe_is_cached_for_the_process():
    db = _db(present=True)
    assert column_present(db, "assets", "unified_values") is True
    assert column_present(db, "assets", "unified_values") is True
    assert len(db.probes) == 1   # second call answered from the cache


def test_an_absent_probe_is_reprobed_on_the_next_call():
    """Columns are never dropped, so True is cacheable forever; False is not —
    the operator may apply migration 149 without restarting the API, and the
    next call must see it."""
    db = _db(present=False)
    assert column_present(db, "assets", "unified_values") is False
    assert column_present(db, "assets", "unified_values") is False
    assert len(db.probes) == 2   # no caching of a negative answer


def test_materialization_available_is_false_without_assets_unified_values():
    db = _db(present={("unified_fields", "materialized_at"): True})
    assert materialization_available(db) is False


def test_materialization_available_is_false_without_materialized_at():
    db = _db(present={("assets", "unified_values"): True})
    assert materialization_available(db) is False


def test_materialization_available_is_true_with_both_columns():
    db = _db(present=True)
    assert materialization_available(db) is True


def test_refresh_on_a_pre149_database_issues_no_update_and_reports_the_skip():
    db = _db(present=False)
    resolver, _ = _resolver()

    out = refresh_unified_values(db, resolver, [_def("lentity")])

    assert out == {"fields": 0, "rows": 0, "chunks": 0,
                   "skipped": "migration_149_pending"}
    assert db.calls == []          # no UPDATE, no stamp — probe SQL only
    assert len(db.probes) == 1     # assets.unified_values False short-circuits


def test_remove_unified_value_on_a_pre149_database_is_a_noop():
    db = _db(present=False)
    assert remove_unified_value(db, "lentity") == 0
    assert db.calls == []


def test_the_pre149_warning_is_emitted_once_per_process_not_per_call(caplog):
    db = _db(present=False)
    resolver, _ = _resolver()
    with caplog.at_level(logging.WARNING):
        refresh_unified_values(db, resolver, [_def("lentity")])
        refresh_unified_values(db, resolver, [_def("lentity")])
        remove_unified_value(db, "lentity")
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "migration 149" in r.message]
    assert len(warnings) == 1
