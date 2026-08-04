#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/update_flow.sh — decision + dry-run reporting for self-fetching updates.
# Requires: common.sh, version.sh, migrate.sh sourced. PSQL_CMD set for the report.

# update_decide_mode : echoes apply | dry-run | cancel
# Honors DRY_RUN, ASSUME_YES, UNATTENDED; otherwise prompts (default dry-run).
update_decide_mode() {
    if [ "${DRY_RUN:-0}" = "1" ]; then echo "dry-run"; return; fi
    if [ "${ASSUME_YES:-0}" = "1" ] || [ "${UNATTENDED:-0}" = "1" ]; then echo "apply"; return; fi
    local reply
    read -r -p "Proceed with the update, dry-run first, or cancel? [u/d/c] (d): " reply
    case "${reply:-d}" in
        u|U|update)  echo "apply" ;;
        c|C|cancel)  echo "cancel" ;;
        *)           echo "dry-run" ;;
    esac
}

# update_dry_run_report <current> <target> <extracted_dir> : prints the plan, mutates nothing.
update_dry_run_report() {
    local current=$1 target=$2 ext=$3
    log_info "=== Dry run: update preview ==="
    printf '  Current version : %s\n' "$current"
    printf '  Target version  : %s\n' "$target"
    if is_linear_jump "$current" "$target"; then
        printf '  Version jump    : linear (OK)\n'
    else
        printf '  Version jump    : NON-LINEAR — a real update would need --allow-version-skip\n'
    fi
    printf '  Pending migrations:\n'
    local pending pending_st=0
    pending=$(MIGRATIONS_DIR="$ext/database/migrations" list_pending_migrations) || pending_st=$?
    if [ "$pending_st" -ne 0 ]; then
        printf '    (unavailable — could not read schema_migrations; is the database running?)\n'
    elif [ -n "$pending" ]; then
        printf '%s\n' "$pending" | sed 's/^/    - /'
    else
        printf '    (none)\n'
    fi
    printf '  Images          : app images would be pinned to %s and pulled.\n' "$target"
    printf '  Safety          : a DB backup + state snapshot would be taken before applying.\n'
    log_ok "No changes were made. Re-run 'assetatlas update' and choose 'update' (or pass --yes) to apply."
}
