#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# scripts/prepare-base-data.sh — prepare reference data and ML model needed by Dockerfile.base.
#
# Idempotent: checks data/.manifest.json before fetching. Pass --force to re-fetch everything.
#
# Outputs:
#   models/cache/                            BGE embedding model
#   data/dbip-city-lite.mmdb                 IP geolocation
#   data/device_knowledge_base/              Device categorization CSVs
#   data/mac_ranges.csv                      MAC vendor lookups
#   data/.manifest.json                      Cache freshness markers
#
# Uses an isolated venv at .venv-base-prep/ so we never install to system Python
# (CLAUDE.md rule). The venv is reused between runs.
#
# CI uses GitHub Actions cache keyed on scrapers tree hash + year-week:
#   - BGE model name (permanent)
#   - dbip year-month (monthly refresh)
#   - device knowledge: scrapers tree hash (refresh when scrapers change)
#   - mac_ranges: year-week (weekly refresh)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

mkdir -p data models/cache

# --- Isolated venv (never install to system Python) ---
VENV_DIR=".venv-base-prep"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
# Use the venv's python for everything from here on. PIP and PY are kept in sync.
PY="$REPO_ROOT/$VENV_DIR/bin/python"
PIP="$PY -m pip"
$PIP install --quiet --upgrade pip wheel

MANIFEST="data/.manifest.json"
if [ ! -f "$MANIFEST" ]; then
    echo '{}' > "$MANIFEST"
fi

# Helper: read a key from manifest
m_get() {
    "$PY" -c "import json,sys; print(json.load(open('$MANIFEST')).get('$1',''))" 2>/dev/null || echo ""
}
m_set() {
    "$PY" -c "
import json
m=json.load(open('$MANIFEST'))
m['$1']='$2'
json.dump(m, open('$MANIFEST','w'), indent=2)
"
}

current_dbip_key="$(date -u +%Y-%m)"
current_mac_key="$(date -u +%Y-W%V)"

# 1. BGE model
# Freshness is judged on the ONNX export, not just the repo directory: since
# #1070 that is the only weight format the image ships and the runtime loads, so
# a cache populated before the swap (or by sentence-transformers) must re-fetch
# rather than silently pass and fail later inside `docker build`.
if [ "$FORCE" = "1" ] \
   || ! ls models/cache/models--BAAI--bge-base-en-v1.5/snapshots/*/onnx/model.onnx >/dev/null 2>&1 \
   || [ "$(m_get bge_model)" != "BAAI/bge-base-en-v1.5" ]; then
    echo "==> Downloading BGE model BAAI/bge-base-en-v1.5..."
    $PIP install --quiet "huggingface-hub>=0.20"
    "$PY" -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='BAAI/bge-base-en-v1.5', cache_dir='models/cache')
"
    m_set bge_model "BAAI/bge-base-en-v1.5"
else
    echo "==> BGE model already present, skip"
fi

# 2. DB-IP geolocation
if [ "$FORCE" = "1" ] || [ ! -f "data/dbip-city-lite.mmdb" ] || [ "$(m_get dbip)" != "$current_dbip_key" ]; then
    echo "==> Downloading dbip-city-lite for $current_dbip_key..."
    "$PY" data_scrapers/geolocation/download_dbip.py
    m_set dbip "$current_dbip_key"
else
    echo "==> dbip-city-lite cached for $current_dbip_key, skip"
fi

# 3. Device knowledge base
# Cache key: hash of scraper source files — regenerate when scrapers change.
current_dk_key="$(git rev-parse --short HEAD:data_scrapers/device_knowledge 2>/dev/null || echo 'unknown')"
if [ "$FORCE" = "1" ] || [ ! -d "data/device_knowledge_base" ] || [ "$(m_get device_knowledge)" != "$current_dk_key" ]; then
    echo "==> Generating device_knowledge_base (key: $current_dk_key)..."
    if [ -f "data_scrapers/device_knowledge/requirements.txt" ]; then
        $PIP install --quiet -r data_scrapers/device_knowledge/requirements.txt
    fi
    # Run as a module so the package's absolute imports
    # (e.g. `from data_scrapers.device_knowledge.category_mapping import ...`) resolve.
    PYTHONPATH="$REPO_ROOT" "$PY" -m data_scrapers.device_knowledge.run_all --output-dir data/device_knowledge_base
    m_set device_knowledge "$current_dk_key"
else
    echo "==> device_knowledge_base cached for $current_dk_key, skip"
fi

# 4. MAC vendor (IEEE OUI database)
# The original mac_vendor scraper writes to PostgreSQL and is not CI-friendly.
# For base-image build we fetch the canonical IEEE OUI CSV directly.
# The runtime app falls back to per-request lookup for unknown MACs, so a
# single-source CSV is acceptable for installer purposes.
if [ "$FORCE" = "1" ] || [ ! -f "data/mac_ranges.csv" ] || [ "$(m_get mac_vendor)" != "$current_mac_key" ]; then
    echo "==> Fetching mac_ranges.csv from IEEE OUI database for $current_mac_key..."
    # Stdlib only — no extra pip install needed.
    "$PY" -c "
import csv, urllib.request
from io import StringIO
from pathlib import Path
url = 'https://standards-oui.ieee.org/oui/oui.csv'
print('  Fetching IEEE OUI database...')
req = urllib.request.Request(url, headers={'User-Agent': 'AssetAtlas-installer-build'})
with urllib.request.urlopen(req, timeout=120) as resp:
    body = resp.read().decode('utf-8', errors='replace')
out = Path('data/mac_ranges.csv')
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['mac_prefix', 'vendor', 'address'])
    rdr = csv.DictReader(StringIO(body))
    cnt = 0
    for row in rdr:
        # IEEE oui.csv columns: Registry, Assignment, Organization Name, Organization Address
        prefix = row.get('Assignment', '').strip()
        vendor = row.get('Organization Name', '').strip()
        addr = row.get('Organization Address', '').strip()
        if prefix and vendor:
            w.writerow([prefix, vendor, addr])
            cnt += 1
print(f'  Wrote {cnt} entries to {out}')
"
    m_set mac_vendor "$current_mac_key"
else
    echo "==> mac_ranges.csv cached for $current_mac_key, skip"
fi

echo ""
echo "Manifest:"
cat "$MANIFEST"
