#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/common.sh — shared helpers for all installer scripts.
# Sourced, never executed directly.
#
# Exports:
#   log_info, log_warn, log_error, log_debug, die
#   prompt_value, prompt_choice, prompt_yn, prompt_password
#   audit log path: $AUDIT_LOG (default /var/log/assetatlas/install.log, override for tests)

set -uo pipefail

# Colors (skip if not a tty)
if [ -t 1 ]; then
    RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
else
    RED=''; YELLOW=''; GREEN=''; BLUE=''; NC=''
fi


####
# print_banner [version] : the brand banner shown once per install run.
#
# The logo art is PRE-RENDERED by scripts/render-banner.py and pasted in as
# text. It is not rendered at runtime because the installer targets bare
# servers where chafa/figlet/PIL are absent, and a banner is never worth a
# dependency. Re-run that script if the logo changes.
#
# Technique: each cell is U+2580 UPPER HALF BLOCK with the foreground painting
# the top pixel and the background the bottom one, so a row carries two pixels
# of vertical resolution. Escapes are literal `\033[...` text (expanded by
# printf %b) rather than raw ESC bytes, so this file stays diffable.
#
# Four tiers, because a banner must never be what breaks an install. The
# 256-colour tier matters more than it looks: sshd forwards TERM but NOT
# COLORTERM, so a plain SSH session advertises xterm-256color and would
# otherwise never see the mark.
#   truecolor + UTF-8   -> logo, brand hue with shading
#   256 colours + UTF-8 -> logo, flat brand palette
#   UTF-8               -> wordmark in basic ANSI
#   anything else       -> one plain ASCII line (CI logs, dumb terminals)
#
# ASSETATLAS_BANNER_SHOWN=1 suppresses it and is exported once printed, so the
# curl|bash bootstrap can greet the operator immediately without install.sh
# printing a second copy after the handoff.
# _banner_depth : echoes the usable colour depth — 24, 8 or 0.
# Split out from print_banner so the tier choice is testable on its own: the
# rendering path needs a pty, and script(1) cannot run under bats (it wires
# stdio to sockets), so a pty-based test would silently assert nothing.
_banner_depth() {
    case "${COLORTERM:-}" in truecolor|24bit) printf '24\n'; return 0 ;; esac
    # sshd forwards TERM but not COLORTERM, so this is the common remote case.
    case "${TERM:-}" in *256color*) printf '8\n'; return 0 ;; esac
    printf '0\n'
}

####
print_banner() {
    [ "${ASSETATLAS_BANNER_SHOWN:-0}" = "1" ] && return 0
    ASSETATLAS_BANNER_SHOWN=1
    export ASSETATLAS_BANNER_SHOWN

    local ver="${1:-}" line utf8=0 depth
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in *[Uu][Tt][Ff]*) utf8=1 ;; esac
    depth=$(_banner_depth)

    if [ ! -t 1 ] || [ "$utf8" != "1" ]; then
        printf 'AssetAtlas by dotdna - IT asset intelligence%s\n' "${ver:+ (v$ver)}"
        return 0
    fi

    printf '\n'
    if [ "$depth" = "24" ]; then
        while IFS= read -r line; do printf '%b\n' "$line"; done <<'ASSETATLAS_BANNER_TRUECOLOR'
       \033[38;2;172;180;189m\033[48;2;172;180;189m▀▀\033[49m▀\033[0m    \033[0m   \033[38;2;154;161;169m▄▀▄ █▀▀ █▀▀ █▀▀ ▀█▀ \033[0m\033[38;2;107;204;19m▄▀▄\033[0m\033[38;2;154;161;169m ▀█▀ █   ▄▀▄ █▀▀\033[0m
     \033[48;2;172;180;189m▀\033[38;2;172;180;189m▀▀\033[38;2;158;165;173m\033[48;2;134;140;147m▀\033[38;2;134;140;147m\033[48;2;172;180;189m▀\033[39m\033[48;2;95;180;17m▀\033[0m   \033[0m   \033[38;2;154;161;169m█ █ ▀▀█ ▀▀█ █▀▀  █  \033[0m\033[38;2;107;204;19m█ █\033[0m\033[38;2;154;161;169m  █  █   █▀█ ▀▀█\033[0m
    \033[48;2;158;165;174m▀\033[38;2;172;180;189m\033[48;2;172;180;189m▀\033[48;2;170;178;187m▀\033[49m▀▀\033[38;2;102;195;18m\033[48;2;84;160;15m▀\033[38;2;90;171;16m\033[48;2;90;171;16m▀\033[39m\033[48;2;86;164;15m▀\033[0m  \033[0m   \033[38;2;154;161;169m▀ ▀ ▀▀▀ ▀▀▀ ▀▀▀  ▀  \033[0m\033[38;2;107;204;19m▀ ▀\033[0m\033[38;2;154;161;169m  ▀  ▀▀▀ ▀ ▀ ▀▀▀\033[0m
   \033[48;2;122;128;134m▀\033[38;2;145;152;159m\033[48;2;145;152;159m▀\033[38;2;161;168;176m\033[48;2;137;144;151m▀\033[0m  \033[38;2;77;147;14m\033[48;2;77;147;14m▀\033[38;2;78;149;14m▀\033[38;2;77;147;14m▀\033[38;2;80;153;14m▀\033[39m▀\033[0m \033[0m   \033[2mby \033[0m\033[1;37mDOT\033[0m\033[38;2;107;204;19mDNA\033[0m\033[2m   IT asset intelligence\033[0m
  \033[48;2;111;116;122m▀\033[38;2;111;116;122m▀\033[38;2;124;130;136m▀\033[0m  \033[38;2;77;147;14m\033[48;2;77;147;14m▀▀▀▀▀▀\033[39m▀\033[0m
ASSETATLAS_BANNER_TRUECOLOR
    elif [ "$depth" = "8" ]; then
        while IFS= read -r line; do printf '%b\n' "$line"; done <<'ASSETATLAS_BANNER_256'
       \033[38;5;247m\033[48;5;247m▀▀\033[49m▀\033[0m    \033[0m   \033[38;5;247m▄▀▄ █▀▀ █▀▀ █▀▀ ▀█▀ \033[0m\033[38;5;76m▄▀▄\033[0m\033[38;5;247m ▀█▀ █   ▄▀▄ █▀▀\033[0m
     \033[48;5;247m▀\033[38;5;247m▀▀▀▀\033[39m\033[48;5;76m▀\033[0m   \033[0m   \033[38;5;247m█ █ ▀▀█ ▀▀█ █▀▀  █  \033[0m\033[38;5;76m█ █\033[0m\033[38;5;247m  █  █   █▀█ ▀▀█\033[0m
    \033[48;5;247m▀\033[38;5;247m▀▀\033[49m▀▀\033[38;5;76m\033[48;5;76m▀▀\033[39m▀\033[0m  \033[0m   \033[38;5;247m▀ ▀ ▀▀▀ ▀▀▀ ▀▀▀  ▀  \033[0m\033[38;5;76m▀ ▀\033[0m\033[38;5;247m  ▀  ▀▀▀ ▀ ▀ ▀▀▀\033[0m
   \033[48;5;247m▀\033[38;5;247m▀▀\033[0m  \033[38;5;76m\033[48;5;76m▀▀▀▀\033[39m▀\033[0m \033[0m   \033[2mby \033[0m\033[1;37mDOT\033[0m\033[38;5;76mDNA\033[0m\033[2m   IT asset intelligence\033[0m
  \033[48;5;247m▀\033[38;5;247m▀▀\033[0m  \033[38;5;76m\033[48;5;76m▀▀▀▀▀▀\033[39m▀\033[0m
ASSETATLAS_BANNER_256
    else
        # Literal UTF-8 below, not \uXXXX escapes: printf %b does not expand
        # \u, and bash 3.2 has no support for it at all.
        printf '%b\n' "  ▄▀▄ █▀▀ █▀▀ █▀▀ ▀█▀ ${GREEN}▄▀▄${NC} ▀█▀ █   ▄▀▄ █▀▀"
        printf '%b\n' "  █ █ ▀▀█ ▀▀█ █▀▀  █  ${GREEN}█ █${NC}  █  █   █▀█ ▀▀█"
        printf '%b\n' "  ▀ ▀ ▀▀▀ ▀▀▀ ▀▀▀  ▀  ${GREEN}▀ ▀${NC}  ▀  ▀▀▀ ▀ ▀ ▀▀▀"
        printf '%b\n' "  by DOT${GREEN}DNA${NC}   IT asset intelligence"
    fi
    [ -n "$ver" ] && printf '%b\n' "                     ${BLUE}v${ver}${NC}"
    printf '\n'
    return 0
}

# Audit log. The default is uid-derived: what decides where the log CAN be
# written is the euid of THIS process right now. An explicitly-set AUDIT_LOG
# still wins — every bats file sets one.
#
# A non-root run used to land in /tmp via the fallback below, which worked but
# put the install log somewhere a logrotate or a reboot could take it.
# ${HOME:-/tmp}, not bare $HOME: this runs under `set -u`, and a systemd unit
# with no `User=`, `env -i`, or some container users leave HOME unset — bare
# $HOME would abort the installer right here, before the touch fallback below
# ever gets a chance to catch it.
if [ -z "${AUDIT_LOG:-}" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        AUDIT_LOG=/var/log/assetatlas/install.log
    else
        AUDIT_LOG="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/assetatlas/install.log"
    fi
fi
mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
touch "$AUDIT_LOG" 2>/dev/null || AUDIT_LOG=/tmp/assetatlas-install.log

_log() {
    local level=$1; shift
    local ts; ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '[%s] %-5s %s\n' "$ts" "$level" "$*" >> "$AUDIT_LOG"
}

log_info()  { _log INFO  "$@"; printf '%b%s%b\n' "$BLUE"   "$*" "$NC"; }
log_warn()  { _log WARN  "$@"; printf '%b%s%b\n' "$YELLOW" "$*" "$NC" >&2; }
log_error() { _log ERROR "$@"; printf '%b%s%b\n' "$RED"    "$*" "$NC" >&2; }
log_debug() { [ "${DEBUG:-0}" = "1" ] && { _log DEBUG "$@"; printf '%b[DEBUG] %s%b\n' "$BLUE" "$*" "$NC" >&2; }; }
log_ok()    { _log INFO  "$@"; printf '%b%s%b\n' "$GREEN"  "$*" "$NC"; }

die() { _log FATAL "$@"; printf '%bFATAL: %s%b\n' "$RED" "$*" "$NC" >&2; exit 1; }

# prompt_value <prompt> [<default>]
# Returns user input or default. Non-interactive ($UNATTENDED=1) always returns default.
prompt_value() {
    local prompt=$1
    local default=${2:-}
    local reply
    if [ "${UNATTENDED:-0}" = "1" ]; then
        printf '%s\n' "$default"; return
    fi
    read -r -p "$prompt [$default]: " reply
    printf '%s\n' "${reply:-$default}"
}

# prompt_choice <prompt> <default> <choice1> <choice2> ...
prompt_choice() {
    local prompt=$1 default=$2; shift 2
    local choices=("$@") reply
    if [ "${UNATTENDED:-0}" = "1" ]; then
        printf '%s\n' "$default"; return
    fi
    while true; do
        read -r -p "$prompt [${choices[*]}] ($default): " reply
        reply="${reply:-$default}"
        for c in "${choices[@]}"; do
            [ "$reply" = "$c" ] && { printf '%s\n' "$reply"; return; }
        done
        printf 'Invalid choice. Pick one of: %s\n' "${choices[*]}" >&2
    done
}

# prompt_yn <prompt> <default Y|N>
# Returns 0 for yes, 1 for no.
prompt_yn() {
    local prompt=$1 default=${2:-N} reply hint
    case "$default" in
        Y|y) hint="[Y/n]" ;;
        N|n) hint="[y/N]" ;;
        *)   die "prompt_yn: default must be Y or N, got $default" ;;
    esac
    if [ "${UNATTENDED:-0}" = "1" ]; then
        case "$default" in Y|y) return 0 ;; N|n) return 1 ;; esac
    fi
    read -r -p "$prompt $hint " reply
    reply="${reply:-$default}"
    case "$reply" in Y|y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# prompt_password <prompt> [<default>]
# Reads password silently from tty. Behavior:
#   - Unattended mode + default given       -> returns default
#   - Unattended mode + no default          -> returns a freshly generated password
#   - Interactive + user provides input     -> returns input
#   - Interactive + user presses enter +
#       default given                       -> returns default
#       no default                          -> returns a freshly generated password
# Caller is responsible for capturing/storing the returned value.
prompt_password() {
    local prompt=$1 default=${2:-} reply
    if [ "${UNATTENDED:-0}" = "1" ]; then
        printf '%s\n' "${default:-$(generate_password)}"; return
    fi
    read -r -s -p "$prompt: " reply; echo >&2
    printf '%s\n' "${reply:-${default:-$(generate_password)}}"
}

generate_password() {
    openssl rand -base64 32 | tr -d '/+=' | cut -c1-32
}

# verify_artifact_signature <artifact-path>
# Best-effort GPG verification. Returns 0 on:
#   - signature file missing (not a fatal condition; many installs aren't signed)
#   - gpg not installed (with warn)
#   - signature valid (with log_ok)
# Returns 1 ONLY when a signature file IS present AND verification fails.
verify_artifact_signature() {
    local artifact=$1
    local sig="$artifact.asc"
    if [ ! -f "$sig" ]; then
        log_info "No signature file ($sig) — skipping verification."
        return 0
    fi
    if ! command -v gpg >/dev/null 2>&1; then
        log_warn "Signature present but gpg not installed; cannot verify."
        return 0
    fi
    if gpg --verify "$sig" "$artifact" 2>/dev/null; then
        log_ok "GPG signature verified."
        return 0
    fi
    log_error "GPG signature verification FAILED for $artifact"
    return 1
}
