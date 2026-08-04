#!/usr/bin/env bash
# deploy/build/build-offline.sh — produces assetatlas-offline-<VERSION>.tar.gz
# Bundles all required vendor + app images as docker tarballs.
# Customer extracts and runs ./installer/install.sh (no self-extract header).
#
# Usage: build-offline.sh [output-path]
# Env vars:
#   SKIP_IMAGE_BUILD=1  — skip docker build steps (for quick testing)
#   SKIP_VENDOR_PULL=1  — skip docker pull of vendor images (assumes they exist locally)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERSION="$(cat "$REPO_ROOT/VERSION")"
OUTPUT="${1:-$REPO_ROOT/dist/assetatlas-offline-$VERSION.tar.gz}"
mkdir -p "$(dirname "$OUTPUT")"

# Suppress macOS extended-attribute (._*) AppleDouble files when building on macOS.
export COPYFILE_DISABLE=1

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "Assembling offline bundle ($VERSION) ..."

# Same scaffolding as build-online (extracted into a shared function would be nice but YAGNI).
# database/ is a relative bind mount from docker-compose.yml and MUST sit next
# to the compose file at $INSTALL_DIR. The nginx conf ships staged under
# compose/nginx/ and is promoted later — see build-online.sh.
mkdir -p "$WORK/installer" "$WORK/lib" "$WORK/compose" \
         "$WORK/database/migrations" \
         "$WORK/database/demo" \
         "$WORK/systemd" "$WORK/images" "$WORK/monitoring" \
         "$WORK/reference"
cp -r "$REPO_ROOT/deploy/installer/"*        "$WORK/installer/"
cp -r "$REPO_ROOT/deploy/lib/"*              "$WORK/lib/"
cp -r "$REPO_ROOT/deploy/compose/"*          "$WORK/compose/"
cp -r "$REPO_ROOT/src/database/migrations/." "$WORK/database/migrations/"
[ -d "$REPO_ROOT/database/demo" ]  && cp -r "$REPO_ROOT/database/demo/."  "$WORK/database/demo/"  2>/dev/null || true

# Reference data (mac_vendor_ranges, os_eol_data). An air-gapped host cannot
# reach the bundle repo, so without this it installs with ZERO mac_vendor_ranges
# rows and enrichment silently under-resolves until someone hand-uploads a bundle
# — on the deployment where that is hardest to notice. ~2.5 MB on a ~2.6 GB
# artifact.
#
# Non-fatal: a build machine without gh/network still produces a valid bundle,
# it just ships without reference data (the installer then says so explicitly
# rather than pretending). REFERENCE_BUNDLE_REPO overrides the source for forks.
_ref_repo="${REFERENCE_BUNDLE_REPO:-dotdna-io/assetatlas-reference-bundles}"
if command -v gh >/dev/null 2>&1; then
    echo "Fetching latest reference-data bundle from $_ref_repo ..."
    if gh release download --repo "$_ref_repo" \
         --pattern 'reference-bundle-*.tar.gz' --dir "$WORK/reference" 2>/dev/null; then
        for _b in "$WORK/reference"/reference-bundle-*.tar.gz; do
            [ -f "$_b" ] && echo "  bundled: $(basename "$_b")"
            break
        done
    else
        echo "  WARNING: could not download a reference bundle; this artifact ships without one." >&2
    fi
else
    echo "  WARNING: gh not available; this artifact ships without reference data." >&2
fi

# Strip pg_dump 17 artifacts rejected by PG 16 (see build-online.sh).
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
# assetatlas-cli ships under installer/ only (already copied). See build-online.sh.

# src/database/schema-full.sql is the canonical bootstrap snapshot.
SCHEMA_SRC="$REPO_ROOT/src/database/schema-full.sql"
[ -f "$SCHEMA_SRC" ] || { echo "ERROR: $SCHEMA_SRC not found" >&2; exit 1; }
cp "$SCHEMA_SRC" "$WORK/database/schema-full.sql"

# Defensive patch (see build-online.sh): qualify create_hypertable for
# the known-bad pg_dump pattern so regenerations don't break bootstrap.
sed -i.bak \
    -e "s|^SELECT create_hypertable('asset_chronicle'|SELECT public.create_hypertable('public.asset_chronicle'|" \
    "$WORK/database/schema-full.sql"
rm -f "$WORK/database/schema-full.sql.bak"
[ -d "$REPO_ROOT/docker/nginx" ] || \
    { echo "ERROR: docker/nginx not found in repo" >&2; exit 1; }
# Staged under compose/, not live — see build-online.sh for why.
mkdir -p "$WORK/compose/nginx"
cp -r "$REPO_ROOT/docker/nginx/." "$WORK/compose/nginx/"

# Monitoring overlay configs — see build-online.sh.
[ -d "$REPO_ROOT/monitoring" ] || \
    { echo "ERROR: monitoring/ not found in repo" >&2; exit 1; }
cp -r "$REPO_ROOT/monitoring/." "$WORK/monitoring/"

echo "offline" > "$WORK/INSTALL_MODE"

# Build app images (skip if SKIP_IMAGE_BUILD=1 passed for testing)
if [ "${SKIP_IMAGE_BUILD:-0}" != "1" ]; then
    echo "Building app images (this may take several minutes)..."
    PLATFORM_FLAG=""
    if [ "$(uname -m)" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then
        # macOS Apple Silicon: enforce linux/amd64 since prod targets are x86_64.
        # Per MEMORY.md: MUST use --platform linux/amd64 when building Docker on Apple Silicon — otherwise Exec format error
        PLATFORM_FLAG="--platform linux/amd64"
    fi
    if [ -f "$REPO_ROOT/Dockerfile.base" ]; then
        echo "Building base image..."
        docker build $PLATFORM_FLAG -t localhost/assetatlas-base:latest -f "$REPO_ROOT/Dockerfile.base" "$REPO_ROOT"
    fi
    echo "Building api/scheduler/mcp image..."
    docker build $PLATFORM_FLAG -t localhost/assetatlas-api:latest -f "$REPO_ROOT/Dockerfile" "$REPO_ROOT"
    if [ -f "$REPO_ROOT/ui/prod/Dockerfile" ]; then
        echo "Building ui image..."
        docker build $PLATFORM_FLAG -t localhost/assetatlas-ui:latest -f "$REPO_ROOT/ui/prod/Dockerfile" "$REPO_ROOT/ui/prod"
    fi
    if [ -f "$REPO_ROOT/Dockerfile.mcp" ] || [ -d "$REPO_ROOT/src/mcp" ]; then
        # MCP shares the api Dockerfile per current setup; tag the same image
        docker tag localhost/assetatlas-api:latest localhost/assetatlas-mcp:latest
    fi
    if [ -f "$REPO_ROOT/Dockerfile.teams-bridge" ]; then
        # Built, not tagged from api (#1069): the bridge is the internet-facing
        # container and now has its own python:3.13-slim image carrying only the
        # four packages it can actually import. Tagging api here would silently
        # put the ML/data stack back into an offline bundle even though the
        # online path builds it properly.
        echo "Building teams-bridge image..."
        docker build $PLATFORM_FLAG -t localhost/assetatlas-teams-bridge:latest \
            -f "$REPO_ROOT/Dockerfile.teams-bridge" "$REPO_ROOT"
    fi
    docker tag localhost/assetatlas-api:latest localhost/assetatlas-scheduler:latest
fi

# Save app images.
# Save them ALL in a single `docker save` call so shared layers (especially the
# ~0.9 GB base layer that api/scheduler/mcp all build on) are deduplicated in
# the output. Per-image saves were ballooning the bundle from ~6 GB to ~25 GB
# (each tarball carried a full copy of the base layer) and overflowing the
# GHA runner's disk during release builds.
# install.sh's image-load loop iterates `images/*.tar*` and `docker load -i`
# each — works the same whether there's one tarball or many.
echo "Saving app images to bundle (single call for layer dedup)..."
present_imgs=""
missing_imgs=""
for img in assetatlas-base assetatlas-api assetatlas-ui assetatlas-mcp assetatlas-scheduler assetatlas-teams-bridge; do
    if docker image inspect "localhost/$img:latest" >/dev/null 2>&1; then
        present_imgs="$present_imgs localhost/$img:latest"
        echo "  including $img"
    else
        missing_imgs="$missing_imgs $img"
        echo "  skip $img (not built)"
    fi
done

# A missing app image used to be that one-line "skip" followed by exit 0: the
# bundle shipped without it and the failure surfaced at install time, on an
# air-gapped customer host, with no network to recover from. Assert instead.
#
# This matters more now that teams-bridge is a real build rather than a tag of
# api (#1069) — a `docker build` that fails leaves nothing behind, where the old
# `docker tag` could only fail if api itself was missing.
#
# Scoped to a genuine build: SKIP_IMAGE_BUILD=1 is the documented test path that
# builds nothing on purpose, so nothing is expected to be present.
if [ "${SKIP_IMAGE_BUILD:-0}" != "1" ] && [ -n "$missing_imgs" ]; then
    echo "ERROR: app image(s) absent after a build that reported success:$missing_imgs" >&2
    echo "       Refusing to write a bundle that would fail at install time." >&2
    exit 1
fi
if [ -n "$present_imgs" ]; then
    # shellcheck disable=SC2086
    docker save $present_imgs | gzip > "$WORK/images/assetatlas-app-images.tar.gz"
fi

# Pull + save vendor images (use the pinned versions from compose)
# prom/prometheus is the opt-in metrics-forwarder agent (#922). It ships in the
# BASE bundle — not only the monitoring pack — so air-gapped installs can enable
# the `forwarding` profile standalone (remote_write app /metrics to a customer
# endpoint) without loading the full monitoring stack.
#
# These four are a HAND-KEPT DUPLICATE of the DB_IMAGE / REDIS_IMAGE /
# NGINX_IMAGE / PROMETHEUS_AGENT_IMAGE defaults in
# deploy/compose/docker-compose.yml — not derived from it the way
# build-monitoring-pack.sh greps the overlay, because those defaults are written
# `${VAR:-ref}` and the same file also carries app images this bundle builds
# itself plus a pgadmin the offline stack deliberately does not ship. Change a
# pin there and change it here. A drift no longer ships silently: install.sh
# verifies every image the compose files name is in local storage after loading
# the bundle, so an image saved under a name compose does not ask for now fails
# the install by name instead of at the health window.
#
# Registry-qualified for the same reason the compose defaults are (podman
# refuses short names on a host with no unqualified-search-registries). This does
# not change the tarballs: `docker save` writes the FAMILIAR name into RepoTags
# either way, and `podman load` normalises that back to docker.io/... — which is
# exactly what the compose defaults now say.
VENDOR_IMAGES="docker.io/timescale/timescaledb:2.17.2-pg16 docker.io/library/redis:7.4-alpine docker.io/library/nginx:1.27-alpine docker.io/prom/prometheus:v2.55.1"
echo "Pulling + saving vendor images..."
for img in $VENDOR_IMAGES; do
    if [ "${SKIP_VENDOR_PULL:-0}" != "1" ]; then
        docker pull --platform linux/amd64 "$img" 2>&1 | tail -1
    fi
    safe=$(echo "$img" | tr '/:' '__')
    if docker image inspect "$img" >/dev/null 2>&1; then
        echo "  saving $safe ..."
        docker save "$img" | gzip > "$WORK/images/$safe.tar.gz"
    fi
done

# Strip macOS junk before manifest computation (xattrs, AppleDouble, .DS_Store)
# so manifest matches tar contents and Linux GNU tar doesn't warn on extract.
if command -v xattr >/dev/null 2>&1; then
    xattr -cr "$WORK" 2>/dev/null || true
fi
find "$WORK" \( -name '._*' -o -name '.DS_Store' \) -delete 2>/dev/null || true

# SHA256 manifest
( cd "$WORK" && find . -type f ! -name SHA256SUMS -print0 \
    | xargs -0 shasum -a 256 2>/dev/null \
    | awk '{print $1"  "$2}' > SHA256SUMS ) || \
( cd "$WORK" && find . -type f ! -name SHA256SUMS -print0 \
    | xargs -0 sha256sum > SHA256SUMS )

# Single tarball (extract with: tar xzf <output>; ./installer/install.sh)
# --no-xattrs: prevent bsdtar (macOS) from embedding xattrs as PAX headers
# that Linux GNU tar warns about on extract. Fall back gracefully.
if tar --no-xattrs -czf /dev/null -C "$WORK" . 2>/dev/null; then
    tar --no-xattrs -czf "$OUTPUT" -C "$WORK" .
else
    tar -czf "$OUTPUT" -C "$WORK" .
fi

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
echo ""
echo "Built: $OUTPUT ($size)"
echo "Extract with: tar xzf $(basename "$OUTPUT") && ./installer/install.sh"
