# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the shared asset-data version stamp (#1410).

The stamp is a single Redis counter every asset-data-changing event INCRs;
the Explorer field-values cache mixes it into its keys so any such write
implicitly invalidates every cached entry. The contract that makes it safe to
call from anywhere — request handlers, the pipeline worker's ``finally``, the
scheduler — is the same as the field-catalog stamp's (#1413):

  * ``bump()`` NEVER raises;
  * ``get()`` returns None on any failure (and when no Redis is configured);
  * every Redis error is swallowed and logged at DEBUG.

No Redis server is required here: the client is a MagicMock throughout.
"""

import logging
from unittest.mock import MagicMock

import pytest

from src.modules.shared import asset_data_version
from src.modules.shared.asset_data_version import (
    STAMP_KEY,
    RedisAssetDataVersionSource,
)


@pytest.fixture(autouse=True)
def _no_redis_url(monkeypatch):
    """Hermetic: a REDIS_URL in the dev shell must not leak into these tests."""
    monkeypatch.delenv("REDIS_URL", raising=False)


class TestUnavailable:
    def test_no_url_get_returns_none_and_bump_is_noop(self):
        src = RedisAssetDataVersionSource()  # no URL, env cleared
        assert src.get() is None
        src.bump()  # must not raise

    def test_client_build_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            "redis.Redis.from_url", MagicMock(side_effect=Exception("no redis"))
        )
        src = RedisAssetDataVersionSource(url="redis://localhost:6379/0")
        assert src.get() is None
        src.bump()  # must not raise
        # A failed build is not retried within the process.
        assert src.get() is None


class TestGet:
    def _source_with_client(self, client):
        src = RedisAssetDataVersionSource(url="redis://localhost:6379/0")
        src._client = client
        return src

    def test_returns_int_value(self):
        client = MagicMock()
        client.get.return_value = b"7"
        assert self._source_with_client(client).get() == 7
        client.get.assert_called_once_with(STAMP_KEY)

    def test_missing_key_is_zero_not_unavailable(self):
        """Never INCR'd yet: Redis is reachable, so the stamp is live at 0 —
        distinct from None, which would disable the field-values cache."""
        client = MagicMock()
        client.get.return_value = None
        assert self._source_with_client(client).get() == 0

    def test_redis_error_returns_none_and_logs_debug(self, caplog):
        client = MagicMock()
        client.get.side_effect = ConnectionError("boom")
        with caplog.at_level(logging.DEBUG, logger="src.modules.shared.asset_data_version"):
            assert self._source_with_client(client).get() is None
        assert any("stamp read failed" in r.getMessage() for r in caplog.records)

    def test_unparsable_value_returns_none(self):
        client = MagicMock()
        client.get.return_value = b"not-an-int"
        assert self._source_with_client(client).get() is None


class TestBump:
    def test_incr_never_raises_and_logs_debug(self, caplog):
        client = MagicMock()
        client.incr.side_effect = ConnectionError("boom")
        src = RedisAssetDataVersionSource(url="redis://localhost:6379/0")
        src._client = client
        with caplog.at_level(logging.DEBUG, logger="src.modules.shared.asset_data_version"):
            src.bump()  # the whole point: must not raise
        assert any("bump failed" in r.getMessage() for r in caplog.records)

    def test_incr_uses_the_stamp_key(self):
        client = MagicMock()
        src = RedisAssetDataVersionSource(url="redis://localhost:6379/0")
        src._client = client
        src.bump()
        client.incr.assert_called_once_with(STAMP_KEY)


class TestModuleLevel:
    def test_get_and_bump_delegate_to_the_default_source(self, monkeypatch):
        fake = MagicMock()
        fake.get.return_value = 42
        monkeypatch.setattr(asset_data_version, "_default_source", fake)
        assert asset_data_version.get() == 42
        asset_data_version.bump()
        fake.bump.assert_called_once_with()

    def test_default_source_is_a_stable_singleton(self):
        assert asset_data_version.default_source() is asset_data_version.default_source()
