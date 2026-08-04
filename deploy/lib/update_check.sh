#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/update_check.sh — turn the update check on at install time.
#
# Requires: common.sh sourced; PSQL_CMD exported by caller.
#
# The shipped default for system.enable_update_check is false and stays that
# way: while disabled the daily job makes no outbound call at all, which is the
# only correct behaviour for an air-gapped install. An online install has
# connectivity by definition, so install.sh layers a true on top for that mode
# only — see the INSTALL_MODE gate at the call site.

# seed_update_check_setting
# UPSERTs system.enable_update_check = true.  Returns non-zero on failure.
seed_update_check_setting() {
    : "${PSQL_CMD:?seed_update_check_setting: PSQL_CMD must be set}"

    # The WHERE clause is a deliberate divergence from smtp.sh, which upserts
    # unconditionally — do not "fix" the inconsistency. SMTP values come from
    # explicit operator input at the install prompt, so overwriting them is
    # what the operator asked for. This value is a default we apply silently on
    # their behalf, so it has to yield to any explicit choice: the row is
    # seeded (migration 119) with updated_by NULL, and settings_manager.py
    # stamps updated_by on every change made through the admin UI. Guarding on
    # NULL therefore means "only touch a row no operator has ever set", so
    # re-running install.sh preserves a deliberate opt-out instead of silently
    # flipping the update check back on.
    # value_type and default_value are spelled out because they are NOT NULL
    # with no default: Postgres validates the proposed tuple against those
    # BEFORE it detects the unique conflict, so naming only (category, key,
    # value) aborts with a not-null violation and never reaches DO UPDATE —
    # even though the row always already exists here. default_value stays
    # 'false': the installer layers a value on top, it does not redefine the
    # product default that a "reset to default" in the admin UI restores.
    # ON_ERROR_STOP=1 is what makes the "returns non-zero on failure" contract
    # above true: without it psql prints the error and still exits 0, so a seed
    # that never happened would be reported to install.sh as a success. Matches
    # migrate.sh, backup.sh and install.sh's demo-seed loop. A row skipped by
    # the updated_by guard is not an error and still exits 0.
    $PSQL_CMD -v ON_ERROR_STOP=1 <<'SQL' >/dev/null
INSERT INTO system_settings (category, key, value, value_type, default_value)
VALUES ('system', 'enable_update_check', 'true', 'boolean', 'false')
ON CONFLICT (category, key) DO UPDATE SET value = 'true'
    WHERE system_settings.updated_by IS NULL;
SQL
}
