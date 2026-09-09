#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backfill unified_values into pre-migration-149 assets_history rows.

Migration 149 added ``assets_history.unified_values``; rows archived before it
landed (or while it was unapplied) have NULL, so history trend charts on
unified fields lose those buckets. This script recomputes the stored values
with the same writer the pipeline uses — ``refresh_unified_values(...,
table="assets_history", stamp=False)``.

Discovery is chunked by ``generation_run_id`` (``--batch-size`` runs per
chunk, in run order) — but run-chunking alone does NOT bound statement size:
the per-asset UPDATE rewrites EVERY history snapshot of the assets it names
(``WHERE id = ANY(...)``), so on a stable fleet the first run chunk would
already rewrite almost the whole table. The real bound is
``--assets-per-statement`` (default 500): each chunk's newly-seen asset ids
are split into groups of at most N, one ``refresh_unified_values`` call per
group, so each UPDATE touches ≤ N assets × their snapshot count.

The merge expression is pure and row-local, so recomputing a row yields the
value that run would have stored. Only rows with NULL ``unified_values`` drive
asset selection; each asset is refreshed at most once (later chunks skip
assets an earlier chunk already covered, because the per-asset UPDATE rewrites
all of that asset's snapshots — idempotently). History writes never stamp
``unified_fields.materialized_at``.

Each statement calls ``catalog().invalidate()``, which bumps the shared Redis
catalog stamp when REDIS_URL is exported — run the backfill without REDIS_URL
exported, or accept one catalog rebuild per API worker per statement.

Dry-run is the default; pass --yes to execute. Requires DATABASE_URL. Refuses
to run (exit 1) while migration 149 is not applied.

Usage:
    DATABASE_URL="postgresql://..." .venv/bin/python scripts/backfill_unified_values_history.py
    DATABASE_URL="postgresql://..." .venv/bin/python scripts/backfill_unified_values_history.py --yes --batch-size 1 --assets-per-statement 500
    DATABASE_URL="postgresql://..." .venv/bin/python scripts/backfill_unified_values_history.py --yes --slug legal_entity
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path (scripts/ is not a package)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.modules.fields.unified.materialize import (  # noqa: E402
    is_materializable,
    materialization_available,
    refresh_unified_values,
)
from src.modules.fields.unified.repository import UnifiedFieldsRepository  # noqa: E402
from src.modules.shared.field_resolver_factory import build_field_resolver  # noqa: E402


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Backfill assets_history.unified_values for pre-migration-149 rows.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="Report what would be updated without writing (default).")
    mode.add_argument("--yes", dest="dry_run", action="store_false",
                      help="Execute the backfill.")
    parser.add_argument("--slug", action="append", default=None, metavar="SLUG",
                        help="Restrict to this unified-field slug (repeatable).")
    parser.add_argument("--batch-size", type=int, default=1, metavar="N",
                        help="Generation runs per discovery chunk (default: 1).")
    parser.add_argument("--assets-per-statement", type=int, default=500, metavar="N",
                        help="Max assets a single UPDATE may rewrite "
                             "(default: 500).")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if args.assets_per_statement < 1:
        parser.error("--assets-per-statement must be >= 1")
    return args


def _null_row_run_ids(db):
    """generation_run_ids that still have NULL-unified_values rows, ordered."""
    rows = db.execute_query(
        "SELECT DISTINCT generation_run_id FROM assets_history "
        "WHERE unified_values IS NULL AND generation_run_id IS NOT NULL "
        "ORDER BY generation_run_id")
    return [str(r["generation_run_id"]) for r in (rows or [])]


def _has_null_run_rows(db):
    """True if rows archived without a generation_run_id need the backfill."""
    rows = db.execute_query(
        "SELECT count(*) AS n FROM assets_history "
        "WHERE unified_values IS NULL AND generation_run_id IS NULL")
    return bool(rows and int(rows[0]["n"]))


def _chunk_asset_ids(db, run_id_chunk):
    """Asset ids with at least one NULL-unified_values row in these runs."""
    if run_id_chunk is None:
        rows = db.execute_query(
            "SELECT DISTINCT id FROM assets_history "
            "WHERE unified_values IS NULL AND generation_run_id IS NULL")
    else:
        rows = db.execute_query(
            "SELECT DISTINCT id FROM assets_history "
            "WHERE unified_values IS NULL "
            "AND generation_run_id = ANY(%(runs)s::uuid[])",
            {"runs": run_id_chunk})
    return [str(r["id"]) for r in (rows or [])]


def run(db, *, slugs=None, batch_size=1, assets_per_statement=500,
        dry_run=True, out=print) -> int:
    """Backfill (or, with dry_run, report) assets_history.unified_values.

    Returns a process exit code: 0 on success, 1 on refusal or failure.
    """
    if not materialization_available(db):
        out("ERROR: migration 149 is not applied on this database — "
            "assets_history.unified_values does not exist. Apply the migration first.")
        return 1

    defs = [d for d in UnifiedFieldsRepository(db).list_for_catalog()
            if is_materializable(d)]
    if slugs:
        known = {d["slug"] for d in defs}
        unknown = sorted(set(slugs) - known)
        if unknown:
            out(f"ERROR: unknown or non-materializable unified field slug(s): "
                f"{', '.join(unknown)}")
            return 1
        defs = [d for d in defs if d["slug"] in set(slugs)]
    if not defs:
        out("No materializable unified fields defined — nothing to backfill.")
        return 0

    run_ids = _null_row_run_ids(db)
    chunks = [run_ids[i:i + batch_size] for i in range(0, len(run_ids), batch_size)]
    if _has_null_run_rows(db):
        chunks.append(None)  # rows archived without a generation_run_id
    if not chunks:
        out("No assets_history rows with NULL unified_values — nothing to backfill.")
        return 0

    mode = "DRY-RUN (no writes)" if dry_run else "EXECUTING"
    out(f"Backfilling {len(defs)} unified field(s) "
        f"({', '.join(d['slug'] for d in defs)}) over {len(chunks)} chunk(s) — {mode}")

    resolver = build_field_resolver(db)
    seen = set()
    total_rows = 0
    for i, chunk in enumerate(chunks, 1):
        label = "unlabelled rows" if chunk is None else f"{len(chunk)} run(s)"
        new_ids = sorted(set(_chunk_asset_ids(db, chunk)) - seen)
        if not new_ids:
            out(f"chunk {i}/{len(chunks)} ({label}): no new assets — skipped")
            continue
        # The per-asset UPDATE rewrites every snapshot of the assets it names,
        # so the statement bound comes from splitting by asset count — one
        # refresh call (one UPDATE) per group of --assets-per-statement ids.
        groups = [new_ids[j:j + assets_per_statement]
                  for j in range(0, len(new_ids), assets_per_statement)]
        for g, group in enumerate(groups, 1):
            prefix = (f"chunk {i}/{len(chunks)} ({label}), "
                      f"statement {g}/{len(groups)}")
            if dry_run:
                rows = db.execute_query(
                    "SELECT count(*) AS n FROM assets_history "
                    "WHERE id = ANY(%(ids)s::uuid[])",
                    {"ids": group})
                n = int(rows[0]["n"]) if rows else 0
                out(f"{prefix}: would refresh {n} history row(s) "
                    f"across {len(group)} asset(s)")
            else:
                result = refresh_unified_values(
                    db, resolver, defs, slugs=slugs, asset_ids=group,
                    table="assets_history", stamp=False)
                n = result["rows"]
                out(f"{prefix}: refreshed {n} history row(s) "
                    f"across {len(group)} asset(s) "
                    f"in {result['chunks']} UPDATE(s)")
            total_rows += n
        seen.update(new_ids)

    verb = "would be" if dry_run else "were"
    out(f"Done: {total_rows} history row(s) {verb} refreshed "
        f"across {len(seen)} asset(s).")
    if dry_run:
        out("Re-run with --yes to execute.")
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL environment variable not set")
        print("  Usage: DATABASE_URL='postgresql://...' "
              ".venv/bin/python scripts/backfill_unified_values_history.py [--yes]")
        return 1
    from src.database.postgres_adapter import PostgreSQLAdapter
    try:
        return run(PostgreSQLAdapter(), slugs=args.slug,
                   batch_size=args.batch_size,
                   assets_per_statement=args.assets_per_statement,
                   dry_run=args.dry_run)
    except Exception as e:
        print(f"ERROR: backfill failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
