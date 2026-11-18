# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for data retention service."""


def test_alert_deliveries_registered_for_age_based_retention():
    """Verify alert_deliveries is registered for age-based retention cleanup."""
    from src.modules.data_retention.data_retention_service import (
        RETENTION_TABLES, _ALLOWED_TABLES, _ALLOWED_COLUMNS)
    entry = [t for t in RETENTION_TABLES if t[0] == 'alert_deliveries']
    assert entry == [('alert_deliveries', 'created_at', 'ALERT_DELIVERIES_RETENTION_DAYS')]
    # allowlists derive from RETENTION_TABLES, so the table/column are safe to interpolate.
    assert 'alert_deliveries' in _ALLOWED_TABLES and 'created_at' in _ALLOWED_COLUMNS


# ---------------------------------------------------------------------------
# Hypertables (asset_chronicle) must never be aged out by ctid.
#
# A ctid is unique only within one TimescaleDB chunk. The batched
# ``DELETE … WHERE ctid IN (SELECT ctid … WHERE ts < cutoff LIMIT n)`` used for
# plain tables matches the same ctid in EVERY chunk, so on a hypertable it
# deleted recent rows too: one iteration on a 7-day-chunked table with 90 days
# to keep removed every row, on TimescaleDB 2.17.2 and 2.25.1 alike.
# ---------------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

from src.modules.data_retention.data_retention_service import (  # noqa: E402
    DataRetentionService,
)


class _FakeAdapter:
    """Answers by SQL shape and records every statement. A MagicMock would
    accept any SQL; this lets a test assert on what was actually sent."""

    DB_CUTOFF = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)

    def __init__(self, *, has_timescaledb=True, time_column='time',
                 dropped_estimate=0, boundary_batches=(), plain_batches=()):
        self.calls = []
        self.has_timescaledb = has_timescaledb
        self.time_column = time_column
        self.dropped_estimate = dropped_estimate
        self._boundary = list(boundary_batches)
        self._plain = list(plain_batches)

    def execute_query(self, query, params=None, fetch=True):
        sql = ' '.join(query.split())
        self.calls.append((sql, params or {}))
        if 'pg_extension' in sql:
            return [{'present': self.has_timescaledb}]
        if 'timescaledb_information.dimensions' in sql:
            return [{'column_name': self.time_column}] if self.time_column else []
        if sql.startswith('SELECT NOW()'):
            return [{'cutoff': self.DB_CUTOFF}]
        if 'timescaledb_information.chunks' in sql:
            return [{'n': self.dropped_estimate}]
        if 'drop_chunks' in sql:
            return [{'drop_chunks': '_timescaledb_internal._hyper_1_1_chunk'}]
        if 'tableoid' in sql:
            return self._boundary.pop(0) if self._boundary else []
        if 'ctid' in sql:
            return self._plain.pop(0) if self._plain else []
        raise AssertionError(f'unexpected SQL: {sql}')


def _svc(adapter):
    return DataRetentionService(adapter, settings_manager=None)


def _deletes(db):
    return [(s, p) for s, p in db.calls if s.startswith('DELETE')]


def test_hypertable_cleanup_never_matches_rows_by_ctid_alone():
    db = _FakeAdapter(dropped_estimate=5000, boundary_batches=[[{'1': 1}] * 40])

    deleted = _svc(db)._cleanup_table('asset_chronicle', 'time', 90)

    assert deleted == 5040
    for sql, _ in _deletes(db):
        # ctid repeats across chunks; only (tableoid, ctid) names one row.
        assert 'WHERE ctid IN' not in sql, sql
        assert '(tableoid, ctid) IN' in sql, sql


def test_hypertable_cleanup_drops_whole_chunks_then_trims_the_boundary():
    db = _FakeAdapter()

    _svc(db)._cleanup_table('asset_chronicle', 'time', 90)

    (drop_sql, drop_params), = [(s, p) for s, p in db.calls if 'drop_chunks' in s]
    (del_sql, del_params), = _deletes(db)
    assert 'older_than => %(cutoff)s' in drop_sql
    # The outer predicate keeps the scan to chunks that can hold expired rows.
    assert del_sql.startswith('DELETE FROM asset_chronicle WHERE time < %(cutoff)s')
    # One cutoff, taken from the database clock, for every step.
    assert drop_params['cutoff'] is _FakeAdapter.DB_CUTOFF
    assert del_params['cutoff'] is _FakeAdapter.DB_CUTOFF
    (cut_sql, cut_params), = [(s, p) for s, p in db.calls if s.startswith('SELECT NOW()')]
    assert cut_params == {'days': 90}


def test_hypertable_boundary_is_trimmed_in_batches_until_short():
    """One statement per batch keeps each inside the pool's statement_timeout."""
    db = _FakeAdapter(boundary_batches=[[{'1': 1}] * 10_000, [{'1': 1}] * 3])

    assert _svc(db)._cleanup_table('asset_chronicle', 'time', 90) == 10_003
    assert len(_deletes(db)) == 2
    assert all(p['batch_size'] == 10_000 for _, p in _deletes(db))


def test_retention_column_that_is_not_the_time_dimension_never_drops_chunks():
    """drop_chunks ages by the partitioning column; with another retention
    column whole chunks could still hold rows inside the window."""
    db = _FakeAdapter(time_column='created_at', boundary_batches=[[{'1': 1}] * 4])

    assert _svc(db)._cleanup_table('asset_chronicle', 'time', 90) == 4
    assert not [s for s, _ in db.calls if 'drop_chunks' in s]
    assert not [s for s, _ in db.calls if 'timescaledb_information.chunks' in s]


def test_plain_table_keeps_the_batched_ctid_delete():
    db = _FakeAdapter(time_column=None, plain_batches=[[{'1': 1}] * 7])

    deleted = _svc(db)._cleanup_table('excluded_records', 'excluded_at', 90)

    assert deleted == 7
    assert not [sql for sql, _ in db.calls if 'drop_chunks' in sql]
    (sql, _), = _deletes(db)
    assert 'WHERE ctid IN' in sql


def test_without_timescaledb_the_hypertable_catalog_is_never_queried():
    """timescaledb_information does not exist without the extension."""
    db = _FakeAdapter(has_timescaledb=False, plain_batches=[[]])

    assert _svc(db)._cleanup_table('asset_chronicle', 'time', 90) == 0
    assert not [sql for sql, _ in db.calls if 'timescaledb_information' in sql]


def test_hypertable_cleanup_error_is_reported_as_failure():
    class _Boom(_FakeAdapter):
        def execute_query(self, query, params=None, fetch=True):
            if 'drop_chunks' in query:
                raise RuntimeError('canceling statement due to statement timeout')
            return super().execute_query(query, params, fetch)

    assert _svc(_Boom())._cleanup_table('asset_chronicle', 'time', 90) == -1
