#!/usr/bin/env bash
# teams-app/build.sh — produces a sideload-ready Teams app zip.
#
# Reads TEAMS_BOT_APP_ID and ASSETATLAS_PUBLIC_BASE_URL from the repo's
# .env (if present) or from the surrounding environment, substitutes them
# into manifest.template.json, and zips manifest + icons.
#
# ORG_NAME is hardcoded to "DOTDNA". Developer URLs derive from
# ASSETATLAS_PUBLIC_BASE_URL, which Teams requires to be HTTPS.
#
# Operators no longer need this script — Settings → Chatbot → Teams ships
# the same zip from the UI. This file remains for CI / local-dev sanity
# checks.
#
# Source-of-truth assets live under src/api/utils/teams_manifest_assets/
# so they're packaged inside the prod Docker image. This script reads
# the same files via a relative path; keep them in sync by editing the
# files in src/, not by copying.

set -euo pipefail

cd "$(dirname "$0")"

if [[ -f ../.env ]]; then
  set -a; source ../.env; set +a
fi

: "${TEAMS_BOT_APP_ID:?TEAMS_BOT_APP_ID must be set}"
: "${ASSETATLAS_PUBLIC_BASE_URL:?ASSETATLAS_PUBLIC_BASE_URL must be set}"

if [[ "$ASSETATLAS_PUBLIC_BASE_URL" != https://* ]]; then
  echo "ERROR: ASSETATLAS_PUBLIC_BASE_URL must be HTTPS." >&2
  exit 1
fi

ASSETS_DIR="../src/api/utils/teams_manifest_assets"
[ -f "$ASSETS_DIR/manifest.template.json" ] || {
  echo "ERROR: manifest template not found at $ASSETS_DIR/manifest.template.json" >&2
  exit 1
}

ORG_NAME="DOTDNA"
HOST="${ASSETATLAS_PUBLIC_BASE_URL#*://}"
HOST="${HOST%%/*}"

OUT_DIR="dist"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

cp "$ASSETS_DIR/manifest.template.json" "$OUT_DIR/manifest.json"

sed -i.bak \
    -e "s|{{TEAMS_BOT_APP_ID}}|$TEAMS_BOT_APP_ID|g" \
    -e "s|{{ORG_NAME}}|$ORG_NAME|g" \
    -e "s|{{ASSETATLAS_PUBLIC_BASE_URL}}|$ASSETATLAS_PUBLIC_BASE_URL|g" \
    -e "s|{{ASSETATLAS_PUBLIC_HOSTNAME}}|$HOST|g" \
    "$OUT_DIR/manifest.json"
rm "$OUT_DIR/manifest.json.bak"

cp "$ASSETS_DIR/icons/color.png" "$ASSETS_DIR/icons/outline.png" "$OUT_DIR/"

(cd "$OUT_DIR" && zip -r ../assetatlas-teams.zip . > /dev/null)

echo "Created: $(pwd)/assetatlas-teams.zip"
