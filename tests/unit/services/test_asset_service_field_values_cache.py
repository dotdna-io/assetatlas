# SPDX-License-Identifier: AGPL-3.0-or-later
"""AssetService.get_field_values consults the stamp-keyed Redis cache (#1410).

Service-level contract:

  * first call computes via the repository and stores; an identical second
    call is served from the cache — the repo is NOT called again and the
    returned dict is deep-equal (including ``total_distinct: None`` when
    include_total=False);
  * key-order variants of the same filters hit the same entry;
  * bumping either version stamp makes the next call recompute;
  * Redis unavailable (stamp None / client raising) bypasses the cache — the
    repo is called every time and no exception escapes;
  * the hardcoded ``virtual:confidence_level`` branch never touches the cache,
    and neither does ``virtual:alert`` (answered from the alerts table, whose
    writers never bump the stamps);
  * a service constructed without a cache behaves exactly as before.

The repo is a MagicMock; Redis is a dict-backed fake; stamps are fakes.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.assets.services.asset_service import AssetService
from src.modules.assets.services.field_values_cache import FieldValuesCache

VALUES = [{"value": "server", "count": 5}, {"value": "desktop", "count": 3}]


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.raise_on_get = False

    def get(self, key):
        if self.raise_on_get:
            raise ConnectionError("boom")
        value = self.store.get(key)
        return value.encode() if value is not None else None

    def setex(self, key, ttl, value):
        self.store[key] = value


class FakeStamps:
    def __init__(self, catalog=1, data=1):
        self.catalog = catalog
        self.data = data


_DEFAULT = object()


def _service(redis_client=_DEFAULT, stamps=None, repo_values=None):
    """(service, repo, redis, stamps) with a real FieldValuesCache over fakes.

    redis_client=_DEFAULT builds a fresh FakeRedis; pass None explicitly for
    the no-client (Redis unavailable) path.
    """
    repo = MagicMock()
    repo.get_field_values.return_value = (
        repo_values if repo_values is not None else VALUES, len(VALUES),
    )
    redis = FakeRedis() if redis_client is _DEFAULT else redis_client
    stamps = stamps if stamps is not None else FakeStamps()
    cache = FieldValuesCache(
        redis_client_provider=lambda: redis,
        catalog_stamp=lambda: stamps.catalog,
        data_stamp=lambda: stamps.data,
    )
    return AssetService(repository=repo, field_values_cache=cache), repo, redis, stamps


class TestCaching:
    def test_first_call_computes_second_call_hits(self):
        svc, repo, redis, _stamps = _service()

        first = svc.get_field_values("static:Category")
        assert repo.get_field_values.call_count == 1
        assert first == {
            "field": "static:Category", "values": VALUES, "total_distinct": 2,
        }
        assert len(redis.store) == 1

        second = svc.get_field_values("static:Category")
        assert repo.get_field_values.call_count == 1  # NOT called again
        assert second == first

    def test_include_total_false_caches_the_none(self):
        svc, repo, _redis, _stamps = _service()
        repo.get_field_values.return_value = (VALUES, None)

        first = svc.get_field_values("static:Category", include_total=False)
        assert first["total_distinct"] is None
        second = svc.get_field_values("static:Category", include_total=False)
        assert repo.get_field_values.call_count == 1
        assert second == first
        assert second["total_distinct"] is None

    def test_distinct_inputs_do_not_share_entries(self):
        svc, repo, _redis, _stamps = _service()
        svc.get_field_values("static:Category")
        svc.get_field_values("static:Category", limit=10)
        svc.get_field_values("static:Category", search="")
        svc.get_field_values("static:Category", include_total=False)
        assert repo.get_field_values.call_count == 4

    def test_filters_key_order_variants_hit_the_same_entry(self):
        svc, repo, _redis, _stamps = _service()
        filters_a = [{"field_id": {"kind": "static", "name": "Category"}, "op": "eq", "value": "server"}]
        filters_b = [{"op": "eq", "value": "server", "field_id": {"name": "Category", "kind": "static"}}]

        svc.get_field_values("static:Category", filters=filters_a)
        svc.get_field_values("static:Category", filters=filters_b)

        assert repo.get_field_values.call_count == 1

    def test_bumping_the_data_stamp_recomputes(self):
        svc, repo, _redis, stamps = _service()
        svc.get_field_values("static:Category")
        stamps.data += 1
        svc.get_field_values("static:Category")
        assert repo.get_field_values.call_count == 2

    def test_bumping_the_catalog_stamp_recomputes(self):
        svc, repo, _redis, stamps = _service()
        svc.get_field_values("static:Category")
        stamps.catalog += 1
        svc.get_field_values("static:Category")
        assert repo.get_field_values.call_count == 2


class TestBypass:
    def test_unavailable_stamp_bypasses_and_never_caches(self):
        svc, repo, redis, stamps = _service()
        stamps.data = None  # Redis unconfigured: get() -> None

        svc.get_field_values("static:Category")
        svc.get_field_values("static:Category")

        assert repo.get_field_values.call_count == 2  # no caching at all
        assert redis.store == {}                      # no in-memory fallback

    def test_no_redis_client_bypasses(self):
        svc, repo, _redis, _stamps = _service(redis_client=None)
        svc.get_field_values("static:Category")
        svc.get_field_values("static:Category")
        assert repo.get_field_values.call_count == 2

    def test_redis_raising_still_serves_the_live_result(self):
        redis = FakeRedis()
        redis.raise_on_get = True
        svc, repo, _redis, _stamps = _service(redis_client=redis)

        result = svc.get_field_values("static:Category")

        assert result["values"] == VALUES
        assert repo.get_field_values.call_count == 1

    def test_invalid_field_error_still_propagates_and_is_not_cached(self):
        svc, repo, redis, _stamps = _service()
        repo.get_field_values.side_effect = ValueError("bad field")

        with pytest.raises(ValueError):
            svc.get_field_values("static:Category")

        assert redis.store == {}


class TestStaticBranch:
    def test_confidence_level_never_touches_the_cache(self):
        repo = MagicMock()
        cache = MagicMock()
        svc = AssetService(repository=repo, field_values_cache=cache)

        result = svc.get_field_values("virtual:confidence_level")

        assert result["total_distinct"] == 3
        cache.get.assert_not_called()
        cache.set.assert_not_called()
        repo.get_field_values.assert_not_called()

    def test_alert_never_touches_the_cache(self):
        """virtual:alert reads the alerts table, whose writers never bump the
        version stamps — a stamp-keyed entry would serve a stale alert list.
        The query is tens of rows, so the field always computes live."""
        repo = MagicMock()
        repo.get_field_values.return_value = (
            [{"value": "Data Source Stale", "count": 12}], 1,
        )
        cache = MagicMock()
        svc = AssetService(repository=repo, field_values_cache=cache)

        result = svc.get_field_values("virtual:alert")
        again = svc.get_field_values("virtual:alert")

        assert result["values"] == [{"value": "Data Source Stale", "count": 12}]
        assert again == result
        cache.get.assert_not_called()
        cache.set.assert_not_called()
        assert repo.get_field_values.call_count == 2  # every call is live

    def test_service_without_a_cache_behaves_as_before(self):
        repo = MagicMock()
        repo.get_field_values.return_value = (VALUES, 2)
        svc = AssetService(repository=repo)

        first = svc.get_field_values("static:Category")
        second = svc.get_field_values("static:Category")

        assert first == second
        assert repo.get_field_values.call_count == 2
