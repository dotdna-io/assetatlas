# SPDX-License-Identifier: AGPL-3.0-or-later
"""Materialize Unified Field values into the ``unified_values`` jsonb bag.

``FieldResolver._emit_sql()`` resolves every ``virtual:<slug>`` reference to
the full merge expression (``build_merge_sql``) at query time — on a real
deployment a 1,700-character COALESCE over dozens of ``extended_attributes``
JSONB extractions, re-evaluated per row on every filter, sort, chart, tag rule
and posture rule. Every unified function is pure and row-local, and the
pipeline rebuilds ``assets`` from scratch on every run, so the value can be
computed once per row during the rebuild and stored.

``build_merge_sql`` is the single source of truth for the merge semantics: the
UPDATE below embeds the very expression the read side would have evaluated, so
a stored value cannot drift from the live definition. Nothing here
re-implements merge semantics in Python, and nothing here changes what any
query returns — the read flip (emitting the materialized reference for fields
whose ``materialized_at`` stamp is set) is a separate change.

Fields with any ``posture:`` or ``tag:`` source are NEVER materialized: posture
state cycles with s17b's re-evaluation later in the same run, so a stored
value would be stale within the run that wrote it.

Deploy order does not matter: on a database where migration 149 has NOT been
applied yet (no ``assets.unified_values`` / ``unified_fields.materialized_at``),
every helper here degrades to the pre-149 behaviour — nothing stored, fields
resolve via the live merge expression — with one WARNING per process, never an
error. ``column_present`` probes information_schema; a True answer is cached
for the process lifetime (columns are never dropped), a False answer is
re-probed on the next call, so an operator applying the migration without
restarting the API flips the behaviour live.
"""

import logging
import re
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.modules.fields.unified.merge_sql import build_merge_sql
from src.modules.fields.unified.repository import UnifiedFieldsRepository

logger = logging.getLogger(__name__)

# Sources that make a field non-materializable (cycle after the refresh runs).
# Encoded prefixes: posture fields encode as posture_state:/posture_output:
# (src/modules/fields/encoding.py); "posture:" covers the logical spelling too.
_NON_MATERIALIZABLE_PREFIXES = ("posture:", "posture_state:", "posture_output:", "tag:")

# jsonb_build_object takes at most 100 arguments = 50 key/value pairs.
_CHUNK_PAIRS = 50

_TABLE_RE = re.compile(r"[a-z_][a-z0-9_]*")


def is_materializable(definition: Dict[str, Any]) -> bool:
    """False when any source is posture:- or tag:-sourced (see module docstring)."""
    return not any(
        str(s).startswith(_NON_MATERIALIZABLE_PREFIXES)
        for s in definition.get("sources", [])
    )


# ── Pre-migration-149 tolerance ───────────────────────────────────────────────
# Probe results: True cached per process per (table, column) — columns are
# never dropped, so once present they stay present. False is deliberately NOT
# cached: the operator may apply migration 149 without restarting the API, and
# the next call must see it (until then each degrade decision costs one tiny
# information_schema query per catalog rebuild / pipeline run).
_COLUMN_PRESENT: Dict[Tuple[str, str], bool] = {}
_MIGRATION_149_WARNED = False


def column_present(db_adapter, table: str, column: str) -> bool:
    """information_schema probe: does <table>.<column> exist in the current schema?"""
    key = (table, column)
    if _COLUMN_PRESENT.get(key):
        return True
    rows = db_adapter.execute_query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = %(t)s AND column_name = %(c)s",
        {"t": table, "c": column})
    if rows:
        _COLUMN_PRESENT[key] = True
        return True
    return False


def materialization_available(db_adapter) -> bool:
    """True when every migration-149 column the materialized path needs exists."""
    return (column_present(db_adapter, "assets", "unified_values")
            and column_present(db_adapter, "unified_fields", "materialized_at"))


def warn_migration_149_pending() -> None:
    """One WARNING per process (not per call) that migration 149 is pending."""
    global _MIGRATION_149_WARNED
    if _MIGRATION_149_WARNED:
        return
    _MIGRATION_149_WARNED = True
    logger.warning(
        "migration 149 is not applied on this database — unified-field "
        "materialization is disabled (nothing stored in unified_values; "
        "fields resolve via the live merge expression) until it lands")


def _slug_literal(slug: str) -> str:
    """SQL string literal for a slug. Slugs come from the DB, but escape anyway."""
    return "'" + str(slug).replace("'", "''") + "'"


def _canonical_ids(asset_ids: Sequence[Any]) -> List[str]:
    """Canonical lowercase 8-4-4-4-12 strings, deduped. Raises on a malformed id —
    psycopg2 renders uuid columns canonically, so a non-canonical lookup spelling
    would silently match nothing."""
    return sorted({str(uuid.UUID(str(a))) for a in asset_ids})


@contextmanager
def outside_request_scope(connection_manager):
    """Run a refresh hook only AFTER the caller's own writes are durable.

    Under Flask every request runs on ONE connection in ONE transaction
    (``factory.py`` ``before_request`` → ``begin_scope()``, teardown →
    ``end_scope()``), and the connection manager poisons that scope —
    ``errored``, rolled back at teardown — on the FIRST psycopg2 error raised
    inside it. A hook whose ``UPDATE assets …`` fails at the DB level
    (statement timeout on a big table, blocking behind the pipeline's
    TRUNCATE lock, deadlock) would otherwise cost the whole request's
    transaction: the user's own write silently undone while the response says
    success.

    Entering this context ends the active scope, COMMITTING the writes made
    so far (for the unified-field update path, the pre-write stamp clear and
    the definition UPDATE commit together here) and releasing the request
    connection; the body then runs on the per-call path, where every
    statement commits on its own. The guaranteed failure mode: a hook failure
    can only leave ``materialized_at`` stamps cleared — it can never roll the
    user's write back, and the stamp clear itself is durable. On exit the
    scope is re-begun so the request teardown's ``end_scope`` stays balanced
    and any later request work keeps sharing one connection.

    Re-entrant no-op when no scope is active (scheduler/CLI/tests): the body
    runs on the per-call path exactly as it would unwrapped. Call sites run
    at scope depth ≤ 1 — bulk writers (``_bulk_scope``) have already closed
    their nested scope before the hook fires, and the pipeline's enrichment
    stage passes ``refresh_materialized=False``, so no hook ever unwinds the
    pipeline's write scope — hence exactly one ``end_scope``/``begin_scope``
    pair. A failure of the commit inside ``end_scope`` itself propagates:
    that is the user's write failing, not the hook, and the request must see
    it rather than report a false success.
    """
    active = connection_manager.is_scope_active()
    if active:
        connection_manager.end_scope()
    try:
        yield
    finally:
        if active:
            connection_manager.begin_scope()


def refresh_unified_values(
    db_adapter,
    resolver,
    definitions: Sequence[Dict[str, Any]],
    *,
    slugs: Optional[Sequence[str]] = None,
    asset_ids: Optional[Sequence[Any]] = None,
    table: str = "assets",
    stamp: bool = True,
) -> Dict[str, Any]:
    """Recompute and store unified-field values for the given definitions.

    ``definitions`` are list_for_catalog rows (slug/function/sources/data_type).
    They are filtered to materializable ones (no posture:/tag: source); when
    ``slugs`` is given, only those slugs are refreshed. The catalog snapshot is
    invalidated immediately before building the merge SQL — a stale snapshot
    would bake values from a degraded source resolution.

    One UPDATE per chunk of 50 fields (jsonb_build_object's argument limit),
    each merging its chunk into the existing bag. ``rows`` in the result is the
    rowcount of a chunk statement — every chunk shares the same WHERE and
    therefore touches the same row set (reported once, not summed per chunk).

    When ``asset_ids`` is None and ``table`` is "assets" and ``stamp`` is true,
    the refreshed fields are stamped materialized_at = NOW() — a full-table
    refresh is the only refresh that makes the stored values trustworthy for
    every row.

    On a database without migration 149 no SQL is issued beyond the probe:
    returns ``{"fields": 0, "rows": 0, "chunks": 0, "skipped":
    "migration_149_pending"}`` with one WARNING per process.
    """
    if not _TABLE_RE.fullmatch(table):
        raise ValueError(f"unsafe table name: {table!r}")

    defs = [d for d in definitions if is_materializable(d)]
    if slugs is not None:
        wanted = set(slugs)
        defs = [d for d in defs if d["slug"] in wanted]
    if not defs:
        return {"fields": 0, "rows": 0, "chunks": 0}

    ids = _canonical_ids(asset_ids) if asset_ids is not None else None

    # Probe AFTER the no-work and input-validation early exits above so those
    # stay SQL-free, but BEFORE the catalog invalidation: on a pre-149 database
    # there is nothing to materialize, and invalidating here would drop every
    # worker's catalog snapshot (the shared version stamp is bumped) for no
    # reason. Past this point an UPDATE would follow, and on a pre-149 database
    # it would raise UndefinedColumn — degrade instead (the resolver keeps
    # serving the live merge expression).
    if not materialization_available(db_adapter):
        warn_migration_149_pending()
        return {"fields": 0, "rows": 0, "chunks": 0,
                "skipped": "migration_149_pending"}

    resolver.catalog().invalidate()

    pairs = []
    for d in defs:
        # The adapter always executes with params (`params or ()`), so psycopg2
        # interpolates: a literal `%` in a source/attribute name must be doubled.
        # _emit_sql never emits psycopg placeholders, so this is safe.
        expr = build_merge_sql(resolver, d["function"], d["sources"]).replace("%", "%%")
        pairs.append((_slug_literal(d["slug"]), expr))

    where = ""
    params: Optional[Dict[str, Any]] = None
    if ids is not None:
        # ::uuid[] — psycopg2 renders a list[str] as text[], and assets.id is uuid.
        where = "\n WHERE id = ANY(%(ids)s::uuid[])"
        params = {"ids": ids}

    rows = 0
    chunks = 0
    for i in range(0, len(pairs), _CHUNK_PAIRS):
        chunk = pairs[i:i + _CHUNK_PAIRS]
        obj_args = ", ".join(f"{lit}, {expr}" for lit, expr in chunk)
        sql = (
            "WITH upd AS (\n"
            f"UPDATE {table}\n"
            "   SET unified_values = COALESCE(unified_values, '{}'::jsonb)\n"
            f"       || jsonb_build_object({obj_args})"
            f"{where}\n"
            "RETURNING 1\n"
            ")\n"
            "SELECT count(*) AS n FROM upd"
        )
        result = db_adapter.execute_query(sql, params) or []
        rows = int(result[0]["n"]) if result else 0
        chunks += 1

    refreshed = [d["slug"] for d in defs]
    if ids is None and table == "assets" and stamp:
        UnifiedFieldsRepository(db_adapter).set_materialized_at(refreshed, True)

    logger.info(
        "refresh_unified_values: %d field(s) over %d row(s) of %s in %d statement(s)",
        len(defs), rows, table, chunks,
    )
    return {"fields": len(defs), "rows": rows, "chunks": chunks}


def refresh_for_sources(
    db_adapter,
    resolver,
    definitions: Sequence[Dict[str, Any]],
    prefixes: Sequence[str],
) -> Dict[str, Any]:
    """Full-table refresh of every materializable definition with at least one
    source under ``prefixes`` (e.g. ``"ext:enrichment:"``, ``"group:"``).

    Convenience for non-pipeline writers (enrichment re-apply via the API,
    integration-group membership changes) whose write changes what a whole
    class of sources resolves to. A full-table assets refresh, so the refreshed
    fields are stamped — the same trust the pipeline's own passes confer.

    Never raises: on failure the affected fields' materialized_at stamps are
    cleared (best effort) so the resolver falls back to the live merge
    expression until the next successful refresh, and the error is logged.
    """
    pref = tuple(prefixes)
    slugs = [
        d["slug"] for d in definitions
        if any(str(s).startswith(pref) for s in d.get("sources", []))
    ]
    if not slugs:
        return {"fields": 0, "rows": 0, "chunks": 0}
    try:
        return refresh_unified_values(db_adapter, resolver, definitions, slugs=slugs)
    except Exception:
        logger.warning(
            "refresh_for_sources%s: materialized refresh failed for %s — "
            "clearing materialized_at (live SQL until the next refresh)",
            pref, slugs, exc_info=True)
        try:
            UnifiedFieldsRepository(db_adapter).set_materialized_at(slugs, False)
        except Exception:
            logger.warning(
                "refresh_for_sources: could not clear materialized_at for %s",
                slugs, exc_info=True)
        return {"fields": 0, "rows": 0, "chunks": 0}


def remove_unified_value(db_adapter, slug: str) -> int:
    """Drop one slug from every assets.unified_values bag.

    Used by the unified-field delete hook: a deleted field's stored value would
    otherwise linger until the next pipeline run. Only rows actually carrying
    the key are rewritten. Returns rows updated.

    On a database without migration 149 there is no bag to clean: no SQL is
    issued beyond the probe, returns 0 with one WARNING per process.
    """
    if not materialization_available(db_adapter):
        warn_migration_149_pending()
        return 0
    rows = db_adapter.execute_query(
        "UPDATE assets SET unified_values = unified_values - %(slug)s "
        "WHERE unified_values ? %(slug)s RETURNING 1",
        {"slug": str(slug)})
    return len(rows or [])
