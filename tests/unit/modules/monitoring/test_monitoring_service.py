# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Prometheus proxy service."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.api.middleware.error_handler import APIError, ValidationError
from src.modules.monitoring.service import MonitoringService


@pytest.fixture
def service():
    return MonitoringService("http://prom-test:9090")


def _resp(status_code: int, json_body: dict):
    """Build a fake requests.Response."""
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.json.return_value = json_body
    return r


def test_query_happy_path(service):
    fake = _resp(200, {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [123, "1"]}]},
    })
    with patch("src.modules.monitoring.service.requests.get", return_value=fake) as get:
        out = service.query("up", time="123")
    args, kwargs = get.call_args
    assert args[0] == "http://prom-test:9090/api/v1/query"
    assert kwargs["params"] == {"query": "up", "time": "123"}
    assert kwargs["timeout"] == 10
    assert out == {"resultType": "vector", "result": [{"metric": {}, "value": [123, "1"]}]}


def test_query_omits_time_when_none(service):
    fake = _resp(200, {"status": "success", "data": {"resultType": "vector", "result": []}})
    with patch("src.modules.monitoring.service.requests.get", return_value=fake) as get:
        service.query("up", time=None)
    _, kwargs = get.call_args
    assert "time" not in kwargs["params"]


def test_query_range_happy_path(service):
    fake = _resp(200, {
        "status": "success",
        "data": {"resultType": "matrix", "result": []},
    })
    with patch("src.modules.monitoring.service.requests.get", return_value=fake) as get:
        out = service.query_range("up", start="100", end="200", step="15s")
    args, kwargs = get.call_args
    assert args[0] == "http://prom-test:9090/api/v1/query_range"
    assert kwargs["params"] == {"query": "up", "start": "100", "end": "200", "step": "15s"}
    assert out["resultType"] == "matrix"


def test_prometheus_4xx_raises_validation_error(service):
    fake = _resp(400, {"status": "error", "errorType": "bad_data", "error": "parse error: bogus"})
    with patch("src.modules.monitoring.service.requests.get", return_value=fake):
        with pytest.raises(ValidationError) as excinfo:
            service.query("bogus(")
    assert "parse error: bogus" in str(excinfo.value)


def test_prometheus_5xx_raises_api_error_503(service):
    fake = _resp(502, {"status": "error", "errorType": "execution", "error": "internal"})
    with patch("src.modules.monitoring.service.requests.get", return_value=fake):
        with pytest.raises(APIError) as excinfo:
            service.query("up")
    assert excinfo.value.status_code == 503


def test_connection_error_raises_api_error_503(service):
    with patch("src.modules.monitoring.service.requests.get",
               side_effect=requests.exceptions.ConnectionError("connect refused")):
        with pytest.raises(APIError) as excinfo:
            service.query("up")
    assert excinfo.value.status_code == 503
    assert "unreachable" in str(excinfo.value).lower()


def test_timeout_raises_api_error_503(service):
    with patch("src.modules.monitoring.service.requests.get",
               side_effect=requests.exceptions.Timeout("timed out")):
        with pytest.raises(APIError) as excinfo:
            service.query("up")
    assert excinfo.value.status_code == 503


def test_strips_trailing_slash_from_url():
    s = MonitoringService("http://prom:9090/")
    assert s.prometheus_url == "http://prom:9090"


# ---------------------------------------------------------------------------
# ping() — cheap reachability probe backing /api/monitoring/status
# ---------------------------------------------------------------------------

def test_ping_reachable(service):
    fake = _resp(200, {})
    with patch("src.modules.monitoring.service.requests.get", return_value=fake) as get:
        reachable, detail = service.ping()
    assert get.call_args[0][0] == "http://prom-test:9090/-/healthy"
    assert reachable is True
    assert detail is None


def test_ping_http_error_is_not_reachable(service):
    fake = _resp(503, {})
    with patch("src.modules.monitoring.service.requests.get", return_value=fake):
        reachable, detail = service.ping()
    assert reachable is False
    assert "503" in detail


def test_ping_connection_error_never_raises(service):
    with patch("src.modules.monitoring.service.requests.get",
               side_effect=requests.exceptions.ConnectionError("refused")):
        reachable, detail = service.ping()
    assert reachable is False
    assert "unreachable" in detail.lower()


# ---------------------------------------------------------------------------
# storage_stats() — TSDB usage + applied retention for the Settings panel
# ---------------------------------------------------------------------------

_SCALARS = {
    "prometheus_tsdb_storage_blocks_bytes": "1000000000",
    "prometheus_tsdb_wal_storage_size_bytes": "200000000",
    "prometheus_tsdb_head_series": "21456",
    "sum(rate(prometheus_tsdb_head_samples_appended_total[1h]))": "3100",
    "prometheus_tsdb_lowest_timestamp_seconds": "1000",  # ancient -> huge span guard
}


def _dispatch(url, params=None, timeout=None):
    if url.endswith("/-/healthy"):
        return _resp(200, {})
    if url.endswith("/api/v1/status/flags"):
        return _resp(200, {"status": "success",
                           "data": {"storage.tsdb.retention.time": "15d"}})
    if url.endswith("/api/v1/query"):
        val = _SCALARS.get(params["query"])
        result = [] if val is None else [{"metric": {}, "value": [0, val]}]
        return _resp(200, {"status": "success",
                           "data": {"resultType": "vector", "result": result}})
    raise AssertionError(f"unexpected url {url}")


def test_storage_stats_happy_path(service):
    with patch("src.modules.monitoring.service.requests.get", side_effect=_dispatch):
        out = service.storage_stats()
    assert out["reachable"] is True
    assert out["disk_used_bytes"] == 1200000000  # blocks + WAL
    assert out["head_series"] == 21456
    assert out["samples_per_second"] == 3100.0
    assert out["applied_retention"] == "15d"
    assert out["applied_retention_seconds"] == 1296000
    # lowest_timestamp=1000 -> span > 10y -> guarded to None
    assert out["history_seconds"] is None


def test_storage_stats_partial_when_a_metric_missing(service):
    def dispatch(url, params=None, timeout=None):
        if url.endswith("/api/v1/query") and params["query"] == "prometheus_tsdb_head_series":
            return _resp(200, {"status": "success",
                               "data": {"resultType": "vector", "result": []}})
        return _dispatch(url, params, timeout)

    with patch("src.modules.monitoring.service.requests.get", side_effect=dispatch):
        out = service.storage_stats()
    assert out["head_series"] is None
    assert out["disk_used_bytes"] == 1200000000  # others still populate


def test_storage_stats_unreachable_returns_early(service):
    with patch("src.modules.monitoring.service.requests.get",
               side_effect=requests.exceptions.ConnectionError("refused")) as get:
        out = service.storage_stats()
    assert out["reachable"] is False
    assert out["applied_retention"] is None
    assert out["disk_used_bytes"] is None
    # Pinged once and bailed — did not fire one request per metric.
    assert get.call_count == 1
