#!/usr/bin/env bash
# deploy/build/build-online.sh — produces assetatlas-online-<VERSION>.sh
# Slim self-extracting installer (~10-20 MB). App images pulled from registries at install time.
# Bundles: installer scripts, lib, compose, migrations, seeds, systemd unit, VERSION, SHA256SUMS.
#
# Usage: build-online.sh [output-path]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERSION="$(cat "$REPO_ROOT/VERSION")"
OUTPUT="${1:-$REPO_ROOT/dist/assetatlas-online-$VERSION.sh}"
mkdir -p "$(dirname "$OUTPUT")"

# Suppress macOS extended-attribute (._*) AppleDouble files when building on macOS.
# Without these, the bundle ships with junk like ._VERSION, ._compose, etc., which
# pollute /opt/assetatlas and cause tar warnings on Linux.
export COPYFILE_DISABLE=1

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "Assembling online bundle ($VERSION) ..."

# Layout inside the bundle. `database/` is referenced as a relative bind mount
# by docker-compose.yml, so it MUST live next to the compose file at
# $INSTALL_DIR after extraction. The nginx conf is ALSO bind-mounted, but ships
# staged under compose/nginx/ and is promoted to docker/nginx/ by install.sh /
# refresh_compose_files — see the copy further down.
mkdir -p "$WORK/installer" "$WORK/lib" "$WORK/compose" \
         "$WORK/database/migrations" \
         "$WORK/database/demo" \
         "$WORK/systemd" "$WORK/monitoring"
cp -r "$REPO_ROOT/deploy/installer/"*       "$WORK/installer/"
cp -r "$REPO_ROOT/deploy/lib/"*             "$WORK/lib/"
cp -r "$REPO_ROOT/deploy/compose/"*         "$WORK/compose/"
# Migrations and seeds live under database/ to match install.sh's
# MIGRATIONS_DIR=$INSTALL_DIR/database/migrations expectation. Compose also
# bind-mounts ./database/schema-full.sql from the same root.
cp -r "$REPO_ROOT/src/database/migrations/." "$WORK/database/migrations/"
[ -d "$REPO_ROOT/database/demo" ]  && cp -r "$REPO_ROOT/database/demo/."  "$WORK/database/demo/"  2>/dev/null || true

# pg_dump 17+ emits artifacts that PG 16 servers and clients reject:
#   - `\restrict <tok>` / `\unrestrict <tok>` meta-commands (psql 17+ only)
#   - `SET transaction_timeout = 0;` (server GUC added in PG 17)
# Strip both so dumps generated against PG 17 still load against PG 16.
shopt -s nullglob
for sql in "$WORK/database/migrations"/*.sql; do
    sed -i.bak \
        -E -e '/^\\(un)?restrict([[:space:]]|$)/d' \
        -e '/^SET transaction_timeout =/d' \
        "$sql"
    rm -f "$sql.bak"
done
shopt -u nullglob
[ -d "$REPO_ROOT/deploy/systemd" ] && cp -r "$REPO_ROOT/deploy/systemd/." "$WORK/systemd/" 2>/dev/null || true
cp "$REPO_ROOT/VERSION" "$WORK/"

# Bootstrap schema + nginx config — referenced by docker-compose.yml as relative
# bind mounts (./database/schema-full.sql, ./docker/nginx/...). Must ship.
# src/database/schema-full.sql is the canonical bootstrap snapshot.
SCHEMA_SRC="$REPO_ROOT/src/database/schema-full.sql"
[ -f "$SCHEMA_SRC" ] || { echo "ERROR: $SCHEMA_SRC not found" >&2; exit 1; }
cp "$SCHEMA_SRC" "$WORK/database/schema-full.sql"

# Defensive patch: pg_dump emits `SET search_path = ''` then unqualified
# `create_hypertable('asset_chronicle', 'time', ...)` which fails because the
# timescaledb extension lives in `public`, not in the empty search_path.
# Re-qualifying here makes regenerations of schema-full.sql via pg_dump ship
# without manual fixup. Only patches the known-bad form (idempotent — re-runs
# on already-qualified files are no-ops).
sed -i.bak \
    -e "s|^SELECT create_hypertable('asset_chronicle'|SELECT public.create_hypertable('public.asset_chronicle'|" \
    "$WORK/database/schema-full.sql"
rm -f "$WORK/database/schema-full.sql.bak"
[ -d "$REPO_ROOT/docker/nginx" ] || \
    { echo "ERROR: docker/nginx not found in repo" >&2; exit 1; }
# STAGED under compose/, not shipped live to docker/nginx/. Extraction writes
# straight into $INSTALL_DIR, so a live path would overwrite the running
# install's conf before the matching compose file is in place — and the conf is
# only valid under the compose that mounts it as an envsubst template.
# install.sh and refresh_compose_files promote it from here.
mkdir -p "$WORK/compose/nginx"
cp -r "$REPO_ROOT/docker/nginx/." "$WORK/compose/nginx/"

# Monitoring overlay configs — bind-mounted by docker-compose.monitoring.yml as
# ./monitoring/{prometheus.yml,alerts,alertmanager.yml}. Required when the
# operator runs install with --with-monitoring (or assetatlas-cli enable monitoring).
[ -d "$REPO_ROOT/monitoring" ] || \
    { echo "ERROR: monitoring/ not found in repo" >&2; exit 1; }
cp -r "$REPO_ROOT/monitoring/." "$WORK/monitoring/"

# assetatlas-cli is shipped under installer/ (already copied via the
# `installer/*` glob above). Self-extract-header symlinks to that copy.
# We do NOT also copy it to the bundle root — the cli derives INSTALL_DIR
# from `dirname/..` so it must live at $INSTALL_DIR/installer/.

# Mark online mode for the installer to detect
echo "online" > "$WORK/INSTALL_MODE"

# Strip macOS junk before manifest computation so manifest matches tar contents.
# (xattrs prevent tar warnings on Linux; ._* / .DS_Store would survive cp from
# any filesystem that already had them.)
if command -v xattr >/dev/null 2>&1; then
    xattr -cr "$WORK" 2>/dev/null || true
fi
find "$WORK" \( -name '._*' -o -name '.DS_Store' \) -delete 2>/dev/null || true

# SHA256 manifest (excludes itself)
( cd "$WORK" && find . -type f ! -name SHA256SUMS -print0 \
    | xargs -0 shasum -a 256 2>/dev/null \
    | awk '{print $1"  "$2}' > SHA256SUMS ) || \
( cd "$WORK" && find . -type f ! -name SHA256SUMS -print0 \
    | xargs -0 sha256sum > SHA256SUMS )

# Tar + prepend self-extract header.
# Important: write the payload tar OUTSIDE $WORK. If we write it inside the dir
# we're tarring, ext4 detects "file changed as we read it" and tar fails (macOS
# doesn't, which is why this only surfaced in CI).
PAYLOAD=$(mktemp -t aa-payload.XXXXXX.tar.gz 2>/dev/null || mktemp /tmp/aa-payload.XXXXXX.tar.gz)
trap 'rm -rf "$WORK" "$PAYLOAD"' EXIT INT TERM
# --no-xattrs: bsdtar on macOS otherwise embeds xattrs as PAX headers
# (LIBARCHIVE.xattr.*) which Linux GNU tar warns about on extract.
# Fall back gracefully on tar implementations that don't accept the flag.
if tar --no-xattrs -czf /dev/null -C "$WORK" . 2>/dev/null; then
    tar --no-xattrs -czf "$PAYLOAD" -C "$WORK" .
else
    tar -czf "$PAYLOAD" -C "$WORK" .
fi

cat "$SCRIPT_DIR/self-extract-header.sh" "$PAYLOAD" > "$OUTPUT"
chmod +x "$OUTPUT"

# Optional GPG signing (set GPG_KEY_ID + GPG_PASSPHRASE to enable)
if [ -n "${GPG_KEY_ID:-}" ]; then
    echo "Signing $(basename "$OUTPUT") with GPG key $GPG_KEY_ID..."
    if [ -n "${GPG_PASSPHRASE:-}" ]; then
        echo "$GPG_PASSPHRASE" | gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 \
            --detach-sign --armor -u "$GPG_KEY_ID" "$OUTPUT" \
            || { echo "GPG signing failed" >&2; exit 1; }
    else
        gpg --batch --yes --detach-sign --armor -u "$GPG_KEY_ID" "$OUTPUT" \
            || { echo "GPG signing failed" >&2; exit 1; }
    fi
    echo "Signed: $OUTPUT.asc"
fi

size=$(du -h "$OUTPUT" | cut -f1)
echo "Built: $OUTPUT ($size)"
