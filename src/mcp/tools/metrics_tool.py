# SPDX-License-Identifier: AGPL-3.0-or-later
"""query_metrics — the infra/observability surface for the chatbot (admin).

Read-only. Proxies the per-tile PromQL of the monitoring dashboards
(``ui/prod/src/pages/monitoring/dashboards/*.tsx``) through the EXISTING
Prometheus ``query_range`` proxy (``monitoring_service.query_range`` behind
``GET /api/monitoring/query_range``). No new infra — the only real work is
encoding each dashboard group's tile set here and keeping it in step with the
UI.

One tool, one ``group`` (system | database | redis | host | containers |
pipeline): it runs that dashboard's tiles over a time window and returns a
compact per-series summary (latest / min / max / avg) plus a ``table``
artifact. Time-series can't ride a line/gauge artifact yet — the chat artifact
union is donut/stacked_bar/table only (``artifacts.validate_envelope`` drops
anything else) — so the series are summarized into a table, exactly as the
dashboard-trend tool does.

Registered for the chat assistant in
``src/api/blueprints/chat/tool_bindings.py``; kept off the standalone MCP
server (chat and MCP diverge by design — see tasks/chatbot-tool-parity.md
Open Decision #6).
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.mcp.tools._common import unknown_enum

ToolReturn = Tuple[str, Optional[Dict[str, Any]]]

# Rate-window placeholder. PromQL itself uses ``{label="x"}`` braces, so a
# str.format/f-string substitution would collide; a sentinel + .replace() is
# brace-safe. Mirrors the UI's ``${rateWindow}`` interpolation (timeRange.ts).
_RATE = "__RATE__"

# Units are not in the PromQL — the dashboards carry them in JS format
# callbacks. We re-declare them per tile so the summary renders human values
# (e.g. a ratio as a %, bytes as GB) instead of raw floats.
_BYTES = "bytes"
_BYTES_S = "bytes_per_s"
_PERCENT = "percent"          # already 0..100
_RATIO = "ratio"             # 0..1, shown as %
_SECONDS = "seconds"
_COUNT = "count"
_OPS_S = "ops_per_s"
_CORES = "cores"
_LOAD = "load"
_RECORDS_S = "records_per_s"


def _tile(title: str, query: str, unit: str) -> Dict[str, str]:
    return {"title": title, "query": query, "unit": unit}


# Per-group tile sets, transcribed verbatim from the dashboard .tsx files (the
# ``${rateWindow}`` interpolations replaced with the _RATE sentinel; literal
# windows like [5m]/[1h] left as-is). Everything is run via query_range; the
# instant/gauge tiles are read as a (short) series whose latest point is the
# current value.
_TILES: Dict[str, List[Dict[str, str]]] = {
    "system": [
        _tile("Request rate",
              f"sum(rate(assetatlas_http_request_total[{_RATE}]))", _OPS_S),
        _tile("Error rate (4xx vs 5xx)",
              f'sum(rate(assetatlas_http_request_total{{status=~"4.."}}[{_RATE}])) '
              f'or sum(rate(assetatlas_http_request_total{{status=~"5.."}}[{_RATE}]))',
              _OPS_S),
        _tile("Latency p50/p95/p99",
              f"histogram_quantile(0.50, sum(rate(assetatlas_http_request_duration_seconds_bucket[{_RATE}])) by (le)) "
              f"or histogram_quantile(0.95, sum(rate(assetatlas_http_request_duration_seconds_bucket[{_RATE}])) by (le)) "
              f"or histogram_quantile(0.99, sum(rate(assetatlas_http_request_duration_seconds_bucket[{_RATE}])) by (le))",
              _SECONDS),
        _tile("Top endpoints by p95 latency",
              "topk(10, histogram_quantile(0.95, sum(rate(assetatlas_http_request_duration_seconds_bucket[5m])) by (le, endpoint)))",
              _SECONDS),
        _tile("Top endpoints by 5xx count",
              'topk(10, sum(rate(assetatlas_http_request_total{status=~"5.."}[5m])) by (endpoint))',
              _OPS_S),
        _tile("Concurrent in-flight",
              "sum(assetatlas_http_request_in_flight) OR on() vector(0)", _COUNT),
    ],
    "database": [
        _tile("Query latency p50/p95/p99",
              f"histogram_quantile(0.50, sum(rate(assetatlas_db_query_duration_seconds_bucket[{_RATE}])) by (le)) "
              f"or histogram_quantile(0.95, sum(rate(assetatlas_db_query_duration_seconds_bucket[{_RATE}])) by (le)) "
              f"or histogram_quantile(0.99, sum(rate(assetatlas_db_query_duration_seconds_bucket[{_RATE}])) by (le))",
              _SECONDS),
        _tile("Connection pool used %",
              "(assetatlas_db_connection_pool_size - assetatlas_db_connection_pool_available) / assetatlas_db_connection_pool_size",
              _RATIO),
        _tile("Active connections", "pg_stat_activity_count", _COUNT),
        _tile("Slow queries (>500ms)",
              f'rate(assetatlas_db_query_duration_seconds_bucket{{le="+Inf"}}[{_RATE}]) '
              f'- rate(assetatlas_db_query_duration_seconds_bucket{{le="0.5"}}[{_RATE}])',
              _OPS_S),
        _tile("Cache hit ratio",
              "pg_stat_database_blks_hit / (pg_stat_database_blks_hit + pg_stat_database_blks_read)",
              _RATIO),
        _tile("Database size",
              'pg_database_size_bytes{datname="assetatlas"}', _BYTES),
    ],
    "redis": [
        _tile("Hit rate",
              f"rate(redis_keyspace_hits_total[{_RATE}]) / "
              f"(rate(redis_keyspace_hits_total[{_RATE}]) + rate(redis_keyspace_misses_total[{_RATE}]))",
              _RATIO),
        _tile("Memory used", "redis_memory_used_bytes", _BYTES),
        _tile("Connected clients", "redis_connected_clients", _COUNT),
        _tile("Evictions per second",
              f"rate(redis_evicted_keys_total[{_RATE}])", _OPS_S),
        _tile("Commands per second",
              f"rate(redis_commands_processed_total[{_RATE}])", _OPS_S),
        _tile("Memory utilization",
              "redis_memory_used_bytes / redis_memory_max_bytes", _RATIO),
    ],
    "host": [
        _tile("CPU usage %",
              f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{_RATE}])) by (instance))',
              _PERCENT),
        _tile("Memory used %",
              "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
              _PERCENT),
        _tile("Disk usage % per mountpoint",
              '(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"}) * 100',
              _PERCENT),
        _tile("Network I/O",
              f"rate(node_network_receive_bytes_total[{_RATE}]) or rate(node_network_transmit_bytes_total[{_RATE}])",
              _BYTES_S),
        _tile("Disk I/O",
              f"rate(node_disk_read_bytes_total[{_RATE}]) or rate(node_disk_written_bytes_total[{_RATE}])",
              _BYTES_S),
        _tile("Load average",
              "node_load1 or node_load5 or node_load15", _LOAD),
    ],
    "containers": [
        _tile("CPU per container",
              f'sum by (name) (rate(container_cpu_usage_seconds_total{{name!=""}}[{_RATE}]))',
              _CORES),
        _tile("Memory per container",
              'sum by (name) (container_memory_usage_bytes{name!=""})', _BYTES),
        _tile("Network I/O per container",
              f'sum by (name) (rate(container_network_receive_bytes_total{{name!=""}}[{_RATE}]))',
              _BYTES_S),
        _tile("Container count",
              'count(container_last_seen{name!=""})', _COUNT),
        _tile("Container uptime",
              'time() - container_start_time_seconds{name!=""}', _SECONDS),
        _tile("File descriptors per container",
              'container_file_descriptors{name!=""}', _COUNT),
    ],
    "pipeline": [
        _tile("Run duration p95",
              f"histogram_quantile(0.95, sum(rate(assetatlas_asset_generation_duration_seconds_bucket[{_RATE}])) by (le))",
              _SECONDS),
        _tile("Records processed per run",
              f"rate(assetatlas_assets_created_total[{_RATE}]) + rate(assetatlas_assets_updated_total[{_RATE}])",
              _RECORDS_S),
        _tile("Stage timing breakdown",
              f"sum by (stage) (rate(assetatlas_pipeline_stage_duration_seconds_sum[{_RATE}]))",
              _SECONDS),
        _tile("Run success rate (1h)",
              'sum(rate(assetatlas_asset_generation_runs_total{status="success"}[1h])) / sum(rate(assetatlas_asset_generation_runs_total[1h]))',
              _RATIO),
        # Job-scoped: the api exports this gauge too (it imports the definition
        # from src/api/utils/metrics.py) but never sets it, so an unscoped query
        # returns a second series pinned at 0 whose "age" reads as ~56 years.
        _tile("Scheduler heartbeat age",
              'time() - assetatlas_scheduler_last_heartbeat_seconds{job="assetatlas-scheduler"}',
              _SECONDS),
    ],
}

_GROUPS: Tuple[str, ...] = tuple(_TILES.keys())

# Preset → (span seconds, step, rate window). Mirrors timeRange.ts so a chat
# user gets the SAME resolution the dashboard would for that range.
_PRESETS: Dict[str, Dict[str, Any]] = {
    "1h":  {"span": 3600,   "step": "15s", "rate": "1m"},
    "6h":  {"span": 21600,  "step": "1m",  "rate": "5m"},
    "24h": {"span": 86400,  "step": "5m",  "rate": "15m"},
    "7d":  {"span": 604800, "step": "30m", "rate": "1h"},
}

_DURATION_RE = re.compile(r"^\d+(ms|s|m|h|d|w|y)$")

# Bound the in-context text; the full per-series rows ride the table artifact.
_MAX_TEXT_SERIES_PER_TILE = 12
_MAX_ARTIFACT_ROWS = 100


def _validate_duration(name: str, value: Optional[str]) -> Optional[str]:
    """Loud error if a caller-supplied step/rate_window isn't a PromQL duration.

    A malformed value would make Prometheus 400 on EVERY tile, which would read
    as "the whole backend is down" rather than "your step is wrong" — so reject
    it up front with a specific message (§4 loud-over-silent).
    """
    if value is None:
        return None
    if not _DURATION_RE.match(str(value)):
        return (
            f"{name}={value!r} is not a valid Prometheus duration "
            "(e.g. '30s', '5m', '1h')."
        )
    return None


def _parse_ts(value: str) -> int:
    """Parse a custom start/end into Unix seconds (int).

    Accepts a Unix timestamp (int/float as string) or an ISO-8601 datetime.
    A NAIVE datetime (no offset) is interpreted as UTC — Prometheus wants epoch
    seconds, and ``.timestamp()`` on a naive datetime would otherwise apply the
    server's LOCAL offset and silently shift the query window by hours.
    Raises ValueError on anything else so the caller can surface a clean error.
    """
    s = str(value).strip()
    try:
        return int(float(s))
    except (TypeError, ValueError):
        pass
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _resolve_window(
    time_range: str,
    start: Optional[str],
    end: Optional[str],
    step: Optional[str],
    rate_window: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve the query window to concrete start/end/step/rate_window.

    Custom mode (both start and end) overrides the preset; otherwise the preset
    drives the look-back and the default step/rate window. Returns
    ``(window_dict, None)`` or ``(None, error_string)``.
    """
    dur_err = _validate_duration("step", step) or _validate_duration(
        "rate_window", rate_window)
    if dur_err:
        return None, dur_err

    if start is not None or end is not None:
        if start is None or end is None:
            return None, (
                "A custom range needs BOTH start and end; otherwise omit them "
                "and use time_range."
            )
        try:
            start_s = _parse_ts(start)
            end_s = _parse_ts(end)
        except (ValueError, TypeError):
            return None, (
                "start/end must be Unix timestamps or ISO-8601 datetimes "
                f"(got start={start!r}, end={end!r})."
            )
        if end_s <= start_s:
            return None, "end must be after start."
        span = end_s - start_s
        step_s = max(15, min(span // 240, 1800))
        eff_step = step or f"{step_s}s"
        eff_rate = rate_window or f"{min(max(60, 4 * step_s), 3600)}s"
        label = f"custom {start_s}..{end_s}"
    else:
        if time_range not in _PRESETS:
            return None, unknown_enum("time_range", time_range, list(_PRESETS))
        preset = _PRESETS[time_range]
        end_s = int(time.time())
        start_s = end_s - preset["span"]
        eff_step = step or preset["step"]
        eff_rate = rate_window or preset["rate"]
        label = f"last {time_range}"

    return {
        "start": str(start_s), "end": str(end_s),
        "step": eff_step, "rate_window": eff_rate, "label": label,
    }, None


def _humanize_bytes(v: float) -> str:
    neg = v < 0
    v = abs(v)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if v < 1024 or unit == "PB":
            out = f"{v:.2f} {unit}" if unit != "B" else f"{v:.0f} B"
            return ("-" if neg else "") + out
        v /= 1024
    return f"{v}"


def _fmt(v: Optional[float], unit: str) -> str:
    """Format a numeric metric value for its unit."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v != v:  # NaN
        return "NaN"
    if unit == _BYTES:
        return _humanize_bytes(v)
    if unit == _BYTES_S:
        return _humanize_bytes(v) + "/s"
    if unit == _PERCENT:
        return f"{v:.1f}%"
    if unit == _RATIO:
        return f"{v * 100:.1f}%"
    if unit == _SECONDS:
        if v < 1:
            return f"{v * 1000:.0f}ms"
        if v < 600:
            return f"{v:.2f}s"
        return f"{v / 60:.1f}min"
    if unit in (_OPS_S, _RECORDS_S):
        return f"{v:.2f}/s"
    if unit == _CORES:
        return f"{v:.3f}"
    if unit == _LOAD:
        return f"{v:.2f}"
    if unit == _COUNT:
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return f"{v:.4g}"


_LABEL_PRIORITY = ("name", "endpoint", "mountpoint", "stage", "device",
                   "instance", "status", "quantile", "le")


def _series_label(metric: Dict[str, Any], index: int) -> str:
    """A readable label for one Prometheus series.

    Builds from the most informative labels; falls back to the metric name, then
    to a positional label (some tiles ``or``/``sum`` series down to no
    distinguishing label — e.g. the p50/p95/p99 latency stack — and an index
    keeps them addressable).
    """
    parts = [f"{k}={metric[k]}" for k in _LABEL_PRIORITY if metric.get(k)]
    if parts:
        return ", ".join(parts)
    name = metric.get("__name__")
    if name:
        return str(name)
    return f"series {index + 1}"


def _summarize_series(values: List[List[Any]]) -> Optional[Dict[str, float]]:
    """Reduce a matrix series' [ts, "val"] points to latest/min/max/avg."""
    nums: List[float] = []
    for point in values or []:
        try:
            v = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Keep only FINITE points. Prometheus emits "NaN" for divide-by-zero
        # ratios and "+Inf" for histogram_quantile over empty/sparse buckets;
        # either would poison the summary (avg of any inf is inf, and "inf%"
        # is a meaningless value to show the admin).
        if math.isfinite(v):
            nums.append(v)
    if not nums:
        return None
    return {
        "latest": nums[-1],
        "min": min(nums),
        "max": max(nums),
        "avg": sum(nums) / len(nums),
    }


def query_metrics_impl(
    monitoring_service,
    *,
    group: str,
    time_range: str = "1h",
    start: Optional[str] = None,
    end: Optional[str] = None,
    step: Optional[str] = None,
    rate_window: Optional[str] = None,
) -> ToolReturn:
    """Run a monitoring dashboard group's PromQL tiles over a time window.

    Returns ``(text, artifact)``: a per-tile, per-series summary
    (latest/min/max/avg, unit-formatted) plus a ``table`` artifact with one row
    per series.
    """
    if monitoring_service is None:
        return (
            "Monitoring/observability is not available on this server "
            "(MONITORING_ENABLED is off, so Prometheus isn't wired).",
            None,
        )
    if group not in _TILES:
        return unknown_enum("group", group, list(_GROUPS)), None

    window, err = _resolve_window(time_range, start, end, step, rate_window)
    if err:
        return err, None

    tiles = _TILES[group]
    lines: List[str] = [
        f"# Metrics: {group} ({window['label']}, step {window['step']}, "
        f"rate window {window['rate_window']})",
    ]
    rows: List[Dict[str, Any]] = []
    failures = 0

    for tile in tiles:
        title, unit = tile["title"], tile["unit"]
        query = tile["query"].replace(_RATE, window["rate_window"])
        lines.append("")
        lines.append(f"## {title}")
        try:
            result = monitoring_service.query_range(
                query, start=window["start"], end=window["end"],
                step=window["step"],
            ) or {}
        except Exception as e:  # noqa: BLE001 — surface a clean per-tile note
            failures += 1
            # Class name only: the proxy's messages can include backend detail
            # we don't want verbatim in a shared transcript (§4 no-raw-leak).
            lines.append(f"- query failed ({type(e).__name__})")
            continue

        series = result.get("result") or []
        if not series:
            lines.append("- (no data in this window)")
            continue

        shown = 0
        for i, item in enumerate(series):
            summary = _summarize_series(item.get("values"))
            if summary is None:
                continue
            label = _series_label(item.get("metric") or {}, i)
            if shown < _MAX_TEXT_SERIES_PER_TILE:
                lines.append(
                    f"- {label}: latest {_fmt(summary['latest'], unit)}, "
                    f"min {_fmt(summary['min'], unit)}, "
                    f"max {_fmt(summary['max'], unit)}, "
                    f"avg {_fmt(summary['avg'], unit)}"
                )
            rows.append({
                "metric": title,
                "series": label,
                "latest": _fmt(summary["latest"], unit),
                "min": _fmt(summary["min"], unit),
                "max": _fmt(summary["max"], unit),
                "avg": _fmt(summary["avg"], unit),
            })
            shown += 1
        if shown == 0:
            lines.append("- (no data in this window)")
        elif shown > _MAX_TEXT_SERIES_PER_TILE:
            lines.append(
                f"- (+{shown - _MAX_TEXT_SERIES_PER_TILE} more series in the "
                "table)"
            )

    # Every tile errored. Two distinct causes: the Prometheus backend is
    # unavailable (connection/timeout), OR the queries no longer match it
    # (metric/label drift from the dashboards → HTTP 400). Don't assert one —
    # name both and point at the per-tile errors (which carry the error type).
    if failures == len(tiles):
        return (
            f"All {group} metric queries failed — the metrics backend "
            "(Prometheus) is unavailable, or the queries no longer match it "
            "(metric/label drift). See the per-tile errors above.",
            None,
        )

    artifact = _build_artifact(group, window, rows)
    return "\n".join(lines), artifact


def _build_artifact(
    group: str, window: Dict[str, Any], rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    truncated = len(rows) > _MAX_ARTIFACT_ROWS
    return {
        "type": "table",
        "title": f"{group} metrics — {window['label']}",
        "columns": [
            {"key": "metric", "label": "Metric"},
            {"key": "series", "label": "Series"},
            {"key": "latest", "label": "Latest"},
            {"key": "min", "label": "Min"},
            {"key": "max", "label": "Max"},
            {"key": "avg", "label": "Avg"},
        ],
        "series": rows[:_MAX_ARTIFACT_ROWS],
        "meta": {
            "source_tool": "query_metrics",
            "layout": "list",
            "group": group,
            "window": window["label"],
            "step": window["step"],
            "rate_window": window["rate_window"],
            "truncated": truncated,
            "note": ("time-series summarized as a table; line/gauge artifact "
                     "types are not yet in the chat artifact union"),
        },
    }
