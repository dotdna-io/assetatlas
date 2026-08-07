#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/update_flow.sh — decision + dry-run reporting for self-fetching updates.
# Requires: common.sh, version.sh, migrate.sh, runtime.sh (runtime_have_tty)
# sourced. PSQL_CMD set for the report.

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

# update_decide_apply_local <install_dir> <current_version> <target_version> :
# echoes apply | dry-run | cancel
#
# The decision update.sh's --confirm flag asks for: install.sh found an
# existing install and dispatched here, so the freshly-extracted bundle is
# about to be APPLIED as an update — DB backup, migrations, image pull,
# container recreate — to a host the operator only asked to *install*.
# One decision with the same [u/d/c] semantics as update_decide_mode, but with
# the version transition stated up front; the atomic body would otherwise
# print it only after the point of no return. The result word is the only
# stdout; the operator-facing lines go to stderr so callers can capture with
# $(...) the same way they capture update_decide_mode.
#
# <current_version> is what the caller's version check already read out of
# get_installed_version — pass it verbatim. Its failure answer, the
# 0.0.0-pre-system-info sentinel (or an empty value), degrades the prompt to
# the unadorned form with no version numbers: an unreadable installed version
# must be neither printed nor a new way for this prompt to fail.
update_decide_apply_local() {
    local install_dir=$1 current=$2 target=$3
    # update_decide_mode's own short-circuits, mirrored so the tty refusal
    # below can never fire on a run that was never going to prompt: install.sh
    # forwards --unattended / -y through the same dispatch that adds --confirm.
    if [ "${DRY_RUN:-0}" = "1" ]; then echo "dry-run"; return; fi
    if [ "${ASSUME_YES:-0}" = "1" ] || [ "${UNATTENDED:-0}" = "1" ]; then echo "apply"; return; fi
    # A bare `read` with no terminal returns empty and would silently land on
    # update_decide_mode's dry-run default — a fall-through, not a decision —
    # so refuse instead. The curl | sudo bash path HAS a tty (the bootstrap
    # runs the bundle with </dev/tty), so arriving here without one means
    # stdin was never a terminal. runtime_have_tty, not a local [ -t 0 ]: one
    # probe, overridable in tests (bats stdin is never a terminal).
    if ! runtime_have_tty; then
        log_error "No terminal for interactive prompts. Re-run attached to a terminal, or pass --unattended."
        return 1
    fi
    case "$current" in
        ""|0.0.0-pre-system-info)
            printf 'Existing install detected at %s.\n' "$install_dir" >&2
            printf 'This will UPDATE it: back up the database, apply migrations,\npull new images and recreate the containers.\n' >&2
            ;;
        *)
            printf 'Existing install detected at %s (%s).\n' "$install_dir" "$current" >&2
            printf 'This will UPDATE it to %s: back up the database, apply migrations,\npull new images and recreate the containers.\n' "$target" >&2
            ;;
    esac
    update_decide_mode
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
