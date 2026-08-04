# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from src.modules.reference_data.manifest import (
    InvalidBundleError,
    IncompatibleAppVersionError,
    parse_and_verify,
)


def test_parse_valid_bundle_returns_manifest_and_payloads(make_bundle):
    parsed = parse_and_verify(make_bundle(), current_app_version='2026.5.0')
    assert parsed.manifest.bundle_version == '2026.17.1'
    assert set(parsed.payloads.keys()) == {'mac_vendor_ranges.jsonl', 'os_eol_data.jsonl'}


def test_corrupt_payload_sha256_raises(make_bundle):
    # Manifest declares sha of the original mac payload; we ship TAMPERED bytes.
    bundle = make_bundle(files={'mac_vendor_ranges.jsonl': b'TAMPERED'})
    with pytest.raises(InvalidBundleError, match='sha256 mismatch'):
        parse_and_verify(bundle, current_app_version='2026.5.0')


def test_incompatible_app_version_raises(make_bundle):
    bundle = make_bundle(app_compat_min='3000.1.0')
    with pytest.raises(IncompatibleAppVersionError):
        parse_and_verify(bundle, current_app_version='2026.5.0')


def test_unknown_format_version_raises(make_bundle):
    bundle = make_bundle(overrides={'format_version': 99})
    with pytest.raises(InvalidBundleError, match='format_version'):
        parse_and_verify(bundle, current_app_version='2026.5.0')
