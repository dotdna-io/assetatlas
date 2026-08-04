# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-process schedule-change signal.

API and scheduler-worker containers don't share an APScheduler. When the
API mutates an integration's schedule/status/cron, it publishes the id
to a Redis set that the worker drains every 10s. Best-effort: a 2-min
full resync in the worker is the fallback.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Union
from uuid import UUID

logger = logging.getLogger(__name__)

DIRTY_SET_KEY = 'scheduler:dirty_schedules'
DIRTY_SET_TTL_SECONDS = 600

_default_client = None
_client_lock = threading.Lock()


def _get_default_client():
    global _default_client
    if _default_client is not None:
        return _default_client
    with _client_lock:
        if _default_client is not None:
            return _default_client
        redis_url = os.getenv('REDIS_URL')
        if not redis_url:
            return None
        import redis
        _default_client = redis.from_url(redis_url)
    return _default_client


def signal_schedule_change(
    integration_id: Union[UUID, str],
    redis_client=None,
) -> None:
    """Publish a dirty signal so the scheduler worker re-syncs this id."""
    try:
        client = redis_client or _get_default_client()
        if client is None:
            return
        pipe = client.pipeline()
        pipe.sadd(DIRTY_SET_KEY, str(integration_id))
        pipe.expire(DIRTY_SET_KEY, DIRTY_SET_TTL_SECONDS)
        pipe.execute()
    except Exception as e:
        logger.debug(f"Could not signal schedule change via Redis: {e}")
