# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for confidence-threshold unification in the dashboard layer.

WHY: The dashboard must read confidence buckets from the configurable
`asset_generation.confidence_threshold_{medium,high}` setting rather than
hardcoding 0.70/0.45.  These tests guard three invariants:

  1. The repo binds thresholds as SQL parameters (no literals in the query).
  2. No hardcoded literal remains anywhere in the repo module.
  3. The service resolves thresholds from its settings_manager and forwards
     them to the repo so a slider change reaches the dashboard without any
     SQL edits.
"""
import inspect
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. Repo binds params, no literals — get_confidence_distribution
# ---------------------------------------------------------------------------

def test_confidence_distribution_binds_thresholds():
    """WHY: cutoffs must come from the caller (settings) so a slider change
    reaches the dashboard without editing SQL."""
    from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository

    db = MagicMock()
    db.execute_query.return_value = []
    repo = DashboardRepository(db)

    repo.get_confidence_distribution(medium=0.50, high=0.90)

    # execute_query is called with (sql, params) positional args
    args, kwargs = db.execute_query.call_args
    sql = args[0]
    params = args[1]

    assert "0.70" not in sql and "0.45" not in sql, (
        "SQL must not contain hardcoded threshold literals"
    )
    assert params["cl_high"] == 0.90, f"cl_high should be 0.90, got {params.get('cl_high')}"
    assert params["cl_med"] == 0.50, f"cl_med should be 0.50, got {params.get('cl_med')}"


def test_confidence_distribution_defaults_to_standard_thresholds():
    """WHY: callers that don't pass thresholds must still get sensible defaults."""
    from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository
    from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS

    db = MagicMock()
    db.execute_query.return_value = []
    repo = DashboardRepository(db)

    repo.get_confidence_distribution()  # no args

    args, kwargs = db.execute_query.call_args
    params = args[1]
    medium_default, high_default = DEFAULT_CONFIDENCE_THRESHOLDS
    assert params["cl_high"] == high_default
    assert params["cl_med"] == medium_default


# ---------------------------------------------------------------------------
# 2. Whole-file completeness guard
# ---------------------------------------------------------------------------

def test_no_hardcoded_thresholds_remain():
    """WHY: a single missed spot silently re-fragments the definition."""
    import src.modules.dashboard.repositories.dashboard_repository as m

    src_text = inspect.getsource(m)
    assert "0.70" not in src_text, (
        "Found hardcoded 0.70 in dashboard_repository — use %(cl_high)s instead"
    )
    assert "0.45" not in src_text, (
        "Found hardcoded 0.45 in dashboard_repository — use %(cl_med)s instead"
    )


# ---------------------------------------------------------------------------
# 3. Service passes configured thresholds to repo (via get_confidence_distribution)
# ---------------------------------------------------------------------------

def test_service_passes_configured_thresholds():
    """WHY: the service is the only dashboard layer with settings access; if it
    doesn't translate the setting into the repo call, the binding is dead."""
    from src.modules.dashboard.services.dashboard_service import DashboardService

    # Build a mock repo whose view fallback returns None so the live path runs
    repo = MagicMock()
    repo.get_summary_from_view.return_value = None
    repo.get_total_assets_count.return_value = 0
    repo.get_assets_added_today.return_value = 0
    repo.get_category_breakdown.return_value = []
    repo.get_confidence_distribution.return_value = []
    repo.get_integration_stats_grouped.return_value = []
    repo.get_source_coverage.return_value = []
    repo.get_completeness.return_value = 0.0
    repo.get_last_updated_time.return_value = None

    # Settings manager that returns our custom thresholds
    sm = MagicMock()
    sm.get.side_effect = lambda cat, key, default=None: {
        "confidence_threshold_medium": 0.50,
        "confidence_threshold_high": 0.90,
    }[key]

    svc = DashboardService(repo, settings_manager=sm)
    svc.get_summary()

    # Confirm the repo received the configured thresholds as keyword args
    _, kwargs = repo.get_confidence_distribution.call_args
    assert kwargs.get("medium") == 0.50, (
        f"Expected medium=0.50 forwarded to repo, got {kwargs.get('medium')}"
    )
    assert kwargs.get("high") == 0.90, (
        f"Expected high=0.90 forwarded to repo, got {kwargs.get('high')}"
    )


def test_service_passes_configured_thresholds_to_confidence_breakdown():
    """WHY: get_confidence_breakdown is a separate call path — must also honor setting."""
    from src.modules.dashboard.services.dashboard_service import DashboardService

    repo = MagicMock()
    repo.get_confidence_breakdown.return_value = []

    sm = MagicMock()
    sm.get.side_effect = lambda cat, key, default=None: {
        "confidence_threshold_medium": 0.35,
        "confidence_threshold_high": 0.75,
    }[key]

    svc = DashboardService(repo, settings_manager=sm)
    svc.get_confidence_breakdown()

    _, kwargs = repo.get_confidence_breakdown.call_args
    assert kwargs.get("medium") == 0.35
    assert kwargs.get("high") == 0.75
