#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/health.sh — post-restart health polling.
# Requires: common.sh sourced.
# For wait_for_all_containers_healthy / capture_container_baseline: COMPOSE_CMD and
# COMPOSE_FILE must be set.
#
# The wait_* functions return 0 on success, non-zero on timeout.
# capture_container_baseline records the pre-update state the container check compares
# against (#1211) and always returns 0 — it is a diagnostic, not a gate.
#
# jq is used to read `compose ps --format json` when it is installed, and is
# deliberately NOT required: without it only compact one-object-per-line output is
# accepted and every other shape fails closed rather than passing as healthy
# (#1233, _health_ps_parse).

# wait_for_health <url> [<timeout_seconds=60>]
# Polls $url every 2 seconds. Uses curl -k (tolerates self-signed certs) since this
# is internal localhost traffic from the installer to its own API.
wait_for_health() {
    local url=$1
    local timeout=${2:-60}
    log_info "Waiting up to ${timeout}s for $url ..."
    local start now
    start=$(date +%s)
    while true; do
        if curl -sfk --max-time 5 "$url" >/dev/null 2>&1; then
            log_ok "Health OK: $url"
            return 0
        fi
        now=$(date +%s)
        if [ "$((now - start))" -ge "$timeout" ]; then
            log_error "Health TIMEOUT after ${timeout}s: $url"
            return 1
        fi
        sleep 2
    done
}

# Consecutive clean polls required before wait_for_all_containers_healthy calls
# the stack healthy. Overridable so a test does not have to sit through the real
# cadence; callers have no reason to touch it.
: "${HEALTH_STABLE_POLLS:=2}"
: "${HEALTH_POLL_INTERVAL:=3}"

# The states that make a container "not healthy", as ONE predicate shared by the health
# window and the pre-update baseline below:
#   - State exited / dead / created / restarting  (regardless of Health)
#   - OR State "absent": SYNTHETIC, never emitted by a runtime (docker and podman
#     between them produce created/running/paused/restarting/removing/exited/dead
#     and nothing else). _health_absent_rows mints it for a service the compose
#     file declares a healthcheck for that has no row in `ps` at all — a container
#     that should be watched and is not there is not known-good either, and
#     counting only the rows that DID arrive is how a check scores zero bad states
#     over an incomplete set.
#   - OR Health unhealthy / starting              (regardless of State)
#   - OR a container whose State could not be read at all: a row this code cannot
#     evaluate must never count as healthy (#1233) — check_cert's missing-file skip,
#     wearing a container's clothes.
# State=running with Health "" or "healthy" is the healthy case (several compose
# services define no healthcheck, so an empty Health is normal, not suspicious), and a
# PRESENT state outside the bad list — paused, removing — passes exactly as it always
# has: #1233 changed how the fields are EXTRACTED, not which values are bad.
#
# One copy on purpose. A baseline built from a second, slightly different list would
# exempt containers the window still fails on (or fail to exempt ones it should) — the
# hand-rolled-duplicate-check-list trap .claude/rules/deploy.md warns about, and how
# `update.sh --force` drifted (#1188).
_health_container_is_bad() {
    local state=$1 health=$2
    case "$state" in
        exited|dead|created|restarting|absent|"") return 0 ;;
    esac
    case "$health" in
        unhealthy|starting) return 0 ;;
    esac
    return 1
}

# _health_baseline_is_bad <state> <health>
# The narrower question the BASELINE asks, DERIVED from the predicate above by asking it
# twice — never a second list of states (#1234).
#
# The baseline is a single `ps` sample taken before the update touches anything, and
# whatever it names is exempt from the health window for the WHOLE update. That is right
# for a container the operator has been ignoring for weeks; it is wrong for one that is
# merely still coming up. Health="starting" is the one bad state that is transient BY
# CONSTRUCTION — bounded by start_period + retries×interval, after which the runtime moves
# the container to "unhealthy" — so a sample taken inside that window says nothing about
# whether the service works. State="restarting" and Health="unhealthy" carry no such bound.
#
# The sequence that bit: host reboots → systemd brings the stack up → an operator or cron
# runs update.sh INSIDE the healthcheck's start_period → every still-starting service is
# baselined as "already not healthy" and exempt for the entire window. A broken new image
# for scheduler, teams-bridge or metrics-forwarder then passes, and those are exactly the
# services nginx's `depends_on: service_healthy` gates do NOT cover, so nothing else
# catches them either.
#
# So: ask the shared predicate as-is, then ask it AGAIN with the transient signal taken
# away ("" is its documented no-healthcheck value). A row that stops being bad once
# "starting" is removed was bad only because of it — do not exempt it. A row that is
# still bad — restarting, exited, an unreadable State — is exempt exactly as before,
# because those are the states #1211 is actually about. The health window keeps the
# UNNARROWED predicate, so #1210's starting/restarting detection there is untouched.
#
# The accepted cost, stated so it is a decision and not an oversight: a CHRONICALLY
# broken service reads as "starting" for its first start_period + retries×interval too
# (teams-bridge: 30s + 3×30s ≈ 2 minutes; api: 40s + 3×30s), and inside that window it is
# no longer exempt — so an update started right after a reboot can now fail on it and take
# the destructive rollback #1211 exists to avoid. That is the same two minutes in which
# the opposite mistake — exempting a service that has proven nothing, and handing the
# whole window to whatever the new image does — is possible, and only one of the two can
# be avoided from a single sample. This picks the one where the check still runs: a
# rollback of a good release is recoverable, an update that verified nothing is not
# (#1233's trade, applied to the baseline). capture_container_baseline names the
# containers it declined to exempt for exactly this reason.
_health_baseline_is_bad() {
    local state=$1 health=$2
    _health_container_is_bad "$state" "$health" || return 1
    if [ "$health" = "starting" ]; then
        health=""
    fi
    _health_container_is_bad "$state" "$health"
}

# _health_baseline_is_transient <state> <health>
# The complement of that narrowing WITHIN the bad set: bad now, but bad only because of
# the transient state. It exists so the baseline can NAME what it declined to exempt —
# a narrowing nobody can see reads as a bug the first time it costs someone an update,
# and "the stack was still starting when you ran this" is the one line that explains a
# health window that then fails on those same services.
_health_baseline_is_transient() {
    _health_container_is_bad "$1" "$2" || return 1
    ! _health_baseline_is_bad "$1" "$2"
}

# Filename of the pre-update baseline INSIDE a snapshot directory. rollback.sh's
# snapshot_state writes it there; update.sh points HEALTH_BASELINE_FILE at it.
# shellcheck disable=SC2034  # read by rollback.sh's snapshot_state and by update.sh
HEALTH_BASELINE_NAME=container-baseline

# Path to the baseline wait_for_all_containers_healthy compares against. UNSET IS A
# SUPPORTED STATE and means "no baseline": every container is then held to a healthy
# result, which is what install.sh wants (a fresh install has no "before") and what
# every caller did before #1211.
: "${HEALTH_BASELINE_FILE:=}"

# The jq program behind _health_ps_parse: one container per output line as three
# '|'-separated fields — service, state, health. '|' and not @tsv, because tab is IFS
# whitespace in the shell: `IFS=$'\t' read` COLLAPSES a run of tabs, so the empty
# Health field of a no-healthcheck container would shift its neighbours and its State
# would be read as its Health. No compose service name, container name or state can
# carry '|' (the runtimes reject the character) — and because that claim is about
# emitters this code has never seen, a field that DOES carry one is blanked by the
# final map rather than joined: a stray '|' can only ever push a row towards
# <unparsed>/bad, never shift a bad State into the healthy Health column.
#
#   - `if type == "array"` explodes the one-array shapes (docker compose < 2.21
#     emitted a single JSON array; podman pretty-prints one) into the same per-object
#     stream that NDJSON and pretty-printed objects already are. jq itself does not
#     care about whitespace — which is the point of parsing the JSON instead of
#     pattern-matching its raw text (#1233).
#   - A stream element that is NOT an object (a scalar, null, an array nested one
#     level deeper) is normalised to {} so it degrades to ONE bad <unparsed> row.
#     Without that, `.Names` on a non-object is an error the trailing `?` does NOT
#     suppress (it binds to the [0] index, not the whole path; jq 1.7 exits 5) and
#     one odd element would refuse the entire poll — per poll, not the per-field
#     degrade everything below promises.
#   - Every field access is error-suppressed (?) and defaulted, so a vocabulary
#     difference degrades per FIELD, never per poll: no Service key falls back to
#     podman's Names[0], then compose's Name; still nothing → "" → later counted
#     under <unparsed>, a name no baseline entry can equal, so never exempt. No State
#     key → "" → _health_container_is_bad fails it closed.
#   - Health: a PRESENT Health key always wins, including compose's own Health:""
#     (the documented no-healthcheck case). With no Health key at all — podman's
#     `ps` JSON carries none — the "(healthy)/(unhealthy)/(starting)" suffix podman
#     embeds in its Status string is the only healthcheck signal that vocabulary
#     has, so it is read from there; no key AND no suffix reads as "no healthcheck".
#     Without this, a running container failing its healthcheck on the podman
#     topology would parse cleanly and still read as healthy — the #1233 silent
#     pass reproduced one layer deeper, behind a successful parse.
#   - `map(if type == "string" …)` blanks a non-string field (an emitter that made
#     State an object) instead of joining garbage into the row.
_HEALTH_PS_JQ='
  if type == "array" then .[] else . end
  | if type == "object" then . else {} end
  | [(.Service? // .Names[0]? // .Name? // ""),
     (.State? // ""),
     (.Health? // (.Status? // ""
        | if type != "string" then ""
          elif test("\\(unhealthy\\)") then "unhealthy"
          elif test("\\(starting\\)") then "starting"
          elif test("\\(healthy\\)") then "healthy"
          else "" end))]
  | map(if type == "string" and (contains("|") | not) then . else "" end)
  | join("|")
'

# ---------------------------------------------------------------------------
# Which containers are SUPPOSED to have a healthcheck (#C)
# ---------------------------------------------------------------------------
# `ps` cannot answer this, and on podman it cannot even be inferred. Proven from
# podman's own source (v5.6.0):
#
#   cmd/podman/containers/ps.go:149  JSON output overwrites Status with
#                                    psReporter.Status()
#   cmd/podman/containers/ps.go:384  which appends " (<health>)" ONLY when the
#                                    health string is non-empty
#   pkg/ps/ps.go:234                 that string is Container.HealthCheckStatus()
#   libpod/healthcheck.go:466-481    which returns results.Status from the
#                                    healthcheck LOG FILE
#   libpod/healthcheck.go:438-445    and reading a log file that does not exist
#                                    yields an EMPTY result and no error
#
# The log file appears only once the first healthcheck has run. So between
# container start and that first result — `interval` after start, 30s for mcp,
# api, ui, nginx and teams-bridge — a container WITH a healthcheck renders
# byte-identically to one WITHOUT: `Up 3 seconds`, no suffix. The same function
# also returns "" for a container that has no healthcheck at all, so the two are
# not merely hard to tell apart, they are the same string.
#
# _health_ps_parse mapped both to Health="", _health_container_is_bad calls that
# healthy, and the window wants two clean polls three seconds apart — so it could
# and did close inside that blind spot: a real install logged "All containers
# healthy." while mcp and teams-bridge went on to report `(unhealthy)` for the
# next two minutes. #1233's failure exactly: zero bad states scored, passed
# silently, the auto-rollback behind it never armed.
#
# The compose file is the only place that says which containers this check is
# supposed to be watching, so that is where the answer comes from. It is also the
# STRONGER source: it says a container should be watched even if the runtime lost
# the healthcheck.

# _health_compose_healthchecked [<compose-file> ...]
# One "<service>|<container_name>" line per service that declares a
# `healthcheck:`; container_name is empty when the service sets none. Both names
# are needed because the two `ps` vocabularies disagree about what a container is
# called: `docker compose ps` carries Service ("mcp") while podman carries
# Names[0], the container_name ("assetatlas-mcp"), and _health_ps_parse hands on
# whichever it found.
#
# Reading NOTHING is a supported state, not an error: it means "this check knows
# of no healthcheck", which is precisely the pre-#C behaviour. _health_ps_parse
# is also called by preflight's check_jq and directly from tests, with no compose
# file anywhere in sight.
#
# Hand-rolled rather than `compose config`: podman-compose 1.0.6 has no usable
# one, this must answer without invoking a container runtime at all, and
# compose_image_refs (runtime.sh) already reads these files the same way. Same
# limitation, stated rather than discovered: a service key is recognised at
# exactly two spaces of indent, which is how this repo writes them.
_health_compose_healthchecked() {
    local file line body ws indent
    local svc cname hc hc_indent in_services
    for file in "$@"; do
        [ -f "$file" ] || continue
        svc=""; cname=""; hc=0; hc_indent=-1; in_services=0
        while IFS= read -r line || [ -n "$line" ]; do
            line=${line%$'\r'}
            body=${line#"${line%%[![:space:]]*}"}
            # Blank and comment lines carry no structure — and a '#' line at
            # service indent must not be read as a service named '#'.
            case "$body" in ''|'#'*) continue ;; esac
            ws=${line%%[![:space:]]*}
            indent=${#ws}
            # The healthcheck block ends at the first line indented no deeper
            # than the `healthcheck:` key itself.
            if [ "$hc_indent" -ge 0 ] && [ "$indent" -le "$hc_indent" ]; then
                hc_indent=-1
            fi
            if [ "$indent" -eq 0 ]; then
                _health_emit_healthchecked "$svc" "$cname" "$hc"
                svc=""; cname=""; hc=0; hc_indent=-1
                # Only `services:` opens a block whose two-space children are
                # services; `volumes:` and `networks:` indent identically.
                if [ "${line%%:*}" = "services" ]; then in_services=1; else in_services=0; fi
                continue
            fi
            [ "$in_services" = 1 ] || continue
            if [ "$indent" -eq 2 ]; then
                _health_emit_healthchecked "$svc" "$cname" "$hc"
                svc=${body%%:*}
                cname=""; hc=0; hc_indent=-1
                continue
            fi
            [ -n "$svc" ] || continue
            if [ "$hc_indent" -ge 0 ]; then
                # A healthcheck the compose file itself turns off is not one this
                # check may wait for — it would block the window for ever.
                case "$body" in
                    disable:*true*|test:*NONE*) hc=0; hc_indent=-1 ;;
                esac
                continue
            fi
            case "$body" in
                healthcheck:*)
                    hc=1; hc_indent=$indent ;;
                container_name:*)
                    cname=${body#container_name:}
                    cname=${cname%%[[:space:]]#*}                 # inline comment
                    cname=${cname#"${cname%%[![:space:]]*}"}      # leading space
                    cname=${cname%"${cname##*[![:space:]]}"}      # trailing space
                    cname=${cname#\"}; cname=${cname%\"}
                    cname=${cname#\'}; cname=${cname%\'}
                    ;;
            esac
        done < "$file"
        _health_emit_healthchecked "$svc" "$cname" "$hc"
    done
    return 0
}

# _health_emit_healthchecked <service> <container_name> <declares_healthcheck>
# Explicit `return 0`, and `if` rather than `[ … ] && printf`: this is called
# from a file sourced under `set -euo pipefail`, where a trailing AND-list that
# happens to be false takes the whole installer down.
_health_emit_healthchecked() {
    if [ "$3" != "1" ] || [ -z "$1" ]; then
        return 0
    fi
    printf '%s|%s\n' "$1" "$2"
    return 0
}

# _health_expected_names <healthchecked>
# The flat name list to match `ps` rows against: every service name and every
# container_name from _health_compose_healthchecked's output.
_health_expected_names() {
    local svc cname
    [ -n "$1" ] || return 0
    while IFS='|' read -r svc cname; do
        if [ -n "$svc" ]; then printf '%s\n' "$svc"; fi
        if [ -n "$cname" ]; then printf '%s\n' "$cname"; fi
    done <<<"$1"
    return 0
}

# _health_mark_pending <parsed>
# Rewrites Health "" to "starting" for any row naming a service the compose file
# declares a healthcheck for. "The healthcheck has not reported yet" IS what
# `starting` means, so this deliberately reuses that value rather than inventing
# a state: everything downstream — the window, the baseline's narrowing (#1234,
# which correctly declines to exempt a transient state), the offender lists and
# every message — keeps working unchanged.
#
# With no compose file, or one that declares no healthcheck, the input is passed
# through untouched.
_health_mark_pending() {
    local parsed=$1 expected names svc state health
    [ -n "$parsed" ] || return 0
    expected=$(_health_compose_healthchecked "${COMPOSE_FILE:-}")
    names=$(_health_expected_names "$expected")
    if [ -z "$names" ]; then
        printf '%s\n' "$parsed"
        return 0
    fi
    while IFS='|' read -r svc state health; do
        # Herestring, never `printf … | grep -Fxq`: under pipefail a MATCHED
        # grep -q fails the pipeline (#1199).
        if [ -z "$health" ] && [ -n "$svc" ] && grep -Fxq "$svc" <<<"$names"; then
            health=starting
        fi
        printf '%s|%s|%s\n' "$svc" "$state" "$health"
    done <<<"$parsed"
    return 0
}

# _health_absent_rows <parsed> <baseline>
# One synthetic "<service>|absent|" row per healthchecked service with NO row in
# the parsed container list — the other half of the same question. A container
# that should be watched and is not in `ps` at all has proven exactly as little
# as one whose healthcheck has not run, and scoring only the rows that arrived is
# how a counting check passes over an incomplete set.
#
# The window reads plain `ps`, which omits non-running containers deliberately
# (#1210: `-a` there would surface the stopped containers of profiles nobody
# asked to run), so this is how a service that failed to come up at all is seen.
#
# Window-only, NOT part of _health_ps_parse, and that is the point:
# capture_container_baseline must never learn "absent" this way. A service that
# is genuinely NEW in this release has no row before the update either, and
# baselining it as already-broken would exempt it for the whole update — the one
# thing _health_offenders' "no baseline entry means REGRESSED" rule exists to
# prevent.
#
# A service named in the baseline under EITHER of its names is skipped outright
# rather than emitted and then classified: it was already not healthy before this
# update, and this update did not remove it.
_health_absent_rows() {
    local parsed=$1 baseline=$2 expected present svc cname
    expected=$(_health_compose_healthchecked "${COMPOSE_FILE:-}")
    [ -n "$expected" ] || return 0
    present=$(_health_ps_names "$parsed")
    while IFS='|' read -r svc cname; do
        [ -n "$svc" ] || continue
        # Herestrings throughout, never `printf … | grep -Fxq` (#1199).
        if [ -n "$present" ] && grep -Fxq "$svc" <<<"$present"; then continue; fi
        if [ -n "$present" ] && [ -n "$cname" ] && grep -Fxq "$cname" <<<"$present"; then continue; fi
        if [ -n "$baseline" ] && grep -Fxq "$svc" <<<"$baseline"; then continue; fi
        if [ -n "$baseline" ] && [ -n "$cname" ] && grep -Fxq "$cname" <<<"$baseline"; then continue; fi
        printf '%s|absent|\n' "$svc"
    done <<<"$expected"
    return 0
}

# _health_ps_names <parsed>
# The name column of _health_ps_parse's output, one per line.
_health_ps_names() {
    local svc
    [ -n "$1" ] || return 0
    while IFS='|' read -r svc _; do
        printf '%s\n' "$svc"
    done <<<"$1"
    return 0
}

# _health_ps_parse <ps_output>
# THE one interpreter of `compose ps --format json` output: the health window and the
# pre-update baseline both read container state through here and nowhere else, so
# "which shapes can we read" has exactly one answer. Prints one
# "<service>|<state>|<health>" line per container.
#
# Returns non-zero when the output cannot be confidently interpreted per container —
# callers MUST fail closed on that (window: the poll is not clean; baseline: nothing
# is exempt). Its predecessor grep'd the RAW text with a compact-JSON-only pattern, so
# a pretty-printing emitter matched nothing, every container read as healthy, and the
# window passed on its first poll without the check ever really running (#1233) — the
# check_cert / ON_ERROR_STOP family. podman-compose on the primary production
# topology has never had its `ps --format json` shape verified, which is why this
# parses instead of pattern-matching.
#
# With jq, every shape parses: NDJSON, pretty-printed objects, single-line array,
# pretty-printed array. jq is NOT a guaranteed dependency of a customer host (nothing
# in preflight requires it; fetch.sh parses its manifest "minimal, no jq" for the same
# reason), so without it the one shape ever verified against real output — compact
# NDJSON, gated by _health_ps_is_ndjson — is still accepted, and everything else is
# REFUSED rather than misread. On such a host the health window fails and its timeout
# message names jq as the remedy. That is the deliberate trade: rolling back a good
# release is recoverable, a no-op health check passing a bad one is not.
#
# HEALTH_JQ_BIN is a test seam (point it at a non-command to drive the no-jq path);
# operators have no reason to touch it.
#
# The rows it prints go through _health_mark_pending on the way out, so a
# healthcheck that has not reported yet reads as "starting" rather than as "no
# healthcheck" (#C). It runs on BOTH extraction paths and after the refusal
# checks, so it can only ever reclassify a row that was going to be printed
# anyway — it never rescues output that could not be parsed.
_health_ps_parse() {
    local ps_output=$1 jq_bin="" parsed="" line svc state health
    jq_bin=$(command -v "${HEALTH_JQ_BIN:-jq}" 2>/dev/null) || jq_bin=""
    if [ -n "$jq_bin" ]; then
        # Herestring, not a pipe (#1199). On invalid or TRUNCATED input jq exits
        # non-zero after emitting whatever rows it had finished — return before
        # printing, so a half-parsed stream is refused whole rather than passed off
        # as a shorter container list.
        parsed=$("$jq_bin" -r "$_HEALTH_PS_JQ" <<<"$ps_output" 2>/dev/null) || return 1
    else
        _health_ps_is_ndjson "$ps_output" || return 1
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            # Compact-only patterns, deliberately: the gate above only ever admits compact
            # one-object-per-line output, and these must not quietly start accepting
            # spaced variants of a shape the gate would refuse. Greedy `.*` is safe for
            # the same reason: one object per line is guaranteed, so there is no second
            # container on the line for it to run into (#1232's failure mode).
            # `sed -nE …p`, not a bare `s///`: without -n a line NOT carrying the field
            # passes through verbatim and a whole JSON blob lands in the variable; a
            # missing field must yield "" instead.
            svc=$(sed -nE 's/.*"Service":"([^"]*)".*/\1/p' <<<"$line")
            state=$(sed -nE 's/.*"State":"([^"]*)".*/\1/p' <<<"$line")
            health=$(sed -nE 's/.*"Health":"([^"]*)".*/\1/p' <<<"$line")
            parsed="$parsed$svc|$state|$health"$'\n'
        done <<<"$ps_output"
        parsed=${parsed%$'\n'}
    fi
    if [ -n "$parsed" ]; then
        _health_mark_pending "$parsed"
    fi
    return 0
}

# _health_bad_services <parsed> [<predicate=_health_container_is_bad>]
# One "<service> <state>" line per container in a bad state; empty when none is.
# Input is _health_ps_parse OUTPUT ("svc|state|health" lines), never raw ps text —
# raw-text matching is the defect this replaced (#1233). A row with no service name is
# reported as <unparsed> — a name no baseline entry can equal, so an unattributable
# container is always counted, never exempted — and a row with no state fails
# _health_container_is_bad closed, so it is listed too.
#
# <predicate> is WHICH bad-question to ask, so the baseline can ask its narrower one
# (_health_baseline_is_bad, #1234) through this same loop instead of hand-rolling a
# second one. It defaults to the shared predicate, which is what the health window always
# uses, and falls BACK to it when handed a name that is not a function: a typo here must
# make this report MORE bad containers, never silently none — an empty answer would pass
# the window on a broken stack.
_health_bad_services() {
    local parsed=$1 is_bad=${2:-_health_container_is_bad} svc state health
    declare -F "$is_bad" >/dev/null 2>&1 || is_bad=_health_container_is_bad
    [ -n "$parsed" ] || return 0
    while IFS='|' read -r svc state health; do
        if "$is_bad" "$state" "$health"; then
            printf '%s %s\n' "${svc:-<unparsed>}" "${state:-<unparsed>}"
        fi
    done <<<"$parsed"
}

# _health_ok_services <parsed>
# One service name per container the shared predicate does NOT call bad — the same
# question the other way round, so there is still exactly one list of bad states.
#
# Only the baseline needs it, and only since it started reading `ps -a` (#1234). The
# baseline exempts by SERVICE NAME, while `ps` returns rows per CONTAINER, and `-a`
# widens the rows that can produce a name: an orphan left by an interrupted recreate, a
# `created` container from a start that failed, any non-running leftover is State=exited
# or created — bad — so its service name would enter the baseline and exempt that
# service's *currently running, currently healthy* container for the whole update. A
# broken new image for it would then pass the window silently, which is the one thing
# this check exists to prevent.
#
# A service with a healthy container was healthy before this update, whatever a leftover
# of the same name says. Cancelling the exemption on that evidence can only ever make the
# baseline SMALLER, and never re-opens #1211: a service with no healthy row keeps its
# exemption exactly as before.
_health_ok_services() {
    local parsed=$1 svc state health
    [ -n "$parsed" ] || return 0
    while IFS='|' read -r svc state health; do
        if ! _health_container_is_bad "$state" "$health"; then
            printf '%s\n' "${svc:-<unparsed>}"
        fi
    done <<<"$parsed"
}

# _health_ps_is_ndjson <ps_output>
# The no-jq admission gate inside _health_ps_parse — with jq present it is never
# consulted. 0 when the output really is ONE JSON OBJECT PER LINE carrying compact
# `"Service":"` keys, which is the single assumption the fallback's sed extraction
# rests on: that parsing is line-oriented, so the moment two containers share a line
# the greedy `.*"Service":"…"` takes the LAST one and the whole stack is reported
# under one (often healthy) service name — #1232 hit exactly that.
#
# That is not hypothetical. `docker compose ps --format json` emitted a SINGLE-LINE
# JSON ARRAY until compose v2.21, and nothing here pins a compose version — runtime.sh
# only gates on Docker >= 24.0, whose contemporary compose plugin is older than that.
# `podman-compose ps --format json` is the other suspect shape: believed to be
# pretty-printed `podman ps` output with no "Service" key at all, never verified.
#
# Cheap test, and it is the right one: in NDJSON the number of `"Service":"` OCCURRENCES
# equals the number of LINES carrying it. An array collapses the occurrences onto one
# line; a pretty-printed object has no compact occurrence at all. A false here makes
# _health_ps_parse refuse the output, and BOTH its callers fail closed — the window's
# poll is not clean, the baseline exempts nothing. (Until #1233 a false only dropped
# the BASELINE, while the detection itself still pattern-matched the raw text and read
# every unmatchable shape as healthy.)
_health_ps_is_ndjson() {
    local ps_output=$1 lines occurrences occurrence_n
    lines=$(grep -cF '"Service":"' <<<"$ps_output") || lines=0
    # grep -oF, then count: one match per line of ITS output, so repeats on one input
    # line are counted separately — which is the whole point of the comparison.
    occurrences=$(grep -oF '"Service":"' <<<"$ps_output") || return 1
    occurrence_n=$(grep -c . <<<"$occurrences") || occurrence_n=0
    [ "$occurrence_n" -gt 0 ] && [ "$occurrence_n" -eq "$lines" ]
}

# _health_offenders <parsed> <baseline_services>
# One "<origin> <service> <state>" line per container in a bad state, where origin is:
#   regressed    — bad now, and NOT named in the baseline
#   pre-existing — named in the baseline, so it was already bad before the update
#
# A container with NO baseline entry is REGRESSED. That is the deliberate call for a
# service that only exists in the new release (#1211): it is genuinely new and genuinely
# unhealthy, nothing says it ever worked, and treating "unknown" as "was already broken"
# would hide every brand-new broken service from the check that exists to catch a bad
# release. It also makes an empty baseline and a missing baseline mean the same safe
# thing — nothing is exempt.
#
# Membership is by SERVICE, not by state: a container that was restarting before and is
# unhealthy now was still already broken, and this update did not break it.
_health_offenders() {
    local parsed=$1 baseline=$2 bad svc state origin
    bad=$(_health_bad_services "$parsed")
    [ -n "$bad" ] || return 0
    while read -r svc state; do
        [ -n "$svc" ] || continue
        origin=regressed
        # Herestring, never `printf … | grep -Fxq`: under pipefail a MATCHED grep -q
        # fails the pipeline (#1199, .claude/rules/deploy.md).
        if [ -n "$baseline" ] && grep -Fxq "$svc" <<<"$baseline"; then
            origin=pre-existing
        fi
        printf '%s %s %s\n' "$origin" "$svc" "$state"
    done <<<"$bad"
}

# _health_offender_list <offenders> <origin>
# "svc(state) svc(state) " for the offenders of one origin — the format #1210
# introduced, kept verbatim so an operator's eye still finds it in the log.
_health_offender_list() {
    local offenders=$1 want=$2 origin svc state out=""
    [ -n "$offenders" ] || return 0
    while read -r origin svc state; do
        [ "$origin" = "$want" ] || continue
        out="$out$svc($state) "
    done <<<"$offenders"
    printf '%s' "$out"
}

# capture_container_baseline <file>
# Record which containers are ALREADY not healthy, BEFORE the update touches anything.
# One service name per line; an EMPTY FILE is a real answer meaning "everything was
# healthy", not "no baseline was taken".
#
# "Not healthy" here is _health_baseline_is_bad — the shared predicate asked twice, with
# the one transient bad state (Health="starting") taken away the second time (#1234). A
# service that had simply not finished starting up when this ran is NOT exempted; a
# single sample cannot tell a service that is coming up from one that will never be up,
# and exempting it would hand a whole update to whatever the new image does.
#
# The container SET is `ps -a`, wider than the window's plain `ps`, so a service the
# operator had STOPPED is seen at all (#1234). It has not been healthy for as long as it
# has been stopped, and the update that restarts it did not break it.
#
# Why this exists (#1211): once starting/restarting counted as bad (#1210), a host with
# a container the operator had been ignoring for weeks — a misconfigured
# metrics-forwarder, a teams-bridge with bad credentials — failed its health window,
# triggered the DESTRUCTIVE auto-rollback (drop and restore the database from a backup
# taken minutes earlier), and then failed the rollback's own container check on the same
# pre-existing condition. That host could no longer update at all, and every attempt
# burned another restore cycle.
#
# Best effort, and it NEVER fails the caller: a snapshot must not die over a diagnostic.
# When compose cannot be read — or is read but not in a JSON shape _health_ps_parse can
# interpret per container — the file is written EMPTY, so nothing is exempted and both
# checks behave exactly as they did before this existed.
capture_container_baseline() {
    local out=$1
    : "${COMPOSE_CMD:?capture_container_baseline: COMPOSE_CMD must be set}"
    : "${COMPOSE_FILE:?capture_container_baseline: COMPOSE_FILE must be set}"
    local ps_output parsed bad transient ok_svcs svc state list="" found=0 total=0 skipped=0
    local transient_list="" transient_n=0 conflicted_list="" conflicted=0
    # `-a` HERE, and deliberately not in the health window (#1234). Plain `ps` omits
    # non-running containers, so a service the operator had STOPPED — `docker stop
    # teams-bridge` is the usual workaround for a broken one — is invisible to the
    # baseline. Every service is `restart: unless-stopped`, so `up -d` starts it again;
    # if it then crash-loops it counts as REGRESSED, fails the window and takes the
    # destructive rollback, which cannot fix it. That is #1211's exact symptom for a
    # shape its text did not describe. Verified against real `docker compose ps -a
    # --format json`: a stopped container reports State="exited" — which the shared
    # predicate already calls bad — and plain `ps` does not list it at all.
    #
    # Baseline-only, because the objection that kept `-a` out of the window (#1210) is
    # about the WINDOW: there `-a` would surface the stopped containers of profiles
    # nobody asked to run (pgadmin, metrics-forwarder) and fail every check on such a
    # host. Here they only add a name to a file the window never asks about, since the
    # window still reads plain `ps` and so never sees them at all.
    #
    # The cost, stated plainly: a service that was stopped when this ran is exempt for
    # the whole update, so a genuinely broken new image for it passes the window. It was
    # not running before either — this update regressed nothing anyone was relying on,
    # which is the only question the baseline exists to answer.
    ps_output=$($COMPOSE_CMD -f "$COMPOSE_FILE" ps -a --format json 2>/dev/null) || ps_output=""
    if [ -z "$ps_output" ]; then
        # A runtime that will not take `-a` must not lose the baseline altogether: fall
        # back to the list this has always read, and say so. Only on a FAILED or empty
        # `-a` — output that arrives and cannot be interpreted is refused below, and
        # plain `ps` comes from the same emitter, so asking it again would only produce
        # the same unreadable shape.
        ps_output=$($COMPOSE_CMD -f "$COMPOSE_FILE" ps --format json 2>/dev/null) || ps_output=""
        if [ -n "$ps_output" ]; then
            log_info "Pre-update container baseline: 'ps -a' was not accepted by $COMPOSE_CMD, so this reads the running containers only — a container that is currently STOPPED is not represented, and will be held to a healthy result if the update starts it."
        fi
    fi
    mkdir -p "$(dirname "$out")" 2>/dev/null || true
    # `printf ''` rather than `:` to truncate: `:` is a special builtin, and a
    # redirection error on one is fatal to the shell under POSIX mode.
    if ! printf '' > "$out" 2>/dev/null; then
        log_warn "Could not write the pre-update container baseline to $out; this update will hold every container to a healthy result."
        return 0
    fi
    if [ -z "$ps_output" ]; then
        log_warn "Pre-update container baseline: the container list could not be read. Recording an empty baseline, so every container is held to a healthy result."
        return 0
    fi
    # Unreadable and UNINTERPRETABLE have to fail closed the same way. Output this code
    # cannot attribute per container is worse than none: it would name the whole stack
    # after one container and exempt every other from the window under that name.
    if ! parsed=$(_health_ps_parse "$ps_output"); then
        log_warn "Pre-update container baseline: the container list is not in a JSON shape this check can read per container (with jq installed NDJSON, pretty-printed and single-array compose shapes all are; without it, only compact one-object-per-line output). Recording an empty baseline, so every container is held to a healthy result."
        return 0
    fi
    total=$(grep -c . <<<"$parsed") || total=0
    # Whitespace-stripped for the same reason the health window strips its own `grep -c`
    # result: it is compared numerically below (found vs total), and a padding grep would
    # otherwise make that comparison quietly never true — silencing the total-exemption
    # warning on exactly the host that needs it.
    total=${total// /}
    # The narrowed question (#1234) — same loop, same state list, one derived predicate.
    bad=$(_health_bad_services "$parsed" _health_baseline_is_bad)
    transient=$(_health_bad_services "$parsed" _health_baseline_is_transient)
    ok_svcs=$(_health_ok_services "$parsed")
    if [ -n "$bad" ]; then
        while read -r svc state; do
            # Never write a container we could not name — <unparsed> in the file would
            # become a wildcard exempting the next unreadable container — and never
            # write one whose State could not be read either: the window parses the
            # same emitter, so on a shape whose State field is unreadable EVERY
            # container would be baselined bad here and then scored pre-existing
            # there, and a genuinely broken release would pass the window through
            # its own unreadability. Unevaluable rows are counted below instead.
            if [ -z "$svc" ] || [ "$svc" = "<unparsed>" ] || [ "$state" = "<unparsed>" ]; then
                skipped=$((skipped + 1))
                continue
            fi
            # …and never exempt a service that ALSO has a healthy container here. `ps -a`
            # returns rows per container while the baseline exempts per service, so a
            # leftover row would otherwise hand the service's running, healthy container a
            # free pass for the whole update (#1234, _health_ok_services). Herestring, not
            # `printf … | grep -Fxq`: under pipefail a MATCHED grep -q fails the pipeline
            # (#1199).
            if [ -n "$ok_svcs" ] && grep -Fxq "$svc" <<<"$ok_svcs"; then
                conflicted_list="$conflicted_list$svc($state) "
                conflicted=$((conflicted + 1))
                continue
            fi
            printf '%s\n' "$svc" >> "$out"
            list="$list$svc($state) "
            found=$((found + 1))
        done <<<"$bad"
    fi
    if [ "$skipped" -gt 0 ]; then
        log_warn "Pre-update container baseline: $skipped container row(s) could not be evaluated (no readable name or State) and were NOT exempted; the health window will hold them to a healthy result."
    fi
    if [ "$conflicted" -gt 0 ]; then
        log_warn "Pre-update container baseline: $conflicted service(s) also have a healthy container in this list and were NOT exempted — a leftover or stopped container does not make a service that is up and healthy 'already broken': $conflicted_list"
    fi
    # Say what the narrowing declined to exempt, and why. Silence here is the whole
    # hazard: an operator who ran this minutes after a reboot needs to know that the
    # services still inside their start_period are the ones the window may fail on.
    if [ -n "$transient" ]; then
        while read -r svc state; do
            [ -n "$svc" ] || continue
            transient_list="$transient_list$svc($state) "
            transient_n=$((transient_n + 1))
        done <<<"$transient"
        log_warn "Pre-update container baseline: $transient_n container(s) had not finished starting up and were deliberately NOT exempted, because that state resolves on its own — the runtime moves such a container to healthy or unhealthy once its healthcheck settles: $transient_list. They are held to a healthy result like every other container, so if this update was started moments after a reboot, wait for the stack to settle before reading a health-window failure on them as a verdict on the new release."
    fi
    if [ "$found" -gt 0 ]; then
        log_warn "Pre-update container baseline: $total container(s) seen, $found ALREADY not healthy and NOT counted against this update: $list"
        # An exemption that covers EVERYTHING leaves the health window able to fail only
        # on a service that is not in the list at all. That is the #1211 bargain taken to
        # its limit and it is the right one — a host whose whole stack is already down
        # must still be able to update, and failing it there would only spend a
        # destructive DB restore that cannot fix it — but it must not be silent, because
        # from the outside the update then succeeds exactly like a verified one. It also
        # reaches further since the baseline started reading `ps -a` (#1234): a stack the
        # operator had STOPPED, not just one that is crash-looping, lands here.
        if [ "$found" -eq "${total:-0}" ]; then
            log_warn "Pre-update container baseline: that is EVERY container this saw, so the health window can now fail only on a service absent from that list (one new in this release). This update's success will say nothing about whether the release is good — fix the stack first."
        fi
    else
        log_info "Pre-update container baseline: $total container(s) seen, 0 already not healthy."
    fi
    return 0
}

# wait_for_all_containers_healthy [<timeout_seconds=90>]
# Polls `$COMPOSE_CMD -f $COMPOSE_FILE ps --format json` until no container has
# REGRESSED against $HEALTH_BASELINE_FILE — i.e. until every container that was healthy
# when the baseline was taken is healthy again. Containers without a healthcheck (empty
# Health field) are treated as healthy IF their State is "running" — several compose
# services define no healthcheck at all, so that exemption is load-bearing, not a
# loophole. Which containers those genuinely are is read from $COMPOSE_FILE's
# `healthcheck:` declarations rather than inferred from `ps` (#C): on podman the two
# cases produce the same bytes, so inferring it hands the exemption to every
# healthchecked container that has not run its first check yet.
#
# With NO baseline (install.sh, or any caller that never set HEALTH_BASELINE_FILE) every
# container must be healthy: the pre-#1211 behaviour, unchanged. That stays unchanged for
# a MISSING baseline too (#1234) — a `--rollback` to a snapshot taken before baselines
# existed, which is every snapshot on disk at the time of writing. Softening it was
# considered and declined: with no record of what "as it was" meant, the only honest
# answer is the strict one, and a lenient one would let a rollback that restored a broken
# stack report success on any host with pre-existing breakage. What it does instead is SAY
# so — "no baseline was taken" and "the baseline was empty" are different facts, and only
# the first one explains a check that then fails on damage the rollback did not do.
#
# The container list is read through _health_ps_parse (#1233): a poll whose output
# cannot be interpreted per container — or that lists no containers at all — is NOT a
# clean poll; it resets the stability streak exactly like output that never arrived.
# The failure mode that buries: the previous raw-text match recognised only compact
# JSON, so a pretty-printing emitter produced zero matches, every poll counted clean,
# and the window passed a genuinely broken release on its first poll — logging "All
# containers healthy." exactly like a real pass, with the auto-rollback behind it
# never firing.
#
# Two things this deliberately does NOT treat as healthy (#1197):
#   - Health="starting". A container that has not yet run a passing healthcheck has
#     proven nothing, and a healthchecked service reports "starting" from the moment it
#     is (re)created until its first check passes — which is exactly where the first
#     poll after `up -d` returns lands. Accepting that state made a bad image
#     indistinguishable from a good one.
#     On podman there IS no such report until the first healthcheck has actually
#     run (see _health_compose_healthchecked for the proof out of podman's
#     source), so this state has to be reconstructed from the compose file's own
#     `healthcheck:` declarations — otherwise a healthchecked container inside
#     its first interval is byte-identical in `ps` to one with no healthcheck,
#     and both read as healthy. That is how a real install logged "All containers
#     healthy." while two containers were on their way to `(unhealthy)` (#C).
#   - State="absent" — a service the compose file declares a healthcheck for with
#     no row in `ps` at all. Counting only the containers that turned up is the
#     same defect from the other end: zero bad states over an incomplete set.
#   - State="restarting". A crash-looping container spends most of its cycle there.
#     ("exited"/"dead" are kept in the pattern but rarely fire on their own: plain
#     `ps` omits non-running containers, so a crash loop is caught as "restarting" —
#     which is also why `-a` is deliberately NOT added here, since it would surface
#     the stopped containers of profiled services nobody asked to run and fail every
#     check on such a host. capture_container_baseline DOES pass `-a`, for a reason
#     that applies only to it: see there (#1234).)
#
# ...and one thing it deliberately does not FAIL on (#1211): a container that was
# already in one of those states before the update started. It is still named in the log
# on every outcome — the exemption is never silent — but it does not by itself fail the
# window, because failing there triggers a destructive rollback that cannot fix it and
# then fails the rollback's own check on the very same condition.
#
# It also requires $HEALTH_STABLE_POLLS *consecutive* clean polls rather than returning
# on the first one, so a stack that is clean only for the instant we happened to look
# cannot pass the whole window off as healthy. Those polls happen INSIDE the caller's
# timeout: the deadline is unchanged and the sleep is clamped to it, so requiring a
# stable result can never extend the window the caller asked for — it can only refuse
# to shorten it to a single instant.
wait_for_all_containers_healthy() {
    local timeout=${1:-90}
    : "${COMPOSE_CMD:?wait_for_all_containers_healthy: COMPOSE_CMD must be set}"
    : "${COMPOSE_FILE:?wait_for_all_containers_healthy: COMPOSE_FILE must be set}"
    local need=${HEALTH_STABLE_POLLS:-2}
    # Read once per call, not per poll: the baseline is written before the update starts
    # and cannot change underneath us. A path that is unset or points at nothing is the
    # supported "no baseline" case, not an error.
    #
    # Three answers, not two (#1234). A baseline that names containers; an EMPTY one,
    # which is a real answer meaning "everything was healthy then"; and NO baseline at
    # all. The last splits again by whether the caller ASKED for one: install.sh never
    # points HEALTH_BASELINE_FILE anywhere, because a fresh install has no "before",
    # while --rollback points it into the snapshot being restored — so a missing file
    # THERE says that snapshot predates baselines and nothing can be exempt. All of them
    # hold every container to a healthy result; only the last is worth explaining when
    # the check then fails.
    local baseline="" baseline_n=0 baseline_state=unrequested
    if [ -n "${HEALTH_BASELINE_FILE:-}" ]; then
        if [ -f "$HEALTH_BASELINE_FILE" ]; then
            # A file that is PRESENT but unreadable (a snapshot restored with the wrong
            # ownership, a permission-denied read) is not the empty answer — it is no
            # answer at all, and must not be logged as "the baseline recorded no
            # container as already unhealthy". Same behaviour as absent: nothing exempt,
            # said out loud so the operator knows why nothing was.
            if baseline=$(cat "$HEALTH_BASELINE_FILE" 2>/dev/null); then
                baseline_state=recorded
            else
                baseline=""
                baseline_state=missing
                log_warn "The pre-update container baseline at $HEALTH_BASELINE_FILE exists but could not be read; treating it as absent."
            fi
        else
            baseline_state=missing
        fi
    fi
    if [ -n "$baseline" ]; then
        baseline_n=$(grep -c . <<<"$baseline") || baseline_n=0
        log_info "Waiting up to ${timeout}s for all containers to be healthy (${need} consecutive clean polls; ${baseline_n} container(s) already not healthy before this update are not counted against it)..."
    elif [ "$baseline_state" = "recorded" ]; then
        log_info "Waiting up to ${timeout}s for all containers to be healthy (${need} consecutive clean polls; the pre-update baseline recorded no container as already unhealthy, so every container is held to a healthy result)..."
    elif [ "$baseline_state" = "missing" ]; then
        log_warn "No pre-update container baseline at $HEALTH_BASELINE_FILE: nothing is exempt and every container is held to a healthy result."
        log_info "Waiting up to ${timeout}s for all containers to be healthy (${need} consecutive clean polls)..."
    else
        log_info "Waiting up to ${timeout}s for all containers to be healthy (${need} consecutive clean polls)..."
    fi
    # Say what this window is actually able to check, once, before it starts
    # (#C). A check that quietly does less than its message claims is the family
    # this whole file is a reaction to, and "declares no healthcheck" and "could
    # not read the compose file" both leave the window able to check container
    # STATE only — which is precisely the condition under which it passed a stack
    # with two permanently unhealthy containers.
    local watched watched_n=0
    watched=$(_health_compose_healthchecked "$COMPOSE_FILE") || watched=""
    if [ -n "$watched" ]; then
        watched_n=$(grep -c . <<<"$watched") || watched_n=0
        watched_n=${watched_n// /}
        log_info "${watched_n} service(s) declare a healthcheck in $COMPOSE_FILE and are held to a HEALTHY result: one that has not run its first healthcheck yet, or is missing from the container list entirely, does not count as healthy."
    else
        log_warn "No service in $COMPOSE_FILE declares a healthcheck, or the file could not be read: this window can only check container STATE. On podman a container whose healthcheck has not reported yet is indistinguishable from one that has none, so a failing healthcheck cannot be caught here."
    fi
    local start now elapsed nap ps_output parsed bad offenders poll_problem regressed preexisting clean=0
    local absent
    start=$(date +%s)
    while true; do
        ps_output=$($COMPOSE_CMD -f "$COMPOSE_FILE" ps --format json 2>/dev/null) || ps_output=""
        # bad = how many containers REGRESSED, i.e. are in a bad state now and were not
        # in one at baseline. Three poll problems reset the streak instead of counting
        # as clean, because an unreadable stack is not a clean one: output that could
        # not be READ at all; output _health_ps_parse cannot interpret per container
        # (#1233 — the old raw-text match scored exactly those as "0 bad" and passed
        # the window); and output that parses to ZERO containers (an empty JSON array
        # from a stack that is not running at all is also "0 bad" under a counting
        # rule). The baseline is used as-is: attribution is now parse-or-refuse, so
        # there is no attributed-to-the-wrong-container middle ground left for a bad
        # shape to reach the baseline through (the old NDJSON-or-drop-it dance).
        offenders=""
        bad=1
        poll_problem="unreadable"
        if [ -n "$ps_output" ]; then
            if parsed=$(_health_ps_parse "$ps_output"); then
                if [ -n "$parsed" ]; then
                    poll_problem=""
                    # A healthchecked service with no row at all is added here as
                    # a synthetic "absent" row (#C). It has to happen against the
                    # baseline, so a service that was already gone before this
                    # update is not counted against it — and it has to happen in
                    # the WINDOW, never inside capture_container_baseline, or a
                    # service that is new in this release would be baselined as
                    # already-broken and exempted for the whole update.
                    absent=$(_health_absent_rows "$parsed" "$baseline")
                    if [ -n "$absent" ]; then
                        parsed="$parsed"$'\n'"$absent"
                    fi
                    offenders=$(_health_offenders "$parsed" "$baseline")
                    bad=$(grep -c '^regressed ' <<<"$offenders") || bad=0
                    bad=${bad// /}
                else
                    poll_problem="empty"
                fi
            else
                poll_problem="uninterpretable"
            fi
        fi
        if [ "$bad" = "0" ]; then
            clean=$((clean + 1))
            if [ "$clean" -ge "$need" ]; then
                preexisting=$(_health_offender_list "$offenders" pre-existing)
                if [ -n "$preexisting" ]; then
                    # Never let the exemption go unsaid: the operator's stack is still
                    # broken, this update just did not break it.
                    log_ok "No container regressed during this update. Still not healthy, and already not healthy before it started: $preexisting"
                else
                    log_ok "All containers healthy."
                fi
                return 0
            fi
        else
            clean=0
        fi
        now=$(date +%s)
        elapsed=$((now - start))
        if [ "$elapsed" -ge "$timeout" ]; then
            # Name the containers holding this up, and say which of them THIS UPDATE
            # broke. The old code ran `ps` here and sent it to /dev/null, so the
            # operator got a bare TIMEOUT and had to go looking (#1210) — and the
            # regressed/pre-existing split is the point of the baseline: "the new
            # release broke the api" and "the scheduler has been crash-looping since
            # Tuesday" are different problems with different fixes.
            regressed=$(_health_offender_list "$offenders" regressed)
            preexisting=$(_health_offender_list "$offenders" pre-existing)
            # A poll problem also names nobody, and each is a different problem:
            # "clean but not yet stable" sent an operator looking at containers when
            # the container list itself was what never came back — a list that
            # arrives but cannot be interpreted (#1233) needs its own answer naming
            # the actual remedy, and a list that reads fine but is EMPTY must not
            # recommend installing a jq that would change nothing — because from
            # the outside all of them just look like a stuck update.
            local nobody="<none in the last poll — the stack was clean but not yet stable>"
            case "$poll_problem" in
                unreadable)
                    nobody="<the container list could not be read at all — check $COMPOSE_CMD -f $COMPOSE_FILE ps>" ;;
                uninterpretable)
                    nobody="<the container list could not be interpreted per container: $COMPOSE_CMD -f $COMPOSE_FILE ps --format json emits a shape this check does not read. Installing jq lets this check read every known compose shape.>" ;;
                empty)
                    nobody="<the container list was read but listed no containers — the stack does not appear to be running; check $COMPOSE_CMD -f $COMPOSE_FILE ps>" ;;
            esac
            log_error "Container health TIMEOUT after ${timeout}s. Not healthy: ${regressed:-$nobody}"
            # Why nothing could be exempt, said once, at the only moment it matters
            # (#1234). Without it a faithful rollback to a pre-#1211 snapshot reads
            # exactly like a rollback that broke something: the operator sees a
            # container named as not healthy and no indication that this check never
            # had anything to compare it against.
            if [ "$baseline_state" = "missing" ]; then
                log_warn "There was no pre-update container baseline at $HEALTH_BASELINE_FILE, so every container above was held to a healthy result rather than only the ones that regressed. On a rollback that is what a snapshot taken before container baselines existed means (#1211) — a container that was ALREADY broken before the update being undone fails this check even when the restore itself was faithful, so check its state before that update before reading this as damage the rollback caused. On an update it means the baseline could not be written; the snapshot step logged why."
            fi
            if [ -n "$preexisting" ]; then
                log_warn "Already not healthy before this update started, so not counted against it: $preexisting"
            fi
            return 1
        fi
        # Clamped to the deadline, so a caller that asks for N seconds waits N — not
        # N plus however much of a poll interval the last sleep overshot by.
        nap=${HEALTH_POLL_INTERVAL:-3}
        if [ "$((timeout - elapsed))" -lt "$nap" ]; then
            nap=$((timeout - elapsed))
        fi
        sleep "$nap"
    done
}

# wait_for_db [<timeout_seconds=60>]
# Polls $RUNTIME exec -i assetatlas-db pg_isready every 2s until ready or timeout.
# Requires: RUNTIME, DB_USER, DB_NAME set by caller.
wait_for_db() {
    local timeout=${1:-60}
    : "${RUNTIME:?wait_for_db: RUNTIME must be set}"
    : "${DB_USER:?wait_for_db: DB_USER must be set}"
    : "${DB_NAME:?wait_for_db: DB_NAME must be set}"
    log_info "Waiting up to ${timeout}s for database to be ready..."
    local start now
    start=$(date +%s)
    while true; do
        if $RUNTIME exec -i assetatlas-db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
            log_ok "Database is ready."
            return 0
        fi
        now=$(date +%s)
        if [ "$((now - start))" -ge "$timeout" ]; then
            log_error "Database TIMEOUT after ${timeout}s."
            return 1
        fi
        sleep 2
    done
}
