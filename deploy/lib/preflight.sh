#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/preflight.sh — sanity checks before install or update.
# Requires: common.sh sourced before this file; check_podman_compose_presence
# also calls podman_compose_install_help from runtime.sh (install.sh and
# update.sh source runtime.sh first — tests that exercise that check must too).
#
# Cross-platform: works on Linux (install/update target) and darwin (dev machine).
#
# Functions:
#   check_disk <path> <min_gb>
#   check_data_dir_disk <min_gb>
#   check_ram <min_gb>
#   check_port <port>
#   check_os
#   check_cert <pem_file> [<warn_days>]
#   check_in_flight_pipeline [<url>]
#   check_jq
#   check_podman_compose_presence
#   check_podman_compose_version
#   preflight_install
#   preflight_update

: "${INSTALL_DIR:=/opt/assetatlas}"

# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

# _preflight_df <existing_dir>
# One `df -k -P` read of the filesystem holding <existing_dir>, as three
# TAB-separated fields: <free_kb> <device> <mount_point>. Echoes nothing and
# returns non-zero when df says nothing usable.
#
# POSIX df -k -P: one line per filesystem, sizes in KiB, so 1 GiB = 1048576.
#
# The mount point is reassembled from field 6 onwards because df prints one
# containing spaces verbatim, and a truncated mount point would make two distinct
# mounts compare EQUAL in check_data_dir_disk below. NF >= 6 is the other half of
# that: a short line yields an EMPTY mount point, and two empty mount points
# compare equal too — the same false collapse, arrived at from the other side.
# $4 must be all digits for the same reason the emptiness check exists: an
# unparseable line has to fail here rather than reach an arithmetic expansion.
#
# PREFLIGHT_DF is the test seam, in the shape of PREFLIGHT_MEMINFO and
# PREFLIGHT_JQ_BIN — a bats test cannot mount a second filesystem, and
# everything under BATS_TEST_TMPDIR is on one, so the two-filesystem cases have
# no other way to be driven. Operators have no reason to touch it.
_preflight_df() {
    local out
    out=$("${PREFLIGHT_DF:-df}" -k -P "$1" 2>/dev/null | awk '
        NR == 2 && NF >= 6 && $4 ~ /^[0-9]+$/ {
            mnt = $6
            for (i = 7; i <= NF; i++) mnt = mnt " " $i
            printf "%s\t%s\t%s", $4, $1, mnt
        }') || return 1
    [ -n "$out" ] || return 1
    printf '%s' "$out"
}

# _preflight_nearest_existing <path>
# The closest existing ancestor of <path>: the directory whose filesystem <path>
# will land on once something creates it.
#
# Creates NOTHING, and that is the whole point. check_disk's mkdir below doubles
# as INSTALL_DIR's "can I create this" permission check, but borrowing it to
# probe a DATA_DIR the operator has only just typed would make
# /opt/assetatlas/data on every host that runs the bats suite, and would defeat
# the seam test_install_dispatch.bats is built on (#1220).
_preflight_nearest_existing() {
    local p=$1 parent
    # Anchor a relative path the way df would resolve it anyway, so the walk
    # below cannot strip its way to an empty string.
    case "$p" in
        /*) ;;
        *)  p="$PWD/$p" ;;
    esac
    while [ ! -d "$p" ]; do
        parent=${p%/*}
        [ -n "$parent" ] || parent="/"
        # "/" reduces to itself — stop rather than spin.
        [ "$parent" != "$p" ] || break
        p="$parent"
    done
    printf '%s' "$p"
}

# check_disk <path> <min_gb>
# Creates the directory if it doesn't exist. Returns non-zero if free space is below min_gb.
#
# The mkdir is not incidental: for INSTALL_DIR it is also the permission check.
# A probe that must not create anything is check_data_dir_disk's job.
check_disk() {
    local path=$1 min_gb=$2
    if [ ! -d "$path" ]; then
        mkdir -p "$path" || { log_error "Disk: cannot create $path (check permissions)"; return 1; }
    fi
    local probe free_kb free_gb
    probe=$(_preflight_df "$path") || { log_error "Disk: cannot read df for $path"; return 1; }
    # Only the free space matters here; the device and mount point are
    # check_data_dir_disk's business.
    IFS=$'\t' read -r free_kb _ _ <<<"$probe"
    free_gb=$((free_kb / 1024 / 1024))
    if [ "$free_gb" -lt "$min_gb" ]; then
        log_error "Disk: $path has ${free_gb}G free, need ${min_gb}G."
        return 1
    fi
    log_ok "Disk: $path has ${free_gb}G free (>= ${min_gb}G)."
}

# check_data_dir_disk <min_gb>
# Free space on the filesystem $DATA_DIR lands on. Reads DATA_DIR and INSTALL_DIR
# from the environment, the way check_os and check_jq read theirs.
#
# Why this is not just a second check_disk call (#1220). Postgres, chromadb, the
# uploaded logos and every container log live under DATA_DIR, not INSTALL_DIR,
# and the two are routinely different mounts — DATA_DIR has taken its default
# from the environment since #1216, which is exactly the case where it differs.
# So the 20 GB check on INSTALL_DIR passed comfortably while the volume that
# actually fills had 1 GB free, and the operator found out when the database
# wedged.
#
# It creates nothing, which is load-bearing rather than tidy: on the install path
# this runs right after the prompt that collects DATA_DIR and long before
# prepare_data_dirs, so a probe that made the directory would create
# /opt/assetatlas/data on every host that runs the bats suite. The probe walks up
# to DATA_DIR's nearest existing ancestor and reads THAT filesystem, which is the
# one the directory will land on.
#
# No SKIP_ opt-out of its own: <min_gb> is the caller's, and both callers pass
# the same INSTALLER_MIN_DISK_GB / INSTALLER_UPDATE_MIN_DISK_GB they just handed
# check_disk, so the documented threshold override already covers a deliberately
# small environment. One threshold for both is also what makes the
# same-filesystem collapse below sound — a separate, larger DATA_DIR minimum
# would make "already covered" false.
check_data_dir_disk() {
    local min_gb=$1
    local data_dir="${DATA_DIR:-}"
    local install_dir="${INSTALL_DIR:-}"

    if [ -z "$data_dir" ]; then
        # An answer, not check_cert's missing-file skip. compose binds its
        # volumes at ${DATA_DIR:-./data-volumes}, resolved against the project
        # directory — so with no DATA_DIR the persistent data lands under
        # INSTALL_DIR, the filesystem check_disk has just measured. An empty
        # value takes this branch too: it must not degrade into probing "/" and
        # reporting the root filesystem as though it were the data volume.
        log_ok "Disk: DATA_DIR is not set — persistent volumes land under ${install_dir:-the install directory} (compose default ./data-volumes), already covered by the check above."
        return 0
    fi

    # Anchor a relative DATA_DIR to the PROJECT directory ($INSTALL_DIR) — where
    # compose binds it — rather than to the caller's CWD, which is all
    # _preflight_nearest_existing has to fall back on. The two consumers of a
    # relative value genuinely disagree (install.sh:216-224): compose resolves a
    # bind-mount source against $INSTALL_DIR while prepare_data_dirs mkdir -p's it
    # against install.sh's CWD, which is why install.sh rejects one outright since
    # #1216. It can still arrive here from an .env written before that.
    #
    # Not a live miss today: update.sh cd's to $INSTALL_DIR at :122, long before
    # preflight_update, so both anchors already give the same answer on the only
    # path a relative value can reach. This is refusing to let which filesystem
    # gets measured depend on that cd.
    case "$data_dir" in
        /*) ;;
        *)  data_dir="${install_dir:-$PWD}/$data_dir" ;;
    esac

    local probe free_kb dev mnt
    probe=$(_preflight_df "$(_preflight_nearest_existing "$data_dir")") || {
        log_error "Disk: cannot read df for DATA_DIR $data_dir"
        return 1
    }
    IFS=$'\t' read -r free_kb dev mnt <<<"$probe"

    # Collapse the report only on positive evidence that this IS the filesystem
    # check_disk just measured: both df reads naming the same device AND the same
    # mount point. Device alone is not enough — every tmpfs on Linux reports the
    # device `tmpfs`, so two genuinely different mounts would compare equal and
    # this check would report "already covered" for a filesystem it had never
    # measured, which is the shape #1220 is an instance of. An INSTALL_DIR probe
    # that cannot be read falls through to the full comparison below instead of
    # collapsing.
    if [ -n "$install_dir" ]; then
        local ref_probe ref_dev ref_mnt
        if ref_probe=$(_preflight_df "$(_preflight_nearest_existing "$install_dir")"); then
            # Identity, not size: check_disk already reported INSTALL_DIR's free
            # space, so only the device and mount point are read back here.
            IFS=$'\t' read -r _ ref_dev ref_mnt <<<"$ref_probe"
            if [ "$dev" = "$ref_dev" ] && [ "$mnt" = "$ref_mnt" ]; then
                log_ok "Disk: DATA_DIR $data_dir is on the same filesystem as $install_dir ($mnt) — covered by the check above."
                return 0
            fi
        fi
    fi

    local free_gb=$((free_kb / 1024 / 1024))
    if [ "$free_gb" -lt "$min_gb" ]; then
        log_error "Disk: DATA_DIR $data_dir lands on $mnt, which has ${free_gb}G free, need ${min_gb}G. Postgres, chromadb and the container logs all live there, not under the install directory."
        return 1
    fi
    log_ok "Disk: DATA_DIR $data_dir lands on $mnt — ${free_gb}G free (>= ${min_gb}G)."
}

# check_ram <min_gb>
# Returns non-zero if total RAM is below min_gb.
#
# Compares in MiB with 5% of headroom rather than flooring GiB. A machine
# provisioned with exactly min_gb never reports min_gb back: the kernel reserves
# a few percent for itself, so a VM given 8 GB reports MemTotal 8131476 kB =
# 7.76 GiB. Flooring that to 7 and comparing `7 -lt 8` rejected a host built to
# the documented minimum. 5% still rejects a genuinely undersized box — 7.0 GiB
# fails an 8 GB requirement.
#
# PREFLIGHT_MEMINFO overrides the meminfo path so this is testable.
check_ram() {
    local min_gb=$1
    local total_kb total_mb min_mb shown
    local meminfo="${PREFLIGHT_MEMINFO:-/proc/meminfo}"
    if [ -r "$meminfo" ]; then
        # Linux
        total_kb=$(awk '/^MemTotal:/ {print $2}' "$meminfo")
    else
        # darwin: hw.memsize is in bytes
        local total_bytes
        total_bytes=$(sysctl -n hw.memsize 2>/dev/null) || {
            log_error "RAM: cannot read memory info"
            return 1
        }
        total_kb=$((total_bytes / 1024))
    fi
    [ -n "$total_kb" ] || { log_error "RAM: cannot determine total memory"; return 1; }
    total_mb=$((total_kb / 1024))
    min_mb=$(( min_gb * 1024 * 95 / 100 ))
    shown="$((total_mb / 1024)).$(( (total_mb % 1024) * 10 / 1024 ))"
    if [ "$total_mb" -lt "$min_mb" ]; then
        log_error "RAM: ${shown}G total, need ${min_gb}G."
        return 1
    fi
    # Not ">= ${min_gb}G": a host built to the documented minimum reports ~7.7G
    # against an 8G requirement (see the headroom note above), so that phrasing
    # printed a passing check that read as a contradiction.
    log_ok "RAM: ${shown}G total (meets ${min_gb}G minimum)."
}

# check_port <port>
# Returns 0 if the port is free, non-zero if already bound.
check_port() {
    local port=$1
    local busy=0 listening=''
    if command -v ss >/dev/null 2>&1; then
        # Linux (ss from iproute2)
        # Captured first, then matched. Written as `ss | awk | grep -qE` this put
        # the early-exiting reader downstream of awk: grep -q exits on its match
        # and closes the pipe, awk takes EPIPE, and pipefail hands the `if` awk's
        # non-zero status — so a BOUND port was reported free, the same shape as
        # check_cert's missing-file skip (#1199). ss output on a busy host is not
        # small, which is what made this reachable.
        #
        # `|| listening=''` keeps the old behaviour for a failing ss — the
        # pipeline's non-zero status simply took the false branch, i.e. "free" —
        # and stops `set -e` in install.sh/update.sh from turning it into an abort
        # now that the assignment is no longer inside an `if` condition.
        listening=$(ss -ltn 2>/dev/null | awk '{print $4}') || listening=''
        if grep -qE "[:.]${port}\$" <<<"$listening"; then
            busy=1
        fi
    elif command -v lsof >/dev/null 2>&1; then
        # macOS and most Linux distributions
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
            busy=1
        fi
    elif command -v python3 >/dev/null 2>&1; then
        # Last resort: try to bind the port
        if ! python3 -c "
import socket, sys
s = socket.socket()
try:
    s.bind(('127.0.0.1', $port))
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
" >/dev/null 2>&1; then
            busy=1
        fi
    else
        # Fail-closed: missing detection tooling is a permanent environment issue,
        # not a transient one — better to surface it than silently pass.
        log_error "check_port: no ss/lsof/python3 available; cannot verify port $port"
        return 1
    fi
    if [ "$busy" = "1" ]; then
        log_error "Port $port is already in use."
        return 1
    fi
    log_ok "Port $port is free."
}

# check_os
# Verifies the OS is a supported distribution and version.
# Always returns 0 on darwin (dev machine) with a warning.
check_os() {
    if [ ! -r /etc/os-release ]; then
        log_warn "OS: cannot determine (no /etc/os-release; likely darwin/dev machine)"
        return 0
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
        rhel|rocky|almalinux|ol)
            local major="${VERSION_ID%%.*}"
            if [ "$major" -lt 9 ]; then
                log_error "${PRETTY_NAME:-$ID} unsupported (need >= 9)"
                return 1
            fi
            log_ok "OS: ${PRETTY_NAME:-$ID} (Tier 1/2)"
            ;;
        ubuntu)
            case "${VERSION_ID:-}" in
                22.04|24.04) log_ok "OS: ${PRETTY_NAME:-$ID} (Tier 1)" ;;
                *)           log_warn "OS: ${PRETTY_NAME:-$ID} (untested)" ;;
            esac
            ;;
        debian)
            local major="${VERSION_ID%%.*}"
            if [ "$major" -lt 12 ]; then
                log_error "Debian < 12 unsupported"
                return 1
            fi
            log_ok "OS: ${PRETTY_NAME:-$ID} (Tier 2)"
            ;;
        sles)
            log_warn "OS: ${PRETTY_NAME:-$ID} (Tier 3, best-effort)"
            ;;
        *)
            if [ "${SKIP_OS_CHECK:-0}" = "1" ]; then
                log_warn "OS: $PRETTY_NAME — not on supported list, but SKIP_OS_CHECK=1 set; proceeding."
                return 0
            fi
            log_error "OS: $PRETTY_NAME is not on the supported list (RHEL/Rocky/Alma 9+, Ubuntu 22.04/24.04, Debian 12+, SLES). Set SKIP_OS_CHECK=1 to override."
            return 1
            ;;
    esac
}

# check_cert <pem_file> [<warn_days=30>]
# Uses openssl x509 -checkend (portable; no date arithmetic).
# Returns non-zero only if the cert is already EXPIRED.
# Warns (but returns 0) if the cert expires within warn_days.
# No-op (returns 0) when the file does not exist.
check_cert() {
    local pem=${1:-}
    local warn_days=${2:-30}
    if [ ! -f "$pem" ]; then
        log_warn "Cert $pem not found — skipping."
        return 0
    fi
    # openssl exits 0 if cert is valid for at least N more seconds, 1 otherwise.
    if ! openssl x509 -checkend 0 -noout -in "$pem" >/dev/null 2>&1; then
        # Name the remedy: no installer flag bypasses this, and on an existing
        # install re-running install.sh will not regenerate the pair either — it
        # dispatches straight to update.sh, which is the command that just failed.
        # Mirrors generate_self_signed_cert (env.sh): same 825 days, and the same
        # subjectAltName — a CN-only cert is rejected outright by every current
        # client, so a remedy without -addext hands back a worse cert than the
        # installer issued. nginx bind-mounts the file, so it needs a restart.
        log_error "Cert $pem EXPIRED. Replace it, or regenerate the self-signed pair in place:"
        log_error "  openssl req -x509 -nodes -newkey rsa:2048 -days 825 -keyout $(dirname "$pem")/server.key -out $pem \\"
        log_error "    -subj \"/CN=\$DOMAIN\" -addext \"subjectAltName=DNS:\$DOMAIN,DNS:localhost,IP:127.0.0.1\""
        log_error "  chmod 600 $(dirname "$pem")/server.key && assetatlas-cli restart"
        return 1
    fi
    local warn_seconds=$((warn_days * 86400))
    if ! openssl x509 -checkend "$warn_seconds" -noout -in "$pem" >/dev/null 2>&1; then
        local enddate
        enddate=$(openssl x509 -enddate -noout -in "$pem" 2>/dev/null | cut -d= -f2)
        log_warn "Cert $pem expires in less than $warn_days days (${enddate:-unknown expiry})."
    else
        log_ok "Cert $pem valid for at least $warn_days days."
    fi
}

# check_in_flight_pipeline [<url>]
# Used by update.sh to avoid killing an in-flight pipeline run.
#
# Fail-open by design: returns 0 (safe-to-proceed) on unreachable URL or 5xx, because
# transient network/API errors during a routine update should not block the operator.
# Returns non-zero ONLY when the API responds successfully AND reports in_flight=true.
#
# Assumes the API returns a flat top-level object: {"in_flight": true|false}.
# A nested {"foo": {"in_flight": true}} would also match — acceptable because the
# /api/health/in-flight endpoint is contractually flat.
#
# Default URL works for production installs (HTTPS via nginx). Callers can override
# (e.g., dev mode: http://localhost:5001/api/health/in-flight).
check_in_flight_pipeline() {
    local url=${1:-https://localhost:${HTTPS_PORT:-8443}/api/health/in-flight}
    local response
    if ! response=$(curl -sfk --max-time 5 "$url" 2>/dev/null); then
        log_warn "Could not reach $url; skipping in-flight check."
        return 0
    fi
    local lower
    lower=$(printf '%s' "$response" | tr '[:upper:]' '[:lower:]')
    if [[ "$lower" =~ \"in_flight\"[[:space:]]*:[[:space:]]*true ]]; then
        log_error "A pipeline run is currently in flight. Wait for it to finish or use --force."
        return 1
    fi
    log_ok "No in-flight pipeline runs."
}

# ---------------------------------------------------------------------------
# jq — the one HARD requirement in this file, and only where it is really one
# ---------------------------------------------------------------------------

# The docker compose release that changed `ps --format json` from a single JSON
# array to one object per line: 2.21, 10/2023. That is the boundary the health
# check's no-jq fallback rests on — at or above it the fallback is adequate,
# below it every poll is refused. Deliberately not an env override: it is a fact
# about compose, not a threshold an operator gets to move.
PREFLIGHT_MIN_NDJSON_COMPOSE="2.21"

# _preflight_jq_install_hint
# The command that installs jq on THIS host, so the failure below ends in
# something the operator can paste rather than a bare "install jq". RHEL 9 — the
# primary production topology — carries it in AppStream.
#
# PREFLIGHT_OS_RELEASE overrides the path so the distro branches are testable:
# CI runs on Ubuntu, so the RHEL branch would otherwise never be exercised (same
# seam as runtime.sh's RUNTIME_OS_RELEASE).
_preflight_jq_install_hint() {
    local osrel="${PREFLIGHT_OS_RELEASE:-/etc/os-release}"
    local id=""
    if [ -r "$osrel" ]; then
        # Subshell, unlike check_os's bare `. /etc/os-release`: this one is
        # called from inside a message and must not leave ID/PRETTY_NAME behind
        # in the caller's shell.
        # shellcheck disable=SC1090
        id=$(. "$osrel" 2>/dev/null && printf '%s' "${ID:-}") || id=""
    fi
    case "$id" in
        rhel|centos|rocky|almalinux|ol|fedora) printf 'sudo dnf install -y jq' ;;
        ubuntu|debian)                         printf 'sudo apt-get install -y jq' ;;
        sles|sled|opensuse*)                   printf 'sudo zypper install -y jq' ;;
        *) printf "install jq with this host's package manager (https://jqlang.github.io/jq/download/)" ;;
    esac
}

# _preflight_ps_excerpt <ps_output>
# The first few lines of what compose actually emitted, clipped. Enough to
# identify the shape, small enough not to bury the remedy under a twelve-service
# listing.
#
# This is also the shape CAPTURE #1233 still wants: nothing in the repo has real
# `podman-compose ps --format json` output, and the first operator to hit this
# check on that topology gets it printed in their install log and their audit log.
_preflight_ps_excerpt() {
    local line out="" n=0
    while IFS= read -r line; do
        n=$((n + 1))
        if [ "$n" -gt 3 ]; then
            out="$out"$'\n'"      ... (truncated)"
            break
        fi
        out="$out"$'\n'"      ${line:0:160}"
    done <<<"$1"
    printf '%s' "$out"
}

# _preflight_compose_emits_ndjson
# Whether this host's compose is KNOWN to emit the one shape the no-jq fallback
# reads — the question for the case where there was nothing to probe.
# preflight_install runs before the stack exists and before COMPOSE_FILE is even
# set (install.sh:162 vs :336), so on a fresh install this is the only evidence
# there is.
#
# Echoes its EVIDENCE on success and its REASON on failure, so check_jq can say
# what it concluded and on what, either way.
#
# Only `docker compose` can be cleared this way. 2.21 is a dated, documented
# change; podman-compose has no equivalent boundary and its `ps --format json`
# shape has never been captured (#1233) — podman's own `ps` JSON is a
# pretty-printed array with no "Service" key at all. So on that topology only a
# probe that actually SAW readable output passes, which is the deliberate answer
# to the standing guidance in .claude/rules/deploy.md: assume the podman
# production host needs jq before a release reaches it.
_preflight_compose_emits_ndjson() {
    if [ -z "${COMPOSE_CMD:-}" ]; then
        printf 'no container runtime has been detected, so nothing is known about its output shape'
        return 1
    fi
    if [ "$COMPOSE_CMD" != "docker compose" ]; then
        printf "%s has no released version known to emit it, and its 'ps --format json' shape has never been captured (#1233)" "$COMPOSE_CMD"
        return 1
    fi
    local raw="" ver=""
    raw=$($COMPOSE_CMD version --short 2>/dev/null) || raw=""
    if [ -z "$raw" ] || ! [[ "$raw" =~ ([0-9]+\.[0-9]+) ]]; then
        printf "'%s version --short' did not report a version" "$COMPOSE_CMD"
        return 1
    fi
    ver="${BASH_REMATCH[1]}"
    local want_major="${PREFLIGHT_MIN_NDJSON_COMPOSE%%.*}"
    local want_minor="${PREFLIGHT_MIN_NDJSON_COMPOSE##*.}"
    local have_major="${ver%%.*}"
    local have_minor="${ver##*.}"
    # Plain integer compare rather than runtime_version_check's
    # `printf | sort -V | head -1`: same answer, without adding another
    # early-exiting reader to a pipeline in this tree (.claude/rules/deploy.md
    # names that shape as the unswept half of the #1199 family). The regex above
    # guarantees both halves are digits, so -lt is safe.
    if [ "$have_major" -lt "$want_major" ] ||
       { [ "$have_major" -eq "$want_major" ] && [ "$have_minor" -lt "$want_minor" ]; }; then
        printf 'docker compose %s emits a single JSON array; one object per line arrived in %s' \
            "$ver" "$PREFLIGHT_MIN_NDJSON_COMPOSE"
        return 1
    fi
    printf 'docker compose %s emits one JSON object per line (>= %s)' \
        "$ver" "$PREFLIGHT_MIN_NDJSON_COMPOSE"
}

# check_jq
# A HARD requirement — and a deliberate departure from this file's house style,
# which is graceful degradation: check_port falls back across ss/lsof/python3
# precisely so that no single tool is mandatory. So this check is explicit and
# named, states what it concluded and on what evidence, and never passes by
# assumption.
#
# Why it exists (#1233). Container state is read through ONE interpreter,
# _health_ps_parse. With jq every `compose ps --format json` shape parses; WITHOUT
# jq only compact one-object-per-line output is admitted and everything else is
# REFUSED — fail-closed on purpose, because the shape-blind predecessor scored
# zero bad states against a pretty-printing emitter and passed the health window
# silently. The cost of that trade lands on a jq-less host with a non-NDJSON
# emitter: a perfectly HEALTHY release fails its health window, which on an update
# fires the destructive auto-rollback, whose own container check then fails on the
# identical shape -> ROLLBACK INCOMPLETE. Today that lands AFTER the backup and the
# migrations, i.e. past the destructive point. This check moves it to before
# anything has been touched, with a remedy the operator can act on.
#
# Scoped to what the runtime actually needs, because a blanket requirement would
# newly block installs that work fine today — with docker compose >= 2.21 the
# no-jq fallback is entirely adequate:
#
#   1. jq present  -> pass; nothing else matters, every shape parses.
#   2. jq absent, and there IS live `ps --format json` output: ask the real
#      interpreter, with jq taken away, whether it can read it. That is the exact
#      question the health window will later ask itself, so there is no second
#      copy of "which shapes are readable" here to drift out of step (#1188).
#      A refusal here is final — output that demonstrably cannot be read outranks
#      any version that is supposed to emit something else.
#   3. jq absent and nothing to probe -> what compose is KNOWN to emit
#      (_preflight_compose_emits_ndjson).
#
# Anything this cannot answer FAILS. "Could not determine" must never be a pass:
# that is check_cert's missing-file skip, a safety check reporting success without
# having run.
#
# SKIP_JQ_CHECK=1 overrides, in the shape of SKIP_OS_CHECK — warn, name the
# variable, proceed. It suppresses the VERDICT, not the check: the diagnosis is
# still printed, so a check that was overridden is still visible in the pre-flight
# output.
#
# PREFLIGHT_JQ_BIN is the test seam (point it at a non-command to drive the no-jq
# path); operators have no reason to touch it. HEALTH_JQ_BIN is _health_ps_parse's
# equivalent seam, and it is honoured as the fallback for the same reason the probe
# threads the answer back out: the question is which binary the HEALTH CHECK will
# consult, so a host that points it at a jq outside PATH must not be failed here for
# a jq it has.
check_jq() {
    local jq_want="${PREFLIGHT_JQ_BIN:-${HEALTH_JQ_BIN:-jq}}"
    local jq_bin=""
    jq_bin=$(command -v "$jq_want" 2>/dev/null) || jq_bin=""
    if [ -n "$jq_bin" ]; then
        log_ok "jq: $jq_bin — the container health check can read every known 'compose ps --format json' shape."
        return 0
    fi

    local ps_output="" diagnosis="" excerpt=""
    if [ -n "${COMPOSE_CMD:-}" ] && [ -n "${COMPOSE_FILE:-}" ] && [ -f "$COMPOSE_FILE" ]; then
        # The same call the health window makes, so the shape seen here is the
        # shape it will be handed. Skipped entirely when there is no compose file
        # to point at — probing whatever project happens to be in the current
        # directory would be evidence about someone else's stack.
        # shellcheck disable=SC2086
        ps_output=$($COMPOSE_CMD -f "$COMPOSE_FILE" ps --format json 2>/dev/null) || ps_output=""
    fi
    if [ -n "$ps_output" ] && declare -F _health_ps_parse >/dev/null 2>&1; then
        # HEALTH_JQ_BIN is threaded through so the probe answers for the same
        # binary this function just failed to find. Without it, a PREFLIGHT_JQ_BIN
        # pointed at nothing would still get its answer from a jq that is on PATH,
        # and the check would clear a host it had never actually tested.
        if HEALTH_JQ_BIN="$jq_want" _health_ps_parse "$ps_output" >/dev/null 2>&1; then
            log_ok "jq: not installed, and not needed here — $COMPOSE_CMD emits one JSON object per line, which the container health check reads without it."
            return 0
        fi
        # The line count is doing real diagnostic work next to a 3-line excerpt:
        # it is what separates the two shapes that land here. A single-line JSON
        # array (compose 2.17-2.20) reports 1; podman-compose's pretty-printed
        # array reports hundreds. The excerpt alone shows neither.
        local ps_lines=0
        ps_lines=$(grep -c . <<<"$ps_output") || ps_lines=0
        ps_lines=${ps_lines// /}
        diagnosis="not installed, and '$COMPOSE_CMD -f $COMPOSE_FILE ps --format json' on this host emits a shape the container health check cannot read without it ($ps_lines line(s)):"
        excerpt=$(_preflight_ps_excerpt "$ps_output")
    else
        local reason=""
        if reason=$(_preflight_compose_emits_ndjson); then
            log_ok "jq: not installed, and not needed here — $reason, which the container health check reads without it."
            return 0
        fi
        diagnosis="not installed, and this host was not shown to emit the one 'compose ps --format json' shape the container health check can read without it (compact, one object per line): ${reason}."
    fi

    if [ "${SKIP_JQ_CHECK:-0}" = "1" ]; then
        log_warn "jq: ${diagnosis}${excerpt}"
        log_warn "jq: SKIP_JQ_CHECK=1 set; proceeding. The container health window will refuse that output and fail even a healthy release — on an update that means the destructive auto-rollback, whose own check then fails on the same shape."
        return 0
    fi
    log_error "jq: ${diagnosis}${excerpt}"
    log_error "  Without jq the health check accepts ONLY compact one-object-per-line JSON and refuses every other shape, so a healthy release fails its health window. On an update that fires the destructive auto-rollback, whose own container check then fails identically -> ROLLBACK INCOMPLETE."
    log_error "  Install it:  $(_preflight_jq_install_hint)"
    log_error "  Or set SKIP_JQ_CHECK=1 to proceed anyway, accepting that the health window becomes the thing that fails."
    return 1
}

# ---------------------------------------------------------------------------
# podman-compose — the release that first honours `profiles:`
# ---------------------------------------------------------------------------

# podman-compose gained `profiles:` support in 1.1.0 and not before. The
# evidence, because "somewhere between the two versions we measured" is not a
# threshold anyone can defend later:
#   - PR containers/podman-compose#592 ("Adding basic support for --profile
#     argument", closing issue #430) merged 2023-04-10. v1.0.6 was tagged
#     2023-04-09 — ONE DAY earlier, and it is the last 1.0.x, so 1.1.0 is the
#     first release that can contain it.
#   - docs/Changelog-1.1.0.md, under New features: "Added support for `profile`
#     argument."
#   - The source at v1.0.6 contains no occurrence of "profile" at all; v1.1.0
#     has _resolve_profiles(), applied both to `config` output and to the live
#     service dict, so it filters every subcommand.
# Measured on this project's own compose files: 1.0.6 lists all 16 services,
# 1.6.0 lists 14 (pgadmin, metrics-forwarder and cadvisor excluded).
#
# 1.1.0 and not 1.6.0: 1.6.0 is where the COMPOSE_PROFILES *environment
# variable* arrived, and this installer does not use it — compose_overlay_args
# passes the `--profile` CLI flag, which is exactly what 1.1.0 added.
#
# Deliberately not an env override: it is a fact about podman-compose, not a
# threshold an operator gets to move (same reasoning as
# PREFLIGHT_MIN_NDJSON_COMPOSE above).
#
# The value itself is defined once, in runtime.sh (MIN_PODMAN_COMPOSE_VERSION),
# because compose_cmd's missing-binary guard names the same floor where
# preflight.sh is never sourced (assetatlas-cli). The `:-` fallback keeps this
# file loadable on its own — the same style as ${MIN_DOCKER_VERSION:-24.0} in
# check_podman_compose_version below.
PREFLIGHT_MIN_PODMAN_COMPOSE="${MIN_PODMAN_COMPOSE_VERSION:-1.1.0}"

# _preflight_podman_compose_version <binary>
# The version <binary> reports, as "MAJOR.MINOR[.PATCH]". Echoes nothing and
# returns non-zero when it cannot be read — "could not determine" is never a
# pass here, for the same reason it is not in check_jq.
#
# `version --short` first, because it prints the number and nothing else. The
# fallback parses `--version`, whose output also carries a `podman version
# 5.6.0` line — so the match is ANCHORED on the words "podman-compose version".
# A bare version-number match would read podman's version as podman-compose's
# and clear a 1.0.6 host on the strength of its podman 4.9.
_preflight_podman_compose_version() {
    local bin=$1 raw=""
    raw=$("$bin" version --short 2>/dev/null) || raw=""
    if [[ "$raw" =~ ^[[:space:]]*([0-9]+\.[0-9]+(\.[0-9]+)?)[[:space:]]*$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        return 0
    fi
    # 2>&1: some releases print the banner on stderr.
    raw=$("$bin" --version 2>&1) || true
    # grep -m1 on a HERESTRING, never `printf … | grep`: an early-exiting reader
    # downstream of a writer is the banned EPIPE shape (#1199).
    local line=""
    line=$(grep -m1 -oE 'podman-compose version[[:space:]]+[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"$raw") || line=""
    if [[ "$line" =~ ([0-9]+\.[0-9]+(\.[0-9]+)?) ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

# _preflight_version_lt <a> <b>
# 0 when version <a> is strictly older than <b>. Field-by-field integer compare
# over three fields, not `sort -V | head -1`: that shape puts an early-exiting
# reader downstream of a writer, which is the family .claude/rules/deploy.md
# names as the unswept half of #1199. Missing fields read as 0, so "1.1" and
# "1.1.0" compare equal.
_preflight_version_lt() {
    local a=$1 b=$2 i av bv
    local -a af bf
    IFS=. read -r -a af <<<"$a"
    IFS=. read -r -a bf <<<"$b"
    for i in 0 1 2; do
        av=${af[$i]:-0}; bv=${bf[$i]:-0}
        # A non-numeric field (a git-describe suffix like "1.2.0-dev") must not
        # reach an arithmetic comparison; strip to leading digits, empty -> 0.
        av=${av%%[!0-9]*}; bv=${bv%%[!0-9]*}
        av=${av:-0}; bv=${bv:-0}
        if [ "$av" -lt "$bv" ]; then return 0; fi
        if [ "$av" -gt "$bv" ]; then return 1; fi
    done
    return 1
}

# The per-distro install remedy itself moved to runtime.sh
# (podman_compose_install_help): pre-flight is not its only printer —
# compose_cmd emits the same text when a podman host reaches a compose call
# with podman-compose missing (assetatlas-cli never runs pre-flight), and two
# copies would drift. PREFLIGHT_OS_RELEASE still selects the branch there, and
# the per-family rationale moved with the function.

# check_podman_compose_presence
# The PRESENCE half of the podman-compose gate, split from the VERSION half so
# preflight_update can take it without taking the floor. The asymmetry is
# deliberate: an update on an OLD podman-compose is no worse off than the
# install it already has (check_podman_compose_version stays install-only),
# but an update with NO podman-compose cannot complete at all — it would fail
# at the first compose call, past the database backup and the migrations.
# Refusing here keeps that report ahead of anything being touched, with a
# remedy attached. It is also what makes _detect_podman not dying on the spot
# safe (runtime.sh): detection answers WHICH interface drives the host, and
# this check is where "the interface is not installed" becomes a verdict —
# before anything is configured, mutated or pulled, rather than as a bare
# FATAL after the bundle has been downloaded, verified and extracted.
#
# Scoped exactly like the version check: podman-compose only. A Docker host
# needs nothing here — Docker Compose comes with the engine — so it passes
# without a line of output, and a podman host WITH podman-compose passes the
# same way (presence is not the version gate).
#
# SKIP_PODMAN_COMPOSE_CHECK=1 overrides, in the shape of SKIP_OS_CHECK /
# SKIP_JQ_CHECK: warn, name what breaks, proceed.
check_podman_compose_presence() {
    # Same guard as the version check: an empty COMPOSE_CMD means a call site
    # skipped runtime detection — say so rather than silently passing.
    if [ -z "${COMPOSE_CMD:-}" ]; then
        log_warn "podman-compose: no container runtime has been detected yet, so its presence could not be checked. If this host uses podman-compose, verify it is installed and >= $PREFLIGHT_MIN_PODMAN_COMPOSE."
        return 0
    fi
    [ "$COMPOSE_CMD" = "podman-compose" ] || return 0
    local bin="${PREFLIGHT_PODMAN_COMPOSE:-podman-compose}"
    command -v "$bin" >/dev/null 2>&1 && return 0

    if [ "${SKIP_PODMAN_COMPOSE_CHECK:-0}" = "1" ]; then
        log_warn "podman-compose: '$bin' is not on PATH."
        log_warn "podman-compose: SKIP_PODMAN_COMPOSE_CHECK=1 set; proceeding. Without it no compose call can run — the install or update fails at the first one, and on an update that is past the database backup and the migrations."
        return 0
    fi
    log_error "podman-compose: '$bin' is not on PATH. podman is this host's container runtime, and the compose files are driven through podman-compose — AssetAtlas needs podman-compose >= $PREFLIGHT_MIN_PODMAN_COMPOSE (the first release that honours 'profiles:')."
    local line
    while IFS= read -r line; do log_error "$line"; done <<<"$(podman_compose_install_help)"
    # Named explicitly because the remedy just printed can APPEAR to have worked
    # and still land here: a pip install puts the console script in a directory
    # sudo's secure_path omits, so pip says "Requirement already satisfied" while
    # this check keeps failing. detect_runtime auto-recovers the /usr/local/bin
    # case; ~/.local/bin (a `pip install --user`) it cannot, because the invoking
    # user's home is not root's (#1325).
    log_error "  If the install reports 'Requirement already satisfied', the binary exists but sudo cannot see it: sudo replaces PATH with secure_path (RHEL default /sbin:/bin:/usr/sbin:/usr/bin). Check with 'command -v podman-compose' as your user, then symlink it into /usr/bin:"
    log_error "      sudo ln -s \"\$(command -v podman-compose)\" /usr/bin/podman-compose"
    log_error "  Then re-run this command. Or set SKIP_PODMAN_COMPOSE_CHECK=1 to proceed anyway, accepting that every compose call fails."
    return 1
}

# check_podman_compose_version
# podman-compose ONLY. Docker Compose has honoured `profiles:` since 1.28
# (01/2021), well below the 24.0 floor runtime.sh already enforces, so gating it
# would only newly fail installs that work — the same reason check_jq clears a
# docker compose >= 2.21 host instead of demanding jq from everyone.
#
# Why this FAILS rather than warns. On podman-compose 1.0.6 — which is what
# Ubuntu 24.04's apt ships, and 24.04 is a Tier 1 OS here — the `profiles:` keys
# in the compose files are read and silently ignored. Every profiled service is
# then part of the project unconditionally, so `up -d` starts:
#   - cadvisor, which exists behind `profiles: [privileged]` precisely so a
#     PODMAN host does not try to run it (compose_overlay_args only activates
#     the profile under docker). It needs `privileged: true`, /dev/kmsg and
#     /var/lib/docker, and is pinned to the Docker API — under podman it
#     produces no usefully-labelled series and just fails to start, then sits
#     in the container list the health window reads.
#   - pgadmin, a database admin UI on a published port, which nobody asked for.
#   - metrics-forwarder, a Prometheus agent that remote_writes wherever its
#     config points.
# A warning cannot prevent any of that, and the failure it leads to arrives
# later and describes something else: a health window timing out on a container
# the operator never chose to run, or on an update, the destructive
# auto-rollback. The remedy is one command and takes seconds, so refusing here —
# before anything on the host has been touched — is strictly the cheaper end.
# It is the same trade check_jq makes, and it fails for the same reason.
#
# preflight_install only, deliberately. An UPDATE on such a host is no worse off
# than the install it already has — those containers are already running, and
# refreshing the compose files does not change that — so failing there would
# strand a working install over a condition the update did not create.
#
# SKIP_PODMAN_COMPOSE_CHECK=1 overrides, in the shape of SKIP_OS_CHECK /
# SKIP_JQ_CHECK: the opt-out lives in the check so preflight_install never
# needs a second copy of its list with one entry dropped (#1188).
#
# PREFLIGHT_PODMAN_COMPOSE is the test seam (the binary to interrogate), in the
# shape of PREFLIGHT_JQ_BIN. Operators have no reason to touch it.
check_podman_compose_version() {
    # $COMPOSE_CMD is exported by detect_runtime, which install.sh runs at :151,
    # well before preflight_install at :211. An EMPTY value therefore means a new
    # call site skipped runtime detection — say so instead of silently passing,
    # because "no runtime detected" is not evidence that profiles work.
    if [ -z "${COMPOSE_CMD:-}" ]; then
        log_warn "podman-compose: no container runtime has been detected yet, so its version could not be checked. If this host uses podman-compose, verify it is >= $PREFLIGHT_MIN_PODMAN_COMPOSE."
        return 0
    fi
    if [ "$COMPOSE_CMD" != "podman-compose" ]; then
        log_ok "Compose profiles: $COMPOSE_CMD honours them (Docker Compose has since 1.28, below the ${MIN_DOCKER_VERSION:-24.0} floor already required)."
        return 0
    fi

    local bin="${PREFLIGHT_PODMAN_COMPOSE:-podman-compose}"
    local ver="" diagnosis=""
    if ! command -v "$bin" >/dev/null 2>&1; then
        # Absent, not merely old: no compose call can run at all, and the
        # remedy is a per-distro-family install command, not the PyPI text the
        # too-old case carries. The presence check owns this report (same SKIP
        # opt-out), so the install and update paths word it identically — one
        # copy, not two that drift.
        check_podman_compose_presence
        return $?
    fi
    if ! ver=$(_preflight_podman_compose_version "$bin"); then
        diagnosis="'$bin --version' did not report a version this check can read"
    elif _preflight_version_lt "$ver" "$PREFLIGHT_MIN_PODMAN_COMPOSE"; then
        diagnosis="version $ver ignores the 'profiles:' keys in the compose files; support for them arrived in $PREFLIGHT_MIN_PODMAN_COMPOSE"
    else
        log_ok "podman-compose $ver honours 'profiles:' (>= $PREFLIGHT_MIN_PODMAN_COMPOSE)."
        return 0
    fi

    if [ "${SKIP_PODMAN_COMPOSE_CHECK:-0}" = "1" ]; then
        log_warn "podman-compose: ${diagnosis}."
        log_warn "podman-compose: SKIP_PODMAN_COMPOSE_CHECK=1 set; proceeding. Expect pgadmin and metrics-forwarder to start unasked, and cadvisor to be attempted and fail — it needs privileged: true and /dev/kmsg and is Docker-specific."
        return 0
    fi
    log_error "podman-compose: ${diagnosis}."
    log_error "  Without profile support every optional service starts unconditionally: pgadmin (a database admin UI on a published port), metrics-forwarder, and — with the monitoring overlay — cadvisor, which needs privileged: true and /dev/kmsg and is Docker-specific."
    # apt genuinely cannot supply it: noble froze on 1.0.6 (current from
    # 2023-04-09 until 1.1.0 shipped on 2024-06-25), so naming apt here would
    # send the operator in a circle.
    log_error "  Ubuntu's apt cannot supply a newer one — 24.04 ships $bin 1.0.6. Install it from PyPI:"
    log_error "      pip install -U podman-compose"
    # PEP 668: Ubuntu 23.04+ marks the system Python externally-managed, so a
    # plain `pip install` there aborts with "externally-managed-environment"
    # rather than doing anything. An operator who has just been told to run a
    # command that refuses is no better off than one told nothing.
    log_error "    On Ubuntu 24.04 the system Python is externally managed (PEP 668), so use one of:"
    log_error "      pipx install podman-compose"
    log_error "      pip install --user --break-system-packages -U podman-compose"
    log_error "  Or set SKIP_PODMAN_COMPOSE_CHECK=1 to proceed anyway, accepting that every profiled service starts."
    return 1
}

# preflight_install
# Runs all install-time checks. Aggregates all failures before exiting.
# Thresholds can be overridden via env vars (useful for CI / smaller test envs):
#   INSTALLER_MIN_DISK_GB   (default 20)
#   INSTALLER_MIN_RAM_GB    (default 7)
#
# The RAM floor was 8 until 2026-08-04. It rejected hosts provisioned as "8 GB"
# that report less: check_ram's 5% headroom covers a VM given 8 GiB (~7.76 GiB
# reported), but not one given 8 GB decimal, nor one with a larger firmware
# reservation — a real RHEL 10 box reporting 7.4 GiB failed a floor of 7782 MiB
# by 1.3%. 7 admits those without admitting a genuinely half-sized host.
#
# check_jq runs LAST: it is the only check here that may shell out to the
# container runtime, and the aggregator collects every failure regardless of
# order. It carries its own SKIP_JQ_CHECK opt-out (see there) — as with
# SKIP_OS_CHECK inside check_os, the override lives in the check, so this list
# never needs a second copy of itself with one entry dropped (#1188).
#
# check_data_dir_disk is deliberately NOT here, and its absence is the one
# asymmetry with preflight_update. This runs at install.sh:162, before the
# prompts; DATA_DIR is collected at :203 and can still be changed at that prompt,
# so anything measured here would be a figure for a path the operator is about to
# replace. install.sh calls the check inline the moment the value is final
# (#1220). Moving the whole list behind the prompts instead is what the fix
# avoids: it would put the OS, port and jq checks after five questions, so an
# operator on an unsupported OS answers all of them before being told no.
preflight_install() {
    log_info "Running pre-install checks..."
    local fail=0
    check_disk "$INSTALL_DIR" "${INSTALLER_MIN_DISK_GB:-20}"  || fail=1
    check_ram "${INSTALLER_MIN_RAM_GB:-7}"                    || fail=1
    check_os                                                  || fail=1
    check_port "${HTTPS_PORT:-8443}"                          || fail=1
    check_port "${HTTP_PORT:-8080}"                           || fail=1
    check_jq                                                  || fail=1
    # Runtime-scoped: it is podman-compose that ignores `profiles:`, so any
    # podman install leaks pgadmin and metrics-forwarder. Its own
    # SKIP_PODMAN_COMPOSE_CHECK opt-out lives inside the check, so this list
    # never needs a second copy of itself with one entry dropped (#1188).
    check_podman_compose_version                              || fail=1
    [ "$fail" -eq 0 ] || die "Pre-install checks failed."
}

# preflight_update
# Runs all update-time checks. Aggregates all failures before exiting.
# Thresholds: INSTALLER_UPDATE_MIN_DISK_GB (default 5)
#
# SKIP_INFLIGHT=1 opts out of the in-flight pipeline check and nothing else —
# every other check still runs, with the same arguments. Default off, so a
# caller that does not set it is unaffected. update.sh --force sets it: the flag
# means "bypass the in-flight check", not "bypass pre-flight". It used to
# hand-roll a second copy of this list instead, and that copy drifted — it
# hardcoded the disk threshold and pointed check_cert at a path no install has.
# Since a missing file is a skip there, the cert check silently never ran and
# reported success, on the one path where a safety check was already being
# overridden. The skip is logged where the check would have been, so a check
# that did not run still shows up in the operator's pre-flight output.
#
# check_jq is the update-side half of #1233 and matters more here than at install
# time: this is the path with a backup, migrations and a destructive auto-rollback
# behind it, and it is the path where the stack is actually running, so check_jq's
# probe has real `ps --format json` output to read. Its own SKIP_JQ_CHECK opt-out
# lives inside the check for the same reason SKIP_INFLIGHT lives here rather than
# in a second copy of this list.
#
# check_data_dir_disk needs no ordering work on this side: update.sh sources the
# install's .env at :144, long before it gets here, so DATA_DIR is already in the
# environment — and an install whose .env carries none is a case the check
# answers rather than skips. It takes the same threshold as the line above it:
# same number, same filesystem, one verdict (#1220).
preflight_update() {
    log_info "Running pre-update checks..."
    local fail=0
    check_disk "$INSTALL_DIR" "${INSTALLER_UPDATE_MIN_DISK_GB:-5}"  || fail=1
    check_data_dir_disk "${INSTALLER_UPDATE_MIN_DISK_GB:-5}"        || fail=1
    if [ "${SKIP_INFLIGHT:-0}" = "1" ]; then
        log_warn "In-flight pipeline check skipped (SKIP_INFLIGHT=1)."
    else
        check_in_flight_pipeline                                    || fail=1
    fi
    check_cert "$INSTALL_DIR/docker/nginx/ssl/server.crt"           || fail=1
    check_jq                                                        || fail=1
    # podman-compose PRESENCE only — the version floor stays install-time-only
    # (an update on an old build is no worse off than the install it already
    # has), but an update with no podman-compose at all cannot complete: it
    # would fail at the first compose call, past the backup and migrations
    # below. detect_runtime no longer dies on that host, so this gate is what
    # keeps the report ahead of anything being touched.
    check_podman_compose_presence                                   || fail=1
    [ "$fail" -eq 0 ] || die "Pre-update checks failed."
}
