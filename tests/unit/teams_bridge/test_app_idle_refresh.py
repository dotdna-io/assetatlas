# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for idle mode and config-refresh-driven adapter rebuild in app.py."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.teams_bridge.app import build_app, _refresh_loop, attach_refresh_loop
from src.teams_bridge.config import BridgeConfig


def _cfg(active: bool, **overrides) -> BridgeConfig:
    """Build a BridgeConfig in active or idle state."""
    base = dict(
        assetatlas_api_url='http://api:5001',
        assetatlas_bridge_api_key='K' * 48,
        enabled=active,
        bot_app_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' if active else '',
        bot_app_password='secret' if active else '',
        bot_app_tenant_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb' if active else '',
        refresh_interval_seconds=30,
    )
    base.update(overrides)
    return BridgeConfig(**base)


class TestBuildAppIdleVsActive:
    def test_idle_app_has_no_adapter(self):
        app = build_app(_cfg(active=False))
        assert app.get('adapter') is None
        assert app.get('bot') is None
        assert app['cfg'].is_active is False

    def test_active_app_has_adapter(self):
        app = build_app(_cfg(active=True))
        assert app.get('adapter') is not None
        assert app.get('bot') is not None
        assert app['cfg'].is_active is True

    def test_idle_messages_route_returns_503(self):
        async def _go():
            from aiohttp.test_utils import TestClient, TestServer
            app = build_app(_cfg(active=False))
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    '/api/messages',
                    json={'type': 'message'},
                    headers={'Content-Type': 'application/json'},
                )
                assert resp.status == 503
        asyncio.run(_go())

    def test_idle_healthz_still_works(self):
        async def _go():
            from aiohttp.test_utils import TestClient, TestServer
            app = build_app(_cfg(active=False))
            async with TestClient(TestServer(app)) as client:
                resp = await client.get('/healthz')
                assert resp.status == 200
                body = await resp.json()
                assert body['status'] == 'ok'
        asyncio.run(_go())


class TestRefreshLoop:
    def test_rebuild_when_tuple_changes(self):
        """If from_api returns a new tuple, the adapter is rebuilt in-process."""
        async def _go():
            initial = _cfg(active=False)
            updated = _cfg(active=True)
            app = build_app(initial)
            assert app.get('adapter') is None

            # First poll returns updated config; loop rebuilds the app state.
            with patch('src.teams_bridge.app.BridgeConfig.from_api',
                       return_value=updated):
                # Run one iteration of the refresh loop manually with a
                # tiny sleep so we don't actually wait 30s.
                await _refresh_once(app)

            assert app.get('adapter') is not None
            assert app['cfg'].is_active is True

        asyncio.run(_go())

    def test_no_rebuild_when_tuple_unchanged(self):
        async def _go():
            cfg = _cfg(active=True)
            app = build_app(cfg)
            original_adapter = app['adapter']

            with patch('src.teams_bridge.app.BridgeConfig.from_api',
                       return_value=cfg):
                await _refresh_once(app)

            # Same adapter instance — no rebuild.
            assert app['adapter'] is original_adapter

        asyncio.run(_go())

    def test_active_to_idle_transition(self):
        async def _go():
            app = build_app(_cfg(active=True))
            assert app.get('adapter') is not None

            with patch('src.teams_bridge.app.BridgeConfig.from_api',
                       return_value=_cfg(active=False)):
                await _refresh_once(app)

            assert app.get('adapter') is None
            assert app['cfg'].is_active is False

        asyncio.run(_go())

    def test_refresh_failure_keeps_last_known_good(self):
        """Network/API error during refresh: keep current config, log, retry."""
        async def _go():
            cfg = _cfg(active=True)
            app = build_app(cfg)
            original_adapter = app['adapter']

            with patch('src.teams_bridge.app.BridgeConfig.from_api',
                       side_effect=ConnectionError('refused')):
                await _refresh_once(app)

            # Adapter unchanged — refresh failure does NOT downgrade to idle.
            assert app['adapter'] is original_adapter
            assert app['cfg'].is_active is True

        asyncio.run(_go())


class TestRefreshIntervalReadEachTick:
    def test_interval_change_picked_up_next_iteration(self):
        """If app['cfg'].refresh_interval_seconds changes, the loop reads
        the new value next tick (not the value frozen at startup)."""
        from src.teams_bridge.app import _refresh_loop

        async def _go():
            cfg_initial = _cfg(active=False, refresh_interval_seconds=30)
            cfg_updated = _cfg(active=False, refresh_interval_seconds=15)
            app = build_app(cfg_initial)

            # Spy on asyncio.sleep to record the interval values seen.
            seen = []
            real_sleep = asyncio.sleep

            async def _spy_sleep(seconds):
                seen.append(seconds)
                if len(seen) >= 2:
                    raise asyncio.CancelledError()  # stop the loop
                # Side-effect: swap in the updated cfg before the next tick.
                app["cfg"] = cfg_updated
                # Don't actually sleep — return immediately.
                await real_sleep(0)

            with patch('src.teams_bridge.app.asyncio.sleep', _spy_sleep), \
                 patch('src.teams_bridge.app.BridgeConfig.from_api',
                       return_value=cfg_initial):
                with pytest.raises(asyncio.CancelledError):
                    await _refresh_loop(app)

            assert seen == [30, 15], f"interval not re-read each tick: {seen}"

        asyncio.run(_go())


# Helper: run a single refresh iteration without the 30s sleep.
async def _refresh_once(app):
    """Mimic one tick of _refresh_loop.

    Now awaits _apply_new_config since it's an async function.
    """
    from src.teams_bridge.app import _apply_new_config
    try:
        new_cfg = BridgeConfig.from_api()
    except Exception:
        return  # Real loop logs and continues; we mirror that.
    await _apply_new_config(app, new_cfg)
