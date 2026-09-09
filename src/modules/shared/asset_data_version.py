# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared version stamp for asset-table data changes (#1410).

Sibling of ``src.modules.fields.catalog_version``: that stamp tracks changes
to the field CATALOG (which fields exist); this one tracks changes to the
asset DATA itself (what `assets` rows and their values are). The Explorer
field-values cache (`src.modules.assets.services.field_values_cache`) mixes
both stamps into its keys, so any writer that can change a field's distinct
values — pipeline completion, enrichment apply, merge/unmerge, reclassify,
label override, manual tags, integration-group CRUD, unified-field CRUD —
bumps this stamp and every cached values entry is implicitly invalidated
without pattern deletes.

Same contract as the catalog stamp: the stamp is an optimisation, never worth
breaking a write over. Every Redis error is swallowed and logged at DEBUG,
``bump()`` never raises, and ``get()`` returns None on any failure. Without
REDIS_URL (tests, simple-cache installs) the source degrades to
"unavailable" — ``get()`` returns None and ``bump()`` is a no-op — and the
field-values cache disables itself (no per-worker memory fallback).

Lives in ``src/modules/shared`` so context-free callers — the scheduler, the
pipeline worker — can bump without a Flask app context, and so
``src/modules/**`` modules never import ``src/api/**``.
"""

import logging
import os
import threading
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

STAMP_KEY = "assetatlas:asset_data_version"


class AssetDataVersionSource(Protocol):
    """What the field-values cache needs from a stamp backend. Tests inject a fake."""

    def get(self) -> Optional[int]:
        """Current stamp value; None when the source is unavailable."""
        ...

    def bump(self) -> None:
        """Advance the stamp. Must never raise."""
        ...


class RedisAssetDataVersionSource:
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
                # longer than this on a stamp read.
                self._client = redis.Redis.from_url(
                    url, socket_timeout=1.0, socket_connect_timeout=1.0,
                )
            except Exception as exc:
                logger.debug("asset-data stamp: Redis client unavailable: %s", exc)
                self._client_failed = True
        return self._client

    def get(self) -> Optional[int]:
        client = self._client_or_none()
        if client is None:
            return None
        try:
            raw = client.get(STAMP_KEY)
        except Exception as exc:
            logger.debug("asset-data stamp read failed: %s", exc)
            return None
        if raw is None:
            # Key not INCR'd yet: Redis is reachable, so the stamp is live at
            # value 0 — distinct from "unavailable" (None), which disables
            # the field-values cache entirely.
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.debug("asset-data stamp: unparsable value %r", raw)
            return None

    def bump(self) -> None:
        client = self._client_or_none()
        if client is None:
            return
        try:
            client.incr(STAMP_KEY)
        except Exception as exc:
            logger.debug("asset-data stamp bump failed: %s", exc)


_default_source = RedisAssetDataVersionSource()


def default_source() -> RedisAssetDataVersionSource:
    """The process-wide source callers share (one client)."""
    return _default_source


def get() -> Optional[int]:
    """Current stamp value from the default source; None when unavailable."""
    return _default_source.get()


def bump() -> None:
    """Advance the shared stamp. Never raises; no-op without Redis.

    This is the call every asset-data-changing event makes: pipeline
    completion, enrichment apply, merge/unmerge, reclassify, asset-label
    override, manual tag add/remove, integration-group CRUD, unified-field
    CRUD.
    """
    _default_source.bump()
