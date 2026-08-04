# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration: posture coverage-trend repo SQL against a live DB.

Proves the snapshot carry-forward (sparse snapshots → continuous daily series),
the SUM across multiple asset categories, and the live per-control counting from
assets.posture_rules. All work happens inside a SAVEPOINT and is rolled back.

Requires: live Postgres via DATABASE_URL.  Run with:
    pytest -m integration tests/integration/test_coverage_trend.py
"""
import uuid

import pytest
import psycopg2.extras

pytestmark = pytest.mark.integration

_DDL = """
CREATE TABLE IF NOT EXISTS posture_coverage_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    snapshot_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    control_id     TEXT NOT NULL,
    asset_category TEXT NOT NULL,
    pass_count     INTEGER NOT NULL,
    fail_count     INTEGER NOT NULL,
    na_count       INTEGER NOT NULL
)
"""


class _ConnAdapter:
    """Routes DashboardRepository.execute_query() through the test connection."""

    def __init__(self, conn):
        self._conn = conn

    def execute_query(self, query, params=None, fetch=True, commit=False):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            if fetch:
                return [dict(r) for r in cur.fetchall()]
            return None


def _repo(conn):
    from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository
    return DashboardRepository(_ConnAdapter(conn))


def test_carry_forward_fills_days_between_sparse_snapshots(db_transaction):
    conn = db_transaction
    with conn.cursor() as cur:
        cur.execute(_DDL)
        # snapshot A: 5 days ago — 10 covered of 100; snapshot B: 2 days ago — 30/100
        cur.execute(
            """
            INSERT INTO posture_coverage_snapshots
                (snapshot_at, control_id, asset_category, pass_count, fail_count, na_count)
            VALUES
                (now() - interval '5 days', 'secured', 'server', 10, 90, 0),
                (now() - interval '2 days', 'secured', 'server', 30, 70, 0)
            """,
        )

    rows = _repo(conn).get_coverage_trend('secured', ['server'], days=7)

    assert len(rows) == 7
    # last point reflects snapshot B (carried forward to today)
    assert rows[-1]['added'] == 30
    assert rows[-1]['removed'] == 70
    # a day inside [A, B) reflects snapshot A; the very first day (>5d ago) is 0
    assert rows[0]['added'] == 0 and rows[0]['removed'] == 0
    assert any(r['added'] == 10 and r['removed'] == 90 for r in rows)


def test_sums_across_multiple_asset_categories(db_transaction):
    conn = db_transaction
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute(
            """
            INSERT INTO posture_coverage_snapshots
                (snapshot_at, control_id, asset_category, pass_count, fail_count, na_count)
            VALUES
                (now(), 'vulnerability_scanning', 'server',      5, 5, 0),
                (now(), 'vulnerability_scanning', 'workstation', 7, 3, 0),
                (now(), 'vulnerability_scanning', 'network',     1, 9, 0),
                (now(), 'vulnerability_scanning', 'mobile',      0, 0, 50)
            """,
        )

    rows = _repo(conn).get_coverage_trend(
        'vulnerability_scanning', ['server', 'workstation', 'network'], days=1,
    )
    # covered = 5+7+1 = 13; total = (5+5)+(7+3)+(1+9) = 30 → without = 17.
    # mobile is excluded (not in the metric scope).
    assert rows[-1]['added'] == 13
    assert rows[-1]['removed'] == 17


def test_live_current_counts_from_posture_rules(db_transaction):
    conn = db_transaction
    ids = [str(uuid.uuid4()) for _ in range(3)]
    with conn.cursor() as cur:
        # two covered servers (secured=true), one uncovered (secured=false)
        cur.execute(
            "INSERT INTO assets (id, category, posture_rules) VALUES "
            "(%s::uuid, 'server', '{\"secured\": {\"state\": \"true\"}}'::jsonb),"
            "(%s::uuid, 'server', '{\"secured\": {\"state\": \"true\"}}'::jsonb),"
            "(%s::uuid, 'server', '{\"secured\": {\"state\": \"false\"}}'::jsonb)",
            ids,
        )

    out = _repo(conn).get_coverage_current('secured', ['server'])
    # at least our 2 covered of 3 seeded; other server rows on the shared DB may add to both
    assert out['covered'] >= 2
    assert out['total'] >= 3
    assert out['total'] >= out['covered']


def test_asset_timeline_and_coverage_trend_share_window_length(db_transaction):
    """Every daily dashboard trend uses the same N-day window (N points), so the
    Trend card's metrics line up on the same x-axis.

    Regression guard for the get_asset_timeline off-by-one that rendered days+1
    points while the posture/coverage trends (generate_series over days-1)
    rendered days.
    """
    conn = db_transaction
    with conn.cursor() as cur:
        cur.execute(_DDL)
    repo = _repo(conn)
    for days in (7, 30, 90):
        timeline = repo.get_asset_timeline(days=days)
        coverage = repo.get_coverage_trend('secured', ['server'], days=days)
        assert len(timeline) == days, f"asset_timeline days={days} -> {len(timeline)} points"
        assert len(coverage) == days, f"coverage_trend days={days} -> {len(coverage)} points"


def test_carry_forward_emptied_category_shows_zero_not_stale(db_transaction):
    """A category that dropped to zero assets must read 0/0, not a stale value.

    Each pipeline run writes ALL of a control's categories in one INSERT sharing
    one snapshot_at, so the carry-forward's inner MAX(snapshot_at) (deliberately
    NOT filtered by asset_category) resolves to the latest run. A category absent
    from that run genuinely had zero assets, so it correctly contributes nothing.

    Regression guard: adding `AND asset_category = ANY(cats)` to the inner MAX
    subquery would instead resurrect the category's last non-empty snapshot
    (here 20/30) — wrong. Keep this test green to prevent that "fix".
    """
    conn = db_transaction
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute(
            """
            INSERT INTO posture_coverage_snapshots
                (snapshot_at, control_id, asset_category, pass_count, fail_count, na_count)
            VALUES
                (now() - interval '2 days', 'secured', 'workstation', 20, 30, 0),
                (now(),                     'secured', 'server',      30, 70, 0)
            """,
        )

    # ws_edr queries the single 'workstation' category for control 'secured'.
    rows = _repo(conn).get_coverage_trend('secured', ['workstation'], days=1)
    # Latest 'secured' batch is today (server-only); workstation is absent → 0/0,
    # NOT the stale 20/30 from two days ago.
    assert rows[-1]['added'] == 0
    assert rows[-1]['removed'] == 0
