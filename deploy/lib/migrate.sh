#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/migrate.sh — apply DB migrations and seed data.
# Requires: common.sh sourced; PSQL_CMD set (e.g., "psql -h db -U app -d assetatlas").
#
# Tracks applied migrations in schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ).
# Bootstrap rule: if schema_migrations is empty BUT the 'assets' table already exists in public,
# mark all existing .sql files as applied (assume the DB was installed before the migration
# runner existed and is fully migrated). The 'assets' table is created by migration 001 and is
# the canonical signal of "app DB present".
#
# Caller-set variables (defaulted):
#   MIGRATIONS_DIR  : directory containing NNN_*.sql files
#                     Default: src/database/migrations (RELATIVE to $PWD — caller must cd to repo root,
#                     or set this to an absolute path).

: "${MIGRATIONS_DIR:=src/database/migrations}"

ensure_migrations_table() {
    # client_min_messages: on a fresh install schema-full.sql has already created
    # this table, so IF NOT EXISTS emits a NOTICE. NOTICE goes to stderr, which
    # the >/dev/null below does not catch, and it surfaces in the installer log
    # looking like a warning about a completely normal condition.
    #
    # ON_ERROR_STOP=1: psql exits 0 for a statement that failed when the SQL
    # arrives on stdin, so without it a CREATE TABLE that never ran is reported
    # as success and everything below tracks migrations against a table that may
    # not exist. apply_migrations calls this bare under `set -euo pipefail`, so
    # the die is what turns an inherited silent exit into a usable message.
    $PSQL_CMD -v ON_ERROR_STOP=1 <<'SQL' >/dev/null || die "Could not create schema_migrations — migration tracking is unusable."
SET client_min_messages = warning;
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL
}

bootstrap_if_empty() {
    # Return non-zero on missing dir (caller may invoke standalone). apply_migrations dies separately
    # via its own dir guard before calling bootstrap_if_empty.
    [ -d "$MIGRATIONS_DIR" ] || return 1
    local migrations_count assets_exists
    migrations_count=$($PSQL_CMD -At -c "SELECT count(*) FROM schema_migrations;")
    # 'assets' is created by migration 001 — canonical signal that the app schema is already present.
    assets_exists=$($PSQL_CMD -At -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = 'assets';")
    if [ "$migrations_count" = "0" ] && [ "$assets_exists" = "1" ]; then
        log_warn "Bootstrap: schema_migrations empty but 'assets' table exists. Marking all known migrations as applied."
        # Build a single multi-VALUES INSERT instead of one round-trip per file.
        local f sql="INSERT INTO schema_migrations (filename) VALUES "
        local first=1
        for f in "$MIGRATIONS_DIR"/*.sql; do
            [ -f "$f" ] || continue
            local name; name=$(basename "$f")
            # Escape any single-quote in filename (defensive — current filenames have none).
            local safe="${name//\'/\'\'}"
            if [ "$first" = "1" ]; then
                sql+="('$safe')"
                first=0
            else
                sql+=", ('$safe')"
            fi
        done
        if [ "$first" = "1" ]; then
            log_warn "Bootstrap: no migration files found in $MIGRATIONS_DIR — nothing to mark."
            return 0
        fi
        sql+=" ON CONFLICT DO NOTHING;"
        $PSQL_CMD -c "$sql" >/dev/null || die "Bootstrap bulk INSERT failed."
        return 0
    fi
    return 1
}

apply_migrations() {
    [ -d "$MIGRATIONS_DIR" ] || die "MIGRATIONS_DIR not found: $MIGRATIONS_DIR (current dir: $PWD)"
    ensure_migrations_table
    if bootstrap_if_empty; then
        log_ok "Migrations: bootstrap completed; no further migrations to apply."
        return 0
    fi
    # Pre-fetch the set of already-applied filenames in one query — avoids one SELECT per file.
    # Stored as a newline-delimited string; grep -Fxq gives O(n) membership without bash 4 arrays.
    #
    # The set reaches grep by HERESTRING, never by a pipe. grep -q exits on its
    # first match and closes the read end; a piped writer still holding bytes
    # takes EPIPE, and pipefail (common.sh) hands the pipeline the WRITER's
    # non-zero status — so the membership test answers "not applied" for a
    # migration that IS applied, and the file runs a second time. That is a
    # `relation "…" already exists` and a die: it cost an install in CI (#1199),
    # and on an update it fires after the backup and snapshot but before the
    # health window auto-rollback wraps, so nothing is restored. A herestring has
    # no writer process, so there is nothing left to signal.
    local applied_set
    applied_set=$($PSQL_CMD -At -c "SELECT filename FROM schema_migrations;")

    local applied=0 skipped=0
    local f name
    for f in "$MIGRATIONS_DIR"/*.sql; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        if grep -Fxq "$name" <<<"$applied_set"; then
            skipped=$((skipped+1))
            continue
        fi
        log_info "Applying migration: $name"
        # PSQL_CMD runs psql inside the DB container via `docker exec -i`, so
        # `-f "$f"` would refer to a path inside the container. Pipe contents
        # via stdin instead — that's why install.sh sets `docker exec -i`.
        if ! $PSQL_CMD -v ON_ERROR_STOP=1 < "$f"; then
            die "Migration $name FAILED. Aborting."
        fi
        # ON_ERROR_STOP=1 is what lets the die fire at all: on stdin psql exits 0
        # for a failed statement, so a migration that ran but was never recorded
        # used to look like a success — and re-applied on every later update.
        $PSQL_CMD -v ON_ERROR_STOP=1 -v name="$name" <<'SQL' >/dev/null || die "Failed to record applied migration $name in schema_migrations"
INSERT INTO schema_migrations (filename) VALUES (:'name');
SQL
        applied_set="$applied_set
$name"
        applied=$((applied+1))
    done
    log_ok "Migrations: $applied applied, $skipped skipped."
}

# list_pending_migrations
# Prints (one per line, in filename order) the migration basenames present in
# $MIGRATIONS_DIR but NOT yet recorded in schema_migrations. Read-only — used by
# the update dry-run report. Prints nothing when the dir is empty or all applied.
#
# Returns 1 (printing nothing) when the database cannot be read at all, so the
# caller can say "unavailable" instead of reporting every migration as pending.
# The probe uses to_regclass(), which returns NULL rather than erroring when the
# table is absent — that distinguishes "DB unreachable" from "migrations never
# run here", which legitimately means everything is pending. Deliberately does
# NOT call ensure_migrations_table: that is DDL, and a dry-run must not mutate.
list_pending_migrations() {
    [ -d "$MIGRATIONS_DIR" ] || return 0
    local have_table applied_set=""
    have_table=$($PSQL_CMD -At -c "SELECT to_regclass('public.schema_migrations') IS NOT NULL;" 2>/dev/null) \
        || return 1
    if [ "$have_table" = "t" ]; then
        applied_set=$($PSQL_CMD -At -c "SELECT filename FROM schema_migrations;" 2>/dev/null) || return 1
    fi
    local f name
    for f in "$MIGRATIONS_DIR"/*.sql; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        # Herestring for the same reason as apply_migrations (see the comment
        # there) — with the failure mirrored: a spurious non-zero keeps
        # `&& continue` from firing, so an applied migration is printed as
        # pending and the update dry-run promises schema changes already in place.
        grep -Fxq "$name" <<<"$applied_set" && continue
        printf '%s\n' "$name"
    done
}
