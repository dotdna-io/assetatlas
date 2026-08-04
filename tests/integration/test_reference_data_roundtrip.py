# SPDX-License-Identifier: AGPL-3.0-or-later
import gzip
import hashlib
import io
import json
import os
import tarfile

import pytest

from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.reference_data.repository import ReferenceDataRepository
from src.modules.reference_data.service import ReferenceDataService
from src.modules.reference_data.sources import UploadedBytesSource

pytestmark = pytest.mark.integration


def _build_bundle() -> bytes:
    mac = (b'{"mac_prefix":"FE:FE:FE","vendor":"TestCo","data_source":"test"}\n'
           b'{"mac_prefix":"FE:FE:FF","vendor":"TestCo2","data_source":"test"}\n')
    eol = (b'{"product_name":"testos","cycle":"1","eol_date":"2030-01-01",'
           b'"data_source":"test","raw_data":{"k":"v"}}\n')
    manifest = {
        'format_version': 1,
        'bundle_version': '2026.99.0-test',
        'generated_at': '2026-04-28T00:00:00Z',
        'app_compat': {'min': '0.0.1'},
        'datasets': [
            {'name': 'mac_vendor_ranges', 'payload': 'mac_vendor_ranges.jsonl',
             'sha256': hashlib.sha256(mac).hexdigest(), 'row_count': 2,
             'source': 'test', 'apply_strategy': 'truncate_replace'},
            {'name': 'os_eol_data', 'payload': 'os_eol_data.jsonl',
             'sha256': hashlib.sha256(eol).hexdigest(), 'row_count': 1,
             'source': 'test', 'apply_strategy': 'upsert_by_product_cycle'},
        ],
    }
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for name, data in (('manifest.json', json.dumps(manifest).encode()),
                           ('mac_vendor_ranges.jsonl', mac),
                           ('os_eol_data.jsonl', eol)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(out.getvalue())


class _StubMetrics:
    class _G:
        def labels(self, **_kw): return self
        def set(self, *_a, **_kw): pass
        def set_to_current_time(self): pass
    reference_data_records_total = _G()
    reference_data_last_updated_timestamp = _G()


@pytest.fixture
def service():
    db_url = os.getenv('DATABASE_URL', 'postgresql://test:test@localhost:5434/assetatlas_test')
    cm = PostgreSQLConnectionManager(db_url)
    adapter = PostgreSQLAdapter(connection_manager=cm)
    repo = ReferenceDataRepository(adapter)
    svc = ReferenceDataService(repo, cm, _StubMetrics(), app_version='99.0.0')
    yield svc
    # Clean up sentinel rows so repeated runs are isolated.
    with cm.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM os_eol_data WHERE product_name='testos'")
        conn.commit()


def test_round_trip_apply_lands_rows(service):
    # The mac_vendor apply is TRUNCATE-then-COPY, so post-apply the table
    # contains EXACTLY the bundle's 2 rows (and nothing else).
    result = service.apply_bundle(UploadedBytesSource(_build_bundle()), initiated_by='itest')
    assert result.status == 'success', result.error_message
    with service.cm.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM mac_vendor_ranges WHERE data_source='test'")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT eol_date FROM os_eol_data WHERE product_name='testos' AND cycle='1'")
        row = cur.fetchone()
        assert row is not None
        assert row[0].isoformat() == '2030-01-01'
