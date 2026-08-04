# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify migration 071 truncates all six affected tables on cutover (#455)."""
import os

import pytest


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "src",
    "database",
    "migrations",
    "071_restructure_field_identifiers.sql",
)

AFFECTED_TABLES = (
    "tag_rules",
    "posture_sub_rules",
    "posture_sub_rule_history",
    "reports",
    "reports_audit_log",
    "system_dashboard_preferences",
)


@pytest.mark.integration
def test_migration_071_truncates_all_affected_tables(db_transaction):
    """Seed each affected table, run migration 071, assert all are empty."""
    cur = db_transaction.cursor()

    # Seed each affected table with at least one row. Each INSERT below
    # satisfies the NOT NULL / CHECK constraints defined in the originating
    # migration for that table.

    # tag_rules
    cur.execute(
        """
        INSERT INTO tag_rules (id, name, type, target_filters, tags)
        VALUES (
            'test-rule-071',
            'Test rule for migration 071',
            'classification',
            '[]'::jsonb,
            '{"label": "test", "color": "#000000"}'::jsonb
        )
        """
    )

    # posture_categories is seeded as an FK prerequisite for posture_sub_rules
    # (ON DELETE RESTRICT). It is NOT in AFFECTED_TABLES / the truncate scope;
    # the row is rolled back with the outer db_transaction SAVEPOINT.
    cur.execute(
        """
        INSERT INTO posture_categories (id, name, description, icon, applicable_categories, display_order)
        VALUES ('test_cat_071', 'Test Cat 071', 'desc', 'M0', '["server"]'::jsonb, 99)
        ON CONFLICT (id) DO NOTHING
        """
    )

    # posture_sub_rules — capture the generated id so posture_sub_rule_history
    # can reference the real sub-rule row (sub_rule_id has no FK, so an
    # orphan UUID would silently succeed but be misleading).
    cur.execute(
        """
        INSERT INTO posture_sub_rules (category_id, name, target_filters, compliance_criteria)
        VALUES (
            'test_cat_071',
            'Test sub-rule 071',
            '[]'::jsonb,
            '{"logic": "all", "conditions": []}'::jsonb
        )
        RETURNING id
        """
    )
    sub_rule_id = cur.fetchone()[0]

    # posture_sub_rule_history — reference the sub-rule we just inserted.
    cur.execute(
        """
        INSERT INTO posture_sub_rule_history (sub_rule_id, category_id, action, changed_by)
        VALUES (%(sub_rule_id)s, 'test_cat_071', 'create', 'tester')
        """,
        {"sub_rule_id": sub_rule_id},
    )

    # reports
    cur.execute(
        """
        INSERT INTO reports (title, charts)
        VALUES ('Test report 071', '[]'::jsonb)
        """
    )

    # reports_audit_log
    cur.execute(
        """
        INSERT INTO reports_audit_log (action, changed_by_username)
        VALUES ('created', 'tester')
        """
    )

    # system_dashboard_preferences is single-row and may have been pre-seeded
    # by migration 031; ON CONFLICT ensures the UPDATE branch fires either way.
    cur.execute(
        """
        INSERT INTO system_dashboard_preferences (id, working_dashboard)
        VALUES (1, '{"charts": [{"id": "x"}]}'::jsonb)
        ON CONFLICT (id) DO UPDATE SET working_dashboard = EXCLUDED.working_dashboard
        """
    )

    # Sanity check: at least one row was inserted for every target table.
    for table in AFFECTED_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        assert count >= 1, f"{table} should have at least one row before migration"

    # Execute the migration SQL.
    with open(MIGRATION_PATH) as f:
        migration_sql = f.read()
    cur.execute(migration_sql)

    # Every affected table should be empty after migration.
    for table in AFFECTED_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        assert count == 0, f"{table} should be empty after migration 071"
