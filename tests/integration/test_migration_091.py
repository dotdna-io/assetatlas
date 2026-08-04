# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify migration 091 adds oidc.allowed_email_domains and is idempotent.

Critically, migration 091 must PRESERVE any existing oidc.auto_create_users
row (true or false) — operators who explicitly set 'true' under migration 089
must NOT be flipped to 'false' by the upgrade.
"""
import os

import pytest


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..",
    "src", "database", "migrations",
    "091_add_oidc_email_allowlist.sql",
)


def _apply_migration(cur):
    with open(MIGRATION_PATH) as f:
        sql = f.read()
    # Strip the transaction wrapper so the test's outer SAVEPOINT stays intact.
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    cur.execute(sql)


@pytest.mark.integration
def test_migration_091_adds_allowed_email_domains(db_transaction):
    cur = db_transaction.cursor()

    _apply_migration(cur)

    cur.execute("""
        SELECT value, value_type, default_value
        FROM system_settings
        WHERE category = 'oidc' AND key = 'allowed_email_domains'
    """)
    row = cur.fetchone()
    assert row is not None, "allowed_email_domains was not seeded"
    value, value_type, default_value = row
    assert value == "", "allowed_email_domains default value should be empty"
    assert value_type == "string"
    assert default_value == ""


@pytest.mark.integration
def test_migration_091_preserves_existing_auto_create_users(db_transaction):
    """If oidc.auto_create_users already exists with 'true' (an operator's
    explicit choice under migration 089), migration 091 must not flip it."""
    cur = db_transaction.cursor()

    cur.execute("""
        INSERT INTO system_settings
            (category, key, value, value_type, default_value, description, is_sensitive, requires_restart)
        VALUES
            ('oidc', 'auto_create_users', 'true', 'boolean', 'true',
             'Operator-set value (simulating an upgrade from migration 089).',
             FALSE, FALSE)
        ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value
    """)

    _apply_migration(cur)

    cur.execute("""
        SELECT value FROM system_settings
        WHERE category = 'oidc' AND key = 'auto_create_users'
    """)
    assert cur.fetchone()[0] == "true", \
        "migration 091 must preserve an existing 'true' setting"


@pytest.mark.integration
def test_migration_091_seeds_auto_create_users_when_missing(db_transaction):
    """On a hypothetical install missing migration 089, migration 091 should
    seed auto_create_users with the new safer 'false' default."""
    cur = db_transaction.cursor()

    cur.execute("""
        DELETE FROM system_settings
        WHERE category = 'oidc' AND key = 'auto_create_users'
    """)

    _apply_migration(cur)

    cur.execute("""
        SELECT value, default_value FROM system_settings
        WHERE category = 'oidc' AND key = 'auto_create_users'
    """)
    row = cur.fetchone()
    assert row is not None, "auto_create_users was not seeded"
    assert row[0] == "false"
    assert row[1] == "false"


@pytest.mark.integration
def test_migration_091_is_idempotent(db_transaction):
    cur = db_transaction.cursor()

    _apply_migration(cur)
    _apply_migration(cur)  # must not raise

    cur.execute("""
        SELECT COUNT(*) FROM system_settings
        WHERE category = 'oidc' AND key = 'allowed_email_domains'
    """)
    assert cur.fetchone()[0] == 1, \
        "re-applying migration 091 must not duplicate the allowlist row"
