# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify migration 083 widens alert_rules.source CHECK and adds alerts dedup columns."""
import os
import uuid

import pytest


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "src",
    "database",
    "migrations",
    "083_prometheus_alerts_schema.sql",
)


@pytest.mark.integration
def test_migration_083_widens_source_check_and_adds_dedup_columns(db_transaction):
    cur = db_transaction.cursor()

    # Apply migration (idempotent; safe even if already applied to the test DB)
    # Strip BEGIN/COMMIT to avoid breaking the SAVEPOINT transaction context
    with open(MIGRATION_PATH) as f:
        migration_sql = f.read()
        migration_sql = migration_sql.replace("BEGIN;", "").replace("COMMIT;", "")
        cur.execute(migration_sql)

    # 1. alert_rules.source can now be 'prometheus'
    rule_id = str(uuid.uuid4())
    slug = f'prom-test-082-{rule_id[:8]}'
    cur.execute(
        """
        INSERT INTO alert_rules
            (id, name, slug, type, severity, description, query, source)
        VALUES
            (%s::uuid, 'Prom test', %s, 'operational', 'low',
             'test', 'up == 0', 'prometheus')
        """,
        (rule_id, slug),
    )

    # 2. alerts.fingerprint + labels columns exist and accept values
    cur.execute(
        """
        INSERT INTO alerts
            (rule_id, type, severity, title, description,
             fingerprint, labels)
        VALUES
            (%s::uuid, 'operational', 'low', 't', 'd',
             'abc123', '{"instance": "localhost:9100"}'::jsonb)
        RETURNING fingerprint, labels
        """,
        (rule_id,),
    )
    row = cur.fetchone()
    assert row[0] == "abc123"
    assert row[1] == {"instance": "localhost:9100"}

    # 3. Partial index exists
    cur.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'idx_alerts_fingerprint_active'"
    )
    idx = cur.fetchone()
    assert idx is not None
    assert "WHERE (status = 'active'" in idx[0]


@pytest.mark.integration
def test_migration_083_rejects_invalid_source(db_transaction):
    """Ensure the widened CHECK still rejects unknown source values."""
    cur = db_transaction.cursor()

    # Apply migration (idempotent; safe even if already applied to the test DB)
    # Strip BEGIN/COMMIT to avoid breaking the SAVEPOINT transaction context
    with open(MIGRATION_PATH) as f:
        migration_sql = f.read()
        migration_sql = migration_sql.replace("BEGIN;", "").replace("COMMIT;", "")
        cur.execute(migration_sql)

    bad_slug = f'bad-082-{str(uuid.uuid4())[:8]}'
    with pytest.raises(Exception) as excinfo:
        cur.execute(
            """
            INSERT INTO alert_rules (name, slug, type, severity, description, query, source)
            VALUES ('bad', %s, 'operational', 'low', 'd', 'q', 'invalid_source')
            """,
            (bad_slug,)
        )
    # psycopg2.errors.CheckViolation
    assert "alert_rules_source_check" in str(excinfo.value)
