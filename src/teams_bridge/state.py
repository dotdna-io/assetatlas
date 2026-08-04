# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge runtime state — JWT cache and the HTTP client to AA API.

Both are async-friendly. The JwtCache is process-local (not Redis-backed)
because the bridge runs single-replica per AssetAtlas instance.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    token: str
    user_id: str
    expires_at: float


class JwtCache:
    """Per-(aad_object_id, tenant_id) cache of /exchange-issued JWTs."""

    def __init__(self, skew_seconds: int = 30):
        self._items: Dict[Tuple[str, str], _Entry] = {}
        self._skew = skew_seconds

    def get(self, aad: str, tnt: str) -> Optional[str]:
        e = self._get_entry(aad, tnt)
        return e.token if e else None

    def get_user_id(self, aad: str, tnt: str) -> Optional[str]:
        e = self._get_entry(aad, tnt)
        return e.user_id if e else None

    def set(
        self, aad: str, tnt: str, token: str, user_id: str,
        expires_in: int,
    ) -> None:
        self._items[(aad, tnt)] = _Entry(
            token=token, user_id=user_id,
            expires_at=time.monotonic() + expires_in,
        )

    def invalidate(self, aad: str, tnt: str) -> None:
        self._items.pop((aad, tnt), None)

    def _get_entry(self, aad: str, tnt: str) -> Optional[_Entry]:
        e = self._items.get((aad, tnt))
        if e is None:
            return None
        if e.expires_at - self._skew <= time.monotonic():
            self._items.pop((aad, tnt), None)
            return None
        return e


class AssetAtlasClient:
    """Async HTTP client for the AssetAtlas API. One instance per bridge
    process. Reuses an aiohttp ClientSession for connection pooling."""

    def __init__(
        self, *, api_url: str, bridge_key: str, request_timeout: int = 60,
    ):
        self._url = api_url.rstrip("/")
        self._key = bridge_key
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        # Guards lazy session creation; without this two concurrent first
        # callers can each create a ClientSession and one gets leaked.
        self._session_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=self._timeout)
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- service-key endpoints ----

    async def initiate_link(
        self, *, aad_object_id: str, tenant_id: str, display_name: str,
    ) -> Dict[str, Any]:
        return await self._post_service(
            "/api/auth/teams-link/initiate",
            {"aad_object_id": aad_object_id,
             "tenant_id": tenant_id,
             "display_name": display_name},
        )

    async def exchange(
        self, *, aad_object_id: str, tenant_id: str,
    ) -> Optional[Dict[str, Any]]:
        # Doesn't delegate to _post_service because 404 is a normal
        # outcome (user hasn't linked yet) and must surface as None
        # rather than raising.
        s = await self._ensure_session()
        async with s.post(
            f"{self._url}/api/auth/teams-bridge/exchange",
            json={"aad_object_id": aad_object_id, "tenant_id": tenant_id},
            headers={"X-Service-Key": self._key},
        ) as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            payload = await r.json()
            return payload["data"]

    # ---- user-JWT endpoints ----

    async def create_chat_session(
        self, *, access_token: str, title: str = "Microsoft Teams chat",
    ) -> str:
        payload = await self._post_user(
            "/api/chat/sessions", {"title": title}, access_token,
        )
        return payload["session_id"]

    async def send_feedback(
        self, *, access_token: str, message_id: str, rating: int,
    ) -> None:
        await self._post_user(
            "/api/chat/feedback",
            {"message_id": message_id, "rating": rating},
            access_token,
        )

    async def get_conversation_session(
        self, conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        s = await self._ensure_session()
        async with s.get(
            f"{self._url}/api/auth/teams-bridge/conversation",
            params={"conversation_id": conversation_id},
            headers={"X-Service-Key": self._key},
        ) as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            payload = await r.json()
            return payload["data"]

    async def set_conversation_session(
        self, *, conversation_id: str, user_id: str, session_id: str,
    ) -> None:
        await self._post_service(
            "/api/auth/teams-bridge/conversation",
            {"conversation_id": conversation_id,
             "user_id": user_id, "session_id": session_id},
        )

    async def delete_conversation_session(
        self, conversation_id: str,
    ) -> None:
        s = await self._ensure_session()
        async with s.delete(
            f"{self._url}/api/auth/teams-bridge/conversation",
            params={"conversation_id": conversation_id},
            headers={"X-Service-Key": self._key},
        ) as r:
            if r.status not in (200, 404):
                r.raise_for_status()

    async def stream_chat(
        self, *, access_token: str, session_id: str, message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield parsed SSE events from /api/chat/stream as
        {event: str, data: dict} dicts."""
        s = await self._ensure_session()
        async with s.post(
            f"{self._url}/api/chat/stream",
            json={"session_id": session_id, "message": message},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "text/event-stream",
            },
        ) as r:
            r.raise_for_status()
            async for ev in _parse_sse(r.content):
                yield ev

    # ---- internal helpers ----

    async def _post_service(
        self, path: str, body: Dict[str, Any],
    ) -> Dict[str, Any]:
        s = await self._ensure_session()
        async with s.post(
            f"{self._url}{path}", json=body,
            headers={"X-Service-Key": self._key},
        ) as r:
            r.raise_for_status()
            payload = await r.json()
            return payload["data"]

    async def _post_user(
        self, path: str, body: Dict[str, Any], access_token: str,
    ) -> Dict[str, Any]:
        s = await self._ensure_session()
        async with s.post(
            f"{self._url}{path}", json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as r:
            r.raise_for_status()
            payload = await r.json()
            return payload.get("data") or {}


async def _parse_sse(body: aiohttp.StreamReader) -> AsyncIterator[Dict[str, Any]]:
    """Minimal SSE parser. Lines beginning with `event:` set the event
    name for the next blank-line-terminated record. `data:` lines
    accumulate. Blank line flushes a record.

    Reads via readline() — `async for raw in body` would yield network
    chunks rather than newline-terminated lines and silently drop every
    multi-line SSE record on the floor.
    """
    buf_event: Optional[str] = None
    buf_data: list[str] = []
    while True:
        raw = await body.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if buf_event or buf_data:
                payload = "\n".join(buf_data)
                try:
                    data = _json.loads(payload) if payload else {}
                except _json.JSONDecodeError:
                    data = {"raw": payload}
                yield {"event": buf_event or "message", "data": data}
            buf_event = None
            buf_data = []
            continue
        if line.startswith("event:"):
            buf_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            buf_data.append(line[len("data:"):].strip())
