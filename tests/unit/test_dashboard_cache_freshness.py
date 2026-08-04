# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard staleness after a pipeline run (#1135).

Two independent defects made a finished pipeline look like it had not landed:

1. Nothing invalidated the dashboard cache. `invalidate_dashboard_cache()`
   existed and was called from precisely nowhere — its only other occurrence in
   the tree was inside its own docstring example — so every card served the
   previous run's numbers until its 60s TTL lapsed.

2. The cache key embedded `hash(frozenset(params.items()))`. Python randomises
   string hashing per process, so each of the four gunicorn workers computed a
   DIFFERENT key for identical requests: four entries, each filled and expiring
   at its own moment. Refreshing round-robins across workers, so the operator
   saw up to four different snapshots of the same data. Measured in the api
   container, `days=30` hashed to five distinct values across five processes,
   while an empty param set hashed to a constant — which is why the
   parameterless cards looked fine and only the trend charts flickered.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from src.api.utils import caching
from src.api.utils.caching import _query_param_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestCacheKeyIsProcessIndependent:
    """The key must not depend on anything that varies between workers."""

    def test_same_params_same_key(self):
        assert _query_param_key({"days": "30"}) == _query_param_key({"days": "30"})

    def test_key_is_order_independent(self):
        """Flask gives no ordering guarantee, so {a,b} and {b,a} are one request."""
        assert (_query_param_key({"metric": "coverage", "days": "30"})
                == _query_param_key({"days": "30", "metric": "coverage"}))

    def test_different_params_differ(self):
        """Still a cache key — distinct requests must not collide."""
        assert _query_param_key({"days": "30"}) != _query_param_key({"days": "7"})
        assert _query_param_key({}) != _query_param_key({"days": "30"})

    def test_stable_across_separate_python_processes(self):
        """The regression itself, and it CANNOT be caught in one process.

        PYTHONHASHSEED is fixed per process, so `hash()` looks perfectly stable
        to any single-process test — which is exactly why this shipped. Each
        subprocess gets its own seed, standing in for a gunicorn worker.
        """
        snippet = (
            "import sys; sys.path.insert(0, %r);"
            "from src.api.utils.caching import _query_param_key;"
            "print(_query_param_key({'metric': 'coverage', 'days': '30'}))"
            % str(PROJECT_ROOT)
        )
        keys = {
            subprocess.run([sys.executable, "-c", snippet],
                           capture_output=True, text=True,
                           cwd=PROJECT_ROOT).stdout.strip()
            for _ in range(5)
        }
        assert len(keys) == 1, (
            f"the cache key differs between processes ({keys}); each gunicorn "
            f"worker would cache under its own key and the dashboard would "
            f"show a different snapshot depending on which one answered"
        )
        assert "" not in keys, "subprocess failed to compute a key"

    def test_hash_builtin_is_not_used_for_the_key(self):
        """Guard the mechanism, not just today's output.

        A future edit could reintroduce `hash()` and every assertion above
        would still pass inside a single process. Uses AST rather than a text
        search so the docstring — which necessarily names the old bug — cannot
        trip it.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(caching._query_param_key)))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "hash" not in called, (
            "cache key uses the builtin hash() again — it is randomised per "
            "process, so every gunicorn worker gets its own cache entry"
        )


class TestPipelineInvalidatesTheDashboard:
    """A completed run must drop the aggregations it just superseded."""

    def test_completion_path_calls_invalidation(self):
        """Pin the wiring: the bug was that nothing called it at all."""
        import inspect
        from src.modules.pipeline.pipeline_service import PipelineService
        src = inspect.getsource(PipelineService._execute_run)
        assert "_invalidate_dashboard_cache" in src, (
            "the pipeline no longer clears the dashboard cache on completion, "
            "so the UI keeps serving the previous run's numbers for 60s"
        )
        # ...and only once the run is recorded as completed, never before the
        # data behind the cache is committed.
        assert src.index("status = 'completed'") < src.index("_invalidate_dashboard_cache")

    def test_invalidation_works_without_an_application_context(self, monkeypatch):
        """The scheduler is the main caller and never pushes an app context.

        A `current_app` read here would raise in exactly the place that matters,
        which is why this is not merely defensive.
        """
        scanned = []

        class _FakeRedis:
            def scan_iter(self, match=None):
                scanned.append(match)
                return iter([b"assetatlas:dashboard:get_stats:-"])

            def delete(self, key):
                return 1

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setattr(
            caching, "_resolve_cache_client", lambda: (_FakeRedis(), "redis"))

        # No app context pushed anywhere in this test.
        assert caching.invalidate_dashboard_cache() == 1
        assert scanned == ["assetatlas:dashboard:*"]

    def test_invalidation_never_raises(self, monkeypatch):
        """A cache flush must not turn a successful pipeline run into a failure."""
        class _Broken:
            def scan_iter(self, match=None):
                raise ConnectionError("redis is down")

        monkeypatch.setattr(
            caching, "_resolve_cache_client", lambda: (_Broken(), "redis"))
        assert caching.invalidate_dashboard_cache() == 0

    def test_returns_zero_when_no_backend_is_reachable(self, monkeypatch):
        monkeypatch.setattr(
            caching, "_resolve_cache_client", lambda: (None, "none"))
        assert caching.invalidate_dashboard_cache() == 0


@pytest.mark.parametrize("params", [
    {}, {"days": "30"}, {"metric": "coverage", "days": "7"}, {"view": "all"},
])
def test_key_is_a_short_stable_string(params):
    key = _query_param_key(params)
    assert isinstance(key, str) and key and len(key) <= 16
