#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/fetch.sh — resolve the latest published version and download+verify
# the online installer bundle. Sourced by update.sh (self-fetching updates).
# Requires: common.sh sourced (log_*). Uses curl + sha256sum/shasum.
#
# URL configuration (all overridable — testing + the final get.assetatlas.com cutover):
#   ASSETATLAS_INSTALL_BASE_URL  default https://get.assetatlas.com
#   ASSETATLAS_MANIFEST_URL      default $BASE_URL/latest.json
#   ASSETATLAS_BUNDLE_BASE       default $BASE_URL/dl

: "${ASSETATLAS_INSTALL_BASE_URL:=https://get.assetatlas.com}"
: "${ASSETATLAS_MANIFEST_URL:=${ASSETATLAS_INSTALL_BASE_URL}/latest.json}"
: "${ASSETATLAS_BUNDLE_BASE:=${ASSETATLAS_INSTALL_BASE_URL}/dl}"

_sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# _json_field <json> <key> -> prints the string value of "key" (minimal, no jq).
_json_field() {
    printf '%s' "$1" \
        | grep -Eo "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
        | head -1 \
        | sed -E "s/.*:[[:space:]]*\"([^\"]*)\"/\1/" || true
}

# resolve_latest_version : prints version, sets MANIFEST_SHA256. Fail-closed.
resolve_latest_version() {
    local body
    body=$(curl -fsSL --max-time 15 "$ASSETATLAS_MANIFEST_URL" 2>/dev/null) \
        || { log_error "Could not fetch version manifest: $ASSETATLAS_MANIFEST_URL"; return 1; }
    local version
    version=$(_json_field "$body" version)
    [ -n "$version" ] || { log_error "Manifest has no 'version': $ASSETATLAS_MANIFEST_URL"; return 1; }
    MANIFEST_SHA256=$(_json_field "$body" online_sha256)
    export MANIFEST_SHA256
    printf '%s\n' "$version"
}

# download_bundle <version> <destdir> : downloads + verifies, prints the path.
download_bundle() {
    local version=$1 destdir=$2
    [ -n "$version" ] || { log_error "download_bundle: version required"; return 1; }
    [ -d "$destdir" ] || { log_error "download_bundle: destdir not found: $destdir"; return 1; }
    local name="assetatlas-online-${version}.sh"
    local url="${ASSETATLAS_BUNDLE_BASE}/${name}"
    local out="${destdir}/${name}"
    log_info "Downloading $name ..." >&2
    curl -fsSL --max-time 300 -o "$out" "$url" \
        || { log_error "Download failed: $url"; rm -f "$out"; return 1; }

    local expected="${MANIFEST_SHA256:-}"
    if [ -z "$expected" ]; then
        expected=$(curl -fsSL --max-time 30 "${url}.sha256" 2>/dev/null | awk '{print $1}' | head -1) || true
    fi
    [ -n "$expected" ] || { log_error "No sha256 available to verify $name"; rm -f "$out"; return 1; }
    local actual; actual=$(_sha256_file "$out")
    if [ "$actual" != "$expected" ]; then
        log_error "sha256 mismatch for $name (expected $expected, got $actual)"
        rm -f "$out"; return 1
    fi
    log_ok "Verified $name (sha256 ok)." >&2
    printf '%s\n' "$out"
}
