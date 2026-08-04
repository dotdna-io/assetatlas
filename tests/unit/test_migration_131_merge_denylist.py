# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

MIG = Path("src/database/migrations/131_create_merge_denylist.sql")


def test_migration_exists_and_is_idempotent():
    sql = MIG.read_text()
    assert "CREATE TABLE IF NOT EXISTS merge_denylist" in sql
    # Containment, not startswith(sql.strip()): this migration (like every
    # recent one) leads with a descriptive `--` header comment before BEGIN;,
    # so a literal-first-bytes check would misfire. See the identical
    # precedent/rationale in test_alert_events_migration.py.
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    # Must NOT self-record — the deploy runner owns schema_migrations
    assert "schema_migrations" not in sql


def test_migration_constrains_identifier_type():
    sql = MIG.read_text()
    for t in ("'hostname'", "'fqdn'", "'mac'", "'serial'"):
        assert t in sql
    assert "UNIQUE (identifier_type, identifier_value)" in sql
