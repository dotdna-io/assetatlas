# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the query_metrics chat tool (plan §2.F).

``query_metrics`` proxies the monitoring dashboards' PromQL tiles through the
existing ``monitoring_service.query_range`` and summarizes each series. These
tests pin the behaviours the parity push requires (§2.F, §4, §6.1):

- the per-series reduction is correct and drops Prometheus NaNs (a
  divide-by-zero ratio emits "NaN"; treating it as a number would mislead);
- units render human values (a ratio as %, bytes as GB) — the PromQL carries no
  unit, so we must;
- the rate window matches the selected range's resolution (24h → 15m), or the
  rendered rates wouldn't match the dashboard;
- bad input (unknown group, half a custom range, a malformed step) is a LOUD
  error, never a silent empty/clamp;
- a dead backend is reported loudly, not as an empty table;
- the artifact is a valid ``table`` envelope (the only chart kinds the registry
  keeps are donut/stacked_bar/table).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.api.blueprints.chat.artifacts import validate_envelope
from src.mcp.tools.metrics_tool import (
    _BYTES,
    _COUNT,
    _RATIO,
    _SECONDS,
    _TILES,
    _fmt,
    _resolve_window,
    _series_label,
    _summarize_series,
    query_metrics_impl,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_summarize_series_computes_latest_min_max_avg():
    out = _summarize_series([[1, "10"], [2, "30"], [3, "20"]])
    assert out == {"latest": 20.0, "min": 10.0, "max": 30.0, "avg": 20.0}


def test_summarize_series_drops_nan_points():
    """A divide-by-zero ratio emits 'NaN'. If the LAST point is NaN, a naive
    'latest' would report NaN; real points must win so the summary is truthful."""
    out = _summarize_series([[1, "0.5"], [2, "NaN"]])
    assert out == {"latest": 0.5, "min": 0.5, "max": 0.5, "avg": 0.5}


def test_summarize_series_drops_infinite_points():
    """histogram_quantile over empty/sparse buckets emits +Inf; like NaN it must
    be dropped, or avg/min/max become inf and render as a meaningless 'inf'."""
    out = _summarize_series([[1, "0.5"], [2, "+Inf"], [3, "0.7"]])
    assert out == {"latest": 0.7, "min": 0.5, "max": 0.7, "avg": 0.6}


def test_summarize_series_all_unparseable_is_none():
    assert _summarize_series([]) is None
    assert _summarize_series([[1, "NaN"]]) is None
    assert _summarize_series([[1, "+Inf"]]) is None


def test_fmt_renders_units_human():
    assert _fmt(0.5, _RATIO) == "50.0%"          # 0..1 ratio as %
    assert _fmt(2 * 1024 ** 3, _BYTES) == "2.00 GB"
    assert _fmt(0.25, _SECONDS) == "250ms"        # sub-second → ms
    assert _fmt(5, _SECONDS) == "5.00s"
    assert _fmt(1234, _COUNT) == "1,234"
    assert _fmt(None, _RATIO) == "—"


def test_series_label_prefers_informative_labels_then_falls_back():
    assert _series_label({"name": "assetatlas-api"}, 0) == "name=assetatlas-api"
    # ``sum``/``or`` tiles strip distinguishing labels (e.g. p50/p95/p99) —
    # an index keeps the three series addressable instead of all reading blank.
    assert _series_label({}, 2) == "series 3"
    assert _series_label({"__name__": "node_load1"}, 0) == "node_load1"


# ---------------------------------------------------------------------------
# query_metrics_impl
# ---------------------------------------------------------------------------

def _matrix(*series):
    return {"resultType": "matrix", "result": list(series)}


def _svc(matrix=None):
    svc = MagicMock()
    svc.query_range.return_value = (
        matrix if matrix is not None
        else _matrix({"metric": {}, "values": [[1, "10"], [2, "20"], [3, "30"]]})
    )
    return svc


def test_happy_path_summarizes_and_builds_valid_table():
    svc = _svc()

    text, artifact = query_metrics_impl(svc, group="database", time_range="1h")

    assert "Metrics: database" in text
    assert "Active connections" in text          # a tile title
    assert "latest 30" in text                   # count summary surfaced
    # one query_range call per tile in the group
    assert svc.query_range.call_count == len(_TILES["database"])
    assert validate_envelope(artifact, tool_name="query_metrics") is artifact
    assert artifact["type"] == "table"
    assert {c["key"] for c in artifact["columns"]} == {
        "metric", "series", "latest", "min", "max", "avg"}
    assert len(artifact["series"]) >= 1


def test_unknown_group_is_loud_error_and_runs_nothing():
    svc = _svc()

    text, artifact = query_metrics_impl(svc, group="bogus")

    assert "Valid values" in text
    assert artifact is None
    svc.query_range.assert_not_called()


def test_monitoring_unavailable_is_graceful():
    text, artifact = query_metrics_impl(None, group="system")

    assert "not available" in text.lower()
    assert "MONITORING_ENABLED" in text
    assert artifact is None


def test_preset_sets_resolution_to_match_dashboard():
    """24h must resolve to step 5m + rate window 15m (the dashboard's mapping);
    a mismatched window would render rates the monitoring UI never shows."""
    svc = _svc()

    query_metrics_impl(svc, group="redis", time_range="24h")

    calls = svc.query_range.call_args_list
    # step is passed identically to every tile
    assert all(c.kwargs["step"] == "5m" for c in calls)
    # the rate window is substituted into the [__RATE__] placeholder
    rate_qs = [c.args[0] for c in calls if "[15m]" in c.args[0]]
    assert rate_qs, "expected the 15m rate window substituted into a tile query"
    assert not any("__RATE__" in c.args[0] for c in calls)


def test_custom_range_requires_both_bounds():
    svc = _svc()

    text, artifact = query_metrics_impl(
        svc, group="system", start="1000", end=None)

    assert "both start and end" in text.lower()
    assert artifact is None
    svc.query_range.assert_not_called()


def test_custom_range_naive_iso_parsed_as_utc():
    """A naive ISO datetime must be read as UTC, not the server's local TZ —
    otherwise the Prometheus window silently shifts by the local offset."""
    from datetime import datetime, timezone
    win, err = _resolve_window(
        "1h", start="2026-01-01T00:00:00", end="2026-01-02T00:00:00",
        step=None, rate_window=None)
    assert err is None
    expected = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    assert win["start"] == str(expected)


def test_custom_range_computes_step_and_is_used():
    svc = _svc()

    query_metrics_impl(svc, group="system", start="1000", end="1000000")

    # span 999000s / 240 = 4162 → clamped to the 1800s ceiling
    assert all(c.kwargs["step"] == "1800s" for c in svc.query_range.call_args_list)
    assert all(c.kwargs["start"] == "1000" and c.kwargs["end"] == "1000000"
               for c in svc.query_range.call_args_list)


def test_bad_step_duration_is_loud_error():
    svc = _svc()

    text, artifact = query_metrics_impl(
        svc, group="system", step="5minutes")

    assert "valid Prometheus duration" in text
    assert artifact is None
    svc.query_range.assert_not_called()


def test_all_tiles_failing_reports_backend_down():
    svc = MagicMock()
    svc.query_range.side_effect = RuntimeError("connection refused")

    text, artifact = query_metrics_impl(svc, group="redis")

    assert "unavailable" in text.lower()
    assert artifact is None


def test_partial_failure_keeps_good_tiles():
    """One failing tile must not blank the rest — and the failure is surfaced
    (a missing tile that reads as 'no data' would hide a real problem)."""
    svc = MagicMock()
    good = _matrix({"metric": {}, "values": [[1, "5"], [2, "7"]]})
    svc.query_range.side_effect = [RuntimeError("boom")] + [good] * 10

    text, artifact = query_metrics_impl(svc, group="redis")

    assert "query failed" in text.lower()   # the broken tile is flagged
    assert artifact is not None             # the good tiles still rendered
    assert len(artifact["series"]) >= 1


def test_container_series_labelled_by_name():
    svc = _svc(_matrix(
        {"metric": {"name": "assetatlas-api"}, "values": [[1, "1000000"]]},
        {"metric": {"name": "assetatlas-db"}, "values": [[1, "2000000"]]},
    ))

    text, _artifact = query_metrics_impl(svc, group="containers")

    assert "name=assetatlas-api" in text
    assert "name=assetatlas-db" in text
