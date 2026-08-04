#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/reference_data.sh — seed/refresh MAC-vendor + OS-EOL reference data.
# Requires: common.sh sourced; RUNTIME set; the api container running.
#
# Why this exists: a fresh install lands with ZERO mac_vendor_ranges rows. There
# is no COPY block for it in schema-full.sql and the migrations that touch it are
# DDL-only, so MAC-vendor enrichment silently resolves nothing until an admin
# happens to find Settings > Data > Reference Data. Nothing errors; the pipeline
# just under-resolves, which is the hardest kind of problem to notice.
#
# Runs in-process inside the api container rather than against the HTTP API: the
# reference-data endpoints are @jwt_required + @require_admin, so calling them
# would mean logging in as the admin account we have only just seeded (which may
# carry TOTP). Same trick install.sh already uses to bcrypt that password.
#
# ALWAYS non-fatal. Reference data is valuable, not load-bearing: a GitHub
# outage, a proxy, or an air-gapped host must never fail an install or an update.
#
# Overridable for tests:
#   REFERENCE_BUNDLE_FILE : path to a bundle tarball shipped in the artifact
#   NO_REFERENCE_DATA     : "1" disables entirely (--no-reference-data)

: "${REFERENCE_APPLY_MODULE:=src.modules.reference_data.apply}"

# reference_bundle_path
# The bundle baked into an offline artifact, if present. build-offline.sh
# downloads the newest one at build time so air-gapped installs get data with no
# manual step.
reference_bundle_path() {
    if [ -n "${REFERENCE_BUNDLE_FILE:-}" ]; then
        printf '%s' "$REFERENCE_BUNDLE_FILE"
        return 0
    fi
    local f
    for f in "$SCRIPT_DIR/../reference"/*.tar.gz "$INSTALL_DIR/reference"/*.tar.gz; do
        [ -f "$f" ] || continue
        printf '%s' "$f"
        return 0
    done
    return 1
}

# apply_reference_data [context]
# context is only used in log lines ("install" / "update").
# Always returns 0 — see the non-fatal note above.
apply_reference_data() {
    local context="${1:-install}"

    if [ "${NO_REFERENCE_DATA:-0}" = "1" ]; then
        log_info "Reference data: skipped (--no-reference-data)."
        return 0
    fi

    local bundle rc=0 err
    # The module runs inside the api container, so it drags the whole Flask app
    # init along with it: an authlib deprecation warning, several JSON log
    # records, and an ERROR-level traceback for the chat provider that is not yet
    # configured on a fresh install. All of that is normal, and all of it used to
    # land in the operator's terminal between two tidy progress lines — a customer
    # reasonably reads a Python traceback mid-install as a failure (#1098).
    #
    # So: stderr is captured, appended to the install log unconditionally (a
    # later diagnosis must not lose it), and only echoed to the terminal when the
    # step actually failed — which is the one time it means something.
    err=$(mktemp)
    if bundle=$(reference_bundle_path); then
        # Prefer the shipped bundle: an offline host cannot reach GitHub, and an
        # online host gets an identical, already-verified artifact without a
        # network round trip.
        log_info "Reference data: applying bundle shipped with this release..."
        $RUNTIME exec -i assetatlas-api python -m "$REFERENCE_APPLY_MODULE" \
            --stdin --label "$(basename "$bundle")" --initiated-by "$context" \
            < "$bundle" 2>"$err" || rc=$?
    else
        log_info "Reference data: fetching the latest bundle..."
        $RUNTIME exec -i assetatlas-api python -m "$REFERENCE_APPLY_MODULE" \
            --latest --initiated-by "$context" 2>"$err" || rc=$?
    fi

    if [ -s "$err" ]; then
        {
            printf '=== reference-data stderr (exit %s) ===\n' "$rc"
            cat "$err"
        } >> "$AUDIT_LOG" 2>/dev/null || true
        # Exit 2 is the air-gapped/no-network skip, not a failure — the case
        # below already explains it in plain words. Dumping app-init noise there
        # would put #1098 straight back on the path most likely to hit it: an
        # online artifact on a host that cannot reach GitHub.
        case "$rc" in
            0|2) ;;
            *) cat "$err" >&2 ;;
        esac
    fi
    rm -f "$err"

    case "$rc" in
        0) log_ok "Reference data: up to date." ;;
        2)
            # Expected on an air-gapped host with no bundle in the artifact.
            log_warn "Reference data: could not reach the bundle repo; skipping."
            log_info "  MAC-vendor and OS-EOL enrichment will under-resolve until you apply a bundle"
            log_info "  from Settings > Data > Reference Data (Upload bundle works offline)."
            ;;
        *)
            log_warn "Reference data: could not be applied (exit $rc); continuing."
            log_info "  Apply it later from Settings > Data > Reference Data."
            ;;
    esac
    return 0
}
