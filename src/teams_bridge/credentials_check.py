# SPDX-License-Identifier: AGPL-3.0-or-later
"""Probe Microsoft Bot Framework auth with a candidate credential set.

Used by the bridge's POST /test-credentials endpoint (and indirectly by the
API's /api/settings/teams/test-connection forwarder) so the UI can verify
credentials before saving them.

MicrosoftAppCredentials.get_access_token() is synchronous (uses requests
internally), so we run it in a thread via asyncio.to_thread to avoid
blocking the bridge's event loop during the OAuth round-trip.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any, Dict

from botframework.connector.auth import MicrosoftAppCredentials

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 240


async def probe_bot_credentials(
    app_id: str, app_password: str, tenant_id: str,
) -> Dict[str, Any]:
    """Try to acquire an OAuth token using the given creds.

    Returns:
      {'ok': True} on success
      {'ok': False, 'error': '<sanitized message>'} on AAD rejection
      {'ok': False, 'error': 'unreachable'} on network/timeout failure
    """
    creds = MicrosoftAppCredentials(
        app_id=app_id,
        password=app_password,
        channel_auth_tenant=tenant_id,
    )
    try:
        await asyncio.to_thread(creds.get_access_token)
        return {'ok': True}
    except (socket.gaierror, ConnectionError, TimeoutError):
        logger.warning("Credentials probe: network unreachable.")
        return {'ok': False, 'error': 'unreachable'}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Credentials probe failed: %s", exc)
        return {'ok': False, 'error': _sanitize(str(exc))}


def _sanitize(msg: str) -> str:
    """Trim absurdly long AAD bodies; keep the AADSTS code if present."""
    return (msg or 'unknown error')[:_MAX_ERROR_LEN]
