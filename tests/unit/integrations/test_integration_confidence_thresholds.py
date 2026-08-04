# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests: per-source confidence distribution must honor the configurable thresholds.

WHY: The integration insights page shows a high/medium/low breakdown of asset
confidence for a data source.  Bucket boundaries previously hardcoded 0.70/0.45
so changing the global setting had no effect.  These tests assert the plumbing
from service → repo → SQL params carries the caller-supplied thresholds all the
way to the query, and that defaults are still safe when nothing is passed.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import uuid


# ---------------------------------------------------------------------------
# Helper: build a mock connection-manager whose cursor records execute() calls
# ---------------------------------------------------------------------------

def _make_mock_repo():
    """Return (repo, captured_calls) where captured_calls is a list that will
    receive (sql, params) tuples from every cur.execute() call."""
    from src.modules.integrations.repositories.integration_repository import IntegrationRepository

    captured = []

    # Mock cursor — records what execute() received then returns a fake row
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = {'high': 0, 'medium': 0, 'low': 0}

    def _capture_execute(sql, params=None):
        captured.append((sql, params))

    mock_cursor.execute = _capture_execute

    # Mock connection — cursor() returns the mock cursor
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Mock connection manager — get_connection() is a context manager yielding conn
    @contextmanager
    def _get_conn():
        yield mock_conn

    mock_cm = MagicMock()
    mock_cm.get_connection = _get_conn

    # Bypass __init__ (needs DATABASE_URL) by injecting the mock directly
    repo = IntegrationRepository.__new__(IntegrationRepository)
    repo._connection_manager = mock_cm

    return repo, captured


# ---------------------------------------------------------------------------
# Test A: repo sends thresholds as named params, not hardcoded literals
# ---------------------------------------------------------------------------

def test_get_confidence_distribution_binds_thresholds():
    """WHY: per-source confidence buckets must come from the setting, not hardcoded SQL."""
    repo, captured = _make_mock_repo()

    repo.get_confidence_distribution("SomeSource", medium=0.50, high=0.90)

    assert len(captured) == 1, "expected exactly one execute() call"
    sql, params = captured[0]

    # No hardcoded threshold literals may appear in the SQL
    assert "0.70" not in sql, "hardcoded 0.70 still in SQL"
    assert "0.45" not in sql, "hardcoded 0.45 still in SQL"

    # The caller-supplied values must reach the params dict
    assert params["cl_high"] == 0.90
    assert params["cl_med"] == 0.50
    assert params["data_source"] == "SomeSource"


def test_get_confidence_distribution_defaults_when_not_provided():
    """WHY: callers that omit thresholds must still get safe defaults (0.45/0.70)."""
    repo, captured = _make_mock_repo()

    repo.get_confidence_distribution("AnotherSource")

    assert len(captured) == 1
    _, params = captured[0]

    assert params["cl_med"] == 0.45
    assert params["cl_high"] == 0.70


def test_get_confidence_distribution_low_bucket_includes_null():
    """WHY: NULL-confidence assets bucket as 'low' on every other surface
    (dashboard CASE, the confidence_level projection, the Explorer filter); the
    per-source low bucket must match, or the 3-way split silently drops unscored
    assets and high+medium+low < total."""
    repo, captured = _make_mock_repo()

    repo.get_confidence_distribution("SomeSource", medium=0.50, high=0.90)

    sql, _ = captured[0]
    assert "IS NULL" in sql, "low bucket must include OR a.confidence IS NULL"


# ---------------------------------------------------------------------------
# Test B: service forwards thresholds to the repo call
# ---------------------------------------------------------------------------

def test_get_insights_forwards_thresholds_to_repo():
    """WHY: get_insights is the only path to the per-source buckets; it must pass
    the caller-resolved thresholds through, or the SQL binding is dead."""
    from src.modules.integrations.services.integration_service import IntegrationService

    svc = IntegrationService.__new__(IntegrationService)
    svc.repository = MagicMock()

    # get_by_id must return a non-falsy value so we don't early-return NOT_FOUND
    fake_integration = MagicMock()
    fake_integration.name = "TestSource"
    svc.repository.get_by_id.return_value = fake_integration

    # Stub the other repo calls that get_insights makes before reaching
    # get_confidence_distribution so the method can run to completion
    svc.repository.get_stale_assets_count.return_value = {
        'stale_count': 0,
        'total_count': 0,
    }
    svc.repository.get_attribute_coverage.return_value = {}
    svc.repository.get_attribute_conflicts.return_value = []
    svc.repository.get_category_coverage.return_value = []
    svc.repository.get_confidence_distribution.return_value = {
        'high': 0,
        'medium': 0,
        'low': 0,
    }

    svc.get_insights(uuid.uuid4(), medium=0.50, high=0.90)

    svc.repository.get_confidence_distribution.assert_called_once()
    _, kwargs = svc.repository.get_confidence_distribution.call_args
    assert kwargs.get("medium") == 0.50, f"expected medium=0.50, got {kwargs}"
    assert kwargs.get("high") == 0.90, f"expected high=0.90, got {kwargs}"
