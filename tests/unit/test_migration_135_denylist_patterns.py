# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks on migration 135 (merge-denylist pattern entries, #991).

Two things are guarded here:

1. The migration itself -- idempotent, transactional, doesn't self-record, and
   declares every constraint the encoder/matcher assume (see
   test_migration_131_merge_denylist.py for the same shape of assertions).

2. The schema-full.sql dual-write. A fresh install bootstraps from
   schema-full.sql and then marks every migration applied WITHOUT running it
   (deploy/lib/migrate.sh bootstrap_if_empty), so a column that exists only in
   the migration is silently missing on every fresh install. Same invariant, and
   same reason, as test_saved_filters_schema_parity.py -- and b63f0bc0 exists
   because a CHECK was once added to a migration and not to schema-full.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "src/database/migrations/135_add_denylist_match_types.sql"
_SCHEMA_FULL = _ROOT / "src/database/schema-full.sql"


def _migration_sql() -> str:
    return _MIGRATION.read_text()


def _merge_denylist_ddl() -> str:
    """The CREATE TABLE public.merge_denylist block from schema-full.sql."""
    sql = _SCHEMA_FULL.read_text()
    start = sql.index("CREATE TABLE public.merge_denylist")
    return sql[start:sql.index("\n);", start)]


def test_migration_exists_and_is_idempotent():
    sql = _migration_sql()
    assert "ADD COLUMN IF NOT EXISTS match_type" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    # Must NOT self-record -- the deploy runner owns schema_migrations.
    assert "schema_migrations" not in sql
    # Re-adding a constraint that already exists errors, so every ADD CONSTRAINT
    # has to be preceded by a DROP ... IF EXISTS (the migration 133 idiom).
    assert sql.count("DROP CONSTRAINT IF EXISTS") == sql.count("ADD CONSTRAINT")


def test_existing_rows_are_backfilled_as_exact():
    """The denylist is live nowhere yet, but the table is non-empty eventually:
    a pre-135 row is an exact entry by definition and must stay matchable. The
    NOT NULL DEFAULT is what backfills it -- without the default the ADD COLUMN
    would fail outright on a non-empty table."""
    sql = _migration_sql()
    assert "match_type text NOT NULL DEFAULT 'exact'" in sql


def test_constrains_match_type():
    sql = _migration_sql()
    assert "CHECK (match_type IN ('exact', 'prefix', 'suffix', 'range'))" in sql


def test_constrains_legal_type_match_pairs():
    """Mirrors MATCH_TYPES_BY_TYPE in encoding.py: mac takes exact/prefix/range,
    hostname+fqdn take exact/prefix/suffix, serial stays exact-only. The DB is
    the backstop if a future caller bypasses the encoder."""
    sql = _migration_sql()
    assert "identifier_type = 'mac' AND match_type IN ('exact', 'prefix', 'range')" in sql
    assert "identifier_type IN ('hostname', 'fqdn')" in sql
    assert "match_type IN ('exact', 'prefix', 'suffix')" in sql
    assert "identifier_type = 'serial' AND match_type = 'exact'" in sql


def test_range_bounds_are_constrained():
    """Bounds exist iff match_type='range', and a reversed range (range_end <
    range_start) can never match anything, so the DB refuses it."""
    sql = _migration_sql()
    assert "range_start bigint" in sql
    assert "range_end bigint" in sql
    assert "range_end >= range_start" in sql
    assert "match_type <> 'range' AND range_start IS NULL AND range_end IS NULL" in sql


def test_unique_key_includes_match_type():
    """create_idempotent()'s ON CONFLICT infers this constraint by column list --
    if the two drift, every idempotent insert (i.e. every asset split) raises."""
    sql = _migration_sql()
    assert "UNIQUE (identifier_type, identifier_value, match_type)" in sql


def test_schema_full_declares_the_new_columns():
    ddl = _merge_denylist_ddl()
    assert "match_type       text NOT NULL DEFAULT 'exact'" in ddl
    assert "range_start      bigint" in ddl
    assert "range_end        bigint" in ddl


def test_schema_full_declares_the_new_constraints():
    ddl = _merge_denylist_ddl()
    assert "merge_denylist_match_type_check" in ddl
    assert "merge_denylist_type_match_check" in ddl
    assert "merge_denylist_range_bounds_check" in ddl
    assert "UNIQUE (identifier_type, identifier_value, match_type)" in ddl
