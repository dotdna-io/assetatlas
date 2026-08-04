#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/env.sh — .env generation, .install-state I/O, secret generation.
# Requires: deploy/lib/common.sh sourced first.
#
# .install-state is a key=value file in $INSTALL_DIR that survives across updates.
# It records user choices (opt-ins, ports, install mode) so update.sh doesn't re-prompt.

# Defensive defaults so sourcing this file without an environment set doesn't crash under set -u.
: "${INSTALL_DIR:=/opt/assetatlas}"
: "${INSTALL_STATE_PATH:=$INSTALL_DIR/.install-state}"

# _validate_state_key <key>
# Rejects any key that is not a plain identifier (alphanumeric/underscore only).
# Keys are interpolated into ERE patterns (^${key}=); a dot or bracket would silently
# match unintended lines.
_validate_state_key() {
    case "$1" in
        ''|*[!a-zA-Z0-9_]*) die "state key must be alphanumeric/underscore only, got: $1" ;;
    esac
}

# state_get <key> [<default>]
# Reads a value from .install-state. Returns <default> (or empty string) if the
# key is absent or the file does not exist yet.
state_get() {
    local key=$1
    _validate_state_key "$key"
    local default=${2:-}
    [ -f "$INSTALL_STATE_PATH" ] || { printf '%s\n' "$default"; return; }
    local val
    val=$(grep -E "^${key}=" "$INSTALL_STATE_PATH" | head -1 | cut -d= -f2-)
    printf '%s\n' "${val:-$default}"
}

# state_set <key> <value>
# Writes (or updates) a single key=value line in .install-state.
# File is created if absent. Permissions are forced to 600 on every write.
state_set() {
    local key=$1
    local value=$2
    _validate_state_key "$key"
    case "$value" in
        *'"'*|*'\'*) die "state value must not contain double-quote or backslash, got: $value" ;;
    esac
    mkdir -p "$(dirname "$INSTALL_STATE_PATH")"
    if [ -f "$INSTALL_STATE_PATH" ] && grep -qE "^${key}=" "$INSTALL_STATE_PATH"; then
        # POSIX-portable in-place edit: write to temp, swap.
        local tmp
        tmp=$(mktemp -p "$(dirname "$INSTALL_STATE_PATH")")
        grep -vE "^${key}=" "$INSTALL_STATE_PATH" > "$tmp" || true
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
        mv "$tmp" "$INSTALL_STATE_PATH"
    else
        [ -f "$INSTALL_STATE_PATH" ] || install -m 600 /dev/null "$INSTALL_STATE_PATH"
        printf '%s=%s\n' "$key" "$value" >> "$INSTALL_STATE_PATH"
    fi
    chmod 600 "$INSTALL_STATE_PATH"
}

# _set_env_var <key> <value>
# Upsert KEY=value in $INSTALL_DIR/.env (replace the matching line or append).
# Creates the file 600 if absent. Key must be a plain identifier; value must not
# contain newline/quote/backslash (callers validate the domain-specific format).
_set_env_var() {
    local key=$1
    local value=$2
    _validate_state_key "$key"
    case "$value" in
        *$'\n'*|*'"'*|*'\'*) die "env value must not contain newline, quote, or backslash: $value" ;;
    esac
    local env_file="$INSTALL_DIR/.env"
    if [ -f "$env_file" ] && grep -qE "^${key}=" "$env_file"; then
        local tmp
        tmp=$(mktemp -p "$(dirname "$env_file")")
        grep -vE "^${key}=" "$env_file" > "$tmp" || true
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
        mv "$tmp" "$env_file"
    else
        [ -f "$env_file" ] || install -m 600 /dev/null "$env_file"
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
    chmod 600 "$env_file"
}

# write_env_file : writes $INSTALL_DIR/.env from collected variables
# Caller must have set: DOMAIN, DATA_DIR, HTTPS_PORT, HTTP_PORT, DB_PASSWORD, SECRET_KEY,
# JWT_SECRET_KEY, ADMIN_PASSWORD, plus any opt-in vars (EMAIL_*, MONITORING_*).
#
# Precondition: caller-supplied strings (DOMAIN, DATA_DIR) must not contain
# whitespace or any of `$ \` \\ ; & | ( ) < > # ' "`. Both are written to .env
# UNQUOTED and update.sh / assetatlas-cli source that file, so a `;` runs a
# command and a quote makes the file unparseable just as surely as a `$` does.
# Validation belongs at the prompt layer (install.sh); this function rejects
# unsafe values defensively as a second line of defense — keep the two lists in
# step.
write_env_file() {
    for var in DOMAIN DATA_DIR; do
        local val=${!var:-}
        case "$val" in
            *'$'*|*'`'*|*'\'*|*';'*|*'&'*|*'|'*|*'('*|*')'*|*'<'*|*'>'*|*'#'*|*"'"*|*'"'*)
                die "write_env_file: $var contains shell-unsafe characters: $val" ;;
            *[[:space:]]*)
                die "write_env_file: $var must not contain whitespace: $val" ;;
        esac
    done
    # ADMIN_PASSWORD gets the same defence-in-depth check, but against a different
    # character set and without echoing the value. It is written single-quoted
    # below, so `$`/backtick/`\` are inert — only a single quote (which would end
    # the quoting) or a newline (which would end the line) can corrupt the file.
    # resolve_admin_password already rejects both; this is the second line.
    case "${ADMIN_PASSWORD:-}" in
        *"'"*|*$'\n'*) die "write_env_file: ADMIN_PASSWORD contains a single quote or newline, which cannot be written to .env safely." ;;
    esac
    local env_file="$INSTALL_DIR/.env"
    log_info "Writing .env to $env_file"
    cat > "$env_file" <<EOF
# Generated by AssetAtlas installer at $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Do not edit by hand without re-running: assetatlas-cli config sync

DOMAIN=$DOMAIN
DATA_DIR=$DATA_DIR
HTTPS_PORT=$HTTPS_PORT
HTTP_PORT=$HTTP_PORT

# Database — host/user must match deploy/compose/docker-compose.yml.
DATABASE_URL=postgresql://assetatlas:$DB_PASSWORD@timescaledb:5432/assetatlas
POSTGRES_USER=assetatlas
POSTGRES_DB=assetatlas
POSTGRES_PASSWORD=$DB_PASSWORD
# Required: compose references \${DB_PASSWORD} with no default, so update.sh
# / cli would re-run compose with an empty DB password without this line.
DB_PASSWORD=$DB_PASSWORD

# Auth
SECRET_KEY=$SECRET_KEY
JWT_SECRET_KEY=$JWT_SECRET_KEY
ASSETATLAS_ENCRYPTION_KEY=$ASSETATLAS_ENCRYPTION_KEY
# Single-quoted: this is the one value an operator can supply verbatim
# (resolve_admin_password). Quoting keeps \`~\`, and anything else the prompt
# guard lets through, from being expanded when update.sh sources this file.
ADMIN_INITIAL_PASSWORD='$ADMIN_PASSWORD'

# Email (set via installer SMTP prompts; password Fernet-encrypted into DB at install)
EMAIL_ENABLED=${EMAIL_ENABLED:-false}

# Monitoring
MONITORING_ENABLED=${MONITORING_ENABLED:-false}
ALERTMANAGER_WEBHOOK_SECRET=${ALERTMANAGER_WEBHOOK_SECRET:-}
PROMETHEUS_RETENTION=${PROMETHEUS_RETENTION:-30d}

# Teams bridge (service-to-service auth; generated at install time
# by generate_secrets, but tolerated as empty if write_env_file is
# called from a context that didn't run generate_secrets first —
# the Teams bot is opt-in and these vars stay blank when unused).
ASSETATLAS_BRIDGE_API_KEY=${ASSETATLAS_BRIDGE_API_KEY:-}
TEAMS_LINK_SECRET_KEY=${TEAMS_LINK_SECRET_KEY:-}
# ASSETATLAS_PUBLIC_BASE_URL is the bootstrap fallback for the public
# AssetAtlas URL. After install, the operator can change it without
# editing this file via System -> Security in the Settings UI
# (security.public_base_url, hot-reloadable).
ASSETATLAS_PUBLIC_BASE_URL=https://${DOMAIN:-localhost}:${HTTPS_PORT:-8443}

# Teams bot credentials moved to the Settings → Chatbot → Teams UI in
# v2026.20+. The values below are kept commented for one release cycle so
# that operators rolling back to v2026.19 retain their previous setup.
# TEAMS_BOT_APP_ID=
# TEAMS_BOT_APP_PASSWORD=
# TEAMS_BOT_APP_TENANT_ID=
# ORG_NAME=
EOF
    chmod 600 "$env_file"
}

# generate_secrets
# Generates fresh random credentials and exports them into the current environment.
# Sets: DB_PASSWORD, SECRET_KEY, JWT_SECRET_KEY, ADMIN_PASSWORD.

####
# generate_self_signed_cert : create $INSTALL_DIR/docker/nginx/ssl/server.{crt,key}
# if not already present. Nginx fails to start without these (the container's
# default.conf references /etc/nginx/ssl/server.{crt,key}). Self-signed is
# acceptable for the IP-only / localhost installer default; operators replace
# them post-install when they front the install with a real CA cert.
####
generate_self_signed_cert() {
    : "${INSTALL_DIR:?INSTALL_DIR must be set}"
    local ssl_dir="$INSTALL_DIR/docker/nginx/ssl"
    local crt="$ssl_dir/server.crt"
    local key="$ssl_dir/server.key"
    if [ -f "$crt" ] && [ -f "$key" ]; then
        log_info "TLS cert already present at $ssl_dir; leaving it in place."
        return 0
    fi
    log_info "Generating self-signed TLS cert at $ssl_dir..."
    mkdir -p "$ssl_dir"
    local cn="${DOMAIN:-localhost}"
    openssl req -x509 -nodes -newkey rsa:2048 \
        -days 825 \
        -keyout "$key" \
        -out "$crt" \
        -subj "/CN=$cn" \
        -addext "subjectAltName=DNS:$cn,DNS:localhost,IP:127.0.0.1" \
        >/dev/null 2>&1 \
        || die "openssl failed to generate self-signed cert."
    chmod 600 "$key"
    chmod 644 "$crt"
    log_ok "Self-signed cert written ($cn, 825-day validity)."
}

####
# prepare_data_dirs : pre-create $DATA_DIR subdirectories with the ownership
# each container expects. Without this, Docker auto-creates missing bind-mount
# paths as root:root, which makes containers running as non-root users (api,
# scheduler — uid 1000 in upstream images) fail with "Permission denied" when
# they try to write logs/state.
#
# UIDs are baked into the upstream images:
#   - assetatlas-{api,scheduler} : 1000 (assetatlas) — needs explicit chown
#   - timescale/timescaledb       : 70   (postgres)  — entrypoint chowns, no-op
#   - redis:alpine                : 999  (redis)     — entrypoint chowns, no-op
#   - nginx:alpine                : 0    (master)    — root-owned dir is fine
#
# Caller must have DATA_DIR set (export from install.sh after prompts), and
# RUNTIME set (detect_runtime) for the rootless branch to find podman.
####

# _chown_in_userns <uid> <gid> <dir>...
# Rootless: the container's uid 1000 is a SUBORDINATE uid on the host (see
# /etc/subuid), so a plain `chown 1000` names a host user that is not the
# container's — the dir ends up owned by the wrong id and the container still
# gets EACCES. `podman unshare` runs the chown INSIDE the user namespace, where
# 1000 means what the container means by it, and the result on the host is the
# correct subordinate id with the dir left at 0750.
#
# Returns non-zero for anything that is not rootless podman, so the caller falls
# back. Rootless Docker is an explicit non-goal and takes that fallback.
#
# On an actual chown failure (no subordinate id range, EROFS, a podman still
# owing `system migrate`, ...) the combined stdout+stderr lands in
# CHOWN_USERNS_ERROR so the caller's fallback warning can name the real reason
# instead of just "it didn't work." Reset unconditionally on every call so a
# stale value from an earlier invocation can never leak into a later one that
# took an early return (not podman, no podman binary).
_chown_in_userns() {
    local uid=$1 gid=$2; shift 2
    CHOWN_USERNS_ERROR=""
    [ "${RUNTIME:-}" = "podman" ] || return 1
    command -v podman >/dev/null 2>&1 || return 1
    local err
    if err=$(podman unshare chown -R "${uid}:${gid}" "$@" 2>&1); then
        return 0
    fi
    CHOWN_USERNS_ERROR="$err"
    return 1
}

prepare_data_dirs() {
    : "${DATA_DIR:?DATA_DIR must be set before prepare_data_dirs}"
    log_info "Preparing $DATA_DIR subdirectories..."
    mkdir -p \
        "$DATA_DIR/logs" \
        "$DATA_DIR/postgres" \
        "$DATA_DIR/redis" \
        "$DATA_DIR/nginx-logs" \
        "$DATA_DIR/chromadb" \
        "$DATA_DIR/uploaded-logos" \
        "$DATA_DIR/metrics-forwarder"
    # API/scheduler containers run as uid 1000 and write to /app/logs,
    # /app/data/chromadb (semantic-search persistence), and
    # /app/data/uploaded-logos (custom integration logo uploads).
    # Rootless Docker (and podman with no subordinate id range) can't chown
    # into a uid the host doesn't have — fall back to world-writable. Detect
    # explicitly so a genuine chown error (typo, EROFS) doesn't get
    # hidden by a blanket retry.
    # #922 P2b: metrics-forwarder is API-written (uid 1000, gid 1000) but READ by the
    # metrics-forwarder agent, which runs as a different uid. The agent is added to gid
    # 1000 via the compose `group_add`, so it reads the 0640 config via GROUP — the dir
    # stays 0750 (group-traversable) like the other uid-1000 dirs; no world access.
    local uid_dirs=(
        "$DATA_DIR/logs" "$DATA_DIR/chromadb"
        "$DATA_DIR/uploaded-logos" "$DATA_DIR/metrics-forwarder"
    )
    # Mode BEFORE ownership, and shared by both branches: `podman unshare chown`
    # hands these directories to a SUBORDINATE uid, at which point the invoking
    # user is no longer their owner and a host-side `chmod` fails EPERM — which,
    # under install.sh's `set -euo pipefail`, takes the whole install down right
    # after "Preparing …". (The bats suite does not catch it: its podman stub
    # exits 0 without changing any ownership, so the chmod that follows still
    # succeeds there.) Root's `chown -R` never touches modes, so running the
    # chmod first is identical for that branch.
    chmod 0750 "${uid_dirs[@]}"
    if [ "$(id -u)" -eq 0 ]; then
        chown -R 1000:1000 "${uid_dirs[@]}"
    elif _chown_in_userns 1000 1000 "${uid_dirs[@]}"; then
        log_ok "Data directories owned by the container's uid 1000 inside the user namespace (0750)."
    else
        # Last resort, and it really is one: these hold a log tree and the
        # chromadb store on a host that may have other users. Reached only by a
        # rootless container runtime this cannot address — rootless Docker, or
        # a podman with no subordinate id range.
        local reason=""
        # `:-` guarded: _chown_in_userns sets this unconditionally, but only
        # when it actually runs — a future short-circuit above this branch would
        # otherwise reach an unbound variable and kill the installer under
        # `set -u` while trying to print a warning.
        [ -n "${CHOWN_USERNS_ERROR:-}" ] && reason=" (${CHOWN_USERNS_ERROR})"
        log_warn "Could not set ownership inside a user namespace${reason} — leaving $DATA_DIR/logs, $DATA_DIR/chromadb, $DATA_DIR/uploaded-logos, and $DATA_DIR/metrics-forwarder world-writable so the container's uid 1000 can write."
        chmod 0777 "${uid_dirs[@]}"
    fi
}

generate_secrets() {
    DB_PASSWORD=$(generate_password)
    SECRET_KEY=$(openssl rand -hex 32)
    JWT_SECRET_KEY=$(openssl rand -hex 32)
    ADMIN_PASSWORD=$(generate_password)
    # Fernet key for at-rest encryption of secrets stored in the database
    # (SMTP password, OIDC client secrets, etc). Required by ProductionConfig
    # — the API refuses to boot if this is unset. Format: url-safe base64
    # of 32 random bytes (44 chars including trailing '=').
    ASSETATLAS_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr -d '\n' | tr '+/' '-_')
    # Teams bridge: bridge ↔ API service-to-service key + link-token
    # signing key. Distinct from JWT_SECRET_KEY so a leak of one
    # doesn't compromise the other. Bridge endpoints are inert until
    # both are populated, so generating them unconditionally is safe.
    ASSETATLAS_BRIDGE_API_KEY=$(openssl rand -base64 48 | tr -d '\n')
    TEAMS_LINK_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')
    export DB_PASSWORD SECRET_KEY JWT_SECRET_KEY ADMIN_PASSWORD \
           ASSETATLAS_ENCRYPTION_KEY \
           ASSETATLAS_BRIDGE_API_KEY TEAMS_LINK_SECRET_KEY
}

# resolve_admin_password
# After generate_secrets, optionally let the operator set the admin password by
# hand. Interactive only — UNATTENDED keeps the generated password (auto-gen-all).
# Machine secrets (DB/JWT/SECRET_KEY/Fernet/bridge) are never prompted.
# Reads directly in the current shell so the ADMIN_PASSWORD override persists.
resolve_admin_password() {
    [ "${UNATTENDED:-0}" = "1" ] && return 0
    if prompt_yn "Auto-generate all secrets?" Y; then
        return 0
    fi
    local pw
    read -r -s -p "Admin password (blank = auto-generate): " pw; echo >&2
    if [ -n "$pw" ]; then
        case "$pw" in
            *[[:space:]]*|*'$'*|*'`'*|*'\'*|*';'*|*'&'*|*'|'*|*'('*|*')'*|*'<'*|*'>'*|*'#'*|*"'"*|*'"'*)
                die "Admin password contains whitespace or a shell-unsafe character (\$ \` \\ ; & | ( ) < > # ' \" or space); choose a password with only letters, digits, and simple punctuation." ;;
        esac
        ADMIN_PASSWORD="$pw"
        export ADMIN_PASSWORD
        log_info "Admin password set from operator input."
    else
        log_info "Blank entry — keeping the auto-generated admin password."
    fi
}
