#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/backup.sh — DB backup, restore, retention.
# Sourced, never executed directly.
# Requires: common.sh sourced; PG_DUMP_CMD and PSQL_CMD set by caller.
#
# Caller-set variables (defaulted):
#   INSTALL_DIR : installation root     (default: /opt/assetatlas)
#   BACKUP_DIR  : where backups land    (default: $INSTALL_DIR/backups)
#
# Caller-set commands (no defaults):
#   PSQL_CMD       : a bare command string, e.g. "psql -h 127.0.0.1 -p 5432 -U postgres -d test"
#                    (env vars in PG environment, not the command string).
#   PG_DUMP_CMD    : may include an env-var prefix, e.g. "PGPASSWORD=secret pg_dump -h ... -d ..."
#                    backup_db uses `eval` so the env-var prefix is honored — necessary because
#                    `pg_dump` doesn't accept a password argument.

: "${INSTALL_DIR:=/opt/assetatlas}"
: "${BACKUP_DIR:=$INSTALL_DIR/backups}"

# backup_db <tag> [prefix]
# Creates $BACKUP_DIR/<prefix>-<tag>.sql.gz with chmod 600.
# Aborts and removes the partial file if pg_dump fails.
# Prints the output path to stdout (for caller capture).
#
# The prefix picks the retention namespace, and the two must not be mixed:
#   pre-update-<version>  update.sh's rollback snapshot — restored by EXACT
#                         filename, so losing one silently disables --rollback
#   backup-<tag>          operator + nightly-timer backups, high churn
# Before #1096 both used pre-update-, sharing one mtime-ordered retention queue,
# so a nightly timer evicted the rollback snapshot on night <keep>+1.
backup_db() {
    local tag=$1
    local prefix=${2:-pre-update}
    mkdir -p "$BACKUP_DIR"
    local out="$BACKUP_DIR/$prefix-$tag.sql.gz"
    local tmp_dump
    tmp_dump=$(mktemp)
    # INT/TERM only — deliberately NOT EXIT. Bash keeps ONE EXIT trap per shell,
    # and update.sh arms `trap 'rm -rf "$TMP_DL"' EXIT` (update.sh:200) before
    # calling this at :260. Setting an EXIT trap here replaced the caller's, and
    # the `trap - EXIT` that cleared it then removed the caller's outright,
    # leaking the downloaded bundle directory (#1106). A library function must
    # not disarm its caller's cleanup.
    #
    # Nothing is lost: every exit path below removes $tmp_dump explicitly, so
    # EXIT coverage was already redundant. INT/TERM still earn their place —
    # a Ctrl-C during pg_dump reaches none of those explicit removals.
    #
    # Path baked in at arm time rather than deferred; see deploy/lib/support.sh
    # for the measured reason a deferred '$tmp_dump' is unsafe here.
    # shellcheck disable=SC2064  # expanding now is the fix, not the bug
    trap "rm -f -- '$tmp_dump'" INT TERM
    log_info "Backing up DB to $out ..." >&2
    # PG_DUMP_CMD is a string (not array) so callers can prefix PGPASSWORD=...
    # eval is intentional here: the value is set by the installer, not user input.
    # We write to a temp file first so we can capture pg_dump's exit code cleanly:
    # piping through eval collapses PIPESTATUS to a single element under bash 3.2.
    #
    # The exit code is captured with `|| pg_exit=$?` on the SAME line, never a
    # bare command with the check on the next line: update.sh calls this
    # function under `set -e`, and a bare failing command aborts the shell
    # before any next-line check runs (#1440) — both dies were unreachable,
    # $tmp_dump leaked, and the gzip arm left a truncated .sql.gz behind. The
    # `||` suspends errexit for exactly this command without touching the
    # caller's `set` options, and behaves identically when the caller has no
    # errexit at all. `if ! cmd` is the wrong shape here: it would report the
    # inverted status, not pg_dump's real exit code.
    local pg_exit=0
    eval "$PG_DUMP_CMD" > "$tmp_dump" || pg_exit=$?
    if [ "$pg_exit" -ne 0 ]; then
        rm -f "$tmp_dump"
        die "DB backup failed (pg_dump exit=$pg_exit)."
    fi
    # Same `||` capture as the eval above, for the same reason.
    local gz_exit=0
    gzip -c "$tmp_dump" > "$out" || gz_exit=$?
    rm -f "$tmp_dump"
    if [ "$gz_exit" -ne 0 ]; then
        rm -f "$out"
        die "DB backup failed (gzip exit=$gz_exit)."
    fi
    chmod 600 "$out"
    log_ok "Backup complete: $(du -h "$out" | cut -f1)" >&2
    # Clears only what this function armed. `trap - EXIT` here would remove the
    # caller's EXIT trap even though we never set one — the second half of #1106.
    trap - INT TERM
    printf '%s\n' "$out"
}

# restore_db <backup_file>
# Drops public schema, recreates it, then restores from a gzipped SQL dump.
# Filters out GUC statements not recognized by older PostgreSQL servers
# (e.g. transaction_timeout emitted by pg_dump 18+ against pg 16 servers).
# WARNING: destructive — caller must have warned the operator.
#
# REPORTS failure with a non-zero return instead of dying (#1212). update.sh's
# rollback calls this with the stack down and only the DB container up, so a die
# here stranded the deployment with nothing but a database running and skipped
# the caller's own state summary. The known trigger is a dump taken before
# migration 142 (#1196): it aborts on the post-data matview REFRESH with the
# schema, data and COPYs already back.
#
# Callers MUST check the return, and both do so explicitly (update.sh's
# run_rollback, assetatlas-cli's cmd_restore) rather than leaning on `set -e`.
# What a caller must NOT do is carry on and start the stack: a return instead of
# a die means the caller gets to DESCRIBE the failure, not that the deployment
# survived it — since #1240 the rollback stops at this call rather than bringing
# api/scheduler up against a schema that is empty or half-loaded.
#
# TWO failure states, which callers name separately because the operator's next
# move differs: if the DROP below fails the database is UNTOUCHED; if it
# succeeds, the database holds exactly what the dump loaded before the error.
restore_db() {
    local backup_file=$1
    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: $backup_file"
        return 1
    fi
    log_warn "Restoring DB from $backup_file (drops current public schema)"
    if ! $PSQL_CMD -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null; then
        log_error "Failed to drop+recreate public schema."
        return 1
    fi
    # The transaction_timeout filter handles pg_dump 18 → pg 16 incompatibility (per MEMORY.md).
    # Only this specific GUC is stripped: a broader '^SET ' filter would suppress session-critical
    # statements like search_path and standard_conforming_strings.
    if ! gunzip -c "$backup_file" \
            | grep -v '^SET transaction_timeout' \
            | $PSQL_CMD -v ON_ERROR_STOP=1 >/dev/null; then
        log_error "DB restore from $backup_file failed."
        return 1
    fi
    log_ok "DB restored from $backup_file."
}

# backup_recorded_version <backup_file>
# Print the installed_version recorded in the dump's system_info COPY row, or
# nothing when the dump does not carry one (a pre-system-info install, or a
# column layout this parser does not recognize). update.sh --rollback uses it
# to know what the restored DB SHOULD report before restoring it — the snapshot
# tag cannot answer that, because the tag names the version the update was
# going TO while the dump holds the state from BEFORE it.
#
# One full pass with no early exit, deliberately: an early-exiting reader
# closes the pipe while gunzip still writes, gunzip takes EPIPE, and pipefail
# hands the substitution the writer's failure — a successful parse would read
# as a failed one (the #1199 family). The COPY column list is pinned to the
# exact `(id, installed_version, ...)` prefix so a reordered future schema
# yields "" (the caller skips its check and says so) rather than some other
# column's value being verified as a version.
backup_recorded_version() {
    local backup_file=$1
    [ -f "$backup_file" ] || return 1
    # LC_ALL=C: this awk scans every line of a potentially multi-GB dump, and
    # byte-wise matching is several times faster than UTF-8-aware matching.
    gunzip -c "$backup_file" 2>/dev/null | LC_ALL=C awk '
        inblock && /^\\\.$/  { inblock = 0 }
        inblock && $1 == "1" { v = $2 }
        /^COPY public\.system_info \(id, installed_version[,)]/ { inblock = 1 }
        END { if (v != "") print v }
    '
}

# _prune_oldest <keep_count> <label> <newest-first file list>
# Deletes everything past the first <keep_count> entries. Shared by the two
# retention namespaces so they cannot drift in behaviour — only in which files
# they are handed.
_prune_oldest() {
    local keep=$1 label=$2 files=$3
    local count=0 deleted=0 f
    [ -n "$files" ] || return 0
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        count=$((count + 1))
        if [ "$count" -gt "$keep" ]; then
            log_info "Pruning old $label: $(basename "$f")"
            rm -f "$f"
            deleted=$((deleted + 1))
        fi
    done <<<"$files"
    [ "$deleted" -gt 0 ] && log_ok "Pruned $deleted old $label(s); kept $keep."
    return 0
}

# prune_backups [<keep_count>]
# Operator and nightly-timer backups only (default: keep 7, matching the
# backup_retention default in assetatlas-cli).
#
# Deliberately does NOT touch pre-update-<version> rollback snapshots. The legacy
# pre-update-*T*Z glob is the pre-#1096 name for a churn backup: cmd_backup tagged
# with `date -u +%Y%m%dT%H%M%SZ` and only prefixed `scheduled-` for the timer, so
# BOTH operator and nightly dumps landed here. Matched so installs that upgrade
# retire them instead of keeping them forever. A rollback snapshot is tagged with
# a version (YYYY.WW.N) and can never contain T…Z, so it cannot match.
# Returns 0 even when BACKUP_DIR doesn't exist.
prune_backups() {
    local keep=${1:-7}
    [ -d "$BACKUP_DIR" ] || return 0
    # ls -1t sorts newest-first by mtime; we keep the first <keep> and delete the rest.
    # The legacy names must stay ONE glob: `ls` lists a file once per matching
    # operand, so overlapping patterns would double-count it against <keep>.
    local files
    files=$(ls -1t "$BACKUP_DIR"/backup-*.sql.gz "$BACKUP_DIR"/pre-update-*T*Z.sql.gz 2>/dev/null || true)
    _prune_oldest "$keep" "backup" "$files"
}

# prune_pre_update_backups [<keep_count>]
# Rollback snapshots only (default: keep 3, paired with prune_snapshots 3 in
# update.sh — a DB backup without its matching config snapshot cannot roll back).
#
# The case filter excludes legacy pre-update-<timestamp> dumps — operator and
# nightly alike, see prune_backups — which share this prefix but belong to the
# churn namespace above. Counting them here would reintroduce exactly the eviction
# this split exists to prevent: being newer, they take the keeper slots and the
# sweep deletes the only real snapshot.
# Returns 0 even when BACKUP_DIR doesn't exist.
prune_pre_update_backups() {
    local keep=${1:-3}
    [ -d "$BACKUP_DIR" ] || return 0
    local all files="" f
    # `ls -1t` for the mtime ordering a glob cannot give; the legacy dumps are
    # filtered in the loop rather than with `ls | grep` (SC2010).
    all=$(ls -1t "$BACKUP_DIR"/pre-update-*.sql.gz 2>/dev/null || true)
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        case "${f##*/}" in
            pre-update-*T*Z.sql.gz) continue ;;
        esac
        files="$files$f
"
    done <<<"$all"
    _prune_oldest "$keep" "pre-update backup" "$files"
}
