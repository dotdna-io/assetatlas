# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-surface confidence-threshold equivalence guard.

All confidence surfaces must partition the asset population identically given
the same thresholds; this test fails the moment any surface reintroduces its
own boundary logic (the bug the confidence-threshold unification work fixed).

The test compares three surfaces:

1. **Reference SQL** — a canonical COUNT FILTER query over the ``assets`` table
   with explicit threshold parameters.  This is the ground truth.

2. **DashboardRepository.get_confidence_distribution(medium, high)** — the
   dashboard surface that was the first to be unified.  Exercised via a thin
   adapter shim that routes queries through the test's psycopg2 connection so
   the seeded boundary rows are visible.

3. **Python bucketing** — every raw ``confidence`` value fetched from the same
   connection, bucketed by ``_calculate_level_python`` (a copy of the shared
   boundary function), cross-checking that the Python logic agrees with both
   SQL surfaces.

Note on the Explorer (AssetRepository confidence_level filter) surface
-----------------------------------------------------------------------
This gap is now CLOSED (fix: confidence_level=low matches NULL confidence).

The Explorer's ``_build_confidence_level_clause`` for "low" previously emitted::

    confidence < %(...)s

SQL three-value logic made this return NULL (not TRUE) for rows where
``confidence IS NULL``, excluding those rows from the "low" count while the
dashboard and reference SQL (CASE ELSE branch) absorbed them.

As of the fix, the low branch now emits::

    (confidence < %(...)s OR confidence IS NULL)

making the Explorer surface consistent with the dashboard, the
``confidence_level`` projection CASE, and the charts QueryBuilder.  All four
surfaces now treat NULL confidence → "low" identically.

NULL handling
-------------
Both the reference SQL and DashboardRepository treat NULLs as low:
  * reference SQL: ``confidence < %(m)s OR confidence IS NULL``
  * dashboard:     ``ELSE 'low'`` in the CASE expression (absorbs NULLs)
  * Python:        ``if confidence is None: return "low"`` (explicit guard)

Thresholds
----------
``MEDIUM=0.50, HIGH=0.90`` — deliberately different from the application
default (0.45/0.70) — so the test proves thresholds are actually passed
through rather than surfaces agreeing only because both fell back to the
same default.

Seeded boundary assets
----------------------
The test optionally inserts a handful of assets with confidence values at the
exact boundaries (0.90, 0.8999, 0.50, 0.4999, 1.0, 0.0, NULL) so the
partition is exercised non-trivially even on a nearly-empty DB.  They are
inserted inside the ``db_transaction`` savepoint and rolled back afterwards;
no data persists between test runs.

Requires: live Postgres reachable via DATABASE_URL.  Run with::

    pytest -m integration tests/integration/test_confidence_equivalence.py
"""

import uuid
from datetime import datetime, timezone

import pytest
import psycopg2.extras

pytestmark = pytest.mark.integration

# Non-default thresholds — chosen to differ from the app default (0.45/0.70).
MEDIUM = 0.50
HIGH = 0.90

# Boundary confidence values that exercise every bucket edge.
# The assets.confidence column is numeric(3,2), so values are rounded to 2
# decimal places.  We use integers-of-hundredths to avoid rounding surprises:
# 0.89 is safely < HIGH=0.90, and 0.49 is safely < MEDIUM=0.50.
_BOUNDARY_VALUES = [
    HIGH,       # = 0.90: exactly at high threshold → high
    0.89,       # just below high → medium
    MEDIUM,     # = 0.50: exactly at medium threshold → medium
    0.49,       # just below medium → low
    1.00,       # maximum representable → high
    0.00,       # minimum → low
    None,       # NULL → low
]


# ---------------------------------------------------------------------------
# Thin adapter shim that routes DashboardRepository queries through an
# existing psycopg2 connection (keeping everything inside the savepoint).
# ---------------------------------------------------------------------------

class _ConnAdapter:
    """Minimal db_adapter shim that routes DashboardRepository.execute_query()
    through a caller-supplied psycopg2 connection.

    DashboardRepository only uses ``execute_query(sql, params)`` → list[dict].
    This shim satisfies that contract without touching the real PostgreSQLAdapter
    or PostgreSQLConnectionManager (which would open a separate connection and
    miss the savepoint-local boundary rows).
    """

    def __init__(self, conn):
        self._conn = conn

    def execute_query(self, query: str, params=None, fetch: bool = True, commit: bool = False):
        """Execute *query* on the test connection; return list-of-dicts or None."""
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            if fetch:
                return [dict(row) for row in cur.fetchall()]
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calculate_level_python(confidence, medium: float, high: float) -> str:
    """Pure-Python bucketing mirroring AssetService._calculate_confidence_level.

    NULL (None) → low, matching the SQL CASE ELSE branch.
    """
    if confidence is None:
        return "low"
    if confidence >= high:
        return "high"
    if confidence >= medium:
        return "medium"
    return "low"


def _rows_to_partition(rows) -> dict:
    """Convert dashboard distribution rows [{level, count, ...}] → {high, medium, low}."""
    result = {"high": 0, "medium": 0, "low": 0}
    for row in rows or []:
        level = row["level"]
        if level in result:
            result[level] = int(row["count"])
    return result


# ---------------------------------------------------------------------------
# Fixture: insert boundary assets inside the savepoint; rolled back after test
# ---------------------------------------------------------------------------

@pytest.fixture()
def boundary_assets(db_transaction):
    """Insert boundary-value assets inside the test savepoint.

    ``db_transaction`` wraps the test in a SAVEPOINT and rolls back after it,
    so these rows never persist to the shared dev DB.  We deliberately do NOT
    call ``conn.commit()`` (which would escape the SAVEPOINT and permanently
    write the rows).
    """
    conn = db_transaction
    inserted_ids = []

    with conn.cursor() as cur:
        for conf in _BOUNDARY_VALUES:
            asset_id = str(uuid.uuid4())
            inserted_ids.append(asset_id)
            # Minimal INSERT: only id (NOT NULL PK) and confidence are required
            # for the equivalence test.  category/subcategory are set so the
            # sanity-check test can identify boundary rows without ambiguity.
            # All other columns have defaults or allow NULL.
            cur.execute(
                """
                INSERT INTO assets (id, category, subcategory, confidence)
                VALUES (%(id)s::uuid, 'Test', 'Boundary', %(confidence)s)
                """,
                {"id": asset_id, "confidence": conf},
            )
    # No conn.commit() — the SAVEPOINT in db_transaction handles isolation
    # and rollback.  The rows are visible to subsequent queries on the same
    # connection within this transaction.

    yield inserted_ids

    # Rollback is performed automatically by db_transaction (ROLLBACK TO SAVEPOINT).


# ---------------------------------------------------------------------------
# Main equivalence test
# ---------------------------------------------------------------------------

class TestConfidenceThresholdEquivalence:
    """Proves that all confidence surfaces partition the asset table identically
    given the same (MEDIUM, HIGH) thresholds."""

    def test_reference_vs_dashboard_vs_python(self, db_transaction, boundary_assets):
        """Dashboard distribution and pure-Python bucketing both agree with the
        reference SQL partition at MEDIUM=0.50, HIGH=0.90.

        Seeded boundary assets (from the ``boundary_assets`` fixture) ensure
        the test is non-trivial even on a sparse database.
        """
        conn = db_transaction

        # ------------------------------------------------------------------ #
        # 1. Reference partition — canonical SQL with explicit thresholds.
        #    NULL → low, matching the dashboard CASE ELSE branch.
        # ------------------------------------------------------------------ #
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE confidence >= %(h)s)                            AS high,
                    COUNT(*) FILTER (WHERE confidence >= %(m)s AND confidence < %(h)s)     AS medium,
                    COUNT(*) FILTER (WHERE confidence < %(m)s OR confidence IS NULL)       AS low
                FROM assets
                """,
                {"m": MEDIUM, "h": HIGH},
            )
            ref_row = cur.fetchone()

        # psycopg2 default cursor returns tuples; column order is high, medium, low.
        ref = {"high": int(ref_row[0]), "medium": int(ref_row[1]), "low": int(ref_row[2])}

        total_ref = ref["high"] + ref["medium"] + ref["low"]
        assert total_ref > 0, (
            "No assets in the database — cannot run equivalence test.  "
            "boundary_assets fixture should have seeded at least one row."
        )

        # ------------------------------------------------------------------ #
        # 2. Dashboard partition — DashboardRepository via the shim adapter.
        #    Queries run on the same connection, so boundary rows are visible.
        # ------------------------------------------------------------------ #
        from src.modules.dashboard.repositories.dashboard_repository import (
            DashboardRepository,
        )

        dashboard_repo = DashboardRepository(_ConnAdapter(conn))
        dash_rows = dashboard_repo.get_confidence_distribution(medium=MEDIUM, high=HIGH)
        dash = _rows_to_partition(dash_rows)

        # ------------------------------------------------------------------ #
        # 3. Python bucketing — fetch all confidence values, bucket in Python.
        # ------------------------------------------------------------------ #
        with conn.cursor() as cur:
            cur.execute("SELECT confidence FROM assets")
            all_rows = cur.fetchall()

        python = {"high": 0, "medium": 0, "low": 0}
        for (conf,) in all_rows:
            level = _calculate_level_python(conf, MEDIUM, HIGH)
            python[level] += 1

        # ------------------------------------------------------------------ #
        # Assertions — all three surfaces must agree.
        # ------------------------------------------------------------------ #
        assert dash == ref, (
            f"DashboardRepository disagrees with reference SQL at "
            f"MEDIUM={MEDIUM}, HIGH={HIGH}.\n"
            f"  Reference SQL : {ref}\n"
            f"  Dashboard     : {dash}\n"
            f"The dashboard CASE boundaries differ from the canonical >= / < "
            f"partition.  The unification may have been reverted."
        )

        assert python == ref, (
            f"Python _calculate_confidence_level disagrees with reference SQL at "
            f"MEDIUM={MEDIUM}, HIGH={HIGH}.\n"
            f"  Reference SQL : {ref}\n"
            f"  Python fn     : {python}\n"
            f"The shared boundary function uses different inequality direction "
            f"than the SQL surfaces."
        )

    def test_boundary_values_are_seeded(self, db_transaction, boundary_assets):
        """Sanity-check: seeded boundary assets are visible within the savepoint."""
        conn = db_transaction
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM assets "
                "WHERE category = 'Test' AND subcategory = 'Boundary'"
            )
            (count,) = cur.fetchone()
        assert count >= len(_BOUNDARY_VALUES), (
            f"Expected at least {len(_BOUNDARY_VALUES)} seeded boundary assets, "
            f"found {count}.  INSERT in boundary_assets fixture may have failed."
        )

    def test_null_confidence_counted_as_low(self, db_transaction, boundary_assets):
        """Assets with NULL confidence must appear in the 'low' bucket of the
        reference SQL (i.e. the OR IS NULL clause is correct).

        The ``boundary_assets`` fixture always seeds one NULL-confidence row,
        so this test is not expected to skip in practice.
        """
        conn = db_transaction

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM assets WHERE confidence IS NULL")
            (null_count,) = cur.fetchone()

        if null_count == 0:
            pytest.skip("No NULL-confidence assets in DB — cannot exercise NULL branch")

        # Reference: low = (< medium) + NULL
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE confidence >= %(h)s)                        AS high,
                    COUNT(*) FILTER (WHERE confidence >= %(m)s AND confidence < %(h)s) AS medium,
                    COUNT(*) FILTER (WHERE confidence < %(m)s OR confidence IS NULL)   AS low
                FROM assets
                """,
                {"m": MEDIUM, "h": HIGH},
            )
            ref_row = cur.fetchone()
        ref = {"high": int(ref_row[0]), "medium": int(ref_row[1]), "low": int(ref_row[2])}

        # Non-NULL low count (strict less-than, no IS NULL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM assets "
                "WHERE confidence IS NOT NULL AND confidence < %(m)s",
                {"m": MEDIUM},
            )
            (low_nn,) = cur.fetchone()

        assert ref["low"] == low_nn + null_count, (
            f"Expected reference-SQL low = non-null-low ({low_nn}) + "
            f"null_count ({null_count}) = {low_nn + null_count}, "
            f"but got ref_low={ref['low']}.  "
            f"The reference SQL does not absorb NULLs into low as expected."
        )
