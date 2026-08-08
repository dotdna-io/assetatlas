# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the update check's cross-process published state store.

Moved out of tests/unit/modules/alerts/test_update_check.py alongside the
store's move to src/modules/shared/update_check_state.py: the old store
resolved its Redis client via src.api.utils.caching, a domain -> API layering
violation (tests/unit/test_layering.py). The new module builds its client
straight from REDIS_URL and falls back to an in-process dict, so these tests
patch `_redis_client` (returns a client or None) rather than the old
`_cache_client` (returned a `(client, backend)` tuple).
"""
import json

import pytest

from src.modules.shared import update_check_state as ucs


class _FakeRedis:
    """Minimal stand-in: the state store only ever does get/set."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value


class TestStateStore:
    """The published check state — the cross-process pacing token."""

    @pytest.fixture(autouse=True)
    def _isolated_local_state(self, monkeypatch):
        # A fresh dict per test: without this, the in-process fallback (a
        # module-level dict) would let one test's write leak into the next.
        monkeypatch.setattr(ucs, '_local_state', {})

    def test_is_due_when_nothing_is_published(self):
        assert ucs.is_due(None, now=1000.0) is True

    def test_not_due_inside_the_interval(self):
        state = {'latest_version': '2026.32.14', 'checked_at_ts': 1000.0}
        now = 1000.0 + ucs.CHECK_INTERVAL_SECONDS - 1
        assert ucs.is_due(state, now=now) is False

    def test_due_at_exactly_the_interval(self):
        # Boundary matters: a strict `>` here would let clock jitter push the
        # real cadence out to 2x the interval, which is the trap the old
        # 24h-job / 12h-TTL pair fell into.
        state = {'latest_version': '2026.32.14', 'checked_at_ts': 1000.0}
        now = 1000.0 + ucs.CHECK_INTERVAL_SECONDS
        assert ucs.is_due(state, now=now) is True

    def test_state_without_a_timestamp_is_due(self):
        # A malformed or truncated payload must not wedge the check off forever.
        assert ucs.is_due({'latest_version': '2026.32.14'}, now=1000.0) is True

    def test_poll_is_shorter_than_the_check_interval(self):
        # The poll must be able to catch a due check inside one window.
        assert ucs.POLL_SECONDS < ucs.CHECK_INTERVAL_SECONDS

    def test_write_then_read_round_trips(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        written = ucs.write_state('2026.32.14', now=1000.0)
        assert ucs.read_state() == written
        assert written['latest_version'] == '2026.32.14'
        assert written['checked_at_ts'] == 1000.0

    def test_checked_at_is_a_z_suffixed_utc_string(self, monkeypatch):
        # The About tab renders this verbatim; keep the shape it already shows.
        fake = _FakeRedis()
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        written = ucs.write_state('2026.32.14', now=0.0)
        assert written['checked_at'] == '1970-01-01T00:00:00Z'

    def test_wire_format_is_json_not_pickle(self, monkeypatch):
        # Writer (scheduler) and reader (api worker) are different processes and
        # can differ in code version mid-deploy; pickle would couple them.
        fake = _FakeRedis()
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        ucs.write_state('2026.32.14', now=1000.0)
        raw = fake.store['assetatlas:update_check:state']
        assert json.loads(raw)['latest_version'] == '2026.32.14'

    def test_a_failed_check_is_published_too(self, monkeypatch):
        # Publishing the failure is what stops a poll storm (or an enabled but
        # unreachable box) from fetching on every single poll.
        fake = _FakeRedis()
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        written = ucs.write_state(None, now=1000.0)
        assert written['latest_version'] is None
        assert ucs.read_state()['checked_at_ts'] == 1000.0

    def test_a_failed_check_keeps_the_last_known_version(self, monkeypatch):
        # Publishing the failure paces the next check, but it must not blank
        # the version: the About tab reads this state, so a transient DNS blip
        # would otherwise flip the badge from "update available" back to "up to
        # date" for a whole window — while the alert the successful check
        # raised is still standing, saying the opposite.
        fake = _FakeRedis()
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        ucs.write_state('2026.33.1', now=1000.0)
        written = ucs.write_state(None, now=2000.0)
        assert written['latest_version'] == '2026.33.1'
        assert written['checked_at_ts'] == 2000.0  # still paces off the attempt

    def test_read_swallows_a_broken_client(self, monkeypatch):
        class _Broken:
            def get(self, key):
                raise RuntimeError('redis down')

        monkeypatch.setattr(ucs, '_redis_client', lambda: _Broken())
        assert ucs.read_state() is None

    def test_write_swallows_a_broken_client(self, monkeypatch):
        class _Broken:
            def set(self, *a, **k):
                raise RuntimeError('redis down')

        monkeypatch.setattr(ucs, '_redis_client', lambda: _Broken())
        # Must not raise, and must still hand the state back to the caller.
        assert ucs.write_state('2026.32.14', now=1.0)['latest_version'] == '2026.32.14'

    def test_write_failure_leaves_state_readable_via_local_fallback(self, monkeypatch):
        # Finding 2: write_state's except branch must also populate
        # _local_state. Without it, a Redis that is CONFIGURED but merely
        # unreachable at call time (from_url() connects lazily, so the client
        # exists but every operation fails) makes the store remember nothing
        # at all — worse than having no REDIS_URL set, where the plain
        # no-client branch already goes through _local_state.
        class _SetRaises:
            def set(self, *a, **k):
                raise RuntimeError('redis down')

        monkeypatch.setattr(ucs, '_redis_client', lambda: _SetRaises())
        written = ucs.write_state('2026.32.14', now=1000.0)
        # Read through the plain "no client" path so this isolates what
        # write_state itself stored, independent of read_state's own
        # fallback (covered separately below).
        monkeypatch.setattr(ucs, '_redis_client', lambda: None)
        assert ucs.read_state() == written

    def test_read_failure_falls_back_to_local_state(self, monkeypatch):
        # Finding 2: read_state's except branch must also fall back to
        # _local_state — a per-worker floor is the point (mirrors the
        # scheduler's in-process floor), so a configured-but-down Redis
        # should degrade to per-worker memory rather than to no memory at
        # all. health.py has no floor of its own the way the scheduler does,
        # so without this every GET while Redis is down would fetch fresh.
        monkeypatch.setattr(ucs, '_redis_client', lambda: None)
        seeded = ucs.write_state('2026.32.14', now=1000.0)  # -> _local_state

        class _GetRaises:
            def get(self, key):
                raise RuntimeError('redis down')

        monkeypatch.setattr(ucs, '_redis_client', lambda: _GetRaises())
        assert ucs.read_state() == seeded

    def test_read_returns_none_with_no_client_at_all(self, monkeypatch):
        monkeypatch.setattr(ucs, '_redis_client', lambda: None)
        assert ucs.read_state() is None

    def test_unparseable_payload_reads_as_no_state(self, monkeypatch):
        fake = _FakeRedis()
        fake.store['assetatlas:update_check:state'] = b'not json{'
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        assert ucs.read_state() is None

    def test_non_object_payload_reads_as_no_state(self, monkeypatch):
        # Valid JSON, wrong shape. Both callers do `state.get(...)`, so handing
        # a list back would raise AttributeError out of is_due() — a 500 on the
        # About tab and a logged failure on every scheduler poll until the key
        # expires (12h).
        fake = _FakeRedis()
        fake.store['assetatlas:update_check:state'] = b'["2026.33.1"]'
        monkeypatch.setattr(ucs, '_redis_client', lambda: fake)
        assert ucs.read_state() is None

    def test_write_state_round_trips_through_the_in_process_fallback(self):
        # No REDIS_URL — the default locally and in CI — so _redis_client()
        # returns None with nothing monkeypatched, and both calls go through
        # the module-level dict. The old (client, backend) design could not
        # express "no client at all, but still round-trips" as a single case
        # separate from "no client and nothing stored"; the new fallback IS
        # the store, not a degraded read.
        written = ucs.write_state('2026.32.14', now=1000.0)
        assert ucs.read_state() == written
        assert written['latest_version'] == '2026.32.14'

    def test_in_process_fallback_is_isolated_per_test(self):
        # Proves the autouse fixture actually resets state: if it did not,
        # this test would see the write from the previous test instead of
        # "nothing published".
        assert ucs.read_state() is None


class TestRedisClient:
    """`_redis_client()` itself.

    Finding 6: every test in TestStateStore monkeypatches `_redis_client`
    away, so the one function the src.api relocation actually changed — the
    `REDIS_URL` env var name, the deferred `import redis`, the `from_url()`
    call — had no coverage of its own. A typo'd env var name would ship green.
    """

    @pytest.fixture(autouse=True)
    def _no_memoised_client(self, monkeypatch):
        # The client is memoised module-wide; start each test from cold so one
        # test's client cannot answer another's assertion.
        monkeypatch.setattr(ucs, '_client_cache', (None, None))

    def test_no_redis_url_returns_none(self, monkeypatch):
        monkeypatch.delenv('REDIS_URL', raising=False)
        assert ucs._redis_client() is None

    def test_redis_url_builds_a_client_for_that_url(self, monkeypatch):
        # redis.Redis.from_url() connects lazily — no socket is opened here —
        # so asserting on the returned client's connection kwargs is enough,
        # and safe to run with no Redis actually listening.
        monkeypatch.setenv('REDIS_URL', 'redis://myhost:6380/2')
        client = ucs._redis_client()
        assert client is not None
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs['host'] == 'myhost'
        assert kwargs['port'] == 6380
        assert kwargs['db'] == 2

    def test_client_is_reused_across_calls(self, monkeypatch):
        # from_url() builds a fresh ConnectionPool each call, so rebuilding per
        # read_state() would mean a new TCP connect on every About-tab GET.
        monkeypatch.setenv('REDIS_URL', 'redis://myhost:6380/2')
        assert ucs._redis_client() is ucs._redis_client()

    def test_a_changed_url_builds_a_new_client(self, monkeypatch):
        monkeypatch.setenv('REDIS_URL', 'redis://myhost:6380/2')
        first = ucs._redis_client()
        monkeypatch.setenv('REDIS_URL', 'redis://otherhost:6379/0')
        second = ucs._redis_client()
        assert second is not first
        assert second.connection_pool.connection_kwargs['host'] == 'otherhost'
