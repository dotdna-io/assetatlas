# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for reference-data unit tests."""

import gzip
import hashlib
import io
import json
import tarfile
from typing import Mapping, Optional

import pytest


def build_bundle(
    *,
    bundle_version: str = '2026.17.1',
    app_compat_min: str = '0.0.1',
    mac_payload: bytes = b'{"mac_prefix":"00:00:00","vendor":"Acme","data_source":"ieee"}\n',
    eol_payload: bytes = (
        b'{"product_name":"windows","cycle":"11","eol_date":"2031-10-14",'
        b'"data_source":"endoflife.date"}\n'
    ),
    overrides: Optional[Mapping] = None,
    files: Optional[Mapping[str, bytes]] = None,
) -> bytes:
    """
    Build a gzipped tar bundle containing manifest.json + the two payloads.

    `overrides` mutates the manifest dict before serialization (e.g. set
    `format_version` to an unsupported value). `files` overrides the payload
    files actually shipped (used to test sha256 mismatch).
    """
    manifest = {
        'format_version': 1,
        'bundle_version': bundle_version,
        'generated_at': '2026-04-28T03:14:00Z',
        'app_compat': {'min': app_compat_min},
        'datasets': [
            {'name': 'mac_vendor_ranges', 'payload': 'mac_vendor_ranges.jsonl',
             'sha256': hashlib.sha256(mac_payload).hexdigest(), 'row_count': 1,
             'source': 'test', 'apply_strategy': 'truncate_replace'},
            {'name': 'os_eol_data', 'payload': 'os_eol_data.jsonl',
             'sha256': hashlib.sha256(eol_payload).hexdigest(), 'row_count': 1,
             'source': 'test', 'apply_strategy': 'upsert_by_product_cycle'},
        ],
    }
    if overrides:
        manifest.update(overrides)

    shipped = {
        'mac_vendor_ranges.jsonl': mac_payload,
        'os_eol_data.jsonl': eol_payload,
    }
    if files:
        shipped.update(files)

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for name, data in (
            ('manifest.json', json.dumps(manifest).encode()),
            *shipped.items(),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(out.getvalue())


class StubMetrics:
    """No-op stand-in for the prometheus metrics module."""
    class _G:
        def labels(self, **_kw): return self
        def set(self, *_a, **_kw): pass
        def set_to_current_time(self): pass

    reference_data_records_total = _G()
    reference_data_last_updated_timestamp = _G()


@pytest.fixture
def make_bundle():
    return build_bundle


@pytest.fixture
def stub_metrics():
    return StubMetrics()
