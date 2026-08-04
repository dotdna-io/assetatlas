# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static parity check: every integration_technologies row a migration INSERTs
must also exist in schema-full.sql's COPY block.

Fresh installs seed integration_technologies from schema-full.sql, then
deploy/lib/migrate.sh's bootstrap_if_empty marks every migration as applied
WITHOUT running it. So a seed row that lives only in a migration silently
misses fresh installs (and one only in schema-full misses upgrades). This guard
runs without a database, unlike the live-DB coverage in
tests/integration/test_via_siem_catalog.py. (CI runs pytest warning-only, so
this primarily protects local runs and documents the dual-write invariant.)
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "src" / "database" / "migrations"
SCHEMA_FULL = PROJECT_ROOT / "src" / "database" / "schema-full.sql"

# First quoted value of a VALUES tuple, e.g. ('windows-events', 'Windows ...
_TUPLE_ID = re.compile(r"\(\s*'([a-z0-9-]+)'")
# The INSERT ... VALUES <tuples>; statement(s) that seed the catalog.
_INSERT = re.compile(
    r"INSERT\s+INTO\s+integration_technologies\b.*?VALUES(.*?);",
    re.S | re.I,
)


def _ids_inserted_by_migrations():
    ids = set()
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = f.read_text()
        for stmt in _INSERT.finditer(text):
            ids.update(_TUPLE_ID.findall(stmt.group(1)))
    return ids


def _copy_block_ids(text):
    lines = text.splitlines()
    start = next(
        i for i, ln in enumerate(lines)
        if ln.startswith("COPY public.integration_technologies ")
    )
    ids = set()
    for ln in lines[start + 1:]:
        if ln.strip() == r"\.":
            break
        ids.add(ln.split("\t", 1)[0])
    return ids


def test_integration_technologies_seed_is_dual_written():
    """Rows a migration INSERTs into integration_technologies must be mirrored
    in schema-full.sql's COPY block, or fresh installs silently lack them."""
    migration_ids = _ids_inserted_by_migrations()
    # Guard against the regex/layout drifting into a silent no-op pass.
    assert migration_ids, (
        "parsed zero integration_technologies INSERT rows from migrations — "
        "the regex or migration layout drifted; this check would be a no-op."
    )
    schema_ids = _copy_block_ids(SCHEMA_FULL.read_text())
    missing = migration_ids - schema_ids
    assert not missing, (
        "integration_technologies rows added by a migration but missing from "
        f"schema-full.sql's COPY block (fresh installs would lack them): "
        f"{sorted(missing)}. Dual-write seed rows to both places — see "
        "deploy/lib/migrate.sh bootstrap_if_empty."
    )
