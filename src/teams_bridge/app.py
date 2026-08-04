# SPDX-License-Identifier: AGPL-3.0-or-later
"""aiohttp routes: /api/messages (Bot Framework), /healthz, /metrics.

The bridge supports two modes selected by BridgeConfig.is_active:
  - active: full Bot Framework adapter wired up, /api/messages serves traffic
  - idle: no adapter, /api/messages returns 503, /healthz still ok

A periodic refresh loop polls the API for config changes and swaps the
adapter in-process when the credential tuple changes — no process restart
needed for credential rotation, enable/disable, or first-time setup.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web
from botbuilder.core import (BotFrameworkAdapter, BotFrameworkAdapterSettings,
                             TurnContext)
from botbuilder.schema import Activity
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from src.teams_bridge.bot import TeamsBridgeBot
from src.teams_bridge.config import BridgeConfig
from src.teams_bridge.state import AssetAtlasClient, JwtCache

logger = logging.getLogger(__name__)


messages_received_total = Counter(
    "teams_bridge_messages_received_total",
    "Total Bot Framework activities received.",
)
messages_errors_total = Counter(
    "teams_bridge_messages_errors_total",
    "Total errors during activity processing.",
    ["kind"],
)


def build_app(cfg: BridgeConfig) -> web.Application:
    """Build the aiohttp app. Wires up the adapter only if cfg.is_active."""
    app = web.Application()
    app["cfg"] = cfg
    app["started_at"] = time.time()
    app["adapter"] = None
    app["bot"] = None
    app["client"] = None

    if cfg.is_active:
        _wire_active(app, cfg)
    else:
        logger.info(
            "teams_bridge_disabled=1 reason=%s",
            "toggle_off" if not cfg.enabled else "credentials_missing",
        )

    app.router.add_post("/api/messages", _messages_handler)
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/metrics", _metrics)
    app.router.add_post("/test-credentials", _test_credentials_handler)
    return app


def _wire_active(app: web.Application, cfg: BridgeConfig) -> None:
    """Build new active state (adapter + bot + AssetAtlas client).

    This function is purely constructive — it does NOT manage the lifecycle
    of any previously-wired state. The caller (_apply_new_config) is
    responsible for closing the old client before invoking this.
    """
    settings = BotFrameworkAdapterSettings(
        app_id=cfg.bot_app_id,
        app_password=cfg.bot_app_password,
        channel_auth_tenant=cfg.bot_app_tenant_id,
    )
    adapter = BotFrameworkAdapter(settings)

    async def _on_error(turn_context: TurnContext, error: Exception):
        logger.exception("Unhandled error in bot turn: %s", error)
        messages_errors_total.labels(kind="bot_turn").inc()
        try:
            await turn_context.send_activity(
                "Sorry, something went wrong. Please try again.",
            )
        except Exception:  # noqa: BLE001
            pass
    adapter.on_turn_error = _on_error

    client = AssetAtlasClient(
        api_url=cfg.assetatlas_api_url,
        bridge_key=cfg.assetatlas_bridge_api_key,
        request_timeout=cfg.request_timeout_seconds,
    )
    bot = TeamsBridgeBot(client=client, jwt_cache=JwtCache(skew_seconds=30))

    app["adapter"] = adapter
    app["bot"] = bot
    app["client"] = client


async def _safe_close(client) -> None:
    try:
        await client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error closing AssetAtlas client: %s", exc)


async def _apply_new_config(
    app: web.Application, new_cfg: BridgeConfig,
) -> None:
    """Apply a fresh BridgeConfig to the running app.

    Compares credential tuples; no-op if unchanged. On change, closes the
    old AssetAtlas client (awaited) before building new state via
    _wire_active. On a transition to idle, tears down without rebuilding.
    """
    current: BridgeConfig = app["cfg"]
    if new_cfg.credential_tuple() == current.credential_tuple():
        return  # No-op.

    logger.info("Teams config changed; rebuilding adapter.")

    # Close the prior client (if any) before swapping.
    old_client = app.get("client")
    if old_client is not None:
        await _safe_close(old_client)

    if new_cfg.is_active:
        _wire_active(app, new_cfg)
    else:
        app["adapter"] = None
        app["bot"] = None
        app["client"] = None
        logger.info(
            "teams_bridge_disabled=1 reason=%s",
            "toggle_off" if not new_cfg.enabled else "credentials_missing",
        )

    app["cfg"] = new_cfg


async def _refresh_loop(app: web.Application) -> None:
    """Re-fetch BridgeConfig every cfg.refresh_interval_seconds.

    Reads the interval from app["cfg"] each iteration so a future config
    change can adjust the cadence without a process restart. On a fetch
    failure, keeps the last-known-good config. On a tuple change, awaits
    the rebuild via _apply_new_config.
    """
    while True:
        try:
            interval = app["cfg"].refresh_interval_seconds
            await asyncio.sleep(interval)
            new_cfg = BridgeConfig.from_api()
            await _apply_new_config(app, new_cfg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "Teams config refresh failed; keeping current config.",
            )


def attach_refresh_loop(app: web.Application) -> None:
    """Register the refresh loop as a background task with proper shutdown."""

    async def _on_startup(_app):
        _app["_refresh_task"] = asyncio.create_task(_refresh_loop(_app))

    async def _on_shutdown(_app):
        task = _app.get("_refresh_task")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Also close the active client on shutdown.
        client = _app.get("client")
        if client is not None:
            await _safe_close(client)

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)


# ── Handlers ───────────────────────────────────────────────────────────────


async def _messages_handler(request: web.Request) -> web.Response:
    """Bot Framework activity handler. Short-circuits 503 when idle."""
    adapter: BotFrameworkAdapter | None = request.app.get("adapter")
    if adapter is None:
        return web.Response(status=503, text="teams bridge not configured")
    if "application/json" not in (request.headers.get("Content-Type") or ""):
        return web.Response(status=415)

    bot: TeamsBridgeBot = request.app["bot"]

    try:
        body = await request.json()
        activity = Activity().deserialize(body)
    except Exception:  # noqa: BLE001
        logger.warning("Malformed activity payload from %s", request.remote)
        messages_errors_total.labels(kind="bad_request").inc()
        return web.Response(status=400)

    auth_header = request.headers.get("Authorization", "")
    messages_received_total.inc()

    try:
        await adapter.process_activity(activity, auth_header, bot.on_turn)
        return web.Response(status=200)
    except PermissionError:
        messages_errors_total.labels(kind="unauthorized").inc()
        return web.Response(status=401)
    except Exception:  # noqa: BLE001
        logger.exception("Internal error processing activity")
        messages_errors_total.labels(kind="internal").inc()
        return web.Response(status=500)


async def _healthz(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "uptime_s": int(time.time() - request.app["started_at"]),
    })


async def _metrics(request: web.Request) -> web.Response:
    return web.Response(
        body=generate_latest(),
        content_type=CONTENT_TYPE_LATEST.split(";")[0].strip(),
    )


async def _test_credentials_handler(request: web.Request) -> web.Response:
    """Probe Bot Framework auth with caller-supplied creds (no persistence).

    Body shape: {bot_app_id, bot_app_password, bot_app_tenant_id}.
    Always returns 200 with a JSON body — the API forwarder distinguishes
    success vs. failure by the body's `ok` field.
    """
    if "application/json" not in (request.headers.get("Content-Type") or ""):
        return web.Response(status=415)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.Response(status=400)

    app_id = body.get('bot_app_id', '')
    app_password = body.get('bot_app_password', '')
    tenant_id = body.get('bot_app_tenant_id', '')
    if not (app_id and app_password and tenant_id):
        return web.json_response(
            {'ok': False, 'error': 'all three credentials are required'},
            status=200,
        )

    # Lazy import to avoid loading the credentials_check module — and its
    # MicrosoftAppCredentials transitive dep — when the bridge is in idle
    # mode and never asked to probe.
    from src.teams_bridge.credentials_check import probe_bot_credentials
    result = await probe_bot_credentials(app_id, app_password, tenant_id)
    return web.json_response(result, status=200)
