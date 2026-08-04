# SPDX-License-Identifier: AGPL-3.0-or-later
"""Thin HTTP client to Prometheus's HTTP API.

Used by the /api/monitoring/query and /api/monitoring/query_range proxy routes.
Maps Prometheus 4xx → ValidationError (400 to caller), Prometheus 5xx and
network errors → _ServiceUnavailableError (503 to caller).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

from src.api.middleware.error_handler import APIError, ValidationError
from src.modules.monitoring.durations import parse_prometheus_duration

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10


class _ServiceUnavailableError(APIError):
    """503 — used when Prometheus is unreachable or returns 5xx."""
    status_code = 503
    error_type = 'service_unavailable'


class MonitoringService:
    """Thin pass-through to Prometheus's HTTP API."""

    def __init__(self, prometheus_url: str):
        self.prometheus_url = prometheus_url.rstrip('/')

    def query(self, query: str, time: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, str] = {"query": query}
        if time:
            params["time"] = time
        return self._get("/api/v1/query", params)

    def query_range(self, query: str, start: str, end: str, step: str) -> Dict[str, Any]:
        params = {"query": query, "start": start, "end": end, "step": step}
        return self._get("/api/v1/query_range", params)

    def ping(self) -> tuple[bool, Optional[str]]:
        """Cheap reachability probe against Prometheus's /-/healthy endpoint.

        Never raises — returns (reachable, detail). Used by the always-available
        /api/monitoring/status route so the Settings toggle can report whether the
        monitoring stack is actually deployed before/after enabling the dashboards.
        """
        try:
            response = requests.get(
                f"{self.prometheus_url}/-/healthy", timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.exceptions.RequestException:
            return False, "Prometheus is unreachable — is the monitoring stack deployed?"
        if response.status_code < 400:
            return True, None
        return False, f"Prometheus returned HTTP {response.status_code}"

    def storage_stats(self) -> Dict[str, Any]:
        """Best-effort TSDB storage + applied-retention summary for the Settings
        panel. Never raises.

        Pings first and early-returns when Prometheus is unreachable, so a down
        stack costs a single timeout instead of one per metric. Any individual
        metric that is missing comes back as None; the rest still populate.
        """
        reachable, detail = self.ping()
        result: Dict[str, Any] = {
            "reachable": reachable,
            "detail": detail,
            "applied_retention": None,
            "applied_retention_seconds": None,
            "disk_used_bytes": None,
            "head_series": None,
            "samples_per_second": None,
            "history_seconds": None,
        }
        if not reachable:
            return result

        blocks = self._scalar_query("prometheus_tsdb_storage_blocks_bytes")
        wal = self._scalar_query("prometheus_tsdb_wal_storage_size_bytes")
        if blocks is not None or wal is not None:
            result["disk_used_bytes"] = int((blocks or 0) + (wal or 0))

        series = self._scalar_query("prometheus_tsdb_head_series")
        if series is not None:
            result["head_series"] = int(series)

        samples = self._scalar_query(
            "sum(rate(prometheus_tsdb_head_samples_appended_total[1h]))"
        )
        if samples is not None:
            result["samples_per_second"] = samples

        lowest = self._scalar_query("prometheus_tsdb_lowest_timestamp_seconds")
        if lowest is not None and lowest > 0:
            span = time.time() - lowest
            # A fresh TSDB reports a max-sentinel lowest timestamp, so span goes
            # negative or absurdly large. Only surface a sane positive span
            # (< 10 years) so the storage projection never divides by garbage.
            if 0 < span < 10 * 31536000:
                result["history_seconds"] = int(span)

        applied = self._applied_retention()
        if applied:
            result["applied_retention"] = applied
            result["applied_retention_seconds"] = parse_prometheus_duration(applied)

        return result

    def _scalar_query(self, promql: str) -> Optional[float]:
        """Instant query -> first sample value as float. None on any error/empty
        result (never raises); callers treat a missing metric as 'unknown'."""
        try:
            data = self.query(promql)
            resultset = (data or {}).get("result") or []
            if not resultset:
                return None
            return float(resultset[0]["value"][1])
        except Exception:  # noqa: BLE001 — never-raise contract
            logger.debug("storage_stats scalar query failed: %s", promql, exc_info=True)
            return None

    def _applied_retention(self) -> Optional[str]:
        """storage.tsdb.retention.time from Prometheus's /status/flags, or None."""
        try:
            data = self._get("/api/v1/status/flags", {})
            value = (data or {}).get("storage.tsdb.retention.time")
            return value or None
        except Exception:  # noqa: BLE001 — never-raise contract
            logger.debug("storage_stats flags fetch failed", exc_info=True)
            return None

    def _get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        url = f"{self.prometheus_url}{path}"
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout as e:
            logger.warning("Prometheus request timed out: %s", e)
            raise _ServiceUnavailableError(
                "Prometheus query timed out — monitoring stack may be unreachable"
            ) from e
        except requests.exceptions.ConnectionError as e:
            logger.warning("Prometheus unreachable: %s", e)
            raise _ServiceUnavailableError(
                "Prometheus is unreachable — is the monitoring overlay running?"
            ) from e

        if 400 <= response.status_code < 500:
            body = self._safe_json(response)
            err_msg = body.get("error", "Prometheus rejected the query")
            raise ValidationError(f"Prometheus error: {err_msg}")

        if response.status_code >= 500:
            body = self._safe_json(response)
            err_msg = body.get("error", f"Prometheus HTTP {response.status_code}")
            raise _ServiceUnavailableError(f"Prometheus internal error: {err_msg}")

        body = self._safe_json(response)
        return body.get("data", {})

    @staticmethod
    def _safe_json(response: requests.Response) -> Dict[str, Any]:
        try:
            return response.json()
        except Exception:
            return {}
