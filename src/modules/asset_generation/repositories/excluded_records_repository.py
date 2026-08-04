# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Repository for managing excluded records.

This repository handles saving records that were excluded from asset generation,
along with their exclusion reasons and execution metadata.
"""

import logging
import json
import ipaddress
import re
from typing import List, Dict, Any, Optional
from uuid import UUID
import pandas as pd
from datetime import datetime
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


# excluded_records.ip is a Postgres ``inet`` column and .mac is ``macaddr`` (#681 A8). Raw
# rejected identifiers ('N/A', 'DHCP', garbage MACs) must not reach those typed columns, or a
# single unparseable value aborts the whole bulk page insert and the run's audit trail is lost.
# These are strict *parse* checks — "will Postgres accept this as inet/macaddr?" — NOT identity
# validity (virtual/placeholder MACs are valid syntax here) and NOT normalizers (the repo already
# has four divergent MAC impls; we add no fifth). A valid value is returned unchanged; the value
# is parsed EXACTLY as given (no whitespace stripping) so the string the validator approves is the
# string handed to Postgres — anything Postgres would reject (e.g. surrounding whitespace) is None.
_MAC_HEX_RE = re.compile(r'[0-9a-fA-F]{12}')
_MAC_SEP_RE = re.compile(r'[:.\-]')


def _valid_inet(value):
    """Return ``value`` unchanged if it parses as Postgres ``inet`` (IPv4/IPv6, optional
    /CIDR), else None. None passes through as None."""
    if value is None:
        return None
    try:
        ipaddress.ip_interface(str(value))  # accepts '10.0.0.1' and '10.0.0.0/24'
        return value
    except ValueError:
        return None


def _valid_macaddr(value):
    """Return ``value`` unchanged if it parses as a 6-byte MAC (Postgres ``macaddr``), else
    None. Strips the common separators (':', '-', '.') and requires exactly 12 hex digits."""
    if value is None:
        return None
    stripped = _MAC_SEP_RE.sub('', str(value))
    return value if _MAC_HEX_RE.fullmatch(stripped) else None


class ExcludedRecordsRepository:
    """Repository for excluded asset records."""

    def __init__(self, db_adapter):
        """
        Initialize repository.

        Args:
            db_adapter: Database adapter for executing queries
        """
        self.db = db_adapter

    @staticmethod
    def _sanitize_value(value: Any, is_json: bool = False) -> Any:
        """
        Convert pandas NA/NaT to None and dicts to JSON for database compatibility.

        Args:
            value: Value to sanitize
            is_json: If True, convert dicts to JSON strings

        Returns:
            Sanitized value (None if pandas NA/NaT, JSON string if dict and is_json)
        """
        if pd.isna(value):
            return None
        if is_json and isinstance(value, dict):
            from src.database.postgres_adapter import _clean_nan
            return json.dumps(_clean_nan(value))
        return value

    def save_excluded_records(
        self,
        excluded_records: List[Dict[str, Any]],
        generation_run_id: UUID
    ) -> int:
        """
        Save excluded records to the database.

        Args:
            excluded_records: List of excluded record dictionaries
            generation_run_id: UUID of the asset generation run

        Returns:
            Number of records saved
        """
        if not excluded_records:
            logger.info("No excluded records to save")
            return 0

        logger.info(f"Saving {len(excluded_records)} excluded records for run {generation_run_id}")

        # Bulk insert query (positional VALUES %s instead of named placeholders)
        insert_query = """
            INSERT INTO excluded_records (
                generation_run_id, excluded_at, raw_record_id, integration_id,
                raw_source_id, data_source, batch_id, hostname, fqdn, ip, mac, sn,
                os, manufacturer, model, vendor_category, exclusion_reason,
                exclusion_category, has_hostname, has_ip, has_mac, has_sn,
                extended_fields, exclusion_details
            ) VALUES %s
        """

        # Column order must match the INSERT column list above
        column_order = [
            'generation_run_id', 'excluded_at', 'raw_record_id', 'integration_id',
            'raw_source_id', 'data_source', 'batch_id', 'hostname', 'fqdn', 'ip',
            'mac', 'sn', 'os', 'manufacturer', 'model', 'vendor_category',
            'exclusion_reason', 'exclusion_category', 'has_hostname', 'has_ip',
            'has_mac', 'has_sn', 'extended_fields', 'exclusion_details',
        ]

        try:
            # Prepare records for insertion
            records_to_insert = []
            for record in excluded_records:
                # Validate ip/mac against the typed inet/macaddr columns: an unparseable
                # value is stored as NULL but its raw text is preserved in exclusion_details
                # (invalid_ip / invalid_mac) so its raw text is never lost (#681 A8).
                raw_ip = self._sanitize_value(record.get('ip'))
                raw_mac = self._sanitize_value(record.get('mac'))
                clean_ip = _valid_inet(raw_ip)
                clean_mac = _valid_macaddr(raw_mac)
                bad_ip = raw_ip is not None and clean_ip is None
                bad_mac = raw_mac is not None and clean_mac is None
                exclusion_details = record.get('exclusion_details')
                if bad_ip or bad_mac:
                    # Copy onto a fresh dict to stash the raw value(s); guard against a
                    # non-dict exclusion_details (None / NaN / str), which dict() would reject.
                    exclusion_details = (
                        dict(exclusion_details)
                        if isinstance(exclusion_details, dict)
                        else {}
                    )
                    if bad_ip:
                        exclusion_details['invalid_ip'] = str(raw_ip)
                    if bad_mac:
                        exclusion_details['invalid_mac'] = str(raw_mac)

                # Ensure all required fields are present and sanitize pandas NA values
                record_data = {
                    'generation_run_id': str(generation_run_id),
                    'excluded_at': record.get('excluded_at', datetime.utcnow()),
                    'raw_record_id': self._sanitize_value(record.get('raw_record_id')),
                    'integration_id': self._sanitize_value(record.get('integration_id')),
                    'raw_source_id': self._sanitize_value(record.get('raw_source_id')),
                    'data_source': self._sanitize_value(record.get('data_source')),
                    'batch_id': self._sanitize_value(record.get('batch_id')),
                    'hostname': self._sanitize_value(record.get('hostname')),
                    'fqdn': self._sanitize_value(record.get('fqdn')),
                    'ip': clean_ip,
                    'mac': clean_mac,
                    'sn': self._sanitize_value(record.get('sn')),
                    'os': self._sanitize_value(record.get('os')),
                    'manufacturer': self._sanitize_value(record.get('manufacturer')),
                    'model': self._sanitize_value(record.get('model')),
                    'vendor_category': self._sanitize_value(record.get('vendor_category')),
                    'exclusion_reason': record.get('exclusion_reason', 'Unknown'),
                    'exclusion_category': record.get('exclusion_category', 'other'),
                    'has_hostname': record.get('has_hostname', False),
                    'has_ip': record.get('has_ip', False),
                    'has_mac': record.get('has_mac', False),
                    'has_sn': record.get('has_sn', False),
                    'extended_fields': self._sanitize_value(record.get('extended_fields'), is_json=True),
                    'exclusion_details': self._sanitize_value(exclusion_details, is_json=True),
                }
                records_to_insert.append(record_data)

            # Convert list of dicts to list of tuples (ordered to match columns)
            values = [
                tuple(record_data[col] for col in column_order)
                for record_data in records_to_insert
            ]

            # Bulk insert with a per-row fallback so one row Postgres still rejects (a value the
            # ip/mac validators didn't catch) can't abort the whole page and lose the run's audit
            # trail (#681 A8). Recovery uses SAVEPOINTs, never conn.rollback(): every row shares
            # one transaction, so a full rollback would discard the good rows already inserted in
            # this batch (and, were the save ever run inside a pipeline scope, the scope's prior
            # work too). ROLLBACK TO SAVEPOINT undoes only the failed statement and clears the
            # aborted-transaction state; RELEASE then keeps the savepoint stack from growing.
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                try:
                    cursor.execute("SAVEPOINT er_bulk")
                    execute_values(cursor, insert_query, values, page_size=1000)
                    cursor.execute("RELEASE SAVEPOINT er_bulk")
                    inserted_count = len(values)
                except Exception as bulk_err:
                    cursor.execute("ROLLBACK TO SAVEPOINT er_bulk")
                    cursor.execute("RELEASE SAVEPOINT er_bulk")
                    logger.warning(
                        "Bulk excluded-records insert failed (%s); retrying row-by-row so one "
                        "bad row can't drop the rest of the page", bulk_err,
                    )
                    inserted_count = 0
                    for row in values:
                        try:
                            cursor.execute("SAVEPOINT er_row")
                            execute_values(cursor, insert_query, [row], page_size=1)
                            cursor.execute("RELEASE SAVEPOINT er_row")
                            inserted_count += 1
                        except Exception as row_err:
                            cursor.execute("ROLLBACK TO SAVEPOINT er_row")
                            cursor.execute("RELEASE SAVEPOINT er_row")
                            logger.warning(
                                "Dropping one unsalvageable excluded record: %s", row_err,
                            )
                if not self.db.conn_manager.is_scope_active():
                    conn.commit()

            logger.info(f"Successfully saved {inserted_count} excluded records")
            return inserted_count

        except Exception as e:
            logger.error(f"Error saving excluded records: {e}", exc_info=True)
            raise

    def get_excluded_records_by_run(
        self,
        generation_run_id: UUID,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Get excluded records for a specific generation run.

        Args:
            generation_run_id: UUID of the generation run
            limit: Optional limit on number of records

        Returns:
            DataFrame of excluded records
        """
        query = """
            SELECT
                id,
                generation_run_id,
                excluded_at,
                raw_record_id,
                integration_id,
                data_source,
                hostname,
                fqdn,
                ip,
                mac,
                sn,
                os,
                manufacturer,
                model,
                vendor_category,
                exclusion_reason,
                exclusion_category,
                has_hostname,
                has_ip,
                has_mac,
                has_sn,
                extended_fields,
                exclusion_details
            FROM excluded_records
            WHERE generation_run_id = %s
            ORDER BY excluded_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        with self.db.conn_manager.get_cursor() as (cursor, _):
            cursor.execute(query, (str(generation_run_id),))
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

        return pd.DataFrame(rows, columns=columns)

    def get_exclusion_statistics(
        self,
        generation_run_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """
        Get statistics about excluded records.

        Args:
            generation_run_id: Optional UUID to filter by specific run

        Returns:
            Dictionary with exclusion statistics
        """
        where_clause = ""
        params = []

        if generation_run_id:
            where_clause = "WHERE generation_run_id = %s"
            params.append(str(generation_run_id))

        query = f"""
            SELECT
                COUNT(*) as total_excluded,
                COUNT(DISTINCT generation_run_id) as total_runs,
                COUNT(DISTINCT data_source) as total_data_sources,
                COUNT(DISTINCT exclusion_category) as total_categories,
                COUNT(CASE WHEN has_hostname THEN 1 END) as with_hostname,
                COUNT(CASE WHEN has_ip THEN 1 END) as with_ip,
                COUNT(CASE WHEN has_mac THEN 1 END) as with_mac,
                COUNT(CASE WHEN has_sn THEN 1 END) as with_sn
            FROM excluded_records
            {where_clause}
        """

        with self.db.conn_manager.get_cursor() as (cursor, _):
            cursor.execute(query, params)
            row = cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]

        stats = dict(zip(columns, row)) if row else {}

        # Get breakdown by category
        category_query = f"""
            SELECT
                exclusion_category,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM excluded_records
            {where_clause}
            GROUP BY exclusion_category
            ORDER BY count DESC
        """

        with self.db.conn_manager.get_cursor() as (cursor, _):
            cursor.execute(category_query, params)
            category_rows = cursor.fetchall()

        stats['by_category'] = [
            {
                'category': row[0],
                'count': row[1],
                'percentage': float(row[2])
            }
            for row in category_rows
        ]

        # Get breakdown by data source
        source_query = f"""
            SELECT
                data_source,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM excluded_records
            {where_clause}
            GROUP BY data_source
            ORDER BY count DESC
            LIMIT 10
        """

        with self.db.conn_manager.get_cursor() as (cursor, _):
            cursor.execute(source_query, params)
            source_rows = cursor.fetchall()

        stats['by_data_source'] = [
            {
                'data_source': row[0],
                'count': row[1],
                'percentage': float(row[2])
            }
            for row in source_rows
        ]

        return stats

    def get_recent_exclusions(
        self,
        limit: int = 100
    ) -> pd.DataFrame:
        """
        Get most recent excluded records.

        Args:
            limit: Number of records to return

        Returns:
            DataFrame of recent excluded records
        """
        query = f"""
            SELECT
                id,
                generation_run_id,
                excluded_at,
                data_source,
                hostname,
                ip,
                mac,
                sn,
                exclusion_reason,
                exclusion_category
            FROM excluded_records
            ORDER BY excluded_at DESC
            LIMIT {limit}
        """

        with self.db.conn_manager.get_cursor() as (cursor, _):
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

        return pd.DataFrame(rows, columns=columns)
