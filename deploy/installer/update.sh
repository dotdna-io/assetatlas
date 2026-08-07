#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/installer/update.sh — self-fetching atomic update with auto-rollback
# when the new stack fails to come up or fails its health window.
#
# Default (no flags): resolves the latest published version, compares it to
# what's installed, and if newer, prompts to update now, dry-run first, or
# cancel. On "update now" it downloads + extracts the new bundle into
# INSTALL_DIR, then re-execs itself with --apply-local so the atomic update
# body below runs against the freshly-extracted code.
#
# Self-fetch flags:
#   --dry-run              resolve + download + extract the latest version
#                           into a scratch dir and print a preview (pending
#                           migrations, version jump) without changing
#                           anything.
#   -y, --yes               assume "update now" instead of prompting.
#   --apply-local           skip the self-fetch front-end entirely and run
#                           the atomic update body directly against
#                           INSTALL_DIR (used internally by the front-end's
#                           re-exec after extraction; can also be invoked
#                           directly against an already-extracted tree).
#   --confirm               with --apply-local, ask [u/d/c] before the first
#                           destructive step. install.sh adds this when it
#                           detects an existing install and dispatches here:
#                           the operator asked to install, so applying
#                           unattended would silently reinterpret that as
#                           "back up and upgrade". A bare --apply-local (the
#                           air-gapped entry point) does not prompt.
#
# Behavior (atomic body, reached via --apply-local or the self-fetch apply):
#   - Pre-flight checks (disk, in-flight pipeline, cert)
#   - Version check (refuses non-linear jumps unless --allow-version-skip)
#   - DB backup + state snapshot before any destructive operation, including a
#     baseline of which containers were ALREADY not healthy (#1211)
#   - Apply migrations + pull/load new images + restart services
#   - Health window: poll /api/health/live for 60s, container health for 90s.
#     Only containers that REGRESSED against the baseline fail it, so a host with
#     a pre-existing crash loop can still update
#   - On a failed `up -d` OR a failed health window: AUTO ROLLBACK (restore
#     snapshot, restore DB, restart), then VERIFY the restore — containers,
#     image tags and the recorded version — before calling it a rollback
#   - Verify the running app reports the target version (online only): a WRONG
#     version is FATAL; an answer that never comes is NOT recorded in
#     system_info and the update otherwise completes (see 7b)
#   - On success: update system_info row, prune old backups + snapshots
#
# Operator-invoked rollback (verified like the auto path — containers, image
#   tags, recorded version — with ROLLBACK INCOMPLETE on any miss):
#   update.sh --rollback [v2026.17.30]  # named snapshot, or the most recent
#
# Other flags:
#   --allow-version-skip   bypass linear-version check (for catch-up upgrades)
#   --force                bypass in-flight pipeline check
#   --unattended           no interactive prompts (assumes yes for rollback confirmation)
#   --install-dir PATH     override install directory (default: the tree this
#                          script lives in, or /opt/assetatlas when run from a
#                          checkout rather than from inside an install)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../lib"

# INSTALL_DIR defaults to the tree this script LIVES in, not to a literal.
# update.sh ships inside the install ($INSTALL_DIR/installer/update.sh) and
# install.sh's closing summary tells the operator to run exactly that path —
# with a hardcoded default, running it bare against a non-default install dir
# died at "Compose file not found: /opt/assetatlas/docker-compose.yml", and on
# a host that ALSO carries an /opt install it did something worse than die:
# the update targeted the OTHER install.
#
# Guarded on .install-state actually being there, so a run from a repo CHECKOUT
# (deploy/installer/update.sh, whose parent is deploy/ and is not an install
# tree) still falls back to the literal it has always used. An inherited
# INSTALL_DIR still wins — that is how install.sh hands over across its `exec` —
# and --install-dir, parsed below, wins over both.
#
# This sits ABOVE the library sources for the same reason install.sh derives
# its own default before sourcing them: env.sh, preflight.sh, backup.sh and
# rollback.sh each carry a defensive `: "${INSTALL_DIR:=/opt/assetatlas}"` that
# fires at SOURCE time, and an ASSIGNING default cannot be overridden by a
# later `${INSTALL_DIR:-…}`. Run any later and the literal has already won.
_self_root="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -z "${INSTALL_DIR:-}" ] && [ -f "$_self_root/.install-state" ]; then
    INSTALL_DIR="$_self_root"
fi
INSTALL_DIR="${INSTALL_DIR:-/opt/assetatlas}"

# shellcheck source=../lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=../lib/runtime.sh
source "$LIB_DIR/runtime.sh"
# shellcheck source=../lib/env.sh
source "$LIB_DIR/env.sh"
# shellcheck source=../lib/version.sh
source "$LIB_DIR/version.sh"
# shellcheck source=../lib/preflight.sh
source "$LIB_DIR/preflight.sh"
# shellcheck source=../lib/backup.sh
source "$LIB_DIR/backup.sh"
# shellcheck source=../lib/rollback.sh
source "$LIB_DIR/rollback.sh"
# shellcheck source=../lib/health.sh
source "$LIB_DIR/health.sh"
# shellcheck source=../lib/migrate.sh
source "$LIB_DIR/migrate.sh"
# shellcheck source=../lib/fetch.sh
source "$LIB_DIR/fetch.sh"
# shellcheck source=../lib/update_flow.sh
source "$LIB_DIR/update_flow.sh"
# systemd.sh is sourced further down — see the comment there.
# shellcheck source=../lib/reference_data.sh
source "$LIB_DIR/reference_data.sh"

# Defaults. INSTALL_DIR is resolved at the top of this file, above the library
# sources — see the comment there for why its position is load-bearing.
DO_ROLLBACK=0
ROLLBACK_TAG=""
ALLOW_VERSION_SKIP=0
FORCE=0
UNATTENDED=0
DRY_RUN=0
ASSUME_YES=0
APPLY_LOCAL=0
CONFIRM=0

# Parse args
while [ $# -gt 0 ]; do
    case "$1" in
        --rollback)
            DO_ROLLBACK=1
            # If next arg is a value (not a flag), consume it as the rollback tag.
            if [ $# -ge 2 ] && [[ "$2" != --* ]]; then
                ROLLBACK_TAG="$2"
                shift
            fi
            ;;
        --allow-version-skip) ALLOW_VERSION_SKIP=1 ;;
        --force)              FORCE=1 ;;
        --unattended)         UNATTENDED=1; export UNATTENDED ;;
        --install-dir)        INSTALL_DIR="$2"; shift ;;
        # --rootless is refused rather than ignored: the mode was removed, and
        # silently proceeding under a flag that promises something else is worse
        # than an error. --system stays an accepted no-op so existing automation
        # keeps working.
        --rootless)           die "the rootless install mode was removed and --rootless is no longer accepted. A v2026.32.1 rootless install cannot be updated in place — recreate it system-wide with the installer, then update normally." ;;
        --system)             ;;
        --dry-run)            DRY_RUN=1 ;;
        -y|--yes)             ASSUME_YES=1 ;;
        --apply-local)        APPLY_LOCAL=1 ;;
        # --confirm is added by install.sh's dispatch on an existing install;
        # the gate itself runs after the version check, before the backup.
        --confirm)            CONFIRM=1 ;;
        -h|--help)            sed -n '2,57p' "$0"; exit 0 ;;
        --with-monitoring|--no-monitoring|--no-systemd|--with-demo-data)
            log_info "Ignoring install-only flag on update: $1 (this setting persists from install)." ;;
        --install-mode)
            log_info "Ignoring install-only flag on update: --install-mode $2."; shift ;;
        *) die "Unknown flag: $1" ;;
    esac
    shift
done

if [ "$APPLY_LOCAL" = "1" ] && [ "$DRY_RUN" = "1" ]; then
    die "--dry-run cannot be combined with --apply-local (--apply-local applies an already-extracted bundle and skips the preview)."
fi

cd "$INSTALL_DIR" || die "Install dir not found: $INSTALL_DIR"
export INSTALL_DIR
BACKUP_DIR="$INSTALL_DIR/backups";   export BACKUP_DIR
SNAPSHOT_DIR="$INSTALL_DIR/snapshots"; export SNAPSHOT_DIR
# Re-derive AFTER --install-dir is parsed: env.sh freezes its default from
# INSTALL_DIR at source time — i.e. /opt/assetatlas, sourced above before the
# flags — so without this a non-default install dir has state_get reading a
# .install-state that does not exist: with_monitoring silently reads 0 and
# compose_overlay_args drops the monitoring overlay from every down/up,
# including the rollback's. install.sh and assetatlas-cli both re-derive it
# exactly like this.
INSTALL_STATE_PATH="$INSTALL_DIR/.install-state"; export INSTALL_STATE_PATH

# A v2026.32.1-era rootless install is the one install this updater cannot
# serve: its containers, units and paths all live under the invoking user's
# home and user manager, and there is no in-place migration to a system-wide
# layout. Refuse it up front, before anything on the host is touched. The key
# was only ever written by v2026.32.1; `system` and — for every install that
# predates the key — an absent key both update normally.
if [ "$(state_get privilege_mode system)" = "rootless" ]; then
    die "this install was created by v2026.32.1 in rootless mode, which was removed. There is no in-place migration: recreate the install system-wide (as root or under sudo) and restore a backup into it, then update normally."
fi

# systemd.sh sourced HERE rather than with the rest of the libs above the
# "Defaults" section — a leftover of the ordering the removed privilege
# resolution needed. Nothing between here and the library block references a
# systemd.sh symbol; reconcile_systemd_units is not called until step 10, well
# below.
# shellcheck source=../lib/systemd.sh
source "$LIB_DIR/systemd.sh"

# Detect runtime (sets $RUNTIME, $COMPOSE_CMD)
detect_runtime || die "Container runtime not available."

COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
[ -f "$COMPOSE_FILE" ] || die "Compose file not found: $COMPOSE_FILE (is this a valid install?)"
export COMPOSE_FILE

# Load env from the install
# shellcheck disable=SC1091
[ -f "$INSTALL_DIR/.env" ] && . "$INSTALL_DIR/.env"

# Build PSQL_CMD / PG_DUMP_CMD via the runtime exec
# DB_USER / DB_NAME come from .env or use defaults
DB_USER="${POSTGRES_USER:-assetatlas}"
DB_NAME="${POSTGRES_DB:-assetatlas}"
PSQL_CMD="$RUNTIME exec -i assetatlas-db psql -U $DB_USER -d $DB_NAME"
PG_DUMP_CMD="$RUNTIME exec -i assetatlas-db pg_dump -U $DB_USER -d $DB_NAME"
export PSQL_CMD PG_DUMP_CMD

# The health probe URL, defined ONCE for the update's health window and the
# rollback body alike — two hand-maintained copies of this literal is how the
# two probes would drift onto different ports or paths.
HEALTH_URL="https://localhost:${HTTPS_PORT:-8443}/api/health/live"

# --- Rollback body (shared by --rollback and the auto-rollback below) --------
# run_rollback <tag> <backup_file> <expected_version>
#
# Tear the stack down, restore snapshot <tag> and the DB from <backup_file>,
# bring everything back up, then VERIFY what the rollback claims instead of
# assuming it from an HTTP probe (#1198): the probe only covers what nginx
# fronts, so a broken scheduler / mcp / teams-bridge / metrics-forwarder would
# still read as a successful rollback — which is exactly what the operator's
# --rollback did until #1212. Failures collect into ONE "ROLLBACK INCOMPLETE"
# die naming the full state, because an operator staring at a half-restored
# stack needs the whole list, not the first miss. ONE body on purpose: a second
# copy of a check list is how update.sh --force drifted (#1188).
#
# The single exception is a failed DB restore (#1240): it ends the body on the
# spot instead of being collected, because every check below it runs against the
# database that did not restore. See the guard at the call site for why.
#
# <expected_version> is what system_info should report once the DB is restored.
# The auto path knows it (the version installed when the update started); the
# manual path reads it out of the dump itself. Empty means "could not be
# determined" (a dump predating system_info): that check is then skipped AND
# SAID to be skipped — never counted as verified.
#
# The caller must have pointed HEALTH_BASELINE_FILE at the pre-update container
# baseline of the snapshot being restored: the container check compares against
# it, so a container that was ALREADY broken before that update does not fail
# the rollback that faithfully restored it (#1211).
run_rollback() {
    local tag=$1 backup_file=$2 expected_version=$3
    local overlay_args=() rollback_issues=() up_rc=0 restored_version=""

    # Overlay args on the `down`, computed HERE — before the snapshot overwrites
    # .env/.install-state — so it takes down exactly what is running now.
    # Without them a monitoring install leaves the overlay containers running
    # against a rolled-back stack, and the network teardown collides with them
    # (#1210 fixed the auto path; --rollback kept the old shape until #1212).
    mapfile -t overlay_args < <(compose_overlay_args)
    $COMPOSE_CMD -f "$COMPOSE_FILE" "${overlay_args[@]}" down \
        || log_warn "compose down returned non-zero; proceeding anyway."

    restore_snapshot "$tag"

    # A snapshot written before the overlay entered _SNAPSHOT_FILES (#1212)
    # captured no docker-compose.monitoring.yml, and restore never deletes what
    # a snapshot didn't capture — so on a monitoring install the restored OLD
    # base compose is now paired with the CURRENT overlay file: the exact
    # untested combination the snapshot entry exists to prevent. It cannot be
    # repaired here (the old overlay was never captured), so SAY it instead of
    # letting the pairing go unnoticed. Ages out as post-#1212 snapshots
    # rotate in.
    if [ -f "$INSTALL_DIR/docker-compose.monitoring.yml" ] \
        && [ ! -f "$SNAPSHOT_DIR/$tag/docker-compose.monitoring.yml" ]; then
        log_warn "Snapshot $tag predates overlay snapshotting: the restored base compose is paired with the CURRENT docker-compose.monitoring.yml (a combination neither release was tested with). If monitoring misbehaves after this rollback, re-run the installer to regenerate the overlay."
    fi

    # Bring the db only back up so we can restore into it.
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d timescaledb \
        || log_warn "compose up -d timescaledb returned non-zero; probing the database anyway."
    wait_for_db 60 || die "ROLLBACK INCOMPLETE. State: snapshot $tag restored (config/compose/.install-state from snapshots/$tag), but the database never came back, so the DB restore never ran and the stack is DOWN. Inspect: $COMPOSE_CMD -f $COMPOSE_FILE logs timescaledb"

    # The DB restore REPORTS failure (a non-zero return) rather than dying
    # (#1212) — so this is the one miss the body can ACT on rather than merely
    # collect, and it ends the rollback here (#1240). Everything below brings the
    # stack up and spends ~150s verifying it; against an empty or half-restored
    # schema that hands the API and the scheduler minutes in which to WRITE into
    # it, minutes before the operator can react, and "all containers came back
    # healthy" is describing a stack that must not be serving. The lost checks
    # are a degraded summary; writes into a half-restored database are not
    # recoverable by re-running the restore.
    #
    # Deliberately its own die rather than the collected one below: that summary
    # reads "the restore did not fully verify", which is the wrong sentence for
    # checks that were never run. So this names everything the path DOES know
    # instead — including WHICH of the two DB states it left behind, the
    # distinction #1212 drew. The known way to land here is a dump taken before
    # migration 142 (#1196): it aborts on the post-data matview REFRESH with the
    # schema, data and COPYs already back, leaving only the matviews unpopulated.
    if ! restore_db "$backup_file"; then
        die "ROLLBACK INCOMPLETE. State: snapshot $tag restored (config/compose/.install-state from snapshots/$tag), but the DB restore from $backup_file FAILED (see the restore error above): if the schema drop itself failed the DB is UNTOUCHED — still the post-update one; otherwise it holds exactly what the dump loaded before the error, which for a pre-migration-142 dump is everything but the matview refresh (#1196). The stack was deliberately NOT brought back up: nothing beyond the database container was started again, so nothing can write into a half-restored schema — the container, image-tag and system_info checks were skipped because they could only have described a stack that must not serve. (The teardown at the top of this rollback is best-effort — it only warns — so confirm with the ps below that no api/ui/mcp/scheduler/nginx container survived it, and stop any that did.) To finish, do NOT simply re-run this rollback against the same dump: it DROPs the public schema again before it reloads, so a database you repair in the meantime would be lost, and the known causes (a pre-142 dump, a truncated one) fail identically on every attempt. Either put a dump that restores at $backup_file and re-run '$INSTALL_DIR/installer/update.sh --rollback $tag', or — if the database as it stands is acceptable — bring the stack up yourself with 'assetatlas start' (it re-applies the monitoring overlay if this install uses one) and check it. Inspect: $COMPOSE_CMD -f $COMPOSE_FILE ps; $COMPOSE_CMD -f $COMPOSE_FILE logs timescaledb"
    fi

    # Re-read the overlay args AFTER the snapshot landed: the restored
    # .install-state is what says whether monitoring is opted in, and a rollback
    # across an enable/disable flip must bring up what the RESTORED state wants.
    # `--remove-orphans` because the restored compose file is the OLDER one, so
    # a service that only exists in the new release would otherwise keep running
    # against the rolled-back database.
    mapfile -t overlay_args < <(compose_overlay_args)
    $COMPOSE_CMD -f "$COMPOSE_FILE" "${overlay_args[@]}" up -d --remove-orphans || up_rc=$?
    [ "$up_rc" = "0" ] \
        || rollback_issues+=("compose up -d exited $up_rc (a restored service did not start)")
    wait_for_health "$HEALTH_URL" 60 \
        || rollback_issues+=("the app is not answering $HEALTH_URL")
    # Covers every container in the project, not only the fronted ones — including
    # the monitoring overlay's, even though the library passes only -f
    # "$COMPOSE_FILE": `ps` selects by PROJECT LABEL, not by the compose model, so
    # it lists containers from overlay files and inactive profiles too. (`down`
    # above is the opposite and does go by the model, which is why it needs the
    # overlay args and this does not. Both verified against real compose output.)
    # Presence is covered by the guarded `up -d` above: a container that could not
    # be created fails that, and one that is not running fails this.
    #
    # Against $HEALTH_BASELINE_FILE — the baseline of the snapshot being restored,
    # deliberately: a rollback promises "the stack is back as it was", and the
    # baseline is exactly what "as it was" means. Without that, a container the
    # operator had been ignoring for weeks turned a perfectly good rollback into
    # ROLLBACK INCOMPLETE — half of #1211, and the half that told the operator to
    # go looking for damage the rollback had not done. The baseline is never
    # (re)taken here: at this point the stack is the failed one, so a fresh
    # baseline would exempt everything.
    wait_for_all_containers_healthy 90 \
        || rollback_issues+=("not every container that was healthy before the update came back running and healthy")
    verify_rollback_images "$tag" \
        || rollback_issues+=("app image tags are not back on their pre-update image ids")
    # Catches a DB that came back empty or unreadable — the shape a botched
    # restore actually takes. It does NOT prove the restore moved data: on the
    # auto path system_info is only written on the update's success path, so the
    # row holds <expected_version> throughout a failed update whether or not the
    # restore ran. And it does not fail fully closed: get_installed_version
    # prints 0.0.0-pre-system-info when the query fails, which matches when
    # <expected_version> is that same sentinel.
    if [ -n "$expected_version" ]; then
        restored_version=$(get_installed_version) || restored_version=""
        [ "$restored_version" = "$expected_version" ] \
            || rollback_issues+=("system_info reports '${restored_version:-unreadable}', expected the pre-update $expected_version")
    else
        log_warn "Skipping the system_info check: could not determine what version the restored DB should report (the dump predates system_info, or its system_info row was unreadable)."
    fi

    # "DB restored from" is unconditional now: the only path that reaches this
    # line is one where restore_db succeeded — a failed restore died above.
    if [ "${#rollback_issues[@]}" -gt 0 ]; then
        die "ROLLBACK INCOMPLETE. State: snapshot $tag restored (config/compose/.install-state from snapshots/$tag), DB restored from $backup_file, but the restore did not fully verify: ${rollback_issues[*]}. Inspect: $COMPOSE_CMD -f $COMPOSE_FILE ps; $COMPOSE_CMD -f $COMPOSE_FILE logs"
    fi
    # Deliberately does not claim the image tags: the image check above passes
    # when every image was skipped (online pins are never retagged), and it logs
    # its own checked/skipped counts on the line above. Repeating the claim here
    # would turn a "0 verified" into a verified restore in the operator's log —
    # and the same goes for a version that could not be determined.
    log_ok "Rollback verified: all containers healthy and system_info back on ${expected_version:-<not checked>} (app image tags: see the line above)."
}

# --- Manual rollback path ---
if [ "$DO_ROLLBACK" = "1" ]; then
    if [ -z "$ROLLBACK_TAG" ]; then
        ROLLBACK_TAG=$(list_snapshots | tail -1)
        [ -n "$ROLLBACK_TAG" ] || die "No snapshots available to roll back to."
    fi
    # Validate BOTH halves of the pair before touching the stack: dying on a
    # typo'd tag after the `down` would leave the stack needlessly stopped.
    [ -d "$SNAPSHOT_DIR/$ROLLBACK_TAG" ] \
        || die "Snapshot $ROLLBACK_TAG not found at $SNAPSHOT_DIR/$ROLLBACK_TAG (available: $(list_snapshots | tr '\n' ' '))"
    backup_file="$BACKUP_DIR/pre-update-$ROLLBACK_TAG.sql.gz"
    [ -f "$backup_file" ] || die "Backup file for $ROLLBACK_TAG not found at $backup_file"
    log_warn "Rolling back to snapshot: $ROLLBACK_TAG"
    log_warn "This restores DB and config from snapshot $ROLLBACK_TAG."
    log_warn "Any data written to the DB since the snapshot was taken WILL BE LOST."
    if [ "$UNATTENDED" != "1" ]; then
        prompt_yn "Continue with rollback?" N || die "Rollback cancelled."
    fi

    # The pre-update container baseline the update that TOOK this snapshot
    # recorded (#1211): snapshot_state writes one for every snapshot, so the
    # container check holds the restored stack to "as it was before that
    # update" rather than to perfection. An older snapshot carries none — then
    # nothing is exempt, which only makes the check stricter.
    HEALTH_BASELINE_FILE="$SNAPSHOT_DIR/$ROLLBACK_TAG/$HEALTH_BASELINE_NAME"
    export HEALTH_BASELINE_FILE

    # What the restored DB should report: the version recorded in the dump
    # itself. The snapshot TAG cannot answer this — it names the version the
    # failed-or-regretted update was going TO, while the dump holds the state
    # from BEFORE it.
    #
    # Announce the read: it decompresses the ENTIRE dump (deliberately, see
    # backup_recorded_version) and on a production-sized backup that is minutes
    # of silence right after the operator confirmed — which reads as a hang.
    # And a read that FAILS is positive evidence the dump itself is damaged —
    # that must not be folded into the ordinary "no version recorded" skip,
    # because the DB restore is about to trust this same file.
    log_info "Reading the recorded pre-update version out of $backup_file (one full decompression pass — a large dump takes a while)..."
    if ! EXPECTED_VERSION=$(backup_recorded_version "$backup_file"); then
        EXPECTED_VERSION=""
        log_warn "Reading $backup_file did not complete cleanly (truncated or corrupt dump?). The version check will be skipped — and the DB restore below may fail against this same file."
    fi

    run_rollback "$ROLLBACK_TAG" "$backup_file" "$EXPECTED_VERSION"
    log_ok "Rolled back to $ROLLBACK_TAG."
    exit 0
fi

# --- Self-fetch front-end (skipped for --apply-local, i.e. the bundle is
#     already extracted into $INSTALL_DIR by the header or by our own apply step) ---
if [ "$APPLY_LOCAL" != "1" ]; then
    log_info "Checking for the latest AssetAtlas version..."
    # Run resolve_latest_version in the CURRENT shell (not a command-substitution
    # subshell) so its `export MANIFEST_SHA256` survives for download_bundle to use.
    # A subshell capture (LATEST=$(resolve_latest_version)) would discard the
    # manifest-pinned hash and silently fall back to the same-origin sidecar.
    _manifest_out=$(mktemp)
    resolve_latest_version >"$_manifest_out" || { rm -f "$_manifest_out"; die "Could not determine the latest version (offline?). Aborting; nothing changed."; }
    LATEST=$(cat "$_manifest_out"); rm -f "$_manifest_out"
    CURRENT=$(get_installed_version)
    if [ "$LATEST" = "$CURRENT" ]; then
        log_ok "Already at $CURRENT; nothing to do."
        exit 0
    fi
    log_info "Update available: $CURRENT -> $LATEST"
    MODE=$(update_decide_mode)
    case "$MODE" in
        cancel)  log_info "Update cancelled."; exit 0 ;;
        dry-run)
            TMP_EXTRACT=$(mktemp -d)
            trap 'rm -rf "$TMP_EXTRACT"' EXIT
            BUNDLE=$(download_bundle "$LATEST" "$TMP_EXTRACT") || die "Download failed; nothing changed."
            ASSETATLAS_EXTRACT_ONLY=1 INSTALL_DIR="$TMP_EXTRACT" bash "$BUNDLE" >/dev/null \
                || die "Could not extract the bundle for the dry-run preview."
            update_dry_run_report "$CURRENT" "$LATEST" "$TMP_EXTRACT"
            exit 0
            ;;
        apply)
            TMP_DL=$(mktemp -d)
            trap 'rm -rf "$TMP_DL"' EXIT
            BUNDLE=$(download_bundle "$LATEST" "$TMP_DL") || die "Download failed; nothing changed."
            log_info "Applying update: extracting $LATEST into $INSTALL_DIR ..."
            ASSETATLAS_EXTRACT_ONLY=1 INSTALL_DIR="$INSTALL_DIR" bash "$BUNDLE" \
                || die "Extraction failed; nothing applied (DB untouched)."
            rm -rf "$TMP_DL"
            # Run the FRESHLY-EXTRACTED update code against the already-extracted tree.
            # Forward the update-relevant flags — the atomic body still enforces the
            # linear-version and in-flight-pipeline checks, so dropping these would
            # extract the new code and then die refusing a jump the operator already
            # opted into (--allow-version-skip) or bypassed (--force).
            APPLY_ARGS=(--apply-local --yes --install-dir "$INSTALL_DIR")
            [ "$ALLOW_VERSION_SKIP" = "1" ] && APPLY_ARGS+=(--allow-version-skip)
            [ "$FORCE" = "1" ] && APPLY_ARGS+=(--force)
            [ "$UNATTENDED" = "1" ] && APPLY_ARGS+=(--unattended)
            exec "$INSTALL_DIR/installer/update.sh" "${APPLY_ARGS[@]}"
            ;;
    esac
fi

# --- Standard atomic update ---
log_info "=== AssetAtlas update starting ==="

# 1. Pre-flight (uses our existing preflight_update aggregator)
# --force bypasses the in-flight pipeline check; we handle that by temporarily
# suppressing it via SKIP_INFLIGHT, then running the rest of preflight normally.
if [ "$FORCE" = "1" ]; then
    log_warn "--force specified: skipping in-flight pipeline check."
    SKIP_INFLIGHT=1 preflight_update
else
    preflight_update
fi

# 2. Version check
CURRENT_VERSION=$(get_installed_version)
TARGET_VERSION=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || cat "$SCRIPT_DIR/../../VERSION" 2>/dev/null)
[ -n "$TARGET_VERSION" ] || die "Cannot determine target version: neither $INSTALL_DIR/VERSION nor repo root VERSION file found."
log_info "Current installed: $CURRENT_VERSION  Target: $TARGET_VERSION"

if [ "$CURRENT_VERSION" = "$TARGET_VERSION" ]; then
    log_warn "Already at $TARGET_VERSION; nothing to do."
    exit 0
fi

if [ "$CURRENT_VERSION" != "0.0.0-pre-system-info" ] && [ "$ALLOW_VERSION_SKIP" != "1" ]; then
    if ! is_linear_jump "$CURRENT_VERSION" "$TARGET_VERSION"; then
        die "Non-linear update $CURRENT_VERSION → $TARGET_VERSION. Use --allow-version-skip to override."
    fi
fi

# 2a. The --confirm gate. install.sh adds --confirm when it dispatches on an
# existing install (curl | sudo bash on one): the operator asked to INSTALL,
# and without this prompt the dispatch is an unattended "back up and upgrade
# production". Ask HERE — after the version check so the prompt can name the
# transition (and after "already at X" has exited, so there is something to
# confirm), and BEFORE step 3 takes the DB backup, the first destructive act.
# A bare --apply-local — the only air-gapped entry point — carries no --confirm
# and applies without asking, exactly as before.
if [ "$CONFIRM" = "1" ]; then
    # A refusal (no terminal, no --unattended/-y) already logged itself in
    # update_decide_apply_local; exit without touching anything.
    CONFIRM_MODE=$(update_decide_apply_local "$INSTALL_DIR" "$CURRENT_VERSION" "$TARGET_VERSION") || exit 1
    case "$CONFIRM_MODE" in
        apply) ;;
        cancel)
            log_info "Update cancelled."
            exit 0
            ;;
        dry-run)
            # --apply-local + --dry-run is refused at the flag parser, so run
            # the report directly rather than re-exec: the bundle is already
            # extracted into $INSTALL_DIR, which is exactly the tree its third
            # argument reads.
            update_dry_run_report "$CURRENT_VERSION" "$TARGET_VERSION" "$INSTALL_DIR"
            exit 0
            ;;
    esac
fi

# 3. Backup + snapshot BEFORE any destructive operation
log_info "Backing up current state for rollback..."
backup_db "$TARGET_VERSION" >/dev/null
snapshot_state "$TARGET_VERSION"

# The pre-update container baseline snapshot_state just wrote. BOTH the health
# window (step 7) and the rollback's own container check read it, so a container
# that was ALREADY crash-looping when this update started fails neither — only one
# this release broke does (#1211). Set once, HERE, for two reasons: it must be
# before anything touches the stack (there is no "before" afterwards), and a second
# assignment further down is how the two checks would drift onto different
# baselines — the same failure mode as a hand-written second copy of a check list.
HEALTH_BASELINE_FILE="$SNAPSHOT_DIR/$TARGET_VERSION/$HEALTH_BASELINE_NAME"
export HEALTH_BASELINE_FILE

# 3a. Refresh compose files from the freshly-extracted bundle.
# install.sh copies compose/docker-compose.yml → INSTALL_DIR/docker-compose.yml
# at install time; without this step, compose-file changes between releases
# never reach the deployed location during an update. The snapshot above
# captured the OLD copy, so rollback restores the prior compose on failure.
log_info "Refreshing compose files from $INSTALL_DIR/compose/ ..."
refresh_compose_files

# 4. Apply migrations
log_info "Applying migrations..."
MIGRATIONS_DIR="$INSTALL_DIR/database/migrations"
export MIGRATIONS_DIR
apply_migrations

# 5. Pull/load new images
if [ -d "$SCRIPT_DIR/../images" ] && [ -n "$(ls -A "$SCRIPT_DIR/../images" 2>/dev/null)" ]; then
    IMAGE_SOURCE="offline"
    log_info "Loading bundled images (offline mode)..."
    for tar in "$SCRIPT_DIR/../images"/*.tar*; do
        [ -f "$tar" ] || continue
        log_info "Loading: $(basename "$tar")"
        if [[ "$tar" == *.gz ]]; then
            gunzip -c "$tar" | $RUNTIME load
        else
            $RUNTIME load -i "$tar"
        fi
    done
else
    IMAGE_SOURCE="online"
    log_info "Pulling images from registry (online mode)..."
    # Pin the app images to TARGET_VERSION so the operator no longer has to
    # hand-bump .env, and the deployed images can't drift from the bundle
    # version we later record in system_info (#812). Vendor pins are untouched.
    log_info "Pinning app images to $TARGET_VERSION..."
    # --create-missing: online installs from before #1391 have NO *_IMAGE lines
    # at all, so a pure tag-rewrite pinned nothing and the host kept running
    # :latest; the absent pins are created so those hosts self-heal on this
    # update. Safe HERE specifically: this branch is online-only, and an
    # offline .env always carries all five localhost/* pins, so the create
    # path can never fire on one and write a ghcr.io ref it cannot pull.
    set_app_image_pins "$TARGET_VERSION" "$INSTALL_DIR/.env" --create-missing \
        || die "Failed to pin app images in .env."
    # One-time namespace migration (2026-07): the project moved from the personal
    # account andrei-dotdna to the dotdna-io org. ghcr packages do NOT follow a repo
    # transfer and there is no registry redirect, so an .env still pinned to the old
    # owner would pull a tag that was never published there and this update would 404.
    # set_app_image_pins above rewrites only the tag, never the owner. No-op once
    # migrated, and no-op for a private-registry ref. Online branch only — offline
    # installs pin localhost/* and never reach here.
    set_app_image_owner || die "Failed to migrate app image owner in .env."
    # Pull base + opted-in overlay images in one shot so monitoring
    # doesn't fall back to outdated cached layers on `up -d`.
    mapfile -t OVERLAY_ARGS < <(compose_overlay_args)
    $COMPOSE_CMD -f "$COMPOSE_FILE" "${OVERLAY_ARGS[@]}" pull \
        || die "Image pull failed. Aborting update."
fi

# 6. Restart services
log_info "Restarting services..."
# Re-read overlay args here in case .env was updated during apply_migrations
# (keeps the up command in sync with whatever the operator opted into).
mapfile -t OVERLAY_ARGS < <(compose_overlay_args)
if [ "$(state_get with_monitoring 0)" = "1" ]; then
    log_info "Monitoring overlay enabled — bringing prometheus/alertmanager/exporters up."
fi

# `up -d` is not just a start command: it WAITS on the
# `depends_on: condition: service_healthy` gates that nginx puts on api/ui/mcp (and
# teams-bridge on api), and exits non-zero when one of those dependencies does not go
# healthy — even when the dependent container is already running and is not being
# recreated. So a bad api/ui/mcp image fails HERE, before the health window, and while
# this line was bare under this script's `set -euo pipefail` that aborted the update
# outright: backup taken, snapshot taken, migrations applied, nothing restored. It is
# both the most likely shape of a bad release and the one shape auto-rollback could not
# help with (#1197). Capture the status instead, and converge on the single rollback
# block below.
UPDATE_FAILURE=""
UP_RC=0
$COMPOSE_CMD -f "$COMPOSE_FILE" "${OVERLAY_ARGS[@]}" up -d --remove-orphans || UP_RC=$?
if [ "$UP_RC" != "0" ]; then
    UPDATE_FAILURE="Starting the new stack FAILED (compose up -d exited $UP_RC): a service did not start, or a service_healthy dependency never went healthy."
else
    # 7. Health window
    log_info "Entering health-check window..."
    if ! wait_for_health "$HEALTH_URL" 60 || ! wait_for_all_containers_healthy 90; then
        UPDATE_FAILURE="Health check FAILED within window."
    fi
fi

# 7a. Auto-rollback — the one path both failure modes above take.
if [ -n "$UPDATE_FAILURE" ]; then
    log_error "$UPDATE_FAILURE"
    log_error "AUTO-ROLLBACK initiating."
    # The shared body verifies against the SAME $HEALTH_BASELINE_FILE the health
    # window used — set once at step 3 and never re-pointed, so the window and
    # the rollback cannot drift onto different baselines (#1211).
    run_rollback "$TARGET_VERSION" "$BACKUP_DIR/pre-update-$TARGET_VERSION.sql.gz" "$CURRENT_VERSION"
    die "Update failed and was rolled back to $CURRENT_VERSION."
fi

# 7b. Verify the running app actually reports TARGET before recording it, so a
# pull/recreate that silently didn't take effect can't leave system_info ahead
# of the deployed code (#812). Online only — offline images aren't
# APP_VERSION-stamped.
#
# Probes the SAME $HEALTH_URL the health window (step 7) just proved reachable:
# a hand-built second URL is how this ended up on /api/health, the HEAVY
# endpoint whose 503 on a slow dependency check — read by curl -f as "could not
# reach" — declared a successful update failed and, worse, recommended rolling
# it back (#1389).
#
# verify_deployed_version has THREE outcomes (its header is the contract), and
# only "confirmed" may lead to system_info being recorded:
#   0 confirmed        → record the update, business as usual.
#   1 wrong version    → FATAL: the new code genuinely did not take effect —
#                        the one branch where the .env-pins / --rollback advice
#                        is honest.
#   2 could not verify → NOT fatal: the stack is healthy and running the new
#                        images, so we do NOT record a version we could not
#                        confirm (#812's fail-closed invariant holds) but we DO
#                        finish the bookkeeping — the old die here skipped
#                        steps 8-11 and left the host half-recorded, so the next
#                        update recomputed from the stale row. Never recommend
#                        --rollback on this branch: that would destroy a good
#                        deployment.
VERIFY_STATUS=0
if [ "${IMAGE_SOURCE:-}" = "online" ]; then
    log_info "Verifying the running app reports $TARGET_VERSION..."
    verify_deployed_version "$TARGET_VERSION" "$HEALTH_URL" || VERIFY_STATUS=$?
    if [ "$VERIFY_STATUS" = "1" ]; then
        die "The running app reports a different version than $TARGET_VERSION — refusing to record the update. The new images did not take effect (a pull/recreate left the previous build serving, #812). Migrations/seeds for $TARGET_VERSION are already applied (idempotent, safe) and system_info was left unchanged. Recover by fixing the .env image pins and re-running the installer, or run 'update.sh --rollback'."
    else
        [ "$VERIFY_STATUS" = "0" ] || log_warn "Could not verify that the running app reports $TARGET_VERSION — NOT recording the update in system_info. The stack is healthy and running the new images, so this is NOT a failure and must NOT be rolled back; the remaining bookkeeping still runs. Re-run 'assetatlas update' to re-verify and record."
    fi
fi

# 8. Update system_info row to mark new version installed — ONLY when 7b
# confirmed the deployed version. The could-not-verify path (VERIFY_STATUS=2)
# deliberately skips this: never record a version we could not confirm (#812's
# fail-closed invariant), and the closing summary below says so instead of
# claiming an unqualified completion. Warn, do not die. The update itself has
# already succeeded by this point and the auto-rollback window is closed, so
# aborting over metadata would risk unwinding a healthy deployment. Keeping the
# previous continue-on-failure behaviour — but deliberately and visibly, now
# that update_system_info reports a failed upsert honestly instead of always
# returning 0.
if [ "$VERIFY_STATUS" = "0" ]; then
    update_system_info "$TARGET_VERSION" "$RUNTIME" "$(state_get install_mode online)" "$INSTALL_DIR" \
        || log_warn "Could not record $TARGET_VERSION in system_info. The update succeeded; until this is fixed the UI and the next update's version check read a stale installed version."
fi

# 9. Refresh reference data (MAC-vendor + OS-EOL)
# Runs on every update so a host never sits on stale reference data, and so a
# host that predates install-time seeding gets it. Non-fatal: an update must not
# fail because GitHub is unreachable.
apply_reference_data update

# 10. Reconcile the systemd units with what this bundle ships
# The unit files travel inside the bundle, so a corrected WorkingDirectory, a
# changed ExecStart or a newly added timer reaches an already-deployed host ONLY
# if the update path re-installs them. update.sh previously never touched systemd
# at all, which meant every future unit change was stranded on fresh installs.
# It also repairs hosts installed before the unit was ever started, where the
# unit sits 'inactive (dead)' and `systemctl stop assetatlas` silently does
# nothing. Every step is idempotent, so on a correct host this is a no-op.
#
# Deliberately after the health window and the version check: only reconcile a
# stack that is actually healthy and running the version we think it is. And
# non-fatal — a systemd problem must not fail an otherwise-successful update.
# reconcile_ (not install_): refresh only the units this host already has, so an
# update never reverses a deliberate --no-systemd install.
if ! reconcile_systemd_units; then
    log_warn "systemd units are not fully configured; the update itself succeeded."
fi

# 11. Prune old rollback backups + snapshots (keep last 3 of each — they are
# restored as a pair, so their retentions must match). Operator/nightly backups
# have their own namespace and their own retention; see prune_backups.
prune_pre_update_backups 3
prune_snapshots 3

if [ "$VERIFY_STATUS" = "0" ]; then
    log_ok "=== Update complete: $CURRENT_VERSION → $TARGET_VERSION ==="
else
    # The could-not-verify path (7b): the update DID succeed — new images,
    # healthy stack — so this exits 0 and never mentions --rollback; but the
    # closing line must not claim an unqualified completion, and must name the
    # stale-row consequence and the repair, because the NEXT update recomputes
    # from system_info.
    log_warn "=== Update finished WITHOUT a recorded version: the stack is healthy and running the $TARGET_VERSION images, but the post-update check could not verify that the running app reports $TARGET_VERSION. system_info still reads $CURRENT_VERSION and was deliberately NOT updated — the UI and the next update's version check keep reading that stale row until a confirmed update repairs it. Repair with: assetatlas update ==="
fi
