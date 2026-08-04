# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-dataset apply logic. Each Dataset is invoked inside an open transaction."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import StringIO

import psycopg2.extras


@dataclass
class DatasetResult:
    name: str
    rows_before: int
    rows_after: int


class Dataset(ABC):
    name: str
    table: str

    @abstractmethod
    def apply(self, cursor, payload: bytes) -> DatasetResult:
        """Apply payload using the given OPEN cursor. Caller manages tx + commit."""

    def _count(self, cursor) -> int:
        cursor.execute(f"SELECT COUNT(*) FROM {self.table}")
        return cursor.fetchone()[0]


class MacVendorDataset(Dataset):
    name = 'mac_vendor_ranges'
    table = 'mac_vendor_ranges'

    COLUMNS = (
        'mac_prefix', 'vendor', 'vendor_short', 'vendor_address',
        'block_type', 'is_private', 'data_source', 'last_updated',
    )

    def apply(self, cursor, payload: bytes) -> DatasetResult:
        rows_before = self._count(cursor)
        cursor.execute(f"TRUNCATE TABLE {self.table}")
        buf = StringIO()
        for line in payload.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            buf.write(self._format_copy_row(row))
        buf.seek(0)
        cursor.copy_expert(
            f"COPY {self.table} ({', '.join(self.COLUMNS)}) FROM STDIN WITH (FORMAT text)",
            buf,
        )
        rows_after = self._count(cursor)
        return DatasetResult(self.name, rows_before, rows_after)

    def _format_copy_row(self, row: dict) -> str:
        parts = []
        for col in self.COLUMNS:
            v = row.get(col)
            if v is None:
                parts.append(r'\N')
            elif isinstance(v, bool):
                parts.append('t' if v else 'f')
            else:
                s = str(v).replace('\\', '\\\\').replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
                parts.append(s)
        return '\t'.join(parts) + '\n'


class OsEolDataset(Dataset):
    name = 'os_eol_data'
    table = 'os_eol_data'

    COLUMNS = (
        'product_name', 'cycle', 'release_label', 'codename',
        'release_date', 'eol_date', 'support_end_date', 'extended_support_date',
        'latest_version', 'latest_release_date', 'lts', 'link',
        'data_source', 'raw_data',
    )

    UPSERT_SQL = """
    INSERT INTO os_eol_data (
        product_name, cycle, release_label, codename,
        release_date, eol_date, support_end_date, extended_support_date,
        latest_version, latest_release_date, lts, link,
        data_source, raw_data, last_synced
    ) VALUES (
        %(product_name)s, %(cycle)s, %(release_label)s, %(codename)s,
        %(release_date)s, %(eol_date)s, %(support_end_date)s, %(extended_support_date)s,
        %(latest_version)s, %(latest_release_date)s, %(lts)s, %(link)s,
        %(data_source)s, %(raw_data)s::jsonb, NOW()
    )
    ON CONFLICT (product_name, cycle) DO UPDATE SET
        release_label = EXCLUDED.release_label,
        codename = EXCLUDED.codename,
        release_date = EXCLUDED.release_date,
        eol_date = EXCLUDED.eol_date,
        support_end_date = EXCLUDED.support_end_date,
        extended_support_date = EXCLUDED.extended_support_date,
        latest_version = EXCLUDED.latest_version,
        latest_release_date = EXCLUDED.latest_release_date,
        lts = EXCLUDED.lts,
        link = EXCLUDED.link,
        data_source = EXCLUDED.data_source,
        raw_data = EXCLUDED.raw_data,
        last_synced = NOW()
    """

    def apply(self, cursor, payload: bytes) -> DatasetResult:
        rows_before = self._count(cursor)
        params_list = []
        for line in payload.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            params = {col: row.get(col) for col in self.COLUMNS}
            if isinstance(params.get('raw_data'), (dict, list)):
                params['raw_data'] = json.dumps(params['raw_data'])
            params_list.append(params)
        if params_list:
            # Pipelined batch — ~10–20× faster than per-row execute on remote DBs
            # because it concatenates many statements into one round-trip per page.
            psycopg2.extras.execute_batch(
                cursor, self.UPSERT_SQL, params_list, page_size=500,
            )
        rows_after = self._count(cursor)
        return DatasetResult(self.name, rows_before, rows_after)


REGISTRY: dict[str, Dataset] = {
    MacVendorDataset.name: MacVendorDataset(),
    OsEolDataset.name: OsEolDataset(),
}
