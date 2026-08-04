#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Fingerprint of everything baked into the base image.
#
# ONE definition, used by scripts/publish-base-image.sh (which stamps it as the
# org.assetatlas.deps-sha label) and .github/workflows/release.yml (which refuses
# to build the app layer on a base whose label no longer matches). Three separate
# copies of this file list is exactly how they drift apart.
#
# The list must cover everything whose change should force a base rebuild:
#
#   requirements-prod.txt  - the runtime pip manifest (caught the 2026.20.2
#                            ship-without-boto3 incident)
#   constraints-prod.txt   - the pinned transitive closure that manifest resolves
#                            to (#1262). requirements-prod.txt says WHICH packages
#                            and at what floor; this says WHICH VERSIONS, and it is
#                            what every install site actually resolves under. It
#                            has to be here or the pin is decorative: regenerating
#                            the constraints would leave the fingerprint unchanged,
#                            release.yml would keep accepting the published base,
#                            and the image would go on serving versions the repo
#                            has already moved off. A pin the base image is free
#                            to ignore is not a pin.
#   Dockerfile.base        - the build itself
#   onnx_backend.py        - the embedding encode path. It SHIPS in the app image,
#                            but Dockerfile.base runs the parity gate against it
#                            (#1070). Without it here, editing the encode path
#                            leaves the base untouched, the gate never runs, and
#                            an embedding drift ships silently — the gate would
#                            guard nothing.
#   verify_embedding_parity.py + fixtures
#                          - the gate and its golden references. Weakening either
#                            must invalidate the attestation it produced.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DEPS_FILES=(
    requirements-prod.txt
    constraints-prod.txt
    Dockerfile.base
    src/services/embedding/onnx_backend.py
    scripts/verify_embedding_parity.py
    tests/fixtures/embedding/golden_texts.json
    tests/fixtures/embedding/golden_vectors.npy
)

for f in "${DEPS_FILES[@]}"; do
    [ -f "$f" ] || { echo "deps-fingerprint: missing $f" >&2; exit 1; }
done

if command -v sha256sum >/dev/null 2>&1; then
    cat "${DEPS_FILES[@]}" | sha256sum | awk '{print $1}'
else
    cat "${DEPS_FILES[@]}" | shasum -a 256 | awk '{print $1}'
fi
