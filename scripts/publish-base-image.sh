#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# scripts/publish-base-image.sh — build the base image and push it to ghcr.io.
#
# Run this MANUALLY when:
#   - You first set up the release pipeline (initial push so CI has something to pull)
#   - Any file in scripts/deps-fingerprint.sh's list changes (requirements-prod.txt,
#     Dockerfile.base, the embedding encode path, the parity gate + its fixtures)
#   - The reference data refresh interval has passed (every quarter or so)
#
# CI release.yml does NOT build base — it pulls this image from ghcr.io and builds
# the thin api/ui layer on top. This is a deliberate split: the base image is big
# (~0.9 GB) and depends on data files that aren't in git, so it's built locally and
# pushed manually.
#
# The image is stamped with an `org.assetatlas.deps-sha` label (sha256 of the six
# files listed in scripts/deps-fingerprint.sh). release.yml reads that label back
# from ghcr.io and FAILS the release if it disagrees with the repo — that's the
# guard that catches "forgot to rerun publish-base-image.sh after editing
# requirements-prod.txt".
#
# Tags pushed:
#   :deps-<first 12 of the deps fingerprint>   always — the image's real identity
#   :<version>                                 only with --version <ver>
#   :<tag>                                     only with --tag-only <tag>
#   :latest                                    always, pushed last — release.yml
#                                              resolves the base through it
#
# WHY THERE IS NO AUTOMATIC :$(cat VERSION) TAG — do not "simplify" one back in:
#   The base MUST be republished BEFORE the release that consumes it can be cut
#   (release.yml refuses to build the app layer while the image's deps-sha label
#   disagrees with the repo), and VERSION is only bumped BY that release. So at
#   publish time the VERSION file still holds the PREVIOUS release, and tagging
#   the base with it labels the image for a release that shipped on a DIFFERENT
#   base. That is exactly what happened on 2026-08-01 (#1265): the base published
#   then got tagged 2026.31.15, but v2026.31.15 had already shipped on the older
#   base — the tag had to be repointed by hand with `imagetools create`.
#   A base is identified by its dependency fingerprint, not by a release: one base
#   legitimately serves every release until a fingerprint file changes again, so a
#   release version cannot name it. Pass --version <ver> only when you already know
#   the upcoming release — the one case where a version tag can be correct.
#
#   CAVEAT: the fingerprint covers dependency files only. The reference data and
#   model cache Dockerfile.base bakes in are NOT in it, so the quarterly data
#   refresh above republishes a different image under the same :deps- tag. The
#   registry check before the push says so; if you need that image to stay
#   reachable, pin it first (--tag-only, or note its digest).
#
# Prerequisites on this machine:
#   - data/, models/, etc. populated (run scripts/prepare-base-data.sh first)
#   - Docker daemon running
#   - `gh auth login` AND `docker login ghcr.io` (or set GHCR_TOKEN env var)
#
# Usage:
#   ./scripts/publish-base-image.sh                          # :deps-<sha12> + :latest
#   ./scripts/publish-base-image.sh --version 2026.31.16     # ...also :2026.31.16
#   ./scripts/publish-base-image.sh --tag-only my-test-tag   # ...also :my-test-tag
#   ./scripts/publish-base-image.sh --no-push                # build locally, don't push (smoke)
#   ./scripts/publish-base-image.sh --print-tags             # print the tag set and exit (no build)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Absolute — `cd "$REPO_ROOT"` below invalidates a relative $0, and --help reads
# its own source back. `bash sub/dir/publish-base-image.sh --help` from anywhere
# but the repo root used to die with "awk: can't open file".
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PUSH=1
EXTRA_TAG=""
VERSION_TAG=""
PRINT_TAGS_ONLY=0

# A `--flag` whose value is missing swallows the NEXT flag instead: `--version
# --no-push` would tag the image ":--no-push" and then push it, having eaten the
# very flag that said not to. Reject a value that is absent or starts with "-".
require_value() {
    echo "$1 requires a value (e.g. $1 $2)" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-push)    PUSH=0 ;;
        --print-tags) PRINT_TAGS_ONLY=1 ;;
        --version)
            { [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; } || require_value --version 2026.31.16
            VERSION_TAG="$2"; shift ;;
        --tag-only)
            # Additive, and deliberately does NOT re-enable pushing: it used to
            # set PUSH=1, so `--no-push --tag-only smoke` pushed to ghcr anyway.
            { [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; } || require_value --tag-only my-test-tag
            EXTRA_TAG="$2"; shift ;;
        -h|--help)    awk 'NR > 1 { if (!/^#/) exit; print }' "$SCRIPT_PATH"; exit 0 ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
    shift
done

# Resolved after the flag loop so --help and "Unknown flag" work on a checkout
# that has not exported GHCR_OWNER.
OWNER="${GHCR_OWNER:?GHCR_OWNER must be set (e.g. your GitHub username or organization)}"
IMAGE="ghcr.io/${OWNER}/assetatlas-base"

# Fingerprint of what determines this image. Goes on as the
# `org.assetatlas.deps-sha` label so release.yml can detect when the published
# base is stale relative to the repo and block the release before it ships an
# app image on top of an outdated layer — and, since #1265, is also the image's
# primary tag: unlike a release version it is derivable from repo state alone,
# and it is the same input release.yml already gates on.
#
# The file list lives in scripts/deps-fingerprint.sh, shared with release.yml —
# two copies would drift, and a fingerprint that disagrees with its verifier is
# worse than none.
DEPS_SHA=$(bash "$SCRIPT_DIR/deps-fingerprint.sh")
DEPS_TAG="deps-${DEPS_SHA:0:12}"
echo "==> deps fingerprint: $DEPS_SHA"

# Tag set, in push order: content-addressed identity first, then the pins an
# operator asked for, then :latest last — :latest is the pointer release.yml
# resolves, so it flips only once everything it could name is already up.
TAGS=("$DEPS_TAG")
PINNED_TAGS=()   # tags a push would silently MOVE; only :latest is exempt
if [ -n "$VERSION_TAG" ]; then
    TAGS+=("$VERSION_TAG")
    PINNED_TAGS+=("$VERSION_TAG")
fi
if [ -n "$EXTRA_TAG" ]; then
    TAGS+=("$EXTRA_TAG")
    PINNED_TAGS+=("$EXTRA_TAG")
fi
TAGS+=(latest)

echo "==> tags:"
for t in "${TAGS[@]}"; do
    printf "  %s:%s\n" "$IMAGE" "$t"
done

if [ "$PRINT_TAGS_ONLY" = "1" ]; then
    exit 0
fi

# Sanity: data files must be present (don't accidentally bake an empty base)
echo "==> Checking data files are present..."
missing=0
for f in data/mac_ranges.csv data/categories.json data/dbip-city-lite.mmdb \
         models/cache/models--BAAI--bge-base-en-v1.5; do
    if [ ! -e "$f" ]; then
        echo "  MISSING: $f" >&2
        missing=1
    else
        printf "  OK: %s\n" "$f"
    fi
done
if [ ! -d data/device_knowledge_base ] || [ -z "$(ls -A data/device_knowledge_base 2>/dev/null)" ]; then
    echo "  MISSING: data/device_knowledge_base/ (empty or absent)" >&2
    missing=1
else
    echo "  OK: data/device_knowledge_base/"
fi

# Catch git-LFS pointer files (130-byte text "version https://git-lfs.github.com...")
# slipping into the image. They make device_knowledge_base.py log "Bad product
# line row" on every boot and CSV ingestion silently degrades. Operators can hit
# this when their checkout doesn't have `git lfs pull` resolved.
echo "==> Checking CSVs are real content (not LFS pointers)..."
lfs_pointers=$(find data/ data_scrapers/ -name '*.csv' -size -2k 2>/dev/null \
    | while read -r f; do
        if head -c 50 "$f" 2>/dev/null | grep -q "git-lfs.github.com"; then
            printf "  LFS POINTER (run 'git lfs pull'): %s\n" "$f"
        fi
    done)
if [ -n "$lfs_pointers" ]; then
    echo "$lfs_pointers" >&2
    missing=1
fi

if [ "$missing" = "1" ]; then
    echo "" >&2
    echo "Run: ./scripts/prepare-base-data.sh" >&2
    echo "(and 'git lfs pull' if the failure mentioned LFS pointers)" >&2
    echo "Then re-run this script." >&2
    exit 1
fi

# Build
PLATFORM_FLAG=""
if [ "$(uname -m)" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then
    # Per MEMORY.md: MUST use --platform linux/amd64 on Apple Silicon
    PLATFORM_FLAG="--platform linux/amd64"
fi

TAG_FLAGS=()
for t in "${TAGS[@]}"; do
    TAG_FLAGS+=(-t "${IMAGE}:${t}")
done

echo ""
echo "==> Building $IMAGE locally (this may take 10+ minutes)..."
# shellcheck disable=SC2086
docker build $PLATFORM_FLAG \
    --label "org.assetatlas.deps-sha=${DEPS_SHA}" \
    "${TAG_FLAGS[@]}" \
    -f Dockerfile.base \
    .
echo "Built:"
for t in "${TAGS[@]}"; do
    printf "  %s:%s\n" "$IMAGE" "$t"
done

if [ "$PUSH" = "0" ]; then
    echo ""
    echo "Skipping push (--no-push). Local images:"
    docker images "${IMAGE}" | head -5
    exit 0
fi

# Login if not already logged in (best effort; no-op if creds present)
if ! docker info 2>/dev/null | grep -q "ghcr.io"; then
    if [ -n "${GHCR_TOKEN:-}" ]; then
        echo "$GHCR_TOKEN" | docker login ghcr.io -u "$OWNER" --password-stdin
    elif command -v gh >/dev/null 2>&1; then
        gh auth token | docker login ghcr.io -u "$OWNER" --password-stdin
    else
        echo "Not logged in to ghcr.io and no GHCR_TOKEN env or gh CLI available." >&2
        echo "Run: docker login ghcr.io -u <user>" >&2
        exit 1
    fi
fi

# Manifest digest of a tag as it exists on the registry right now. Prints nothing
# when the tag is absent — and also when the registry can't be read at all, which
# is why check_tag_collisions probes a tag that must exist before trusting any
# empty answer. A check that reports "free" because it never ran is worse than no
# check (same shape as preflight.sh's check_cert, see .claude/rules/deploy.md).
registry_digest() {
    docker buildx imagetools inspect --format '{{ .Manifest.Digest }}' "$1" 2>/dev/null || true
}

# Would pushing MOVE a tag somebody is already reading as provenance?
#
# :latest is exempt — it is meant to move. Every other tag is a pin, and
# re-pushing it silently rewrites the answer to "which base did release X run
# on" (#1265).
#
# :deps-<sha12> is NOT exempt, and is not content-addressed for the image: the
# fingerprint covers the six dependency files in scripts/deps-fingerprint.sh,
# while Dockerfile.base additionally bakes data/mac_ranges.csv, categories.json,
# device_knowledge_base/, the dbip mmdb and the BGE model cache — none of which
# are in it. A quarterly reference-data refresh (a documented reason to rerun
# this script) therefore republishes a DIFFERENT image under the SAME deps tag.
check_tag_collisions() {
    local pinned=("$@")
    local probe existing ref t

    echo ""
    echo "==> Checking tags against the registry..."
    probe=$(registry_digest "${IMAGE}:latest")
    if [ -z "$probe" ]; then
        echo "  !! COULD NOT READ ${IMAGE}:latest — registry unreachable, not" >&2
        echo "  !! authenticated, or nothing published yet. The tag check below" >&2
        echo "  !! did NOT run; empty results here do not mean the tags are free." >&2
        return 0
    fi

    existing=$(registry_digest "${IMAGE}:${DEPS_TAG}")
    if [ -z "$existing" ]; then
        printf "  OK: %s:%s is not on the registry yet\n" "$IMAGE" "$DEPS_TAG"
    else
        printf "  NOTE: %s:%s already exists (%s) and this push REPLACES it.\n" \
            "$IMAGE" "$DEPS_TAG" "$existing"
        printf "        Same dependency fingerprint, but the reference data and model\n"
        printf "        cache baked into the image are not part of it — if either was\n"
        printf "        refreshed, this tag stops naming the base earlier releases ran on.\n"
    fi

    for t in ${pinned[@]+"${pinned[@]}"}; do
        ref="${IMAGE}:${t}"
        existing=$(registry_digest "$ref")
        if [ -z "$existing" ]; then
            printf "  OK: %s is not on the registry yet\n" "$ref"
        else
            echo "" >&2
            echo "  !! WARNING: $ref already exists on the registry." >&2
            echo "  !!   currently: $existing" >&2
            echo "  !! Pushing MOVES that tag onto the image just built. If a release" >&2
            echo "  !! shipped on the image it points at today, the record of which base" >&2
            echo "  !! that release used is gone — that is #1265, and undoing it needs" >&2
            echo "  !! \`imagetools create\`." >&2
            echo "" >&2
        fi
    done
}

check_tag_collisions ${PINNED_TAGS[@]+"${PINNED_TAGS[@]}"}

for t in "${TAGS[@]}"; do
    echo ""
    echo "==> Pushing ${IMAGE}:${t}..."
    docker push "${IMAGE}:${t}"
done

echo ""
echo "Done. CI release.yml can now pull ghcr.io/${OWNER}/assetatlas-base:latest."
echo "This base is ${IMAGE}:${DEPS_TAG} — it serves every release until a file in"
echo "scripts/deps-fingerprint.sh changes."
