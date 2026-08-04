# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that MCP confidence-bucketing surfaces honor the configurable setting.

Two guarantees:
1. Static: no hardcoded 0.8/0.5/0.70/0.45 bucket literals remain in the SQL
   (those were a THIRD independent threshold definition diverging from the
   single source of truth at asset_generation.confidence_threshold_*).
2. Binding-correctness: the configured values actually reach the query params —
   not just that literals are gone but that a non-default config propagates.
"""

import inspect
from unittest.mock import MagicMock, patch

import src.mcp.resources.stats as stats_mod
import src.mcp.tools.monitoring as mon_mod


# ---------------------------------------------------------------------------
# Static guards — catch a missed literal at import time
# ---------------------------------------------------------------------------

def test_stats_has_no_hardcoded_bucket_literals():
    """WHY: 0.8/0.5 was a THIRD threshold definition in stats.py; it must
    read the setting instead of hardcoding the value in SQL."""
    src = inspect.getsource(stats_mod)
    assert ">= 0.8" not in src, "stats.py still contains hardcoded >= 0.8"
    assert ">= 0.5" not in src, "stats.py still contains hardcoded >= 0.5"
    assert "< 0.8" not in src, "stats.py still contains hardcoded < 0.8"
    assert "< 0.5" not in src, "stats.py still contains hardcoded < 0.5"


def test_monitoring_has_no_hardcoded_bucket_literals():
    """WHY: 0.70/0.45 in monitoring.py ignored the shared setting, producing
    different buckets from the dashboard and asset inventory surfaces."""
    src = inspect.getsource(mon_mod)
    # Only the SQL bucketing CASE expressions are forbidden; function-signature
    # defaults (medium: float = 0.45) are fine — they mirror DEFAULT_CONFIDENCE_THRESHOLDS
    # and serve as fallbacks for direct impl calls (e.g. existing smoke tests).
    # The pattern '>= 0.70' / '>= 0.45' uniquely identifies SQL comparators.
    assert ">= 0.70" not in src, "monitoring.py still contains hardcoded >= 0.70 in SQL"
    assert ">= 0.45" not in src, "monitoring.py still contains hardcoded >= 0.45 in SQL"


# ---------------------------------------------------------------------------
# Binding-correctness: stats resource
# ---------------------------------------------------------------------------

def test_stats_summary_binds_configured_thresholds():
    """Verify that a non-default settings_manager value (0.50/0.90) reaches
    the execute_query params dict — not just that SQL literals are gone."""
    # Build a mock settings_manager that returns our test thresholds
    mock_sm = MagicMock()
    mock_sm.get.side_effect = lambda section, key: (
        0.50 if "medium" in key else 0.90
    )

    # Build a mock AppContext whose .settings_manager is our mock
    mock_ctx = MagicMock()
    mock_ctx.settings_manager = mock_sm

    # Build a mock db that returns appropriate rows per call:
    # 1st call = totals (confidence bucketing query)
    # 2nd call = categories (no params)
    # 3rd call = active data sources
    # 4th call = stale assets
    mock_db = MagicMock()
    mock_db.execute_query.side_effect = [
        [{"total_assets": 10, "high_confidence": 5,
          "medium_confidence": 3, "low_confidence": 2,
          "avg_confidence": 0.72}],  # totals (has cl_high/cl_med params)
        [{"category": "Server", "count": 10}],  # categories
        [{"source_count": 2}],                   # active data sources
        [{"stale_count": 0}],                    # stale assets
    ]

    mock_ctx.db = mock_db

    with patch.object(stats_mod, "get_app_context", return_value=mock_ctx):
        # The resource function is the inner function; access via the closure
        # by calling register_resources and then retrieving from a mock FastMCP
        mcp = MagicMock()
        captured = {}

        def fake_resource(uri):
            def decorator(fn):
                captured[uri] = fn
                return fn
            return decorator

        mcp.resource = fake_resource
        stats_mod.register_resources(mcp)

        result = captured["assetatlas://stats/summary"]()

    # Verify the function ran (db was called)
    assert mock_db.execute_query.called

    # Find the call that carried confidence params (the first SELECT on assets)
    calls = mock_db.execute_query.call_args_list
    confidence_call = None
    for call in calls:
        args, kwargs = call
        # execute_query(sql, params) — params is positional arg 2
        if len(args) >= 2 and isinstance(args[1], dict):
            p = args[1]
            if "cl_high" in p or "cl_med" in p:
                confidence_call = p
                break

    assert confidence_call is not None, (
        "No execute_query call found with cl_high/cl_med params. "
        f"Calls were: {calls}"
    )
    assert confidence_call["cl_high"] == 0.90, (
        f"Expected cl_high=0.90, got {confidence_call['cl_high']}"
    )
    assert confidence_call["cl_med"] == 0.50, (
        f"Expected cl_med=0.50, got {confidence_call['cl_med']}"
    )

    # The SQL itself must use placeholders, not literals
    sql_with_params = [a[0][0] for a in calls if len(a[0]) >= 2 and isinstance(a[0][1], dict) and "cl_high" in a[0][1]]
    assert sql_with_params, "No call with cl_high in params found"
    assert "%(cl_high)s" in sql_with_params[0], "SQL does not contain %(cl_high)s placeholder"
    assert "%(cl_med)s" in sql_with_params[0], "SQL does not contain %(cl_med)s placeholder"


# ---------------------------------------------------------------------------
# Binding-correctness: monitoring tool impls
# ---------------------------------------------------------------------------

def _make_dashboard_db():
    """Return a mock db whose execute_query returns plausible rows for
    get_dashboard_impl."""
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"total": 20}],                      # total count
        [{"cnt": 1}],                          # added today
        [{"category": "Server", "count": 10, "percentage": 50}],  # categories
        [{"level": "high", "count": 12, "percentage": 60}],       # confidence
        [],                                    # sources
    ]
    return db


def test_dashboard_impl_passes_configured_thresholds_to_query():
    """WHY: the impl receives medium/high as kwargs and must bind them into
    the confidence CASE query — any drift means a different bucket boundary
    than the caller intended."""
    db = _make_dashboard_db()
    mon_mod.get_dashboard_impl(db, medium=0.50, high=0.90)

    calls = db.execute_query.call_args_list
    # Find the confidence distribution query
    confidence_call = None
    for call in calls:
        args, kwargs = call
        if len(args) >= 2 and isinstance(args[1], dict):
            p = args[1]
            if "cl_high" in p and "cl_med" in p:
                confidence_call = (args[0], p)
                break

    assert confidence_call is not None, (
        "get_dashboard_impl did not pass a params dict with cl_high/cl_med. "
        f"Calls: {calls}"
    )
    sql, params = confidence_call
    assert params["cl_high"] == 0.90
    assert params["cl_med"] == 0.50
    assert "%(cl_high)s" in sql
    assert "%(cl_med)s" in sql


def _make_data_quality_db():
    db = MagicMock()
    # get_data_quality_impl makes these calls in order (no category filter):
    # 1. total count
    # 2. confidence distribution (has cl_high/cl_med params)
    # 3. field coverage
    # 4. stale records
    # 5. per-category confidence ranking
    db.execute_query.side_effect = [
        [{"total": 10}],
        [{"level": "high", "count": 8, "avg_pct": 85}],
        [{"has_hostname": 10, "has_ip": 10, "has_mac": 5, "has_serial": 3,
          "has_os": 9, "has_manufacturer": 7, "has_owner": 2,
          "has_category": 10, "total": 10}],
        [{"stale_30d": 0, "stale_90d": 0, "never_seen": 0}],
        [{"category": "Server", "count": 10, "avg_confidence": 72.5}],
    ]
    return db


def test_data_quality_impl_passes_configured_thresholds_to_query():
    """WHY: same binding guarantee for get_data_quality_impl."""
    db = _make_data_quality_db()
    mon_mod.get_data_quality_impl(db, medium=0.35, high=0.80)

    calls = db.execute_query.call_args_list
    confidence_call = None
    for call in calls:
        args, kwargs = call
        if len(args) >= 2 and isinstance(args[1], dict):
            p = args[1]
            if "cl_high" in p and "cl_med" in p:
                confidence_call = (args[0], p)
                break

    assert confidence_call is not None, (
        "get_data_quality_impl did not pass a params dict with cl_high/cl_med. "
        f"Calls: {calls}"
    )
    sql, params = confidence_call
    assert params["cl_high"] == 0.80
    assert params["cl_med"] == 0.35
    assert "%(cl_high)s" in sql
    assert "%(cl_med)s" in sql
