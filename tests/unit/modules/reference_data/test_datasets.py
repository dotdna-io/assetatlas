# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from unittest.mock import patch

from src.modules.reference_data.datasets import MacVendorDataset, OsEolDataset


class FakeCursor:
    def __init__(self, count_sequence):
        self.queries = []
        self.copy_calls = []
        self._counts = list(count_sequence)
        self._last_count = 0

    def execute(self, sql, params=None):
        self.queries.append((sql.strip(), params))
        if 'count' in sql.lower():
            self._last_count = self._counts.pop(0) if self._counts else 0

    def fetchone(self):
        return (self._last_count,)

    def copy_expert(self, sql, file):
        self.copy_calls.append((sql, file.read()))


def test_mac_vendor_apply_truncates_then_loads_jsonl():
    cur = FakeCursor(count_sequence=[42, 3])
    payload = (
        b'{"mac_prefix":"00:00:00","vendor":"Acme","data_source":"ieee"}\n'
        b'{"mac_prefix":"00:00:01","vendor":"Beta","data_source":"ieee"}\n'
        b'{"mac_prefix":"00:00:02","vendor":"Gamma","data_source":"wireshark"}\n'
    )
    ds = MacVendorDataset()
    result = ds.apply(cur, payload)
    assert result.rows_before == 42
    assert result.rows_after == 3
    assert any('TRUNCATE' in q[0].upper() for q in cur.queries)
    assert len(cur.copy_calls) == 1
    assert 'Acme' in cur.copy_calls[0][1]


@patch('src.modules.reference_data.datasets.psycopg2.extras.execute_batch')
def test_os_eol_apply_upserts_by_product_cycle(mock_execute_batch):
    cur = FakeCursor(count_sequence=[10, 12])
    rows = [
        {'product_name': 'windows', 'cycle': '11', 'eol_date': '2031-10-14',
         'data_source': 'endoflife.date', 'raw_data': {'foo': 'bar'}},
        {'product_name': 'macos', 'cycle': '14', 'eol_date': None,
         'data_source': 'endoflife.date', 'raw_data': None},
    ]
    payload = b''.join((json.dumps(r) + '\n').encode() for r in rows)
    ds = OsEolDataset()
    result = ds.apply(cur, payload)
    assert result.rows_after == 12
    mock_execute_batch.assert_called_once()
    args, kwargs = mock_execute_batch.call_args
    assert args[0] is cur
    assert 'INSERT INTO os_eol_data' in args[1]
    assert 'ON CONFLICT' in args[1]
    params_list = args[2]
    assert len(params_list) == 2
    # raw_data dict gets serialized to a JSON string before reaching psycopg2
    assert params_list[0]['raw_data'] == '{"foo": "bar"}'
    assert params_list[1]['raw_data'] is None
