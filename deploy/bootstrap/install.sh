#!/usr/bin/env bash
# deploy/bootstrap/install.sh — pipeable one-line bootstrap for AssetAtlas (online).
#   curl -fsSL https://get.assetatlas.com | sudo bash [-s -- <install.sh args>]
#
# Downloads + sha256-verifies the online installer bundle, then execs it with
# stdin bound to the terminal so the interactive prompts survive the pipe.
# ALL logic lives in main(), called on the LAST line — a truncated download
# cannot execute a partial script.
set -euo pipefail

BASE_URL="${ASSETATLAS_INSTALL_BASE_URL:-https://get.assetatlas.com}"
MANIFEST_URL="${ASSETATLAS_MANIFEST_URL:-${BASE_URL}/latest.json}"
BUNDLE_BASE="${ASSETATLAS_BUNDLE_BASE:-${BASE_URL}/dl}"

_err()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
_have() { command -v "$1" >/dev/null 2>&1; }
_sha256() { if _have sha256sum; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }
_json_field() {
    printf '%s' "$1" | grep -Eo "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
        | head -1 | sed -E "s/.*:[[:space:]]*\"([^\"]*)\"/\1/" || true
}


# Brand banner. Duplicated from deploy/lib/common.sh because this script is
# curl'd on its own and can source nothing — the same reason _json_field and
# _sha256 are duplicated here. Regenerate both with scripts/render-banner.py.
# Exporting ASSETATLAS_BANNER_SHOWN stops install.sh printing a second copy
# after the handoff. The 256-colour tier is the one most operators hit: sshd
# forwards TERM but not COLORTERM.
_banner() {
    local ver="${1:-}" line utf8=0 depth=0
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in *[Uu][Tt][Ff]*) utf8=1 ;; esac
    case "${COLORTERM:-}" in truecolor|24bit) depth=24 ;; esac
    [ "$depth" = "0" ] && case "${TERM:-}" in *256color*) depth=8 ;; esac
    ASSETATLAS_BANNER_SHOWN=1; export ASSETATLAS_BANNER_SHOWN
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
        printf '%b\n' "  ▄▀▄ █▀▀ █▀▀ █▀▀ ▀█▀ \033[0;32m▄▀▄\033[0m ▀█▀ █   ▄▀▄ █▀▀"
        printf '%b\n' "  █ █ ▀▀█ ▀▀█ █▀▀  █  \033[0;32m█ █\033[0m  █  █   █▀█ ▀▀█"
        printf '%b\n' "  ▀ ▀ ▀▀▀ ▀▀▀ ▀▀▀  ▀  \033[0;32m▀ ▀\033[0m  ▀  ▀▀▀ ▀ ▀ ▀▀▀"
        printf '%b\n' "  by DOT\033[0;32mDNA\033[0m   IT asset intelligence"
    fi
    [ -n "$ver" ] && printf '                     \033[0;34mv%s\033[0m\n' "$ver"
    printf '\n'
    return 0
}

# _have_sudo : its own function, and env-overridable, because a bats run cannot
# take the real /usr/bin/sudo off PATH — `command -v sudo` would find it whatever
# the stub directory contains. Same reason runtime.sh factors out
# runtime_have_tty: bats stdin is never a terminal.
_have_sudo() {
    [ "${ASSETATLAS_ASSUME_NO_SUDO:-0}" = "1" ] && return 1
    command -v sudo >/dev/null 2>&1
}

# _resolve_mode : system-wide is the only install mode. A non-root run either
# escalates through sudo or stops with a clear message — there is no rootless
# fallback and no interactive system-vs-rootless prompt.
#
# Returns only for an already-root run. Deliberately NOT an echo-and-capture
# function: the escalation branch execs, and an exec inside `$( … )` replaces
# the SUBSHELL, not this process — sudo would run to completion, the parent
# would carry on with sudo's output in a variable, and the install would
# continue as the unprivileged user having "escalated". Call it bare, never as
# `x=$(_resolve_mode …)`.
_resolve_mode() {
    # The rootless install mode shipped in v2026.32.1 and was removed again.
    # Refuse the flag everywhere — root or not, before anything else — rather
    # than silently installing system-wide under a flag that promises something
    # else.
    local a
    for a in "$@"; do
        [ "$a" = "--rootless" ] && _err \
"The rootless install mode was removed. AssetAtlas now installs system-wide only.
  Re-run as root or under sudo, without --rootless:
  curl -fsSL $BASE_URL | sudo bash"
    done

    [ "$(id -u)" -eq 0 ] && return 0

    # No usable sudo and no other route to a system-wide install: stop here,
    # naming the remedy, instead of falling back to anything unprivileged.
    _have_sudo || _err \
"AssetAtlas installs system-wide only, and this run is neither root nor able to use sudo.
  Re-run as root or under sudo:
  curl -fsSL $BASE_URL | sudo bash"

    # Non-root with sudo: escalate automatically. --unattended needs no explicit
    # mode flag — there is only one mode left to choose.
    _escalate "$@"
}

# _escalate : re-run the ONE-LINER under sudo. Does not return.
#
# Deliberately not `sudo bash "$downloaded_bundle"`: the bundle lands in a
# mktemp dir owned by the invoking user, so sudo-exec'ing it has root run a file
# that user can swap between the sha256 verification and the exec. Re-running the
# one-liner costs a second ~4 KB transfer of THIS script and puts the whole
# verified flow — manifest, download, checksum, handoff — on the root side.
#
# Deliberately NOT forwarding ASSETATLAS_BANNER_SHOWN=1 (which would silence the
# second _banner call below and let this run print the logo only once): a
# `VAR=value` argument on a sudo command line is only accepted when the matched
# sudoers Cmnd_Spec is ALL (which implies SETENV) — under a restricted Cmnd_Spec
# sudo refuses the WHOLE command with "sorry, you are not allowed to set the
# following environment variables", and a restricted-sudoers operator is exactly
# who escalate-before-download exists to serve. A duplicated banner is cosmetic
# — and arguably correct, since it now marks a genuine change of privilege
# context — while a hard failure for that operator is not an acceptable trade.
#
# The three ASSETATLAS_* URL overrides ARE carried across, and have to be: sudo's
# env_reset drops them, so the re-run would fetch THIS script from the staging
# mirror named on the command line and then resolve latest.json and the bundle
# against the production default — a staging install that silently pulls the
# production artifact. They are exported inside the `bash -c` body rather than
# written as `VAR=value` arguments to sudo, for the reason in the paragraph
# above: the latter form is refused outright under a restricted Cmnd_Spec.
_escalate() {
    printf 'Escalating with sudo for a system-wide install...\n' >&2
    exec sudo bash -c \
        'export ASSETATLAS_INSTALL_BASE_URL="$1" ASSETATLAS_MANIFEST_URL="$2" ASSETATLAS_BUNDLE_BASE="$3"
         curl -fsSL "$1" | bash -s -- "${@:4}"' \
        _ "$BASE_URL" "$MANIFEST_URL" "$BUNDLE_BASE" "$@"
}

main() {
    _banner
    [ "$(uname -s)" = "Linux" ] || _err "The online installer supports Linux only."
    _have curl || _err "curl is required."
    _have tar  || _err "tar is required."
    { _have sha256sum || _have shasum; } || _err "sha256sum or shasum is required."

    # Mode is decided before the download proper (the manifest/bundle curl calls
    # below) — an escalating run must not transfer the bundle as one user and
    # execute it as another. Deferred until after the four LOCAL checks above:
    # they are cheap, network-free, and true regardless of privilege, so putting
    # them first still satisfies "before the download" while catching a
    # non-Linux host or a missing tool BEFORE a sudo prompt and a second full
    # transfer. Missing curl specifically matters here — _have curl is what
    # stands between a curl-less host and _escalate's re-run of
    # `curl -fsSL "$1" | bash -s -- …`, which would otherwise feed `bash -s` an
    # empty stdin, exit 0, and let the whole bootstrap report success having
    # installed nothing: silent success is the worst failure mode this repo has.
    # Called BARE — see the subshell note on _resolve_mode; a command
    # substitution here would swallow the exec and silently continue
    # unprivileged.
    _resolve_mode "$@"

    local manifest version expected
    manifest=$(curl -fsSL --max-time 15 "$MANIFEST_URL") || _err "Could not fetch $MANIFEST_URL"
    version=$(_json_field "$manifest" version)
    [ -n "$version" ] || _err "Manifest has no version: $MANIFEST_URL"
    expected=$(_json_field "$manifest" online_sha256)

    local tmp name url out
    tmp=$(mktemp -d)
    # Bake the path in (double quotes) rather than deferring expansion: `tmp` is
    # local to main(), and the EXIT trap fires after that scope is unwound — so a
    # single-quoted '$tmp' resolved to nothing, tripped `set -u`, and printed
    # "tmp: unbound variable" AFTER the real error while leaking the temp dir.
    # Only ever visible on a failure path, i.e. when the operator is already
    # trying to read an error message.
    # shellcheck disable=SC2064  # expanding now is the fix, not the bug
    trap "rm -rf -- '$tmp'" EXIT
    name="assetatlas-online-${version}.sh"
    url="${BUNDLE_BASE}/${name}"
    out="${tmp}/${name}"
    printf 'Downloading AssetAtlas %s ...\n' "$version"
    curl -fsSL --max-time 300 -o "$out" "$url" || _err "Download failed: $url"

    if [ -z "$expected" ]; then
        expected=$(curl -fsSL --max-time 30 "${url}.sha256" 2>/dev/null | awk '{print $1}' | head -1) || true
    fi
    [ -n "$expected" ] || _err "No sha256 available to verify $name."
    local actual; actual=$(_sha256 "$out")
    [ "$actual" = "$expected" ] || _err "sha256 mismatch for $name (expected $expected, got $actual)."
    printf 'Verified %s.\n' "$name"

    # Hand off with stdin on the terminal so the installer's prompts work.
    # Run it as a CHILD rather than exec'ing: `exec` replaces this process, which
    # skips the EXIT trap above and strands the downloaded bundle in $tmp. The
    # `</dev/tty` redirect is what makes the prompts work — not the exec — and a
    # child inherits it identically, so cleanup (including on Ctrl-C, which hits
    # the whole process group) costs nothing.
    if [ -e /dev/tty ] && (exec </dev/tty) 2>/dev/null; then
        bash "$out" "$@" </dev/tty
        exit $?
    fi
    local a
    for a in "$@"; do
        if [ "$a" = "--unattended" ]; then
            bash "$out" "$@"
            exit $?
        fi
    done
    # Reached only as root: _resolve_mode has already ended or escalated every
    # non-root run by this point.
    _err "No terminal for interactive prompts. Re-run attached to a terminal, or pass --unattended:
  curl -fsSL $BASE_URL | sudo bash -s -- --unattended"
}

main "$@"
