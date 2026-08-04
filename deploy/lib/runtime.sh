#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/runtime.sh — detect container runtime and abstract per-runtime quirks.
# Requires: deploy/lib/common.sh sourced first.
#
# Exports after detect_runtime:
#   RUNTIME       : "docker" or "podman"
#   COMPOSE_CMD   : "docker compose" or "podman-compose"
#   MIN_DOCKER_VERSION="24.0"  MIN_PODMAN_VERSION="4.6"  MIN_PODMAN_COMPOSE_VERSION="1.1.0"
#
# There is deliberately no VOLUME_FLAG. It existed here for years, set to ":Z"
# on the podman path "for SELinux", exported — and read by nothing outside
# deploy/tests, so SELinux labelling never actually happened and RHEL installs
# hung (#1325). Labels now live where they take effect, as `:z` literals on the
# bind mounts in deploy/compose/*.yml; see the note at the top of
# docker-compose.yml for why `:z` and not the `:Z` this variable named.
#
# COMPOSE_CMD names the compose interface for the detected runtime — which on
# the podman path may be a podman-compose that is not installed yet. Detection
# answers WHICH interface drives the host; whether that interface is present
# and new enough is pre-flight's verdict (check_podman_compose_presence /
# check_podman_compose_version in preflight.sh), reported there with a
# per-distro remedy before anything is configured, mutated or pulled. For the
# callers that never run pre-flight (assetatlas-cli's cmd_*), compose_cmd
# repeats that presence check at invocation time and prints the same remedy
# (podman_compose_install_help) — one copy, so the two cannot drift.
#
# Caller-set variables (required by compose_cmd):
#   COMPOSE_FILE  : path to docker-compose.yml (the installer/update sets this before calling compose_cmd)

MIN_DOCKER_VERSION="24.0"
MIN_PODMAN_VERSION="4.6"
# The podman-compose floor lives here, not in preflight.sh, because pre-flight
# is not its only consumer: compose_cmd names it in the missing-binary message
# on hosts where pre-flight never ran. preflight.sh derives
# PREFLIGHT_MIN_PODMAN_COMPOSE from it, so the two cannot drift.
MIN_PODMAN_COMPOSE_VERSION="1.1.0"

_detect_docker() {
    command -v docker >/dev/null 2>&1 && docker --version >/dev/null 2>&1 || return 1
    RUNTIME="docker"
    COMPOSE_CMD="docker compose"
    export RUNTIME COMPOSE_CMD
    runtime_version_check docker || die "Docker version too old (need >= $MIN_DOCKER_VERSION)"
    log_info "Detected runtime: docker"
}

# podman-compose is not packaged in RHEL BaseOS/AppStream, so on the primary
# production topology it is nearly always a `pip3 install`, which puts the
# console script in /usr/local/bin. RHEL's sudo ships
# `Defaults secure_path = /sbin:/bin:/usr/sbin:/usr/bin` — no /usr/local/bin —
# so the installer, which runs under `curl … | sudo bash`, cannot see a
# podman-compose that is demonstrably installed. That produced an unescapable
# loop: pre-flight failed with "not on PATH", its own remedy said to pip-install
# it, and pip reported "Requirement already satisfied" (#1325).
#
# Appended, never prepended: /usr/local/bin must not shadow a system binary just
# because the installer touched PATH. Exported so every child — the compose
# calls, assetatlas-cli, the systemd unit's ExecStart — inherits it.
#
# PODMAN_COMPOSE_LOCAL_BIN is the test seam, in the shape of PREFLIGHT_JQ_BIN.
_add_local_bin_to_path() {
    local dir="${PODMAN_COMPOSE_LOCAL_BIN:-/usr/local/bin}"
    command -v podman-compose >/dev/null 2>&1 && return 0
    [ -x "$dir/podman-compose" ] || return 0
    case ":$PATH:" in
        *":$dir:"*) return 0 ;;
    esac
    PATH="$PATH:$dir"
    export PATH
    log_info "podman-compose: found $dir/podman-compose, which was not on PATH (sudo's secure_path excludes $dir on RHEL). Added $dir to PATH for this run."
}

_detect_podman() {
    command -v podman >/dev/null 2>&1 && podman --version >/dev/null 2>&1 || return 1
    RUNTIME="podman"
    COMPOSE_CMD="podman-compose"
    export RUNTIME COMPOSE_CMD
    _add_local_bin_to_path
    # No podman-compose presence check here, on purpose. "podman-compose" is
    # the correct answer for a podman host whether or not the binary is
    # installed yet — it names exactly what the remedy must install, and the
    # pair stays coherent (never RUNTIME=podman with a Docker compose). The old
    # `die "podman-compose not installed"` here fired AFTER the bundle had been
    # downloaded, verified and extracted, and named no remedy — on RHEL 9 the
    # obvious one (`yum install podman-compose`) itself fails, because the
    # package is in EPEL. Presence is a host-readiness verdict, so it lives in
    # pre-flight (check_podman_compose_presence, reached from install.sh's
    # preflight_install via check_podman_compose_version, and from update.sh's
    # preflight_update), which reports it with the per-distro remedy before
    # anything is configured, mutated or pulled — and in compose_cmd's
    # invocation-time guard, which covers the callers that never run pre-flight
    # (assetatlas-cli) with the same remedy text.
    runtime_version_check podman || die "Podman version too old (need >= $MIN_PODMAN_VERSION)"
    log_info "Detected runtime: podman"
}

# detect_runtime
# Docker first, Podman as fallback — by availability, not by OS. A host with
# only Podman (RHEL 9, the primary production topology) picks Podman; a host
# with both picks Docker.
detect_runtime() {
    _detect_docker && return 0
    _detect_podman && return 0
    log_error "No supported container runtime found. Install Docker 24+ or Podman 4.6+."
    return 1
}

# try_detect_runtime
# detect_runtime for best-effort / diagnostic callers: sets RUNTIME when it can,
# returns 1 when it cannot, and NEVER takes the shell down with it.
#
# detect_runtime does not merely return 1 on a bad host — it `die`s (a too-old
# runtime), and `die` is an `exit`. A `|| true` at the call site cannot catch an
# `exit`, so `support-bundle` on a host with Docker 23 produced no bundle at
# all, which is precisely the host you need one from.
# The subshell absorbs that exit; the repeat call is then known not to die, so
# RUNTIME/COMPOSE_CMD still land in the caller's shell. The reason a host was
# rejected still reaches stderr — only the duplicate success line is suppressed.
try_detect_runtime() {
    ( detect_runtime >/dev/null ) || return 1
    detect_runtime
}

runtime_version_check() {
    local rt=$1
    local ver
    local min
    case "$rt" in
        docker)
            ver=$(docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
            min=$MIN_DOCKER_VERSION
            ;;
        podman)
            ver=$(podman --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
            min=$MIN_PODMAN_VERSION
            ;;
        *) return 1 ;;
    esac
    [ -n "$ver" ] || return 1
    # POSIX-ish version compare via sort -V
    [ "$(printf '%s\n%s\n' "$min" "$ver" | sort -V | head -1)" = "$min" ]
}

# Wrapper for runtime-agnostic compose commands.
# Usage: compose_cmd up -d
#
# Also the podman-compose PRESENCE GUARD for every caller that never runs
# pre-flight. install.sh and update.sh reach their compose calls only after
# check_podman_compose_presence has passed, but assetatlas-cli has no
# pre-flight — and a host can lose podman-compose AFTER a successful install
# (a pip upgrade, a package removal). Without this check `assetatlas start` on
# such a host failed with a bare `podman-compose: command not found` from the
# shell: no remedy, no indication of what to install. The remedy printed is
# pre-flight's own (podman_compose_install_help) — one copy, so the two cannot
# drift.
#
# Scoped to the podman path exactly like the pre-flight check: `docker compose`
# is a plugin of the engine detect_runtime already validated, so a Docker host
# has nothing to re-check per call and is untouched by this guard.
compose_cmd() {
    : "${COMPOSE_FILE:?compose_cmd: COMPOSE_FILE must be set by caller}"
    if [ "${COMPOSE_CMD:-}" = "podman-compose" ] \
        && ! command -v podman-compose >/dev/null 2>&1; then
        log_error "podman-compose: 'podman-compose' is not on PATH. podman is this host's container runtime, and the compose files are driven through podman-compose — AssetAtlas needs podman-compose >= $MIN_PODMAN_COMPOSE_VERSION (the first release that honours 'profiles:')."
        local line
        while IFS= read -r line; do log_error "$line"; done <<<"$(podman_compose_install_help)"
        log_error "  Then re-run this command."
        return 1
    fi
    # shellcheck disable=SC2086
    $COMPOSE_CMD -f "$COMPOSE_FILE" "$@"
}

# compose_overlay_args
# Echoes the `-f overlay -f overlay --profile p` chunks the operator opted
# into at install time, so any caller (install.sh, update.sh, assetatlas-cli)
# brings up exactly the same set of containers without duplicating logic.
#
# Reads:
#   $INSTALL_DIR              — install root (caller must have set or sourced .install-state)
#   `state_get with_monitoring`  — 1/0 monitoring overlay opt-in
#   $RUNTIME                  — detect_runtime's answer; every caller runs it first
#
# Requires: env.sh sourced for state_get.
compose_overlay_args() {
    : "${INSTALL_DIR:?compose_overlay_args: INSTALL_DIR must be set by caller}"
    local args=()
    local monitoring="$INSTALL_DIR/docker-compose.monitoring.yml"
    if [ -f "$monitoring" ] && [ "$(state_get with_monitoring 0)" = "1" ]; then
        args+=("-f" "$monitoring")
        # cadvisor sits behind this profile. Omitting it is the whole mechanism:
        # a profiled service simply is not part of the project unless its
        # profile is active. It is added only under RUNTIME=docker, for two
        # reasons.
        #
        # podman-compose only learned `--profile` in 1.1.0, and Ubuntu and the
        #   distro RHEL builds still ship 1.0.x, where it is an
        #   `unrecognized arguments: --profile` hard failure. Without this
        #   condition the flag would go onto the very first
        #   `assetatlas start`/`stop`/`status` and every update.sh compose
        #   call on RHEL 9 + Podman — the primary production topology — and break
        #   a stack that worked before the upgrade.
        #   It is also the better answer on the merits, not merely a
        #   compatibility workaround: the cadvisor service is configured for the
        #   DOCKER factory specifically (it bind-mounts /var/lib/docker, and the
        #   image is pinned for the Docker API version its client must speak), so
        #   under podman it yields no usefully-labelled container_* series
        #   whatever the profile says. A podman host loses nothing real by not
        #   starting it — the Containers dashboard already tells operators as
        #   much — and gains not being broken.
        #
        # $RUNTIME is exported by detect_runtime, and every caller runs it
        # first: assetatlas-cli's cmd_* each call it before
        # _compose_with_overlays, and update.sh calls it at the top, above
        # run_rollback and both `up` paths. An empty $RUNTIME therefore means a
        # new call site forgot, and this fails closed — losing cadvisor beats
        # handing podman-compose an argument it rejects.
        if [ "${RUNTIME:-}" = "docker" ]; then
            args+=("--profile" "privileged")
        fi
    fi
    # Guard `${args[@]}` expansion: empty arrays under `set -u` (bats default)
    # raise "unbound variable" in older bash; the explicit length check
    # avoids the trap and keeps the empty-output contract clean.
    if [ ${#args[@]} -gt 0 ]; then
        printf '%s\n' "${args[@]}"
    fi
}

# ---------------------------------------------------------------------------
# Image-set verification
# ---------------------------------------------------------------------------
# `podman-compose pull` EXITS 0 after failing to pull images — measured on
# podman-compose 1.0.6 (the Ubuntu/RHEL distro build): five images failed to
# resolve and `echo $?` still printed 0. So install.sh's `pull || die` never
# fired; the install went on to `up -d`, the containers died one at a time, and
# the first thing the operator saw was the API health poll timing out 60 s later,
# which sends them to read container logs for what was a registry failure.
#
# Same family as psql exiting 0 without ON_ERROR_STOP and check_cert returning 0
# for a missing file: do not trust the exit code of the step, verify its
# POSTCONDITION — the images are actually here — the way verify_rollback_images
# verifies a retag instead of assuming it took.

# _compose_expand_vars <string>
# Expands ${VAR}, ${VAR:-default} and ${VAR-default} from the environment, which
# is how compose renders an `image:` value. An unterminated ${ is left verbatim
# rather than looped on.
_compose_expand_vars() {
    local s=$1 out="" pre rest tok name def
    while [[ "$s" == *'${'* ]]; do
        pre=${s%%'${'*}
        rest=${s#*'${'}
        [[ "$rest" == *'}'* ]] || break
        out+=$pre
        tok=${rest%%\}*}
        s=${rest#*\}}
        if [[ "$tok" == *:-* ]]; then
            name=${tok%%:-*}; def=${tok#*:-}
        elif [[ "$tok" == *-* ]]; then
            # `${VAR-default}` keeps a set-but-empty value where `:-` replaces it.
            # Folded into the same branch on purpose: an empty image ref is
            # invalid either way, so the distinction cannot change an answer here.
            name=${tok%%-*}; def=${tok#*-}
        else
            name=$tok; def=""
        fi
        if [ -n "${!name:-}" ]; then out+=${!name}; else out+=$def; fi
    done
    printf '%s\n' "$out$s"
}

# _compose_emit_image <raw-image-value> <profiled-flag>
# Prints one expanded image reference, or nothing when the service had no image
# or sits behind a profile.
_compose_emit_image() {
    local raw=$1
    [ -n "$raw" ] || return 0
    [ "$2" = "0" ] || return 0
    raw=${raw%%[[:space:]]#*}                     # end-of-line comment
    raw=${raw%"${raw##*[![:space:]]}"}            # trailing whitespace / CR
    raw=${raw#\"}; raw=${raw%\"}
    raw=${raw#\'}; raw=${raw%\'}
    [ -n "$raw" ] || return 0
    _compose_expand_vars "$raw"
}

# compose_image_refs <compose-file> [<compose-file> ...]
# One image reference per line: the `image:` value of every service in the given
# files, with ${VAR:-default} expanded from the environment the way compose
# would. install.sh's offline branch exports its five app-image pins before
# calling this, so those are honoured; nothing else sets an image var at install
# time. A caller that runs AFTER set_app_image_pins has written .env (update.sh)
# would have to load those assignments first — compose reads .env, this does not.
#
# Services carrying a `profiles:` key are SKIPPED. A profiled service is not part
# of the project unless its profile is selected (pgadmin/tools,
# metrics-forwarder/forwarding, cadvisor/privileged), and nothing may demand an
# image for a container that will never be started — a check that fails a healthy
# install is worse than the gap it leaves. install.sh activates at most
# `privileged`, whose one member is cadvisor, and a missing cadvisor image still
# surfaces at `up -d`.
#
# Hand-rolled rather than `compose config --images`: podman-compose 1.0.6 has no
# such subcommand, and this has to answer for the runtime that is installed.
# Later files ADD services here, they do not re-image an existing one (the
# monitoring overlay's `api:` block sets environment only) — a service given a
# different image in a second file would be listed twice and both refs required.
compose_image_refs() {
    local file line img profiled in_services
    for file in "$@"; do
        [ -f "$file" ] || continue
        in_services=0; img=""; profiled=0
        while IFS= read -r line || [ -n "$line" ]; do
            # A top-level key closes the previous service. Only `services:` opens
            # a block whose two-space children are services — `volumes:` and
            # `networks:` have children indented identically.
            if [[ "$line" =~ ^[A-Za-z_] ]]; then
                _compose_emit_image "$img" "$profiled"
                img=""; profiled=0
                if [ "${line%%:*}" = "services" ]; then in_services=1; else in_services=0; fi
                continue
            fi
            [ "$in_services" = 1 ] || continue
            # Exactly two spaces then a non-comment character: a service name.
            if [[ "$line" =~ ^\ \ [^[:space:]#] ]]; then
                _compose_emit_image "$img" "$profiled"
                img=""; profiled=0
                continue
            fi
            if [[ "$line" =~ ^[[:space:]]+image:[[:space:]]*(.+)$ ]]; then
                img="${BASH_REMATCH[1]}"
            elif [[ "$line" =~ ^[[:space:]]+profiles:[[:space:]]* ]]; then
                profiled=1
            fi
        done < "$file"
        _compose_emit_image "$img" "$profiled"
    done
}

# verify_compose_images <compose-file> [<compose-file> ...]
# 0 when every image those files name is in local image storage; 1 otherwise,
# having named each missing reference.
#
# `image inspect`, not `image exists`: the latter is podman-only (`docker image
# exists` is "unknown command"), and this must answer for both runtimes.
#
# Reading no reference at all is a FAILURE, not a pass: a compose reformat that
# stopped matching would otherwise report "all present" over an empty set for
# ever — the shape of every silent-success trap this file exists to close.
verify_compose_images() {
    : "${RUNTIME:?verify_compose_images: RUNTIME must be set by caller}"
    local refs ref seen="|" total=0
    local missing=()
    refs=$(compose_image_refs "$@")
    if [ -z "$refs" ]; then
        log_error "No image reference could be read from: $*"
        return 1
    fi
    while IFS= read -r ref; do
        [ -n "$ref" ] || continue
        # One inspect per distinct ref: api/scheduler/mcp are three services on
        # one published image in an offline install.
        case "$seen" in *"|$ref|"*) continue ;; esac
        seen="$seen$ref|"
        total=$((total + 1))
        $RUNTIME image inspect "$ref" >/dev/null 2>&1 || missing+=("$ref")
    done <<<"$refs"
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "${#missing[@]} of $total image(s) are NOT in local $RUNTIME storage:"
        for ref in "${missing[@]}"; do
            log_error "    $ref"
        done
        return 1
    fi
    log_ok "All $total images are present locally."
    return 0
}

# refresh_compose_files: copy the staged compose files from
# $INSTALL_DIR/compose/ into $INSTALL_DIR root, where update.sh / assetatlas-cli
# read them.
#
# install.sh does this copy on first install; update.sh must repeat it so that
# compose-file changes between releases reach the deployed location. The
# self-extracting bundle places the new compose files at $INSTALL_DIR/compose/,
# but the actual files used by `docker compose -f ...` live at $INSTALL_DIR
# root. Without this step, every release that changes the compose file ships
# stale config to existing installs.
#
# Idempotent: silently no-op when the staged compose dir is missing
# (older bundle layouts that pre-date this convention). Caller is responsible
# for snapshotting the prior compose file BEFORE invoking this.
#
# Inputs:
#   $INSTALL_DIR — install root (caller must have set it).
refresh_compose_files() {
    : "${INSTALL_DIR:?refresh_compose_files: INSTALL_DIR must be set by caller}"
    local stage="$INSTALL_DIR/compose"
    [ -f "$stage/docker-compose.yml" ] || return 0
    cp "$stage/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
    if [ -f "$stage/docker-compose.monitoring.yml" ]; then
        cp "$stage/docker-compose.monitoring.yml" \
           "$INSTALL_DIR/docker-compose.monitoring.yml"
    fi
    # The nginx config is promoted HERE, with the compose file that mounts it,
    # not at bundle-extraction time. The two must move as a pair: the conf now
    # carries ${HTTPS_PORT} and is only valid when mounted as a template, which
    # only the matching compose file does. Landing it live at extraction would
    # leave every `die` between extraction and this line (in-flight pipeline,
    # expired cert, low disk, non-linear version, backup failure) with a new
    # conf under an old compose — nginx then fails to start on the NEXT restart,
    # long after the aborted update that caused it.
    promote_nginx_conf "$stage/nginx"
}

# promote_nginx_conf <source-dir>
# Copies nginx.conf + conf.d/*.conf into $INSTALL_DIR/docker/nginx/.
# No-op when the source is absent. Returns 0 always.
#
# ssl/ under the destination is left alone: the certificate is generated per
# install by generate_self_signed_cert, and an operator may have replaced it
# with a real one.
promote_nginx_conf() {
    local src=$1
    : "${INSTALL_DIR:?promote_nginx_conf: INSTALL_DIR must be set by caller}"
    [ -d "$src" ] || return 0
    local dst="$INSTALL_DIR/docker/nginx"
    mkdir -p "$dst/conf.d"
    [ -f "$src/nginx.conf" ] && cp "$src/nginx.conf" "$dst/nginx.conf"
    # `find | while read` rather than a bare glob so an empty conf.d is not an
    # error under `set -u`/`set -e`.
    find "$src/conf.d" -maxdepth 1 -name '*.conf' -type f 2>/dev/null \
        | while IFS= read -r f; do
            cp "$f" "$dst/conf.d/"
        done
    return 0
}

# ---------------------------------------------------------------------------
# Container-runtime bootstrap
# ---------------------------------------------------------------------------
# detect_runtime() only reports. On a clean host it left the installer to die
# with "Container runtime not available." — technically correct and completely
# unhelpful, since installing Docker is what the operator was always going to do
# next. These offer to do it.
#
# Overridable for tests:
#   DOCKER_INSTALL_URL : the convenience-script URL (default https://get.docker.com)

: "${DOCKER_INSTALL_URL:=https://get.docker.com}"

# epel_enable_lines <indent>
# The commands that turn EPEL on for THIS host, one per line, each prefixed with
# <indent>. Emits nothing (and still returns 0) where EPEL is not the answer, so
# a caller can call it unconditionally and let the branch decide.
#
# Split out because two functions print this text — podman_compose_install_help
# and runtime_install_help — and the comment below already records why a second
# copy must not exist. It is keyed on ID and not on family, which is exactly
# what the old single `rhel|rocky|almalinux|ol` branch got wrong:
#   rhel        : `epel-release` is in NO Red Hat repo. It is served only as an
#                 RPM from Fedora infrastructure, so `dnf install epel-release`
#                 fails with "No match for argument" on a fully subscribed host
#                 — the wall a RHEL 10.2 operator hit after the pre-flight
#                 failure this very text was meant to resolve.
#   rocky|almalinux|centos : it IS a distro package, in `extras`. The only case
#                 the old text was right for — and the bats case that covered
#                 it pinned ID=rocky, which is how the defect shipped green.
#   ol          : Oracle names the package oracle-epel-release-el<N> and its CRB
#                 repo ol<N>_codeready_builder; neither of the above resolves.
#   fedora      : podman-compose is in Fedora proper. EPEL does not apply.
# CRB first wherever it applies: EPEL packages routinely depend on it, and
# enabling EPEL without it is the documented route to a broken dep chain.
#
# PREFLIGHT_OS_RELEASE / RUNTIME_OS_RELEASE override the os-release path so the
# branches are testable — CI is Ubuntu, so none of them would ever run.
epel_enable_lines() {
    local indent="${1:-}"
    local osrel="${PREFLIGHT_OS_RELEASE:-${RUNTIME_OS_RELEASE:-/etc/os-release}}"
    local id="" ver=""
    if [ -r "$osrel" ]; then
        # One subshell for both fields (not two), same seam as
        # podman_compose_install_help and _preflight_jq_install_hint: this runs
        # from inside a message and must not leave ID/VERSION_ID behind in the
        # caller. `|` cannot appear in either field on a real os-release, so it
        # is a safe join/split separator.
        local osfields
        # shellcheck disable=SC1090
        osfields=$(. "$osrel" 2>/dev/null && printf '%s|%s' "${ID:-}" "${VERSION_ID:-}") || osfields="|"
        id="${osfields%%|*}"
        ver="${osfields#*|}"
    fi
    # Major only: the EPEL URL and the CRB repo id both take 9 or 10, never 10.2.
    local major="${ver%%.*}"
    # A missing or non-numeric VERSION_ID degrades to the rpm macro rather than
    # emitting `epel-release-latest-.noarch.rpm`, which 404s undiagnosably.
    case "$major" in
        ''|*[!0-9]*) major='$(rpm -E %rhel)' ;;
    esac
    case "$id" in
        rhel)
            printf '%ssudo subscription-manager repos --enable codeready-builder-for-rhel-%s-$(arch)-rpms\n' \
                "$indent" "$major"
            printf '%ssudo dnf install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-%s.noarch.rpm\n' \
                "$indent" "$major"
            ;;
        rocky|almalinux|centos)
            # `crb` is the 9+/10 repo id. EL8 rebuilds call this same repo
            # `powertools`, so this line would fail there with "No matching
            # repo to modify: crb" — deliberately not branched on: check_os
            # (preflight.sh) hard-rejects major < 9, so the pre-flighted
            # install/update path never reaches EL8 here. compose_cmd can
            # still print this text on an EL8 host that never ran pre-flight
            # (assetatlas-cli), but EL8 is out of support regardless, so no
            # `powertools` arm is added for it.
            printf '%ssudo dnf config-manager --set-enabled crb\n' "$indent"
            printf '%ssudo dnf install -y epel-release\n' "$indent"
            ;;
        ol)
            printf '%ssudo dnf config-manager --enable ol%s_codeready_builder\n' "$indent" "$major"
            printf '%ssudo dnf install -y oracle-epel-release-el%s\n' "$indent" "$major"
            ;;
    esac
}

# podman_compose_install_help
# The podman-compose install remedy for THIS host's distro family, as plain
# lines the caller logs. Lives here, not in preflight.sh, because pre-flight
# (check_podman_compose_presence) is not its only printer: compose_cmd emits
# the same text when a podman host reaches a compose call with podman-compose
# missing — assetatlas-cli never runs pre-flight — and two copies would drift.
# This case list is deliberately WIDER than check_os's (preflight.sh:310,
# `rhel|rocky|almalinux|ol` — no centos arm, no fedora arm; both fall through
# to `*` and are rejected as unsupported there). check_os has exactly one
# call site, preflight_install (preflight.sh:884) — preflight_update never
# calls it, so not even an update screens the OS — and compose_cmd reaches
# this same text from assetatlas-cli, which runs no pre-flight at all. So a
# distro check_os would reject can still hit this message and needs a real
# answer here. Do not narrow this list to match check_os's. What "do not
# invent a second detection" still means: epel_enable_lines owns the per-ID
# EPEL knowledge, and no caller in this file should grow its own parallel
# ID/VERSION_ID detector — route through it instead.
#   rhel|rocky|almalinux|centos|ol : podman-compose ships in EPEL, not in
#       BaseOS/AppStream, so the operator's obvious next move —
#       `sudo yum install podman-compose` — fails with "No match for argument".
#       epel_enable_lines owns WHICH commands enable EPEL, because that differs
#       per ID inside this one branch; see its comment. The PyPI line after it
#       is the way out when EPEL itself is unreachable — EPEL 10 is branched per
#       RHEL minor, so a host on a minor EPEL has not branched for gets a 404
#       from the repo the lines above just configured.
#   fedora : podman-compose is in Fedora proper; EPEL is not involved.
#   debian|ubuntu : apt ships it, but the distro builds are 1.0.x — below the
#       1.1.0 floor (Ubuntu 24.04 carries 1.0.6) — so the remedy is PyPI, the
#       same one check_podman_compose_version names for the too-old case.
#   anything else / unknown : no family-specific package route is documented
#       here; generic pip/pipx.
# PREFLIGHT_OS_RELEASE / RUNTIME_OS_RELEASE override the os-release path so the
# branches are testable (CI is Ubuntu) — the same seams _preflight_jq_install_hint
# and runtime_install_help use.
podman_compose_install_help() {
    local osrel="${PREFLIGHT_OS_RELEASE:-${RUNTIME_OS_RELEASE:-/etc/os-release}}"
    local id=""
    if [ -r "$osrel" ]; then
        # Subshell, same as _preflight_jq_install_hint: called from inside a
        # message and must not leave ID/PRETTY_NAME behind in the caller.
        # shellcheck disable=SC1090
        id=$(. "$osrel" 2>/dev/null && printf '%s' "${ID:-}") || id=""
    fi
    case "$id" in
        rhel|rocky|almalinux|centos|ol)
            printf '  Install it — podman-compose ships in EPEL, not in BaseOS/AppStream, so\n'
            printf '  `sudo yum install podman-compose` alone fails with "No match for\n'
            printf '  argument". Enable EPEL first:\n'
            # Every ID in THIS pattern needs a matching arm in
            # epel_enable_lines: the header just printed is unconditional,
            # but whether anything follows it is that helper's decision
            # alone, so an ID added here without an arm there prints
            # "Enable EPEL first:" over nothing. The three case lists are
            # NOT identical and must not be made so — runtime_install_help's
            # below also carries `fedora`, which is right there (
            # epel_enable_lines no-ops on it) and wrong here, where fedora
            # needs its own EPEL-free arm.
            epel_enable_lines '      '
            printf '      sudo dnf install -y podman-compose\n'
            printf '  If EPEL is unreachable — no subscription, an unbranched RHEL minor, or\n'
            printf '  no route to the Fedora mirrors — install it from PyPI instead:\n'
            # pip, not pipx: pipx itself ships in EPEL, exactly what this
            # fallback exists because the host cannot reach. Naming it here
            # would reintroduce the circular dependency this branch routes
            # around.
            printf '      sudo dnf install -y python3-pip && sudo pip3 install -U podman-compose\n'
            printf '  If pip reports "externally-managed-environment", re-run with --break-system-packages.\n'
            ;;
        fedora)
            printf '  Install it — podman-compose is in Fedora proper, no extra repo needed:\n'
            printf '      sudo dnf install -y podman-compose\n'
            ;;
        debian|ubuntu)
            printf '  apt ships podman-compose, but the distro builds are 1.0.x — below the\n'
            printf '  >= %s floor (Ubuntu 24.04 carries 1.0.6). Install it from PyPI:\n' "$MIN_PODMAN_COMPOSE_VERSION"
            printf '      pipx install podman-compose\n'
            printf '  On Ubuntu 24.04 the system Python is externally managed (PEP 668), so the\n'
            printf '  plain-pip form is:\n'
            printf '      pip install --user --break-system-packages -U podman-compose\n'
            ;;
        *)
            printf '  Install it from PyPI:\n'
            printf '      pipx install podman-compose\n'
            printf '  or:\n'
            printf '      pip install --user -U podman-compose\n'
            ;;
    esac
}

# runtime_install_help
# Print how to install a runtime by hand, tailored to the distro. Goes to stdout
# (not log_*) because it is an instruction block the operator will copy, not a
# log line.
runtime_install_help() {
    # Same PREFLIGHT_OS_RELEASE -> RUNTIME_OS_RELEASE -> /etc/os-release order
    # as epel_enable_lines and podman_compose_install_help use. This function
    # calls epel_enable_lines below, which resolves independently; if the two
    # ever disagreed on which file to read, this function would branch on one
    # distro while the helper it calls emitted another's commands, with
    # nothing to catch the mismatch. CI is Ubuntu, so RUNTIME_OS_RELEASE is
    # still what exercises the RHEL path in tests.
    local osrel="${PREFLIGHT_OS_RELEASE:-${RUNTIME_OS_RELEASE:-/etc/os-release}}"
    local id=""
    [ -r "$osrel" ] && id=$(. "$osrel" 2>/dev/null && printf '%s' "${ID:-}")
    printf '\n  AssetAtlas needs Docker >= %s or Podman >= %s.\n\n' \
        "$MIN_DOCKER_VERSION" "$MIN_PODMAN_VERSION"
    case "$id" in
        rhel|centos|rocky|almalinux|ol|fedora)
            # Podman is the supported runtime on RHEL-family hosts. EPEL first
            # where it applies: podman-compose is not in BaseOS/AppStream, so
            # the dnf line below fails without it. epel_enable_lines emits
            # nothing on Fedora, where podman-compose is in the distro proper,
            # and knows that `epel-release` is not installable on genuine RHEL.
            # `ol` was in podman_compose_install_help's list and missing here.
            epel_enable_lines '    '
            printf '    sudo dnf install -y podman podman-compose\n\n'
            printf '  or Docker:\n\n'
            printf '    curl -fsSL %s | sudo sh\n' "$DOCKER_INSTALL_URL"
            ;;
        *)
            printf '    curl -fsSL %s | sudo sh\n' "$DOCKER_INSTALL_URL"
            printf '    sudo systemctl enable --now docker\n'
            ;;
    esac
    printf '\n  Then re-run this installer.\n'
    printf '  Docs: https://docs.docker.com/engine/install/\n\n'
}

# install_docker
# Install Docker via the official convenience script.
install_docker() {
    command -v curl >/dev/null 2>&1 || { log_error "curl is required to install Docker."; return 1; }
    log_info "Installing Docker from $DOCKER_INSTALL_URL ..."
    local tmp rc
    tmp=$(mktemp) || return 1
    # Download, THEN run — never pipe straight to sh. A transfer cut off midway
    # would otherwise execute a partial script as root. Same reasoning as the
    # bootstrap keeping all its logic in main().
    if ! curl -fsSL --max-time 180 "$DOCKER_INSTALL_URL" -o "$tmp"; then
        rm -f "$tmp"
        log_error "Could not download the Docker install script (no network?)."
        return 1
    fi
    sh "$tmp"; rc=$?
    rm -f "$tmp"
    [ "$rc" -eq 0 ] || { log_error "The Docker install script failed (exit $rc)."; return 1; }
    # The very next thing the installer does is talk to the daemon, so make sure
    # it is actually up — the script enables it on most distros, but not all.
    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable --now docker >/dev/null 2>&1 \
            || sudo systemctl enable --now docker >/dev/null 2>&1 \
            || log_warn "Docker installed but the daemon could not be started automatically."
    fi
    return 0
}

# runtime_have_tty
# Whether we can ask the operator anything. Its own function so tests can
# override it: under bats stdin is never a terminal, which would otherwise make
# every interactive branch below unreachable. The real one-liner path does have a
# tty — the bootstrap runs install.sh with `</dev/tty` precisely so prompts
# survive the pipe.
runtime_have_tty() { [ -t 0 ]; }

# ensure_container_runtime
# Exit codes:
#   0  a usable runtime is present (RUNTIME/COMPOSE_CMD exported)
#   1  no runtime, and we could not or must not install one  -> caller should fail
#   2  no runtime, operator declined                         -> caller should exit CLEANLY
#
# The 1/2 split is the point: a declined offer is a decision, not a failure, and
# should not spray FATAL at someone who just answered "n". An unattended run is
# the opposite — nobody is watching, so it must exit non-zero for automation to
# notice rather than silently installing a container runtime host-wide.
ensure_container_runtime() {
    detect_runtime && return 0

    if [ "${INSTALL_DOCKER:-0}" = "1" ]; then
        log_info "--install-docker given; installing without prompting."
    elif [ "${UNATTENDED:-0}" = "1" ]; then
        log_error "No container runtime, and --unattended will not install one without --install-docker."
        runtime_install_help
        return 1
    elif ! runtime_have_tty; then
        # Piped stdin with no terminal: cannot ask, must not assume.
        log_error "No container runtime, and no terminal to ask on."
        runtime_install_help
        return 1
    elif prompt_yn "Install Docker now using the official script ($DOCKER_INSTALL_URL)?" Y; then
        :
    else
        log_info "Skipping Docker installation at your request."
        runtime_install_help
        return 2
    fi

    install_docker || { runtime_install_help; return 1; }

    # Re-detect rather than assume: this also re-runs the version floor check, so
    # a script that installed something too old is caught here instead of at the
    # first compose call.
    detect_runtime || { log_error "Docker was installed but still is not usable."; return 1; }
    return 0
}
