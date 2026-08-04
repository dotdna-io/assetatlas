# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration tests for PostgreSQLAdapter bulk operations.

Tests bulk_upsert_assets(), bulk_write_chronicle_events(), and NaN handling
against the real database, wrapped in SAVEPOINT/ROLLBACK so no data persists.
"""

import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from psycopg2 import extras

from src.database.postgres_adapter import PostgreSQLAdapter, _clean_nan


# ---------------------------------------------------------------------------
# Helper: thin connection-manager wrapper around a raw psycopg2 connection
# ---------------------------------------------------------------------------

class _SavepointConnManager:
    """Mimics PostgreSQLConnectionManager but reuses a single connection.

    Every call to ``get_cursor()`` / ``get_connection()`` returns the same
    savepointed connection so that the adapter never commits or talks to a
    different connection than the one wrapped by ``db_transaction``.
    """

    def __init__(self, conn):
        self._conn = conn

    # --- scope helpers (no-op; the test fixture controls the savepoint) ---
    def begin_scope(self):
        pass

    def end_scope(self, error=None):
        pass

    def is_scope_active(self):
        return True  # prevent adapter from committing

    # --- connection / cursor providers ---
    @contextmanager
    def get_connection(self, autocommit=False):
        yield self._conn

    @contextmanager
    def get_cursor(self, cursor_factory=None, autocommit=False):
        if cursor_factory is not None:
            cur = self._conn.cursor(cursor_factory=cursor_factory)
        else:
            cur = self._conn.cursor()
        try:
            yield cur, self._conn
        finally:
            cur.close()


@pytest.fixture()
def adapter(db_transaction):
    """Build a PostgreSQLAdapter that uses the savepointed connection."""
    conn = db_transaction
    cm = _SavepointConnManager(conn)
    adp = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
    adp.conn_manager = cm
    return adp


def _make_asset_row(asset_id=None, hostname="integ-host", category="Desktop"):
    """Return a minimal asset dict suitable for bulk_upsert_assets()."""
    now = datetime.now(timezone.utc)
    return {
        "id": str(asset_id or uuid.uuid4()),
        "asset": hostname,
        "hostname": [hostname],
        "fqdn": [f"{hostname}.example.com"],
        "ip": ["10.0.0.1"],
        "mac": ["AA:BB:CC:DD:EE:FF"],
        "sn": ["SN-001"],
        "category": category,
        "subcategory": None,
        "os_normalized": "Windows 11",
        "os": ["Windows 11 23H2"],
        "os_orig": ["Windows 11"],
        "mac_vendor_enriched": None,
        "manufacturer": "Dell",
        "model": "OptiPlex",
        "data_source": ["test"],
        "data_source_category": None,
        "vendor_category": None,
        "confidence": 0.9,
        "confidence_breakdown": {"hostname": 0.9},
        "ipam_location": None,
        "owner": None,
        "location": None,
        "user_field": None,
        "extended_attributes": {},
        "os_enrichment": None,
        "os_eol_status": None,
        "os_eol_date": None,
        "os_support_end_date": None,
        "os_extended_support_date": None,
        "os_is_lts": None,
        "os_eol_checked_at": None,
        "first_time": now,
        "last_time": now,
        "first_seen": now,
        "last_seen": now,
        "last_generation_run_id": None,
    }


# =====================================================================
# Tests
# =====================================================================

@pytest.mark.integration
class TestBulkUpsertAssets:
    """Tests for PostgreSQLAdapter.bulk_upsert_assets()."""

    def test_insert_small_batch(self, adapter):
        """Inserting 3 assets should return count=3 and rows are queryable."""
        ids = [uuid.uuid4() for _ in range(3)]
        assets = [
            _make_asset_row(asset_id=ids[0], hostname="host-a"),
            _make_asset_row(asset_id=ids[1], hostname="host-b"),
            _make_asset_row(asset_id=ids[2], hostname="host-c"),
        ]

        count = adapter.bulk_upsert_assets(assets)
        assert count == 3

        # Verify rows exist
        for aid in ids:
            row = adapter.get_asset_by_id(aid)
            assert row is not None
            assert str(row["id"]) == str(aid)

    def test_upsert_updates_existing(self, adapter):
        """Re-upserting with changed category should update the row."""
        aid = uuid.uuid4()
        asset = _make_asset_row(asset_id=aid, category="Desktop")
        adapter.bulk_upsert_assets([asset])

        row = adapter.get_asset_by_id(aid)
        assert row["category"] == "Desktop"

        # Update category and upsert again
        asset["category"] = "Server"
        adapter.bulk_upsert_assets([asset])

        row = adapter.get_asset_by_id(aid)
        assert row["category"] == "Server"

    def test_empty_list_returns_zero(self, adapter):
        """Passing an empty list should return 0 without error."""
        assert adapter.bulk_upsert_assets([]) == 0

    def test_round_trip_get_asset_by_id(self, adapter):
        """Insert then read back via get_asset_by_id and verify key fields."""
        aid = uuid.uuid4()
        asset = _make_asset_row(asset_id=aid, hostname="roundtrip-host", category="Server")
        adapter.bulk_upsert_assets([asset])

        row = adapter.get_asset_by_id(aid)
        assert row is not None
        assert row["asset"] == "roundtrip-host"
        assert row["category"] == "Server"
        assert row["manufacturer"] == "Dell"
        assert row["confidence"] == pytest.approx(0.9)


@pytest.mark.integration
class TestBulkWriteChronicleEvents:
    """Tests for PostgreSQLAdapter.bulk_write_chronicle_events()."""

    def test_write_chronicle_events(self, adapter, db_transaction):
        """Writing 3 chronicle events should succeed and be queryable."""
        conn = db_transaction
        # First insert an asset so the FK constraint is satisfied
        aid = uuid.uuid4()
        asset = _make_asset_row(asset_id=aid)
        adapter.bulk_upsert_assets([asset])

        now = datetime.now(timezone.utc)
        events = [
            {
                "time": datetime(2099, 1, 1, 0, 0, i, tzinfo=timezone.utc),
                "asset_id": aid,
                "event_type": "asset_created",
                "changed_attributes": ["hostname"],
                "attribute_changes": {"hostname": {"old": None, "new": "integ-host"}},
                "full_snapshot": None,
                "generation_run_id": None,
                "operation_id": None,
                "causation": "test",
                "related_asset_id": None,
            }
            for i in range(3)
        ]

        count = adapter.bulk_write_chronicle_events(events)
        assert count == 3

        # Verify via direct query
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM asset_chronicle WHERE asset_id = %s::uuid ORDER BY time",
            (str(aid),),
        )
        rows = cur.fetchall()
        cur.close()
        assert len(rows) >= 3

    def test_empty_events_returns_zero(self, adapter):
        """Passing an empty list should return 0."""
        assert adapter.bulk_write_chronicle_events([]) == 0


@pytest.mark.integration
class TestNanHandling:
    """Verify _clean_nan prevents invalid JSONB in real DB context."""

    def test_nan_in_confidence_breakdown_replaced(self, adapter):
        """Asset with NaN in confidence_breakdown should insert without error."""
        aid = uuid.uuid4()
        asset = _make_asset_row(asset_id=aid)
        asset["confidence_breakdown"] = {"score": float("nan"), "detail": 0.5}

        # This would fail with a JSON serialization error if NaN isn't cleaned
        adapter.bulk_upsert_assets([asset])

        row = adapter.get_asset_by_id(aid)
        assert row is not None
        breakdown = row["confidence_breakdown"]
        assert breakdown["score"] is None  # NaN replaced with None
        assert breakdown["detail"] == pytest.approx(0.5)

    def test_nan_in_extended_attributes_replaced(self, adapter):
        """Asset with NaN in extended_attributes should insert without error."""
        aid = uuid.uuid4()
        asset = _make_asset_row(asset_id=aid)
        asset["extended_attributes"] = {
            "nested": {"value": float("nan")},
            "list_val": [1.0, float("nan"), 3.0],
        }

        adapter.bulk_upsert_assets([asset])

        row = adapter.get_asset_by_id(aid)
        assert row is not None
        ext = row["extended_attributes"]
        assert ext["nested"]["value"] is None
        assert ext["list_val"] == [1.0, None, 3.0]
