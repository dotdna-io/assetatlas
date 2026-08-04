# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for DashboardRepository.refresh_materialized_views (mocked db).

Why this matters: ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` raises Postgres
errcode 0A000 ("CONCURRENTLY cannot be used when the materialized view is not
populated") on a matview that was created ``WITH NO DATA`` and never refreshed.
On a fresh install ``mv_dashboard_summary`` is exactly that, so the pipeline's
refresh failed every run and -- post-#681-A1 -- that error fired inside the
pipeline's atomic scope and rolled the run back. The refresh must therefore
populate a never-populated view with a plain (non-concurrent) ``REFRESH``, and
use ``CONCURRENTLY`` only once ``pg_class.relispopulated`` is true. (#681 A2)
"""

from unittest.mock import MagicMock

from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository


def _make_db(populated):
    """Build a mock db_adapter for refresh_materialized_views().

    ``populated`` is either a bool/None (applied to every view) or a mapping of
    view name -> bool/None. ``execute_query`` answers the ``relispopulated``
    pre-check from that state -- ``None`` models a matview not found in pg_class
    (pre-check returns no rows) -- and records every REFRESH statement it is
    asked to run (whitespace-normalized) in the returned ``issued`` list.
    """
    issued = []

    def _is_populated(view):
        if isinstance(populated, dict):
            return populated[view]
        return populated

    def exec_q(sql, params=None, **kwargs):
        if "relispopulated" in sql:
            state = _is_populated(params["view"])
            return [] if state is None else [{"relispopulated": state}]
        issued.append(" ".join(sql.split()))
        return None

    db = MagicMock()
    db.execute_query.side_effect = exec_q
    return db, issued


class TestRefreshMaterializedViews:
    def test_unpopulated_view_uses_plain_refresh(self):
        db, issued = _make_db(populated=False)

        result = DashboardRepository(db).refresh_materialized_views()

        # CONCURRENTLY on a never-populated matview raises 0A000, so an
        # unpopulated view must be refreshed with a plain REFRESH.
        assert issued, "expected at least one REFRESH to be issued"
        assert not any("CONCURRENTLY" in sql for sql in issued)
        assert any(
            sql.startswith("REFRESH MATERIALIZED VIEW mv_dashboard_summary")
            for sql in issued
        )
        # The plain refresh still counts as a successful refresh.
        assert "mv_dashboard_summary" in result["refreshed_views"]

    def test_populated_view_uses_concurrently(self):
        db, issued = _make_db(populated=True)

        result = DashboardRepository(db).refresh_materialized_views()

        # Once populated, refresh stays CONCURRENT so it never takes a
        # read-blocking AccessExclusiveLock on the dashboard view.
        assert issued, "expected at least one REFRESH to be issued"
        assert all("CONCURRENTLY" in sql for sql in issued)
        assert "mv_dashboard_summary" in result["refreshed_views"]

    def test_variant_is_chosen_per_view_from_relispopulated(self):
        # Real fresh-install shape: only mv_dashboard_summary is unpopulated
        # (schema-full populates the other three but not this one).
        populated = {
            "mv_dashboard_summary": False,
            "mv_provider_coverage": True,
            "mv_provider_coverage_grouped": True,
            "mv_asset_quality_metrics": True,
        }
        db, issued = _make_db(populated=populated)

        result = DashboardRepository(db).refresh_materialized_views()

        by_view = {sql.split()[-1]: sql for sql in issued}
        # The variant is driven by each view's own relispopulated -- not a
        # hardcoded name or a blanket on/off switch: the one unpopulated view
        # gets a plain REFRESH while the populated ones stay CONCURRENT.
        assert "CONCURRENTLY" not in by_view["mv_dashboard_summary"]
        assert "CONCURRENTLY" in by_view["mv_provider_coverage"]
        assert "CONCURRENTLY" in by_view["mv_provider_coverage_grouped"]
        assert "CONCURRENTLY" in by_view["mv_asset_quality_metrics"]
        assert set(result["refreshed_views"]) == set(populated)

    def test_precheck_with_no_matview_row_falls_back_to_plain_refresh(self):
        # When the pre-check finds no pg_class row (matview never created or
        # dropped -> to_regclass yields NULL -> no rows), ``bool(rows)`` is
        # False, so the code must NOT index rows[0]: it falls through to a plain
        # REFRESH. In production that plain REFRESH then surfaces the real
        # "relation does not exist" via failed_views instead of crashing the
        # whole refresh with an uncaught IndexError (which, post-#681-A1, would
        # roll the pipeline run back -- the very failure A2 prevents).
        db, issued = _make_db(populated=None)

        DashboardRepository(db).refresh_materialized_views()

        assert issued, "expected REFRESH attempts even when the pre-check finds no row"
        assert not any("CONCURRENTLY" in sql for sql in issued)
