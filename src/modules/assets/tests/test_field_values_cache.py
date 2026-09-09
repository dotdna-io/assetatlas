# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Explorer field-values Redis cache (#1410).

The cache stores the service-level result of get_field_values under a key
that mixes the request shape with two version stamps (field-catalog and
asset-data). The contract under test:

  * a stored result comes back byte-identical (deep-equal, including
    ``total_distinct: None`` when include_total was False);
  * distinct inputs derive distinct keys — and key-order variants of the
    same filters collapse to ONE key, in-process and across processes
    (no ``hash()`` — PYTHONHASHSEED differs per worker, #1136);
  * bumping either stamp changes the key, so the next lookup misses;
  * Redis unavailable (stamp read None, no client) or raising bypasses the
    cache silently — no exception, and NO per-worker memory fallback;
  * set() files the result under the SAME stamps the lookup derived, so a
    bump landing between miss and store cannot create a stale hit.

No Redis server is required: the client is a dict-backed fake throughout.
"""

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.modules.assets.services.field_values_cache as fvc
from src.modules.assets.services.field_values_cache import (
    DEFAULT_TTL_SECONDS,
    KEY_PREFIX,
    _KEY_ATTR,
    _REDIS_DOWN_SECONDS,
    FieldValuesCache,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


class FakeRedis:
    """Dict-backed stand-in for the two redis client calls the cache uses."""

    def __init__(self):
        self.store = {}
        self.setex_calls = []
        self.raise_on = set()  # 'get' | 'setex'

    def get(self, key):
        if "get" in self.raise_on:
            raise ConnectionError("boom")
        value = self.store.get(key)
        return value.encode() if value is not None else None

    def setex(self, key, ttl, value):
        if "setex" in self.raise_on:
            raise ConnectionError("boom")
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value


class FakeStamps:
    """Mutable stamp stand-ins; None models an unavailable source."""

    def __init__(self, catalog=1, data=1):
        self.catalog = catalog
        self.data = data


def _cache(redis_client, stamps):
    return FieldValuesCache(
        redis_client_provider=lambda: redis_client,
        catalog_stamp=lambda: stamps.catalog,
        data_stamp=lambda: stamps.data,
    )


def _payload(**over):
    base = {
        "field": "static:Hostname",
        "limit": 50,
        "search": None,
        "filters": None,
        "include_total": True,
    }
    base.update(over)
    return base


RESULT = {
    "field": "static:Hostname",
    "values": [{"value": "web-01", "count": 3}, {"value": "db-01", "count": 1}],
    "total_distinct": 2,
}


class TestRoundTrip:
    def test_miss_then_store_then_hit_returns_identical_result(self):
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps())

        assert cache.get(_payload()) is None          # first lookup: miss
        cache.set(_payload(), RESULT)
        assert cache.get(_payload()) == RESULT        # second lookup: hit
        assert len(redis.store) == 1

    def test_total_distinct_none_round_trips(self):
        """include_total=False stores total_distinct: None; it must come back
        as None, not 0 or missing."""
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps())
        result = {**RESULT, "total_distinct": None}

        cache.set(_payload(include_total=False), result)

        assert cache.get(_payload(include_total=False)) == result

    def test_every_key_carries_the_prefix(self):
        redis = FakeRedis()
        _cache(redis, FakeStamps()).set(_payload(), RESULT)
        assert next(iter(redis.store)).startswith(KEY_PREFIX)

    def test_ttl_is_the_safety_net_default(self):
        redis = FakeRedis()
        _cache(redis, FakeStamps()).set(_payload(), RESULT)
        assert redis.setex_calls[0][1] == DEFAULT_TTL_SECONDS == 900


class TestKeyDerivation:
    def test_distinct_inputs_derive_distinct_keys(self):
        stamps = FakeStamps()
        cache = _cache(FakeRedis(), stamps)
        variants = [
            _payload(),
            _payload(field="static:Category"),                    # field
            _payload(limit=10),                                   # limit
            _payload(search="web"),                               # search set
            _payload(search=""),                                  # '' vs None
            _payload(filters=[{"op": "eq", "value": "server"}]),  # filters
            _payload(include_total=False),                        # include_total
        ]
        keys = {cache._derive_key(p) for p in variants}
        assert None not in keys
        assert len(keys) == len(variants)

    def test_search_none_and_empty_string_differ(self):
        cache = _cache(FakeRedis(), FakeStamps())
        assert cache._derive_key(_payload(search=None)) != cache._derive_key(
            _payload(search=""))

    def test_filters_key_order_variants_collapse(self):
        """The same filters as a JSON string with keys in a different order —
        and as an already-parsed list — must derive ONE key."""
        cache = _cache(FakeRedis(), FakeStamps())
        as_string = _payload(
            filters='[{"field_id": {"kind": "static", "name": "Category"}, "op": "eq", "value": "server"}]')
        reordered = _payload(
            filters='[{"op": "eq", "value": "server", "field_id": {"name": "Category", "kind": "static"}}]')
        as_list = _payload(
            filters=[{"field_id": {"kind": "static", "name": "Category"}, "op": "eq", "value": "server"}])
        keys = {cache._derive_key(p) for p in (as_string, reordered, as_list)}
        assert len(keys) == 1

    def test_semantically_different_filters_differ(self):
        cache = _cache(FakeRedis(), FakeStamps())
        a = _payload(filters='[{"op": "eq", "value": "server"}]')
        b = _payload(filters='[{"op": "eq", "value": "desktop"}]')
        assert cache._derive_key(a) != cache._derive_key(b)

    def test_two_instances_derive_the_same_key(self):
        """Same-process proxy for two gunicorn workers."""
        payload_a, payload_b = _payload(search="web"), _payload(search="web")
        key_a = _cache(FakeRedis(), FakeStamps())._derive_key(payload_a)
        key_b = _cache(FakeRedis(), FakeStamps())._derive_key(payload_b)
        assert key_a == key_b

    def test_subprocess_derives_the_same_key(self):
        """A fresh interpreter (fresh PYTHONHASHSEED) must derive the same key
        for the same payload — the regression test for the hash() trap."""
        code = (
            "from src.modules.assets.services.field_values_cache import FieldValuesCache\n"
            "cache = FieldValuesCache(redis_client_provider=lambda: None,\n"
            "                         catalog_stamp=lambda: 7, data_stamp=lambda: 3)\n"
            "print(cache._derive_key({'field': 'static:Hostname', 'limit': 50,\n"
            "    'search': 'web', 'filters': '[{\"op\": \"eq\", \"value\": \"server\"}]',\n"
            "    'include_total': True}))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        other_process_key = out.stdout.strip()

        this_process_key = FieldValuesCache(
            redis_client_provider=lambda: None,
            catalog_stamp=lambda: 7, data_stamp=lambda: 3,
        )._derive_key(_payload(
            search="web", filters='[{"op": "eq", "value": "server"}]'))

        assert other_process_key == this_process_key


class TestStampVersioning:
    def test_bumping_either_stamp_changes_the_key(self):
        stamps = FakeStamps()
        cache = _cache(FakeRedis(), stamps)
        base = cache._derive_key(_payload())

        stamps.catalog += 1
        after_catalog_bump = cache._derive_key(_payload())
        assert after_catalog_bump != base

        stamps.data += 1
        after_data_bump = cache._derive_key(_payload())
        assert after_data_bump != after_catalog_bump

    def test_a_bump_invalidates_stored_entries(self):
        redis = FakeRedis()
        stamps = FakeStamps()
        cache = _cache(redis, stamps)
        cache.set(_payload(), RESULT)

        assert cache.get(_payload()) == RESULT
        stamps.data += 1                    # a writer committed
        assert cache.get(_payload()) is None  # implicit invalidation

    def test_set_files_under_the_stamps_the_lookup_used(self):
        """A bump landing between the miss and the store must NOT file
        pre-bump data under the new stamp — that would be a stale hit."""
        redis = FakeRedis()
        stamps = FakeStamps()
        cache = _cache(redis, stamps)
        payload = _payload()

        assert cache.get(payload) is None   # miss at stamps (1, 1)
        stamps.data += 1                    # writer commits mid-request
        cache.set(payload, RESULT)          # must still file under (1, 1)

        # At the new stamp there is no stale hit…
        assert cache.get(_payload()) is None
        # …the entry exists only under the OLD stamps: rewind and it hits.
        stamps.data = 1
        assert cache.get(_payload()) == RESULT


class TestBypass:
    def test_unavailable_catalog_stamp_disables_the_cache(self):
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps(catalog=None))
        assert cache.get(_payload()) is None
        cache.set(_payload(), RESULT)
        assert redis.store == {}
        assert cache.get(_payload()) is None  # no in-memory fallback

    def test_unavailable_data_stamp_disables_the_cache(self):
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps(data=None))
        assert cache.get(_payload()) is None
        cache.set(_payload(), RESULT)
        assert redis.store == {}

    def test_no_redis_client_bypasses(self):
        cache = _cache(None, FakeStamps())
        assert cache.get(_payload()) is None
        cache.set(_payload(), RESULT)         # must not raise
        assert cache.get(_payload()) is None

    def test_redis_get_error_falls_through_silently(self):
        redis = FakeRedis()
        redis.raise_on.add("get")
        cache = _cache(redis, FakeStamps())
        assert cache.get(_payload()) is None  # no exception escapes

    def test_redis_set_error_is_swallowed(self):
        redis = FakeRedis()
        redis.raise_on.add("setex")
        cache = _cache(redis, FakeStamps())
        cache.set(_payload(), RESULT)         # must not raise
        assert redis.store == {}

    def test_stamp_read_raising_bypasses(self):
        cache = FieldValuesCache(
            redis_client_provider=lambda: FakeRedis(),
            catalog_stamp=lambda: (_ for _ in ()).throw(ConnectionError("boom")),
            data_stamp=lambda: 1,
        )
        assert cache.get(_payload()) is None
        cache.set(_payload(), RESULT)         # must not raise

    def test_unparseable_filters_string_bypasses(self):
        """Invalid filters JSON: the repository raises its own validation
        error on the live path; the cache must not raise a different one."""
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps())
        payload = _payload(filters="{not json")
        assert cache.get(payload) is None
        cache.set(payload, RESULT)            # no-op, no raise
        assert redis.store == {}

    def test_malformed_entry_counts_as_a_miss(self):
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps())
        payload = _payload()
        payload2 = _payload()
        cache.get(payload)                    # derives + stashes the key
        redis.store[payload["_field_values_cache_key"]] = '{"not": "a result"}'
        assert cache.get(payload2) is None

    def test_set_without_a_prior_get_derives_fresh(self):
        redis = FakeRedis()
        cache = _cache(redis, FakeStamps())
        cache.set(_payload(), RESULT)
        assert len(redis.store) == 1


class TestRedisDownLatch:
    """After a Redis error the cache latches off for _REDIS_DOWN_SECONDS, so a
    hung-but-connected Redis costs one socket timeout per window instead of
    up to four (two stamp reads + get + setex) on every request."""

    def test_an_error_latches_the_cache_off_for_a_window(self, monkeypatch):
        redis = FakeRedis()
        redis.raise_on.add("get")
        cache = _cache(redis, FakeStamps())

        assert cache.get(_payload()) is None          # Redis error trips the latch
        redis.raise_on.discard("get")                  # Redis healthy again

        payload = _payload()
        assert cache.get(payload) is None              # latched: no key derived…
        assert payload[_KEY_ATTR] is None
        cache.set(payload, RESULT)
        assert redis.store == {}                       # …and set() stores nothing

        monkeypatch.setattr(                           # window expires
            fvc, "time",
            SimpleNamespace(
                monotonic=lambda: time.monotonic() + _REDIS_DOWN_SECONDS + 1))
        assert cache.get(_payload()) is None           # consulted again: real miss
        cache.set(_payload(), RESULT)
        assert cache.get(_payload()) == RESULT         # round-trip restored

    def test_a_raising_client_provider_bypasses_and_latches(self):
        calls = []

        def provider():
            calls.append(1)
            raise ConnectionError("boom")

        cache = FieldValuesCache(
            redis_client_provider=provider,
            catalog_stamp=lambda: 1, data_stamp=lambda: 1)
        assert cache.get(_payload()) is None           # no exception escapes
        assert cache.get(_payload()) is None
        assert len(calls) == 1                         # second call never reached Redis


@pytest.fixture(autouse=True)
def _no_redis_url(monkeypatch):
    """Hermetic: a REDIS_URL in the dev shell must not leak into these tests."""
    monkeypatch.delenv("REDIS_URL", raising=False)
