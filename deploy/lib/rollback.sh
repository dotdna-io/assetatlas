#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/rollback.sh — atomic update primitives: snapshot/restore .env+compose+image tags.
# Sourced, never executed directly.
# Requires: common.sh sourced. INSTALL_DIR must be set.
# Optional: RUNTIME (docker|podman) for image-tag retagging; verify_rollback_images
# requires it.
# Optional: health.sh sourced + COMPOSE_CMD/COMPOSE_FILE set, for the pre-update
# container health baseline snapshot_state records (#1211).
#
# Snapshot files: _SNAPSHOT_FILES below (plus the _SNAPSHOT_TREES directories).
# Image retag: localhost/<image>:latest -> localhost/<image>:rollback-from-<tag>.
# Apps retagged: assetatlas-api, assetatlas-ui, assetatlas-mcp, assetatlas-scheduler.

: "${INSTALL_DIR:=/opt/assetatlas}"
: "${SNAPSHOT_DIR:=$INSTALL_DIR/snapshots}"

# Files captured by snapshot_state and restored by restore_snapshot.
# Paths are relative to INSTALL_DIR and are preserved inside the snapshot.
#
# docker/nginx/nginx.conf and conf.d/ are here because they are deployed
# CONFIGURATION that docker-compose.yml bind-mounts, not build artifacts.
# Restoring one without the other pairs an old compose file with a new conf —
# and since the conf now carries ${HTTPS_PORT} for the image's envsubst
# entrypoint, an old compose mounts it straight into conf.d/ with no
# substitution and nginx refuses to start (`unknown "https_port" variable`).
# The post-rollback health check runs THROUGH nginx, so that failure makes the
# rollback itself unrecoverable — a failed update would become an outage.
#
# docker-compose.monitoring.yml is the opted-in overlay (#1212): update.sh's
# refresh_compose_files rewrites it on every update, so restoring only the base
# compose pairs the OLD base with the NEW overlay — a combination neither
# release was tested with. A non-monitoring install has no such file, which
# both loops below tolerate — the entry costs nothing there.
_SNAPSHOT_FILES=(.env docker-compose.yml .install-state docker/nginx/nginx.conf docker-compose.monitoring.yml)

# Directory trees captured alongside the files above, same relative-path rule.
# ssl/ is deliberately NOT snapshotted: the certificate is a per-install
# artifact rather than shipped config, and reinstating an older one on rollback
# would be actively wrong.
_SNAPSHOT_TREES=(docker/nginx/conf.d)

# App images that get retagged for rollback. Vendor images (db, redis, nginx) are
# version-pinned in compose so we don't retag them — restoring the compose file is enough.
_ROLLBACK_IMAGES=(assetatlas-api assetatlas-ui assetatlas-mcp assetatlas-scheduler)

# snapshot_state <tag> : capture current state under $SNAPSHOT_DIR/<tag>/
snapshot_state() {
    local tag=$1
    local dst="$SNAPSHOT_DIR/$tag"
    log_info "Snapshotting state to $dst"
    mkdir -p "$dst"
    local f d
    for f in "${_SNAPSHOT_FILES[@]}"; do
        if [ -f "$INSTALL_DIR/$f" ]; then
            # Path-preserving. For the top-level entries this is byte-identical
            # to the previous `cp … "$dst/"`, so snapshots written by older
            # versions still restore correctly.
            mkdir -p "$dst/$(dirname "$f")"
            cp "$INSTALL_DIR/$f" "$dst/$f"
        fi
    done
    for d in "${_SNAPSHOT_TREES[@]}"; do
        if [ -d "$INSTALL_DIR/$d" ]; then
            mkdir -p "$dst/$d"
            cp -R "$INSTALL_DIR/$d/." "$dst/$d/"
        fi
    done
    # Retag current images (best effort; missing tags are silently skipped)
    if [ -n "${RUNTIME:-}" ]; then
        local img
        for img in "${_ROLLBACK_IMAGES[@]}"; do
            $RUNTIME tag "localhost/$img:latest" "localhost/$img:rollback-from-$tag" 2>/dev/null || true
        done
    fi
    # Pre-update container health baseline (#1211). It belongs in the snapshot
    # because it IS pre-update state: it is what the update's health window AND the
    # rollback's own verification compare against, and prune_snapshots retires it
    # with the snapshot it describes. Deliberately NOT in _SNAPSHOT_FILES — that list
    # means "copied out of INSTALL_DIR, copied back on restore", and this one is
    # generated here and must never be written into the install tree.
    #
    # Guarded exactly like the retag above: health.sh owns the definition of "not
    # healthy" and is not sourced by every caller of this library, and
    # COMPOSE_CMD/COMPOSE_FILE belong to the update path. Without them the snapshot
    # simply carries no baseline, and a health check that finds none holds every
    # container to a healthy result — the behaviour before #1211.
    if declare -F capture_container_baseline >/dev/null 2>&1 \
        && [ -n "${COMPOSE_CMD:-}" ] && [ -n "${COMPOSE_FILE:-}" ]; then
        capture_container_baseline "$dst/$HEALTH_BASELINE_NAME"
    fi
    log_ok "Snapshot saved as '$tag'."
}

# restore_snapshot <tag> : restore snapshot files into INSTALL_DIR; retag images back to :latest
restore_snapshot() {
    local tag=$1
    local src="$SNAPSHOT_DIR/$tag"
    [ -d "$src" ] || die "Snapshot $tag not found at $src"
    log_warn "Restoring snapshot $tag"
    local f d
    for f in "${_SNAPSHOT_FILES[@]}"; do
        if [ -f "$src/$f" ]; then
            mkdir -p "$INSTALL_DIR/$(dirname "$f")"
            cp -f "$src/$f" "$INSTALL_DIR/$f"
        fi
    done
    for d in "${_SNAPSHOT_TREES[@]}"; do
        if [ -d "$src/$d" ]; then
            mkdir -p "$INSTALL_DIR/$d"
            cp -Rf "$src/$d/." "$INSTALL_DIR/$d/"
        fi
    done
    if [ -n "${RUNTIME:-}" ]; then
        local img
        for img in "${_ROLLBACK_IMAGES[@]}"; do
            $RUNTIME tag "localhost/$img:rollback-from-$tag" "localhost/$img:latest" 2>/dev/null || true
        done
    fi
    log_ok "Snapshot $tag restored."
}

# verify_rollback_images <tag>
# Confirm that restore_snapshot's retag actually took: for every app image that HAS a
# localhost/<img>:rollback-from-<tag> tag, localhost/<img>:latest must resolve to the
# SAME image id. Without this, "rolled back successfully" is claimed from an HTTP probe
# that cannot see which image is behind it (#1198).
#
# An image with NO rollback-from-<tag> tag is SKIPPED, not failed. snapshot_state only
# ever retags localhost/*, so against the online ghcr.io/* pins there was nothing to
# retag, the image half of a rollback is a documented no-op, and reporting failure would
# blame the rollback for something it never promised (.claude/rules/deploy.md,
# Backup / restore). The counts are logged either way so "0 checked" is visible rather
# than reading as a verified restore.
#
# Reports EVERY mismatch (not just the first) so one log line covers the whole stack.
# Returns 0 when every checked image matches — including when none was checkable —
# and 1 when at least one image is still on the post-update id.
verify_rollback_images() {
    local tag=$1
    : "${RUNTIME:?verify_rollback_images: RUNTIME must be set}"
    [ -n "$tag" ] || { log_error "verify_rollback_images: tag required"; return 1; }
    local img live saved checked=0 skipped=0 rc=0
    for img in "${_ROLLBACK_IMAGES[@]}"; do
        # `|| saved=""`, not a bare assignment: a missing tag is the expected case on
        # an online install and must not take the caller down under `set -e`.
        saved=$($RUNTIME image inspect --format '{{.Id}}' "localhost/$img:rollback-from-$tag" 2>/dev/null) || saved=""
        if [ -z "$saved" ]; then
            skipped=$((skipped + 1))
            continue
        fi
        live=$($RUNTIME image inspect --format '{{.Id}}' "localhost/$img:latest" 2>/dev/null) || live=""
        checked=$((checked + 1))
        if [ "$live" != "$saved" ]; then
            log_error "  localhost/$img:latest is '${live:-<missing>}', expected the pre-update $saved"
            rc=1
        fi
    done
    if [ "$rc" = "0" ]; then
        log_ok "App image tags match snapshot $tag ($checked verified, $skipped not retagged at snapshot time)."
    else
        log_error "App image tags were NOT restored to their pre-update ids ($checked checked against snapshot $tag)."
    fi
    return "$rc"
}

# list_snapshots : print snapshot tags one per line, oldest version first.
# Tags are YYYY.WW.N version strings, so the sort must be numeric per
# dot-field: plain alphabetical sort puts 2026.31.9 AFTER 2026.31.13, and
# `update.sh --rollback` (no tag) takes `tail -1` as "most recent" — with a
# double-digit N in the set that silently restored the OLDEST snapshot and
# then verified it against itself. (`sort -t. -kN,Nn` rather than `sort -V`:
# BSD sort on the macOS dev machines predates -V's guarantees.)
list_snapshots() {
    [ -d "$SNAPSHOT_DIR" ] || return 0
    local d
    for d in "$SNAPSHOT_DIR"/*/; do
        [ -d "$d" ] || continue
        basename "$d"
    done | sort -t. -k1,1n -k2,2n -k3,3n
}

# prune_snapshots [<keep_count=3>] : remove oldest snapshots past the Nth
prune_snapshots() {
    local keep=${1:-3}
    [ -d "$SNAPSHOT_DIR" ] || return 0
    local count=0 deleted=0
    # ls -1t sorts newest-first by mtime; we keep the first <keep> and delete the rest.
    local dirs
    if dirs=$(ls -1t "$SNAPSHOT_DIR" 2>/dev/null); then
        while IFS= read -r d; do
            [ -z "$d" ] && continue
            count=$((count + 1))
            if [ "$count" -gt "$keep" ]; then
                log_info "Pruning old snapshot: $d"
                rm -rf "${SNAPSHOT_DIR:?}/$d"
                deleted=$((deleted + 1))
            fi
        done <<<"$dirs"
    fi
    [ "$deleted" -gt 0 ] && log_ok "Pruned $deleted old snapshot(s); kept $keep."
    return 0
}
