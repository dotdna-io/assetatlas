# SPDX-License-Identifier: AGPL-3.0-or-later
"""#906 — migration 126 and the schema-full.sql snapshot must stay in sync.

Fresh installs bootstrap from schema-full.sql and migrate.sh marks migration
126 as applied-without-running, so a table only in the migration would never
exist on a fresh install. These assertions guard that dual-write.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MIGRATION = ROOT / "src/database/migrations/126_create_alert_events.sql"
SCHEMA_FULL = ROOT / "src/database/schema-full.sql"


def test_migration_file_exists_and_is_transactional():
    """Every recent migration (120-125) leads with a descriptive `--` header
    comment before BEGIN;, so this checks containment rather than the literal
    first bytes of the file — matching how test_migration_layout.py's own
    test_migration_uses_explicit_transaction checks the same property.
    """
    sql = MIGRATION.read_text()
    assert "BEGIN;" in sql
    assert sql.strip().endswith("COMMIT;")


def test_migration_creates_alert_events_with_run_and_history_columns():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS alert_events" in sql
    for col in ("alert_id", "rule_id", "pipeline_run_id", "status",
                "severity", "asset_count", "created_at"):
        assert col in sql, col
    assert "REFERENCES alerts(id) ON DELETE CASCADE" in sql
    assert "idx_alert_events_alert_id" in sql
    assert "idx_alert_events_pipeline_run" in sql


def test_migration_adds_last_run_pointer_and_seeds_retention():
    sql = MIGRATION.read_text()
    assert "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_pipeline_run_id UUID" in sql
    assert "ALERT_EVENTS_RETENTION_DAYS" in sql
    assert "ALERT_EVENTS_RETENTION_COUNT" in sql
    assert "ON CONFLICT (category, key) DO NOTHING" in sql


def test_schema_full_snapshot_mirrors_the_migration():
    snap = SCHEMA_FULL.read_text()
    assert "CREATE TABLE public.alert_events" in snap
    assert "last_pipeline_run_id uuid" in snap
    assert "alert_events_alert_id_fkey" in snap
    assert "idx_alert_events_pipeline_run" in snap
    assert "ALERT_EVENTS_RETENTION_DAYS" in snap
