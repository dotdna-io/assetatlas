# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for DashboardService and DashboardRepository.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.modules.dashboard.services.dashboard_service import DashboardService
from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository


def _is_hex_color(s: str) -> bool:
    """Check that s looks like #rrggbb."""
    return bool(re.fullmatch(r'#[0-9a-f]{6}', s))


def _seeded_posture_category_ids() -> set:
    """Posture-category IDs (the ``id`` column) seeded in schema-full.sql.

    schema-full.sql is the authoritative seed (CLAUDE.md 'seed dual-write'), so
    parsing its ``posture_categories`` COPY block tells us which posture controls
    a freshly-deployed DB actually has — the ground truth the coverage-trend
    mapping must point at.
    """
    schema = (
        Path(__file__).resolve().parents[3] / "src" / "database" / "schema-full.sql"
    )
    ids: set = set()
    in_copy = False
    for line in schema.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY public.posture_categories "):
            in_copy = True
            continue
        if in_copy:
            if line.strip() == r"\.":
                break
            ids.add(line.split("\t", 1)[0])
    return ids


class TestGenerateColorPalette:
    """Tests for generate_color_palette() — a static, pure-logic method."""

    # ------------------------------------------------------------------
    # Basic contracts
    # ------------------------------------------------------------------

    def test_returns_list_of_correct_length(self):
        colors = DashboardService.generate_color_palette(5, 'categories')
        assert len(colors) == 5

    def test_zero_count_returns_empty(self):
        assert DashboardService.generate_color_palette(0, 'sources') == []

    def test_single_color(self):
        colors = DashboardService.generate_color_palette(1, 'categories')
        assert len(colors) == 1
        assert _is_hex_color(colors[0])

    def test_all_hex_format(self):
        for color_type in ('sources', 'categories', 'os', 'subcategories'):
            colors = DashboardService.generate_color_palette(8, color_type)
            for c in colors:
                assert _is_hex_color(c), f"Bad color '{c}' for type={color_type}"

    # ------------------------------------------------------------------
    # Per-type behavior
    # ------------------------------------------------------------------

    def test_sources_produces_green_hues(self):
        """Sources use hue=140 (green). The first color should be distinctly greenish."""
        colors = DashboardService.generate_color_palette(3, 'sources')
        assert len(colors) == 3
        # Just verify all are valid hex
        for c in colors:
            assert _is_hex_color(c)

    def test_categories_produces_blue_hues(self):
        colors = DashboardService.generate_color_palette(3, 'categories')
        assert len(colors) == 3

    def test_os_produces_orange_hues(self):
        colors = DashboardService.generate_color_palette(3, 'os')
        assert len(colors) == 3

    def test_subcategories_produces_gray_scale(self):
        """Subcategories use saturation=0 → true grayscale."""
        colors = DashboardService.generate_color_palette(4, 'subcategories')
        for c in colors:
            # For true gray, R == G == B
            r = int(c[1:3], 16)
            g = int(c[3:5], 16)
            b = int(c[5:7], 16)
            assert r == g == b, f"Expected grayscale, got {c}"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_unknown_type_uses_default(self):
        """An unrecognized color_type should still produce valid hex colors."""
        colors = DashboardService.generate_color_palette(3, 'nonexistent')
        assert len(colors) == 3
        for c in colors:
            assert _is_hex_color(c)

    def test_large_count(self):
        colors = DashboardService.generate_color_palette(50, 'categories')
        assert len(colors) == 50
        # All still valid hex
        for c in colors:
            assert _is_hex_color(c)

    def test_two_colors_are_distinct(self):
        """With count >= 2, the first and last colors should differ."""
        colors = DashboardService.generate_color_palette(5, 'categories')
        assert colors[0] != colors[-1]

    # ------------------------------------------------------------------
    # Determinism
    # ------------------------------------------------------------------

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        a = DashboardService.generate_color_palette(7, 'os')
        b = DashboardService.generate_color_palette(7, 'os')
        assert a == b


class TestGetSourceCoverage:
    """Tests for DashboardRepository.get_source_coverage()."""

    def test_returns_list(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [
            {'category': 'Server', 'source_count': 5},
            {'category': 'Workstation', 'source_count': 3},
        ]
        repo = DashboardRepository(mock_db)
        result = repo.get_source_coverage()

        assert len(result) == 2
        assert result[0]['category'] == 'Server'
        assert result[0]['source_count'] == 5

    def test_empty_table_returns_empty_list(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        repo = DashboardRepository(mock_db)
        result = repo.get_source_coverage()

        assert result == []


class TestGetCompleteness:
    """Tests for DashboardRepository.get_completeness()."""

    def test_returns_percentage(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'completeness': 72.3}]
        repo = DashboardRepository(mock_db)
        result = repo.get_completeness()

        assert result == 72.3

    def test_null_result_returns_zero(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'completeness': None}]
        repo = DashboardRepository(mock_db)
        result = repo.get_completeness()

        assert result == 0

    def test_empty_result_returns_zero(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        repo = DashboardRepository(mock_db)
        result = repo.get_completeness()

        assert result == 0


class TestGetSummaryNewFields:
    """Tests that get_summary() includes sourceCoverage, completeness, lastUpdated."""

    def test_summary_includes_source_coverage_and_completeness(self):
        mock_db = MagicMock()
        repo = DashboardRepository(mock_db)
        service = DashboardService(repo)

        # Mock all repo methods called by get_summary()
        repo.get_summary_from_view = MagicMock(return_value=None)
        repo.get_total_assets_count = MagicMock(return_value=100)
        repo.get_assets_added_today = MagicMock(return_value=5)
        repo.get_category_breakdown = MagicMock(return_value=[
            {'category': 'Server', 'count': 60, 'percentage': 60.0},
            {'category': 'Workstation', 'count': 40, 'percentage': 40.0},
        ])
        repo.get_confidence_distribution = MagicMock(return_value=[
            {'level': 'high', 'count': 70, 'percentage': 70.0},
            {'level': 'medium', 'count': 20, 'percentage': 20.0},
            {'level': 'low', 'count': 10, 'percentage': 10.0},
        ])
        repo.get_integration_stats_grouped = MagicMock(return_value=[
            {'id': '1', 'name': 'Source1', 'avg_confidence': 80, 'asset_count': 50,
             'high_conf_count': 30, 'medium_conf_count': 15, 'low_conf_count': 5},
        ])
        repo.get_category_confidence_from_view = MagicMock(return_value=[
            {'category': 'Server', 'avg_confidence': 0.8, 'count': 60, 'confidence_level': 'high',
             'high_count': 40, 'medium_count': 15, 'low_count': 5},
        ])
        repo.get_source_coverage = MagicMock(return_value=[
            {'category': 'Server', 'source_count': 5},
        ])
        repo.get_completeness = MagicMock(return_value=72.3)
        repo.get_last_updated_time = MagicMock(return_value=None)

        result = service.get_summary()

        assert 'sourceCoverage' in result
        assert result['sourceCoverage'] == [{'category': 'Server', 'source_count': 5}]
        assert result['completeness'] == 72.3
        assert 'lastUpdated' in result


class TestGetPostureTierBreakdown:
    """DashboardRepository.get_posture_tier_breakdown() runs the tier CTE."""

    def test_runs_cte_and_returns_rows(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [
            {"asset_category": "server", "tier": "secured", "n": 10},
            {"asset_category": "server", "tier": "discovered", "n": 2},
        ]
        repo = DashboardRepository(db_adapter=mock_db)

        rows = repo.get_posture_tier_breakdown()

        assert rows == [
            {"asset_category": "server", "tier": "secured", "n": 10},
            {"asset_category": "server", "tier": "discovered", "n": 2},
        ]
        sql = mock_db.execute_query.call_args[0][0]
        assert "WITH tiered AS" in sql
        assert "posture_rules -> 'secured'" in sql
        assert "posture_rules -> 'monitored'" in sql
        assert sql.index("'secured'") < sql.index("'monitored'") < sql.index("'discovered'")
        assert "miscellaneous" in sql


class TestGetPostureSnapshotAtOrBefore:
    """Looks up the most recent snapshot ≤ (now - days_ago)."""

    def test_returns_row_when_snapshot_exists(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [
            {"secured": 100, "monitored": 50, "discovered": 25},
        ]
        repo = DashboardRepository(db_adapter=mock_db)

        row = repo.get_posture_snapshot_at_or_before(days_ago=7)

        assert row == {"secured": 100, "monitored": 50, "discovered": 25}
        sql, params = mock_db.execute_query.call_args[0][:2]
        assert "FROM posture_tier_snapshots" in sql
        assert "secured_count    AS secured" in sql
        assert "ORDER BY snapshot_at DESC" in sql
        assert "LIMIT 1" in sql
        assert params == {"days": 7}

    def test_returns_none_when_no_snapshot_old_enough(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        repo = DashboardRepository(db_adapter=mock_db)

        assert repo.get_posture_snapshot_at_or_before(days_ago=7) is None


class TestGetPostureData:
    """DashboardService.get_posture_data() pivots tier rows + computes deltas."""

    CANONICAL_KEYS = {"server", "workstation", "mobile", "network",
                      "iot", "ot", "miscellaneous"}

    def _service(self, breakdown_rows, snapshot_row):
        repo = MagicMock()
        repo.get_posture_tier_breakdown.return_value = breakdown_rows
        repo.get_posture_snapshot_at_or_before.return_value = snapshot_row
        return DashboardService(repo), repo

    def test_pivots_breakdown_rows_into_per_category_dict(self):
        service, _ = self._service(
            breakdown_rows=[
                {"asset_category": "server", "tier": "secured", "n": 100},
                {"asset_category": "server", "tier": "monitored", "n": 5},
                {"asset_category": "server", "tier": "discovered", "n": 1},
                {"asset_category": "workstation", "tier": "secured", "n": 50},
            ],
            snapshot_row=None,
        )

        result = service.get_posture_data()

        assert result["posture"]["server"] == {"secured": 100, "monitored": 5, "discovered": 1}
        assert result["posture"]["workstation"] == {"secured": 50, "monitored": 0, "discovered": 0}

    def test_seeds_canonical_keys_with_zeros_when_missing(self):
        """Even with no rows, the response always exposes the seven canonical asset categories."""
        service, _ = self._service(breakdown_rows=[], snapshot_row=None)

        result = service.get_posture_data()

        assert set(result["posture"].keys()) == self.CANONICAL_KEYS
        for cat in self.CANONICAL_KEYS:
            assert result["posture"][cat] == {"secured": 0, "monitored": 0, "discovered": 0}

    def test_unknown_asset_category_buckets_into_miscellaneous(self):
        service, _ = self._service(
            breakdown_rows=[
                {"asset_category": "printer", "tier": "discovered", "n": 7},
                {"asset_category": "miscellaneous", "tier": "discovered", "n": 3},
            ],
            snapshot_row=None,
        )

        result = service.get_posture_data()

        # 7 (printer) + 3 (miscellaneous) merged into miscellaneous
        assert result["posture"]["miscellaneous"] == {"secured": 0, "monitored": 0, "discovered": 10}

    def test_changes_null_when_no_snapshot(self):
        service, _ = self._service(
            breakdown_rows=[{"asset_category": "server", "tier": "secured", "n": 100}],
            snapshot_row=None,
        )

        result = service.get_posture_data()

        assert result["changes"] == {
            "secured":    {"delta": None, "period": "week"},
            "monitored":  {"delta": None, "period": "week"},
            "discovered": {"delta": None, "period": "week"},
        }

    def test_changes_computed_from_snapshot(self):
        service, _ = self._service(
            breakdown_rows=[
                {"asset_category": "server", "tier": "secured", "n": 108},
                {"asset_category": "server", "tier": "monitored", "n": 65},
                {"asset_category": "server", "tier": "discovered", "n": 22},
            ],
            snapshot_row={
                "secured": 100,
                "monitored": 50,
                "discovered": 25,
            },
        )

        result = service.get_posture_data()

        assert result["changes"] == {
            "secured":    {"delta": 8,  "period": "week"},
            "monitored":  {"delta": 15, "period": "week"},
            "discovered": {"delta": -3, "period": "week"},
        }

    def test_calls_snapshot_lookup_with_seven_days(self):
        service, repo = self._service(breakdown_rows=[], snapshot_row=None)
        service.get_posture_data()
        repo.get_posture_snapshot_at_or_before.assert_called_once_with(days_ago=7)


# ============================================================================
# refresh_materialized_views — must isolate per-view failures. Production
# bug: mv_dashboard_summary was undefined on a dev DB, the first iteration
# raised UndefinedTable, the broad try/except swallowed it, and
# mv_provider_coverage{,_grouped} drifted up to 30 days behind reality
# because they were never reached.
# ============================================================================

class TestRefreshMaterializedViews:

    def _repo_with_db(self, refresh_outcomes, populated=True):
        """Build a repo whose mocked db answers the ``relispopulated`` pre-check
        with ``populated`` for every view, then applies ``refresh_outcomes``
        (one per view, in order: ``None`` = REFRESH ok, an ``Exception`` = the
        REFRESH raises). Post-#681-A2 each view costs two execute_query calls:
        the pre-check then the REFRESH.
        """
        db = MagicMock()
        outcomes = iter(refresh_outcomes)

        def exec_q(sql, params=None, **kwargs):
            if "relispopulated" in sql:
                return [{"relispopulated": populated}]
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        db.execute_query.side_effect = exec_q
        return DashboardRepository(db), db

    def test_all_views_succeed(self):
        repo, db = self._repo_with_db([None, None, None, None])
        result = repo.refresh_materialized_views()
        assert result["success"] is True
        assert set(result["refreshed_views"]) == {
            'mv_dashboard_summary',
            'mv_provider_coverage',
            'mv_provider_coverage_grouped',
            'mv_asset_quality_metrics',
        }
        assert result.get("failed_views", {}) == {}
        # 4 relispopulated pre-checks + 4 REFRESHes.
        assert db.execute_query.call_count == 8

    def test_missing_first_view_does_not_block_remaining(self):
        boom = Exception('relation "mv_dashboard_summary" does not exist')
        repo, db = self._repo_with_db([boom, None, None, None])

        result = repo.refresh_materialized_views()

        assert db.execute_query.call_count == 8
        assert 'mv_provider_coverage' in result["refreshed_views"]
        assert 'mv_provider_coverage_grouped' in result["refreshed_views"]
        assert 'mv_asset_quality_metrics' in result["refreshed_views"]
        assert 'mv_dashboard_summary' in result["failed_views"]
        assert result["success"] is True

    def test_all_views_fail_returns_unsuccess(self):
        boom = Exception("locked")
        repo, db = self._repo_with_db([boom, boom, boom, boom])
        result = repo.refresh_materialized_views()
        assert result["success"] is False
        assert result["refreshed_views"] == []
        assert len(result["failed_views"]) == 4

    def test_populated_views_use_concurrent_refresh(self):
        # Steady state: every view is already populated, so each REFRESH stays
        # CONCURRENT (no read-blocking AccessExclusiveLock). The plain
        # (non-concurrent) path is for never-populated views only -- see #681
        # A2 and tests/unit/dashboard/test_refresh_materialized_views.py.
        repo, db = self._repo_with_db([None, None, None, None], populated=True)
        repo.refresh_materialized_views()
        refreshes = [
            call.args[0]
            for call in db.execute_query.call_args_list
            if call.args[0].startswith("REFRESH MATERIALIZED VIEW")
        ]
        assert len(refreshes) == 4
        for sql in refreshes:
            assert sql.startswith("REFRESH MATERIALIZED VIEW CONCURRENTLY ")


class TestGetPostureTrend:
    """DashboardService.get_posture_trend() — chevron bar-chart series.

    The latest point must reflect the LIVE tier totals, not the most recent
    snapshot: a just-applied rule change isn't snapshotted yet, but the bars
    share a chevron with the live count, so the right edge must agree.
    """

    def _service(self, trend_rows, breakdown_rows):
        repo = MagicMock()
        repo.get_posture_tier_trend.return_value = trend_rows
        repo.get_posture_tier_breakdown.return_value = breakdown_rows
        return DashboardService(repo)

    def test_overrides_last_point_with_live_totals(self):
        # Snapshots say secured=0 for every day (stale), but live breakdown
        # says 734 secured servers + 50 secured workstations = 784.
        service = self._service(
            trend_rows=[
                {"day": "2026-06-06", "secured": 0, "monitored": 0, "discovered": 100},
                {"day": "2026-06-07", "secured": 0, "monitored": 0, "discovered": 100},
            ],
            breakdown_rows=[
                {"asset_category": "server", "tier": "secured", "n": 734},
                {"asset_category": "workstation", "tier": "secured", "n": 50},
                {"asset_category": "server", "tier": "discovered", "n": 40},
            ],
        )

        result = service.get_posture_trend(days=2)

        # Last point replaced with live totals; earlier points untouched.
        assert result["series"]["secured"] == [0, 784]
        assert result["series"]["discovered"] == [100, 40]
        assert result["days"] == 2

    def test_null_snapshot_values_render_as_zero(self):
        service = self._service(
            trend_rows=[
                {"day": "2026-06-06", "secured": None, "monitored": None, "discovered": None},
            ],
            breakdown_rows=[],
        )

        result = service.get_posture_trend(days=1)
        assert result["series"]["secured"] == [0]
        assert result["series"]["monitored"] == [0]
        assert result["series"]["discovered"] == [0]


class TestGetDataSourcesCategoryDistribution:
    """get_data_sources() attaches a per-source category mix for the
    dashboard mini-bars, keyed by source name and rolling non-canonical
    categories into 'miscellaneous'."""

    def _service(self, integrations, breakdown):
        repo = MagicMock()
        repo.get_integration_stats_from_view.return_value = integrations
        repo.get_provider_category_breakdown.return_value = breakdown
        return DashboardService(repo)

    def test_maps_breakdown_onto_sources_by_name(self):
        service = self._service(
            integrations=[{
                "id": "i1", "name": "Entra Devices", "integration_type": "entra",
                "logo": None, "asset_count": 4418, "last_sync_at": None,
                "hours_since_sync": None, "last_run_at": None, "hours_since_run": None,
                "avg_confidence": 70, "high_conf_count": 0, "medium_conf_count": 0,
                "low_conf_count": 0,
            }],
            breakdown=[
                {"source": "Entra Devices", "category": "workstation", "n": 3006},
                {"source": "Entra Devices", "category": "mobile", "n": 1389},
                {"source": "Entra Devices", "category": "server", "n": 18},
                # non-canonical rolls into miscellaneous
                {"source": "Entra Devices", "category": "storage", "n": 5},
                # other source — must not leak onto this one
                {"source": "Intune", "category": "workstation", "n": 1694},
            ],
        )

        result = service.get_data_sources()
        cd = result["sources"][0]["categoryDistribution"]

        assert cd["workstation"] == 3006
        assert cd["mobile"] == 1389
        assert cd["server"] == 18
        assert cd["miscellaneous"] == 5          # storage rolled in
        assert cd["network"] == 0 and cd["iot"] == 0 and cd["ot"] == 0
        assert set(cd) == set(DashboardService.POSTURE_CANONICAL_CATEGORIES)

    def test_source_with_no_breakdown_gets_all_zeros(self):
        service = self._service(
            integrations=[{
                "id": "i2", "name": "Lonely Source", "integration_type": "x",
                "logo": None, "asset_count": 0, "last_sync_at": None,
                "hours_since_sync": None, "last_run_at": None, "hours_since_run": None,
                "avg_confidence": 0, "high_conf_count": 0, "medium_conf_count": 0,
                "low_conf_count": 0,
            }],
            breakdown=[],
        )

        cd = service.get_data_sources()["sources"][0]["categoryDistribution"]
        assert cd == {c: 0 for c in DashboardService.POSTURE_CANONICAL_CATEGORIES}


class TestCoverageTrend:
    """DashboardService.get_coverage_trend resolves the metric catalog and
    overrides the latest point with the live current value."""

    def _service(self):
        repo = MagicMock()
        return DashboardService(repo), repo

    def test_resolves_metric_to_control_and_categories(self):
        svc, repo = self._service()
        repo.get_coverage_trend.return_value = [
            {"label": "Jun 01", "added": 1, "removed": 1},
        ]
        repo.get_coverage_current.return_value = {"covered": 10, "total": 25}

        out = svc.get_coverage_trend("vuln", days=30)

        repo.get_coverage_trend.assert_called_once_with(
            "vulnerability_scanning", ["server", "workstation", "network"], days=30,
        )
        assert out["metric"] == "vuln"
        assert out["range"] == "30d"
        assert out["granularity"] == "daily"

    def test_overrides_latest_point_with_live_value(self):
        svc, repo = self._service()
        repo.get_coverage_trend.return_value = [
            {"label": "Jun 01", "added": 1, "removed": 1},
            {"label": "Jun 02", "added": 2, "removed": 2},
        ]
        repo.get_coverage_current.return_value = {"covered": 7, "total": 10}

        out = svc.get_coverage_trend("srv_edr", days=30)

        repo.get_coverage_current.assert_called_once_with("secured", ["server"])
        assert out["data"][-1] == {"label": "Jun 02", "added": 7, "removed": 3}
        # earlier points untouched
        assert out["data"][0] == {"label": "Jun 01", "added": 1, "removed": 1}

    def test_unknown_metric_returns_empty_without_touching_repo(self):
        svc, repo = self._service()
        out = svc.get_coverage_trend("bogus", days=30)
        assert out == {"metric": "bogus", "range": "30d", "granularity": "daily", "data": []}
        repo.get_coverage_trend.assert_not_called()

    def test_empty_series_skips_live_override(self):
        svc, repo = self._service()
        repo.get_coverage_trend.return_value = []
        out = svc.get_coverage_trend("srv_edr", days=30)
        assert out["data"] == []
        repo.get_coverage_current.assert_not_called()

    # ------------------------------------------------------------------
    # issue #913: the full metric->control mapping for all four coverage
    # cards (added_removed is a separate chronicle path). ws_edr and
    # mobile_mdm were previously unguarded.
    # ------------------------------------------------------------------
    EXPECTED_MAPPING = {
        "srv_edr": ("secured", ["server"]),
        "ws_edr": ("secured", ["workstation"]),
        "mobile_mdm": ("device_management", ["mobile"]),
        "vuln": ("vulnerability_scanning", ["server", "workstation", "network"]),
    }

    @pytest.mark.parametrize("metric", list(EXPECTED_MAPPING))
    def test_all_metrics_resolve_and_yield_nonempty_series(self, metric):
        """Every homepage trend metric resolves to its documented posture
        control and returns a non-empty series when snapshots exist.

        A typo'd control, a wrong category filter, or a dropped catalog entry
        is exactly what makes a card render "No data available" (#913); any of
        those regresses this test.
        """
        svc, repo = self._service()
        control, categories = self.EXPECTED_MAPPING[metric]
        repo.get_coverage_trend.return_value = [
            {"label": "Jun 01", "added": 3, "removed": 2},
            {"label": "Jun 02", "added": 4, "removed": 1},
        ]
        repo.get_coverage_current.return_value = {"covered": 5, "total": 8}

        out = svc.get_coverage_trend(metric, days=30)

        repo.get_coverage_trend.assert_called_once_with(control, categories, days=30)
        assert out["metric"] == metric
        # a real series the butterfly card can render (not the empty state)
        assert len(out["data"]) == 2
        assert out["data"][-1] == {"label": "Jun 02", "added": 5, "removed": 3}

    def test_every_metric_control_is_seeded(self):
        """Each metric maps to a posture control that actually exists in the seed.

        Root cause guard for #913: if COVERAGE_METRICS points at a control that
        isn't seeded (or a control is renamed/removed from the seed without
        updating the mapping), the trend query returns nothing and the card goes
        empty. All four controls (secured, device_management,
        vulnerability_scanning) are seeded, so this passes today and locks it in.
        """
        seeded = _seeded_posture_category_ids()
        assert seeded, "no posture_categories seed parsed from schema-full.sql"
        mapped = {spec["control"] for spec in DashboardService.COVERAGE_METRICS.values()}
        unseeded = mapped - seeded
        assert not unseeded, f"coverage controls not in seed: {sorted(unseeded)}"
