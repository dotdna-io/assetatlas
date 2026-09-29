# SPDX-License-Identifier: AGPL-3.0-or-later
"""The HTTP transport's process-level AppContext (#1448).

`test_context.py` covers the stdio shape of `app_lifespan`; these cover what an
HTTP process adds around it: `app_context_process_scope` and the lazy,
single-flight `ensure_app_context`.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

import src.mcp.context as ctx_mod
from src.mcp.context import (
    AppContext,
    app_context_process_scope,
    app_lifespan,
    ensure_app_context,
)


class Builds:
    """Stand-in for `_build_app_context`: counts calls, can fail the first N."""

    def __init__(self, fail_first=0, delay=0.0):
        self.calls = 0
        self.fail_first = fail_first
        self.delay = delay
        self.built = []
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            call = self.calls
        time.sleep(self.delay)
        if call <= self.fail_first:
            raise ConnectionError('Database health check failed: down')
        ctx = AppContext(db=MagicMock(), conn_manager=MagicMock())
        self.built.append(ctx)
        return ctx


@pytest.fixture
def builds(monkeypatch):
    monkeypatch.setattr(ctx_mod, '_app_context', None)
    monkeypatch.setattr(ctx_mod, '_build_lock', None)

    def install(**kwargs):
        b = Builds(**kwargs)
        monkeypatch.setattr(ctx_mod, '_build_app_context', b)
        return b
    return install


def test_scope_builds_at_startup_and_closes_once_at_shutdown(builds):
    b = builds()

    async def run():
        async with app_context_process_scope():
            assert ctx_mod.get_app_context() is b.built[0]
            assert await ensure_app_context() is b.built[0]

    asyncio.run(run())

    assert b.calls == 1
    b.built[0].conn_manager.close_all_connections.assert_called_once()
    assert ctx_mod.get_app_context() is None
    assert ctx_mod._build_lock is None


def test_concurrent_callers_share_one_build(builds):
    # Startup fails, then several requests race the retry: one build, one
    # pool, every caller gets the same context.
    b = builds(fail_first=1, delay=0.05)

    async def run():
        async with app_context_process_scope():
            assert ctx_mod.get_app_context() is None
            return await asyncio.gather(*(ensure_app_context() for _ in range(8)))

    results = asyncio.run(run())

    assert b.calls == 2
    assert len(b.built) == 1
    assert all(r is b.built[0] for r in results)


def test_a_failed_build_publishes_nothing_and_the_next_call_retries(builds):
    b = builds(fail_first=2)

    async def run():
        async with app_context_process_scope():      # attempt 1 fails, logged
            with pytest.raises(ConnectionError):
                await ensure_app_context()           # attempt 2 fails, raised
            assert ctx_mod.get_app_context() is None
            return await ensure_app_context()        # attempt 3 succeeds

    assert asyncio.run(run()) is b.built[0]
    assert b.calls == 3


def test_the_scope_is_usable_from_a_new_event_loop_each_time(builds):
    # The lock is created inside the scope, so a later loop never waits on a
    # lock bound to an earlier one.
    b = builds(fail_first=1)

    async def run():
        async with app_context_process_scope():
            await asyncio.gather(ensure_app_context(), ensure_app_context())

    for _ in range(3):
        asyncio.run(run())
        b.fail_first = b.calls + 1   # make the next scope's startup fail too

    assert len(b.built) == 3


def test_nested_scopes_are_refused(builds):
    builds()

    async def run():
        async with app_context_process_scope():
            async with app_context_process_scope():
                pass

    with pytest.raises(RuntimeError, match='already active'):
        asyncio.run(run())
    assert ctx_mod._build_lock is None


def test_app_lifespan_inside_a_scope_borrows_and_never_closes(builds):
    b = builds()

    async def run():
        async with app_context_process_scope():
            for _ in range(5):
                async with app_lifespan(MagicMock()) as app_ctx:
                    assert app_ctx is b.built[0]
                assert ctx_mod.get_app_context() is b.built[0]
            b.built[0].conn_manager.close_all_connections.assert_not_called()

    asyncio.run(run())
    assert b.calls == 1


def test_app_lifespan_inside_a_scope_raises_when_the_db_stays_down(builds):
    builds(fail_first=99)

    async def run():
        async with app_context_process_scope():
            async with app_lifespan(MagicMock()):
                pass

    with pytest.raises(ConnectionError, match='health check failed'):
        asyncio.run(run())


def test_ensure_outside_a_scope_returns_a_published_context(builds):
    # stdio: `app_lifespan` owns and publishes the context; nothing builds.
    b = builds()
    published = AppContext(db=MagicMock(), conn_manager=MagicMock())
    ctx_mod._app_context = published

    assert asyncio.run(ensure_app_context()) is published
    assert b.calls == 0


def test_ensure_outside_a_scope_with_nothing_published_raises(builds):
    b = builds()

    with pytest.raises(RuntimeError, match='not available'):
        asyncio.run(ensure_app_context())
    assert b.calls == 0


def test_failed_build_closes_the_pool_it_opened(monkeypatch):
    manager = MagicMock()
    manager.health_check.return_value = {'healthy': False, 'error': 'down'}
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.setattr(ctx_mod, 'PostgreSQLConnectionManager',
                        MagicMock(return_value=manager))
    monkeypatch.setattr(ctx_mod, 'PostgreSQLAdapter', MagicMock())

    with pytest.raises(ConnectionError, match='health check failed'):
        ctx_mod._build_app_context()

    manager.close_all_connections.assert_called_once()
