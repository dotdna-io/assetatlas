# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared version stamp for cross-worker FieldCatalog invalidation (#1413).

Each gunicorn worker keeps its own in-process FieldCatalog snapshot; a bare
``invalidate()`` only clears the worker that served the request, so the other
workers used to serve a stale catalog for up to the TTL — a just-created
unified field raised InvalidFieldError there. This module owns a single Redis
counter (key ``assetatlas:field_catalog_version``) that every content-changing
event bumps via INCR; catalogs poll it (throttled, see FieldCatalog) and
rebuild their snapshot on mismatch.

The stamp is an optimisation over the TTL safety net, never worth breaking a
request over: every Redis error is swallowed and logged at DEBUG, ``bump()``
never raises, and ``get()`` returns None on any failure. Without REDIS_URL
(tests, simple-cache installs) the source degrades to "unavailable" —
``get()`` returns None and ``bump()`` is a no-op — and FieldCatalog falls back
to its plain TTL behaviour.

Lives in ``src/modules/fields`` (not ``src/api``) so context-free callers —
the scheduler, the pipeline worker, the MCP server — can bump without a Flask
app context; ``src.api.utils.caching`` imports it the other way around.
"""

import logging
import os
import threading
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

STAMP_KEY = "assetatlas:field_catalog_version"


class CatalogVersionSource(Protocol):
    """What FieldCatalog needs from a stamp backend. Tests inject a fake."""

    def get(self) -> Optional[int]:
        """Current stamp value; None when the source is unavailable."""
        ...

    def bump(self) -> None:
        """Advance the stamp. Must never raise."""
        ...


class RedisCatalogVersionSource:
    """get()/bump() against the shared Redis stamp; None/no-op when down.

    The client is built lazily from REDIS_URL (read at first use, so import
    order never matters) and cached; a client that failed to build is not
    retried — the next process start picks up a fixed configuration.
    """

    def __init__(self, url: Optional[str] = None):
        self._url = url
        self._client = None
        self._client_failed = False
        self._client_lock = threading.Lock()

    def _client_or_none(self):
        if self._client is not None or self._client_failed:
            return self._client
        url = self._url or os.getenv("REDIS_URL")
        if not url:
            return None
        with self._client_lock:
            if self._client is not None or self._client_failed:
                return self._client
            try:
                import redis
                # Short timeouts: a hung Redis must not stall a request for
                # longer than this on the throttled stamp read.
                self._client = redis.Redis.from_url(
                    url, socket_timeout=1.0, socket_connect_timeout=1.0,
                )
            except Exception as exc:
                logger.debug("field-catalog stamp: Redis client unavailable: %s", exc)
                self._client_failed = True
        return self._client

    def get(self) -> Optional[int]:
        client = self._client_or_none()
        if client is None:
            return None
        try:
            raw = client.get(STAMP_KEY)
        except Exception as exc:
            logger.debug("field-catalog stamp read failed: %s", exc)
            return None
        if raw is None:
            # Key not INCR'd yet: Redis is reachable, so the stamp is live at
            # value 0 — distinct from "unavailable" (None), which would drop
            # the catalog back to its short TTL.
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.debug("field-catalog stamp: unparsable value %r", raw)
            return None

    def bump(self) -> None:
        client = self._client_or_none()
        if client is None:
            return
        try:
            client.incr(STAMP_KEY)
        except Exception as exc:
            logger.debug("field-catalog stamp bump failed: %s", exc)


_default_source = RedisCatalogVersionSource()


def default_source() -> RedisCatalogVersionSource:
    """The process-wide source FieldCatalog instances share (one client)."""
    return _default_source


def get() -> Optional[int]:
    """Current stamp value from the default source; None when unavailable."""
    return _default_source.get()


def bump() -> None:
    """Advance the shared stamp. Never raises; no-op without Redis.

    This is the call every catalog-content-changing event makes: FieldCatalog
    edits (via invalidate()), pipeline completion, integration-group CRUD,
    posture category changes, manual tag add/remove.
    """
    _default_source.bump()
