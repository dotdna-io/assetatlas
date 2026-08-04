#!/usr/bin/env bash
# deploy/build/build-monitoring-pack.sh — produces assetatlas-monitoring-images-<VERSION>.tar.gz
# Optional add-on for offline installs that opt into monitoring.
# Customers extract this INTO an existing offline install's images/ directory:
#   tar xzf assetatlas-monitoring-images-VERSION.tar.gz -C /opt/assetatlas/images/
#   /opt/assetatlas/installer/assetatlas-cli enable monitoring
#
# Output is a tar.gz containing the docker-saved images, ~300-400 MB.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERSION="$(cat "$REPO_ROOT/VERSION")"
OUTPUT="${1:-$REPO_ROOT/dist/assetatlas-monitoring-images-$VERSION.tar.gz}"
mkdir -p "$(dirname "$OUTPUT")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "Assembling monitoring vendor pack ($VERSION) ..."
mkdir -p "$WORK/images"

# Read the actual versions from the monitoring compose file rather than hardcoding.
# Use grep + awk to pull out "image: foo:v1.2.3" lines.
MONITORING_COMPOSE="$REPO_ROOT/deploy/compose/docker-compose.monitoring.yml"
[ -f "$MONITORING_COMPOSE" ] || { echo "Missing $MONITORING_COMPOSE" >&2; exit 1; }
IMAGES=$(grep -E '^\s+image:' "$MONITORING_COMPOSE" | awk '{print $2}' | sed 's/^"//; s/"$//' | sort -u)
[ -n "$IMAGES" ] || { echo "No images found in $MONITORING_COMPOSE" >&2; exit 1; }

echo "Will bundle:"
printf '  %s\n' $IMAGES

for img in $IMAGES; do
    if [ "${SKIP_VENDOR_PULL:-0}" != "1" ]; then
        docker pull --platform linux/amd64 "$img" 2>&1 | tail -1
    fi
    safe=$(echo "$img" | tr '/:' '__')
    if docker image inspect "$img" >/dev/null 2>&1; then
        echo "  saving $safe ..."
        docker save "$img" | gzip > "$WORK/images/$safe.tar.gz"
    else
        echo "  WARN: $img not present locally; skipping" >&2
    fi
done

# Top-level tarball
tar -czf "$OUTPUT" -C "$WORK" images
size=$(du -h "$OUTPUT" | cut -f1)
echo ""
echo "Built: $OUTPUT ($size)"
echo "Install with:"
echo "  tar xzf $(basename "$OUTPUT") -C /opt/assetatlas/"
echo "  /opt/assetatlas/installer/assetatlas-cli enable monitoring"
