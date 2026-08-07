#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/version.sh — version parsing and system_info CRUD.
# Requires: deploy/lib/common.sh sourced; PSQL_CMD set if you want DB ops.

# parse_version <YYYY.WW.N> -> sets VERSION_YEAR VERSION_WEEK VERSION_PATCH
parse_version() {
    local v=$1
    [[ "$v" =~ ^([0-9]{4})\.([0-9]{1,2})\.([0-9]+)$ ]] || { log_error "Bad version: $v"; return 1; }
    VERSION_YEAR="${BASH_REMATCH[1]}"
    VERSION_WEEK="${BASH_REMATCH[2]}"
    VERSION_PATCH="${BASH_REMATCH[3]}"
}

# is_linear_jump <from-version> <to-version>
# Returns 0 if:
#   - same year, same week (different patch), or
#   - same year, next week (week N+1), or
#   - next year, week 1 (year-rollover; ISO weeks 53 → 1 is also accepted via this rule)
# Note: ISO 8601 years can have 52 or 53 weeks. The year-rollover rule always
# accepts (year_N, week_max_for_N) → (year_N+1, week_1) regardless of whether
# year N had a 53rd week.
is_linear_jump() {
    local from=$1 to=$2
    parse_version "$from" || return 1
    local fy=$VERSION_YEAR fw=$VERSION_WEEK
    parse_version "$to" || return 1
    local ty=$VERSION_YEAR tw=$VERSION_WEEK
    if [ "$fy" = "$ty" ] && [ "$tw" -ge "$fw" ] && [ "$((10#$tw - 10#$fw))" -le 1 ]; then
        return 0
    fi
    if [ "$ty" = "$((fy + 1))" ] && [ "$tw" = "1" ]; then
        return 0
    fi
    return 1
}

# get_installed_version : queries system_info, prints version or "0.0.0-pre-system-info"
get_installed_version() {
    [ -z "${PSQL_CMD:-}" ] && { log_error "PSQL_CMD not set"; return 1; }
    $PSQL_CMD -At -c "SELECT installed_version FROM system_info WHERE id = 1;" 2>/dev/null || echo "0.0.0-pre-system-info"
}

# update_system_info <version> <runtime> <install_mode> <install_dir>
update_system_info() {
    local version=$1 runtime=$2 mode=$3 install_dir=$4
    # Redirected: psql's "INSERT 0 1" command tag is stdout noise in the
    # installer log, which prints its own confirmation. No caller reads it.
    #
    # ON_ERROR_STOP=1: on stdin psql exits 0 for a statement that failed, so
    # without it this function returned success for an upsert that never
    # happened and the recorded version silently stayed behind the deployed
    # code. Deliberately no die here — the two callers want different things
    # and each says so at its own call site (install.sh dies, update.sh warns).
    $PSQL_CMD -v ON_ERROR_STOP=1 \
        -v version="$version" \
        -v runtime="$runtime" \
        -v mode="$mode" \
        -v install_dir="$install_dir" <<'SQL' >/dev/null
INSERT INTO system_info (id, installed_version, runtime, install_mode, install_dir, last_updated_at)
VALUES (1, :'version', :'runtime', :'mode', :'install_dir', NOW())
ON CONFLICT (id) DO UPDATE
  SET installed_version = EXCLUDED.installed_version,
      runtime           = EXCLUDED.runtime,
      install_mode      = EXCLUDED.install_mode,
      install_dir       = EXCLUDED.install_dir,
      last_updated_at   = NOW();
SQL
}

# set_opt_in <field> <true|false>
set_opt_in() {
    local field=$1 value=$2
    case "$field" in opt_in_monitoring) ;; *) die "Bad opt-in field: $field" ;; esac
    case "$value" in true|false) ;; *) die "set_opt_in value must be true or false, got: $value" ;; esac
    # field and value are both allowlisted; safe to interpolate.
    # Redirected for the same reason as update_system_info: psql's "UPDATE 1"
    # command tag is stdout noise in the installer log; no caller reads it.
    $PSQL_CMD -c "UPDATE system_info SET $field = $value WHERE id = 1;" >/dev/null
}

# set_app_image_pins <version> [env_file] [--create-missing]
# Rewrite the tag of the app-image pins in the .env to :<version>, so an online
# update deploys exactly the bundle's version instead of whatever the operator
# last hand-edited. Only the five images that track the app version are touched;
# vendor pins (DB/Redis/Nginx/PgAdmin) carry upstream versions and are left
# alone. Only the tag after the LAST ':' is replaced, so a registry-with-port
# ref (host:5000/repo:tag) is preserved. A digest-pinned ref (repo@sha256:...)
# is deliberately NOT touched — excluding '@' from the repo group means the
# regex won't match it, so we never turn a digest into an invalid :tag.
# Idempotent. Root cause of #812: the deployed images (.env pins) and the
# recorded version drifted independently.
#
# A pin line that is simply ABSENT used to be skipped silently: sed with zero
# matches exits 0, so an .env with no *_IMAGE lines at all — every pre-#1391
# online install, whose write_env_file emits none — "pinned" nothing, returned
# 0 under update.sh's `|| die` guard, and the host kept running :latest. Two
# guards against that shape now:
#   - if NONE of the five lines exists and none was created, the run fails
#     loudly instead of reporting success for having changed nothing;
#   - --create-missing appends the absent lines as the compose-default
#     ghcr.io/dotdna-io refs at <version>, so an already-deployed online host
#     self-heals on its next update. Each created line is logged.
# --create-missing is an OPT-IN, not the default, on purpose: it is the only
# path that writes a ghcr.io ref, and an air-gapped install pins localhost/*
# and cannot reach a registry. The sole production caller that passes it is
# update.sh's online branch (IMAGE_SOURCE=online); an offline .env always
# carries all five localhost/* lines (install.sh's offline branch writes
# them), so the create path is unreachable there regardless. A PARTIAL .env
# (some lines present) without the flag rewrites what exists and warns —
# pre-#1391 installs never had a subset, and update.sh always passes the flag.
set_app_image_pins() {
    local version=$1
    local env_file="${2:-$INSTALL_DIR/.env}"
    local create_missing=${3:-}
    [ -n "$version" ] || { log_error "set_app_image_pins: version required"; return 1; }
    [ -f "$env_file" ] || { log_error "set_app_image_pins: env file not found: $env_file"; return 1; }
    case "$create_missing" in
        ""|--create-missing) ;;
        *) log_error "set_app_image_pins: unknown argument: $create_missing"; return 1 ;;
    esac
    local vars=(API_IMAGE SCHEDULER_IMAGE MCP_IMAGE UI_IMAGE TEAMS_BRIDGE_IMAGE)
    local var args=()
    for var in "${vars[@]}"; do
        args+=(-e "s|^(${var}=[^@[:space:]]*):[^:[:space:]]+\$|\1:${version}|")
    done
    local tmp
    tmp="$(mktemp)"
    sed -E "${args[@]}" "$env_file" > "$tmp" && mv "$tmp" "$env_file" || {
        rm -f "$tmp"
        log_error "set_app_image_pins: failed to rewrite $env_file"
        return 1
    }

    # Presence, not rewrite count: a digest-pinned line is deliberately left
    # unrewritten but still counts as pinned. grep reads the FILE here — the
    # pipefail/EPIPE trap (#1199) is `writer | grep -q`, not this shape.
    local missing=()
    for var in "${vars[@]}"; do
        grep -qE "^${var}=" "$env_file" || missing+=("$var")
    done
    [ "${#missing[@]}" -eq 0 ] && return 0

    if [ "$create_missing" = "--create-missing" ]; then
        # Repo paths mirror the ${VAR:-default}s in deploy/compose/docker-compose.yml.
        local repo
        for var in "${missing[@]}"; do
            case "$var" in
                API_IMAGE)           repo=ghcr.io/dotdna-io/assetatlas-api ;;
                SCHEDULER_IMAGE)     repo=ghcr.io/dotdna-io/assetatlas-scheduler ;;
                MCP_IMAGE)           repo=ghcr.io/dotdna-io/assetatlas-mcp ;;
                UI_IMAGE)            repo=ghcr.io/dotdna-io/assetatlas-ui ;;
                TEAMS_BRIDGE_IMAGE)  repo=ghcr.io/dotdna-io/assetatlas-teams-bridge ;;
            esac
            printf '%s=%s:%s\n' "$var" "$repo" "$version" >> "$env_file"
            log_info "  created missing pin $var=$repo:$version"
        done
        return 0
    fi

    if [ "${#missing[@]}" -eq "${#vars[@]}" ]; then
        log_error "set_app_image_pins: $env_file has none of the five app-image pins — nothing was pinned. On an ONLINE install, re-run with --create-missing to write the ghcr.io/dotdna-io refs (update.sh's online branch does). Never create them on an offline install: it pins localhost/* and cannot reach a registry."
        return 1
    fi
    log_warn "set_app_image_pins: no pin line for ${missing[*]} in $env_file — rewrote the pins that exist, created nothing (no --create-missing)."
}

# set_app_image_owner [old-owner] [new-owner] [env_file]
# Rewrite the ghcr.io OWNER segment of the five app-image pins. Needed once, for the
# 2026-07 move of this project from the personal account `andrei-dotdna` to the
# `dotdna-io` org: ghcr packages do NOT follow a repository transfer and there is no
# registry redirect, so an .env left on the old owner pulls a tag that was never
# published there and the update 404s. set_app_image_pins() above rewrites only the
# tag and deliberately preserves the repo path, so nothing else repairs this.
#
# ONLINE installs only — and that is the whole scope. Air-gapped installs pin
# localhost/* (install.sh offline branch; build-offline.sh saves localhost/<img>:latest)
# and never consult a registry, which is why they are untouched by the namespace move.
# Only `ghcr.io/<old-owner>/` prefixed values are rewritten, so offline pins,
# private-registry refs (registry.local:5000/...), vendor images and any third-party
# ghcr owner are all left alone.
#
# Unlike set_app_image_pins there is deliberately NO digest carve-out: that function
# skips `repo@sha256:...` because rewriting a *tag* there would produce the invalid
# `@sha256:<version>`. Swapping the *owner* of a digest ref stays valid, and the
# namespace was mirrored by manifest copy so the digest still resolves. Idempotent.
set_app_image_owner() {
    local old_owner="${1:-andrei-dotdna}"
    local new_owner="${2:-dotdna-io}"
    local env_file="${3:-$INSTALL_DIR/.env}"
    [ -f "$env_file" ] || { log_error "set_app_image_owner: env file not found: $env_file"; return 1; }
    local var args=()
    for var in API_IMAGE SCHEDULER_IMAGE MCP_IMAGE UI_IMAGE TEAMS_BRIDGE_IMAGE; do
        args+=(-e "s|^(${var}=)ghcr\.io/${old_owner}/|\1ghcr.io/${new_owner}/|")
    done
    local tmp
    tmp="$(mktemp)"
    sed -E "${args[@]}" "$env_file" > "$tmp" && mv "$tmp" "$env_file"
}

# verify_deployed_version <version> <health_url> [<timeout_seconds=60>]
# Confirm the RUNNING app reports <version> before we record it in system_info.
# Polls the app's own liveness endpoint (/api/health/live), whose
# application.version is the image-baked APP_VERSION (release.yml build-arg) —
# so this verifies the actually-deployed CODE, not just the .env tag we wrote
# (which would be tautological). Online mode only — offline images aren't
# APP_VERSION-stamped. Requires curl.
#
# It POLLS like every other probe in the update (wait_for_health runs the same
# 60s at 2s cadence): this fires against a freshly recreated api container, and
# the old single 15s shot read one slow first answer as a failed deployment
# (#1389). The window is a parameter with a default so a test does not have to
# sit through the real cadence.
#
# Deliberately no curl -f: the classification reads the BODY, never the HTTP
# status. The health window already established liveness, so this check's only
# question is which code is deployed — and a degraded-but-answering app that
# reports the right version still counts as confirmed (/api/health's 503 on a
# slow dependency check is what made the old probe lie about a healthy update).
#
# THREE outcomes, one exit status each, so the caller can act differently:
#   0 confirmed        — a body carried "version": "<version>".
#   1 wrong version    — a body carried a recognisable version field whose value
#                        is NOT <version>: the genuine deployment failure (#812,
#                        a pull/recreate that did not take effect). Stays FATAL
#                        at the call site.
#   2 could not verify — no answer at all within the window, or every answer
#                        carried no recognisable version field (an nginx 404/502
#                        HTML page must land HERE, not in "wrong version" —
#                        misreading it would FATAL a healthy deployment, which
#                        is exactly #1389). NOT fatal at the call site, but
#                        system_info is still never written: only 0 may lead to
#                        update_system_info (#812's fail-closed invariant).
verify_deployed_version() {
    local version=$1 url=$2
    local timeout=${3:-60}
    [ -n "$version" ] || { log_error "verify_deployed_version: version required"; return 1; }
    [ -n "$url" ]     || { log_error "verify_deployed_version: health url required"; return 1; }
    # Escape dots so the version matches literally (2026.27.1 must not match
    # 2026x27x1); the closing quote anchors it so 2026.27.1 != 2026.27.11.
    local vpat=${version//./\\.}
    # Any "version": "<value>" field, for the wrong-version branch's message.
    # Unquoted-variable regex on purpose: bash 3.2 mis-parses quoted sub-regexes.
    local re='"version"[[:space:]]*:[[:space:]]*"([^"]+)"'
    local start now body
    start=$(date +%s)
    while true; do
        body=$(curl -sSk --max-time 5 "$url" 2>/dev/null) || body=""
        if [ -n "$body" ]; then
            # Herestring, not a pipe: grep -q exits on its match, so a piped
            # writer takes EPIPE and pipefail turns a MATCH into a failed
            # pipeline (#1199) — which would invert this fail-closed check into
            # a false "not the target version". Whether this check answers
            # correctly must not depend on scheduling.
            if grep -Eq "\"version\"[[:space:]]*:[[:space:]]*\"${vpat}\"" <<<"$body"; then
                log_ok "  confirmed: the running app reports $version"
                return 0
            fi
            if [[ "$body" =~ $re ]]; then
                log_error "  running app reports version ${BASH_REMATCH[1]} — a different version than the target $version"
                return 1
            fi
        fi
        now=$(date +%s)
        if [ "$((now - start))" -ge "$timeout" ]; then
            log_error "  could not verify the deployed version at $url within ${timeout}s (no answer carrying a recognisable version field)"
            return 2
        fi
        sleep 2
    done
}
