# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bundle manifest parsing, sha256 verification, and app-version compatibility check."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from typing import Dict

from src.modules.reference_data.models import Manifest


SUPPORTED_FORMAT_VERSION = 1


class InvalidBundleError(ValueError):
    """Bundle is malformed, corrupted, or sha256 verification failed."""


class IncompatibleAppVersionError(ValueError):
    """Bundle declares a min app version higher than the running app."""


@dataclass
class ParsedBundle:
    manifest: Manifest
    payloads: Dict[str, bytes]


def parse_and_verify(bundle_bytes: bytes, current_app_version: str) -> ParsedBundle:
    """Decompress, verify sha256s and app-compat, return parsed manifest + payloads."""
    try:
        inner = gzip.decompress(bundle_bytes)
    except OSError as e:
        raise InvalidBundleError(f"not a gzip stream: {e}") from e

    try:
        tar = tarfile.open(fileobj=io.BytesIO(inner), mode='r')
    except tarfile.TarError as e:
        raise InvalidBundleError(f"not a tar stream: {e}") from e

    files: Dict[str, bytes] = {}
    for member in tar.getmembers():
        if not member.isfile():
            continue
        f = tar.extractfile(member)
        if f is None:
            continue
        files[member.name] = f.read()
    tar.close()

    if 'manifest.json' not in files:
        raise InvalidBundleError("manifest.json missing from bundle")

    try:
        manifest_dict = json.loads(files.pop('manifest.json'))
    except json.JSONDecodeError as e:
        raise InvalidBundleError(f"manifest.json not valid JSON: {e}") from e

    fmt = manifest_dict.get('format_version')
    if fmt != SUPPORTED_FORMAT_VERSION:
        raise InvalidBundleError(
            f"unsupported format_version {fmt!r}; this app supports {SUPPORTED_FORMAT_VERSION}"
        )

    # Tolerate (and ignore) a top-level "sha256" field if a producer still emits one.
    manifest_dict.pop('sha256', None)
    try:
        manifest = Manifest.model_validate(manifest_dict)
    except Exception as e:
        raise InvalidBundleError(f"manifest schema invalid: {e}") from e

    for ds in manifest.datasets:
        if ds.payload not in files:
            raise InvalidBundleError(f"payload {ds.payload!r} missing from bundle")
        actual = hashlib.sha256(files[ds.payload]).hexdigest()
        if actual != ds.sha256:
            raise InvalidBundleError(f"sha256 mismatch for {ds.payload}")

    if _version_lt(current_app_version, manifest.app_compat.min):
        raise IncompatibleAppVersionError(
            f"bundle requires app >= {manifest.app_compat.min}, "
            f"running {current_app_version}"
        )

    return ParsedBundle(manifest=manifest, payloads=files)


def _version_lt(a: str, b: str) -> bool:
    """Return True iff version a < version b (CalVer/SemVer-like dotted int compare)."""
    def _parts(v: str) -> tuple:
        return tuple(int(x) for x in v.split('.') if x.isdigit())
    return _parts(a) < _parts(b)
