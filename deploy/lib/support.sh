#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/support.sh — collect a support bundle for diagnostics.
# Requires: common.sh + runtime.sh + env.sh sourced; INSTALL_DIR set.

# _unreadable_hint <path>
# The remedy for an unreadable install file. .install-state and .env are 0600,
# so a non-root operator genuinely does need sudo — the operator docs show
# `support-bundle` without it, which is how that used to produce no bundle at
# all (#1105).
_unreadable_hint() {
    printf '(could not read %s — try sudo)\n' "$1"
}

# collect_support_bundle [output-path]
# Tarballs: container logs, install state, .env (REDACTED), system_info row,
#   schema_migrations row, last 100 pipeline_runs, OS info, runtime info,
#   install/run context (euid, HOME, systemd unit status).
# Default output: /tmp/assetatlas-support-<TIMESTAMP>.tar.gz
collect_support_bundle() {
    local out=${1:-/tmp/assetatlas-support-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}
    local work
    work=$(mktemp -d)
    # Cleared on the success path below, exactly as backup_db does. Left armed,
    # this trap fires at shell exit — after the function has returned and `work`
    # has gone out of scope — so under `set -u` the trap body itself errors,
    # turning a successful run into exit 1 and leaking the temp dir (#1094).
    #
    # Clearing on success is necessary but not sufficient, so the path is also
    # baked in (double quotes) rather than deferred — the same fix, for the same
    # reason, as deploy/bootstrap/install.sh. On any FAILURE path between here
    # and the clear below, the trap fires with the function's locals already
    # gone, so a deferred '$work' reproduces #1094 verbatim: "work: unbound
    # variable" after the real error, and the temp dir leaked exactly when
    # something went wrong.
    #
    # Measured, because the failure mode depends on HOW the shell leaves the
    # function and it is easy to test the shape that hides it:
    #
    #   function RETURNS, shell exits later  -> local already popped on
    #                                           3.2, 5.1 AND 5.2 (all fail)
    #   shell exits from INSIDE the function -> 5.2 pops locals before the
    #     (set -e, the path taken here)         trap; 3.2 and 5.1 do not
    #
    # So bats on macOS (3.2) cannot see the second shape, which is exactly the
    # one this function hits — and RHEL 9 (5.1) hides it too, so a clean run on
    # a RHEL host would prove nothing either. Baking the path in is clean on all
    # three.
    # shellcheck disable=SC2064  # expanding now is the fix, not the bug
    trap "rm -rf -- '$work'" EXIT INT TERM

    log_info "Collecting support bundle..."
    mkdir -p "$work/logs" "$work/config" "$work/diagnostics"

    # Container logs. try_detect_runtime, not `detect_runtime || true`: detect_runtime
    # `die`s (an `exit`, which `|| true` cannot catch) on a too-old runtime, and a
    # bundle is worth collecting from exactly that host.
    try_detect_runtime 2>/dev/null || true
    if [ -n "${RUNTIME:-}" ]; then
        for c in assetatlas-api assetatlas-scheduler assetatlas-mcp assetatlas-ui assetatlas-nginx assetatlas-db assetatlas-redis; do
            $RUNTIME logs --tail=500 "$c" > "$work/logs/$c.log" 2>&1 || echo "(container not running)" > "$work/logs/$c.log"
        done
    fi

    # Install state + redacted .env (strip secrets/passwords).
    #
    # Each step is individually non-fatal and records its own failure INTO the
    # bundle. This function's entire value is being usable on a broken host, so
    # under `set -e` the old `[ -f x ] && cp x dst` was doubly wrong: it aborted
    # the whole command both when the file was ABSENT (a false AND-list is a
    # failed command) and when the copy was DENIED. .install-state and .env are
    # 0600 root:root and the operator docs show this command without sudo, so a
    # normal user hit that every time — no bundle, no error, nothing to report
    # (#1105). "could not read .env: permission denied" in the tarball beats no
    # tarball.
    if [ -f "$INSTALL_DIR/.install-state" ]; then
        cp "$INSTALL_DIR/.install-state" "$work/config/" 2>/dev/null \
            || _unreadable_hint "$INSTALL_DIR/.install-state" \
                 > "$work/config/.install-state.unreadable"
    fi
    if [ -f "$INSTALL_DIR/.env" ]; then
        sed -E 's/(PASSWORD|SECRET|KEY|TOKEN)=.*/\1=<REDACTED>/g' \
            "$INSTALL_DIR/.env" > "$work/config/.env.redacted" 2>/dev/null \
            || _unreadable_hint "$INSTALL_DIR/.env" > "$work/config/.env.redacted"
    fi
    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        cp "$INSTALL_DIR/docker-compose.yml" "$work/config/" 2>/dev/null || true
    fi

    # OS + runtime info
    {
        echo "=== /etc/os-release ==="
        cat /etc/os-release 2>/dev/null || echo "(not available)"
        echo ""
        echo "=== uname -a ==="
        uname -a
        echo ""
        echo "=== runtime ==="
        echo "RUNTIME=${RUNTIME:-unknown}"
        [ -n "${RUNTIME:-}" ] && $RUNTIME --version 2>/dev/null
        [ -n "${COMPOSE_CMD:-}" ] && $COMPOSE_CMD version 2>/dev/null
        echo ""
        echo "=== container status ==="
        [ -n "${RUNTIME:-}" ] && $RUNTIME ps -a 2>/dev/null
        echo ""
        echo "=== disk ==="
        df -h "$INSTALL_DIR" 2>/dev/null
    } > "$work/diagnostics/system.txt" || true
    # `|| true` on the group, not on each line: it suppresses errexit for the
    # whole block, so a failure part-way through still lets the rest run.
    #
    # The hazard is a command that genuinely FAILS — `$RUNTIME --version` and
    # `$RUNTIME ps -a` when the daemon is installed but dead, which is a headline
    # reason to collect a bundle at all. Note it is NOT the `[ -n "$X" ] && cmd`
    # lines: a false test is exempt from errexit (it is not the command following
    # the final &&), so an unset RUNTIME was always survivable. Verified, because
    # guarding the wrong thing here would look like a fix and change nothing.

    # Install/run context: who ran this, against which install, and what the
    # systemd unit says. An euid that is not 0 next to root-owned 0600 files is
    # itself a finding — it explains an unreadable .install-state above.
    {
        echo "=== context ==="
        echo "euid                    : $(id -u) ($(id -un))"
        echo "HOME                    : ${HOME:-<unset>}"
        echo "INSTALL_DIR             : $INSTALL_DIR"
        echo ""
        echo "=== systemd unit ==="
        if ! command -v systemctl >/dev/null 2>&1; then
            echo "(systemctl not installed)"
        else
            systemctl status assetatlas.service --no-pager 2>&1 || true
        fi
    } > "$work/diagnostics/context.txt" || true

    # DB diagnostics (best effort — DB may not be reachable)
    if [ -n "${PSQL_CMD:-}" ]; then
        {
            echo "=== system_info ==="
            $PSQL_CMD -c "SELECT * FROM system_info;" 2>&1 || echo "(query failed)"
            echo ""
            echo "=== schema_migrations (last 10) ==="
            $PSQL_CMD -c "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 10;" 2>&1 || echo "(query failed)"
            echo ""
            echo "=== pipeline_runs (last 50) ==="
            $PSQL_CMD -c "SELECT id, status, started_at, completed_at FROM pipeline_runs ORDER BY started_at DESC LIMIT 50;" 2>&1 || echo "(query failed)"
        } > "$work/diagnostics/database.txt" || true
    else
        echo "(PSQL_CMD not set; skipped DB diagnostics)" > "$work/diagnostics/database.txt"
    fi

    # Install log
    [ -f "${AUDIT_LOG:-}" ] && cp "$AUDIT_LOG" "$work/logs/install.log" 2>/dev/null || true

    # Tar it up. This one IS fatal: a bundle that was not written is not a
    # bundle, and the caller must not be told otherwise.
    tar -czf "$out" -C "$work" . || die "Could not write the support bundle to $out"
    log_ok "Support bundle: $out ($(du -h "$out" | cut -f1))"
    rm -rf "$work"
    trap - EXIT INT TERM
    printf '%s\n' "$out"
}
