# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-process published state for the update check, plus its pacing constants.

Moved out of ``src/modules/alerts/update_check.py``: that module resolved its
Redis client through ``src.api.utils.caching._resolve_cache_client``, which is
a domain -> API layering violation (tests/unit/test_layering.py forbids new
imports of ``src.api`` from ``src/modules/``). This module builds its own
client straight from ``REDIS_URL`` instead, so it has no dependency on Flask,
Flask-Caching, or anything else under ``src/api`` — the scheduler worker (no
Flask app at all) and the API's health blueprint (a full Flask app) both
import it as ordinary module-level state.

``src.modules.alerts.update_check`` re-exports the public names below at
module scope, so ``UpdateCheckService.tick()``/``_is_due()`` keep referring to
them unqualified and existing tests keep monkeypatching them as
``uc.read_state`` / ``uc.write_state``.

Usage:
    from src.modules.shared.update_check_state import (
        CHECK_INTERVAL_SECONDS, POLL_SECONDS, is_due, read_state, write_state,
    )
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# How often a REAL check may happen, process-wide. The scheduler polls more
# often than this (POLL_SECONDS) and gates each poll on the published state, so
# the cadence is a property of the timestamp in the store rather than of the
# job's trigger. That is what makes it survive a restart: MemoryJobStore + a
# bare IntervalTrigger re-anchor to `restart + interval` on every container
# start, and this scheduler restarts on every release on boxes with an
# auto-updater (PR #1031 fixed the same starvation for the pipeline schedule).
CHECK_INTERVAL_SECONDS = 6 * 3600
# Poll cadence. MUST stay below CHECK_INTERVAL_SECONDS: a poll >= the interval
# would let jitter push the true cadence out to 2x, which is exactly how the
# old 24h-job / 12h-TTL pair behaved. Worst case a check lands one poll late.
POLL_SECONDS = 3600
# The published state outlives one interval on purpose, so the About tab can
# still render `checked_at` from the previous window rather than going blank.
_STATE_TTL_SECONDS = 2 * CHECK_INTERVAL_SECONDS
# Logical key (the in-process fallback dict is keyed on this directly) and the
# fully-qualified Redis key the raw client needs — same split as
# `invalidate_dashboard_cache`'s "assetatlas:dashboard:*". Deliberately NOT the
# old `update_check:latest`, which held Flask-Caching pickle: a new key means
# no decode risk on upgrade and the old one just expires unread.
_STATE_KEY = 'update_check:state'
_STATE_REDIS_KEY = 'assetatlas:' + _STATE_KEY

# In-process fallback for when there is no Redis at all (REDIS_URL unset, or a
# client could not be built from it) — what makes the store work on a box
# with no Redis, including every test run (REDIS_URL is unset locally and in
# CI). Needs no TTL of its own: is_due() already ages an entry out after
# CHECK_INTERVAL_SECONDS, so a stale dict entry just reads as due again. Like
# the old Flask-Caching 'simple' backend it replaces, this is per-worker, not
# shared — it still stops one worker re-fetching on every About-tab load.
_local_state = {}

# Memoised client, keyed on the URL it was built from. `Redis.from_url()` builds
# a fresh ConnectionPool every call, so building one per read_state() would open
# (and drop) a TCP connection on every About-tab GET and every scheduler poll.
# Same pattern as `scheduler_signal._get_default_client`. Keyed on the URL so a
# test (or a re-exec) that changes REDIS_URL still gets a matching client.
_client_lock = threading.Lock()
_client_cache = (None, None)  # (url, client)


def _redis_client():
    """A raw Redis client built from REDIS_URL, or None when unavailable.

    Deferred import: this module must stay importable with no Redis package
    installed and no app context at all (the scheduler worker never builds
    one). Every failure — unset REDIS_URL, unreachable host, a bad URL — falls
    back to None so callers use the in-process store instead of raising.

    The client is built once per URL and reused: redis-py clients are
    thread-safe and pool their connections, and `from_url()` connects lazily,
    so this stays as cheap as before on a box with no Redis listening.
    """
    global _client_cache
    url = os.getenv('REDIS_URL')
    if not url:
        return None
    cached_url, client = _client_cache
    if client is not None and cached_url == url:
        return client
    with _client_lock:
        cached_url, client = _client_cache
        if client is not None and cached_url == url:
            return client
        try:
            import redis
            client = redis.Redis.from_url(url)
        except Exception as e:  # noqa: BLE001 — building a client must never raise
            logger.debug("Update check: could not build a Redis client (ignored): %s", e)
            return None
        _client_cache = (url, client)
        return client


def _iso(ts):
    """Epoch seconds -> '...Z' UTC string, the shape the About tab renders."""
    return (datetime.fromtimestamp(ts, tz=timezone.utc)
            .isoformat().replace('+00:00', 'Z'))


def read_state():
    """Last published check result, or None when there is none to read.

    Never raises: an unreachable or malformed store reads as "no state", which
    makes the caller check rather than wedging it off.
    """
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_STATE_REDIS_KEY)
            if not raw:
                return None
            # Anything that is not a JSON object reads as "no state": callers
            # do `state.get(...)`, so a stray list/scalar under this key would
            # raise AttributeError out of is_due() — a 500 on the About tab and
            # a logged failure on every scheduler poll until the key expires.
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception as e:  # noqa: BLE001 — a cache read must never break the check
            logger.debug("Update check: state read failed (ignored): %s", e)
            # Redis is CONFIGURED but unreachable at call time (from_url()
            # connects lazily, so this is where that first shows up) — fall
            # back to the in-process store instead of reading as "nothing
            # published". A per-worker floor is the point (see write_state
            # below), so an unusable Redis should degrade to per-worker
            # memory, not to no memory at all.
            return _local_state.get(_STATE_KEY)
    return _local_state.get(_STATE_KEY)


def write_state(latest_version, now):
    """Publish a check result and return it. Never raises.

    A FAILED check is published too (``latest_version=None``): that is what
    stops a poll storm, or an enabled-but-unreachable box, from fetching on
    every poll. It carries the LAST KNOWN version forward rather than blanking
    it, though — ``checked_at`` is "when we last tried", ``latest_version`` is
    "the newest release we know of". Overwriting it with None on a transient
    DNS blip would flip the About tab from "update available" back to "up to
    date" for a whole window, while the alert the same check raised still says
    otherwise.
    """
    if latest_version is None:
        previous = read_state()
        if previous:
            latest_version = previous.get('latest_version')
    state = {'latest_version': latest_version,
             'checked_at': _iso(now),
             'checked_at_ts': now}
    client = _redis_client()
    try:
        if client is not None:
            client.set(_STATE_REDIS_KEY, json.dumps(state),
                       ex=_STATE_TTL_SECONDS)
        else:
            _local_state[_STATE_KEY] = state
    except Exception as e:  # noqa: BLE001 — publishing is best-effort
        logger.debug("Update check: state write failed (ignored): %s", e)
        # Same reasoning as read_state's except branch above: a Redis that is
        # configured but unreachable must not lose the write entirely, or a
        # Redis-down box remembers NOTHING at all. A per-worker floor is the
        # point, so an unusable Redis should degrade to per-worker memory,
        # not to no memory at all.
        _local_state[_STATE_KEY] = state
    return state


def is_due(state, now):
    """True when a real check should run: nothing published, or it is stale."""
    if not state:
        return True
    ts = state.get('checked_at_ts')
    if not isinstance(ts, (int, float)):
        return True  # malformed payload must not wedge the check off
    return (now - ts) >= CHECK_INTERVAL_SECONDS
