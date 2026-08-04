# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge configuration: env for bootstrap, API for live config."""

from __future__ import annotations

import json as _json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# Sync HTTP via stdlib urllib for the boot-time fetch. The bridge runs an
# async event loop after boot, but from_api() is called BEFORE
# web.run_app() starts the loop, so a synchronous call is fine here. Task
# 9 adds an async refresh loop that uses aiohttp.

logger = logging.getLogger(__name__)

_INSECURE_TRANSPORT_WARNED: set[str] = set()


@dataclass(frozen=True)
class BridgeConfig:
    # Bootstrap (env) — required at boot
    assetatlas_api_url: str
    assetatlas_bridge_api_key: str
    # Live (API) — may be unset in idle mode
    enabled: bool
    bot_app_id: str
    bot_app_password: str
    bot_app_tenant_id: str
    # Static knobs
    bind_host: str = "0.0.0.0"
    bind_port: int = 3978
    log_level: str = "INFO"
    request_timeout_seconds: int = 60
    refresh_interval_seconds: int = 30

    @property
    def is_active(self) -> bool:
        """True iff the bridge should attempt Bot Framework adapter init."""
        return bool(
            self.enabled and self.bot_app_id and self.bot_app_password
            and self.bot_app_tenant_id
        )

    def credential_tuple(self) -> tuple[bool, str, str, str]:
        """Used by the refresh loop to detect changes and decide whether to
        rebuild the adapter. Order: (enabled, app_id, password, tenant)."""
        return (
            self.enabled, self.bot_app_id, self.bot_app_password,
            self.bot_app_tenant_id,
        )

    @classmethod
    def from_api(cls) -> "BridgeConfig":
        """Bootstrap from env, then fetch live config from the API.

        Raises RuntimeError if required env vars are missing. Returns an
        idle config (is_active==False) when the API is unreachable, the
        toggle is off, or any credential field is empty. The bridge
        survives a temporary API outage by booting in idle mode and
        letting the periodic refresh loop (Task 9) recover.
        """
        required = ("ASSETATLAS_API_URL", "ASSETATLAS_BRIDGE_API_KEY")
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                f"Required env vars not set: {', '.join(missing)}",
            )

        api_url = os.environ["ASSETATLAS_API_URL"]
        api_key = os.environ["ASSETATLAS_BRIDGE_API_KEY"]

        try:
            remote = _fetch_remote(api_url, api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch Teams config from API: %s. Starting idle.",
                exc,
            )
            remote = {
                'enabled': False, 'bot_app_id': '',
                'bot_app_password': '', 'bot_app_tenant_id': '',
            }

        return cls(
            assetatlas_api_url=api_url,
            assetatlas_bridge_api_key=api_key,
            enabled=bool(remote.get('enabled', False)),
            bot_app_id=remote.get('bot_app_id', '') or '',
            bot_app_password=remote.get('bot_app_password', '') or '',
            bot_app_tenant_id=remote.get('bot_app_tenant_id', '') or '',
            bind_host=os.environ.get("BRIDGE_BIND_HOST", "0.0.0.0"),
            bind_port=int(os.environ.get("BRIDGE_BIND_PORT", "3978")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


def _fetch_remote(api_url: str, api_key: str) -> dict:
    """Sync HTTP GET /api/auth/teams-bridge/config. Returns the decoded
    payload's `data` envelope (or the body if no envelope).

    Header is X-Service-Key per src/auth/api_key_auth.py.

    The endpoint returns the decrypted bot password in the response body.
    Deployment policy: the bridge calls the API over the Docker internal
    network (e.g. http://api:5001), which is treated as an internal trust
    boundary. We emit a one-time WARNING on http:// URLs so an operator
    who accidentally exposes the API endpoint via http on a public
    interface gets a visible signal in the logs.
    """
    base = api_url.rstrip('/')
    if (
        base.lower().startswith('http://')
        and base not in _INSECURE_TRANSPORT_WARNED
    ):
        _INSECURE_TRANSPORT_WARNED.add(base)
        logger.warning(
            "Fetching Teams config over plaintext HTTP (%s); the bot "
            "password is transmitted unencrypted on this hop. Acceptable "
            "for Docker-internal networks; review if running on a public "
            "interface.", base,
        )
    url = f"{base}/api/auth/teams-bridge/config"
    headers = {'X-Service-Key': api_key}

    req = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = _json.loads(resp.read().decode('utf-8'))

    if isinstance(body, dict) and 'data' in body:
        return body['data']
    return body
