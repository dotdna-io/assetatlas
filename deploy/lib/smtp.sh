#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/smtp.sh — seed SMTP settings into system_settings at install time.
#
# Requires: common.sh sourced; PSQL_CMD and RUNTIME exported by caller;
#           SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
#           SMTP_SECURITY, SMTP_DISPLAY_NAME, SMTP_FRONTEND_URL set by caller.
#
# Encryption: Fernet via the api container's Python (cryptography is already
# installed there). Key derivation matches src/modules/integrations/shared/encryption.py:
#   Priority 1 — ASSETATLAS_ENCRYPTION_KEY env var (used as-is, must be valid Fernet key)
#   Priority 2 — SHA256("AssetAtlas_Integration_Encryption_V1") → base64url-encoded (fallback)
# Stored value format: "ENC:<fernet_token>" (matched by is_encrypted() in the app).

# _sql_lit <value>
# Escape a value for use inside a single-quoted SQL literal by doubling every
# single quote. sed rather than ${v//\'/\'\'} so the result is identical on
# bash 3.2 (macOS, where the bats suite is also run by hand).
_sql_lit() {
    printf '%s' "${1-}" | sed "s/'/''/g"
}

# seed_smtp_settings
# Encrypts the SMTP password inside the api container and UPSERTs all email
# settings rows in system_settings.  Returns non-zero on any failure.
seed_smtp_settings() {
    : "${RUNTIME:?seed_smtp_settings: RUNTIME must be set}"
    : "${PSQL_CMD:?seed_smtp_settings: PSQL_CMD must be set}"
    : "${SMTP_HOST:?seed_smtp_settings: SMTP_HOST must be set}"
    : "${SMTP_PORT:?seed_smtp_settings: SMTP_PORT must be set}"
    : "${SMTP_USER:?seed_smtp_settings: SMTP_USER must be set}"
    : "${SMTP_PASSWORD:?seed_smtp_settings: SMTP_PASSWORD must be set}"
    : "${SMTP_FROM:?seed_smtp_settings: SMTP_FROM must be set}"

    # Encrypt the password using the api container's Python interpreter so the
    # key derivation is guaranteed to match the running application.
    # SMTP_PASSWORD is passed via the _SMTP_PW env var to avoid shell quoting
    # issues with special characters; Python reads it from its own os.environ.
    local encrypted
    encrypted=$($RUNTIME exec -i \
        -e "_SMTP_PW=$SMTP_PASSWORD" \
        assetatlas-api python3 - <<'PYEOF' 2>/dev/null
import os, base64, hashlib
from cryptography.fernet import Fernet

env_key = os.environ.get("ASSETATLAS_ENCRYPTION_KEY", "")
if env_key:
    key = env_key.encode()
else:
    # Fallback: matches encryption.py get_encryption_key() non-production path.
    salt = "AssetAtlas_Integration_Encryption_V1"
    key = base64.urlsafe_b64encode(hashlib.sha256(salt.encode()).digest())

plaintext = os.environ.get("_SMTP_PW", "")
if not plaintext:
    raise SystemExit("_SMTP_PW not set")

f = Fernet(key)
print("ENC:" + f.encrypt(plaintext.encode()).decode())
PYEOF
    ) || { log_error "seed_smtp_settings: failed to encrypt SMTP password via api container"; return 1; }

    [ -n "$encrypted" ] || { log_error "seed_smtp_settings: empty encrypted password returned"; return 1; }

    # Optional fields default to empty / sensible values if not supplied by caller.
    local smtp_security="${SMTP_SECURITY:-starttls}"
    local display_name="${SMTP_DISPLAY_NAME:-AssetAtlas}"
    local frontend_url="${SMTP_FRONTEND_URL:-}"

    # Every value below is operator input interpolated into a single-quoted SQL
    # literal, so an apostrophe (SMTP_DISPLAY_NAME="O'Brien Corp", a From
    # address like o'brien@example.com) would terminate the literal and abort
    # the statement. Double it, the standard SQL escape — correct under
    # standard_conforming_strings, which is on by default in PG 16.
    local q_host q_port q_security q_user q_pw q_from q_display q_frontend
    q_host=$(_sql_lit "$SMTP_HOST")
    q_port=$(_sql_lit "$SMTP_PORT")
    q_security=$(_sql_lit "$smtp_security")
    q_user=$(_sql_lit "$SMTP_USER")
    q_pw=$(_sql_lit "$encrypted")
    q_from=$(_sql_lit "$SMTP_FROM")
    q_display=$(_sql_lit "$display_name")
    q_frontend=$(_sql_lit "$frontend_url")

    # UPSERT each system_settings row individually.
    # ON CONFLICT (category, key) DO UPDATE matches migration 082's schema.
    #
    # value_type and default_value must be named even though migration 082
    # guarantees every one of these rows already exists: they are NOT NULL with
    # no default (migration 042), and Postgres validates the proposed tuple
    # against them BEFORE it detects the unique conflict. Omitting them aborts
    # the statement with a not-null violation and DO UPDATE is never reached.
    # Both mirror migration 082 — default_value is what a "reset to default" in
    # the admin UI restores, so it stays the shipped default and never becomes
    # whatever the operator typed at the install prompt.
    #
    # ON_ERROR_STOP=1 is load-bearing, not decoration: without it psql reports a
    # failed statement on stderr and still exits 0, so this function would
    # return success and install.sh would log "SMTP settings persisted" for rows
    # that were never written. That is exactly how the missing-column bug above
    # survived. Matches migrate.sh, backup.sh and install.sh's demo-seed loop.
    $PSQL_CMD -v ON_ERROR_STOP=1 <<SQL >/dev/null
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'enabled',           'true', 'boolean', 'false')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'smtp_host',         '$q_host', 'string',  '')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'smtp_port',         '$q_port', 'integer', '587')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'smtp_security',     '$q_security', 'string',  'starttls')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'smtp_username',     '$q_user', 'string',  '')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'smtp_password',     '$q_pw', 'string',  '')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'from_address',      '$q_from', 'string',  '')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'from_display_name', '$q_display', 'string',  'AssetAtlas')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO system_settings (category, key, value, value_type, default_value) VALUES ('email', 'frontend_url',      '$q_frontend', 'string',  '')
    ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value;
SQL
}
