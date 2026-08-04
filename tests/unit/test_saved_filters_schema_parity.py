# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards the dual-write invariant for the saved_filters tables.

A fresh install bootstraps the schema from schema-full.sql and then marks
every migration as applied WITHOUT running it (deploy/lib/migrate.sh
bootstrap_if_empty). So any table that exists only in the migration — and
not in schema-full.sql — is silently missing on every fresh install. This
test fails until both files declare both tables.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "src/database/migrations/113_create_saved_filters.sql"
_SCHEMA_FULL = _ROOT / "src/database/schema-full.sql"


def test_migration_creates_both_tables():
    sql = _MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS saved_filters" in sql
    assert "CREATE TABLE IF NOT EXISTS saved_filter_grants" in sql


def test_schema_full_declares_both_tables():
    sql = _SCHEMA_FULL.read_text()
    assert "CREATE TABLE public.saved_filters" in sql
    assert "CREATE TABLE public.saved_filter_grants" in sql
