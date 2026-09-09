# SPDX-License-Identifier: AGPL-3.0-or-later
"""assets_history must snapshot unified_values when migration 149 has landed,
and must render the pre-#1427 column lists when it has not (#1405).

`unified_values` is written only by refresh_unified_values after the rebuild:
the pipeline's TRUNCATE + plain INSERT resets any column absent from
bulk_upsert_assets' explicit list to NULL, and the materialize stage refills it
inside the same transaction. Three properties pin that design:

1. Both assets_history INSERT…SELECT statements (archive_assets_to_history and
   archive_all_to_history) carry the column when (and only when) the probe
   finds it on BOTH assets_history and assets — history trend charts bind
   unified expressions to assets_history, but on a database without migration
   149 naming the column would raise UndefinedColumn and fail the persistence
   stage itself. Deploy order must not matter.
2. Column counts on the INSERT and SELECT sides stay equal in both renderings.
3. bulk_upsert_assets' INSERT column list never carries it — adding it there
   would write whatever a caller supplies instead of the stage's computed
   values.

The probe goes through the adapter's execute_query, stubbed here per
(table, column) — so probe SQL never reaches the mock cursor and the archive
statements are captured off it exactly as before. The upsert is a
psycopg2.sql composition rendered with quote_ident stubbed (see
test_category_prediction_persistence.py for the pattern).
"""

from unittest.mock import MagicMock
from uuid import uuid4

import psycopg2.sql
import pytest

from src.database import postgres_adapter
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields.unified import materialize


@pytest.fixture(autouse=True)
def _reset_migration_149_probe_state(monkeypatch):
    """No cross-test leaks of the process-wide probe cache."""
    monkeypatch.setattr(materialize, "_COLUMN_PRESENT", {})


def _adapter(present=("assets_history", "assets")):
    """(adapter, cursor) — `present` names which of the two tables has the
    unified_values column; the probe answers accordingly."""
    adapter = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
    adapter.conn_manager = MagicMock()
    cursor = MagicMock()
    adapter.conn_manager.get_cursor.return_value.__enter__.return_value = (
        cursor, MagicMock(),
    )

    def _probe(sql, params=None, **kwargs):
        return [{"?column?": 1}] if params["t"] in present else []

    adapter.execute_query = MagicMock(side_effect=_probe)
    return adapter, cursor


def _insert_columns(sql):
    inner = sql.split('INSERT INTO assets_history (', 1)[1].split(')', 1)[0]
    return [c.strip() for c in inner.split(',')]


def _select_columns(sql):
    sel = sql.split('SELECT', 1)[1].split('FROM assets', 1)[0]
    return [c.strip() for c in sel.split(',')]


class TestArchiveAssetsToHistory:

    def test_unified_values_is_in_the_insert_column_list(self):
        adapter, cursor = _adapter()
        adapter.archive_assets_to_history(uuid4())
        sql = cursor.execute.call_args_list[0][0][0]
        assert 'unified_values' in _insert_columns(sql)

    def test_unified_values_is_in_the_select_list(self):
        adapter, cursor = _adapter()
        adapter.archive_assets_to_history(uuid4())
        sql = cursor.execute.call_args_list[0][0][0]
        assert 'unified_values' in _select_columns(sql)

    def test_insert_and_select_lists_stay_the_same_width(self):
        adapter, cursor = _adapter()
        adapter.archive_assets_to_history(uuid4())
        sql = cursor.execute.call_args_list[0][0][0]
        assert len(_insert_columns(sql)) == len(_select_columns(sql))

    def test_without_migration_149_the_column_is_left_out_of_both_lists(self):
        adapter, cursor = _adapter(present=())
        adapter.archive_assets_to_history(uuid4())
        sql = cursor.execute.call_args_list[0][0][0]
        assert 'unified_values' not in _insert_columns(sql)
        assert 'unified_values' not in _select_columns(sql)
        assert len(_insert_columns(sql)) == len(_select_columns(sql))

    @pytest.mark.parametrize("present", [("assets_history",), ("assets",)])
    def test_the_column_requires_both_tables(self, present):
        """A half-migrated database (only one table altered) renders the
        pre-#1427 lists too — the SELECT must never name a column the FROM
        table lacks, nor insert into one the target lacks."""
        adapter, cursor = _adapter(present=present)
        adapter.archive_assets_to_history(uuid4())
        sql = cursor.execute.call_args_list[0][0][0]
        assert 'unified_values' not in _insert_columns(sql)
        assert 'unified_values' not in _select_columns(sql)

    def test_the_probe_is_read_once_per_archive_call(self):
        adapter, cursor = _adapter(present=())
        adapter.archive_assets_to_history(uuid4())
        probed = {c[0][1]["t"] for c in adapter.execute_query.call_args_list}
        assert probed == {"assets_history", "assets"}
        adapter.archive_assets_to_history(uuid4())
        # a present answer is cached — but here the columns are absent, so the
        # second call probes again (False is deliberately not cached)
        assert adapter.execute_query.call_count == 4


class TestArchiveAllToHistory:

    def _assets_sql(self, cursor):
        # call order: assets INSERT…SELECT, providers INSERT…SELECT, TRUNCATE
        return cursor.execute.call_args_list[0][0][0]

    def test_unified_values_is_in_the_insert_column_list(self):
        adapter, cursor = _adapter()
        adapter.archive_all_to_history(uuid4())
        assert 'unified_values' in _insert_columns(self._assets_sql(cursor))

    def test_unified_values_is_in_the_select_list(self):
        adapter, cursor = _adapter()
        adapter.archive_all_to_history(uuid4())
        assert 'unified_values' in _select_columns(self._assets_sql(cursor))

    def test_insert_and_select_lists_stay_the_same_width(self):
        adapter, cursor = _adapter()
        adapter.archive_all_to_history(uuid4())
        sql = self._assets_sql(cursor)
        assert len(_insert_columns(sql)) == len(_select_columns(sql))

    def test_without_migration_149_the_column_is_left_out_of_both_lists(self):
        adapter, cursor = _adapter(present=())
        adapter.archive_all_to_history(uuid4())
        sql = self._assets_sql(cursor)
        assert 'unified_values' not in _insert_columns(sql)
        assert 'unified_values' not in _select_columns(sql)
        assert len(_insert_columns(sql)) == len(_select_columns(sql))

    @pytest.mark.parametrize("present", [("assets_history",), ("assets",)])
    def test_the_column_requires_both_tables(self, present):
        adapter, cursor = _adapter(present=present)
        adapter.archive_all_to_history(uuid4())
        sql = self._assets_sql(cursor)
        assert 'unified_values' not in _insert_columns(sql)
        assert 'unified_values' not in _select_columns(sql)

    def test_the_assets_insert_is_the_first_statement(self):
        """Guards the guard: if the call order drifts, the tests above must
        not start passing against the providers statement by accident."""
        adapter, cursor = _adapter()
        adapter.archive_all_to_history(uuid4())
        assert 'INSERT INTO assets_history (' in self._assets_sql(cursor)


class TestBulkUpsertDoesNotCarryIt:

    @pytest.fixture
    def upsert_columns(self, monkeypatch):
        monkeypatch.setattr(psycopg2.sql.ext, "quote_ident", lambda s, ctx: '"%s"' % s)
        captured = {}

        def _capture(cursor, query, values, **kwargs):
            captured['sql'] = query

        monkeypatch.setattr(postgres_adapter.extras, "execute_values", _capture)

        adapter = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
        adapter.conn_manager = MagicMock()
        adapter.conn_manager.get_cursor.return_value.__enter__.return_value = (
            MagicMock(), MagicMock(),
        )
        adapter.bulk_upsert_assets([{'id': 'a1', 'unified_values': {'x': 1}}])
        inner = captured['sql'].split('INSERT INTO assets (', 1)[1].split(')', 1)[0]
        return [c.strip().strip('"') for c in inner.split(',')]

    def test_unified_values_stays_out_of_the_insert_column_list(self, upsert_columns):
        """The TRUNCATE-resets-unlisted-columns rule is deliberate here: the
        rebuild leaves unified_values NULL and the materialize stage refills it,
        so the bag can never carry stale values past a run."""
        assert 'unified_values' not in upsert_columns
