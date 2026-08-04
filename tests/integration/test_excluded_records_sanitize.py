# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for excluded-records IP/MAC sanitization (#681 A8).

`excluded_records.ip` is a Postgres ``inet`` column and `.mac` is ``macaddr``. The pipeline
writes raw *rejected* identifiers into them via a single bulk ``execute_values`` page, so before
this fix one unparseable value ('N/A', 'DHCP', garbage) aborted the entire page INSERT and the
background writer swallowed the error — the whole run's excluded-records audit trail was lost.

These tests run against the seeded harness DB (``TEST_DATABASE_URL``) where ``excluded_records``
exists; they are ``-m integration`` so they are excluded from CI and from the default ``pytest``
run, and are executed by the regression orchestrator. They prove the real database behavior, not
just the pure validators (covered by the unit tests).

Modeled on tests/integration/test_scope_rollback.py.
"""

import json
import os
import uuid

import pytest

from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.repositories.excluded_records_repository import (
    ExcludedRecordsRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def adapter():
    url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://harness:harness@localhost:5440/assetatlas_harness",
    )
    cm = PostgreSQLConnectionManager(database_url=url)
    yield PostgreSQLAdapter(cm)


@pytest.fixture
def run_id(adapter):
    """A unique generation_run_id per test, with row cleanup afterwards so the shared
    harness ``excluded_records`` table is left untouched."""
    rid = uuid.uuid4()
    yield rid
    adapter.execute_query(
        "DELETE FROM excluded_records WHERE generation_run_id = %s::uuid",
        (str(rid),),
        fetch=False,
    )


def _rec(**kw):
    """Build a minimal excluded-record dict; exclusion_reason/category are NOT NULL columns."""
    base = {"exclusion_reason": "test", "exclusion_category": "other"}
    base.update(kw)
    return base


def _rows_by_hostname(adapter, rid):
    rows = adapter.execute_query(
        "SELECT hostname, ip, mac, exclusion_details "
        "FROM excluded_records WHERE generation_run_id = %s::uuid",
        (str(rid),),
    )
    return {row["hostname"]: row for row in rows}


def _details(row):
    """jsonb may come back as a parsed dict or as raw text depending on typecaster config."""
    value = row["exclusion_details"]
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def test_invalid_ip_mac_nulled_and_raw_preserved(adapter, run_id):
    """A batch mixing valid identifiers with unparseable ip/mac must persist EVERY row:
    the unparseable values become NULL while their raw text is kept in exclusion_details.
    On pre-fix code the whole page INSERT raises and zero rows are saved."""
    records = [
        _rec(
            hostname="valid-1",
            ip="10.1.1.1",
            mac="00:11:22:33:44:55",
            exclusion_details={"note": "keep me"},
        ),
        _rec(hostname="bad-ip", ip="N/A", exclusion_details={"note": "ip was junk"}),
        _rec(
            hostname="bad-mac", mac="DHCP", exclusion_details={"note": "mac was junk"}
        ),
        _rec(hostname="valid-2", ip="10.1.1.2", mac="aa:bb:cc:dd:ee:ff"),
    ]

    saved = ExcludedRecordsRepository(adapter).save_excluded_records(records, run_id)

    # All four rows survive — the dirty ones are NOT dropped.
    assert saved == 4
    rows = _rows_by_hostname(adapter, run_id)
    assert set(rows) == {"valid-1", "bad-ip", "bad-mac", "valid-2"}

    # Unparseable ip/mac stored as NULL, raw text preserved in exclusion_details.
    assert rows["bad-ip"]["ip"] is None
    assert _details(rows["bad-ip"])["invalid_ip"] == "N/A"
    assert rows["bad-mac"]["mac"] is None
    assert _details(rows["bad-mac"])["invalid_mac"] == "DHCP"

    # Valid identifiers are kept (not collateral damage), original details untouched.
    assert rows["valid-1"]["ip"] is not None
    assert rows["valid-1"]["mac"] is not None
    assert _details(rows["valid-1"]) == {"note": "keep me"}
    assert rows["valid-2"]["ip"] is not None
    assert rows["valid-2"]["mac"] is not None
    assert "invalid_ip" not in _details(rows["valid-2"])
    assert "invalid_mac" not in _details(rows["valid-2"])


def test_per_row_fallback_keeps_good_rows_around_a_mid_batch_bad_row(adapter, run_id):
    """Defense-in-depth: if a value the ip/mac validators don't guard still makes the bulk
    page INSERT fail, the per-row fallback must persist every good row and drop only the
    offending one. The bad row sits in the MIDDLE of the batch, so this only holds with
    SAVEPOINT/ROLLBACK TO SAVEPOINT — a plain conn.rollback() would also discard the good
    rows already inserted before it (and miscount)."""
    records = [
        _rec(hostname="keep-1", ip="10.2.2.1"),
        _rec(hostname="keep-2", ip="10.2.2.2"),
        # excluded_at is a NOT NULL timestamptz the validators never touch; a non-timestamp
        # string passes Python-side but Postgres rejects it at INSERT time.
        _rec(hostname="drop-me", excluded_at="not-a-timestamp"),
        _rec(hostname="keep-3", ip="10.2.2.3"),
    ]

    saved = ExcludedRecordsRepository(adapter).save_excluded_records(records, run_id)

    assert saved == 3
    rows = _rows_by_hostname(adapter, run_id)
    assert set(rows) == {"keep-1", "keep-2", "keep-3"}
    assert "drop-me" not in rows
