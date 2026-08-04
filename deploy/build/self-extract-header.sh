#!/usr/bin/env bash
# Self-extracting installer header. Prepended to a gzipped tarball.
# Usage: ./assetatlas-online-VERSION.sh [install.sh args]
set -euo pipefail

SCRIPT="$0"
ARCHIVE_LINE=$(awk '/^__ARCHIVE_BELOW__/ {print NR + 1; exit 0;}' "$SCRIPT")

# ---------------------------------------------------------------------------
# Argument pre-scan — resolved HERE, before anything is extracted, because
# this header runs FIRST.
#
# The rootless install mode shipped in v2026.32.1 and was removed again: the
# only supported install is system-wide, under root/sudo. `--rootless` on the
# command line is refused outright (root or not). --install-dir is scanned for
# the same reason: install.sh honours it (and $INSTALL_DIR), so a header that
# hardcoded /opt/assetatlas would extract to one tree while install.sh
# installed into another.
# ---------------------------------------------------------------------------

_prev_arg=""
_install_dir_flag=""
for _arg in "$@"; do
    case "$_arg" in
        --rootless)
            echo "FATAL: the rootless install mode was removed. AssetAtlas now installs system-wide only — re-run as root or under sudo, without --rootless." >&2
            exit 1 ;;
    esac
    # Positional (previous argument), matching install.sh's own pre-parse loop.
    [ "$_prev_arg" = "--install-dir" ] && _install_dir_flag="$_arg"
    _prev_arg="$_arg"
done

if [ -n "$_install_dir_flag" ]; then
    INSTALL_DIR="$_install_dir_flag"
elif [ -z "${INSTALL_DIR:-}" ]; then
    INSTALL_DIR="/opt/assetatlas"
fi

# Announced BEFORE the mkdir, not after: when the mkdir is what fails — the
# permission-denied case above — the operator needs to see which directory was
# refused, and a message printed afterwards never arrives.
echo "Extracting AssetAtlas installer to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
tail -n +"$ARCHIVE_LINE" "$SCRIPT" | tar -xz -C "$INSTALL_DIR"

# Verify SHA256 if manifest present
if [ -f "$INSTALL_DIR/SHA256SUMS" ]; then
    echo "Verifying integrity..."
    (cd "$INSTALL_DIR" && sha256sum -c SHA256SUMS >/dev/null) || { echo "FATAL: integrity check failed."; exit 1; }
fi

# Optional GPG signature verification (best-effort; skip silently if no .asc)
if [ -f "$SCRIPT.asc" ]; then
    if command -v gpg >/dev/null 2>&1; then
        if ! gpg --verify "$SCRIPT.asc" "$SCRIPT" 2>/dev/null; then
            echo "FATAL: GPG signature verification failed for $SCRIPT" >&2
            echo "If you trust this artifact, you can skip with INSTALLER_SKIP_SIGCHECK=1" >&2
            [ "${INSTALLER_SKIP_SIGCHECK:-0}" = "1" ] || exit 1
        fi
    fi
fi

# Extract-only mode: used by `assetatlas update` to refresh the install tree
# (or a temp dir for dry-run) WITHOUT re-triggering the fresh-install handoff.
if [ "${ASSETATLAS_EXTRACT_ONLY:-0}" = "1" ]; then
    echo "Extract-only: skipping symlinks and installer handoff."
    exit 0
fi

# Install both operator commands: `assetatlas` (primary) and the back-compat
# `assetatlas-cli` alias. Both point at the same script. BIN_DIR is overridable
# for tests. The cli expects its parent dir to be $INSTALL_DIR (INSTALL_DIR=$SCRIPT_DIR/..),
# so we MUST target installer/, not any bundle-root copy.
BIN_DIR="${ASSETATLAS_BIN_DIR:-/usr/local/bin}"
# A non-root run may not be able to create or write BIN_DIR; the -w test below
# then skips both links, exactly as it always has.
mkdir -p "$BIN_DIR" 2>/dev/null || true
if [ -f "$INSTALL_DIR/installer/assetatlas-cli" ] && [ -w "$BIN_DIR" ]; then
    ln -sf "$INSTALL_DIR/installer/assetatlas-cli" "$BIN_DIR/assetatlas-cli"
    ln -sf "$INSTALL_DIR/installer/assetatlas-cli" "$BIN_DIR/assetatlas"
fi

# Hand off to installer
exec "$INSTALL_DIR/installer/install.sh" "$@"
__ARCHIVE_BELOW__
