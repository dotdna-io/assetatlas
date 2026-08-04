# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify migration 084 adds evaluation_type column, backfills,
updates the path check, and seeds the three watchdog rules."""
import os

import pytest


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..",
    "src", "database", "migrations",
    "084_add_evaluation_type_and_watchdog_rules.sql",
)


def _apply_migration(cur):
    with open(MIGRATION_PATH) as f:
        sql = f.read()
    # Strip the transaction wrapper so the test's outer SAVEPOINT stays intact.
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    cur.execute(sql)


@pytest.mark.integration
def test_migration_084_adds_column_backfills_and_seeds(db_transaction):
    cur = db_transaction.cursor()

    _apply_migration(cur)

    # 1. evaluation_type column exists and is NOT NULL
    cur.execute("""
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'alert_rules' AND column_name = 'evaluation_type'
    """)
    col = cur.fetchone()
    assert col is not None, "evaluation_type column was not created"
    assert col[1] == "NO", "evaluation_type must be NOT NULL"

    # 2. Existing rows have evaluation_type set
    cur.execute("""
        SELECT COUNT(*) FROM alert_rules WHERE evaluation_type IS NULL
    """)
    assert cur.fetchone()[0] == 0, "backfill left rows with NULL evaluation_type"

    # 3. SQL rules got 'sql'; filter rules got 'filter'
    cur.execute("""
        SELECT COUNT(*) FROM alert_rules
        WHERE query IS NOT NULL AND evaluation_type != 'sql'
    """)
    assert cur.fetchone()[0] == 0, "SQL-bearing rules should all be evaluation_type='sql'"

    # 4. Three watchdog rules exist exactly once each
    cur.execute("""
        SELECT slug, type, severity, evaluation_type, source
        FROM alert_rules
        WHERE slug IN ('pipeline-stale', 'pipeline-last-run-failed', 'pipeline-stuck')
        ORDER BY slug
    """)
    rows = cur.fetchall()
    assert len(rows) == 3, f"expected 3 watchdog rules, found {len(rows)}"
    for row in rows:
        slug, rule_type, severity, eval_type, source = row
        assert rule_type == "operational"
        assert eval_type == "external"
        assert source == "system"
        assert severity in ("critical", "high")


@pytest.mark.integration
def test_migration_084_is_idempotent(db_transaction):
    cur = db_transaction.cursor()
    _apply_migration(cur)
    _apply_migration(cur)  # must not raise

    cur.execute("""
        SELECT COUNT(*) FROM alert_rules
        WHERE slug IN ('pipeline-stale', 'pipeline-last-run-failed', 'pipeline-stuck')
    """)
    assert cur.fetchone()[0] == 3, "re-applying the migration should not duplicate rules"


@pytest.mark.integration
def test_migration_084_constraint_permits_external(db_transaction):
    """After migration, inserting an external rule with all-NULL paths succeeds."""
    cur = db_transaction.cursor()
    _apply_migration(cur)

    cur.execute("""
        INSERT INTO alert_rules
            (name, slug, type, severity, description, evaluation_type, source)
        VALUES
            ('Test External', 'test-ext-082', 'operational', 'high',
             'external test rule', 'external', 'system')
    """)

    cur.execute("""
        SELECT evaluation_type FROM alert_rules WHERE slug = 'test-ext-082'
    """)
    assert cur.fetchone()[0] == "external"


@pytest.mark.integration
def test_migration_084_constraint_rejects_sql_with_filters(db_transaction):
    """After migration, inserting a 'sql' rule that also has filters should fail."""
    import psycopg2
    cur = db_transaction.cursor()
    _apply_migration(cur)

    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("""
            INSERT INTO alert_rules
                (name, slug, type, severity, description,
                 query, target_filters, evaluation_type, source)
            VALUES
                ('Bad Rule', 'bad-082', 'security', 'low',
                 'bad rule', 'SELECT 1', '[]'::jsonb, 'sql', 'system')
        """)
