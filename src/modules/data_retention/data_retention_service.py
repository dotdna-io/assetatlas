# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Data Retention Service for AssetAtlas

Deletes expired rows from database tables in batches based on
configurable retention settings.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000

# Tables to clean up with simple age-based retention.
# Ingestion data (ingestion_batches, raw_asset_data, raw_source_data) is handled
# separately by _cleanup_ingestion_batches() with a smarter two-rule policy.
RETENTION_TABLES: List[Tuple[str, str, str]] = [
    ('excluded_records', 'excluded_at', 'EXCLUDED_RECORDS_RETENTION_DAYS'),
    ('asset_chronicle', 'time', 'ASSET_CHRONICLE_RETENTION_DAYS'),
    ('audit_trail', 'timestamp', 'AUDIT_TRAIL_RETENTION_DAYS'),
    ('alert_deliveries', 'created_at', 'ALERT_DELIVERIES_RETENTION_DAYS'),
]

# Allowlists for safe SQL identifier interpolation. ``chat_sessions`` uses
# env-driven retention (not settings_manager) so it isn't in RETENTION_TABLES,
# but still reuses _cleanup_table's batched delete.
_ALLOWED_TABLES = {t[0] for t in RETENTION_TABLES} | {'chat_sessions'}
_ALLOWED_COLUMNS = {t[1] for t in RETENTION_TABLES} | {'updated_at'}

# History tables with smart retention (age OR latest N runs).
# (table, days_setting_key, count_setting_key, run_col, ts_col)
HISTORY_TABLES: List[Tuple[str, str, str, str, str]] = [
    ('assets_history', 'ASSETS_HISTORY_RETENTION_DAYS', 'ASSETS_HISTORY_RETENTION_COUNT',
     'generation_run_id', 'archived_at'),
    ('asset_providers_history', 'ASSET_PROVIDERS_HISTORY_RETENTION_DAYS',
     'ASSET_PROVIDERS_HISTORY_RETENTION_COUNT', 'generation_run_id', 'archived_at'),
    ('alert_events', 'ALERT_EVENTS_RETENTION_DAYS', 'ALERT_EVENTS_RETENTION_COUNT',
     'pipeline_run_id', 'created_at'),
]
_ALLOWED_HISTORY_TABLES = {t[0] for t in HISTORY_TABLES}
_ALLOWED_HISTORY_COLS = {t[3] for t in HISTORY_TABLES} | {t[4] for t in HISTORY_TABLES}

DEFAULT_RETENTION_DAYS = 90
DEFAULT_RETENTION_COUNT = 5


class DataRetentionService:
    """Deletes expired rows from database tables in configurable batches."""

    def __init__(self, db_adapter, settings_manager):
        """
        Initialize the data retention service.

        Args:
            db_adapter: PostgreSQLAdapter instance
            settings_manager: SettingsManager instance
        """
        self.db_adapter = db_adapter
        self.settings_manager = settings_manager

    def run_cleanup(self) -> Dict[str, int]:
        """
        Run retention cleanup across all configured tables.

        Returns:
            Dictionary of {table_name: rows_deleted}.
            A value of -1 indicates an error for that table.
        """
        results = {}

        # Smart batch retention: keep last N days OR latest M per integration
        results['ingestion_batches'] = self._cleanup_ingestion_batches()

        # Smart history retention: keep last N days OR latest M generation runs
        for table, days_key, count_key, run_col, ts_col in HISTORY_TABLES:
            results[table] = self._cleanup_history_table(
                table, days_key, count_key, run_col, ts_col)

        for table, ts_col, setting_key in RETENTION_TABLES:
            retention_days = self.settings_manager.get(
                'retention', setting_key, DEFAULT_RETENTION_DAYS
            )
            results[table] = self._cleanup_table(table, ts_col, retention_days)

        # Session cleanup — uses SessionRepository's own cleanup logic
        results['user_sessions'] = self._cleanup_sessions()

        # Chat session cleanup — cascades to chat_messages, chat_tool_calls,
        # chat_feedback via FK ON DELETE CASCADE on each child table.
        results['chat_sessions'] = self._cleanup_chat_sessions()

        total = sum(v for v in results.values() if v > 0)
        logger.info(
            f"Data retention cleanup complete: {total} total rows deleted "
            f"across {len(results)} tables"
        )

        return results

    def _cleanup_sessions(self) -> int:
        """
        Delete expired and stale inactive sessions.

        Uses SessionRepository.cleanup_expired_sessions() which deletes:
        - Sessions past their expires_at timestamp
        - Inactive sessions unused for 30+ days
        """
        try:
            from src.auth.repositories.session_repository import SessionRepository
            session_repo = SessionRepository(self.db_adapter.conn_manager)
            return session_repo.cleanup_expired_sessions()
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")
            return -1

    def _cleanup_chat_sessions(self) -> int:
        """
        Delete chat sessions older than CHAT_RETENTION_DAYS (default 90 days).

        Reads from env (matching ``CHAT_RETENTION_DAYS`` in
        ``src/api/config.py``) rather than ``settings_manager`` because chat
        retention is not managed through the settings UI. Cascades to
        chat_messages, chat_tool_calls, chat_feedback via ON DELETE CASCADE.
        """
        try:
            retention_days = int(os.getenv('CHAT_RETENTION_DAYS', '90'))
        except ValueError:
            logger.warning(
                "CHAT_RETENTION_DAYS is not a valid integer; defaulting to 90"
            )
            retention_days = 90
        return self._cleanup_table('chat_sessions', 'updated_at', retention_days)

    def _cleanup_ingestion_batches(self) -> int:
        """
        Delete old ingestion data using a two-rule retention policy.

        A batch survives if it matches EITHER rule:
        - Rule 1: Created within the last N days (default 7)
        - Rule 2: Among the latest M batches per integration (default 5)

        Cleans three tables in one pass:
        - ingestion_batches (raw_asset_data cascades via FK)
        - raw_source_data (references ingestion_batches.id but FK is soft)
        - batches / batch_logs (SDK tables, cleaned if non-empty)

        Returns:
            Total rows deleted across all ingestion tables, or -1 on error.
        """
        retention_days = self.settings_manager.get(
            'retention', 'BATCH_RETENTION_DAYS', 7
        )
        retention_count = self.settings_manager.get(
            'retention', 'BATCH_RETENTION_COUNT', DEFAULT_RETENTION_COUNT
        )

        keep_cte = """
            WITH keep AS (
                SELECT id FROM ingestion_batches
                WHERE started_at > NOW() - INTERVAL '1 day' * %(days)s
                UNION
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY integration_id
                               ORDER BY started_at DESC
                           ) AS rn
                    FROM ingestion_batches
                ) ranked
                WHERE rn <= %(count)s
            )
        """
        params = {'days': retention_days, 'count': retention_count}
        total_deleted = 0

        try:
            # 1. Delete raw_source_data for doomed batches (no CASCADE FK)
            result = self.db_adapter.execute_query(
                keep_cte + """
                DELETE FROM raw_source_data
                WHERE batch_id NOT IN (SELECT id FROM keep)
                RETURNING 1
                """,
                params
            )
            rsd_deleted = len(result) if result else 0
            if rsd_deleted > 0:
                logger.info(f"Batch retention: deleted {rsd_deleted} raw_source_data rows")
            total_deleted += rsd_deleted

            # 2. Delete ingestion_batches (raw_asset_data cascades via FK)
            result = self.db_adapter.execute_query(
                keep_cte + """
                DELETE FROM ingestion_batches
                WHERE id NOT IN (SELECT id FROM keep)
                RETURNING id
                """,
                params
            )
            ib_deleted = len(result) if result else 0
            total_deleted += ib_deleted

            # 3. Clean empty SDK tables (batches → batch_logs cascade)
            for table in ('batch_logs', 'batches'):
                result = self.db_adapter.execute_query(
                    f"DELETE FROM {table} RETURNING 1", {}
                )
                sdk_deleted = len(result) if result else 0
                if sdk_deleted > 0:
                    logger.info(f"Batch retention: deleted {sdk_deleted} rows from {table}")
                    total_deleted += sdk_deleted

            if ib_deleted > 0:
                logger.info(
                    f"Batch retention: deleted {ib_deleted} ingestion batches "
                    f"(policy: keep {retention_days} days or latest {retention_count} per integration)"
                )
            else:
                logger.debug(
                    f"Batch retention: no batches to delete "
                    f"(policy: keep {retention_days} days or latest {retention_count} per integration)"
                )

            return total_deleted

        except Exception as e:
            logger.error(f"Batch retention cleanup error: {e}")
            return -1

    def _cleanup_history_table(self, table: str, days_key: str, count_key: str,
                               run_col: str = 'generation_run_id',
                               ts_col: str = 'archived_at') -> int:
        """Delete old history snapshots: keep rows within N days OR among the
        latest M runs (by ``run_col``). Column names are allowlisted so they are
        safe to interpolate.

        A row survives if it matches EITHER rule:
        - Rule 1: ts_col within the last N days
        - Rule 2: run_col among the latest M runs

        Args:
            table: History table name
            days_key: Settings key for retention days
            count_key: Settings key for retention count
            run_col: Run-scoping column name (allowlisted)
            ts_col: Timestamp column name (allowlisted)

        Returns:
            Total rows deleted, or -1 on error.
        """
        if (table not in _ALLOWED_HISTORY_TABLES
                or run_col not in _ALLOWED_HISTORY_COLS
                or ts_col not in _ALLOWED_HISTORY_COLS):
            logger.error(f"Invalid table/column for history retention: {table}.{run_col}/{ts_col}")
            return -1

        retention_days = self.settings_manager.get(
            'retention', days_key, DEFAULT_RETENTION_DAYS
        )
        retention_count = self.settings_manager.get(
            'retention', count_key, DEFAULT_RETENTION_COUNT
        )

        total_deleted = 0

        try:
            # Compute protected runs once — stable throughout cleanup
            keep_rows = self.db_adapter.execute_query(
                f"""
                SELECT {run_col} AS run_id
                FROM {table}
                WHERE {run_col} IS NOT NULL
                GROUP BY {run_col}
                ORDER BY MAX({ts_col}) DESC
                LIMIT %(count)s
                """,
                {'count': retention_count}
            )
            keep_run_ids = [r['run_id'] for r in (keep_rows or [])]

            while True:
                result = self.db_adapter.execute_query(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid IN (
                        SELECT ctid FROM {table}
                        WHERE {ts_col} < NOW() - INTERVAL '1 day' * %(days)s
                          AND ({run_col} IS NULL
                               OR NOT EXISTS (
                                   SELECT 1 FROM unnest(%(keep_run_ids)s::uuid[]) AS k(id)
                                   WHERE k.id = {table}.{run_col}
                               ))
                        LIMIT %(batch_size)s
                    )
                    RETURNING 1
                    """,
                    {'days': retention_days,
                     'keep_run_ids': [str(rid) for rid in keep_run_ids],
                     'batch_size': BATCH_SIZE}
                )
                rows_deleted = len(result) if result else 0
                total_deleted += rows_deleted

                if rows_deleted == 0 or rows_deleted < BATCH_SIZE:
                    break

            if total_deleted > 0:
                logger.info(
                    f"History retention: deleted {total_deleted} rows from {table} "
                    f"(policy: keep {retention_days} days or latest "
                    f"{retention_count} generation runs)"
                )
            else:
                logger.debug(
                    f"History retention: no rows to delete from {table} "
                    f"(policy: keep {retention_days} days or latest "
                    f"{retention_count} generation runs)"
                )

        except Exception as e:
            logger.error(f"History retention cleanup error for {table}: {e}")
            return -1

        return total_deleted

    def _cleanup_table(self, table: str, ts_col: str, retention_days: int) -> int:
        """
        Delete expired rows from a single table in batches.

        Args:
            table: Table name
            ts_col: Timestamp column to filter on
            retention_days: Number of days to retain

        Returns:
            Total number of rows deleted, or -1 on error.
        """
        if table not in _ALLOWED_TABLES or ts_col not in _ALLOWED_COLUMNS:
            logger.error(f"Invalid table/column for retention cleanup: {table}.{ts_col}")
            return -1

        total_deleted = 0

        try:
            time_column = self._hypertable_time_column(table)
            if time_column:
                return self._cleanup_hypertable(
                    table, ts_col, retention_days,
                    drop_whole_chunks=(time_column == ts_col))

            while True:
                result = self.db_adapter.execute_query(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid IN (
                        SELECT ctid FROM {table}
                        WHERE {ts_col} < NOW() - INTERVAL '1 day' * %(days)s
                        LIMIT %(batch_size)s
                    )
                    RETURNING 1
                    """,
                    {'days': retention_days, 'batch_size': BATCH_SIZE}
                )
                rows_deleted = len(result) if result else 0
                total_deleted += rows_deleted

                if rows_deleted == 0 or rows_deleted < BATCH_SIZE:
                    break

            if total_deleted > 0:
                logger.info(
                    f"Retention cleanup: deleted {total_deleted} rows from {table} "
                    f"(retention: {retention_days} days)"
                )
            else:
                logger.debug(
                    f"Retention cleanup: no expired rows in {table} "
                    f"(retention: {retention_days} days)"
                )

        except Exception as e:
            logger.error(f"Retention cleanup error for {table}: {e}")
            return -1

        return total_deleted

    def _hypertable_time_column(self, table: str) -> Optional[str]:
        """The time-partitioning column if ``table`` is a TimescaleDB
        hypertable (asset_chronicle), else None.

        The extension check comes first: ``timescaledb_information`` does not
        exist without it, and querying it would fail the whole cleanup.
        """
        rows = self.db_adapter.execute_query(
            "SELECT EXISTS (SELECT 1 FROM pg_extension "
            "WHERE extname = 'timescaledb') AS present",
            {}
        )
        if not (rows and rows[0].get('present')):
            return None
        rows = self.db_adapter.execute_query(
            """
            SELECT column_name FROM timescaledb_information.dimensions
            WHERE hypertable_schema = 'public' AND hypertable_name = %(table)s
              AND dimension_type = 'Time'
            ORDER BY dimension_number
            LIMIT 1
            """,
            {'table': table}
        )
        return rows[0].get('column_name') if rows else None

    def _cleanup_hypertable(self, table: str, ts_col: str, retention_days: int,
                            drop_whole_chunks: bool) -> int:
        """Age out a hypertable without ever matching rows by ctid alone.

        A ctid is unique only within one chunk, so the plain-table
        ``ctid IN (SELECT ctid … LIMIT n)`` delete matches the same ctid in
        EVERY chunk and removes recent rows along with expired ones.

        When ``ts_col`` is the partitioning column, whole chunks past the
        cutoff are dropped (metadata-only, no dead tuples — the ctid scan of a
        large chronicle never finished inside the pool's 60 s
        statement_timeout). What is left past the cutoff — the one chunk
        straddling it, or everything when ``ts_col`` is some other column —
        is deleted in batches keyed by ``(tableoid, ctid)``, which does name a
        single row; the outer range predicate keeps each batch's scan to the
        chunks that can hold expired rows. The cutoff comes from the database
        clock, once, so no step can disagree about the boundary.

        Returns rows removed; the dropped-chunk share is the planner's row
        estimate (pg_class.reltuples), since counting them exactly would scan
        the very data being discarded.
        """
        cutoff = self.db_adapter.execute_query(
            "SELECT NOW() - INTERVAL '1 day' * %(days)s AS cutoff",
            {'days': retention_days}
        )[0]['cutoff']

        dropped_rows = 0
        dropped_chunks = 0
        if drop_whole_chunks:
            estimate = self.db_adapter.execute_query(
                """
                SELECT COALESCE(SUM(GREATEST(c.reltuples, 0)), 0)::bigint AS n
                FROM timescaledb_information.chunks ch
                JOIN pg_namespace ns ON ns.nspname = ch.chunk_schema
                JOIN pg_class c ON c.relnamespace = ns.oid AND c.relname = ch.chunk_name
                WHERE ch.hypertable_schema = 'public'
                  AND ch.hypertable_name = %(table)s
                  AND ch.range_end <= %(cutoff)s
                """,
                {'table': table, 'cutoff': cutoff}
            )
            dropped_rows = int(estimate[0]['n']) if estimate else 0
            dropped = self.db_adapter.execute_query(
                "SELECT drop_chunks(%(relation)s::regclass, older_than => %(cutoff)s)",
                {'relation': f'public.{table}', 'cutoff': cutoff}
            )
            dropped_chunks = len(dropped or [])

        boundary_rows = 0
        while True:
            result = self.db_adapter.execute_query(
                f"""
                DELETE FROM {table} WHERE {ts_col} < %(cutoff)s
                  AND (tableoid, ctid) IN (
                      SELECT tableoid, ctid FROM {table}
                      WHERE {ts_col} < %(cutoff)s
                      LIMIT %(batch_size)s
                  )
                RETURNING 1
                """,
                {'cutoff': cutoff, 'batch_size': BATCH_SIZE}
            )
            rows_deleted = len(result) if result else 0
            boundary_rows += rows_deleted
            if rows_deleted < BATCH_SIZE:
                break

        logger.info(
            f"Retention cleanup: {table} dropped {dropped_chunks} chunk(s) "
            f"(~{dropped_rows} rows) and deleted {boundary_rows} rows "
            f"(retention: {retention_days} days)"
        )
        return dropped_rows + boundary_rows
