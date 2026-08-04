# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sectioned dashboard chat tools (plan §2.C).

Three tools that make the chatbot reach read-parity with the product homepage:

- ``get_dashboard`` — one sectioned tool over ``DashboardService`` (the SAME
  service the homepage UI calls), so every number a chat user sees matches the
  UI exactly. Sections mirror the homepage panels: summary / posture_tiers /
  confidence_breakdown / data_source_health / reliability / data_quality.
- ``get_dashboard_trend`` — the time-series kinds (posture / coverage / asset
  timeline) behind one ``kind`` enum.
- ``get_relationship_graph`` — the cross-entity link data (v1 flower / v2
  any-to-any) with presentation-only colour/icon/id-hash stripped.

These are THIN wrappers: no new SQL, no re-derived metrics. The only place this
module reaches past ``DashboardService`` is ``data_quality`` per-field coverage,
which counts non-empty values through the canonical asset query path
(``list_assets_query`` + the ``notempty`` operator) so coverage matches a user's
own Explorer filter rather than a bespoke re-implementation (plan §3.1).

Artifacts use only the chart vocabulary the chat registry validates
(donut / stacked_bar / table — ``artifacts.validate_envelope`` drops unknown
types); time-series and graph data ride in a ``table`` artifact plus a structured
``meta`` payload (see ``get_dashboard_trend``/``get_relationship_graph``).
Aggregate/breakdown buckets carry a ``drilldown`` (§3.6) ONLY where it faithfully
reproduces the bucket's population — never a count we can't substantiate.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

from src.mcp.tools._common import overflow_note
from src.mcp.tools.assets import FieldResolutionError, list_assets_query
from src.modules.fields.operators import OperatorResolutionError

from src.api.blueprints.chat.artifacts import (
    build_drilldown,
    build_stacked_bar_from_aggregate,
)

ToolReturn = Tuple[str, Optional[Dict[str, Any]]]

_ARTIFACT_SOURCE = "get_dashboard"
_TREND_SOURCE = "get_dashboard_trend"
_GRAPH_SOURCE = "get_relationship_graph"

_SECTIONS = (
    "summary", "posture_tiers", "confidence_breakdown",
    "data_source_health", "reliability", "data_quality",
)

_POSTURE_TIERS = ("secured", "monitored", "discovered")

# confirmed/under_covered/shadows split the asset set by the SAME thresholds as
# the confidence_level virtual field (confirmed≥high, under_covered≥medium,
# shadows<medium ⇔ high/medium/low), so a band drill-down re-runs to exactly the
# population the band counted.
_CONFIDENCE_BANDS = (
    ("confirmed", "high"),
    ("under_covered", "medium"),
    ("shadows", "low"),
)

# Default fields whose completeness data_quality reports when the caller doesn't
# pick any — the core identity/attribution columns the old get_data_quality
# surfaced, now counted through the canonical engine so ANY field works too.
_DEFAULT_QUALITY_FIELDS = (
    "Hostname", "IP Address", "MAC Address", "Serial Number",
    "OS Normalized", "Manufacturer", "Owner", "Category",
)
# Bound the per-field count fan-out (one COUNT query each) so a huge fields list
# can't blow the per-tool timeout; overflow is reported, never silently dropped.
_MAX_QUALITY_FIELDS = 25
# data_quality category ranking: show the worst N unless full=True (the plan's
# removed [:10] cap).
_RANKING_CAP = 10

_UNAVAILABLE = "The dashboard service is not available."


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v if v is not None else 0)


# ===========================================================================
# get_dashboard (sectioned)
# ===========================================================================

def get_dashboard_impl(
    dashboard_service,
    db,
    resolver,
    *,
    section: str = "summary",
    view: str = "individual",
    fields: Any = None,
    full: bool = False,
    drilldown: bool = False,
    category: Optional[str] = None,
    medium: float = 0.45,
    high: float = 0.70,
) -> ToolReturn:
    """Return one homepage section's data as ``(text, artifact)``.

    ``dashboard_service`` provides every metric (UI parity). ``db``/``resolver``
    are used ONLY by the data_quality per-field coverage count. ``drilldown``
    attaches a re-runnable §3.6 filter to breakdown buckets where faithful.
    """
    if dashboard_service is None:
        return _UNAVAILABLE, None

    if section == "summary":
        return _section_summary(dashboard_service)
    if section == "posture_tiers":
        return _section_posture_tiers(dashboard_service)
    if section == "confidence_breakdown":
        return _section_confidence_breakdown(
            dashboard_service, drilldown=drilldown, medium=medium, high=high,
        )
    if section == "data_source_health":
        return _section_data_source_health(
            dashboard_service, view=view, drilldown=drilldown,
        )
    if section == "reliability":
        return _section_reliability(dashboard_service, full=full)
    if section == "data_quality":
        return _section_data_quality(
            dashboard_service, db, resolver, fields=fields, full=full,
            drilldown=drilldown, category=category,
        )
    return (
        f"Unknown dashboard section {section!r}. Valid sections: "
        f"{', '.join(_SECTIONS)}.",
        None,
    )


def _section_summary(svc) -> ToolReturn:
    s = svc.get_summary()
    total = s.get("totalAssets", 0)
    added = (s.get("changeToday") or {}).get("added", 0)
    rating = (s.get("dataQuality") or {}).get("rating", "—")
    vis = s.get("visibility") or {}
    completeness = s.get("completeness", 0)
    last_updated = s.get("lastUpdated")

    lines = [
        "# Dashboard summary",
        f"- Total assets: {_fmt_int(total)}",
        f"- Added today: {_fmt_int(added)}",
        f"- Data quality: {rating}",
        f"- Visibility: {vis.get('level', '—')} (score {vis.get('score', 0)})",
        f"- Completeness: {completeness}%",
        f"- Last updated: {last_updated or 'never'}",
    ]
    cats = s.get("categoryBreakdown") or []
    if cats:
        lines.append("Categories:")
        for c in cats:
            lines.append(
                f"  - {c.get('category', '—')}: {_fmt_int(c.get('count', 0))}"
            )

    series = [
        {"metric": "Total assets", "value": total},
        {"metric": "Added today", "value": added},
        {"metric": "Data quality", "value": rating},
        {"metric": "Visibility", "value": f"{vis.get('level', '—')} "
                                          f"({vis.get('score', 0)})"},
        {"metric": "Completeness %", "value": completeness},
    ]
    artifact = {
        "type": "table",
        "title": "Dashboard summary",
        "columns": [
            {"key": "metric", "label": "Metric"},
            {"key": "value", "label": "Value"},
        ],
        "series": series,
        "meta": {
            "source_tool": _ARTIFACT_SOURCE,
            "section": "summary",
            "total": total,
        },
    }
    return "\n".join(lines), artifact


def _section_posture_tiers(svc) -> ToolReturn:
    pd = svc.get_posture_data()
    posture: Dict[str, Dict[str, int]] = pd.get("posture") or {}
    changes: Dict[str, Dict[str, Any]] = pd.get("changes") or {}
    totals = {
        t: sum(int((c or {}).get(t, 0)) for c in posture.values())
        for t in _POSTURE_TIERS
    }

    lines = ["# Posture tiers (per asset category)"]
    for cat, tiers in posture.items():
        lines.append(
            f"- {cat}: secured {_fmt_int(tiers.get('secured', 0))}, "
            f"monitored {_fmt_int(tiers.get('monitored', 0))}, "
            f"discovered {_fmt_int(tiers.get('discovered', 0))}"
        )
    lines.append("Totals (week-over-week change):")
    for t in _POSTURE_TIERS:
        delta = (changes.get(t) or {}).get("delta")
        # None = no 7-day-old snapshot yet; printing +0 would falsely claim
        # 'no change'.
        delta_txt = "no 7-day baseline" if delta is None else f"{int(delta):+d}"
        lines.append(f"- {t}: {_fmt_int(totals[t])} ({delta_txt})")

    grand = sum(totals.values())
    result = {
        "rows": list(posture.keys()),
        "columns": list(_POSTURE_TIERS),
        "data": {
            cat: {t: int((tiers or {}).get(t, 0)) for t in _POSTURE_TIERS}
            for cat, tiers in posture.items()
        },
        "grand_total": grand,
    }
    # Tiers PARTITION each category's assets, so a stacked bar is honest. No
    # per-cell drill-down: a faithful posture-tier filter needs the posture
    # field grammar that Phase 2 owns; a fabricated 'Posture Tier' filter would
    # loud-error on re-run (worse than none).
    artifact = build_stacked_bar_from_aggregate(
        result, "Category", "Posture Tier", with_drilldown=False,
    )
    if artifact is not None:
        artifact["meta"]["source_tool"] = _ARTIFACT_SOURCE
        artifact["meta"]["section"] = "posture_tiers"
        artifact["meta"]["changes"] = changes
    return "\n".join(lines), artifact


def _section_confidence_breakdown(svc, *, drilldown, medium, high) -> ToolReturn:
    cb = svc.get_confidence_breakdown()
    breakdown: Dict[str, Dict[str, int]] = cb.get("breakdown") or {}

    lines = [
        "# Confidence breakdown (per asset category)",
        f"(confirmed ≥ {high:.0%}, under_covered ≥ {medium:.0%}, "
        f"shadows < {medium:.0%})",
    ]
    series: List[Dict[str, Any]] = []
    for cat, bands in breakdown.items():
        lines.append(
            f"- {cat}: confirmed {_fmt_int(bands.get('confirmed', 0))}, "
            f"under_covered {_fmt_int(bands.get('under_covered', 0))}, "
            f"shadows {_fmt_int(bands.get('shadows', 0))}"
        )
        stacks: List[Dict[str, Any]] = []
        for band, level in _CONFIDENCE_BANDS:
            val = int((bands or {}).get(band, 0))
            stack: Dict[str, Any] = {"label": band, "value": val}
            if drilldown and val:
                stack["drilldown"] = build_drilldown([
                    {"field": "Category", "op": "eq", "value": cat},
                    {"field": "confidence_level", "op": "eq", "value": level},
                ])
            stacks.append(stack)
        series.append({"x_label": cat, "stacks": stacks})

    artifact = {
        "type": "stacked_bar",
        "title": "Assets by Category × Confidence band",
        "series": series,
        "meta": {
            "source_tool": _ARTIFACT_SOURCE,
            "section": "confidence_breakdown",
        },
    } if series else None
    return "\n".join(lines), artifact


def _section_data_source_health(svc, *, view, drilldown) -> ToolReturn:
    view = view if view in ("individual", "grouped") else "individual"
    ds = svc.get_data_sources(view=view)
    sources = ds.get("sources") or []

    lines = [f"# Data source health ({view}, {len(sources)} sources)"]
    series: List[Dict[str, Any]] = []
    # Text rows are capped (the artifact carries every source) so a fleet with
    # many integrations can't bloat the context.
    for idx, s in enumerate(sources):
        name = s.get("name", "—")
        if idx < _RANKING_CAP:
            lines.append(
                f"- {name}: {_fmt_int(s.get('assetCount', 0))} assets, "
                f"status {s.get('status', '—')}, "
                f"last sync {s.get('lastSyncDisplay', '—')}, "
                f"confidence {s.get('confidence', 0)}%"
            )
        row: Dict[str, Any] = {
            "source": name,
            "assets": s.get("assetCount", 0),
            "status": s.get("status", "—"),
            "last_sync": s.get("lastSyncDisplay", "—"),
            "confidence": s.get("confidence", 0),
        }
        # Data Source eq <name> reproduces the source's assets exactly (the
        # static field carries the hidden group-name expansion in the repo).
        if drilldown and name:
            row["drilldown"] = build_drilldown(
                [{"field": "Data Source", "op": "eq", "value": name}]
            )
        series.append(row)

    note = overflow_note(
        len(sources), min(len(sources), _RANKING_CAP),
        noun="sources", hint="see the full table artifact")
    if note:
        lines.append(note)

    artifact = {
        "type": "table",
        "title": "Data source health",
        "columns": [
            {"key": "source", "label": "Source"},
            {"key": "assets", "label": "Assets"},
            {"key": "status", "label": "Status"},
            {"key": "last_sync", "label": "Last Sync"},
            {"key": "confidence", "label": "Confidence %"},
        ],
        "series": series,
        "meta": {
            "source_tool": _ARTIFACT_SOURCE,
            "section": "data_source_health",
            "view": view,
            "count": ds.get("count", len(sources)),
        },
    } if series else None
    return "\n".join(lines), artifact


def _section_reliability(svc, *, full) -> ToolReturn:
    if not full:
        rel = svc.get_reliability()
        most_src = rel.get("mostReliableSource") or {}
        least_src = rel.get("leastReliableSource") or {}
        most_cat = rel.get("mostReliableCategory") or {}
        least_cat = rel.get("leastReliableCategory") or {}
        # Source scores are raw 0..1 → scale to %; category scores already %.
        ms = float(most_src.get("score", 0)) * 100
        ls = float(least_src.get("score", 0)) * 100
        mc = float(most_cat.get("score", 0))
        lc = float(least_cat.get("score", 0))
        lines = [
            "# Data reliability",
            "## Data sources",
            f"- Most reliable: {most_src.get('name', '—')} — {ms:.1f}%",
            f"- Least reliable: {least_src.get('name', '—')} — {ls:.1f}%",
            "## Asset categories",
            f"- Most reliable: {most_cat.get('name', '—')} — {mc:.1f}%",
            f"- Least reliable: {least_cat.get('name', '—')} — {lc:.1f}%",
        ]
        series = [
            {"metric": "Most reliable source",
             "name": most_src.get("name", "—"), "score": round(ms, 1)},
            {"metric": "Least reliable source",
             "name": least_src.get("name", "—"), "score": round(ls, 1)},
            {"metric": "Most reliable category",
             "name": most_cat.get("name", "—"), "score": round(mc, 1)},
            {"metric": "Least reliable category",
             "name": least_cat.get("name", "—"), "score": round(lc, 1)},
        ]
        artifact = {
            "type": "table",
            "title": "Data reliability",
            "columns": [
                {"key": "metric", "label": "Metric"},
                {"key": "name", "label": "Name"},
                {"key": "score", "label": "Score %"},
            ],
            "series": series,
            "meta": {"source_tool": _ARTIFACT_SOURCE, "section": "reliability"},
        }
        return "\n".join(lines), artifact

    # full=True: complete ranking (sources by sync-grouped confidence, all
    # categories by avg confidence), with sample sizes — all from the SAME
    # service methods the UI uses, so the numbers match.
    sources = (svc.get_data_sources(view="grouped") or {}).get("sources") or []
    gauges = (svc.get_inventory_summary() or {}).get("categoryGauges") or []
    src_ranked = sorted(
        sources, key=lambda s: s.get("confidence", 0) or 0, reverse=True,
    )
    cat_ranked = sorted(
        gauges, key=lambda g: g.get("avgConfidence", 0) or 0, reverse=True,
    )
    # NOTE: this is the `full=True` opt-in ranking — the plan (§2.C) deliberately
    # removed the [:10] cap here so a triage user gets the COMPLETE list. We honor
    # that: the text is intentionally uncapped (the non-full `reliability` view is
    # the bounded default). Source/category counts are bounded by deployment
    # cardinality, not by inventory size, so this can't scale unboundedly.
    lines = ["# Data reliability (full ranking)", "## Data sources"]
    for s in src_ranked:
        lines.append(
            f"- {s.get('name', '—')}: {s.get('confidence', 0)}% "
            f"({_fmt_int(s.get('assetCount', 0))} assets)"
        )
    lines.append("## Asset categories")
    for g in cat_ranked:
        lines.append(
            f"- {g.get('category', '—')}: {g.get('avgConfidence', 0)}% "
            f"({_fmt_int(g.get('count', 0))} assets)"
        )
    series = [
        {"entity": s.get("name", "—"), "kind": "source",
         "score": s.get("confidence", 0),
         "sample": s.get("assetCount", 0)}
        for s in src_ranked
    ] + [
        {"entity": g.get("category", "—"), "kind": "category",
         "score": g.get("avgConfidence", 0), "sample": g.get("count", 0)}
        for g in cat_ranked
    ]
    artifact = {
        "type": "table",
        "title": "Data reliability (full ranking)",
        "columns": [
            {"key": "entity", "label": "Entity"},
            {"key": "kind", "label": "Type"},
            {"key": "score", "label": "Score %"},
            {"key": "sample", "label": "Assets"},
        ],
        "series": series,
        "meta": {"source_tool": _ARTIFACT_SOURCE, "section": "reliability",
                 "full": True},
    }
    return "\n".join(lines), artifact


def _coerce_field_list(fields: Any) -> List[str]:
    if fields is None:
        return list(_DEFAULT_QUALITY_FIELDS)
    if isinstance(fields, str):
        return [f.strip() for f in fields.split(",") if f.strip()]
    if isinstance(fields, (list, tuple)):
        return [str(f).strip() for f in fields if str(f).strip()]
    return list(_DEFAULT_QUALITY_FIELDS)


def _section_data_quality(
    svc, db, resolver, *, fields, full, drilldown, category,
) -> ToolReturn:
    # Per-category confidence ranking (lowest-confidence first = triage order),
    # straight from the UI's /inventory gauges so the numbers match.
    gauges = (svc.get_inventory_summary() or {}).get("categoryGauges") or []
    if category:
        gauges = [g for g in gauges
                  if str(g.get("category", "")).lower() == category.lower()]
    ranked = sorted(gauges, key=lambda g: g.get("avgConfidence", 0) or 0)
    overflow = 0
    if not full and len(ranked) > _RANKING_CAP:
        overflow = len(ranked) - _RANKING_CAP
        ranked = ranked[:_RANKING_CAP]

    summ = svc.get_summary() or {}
    completeness = summ.get("completeness", 0)
    rating = (summ.get("dataQuality") or {}).get("rating", "—")

    scope = f" ({category})" if category else ""
    # completeness/rating come from the global get_summary() (no category-scoped
    # method exists), so label them inventory-wide in a category-scoped report
    # rather than implying they're the category's numbers.
    inv = " (inventory-wide)" if category else ""
    lines = [
        f"# Data quality{scope}",
        f"- Overall completeness{inv}: {completeness}%",
        f"- Data quality rating{inv}: {rating}",
    ]

    # Per-field coverage via the canonical engine (notempty / total). Needs the
    # asset query infra; without it we still return the ranking + completeness.
    field_list = _coerce_field_list(fields)
    coverage_rows: List[Dict[str, Any]] = []
    artifact: Optional[Dict[str, Any]] = None
    field_overflow = 0
    if db is not None and resolver is not None and field_list:
        if len(field_list) > _MAX_QUALITY_FIELDS:
            field_overflow = len(field_list) - _MAX_QUALITY_FIELDS
            field_list = field_list[:_MAX_QUALITY_FIELDS]
        base = ([{"field": "Category", "op": "eq", "value": category}]
                if category else None)
        try:
            _, total = list_assets_query(
                db, resolver, filters=base, page=1, limit=1,
            )
            for f in field_list:
                nn = (list(base) if base else []) + [
                    {"field": f, "op": "notempty"}
                ]
                _, covered = list_assets_query(
                    db, resolver, filters=nn, page=1, limit=1,
                )
                pct = round(covered / total * 100, 1) if total else 0.0
                row: Dict[str, Any] = {
                    "field": f, "covered": covered, "total": total, "pct": pct,
                }
                # Drill into the assets MISSING the field (the actionable set).
                if drilldown:
                    miss = (list(base) if base else []) + [
                        {"field": f, "op": "empty"}
                    ]
                    row["drilldown"] = build_drilldown(miss)
                coverage_rows.append(row)
        except (FieldResolutionError, OperatorResolutionError) as exc:
            # Loud: a dropped field would silently mis-report coverage (§4).
            return str(exc), None

        lines.append("Field coverage:")
        for r in coverage_rows:
            lines.append(
                f"- {r['field']}: {r['pct']}% "
                f"({_fmt_int(r['covered'])}/{_fmt_int(r['total'])})"
            )
        if field_overflow:
            lines.append(
                f"[{field_overflow} more field(s) not shown — request fewer "
                f"in `fields` (max {_MAX_QUALITY_FIELDS}).]"
            )
        artifact = {
            "type": "table",
            "title": "Field coverage",
            "columns": [
                {"key": "field", "label": "Field"},
                {"key": "covered", "label": "Covered"},
                {"key": "total", "label": "Total"},
                {"key": "pct", "label": "Coverage %"},
            ],
            "series": coverage_rows,
            "meta": {
                "source_tool": _ARTIFACT_SOURCE,
                "section": "data_quality",
                "category": category,
                "ranking": ranked,
            },
        }

    lines.append("Confidence by category (lowest first):")
    for g in ranked:
        lines.append(
            f"- {g.get('category', '—')}: avg {g.get('avgConfidence', 0)}% "
            f"({_fmt_int(g.get('count', 0))} assets, "
            f"{g.get('confidenceLevel', '—')})"
        )
    if overflow:
        lines.append(
            f"[{overflow} more categor(ies) not shown — pass full=true.]"
        )
    return "\n".join(lines), artifact


# ===========================================================================
# get_dashboard_trend
# ===========================================================================

_TREND_KINDS = ("posture", "coverage", "asset", "timeline")
_BUCKETS = ("day", "week", "month")
# Keep the last N buckets in the text preview; the full series rides the artifact.
_TREND_TEXT_ROWS = 14


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_days(days: Any, start: Any, end: Any) -> Tuple[Optional[int], Optional[str]]:
    """Resolve the look-back window to a clamped day count (1..365), matching the
    UI's clamp. A custom start/end is converted to its span (the underlying
    service anchors every trend to today, so a custom window is a look-back of
    that many days). Bad dates are a loud error, never silently ignored (§4)."""
    if start or end:
        if not (start and end):
            return None, (
                "For a custom window pass BOTH start and end (YYYY-MM-DD); "
                "otherwise use days."
            )
        try:
            s = _dt.date.fromisoformat(str(start))
            e = _dt.date.fromisoformat(str(end))
        except ValueError:
            return None, (
                f"Invalid date in start/end (use YYYY-MM-DD): "
                f"start={start!r}, end={end!r}."
            )
        if e < s:
            return None, "end date must be on or after start date."
        return max(1, min(365, (e - s).days + 1)), None
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = 30
    return max(1, min(365, d)), None


def _synth_dates(n: int) -> List[_dt.date]:
    """``n`` calendar dates oldest-first, ending today — the exact window the
    DashboardService trend queries cover (generate_series(today-(n-1)..today)),
    so points align by index even though the service omits the dates."""
    today = _dt.date.today()
    return [today - _dt.timedelta(days=(n - 1 - i)) for i in range(n)]


def _bucket_key(d: _dt.date, bucket: str) -> str:
    if bucket == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if bucket == "week":
        iso = d.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    return d.isoformat()


def _bucket_points(
    dates: List[_dt.date], points: List[Dict[str, Any]],
    value_keys: List[str], *, bucket: str, agg: str,
) -> List[Dict[str, Any]]:
    """Roll per-day points up into bucket periods, preserving chronological
    order. ``agg='sum'`` for event FLOWS (added/removed); ``agg='last'`` for
    point-in-time LEVELS (tier/coverage counts) — summing daily snapshots of a
    level would be nonsense, so the period takes its last value."""
    if not points:
        return []
    order: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for d, p in zip(dates, points):
        key = _bucket_key(d, bucket)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(p)
    rows: List[Dict[str, Any]] = []
    for key in order:
        row: Dict[str, Any] = {"period": key}
        for k in value_keys:
            vals = [_to_int(p.get(k, 0)) for p in grouped[key]]
            row[k] = sum(vals) if agg == "sum" else (vals[-1] if vals else 0)
        rows.append(row)
    return rows


def get_dashboard_trend_impl(
    dashboard_service,
    *,
    kind: str = "posture",
    days: Any = 30,
    start: Optional[str] = None,
    end: Optional[str] = None,
    metric: str = "srv_edr",
    bucket: str = "day",
) -> ToolReturn:
    """Return a homepage time-series as ``(text, artifact)``.

    ``kind``: posture (tier levels) | coverage (one posture-coverage metric) |
    asset|timeline (added/removed flows). ``bucket`` rolls daily points up into
    week/month periods. Rendered as a ``table`` artifact (line/area chart types
    aren't in the artifact union yet); the full per-bucket series rides ``meta``.
    """
    if dashboard_service is None:
        return _UNAVAILABLE, None
    if kind not in _TREND_KINDS:
        return (
            f"Unknown trend kind {kind!r}. Valid kinds: {', '.join(_TREND_KINDS)}.",
            None,
        )
    bucket = bucket if bucket in _BUCKETS else "day"

    resolved_days, err = _resolve_days(days, start, end)
    if err:
        return err, None
    days = resolved_days

    extra_meta: Dict[str, Any] = {}
    if kind == "coverage":
        # Import here to avoid a module-level dependency on the service class;
        # the valid metric set is single-sourced from it (no drift).
        from src.modules.dashboard.services.dashboard_service import (
            DashboardService,
        )
        valid = tuple(DashboardService.COVERAGE_METRICS.keys())
        if metric not in valid:
            return (
                f"Unknown coverage metric {metric!r}. Valid metrics: "
                f"{', '.join(valid)}.",
                None,
            )
        res = dashboard_service.get_coverage_trend(metric=metric, days=days)
        data = res.get("data") or []
        points = [
            {"covered": d.get("added", 0), "uncovered": d.get("removed", 0)}
            for d in data
        ]
        value_keys = ["covered", "uncovered"]
        agg, title = "last", f"Coverage trend — {metric}"
        extra_meta["metric"] = metric
    elif kind in ("asset", "timeline"):
        res = dashboard_service.get_asset_timeline(days=days)
        data = res.get("data") or []
        points = [
            {"added": d.get("added", 0),
             "returned": d.get("returned", 0),
             "removed": d.get("removed", 0)}
            for d in data
        ]
        value_keys = ["added", "returned", "removed"]
        agg, title = "sum", "Asset added/returned/removed timeline"
    else:  # posture
        res = dashboard_service.get_posture_trend(days=days)
        series = res.get("series") or {}
        n = max((len(series.get(t) or []) for t in _POSTURE_TIERS), default=0)
        points = [
            {t: ((series.get(t) or [])[i]
                 if i < len(series.get(t) or []) else 0)
             for t in _POSTURE_TIERS}
            for i in range(n)
        ]
        value_keys = list(_POSTURE_TIERS)
        agg, title = "last", "Posture tier trend"

    rows = _bucket_points(
        _synth_dates(len(points)), points, value_keys, bucket=bucket, agg=agg,
    )

    lines = [f"# {title} (last {days}d, {len(rows)} {bucket} bucket(s))"]
    if start or end:
        # The DashboardService trend queries are anchored to today, so a custom
        # start/end can only be honoured as a look-back of that many days. Say so
        # in the output (not just the tool description) — the rows below are
        # labelled with today-relative dates, NOT the literal start–end range.
        lines.append(
            f"(custom start/end approximated as the last {days} day(s) ending "
            "today — dashboard trends are anchored to today)"
        )
    if len(rows) > _TREND_TEXT_ROWS:
        lines.append(f"(showing the last {_TREND_TEXT_ROWS} of {len(rows)})")
    for r in rows[-_TREND_TEXT_ROWS:]:
        parts = ", ".join(f"{k}={_fmt_int(r[k])}" for k in value_keys)
        lines.append(f"- {r['period']}: {parts}")
    if not rows:
        lines.append("(no data in this window)")

    artifact = {
        "type": "table",
        "title": title,
        "columns": (
            [{"key": "period", "label": "Period"}]
            + [{"key": k, "label": k.replace("_", " ").title()}
               for k in value_keys]
        ),
        "series": rows,
        "meta": {
            "source_tool": _TREND_SOURCE,
            "kind": kind,
            "bucket": bucket,
            "days": days,
            "note": ("time-series rendered as a table; line/area artifact "
                     "types are not yet in the chat artifact union"),
            **extra_meta,
        },
    } if rows else None
    return "\n".join(lines), artifact


# ===========================================================================
# get_relationship_graph
# ===========================================================================

_GRAPH_VERSIONS = ("v1", "v2")
# Entity types in a stable display order.
_GRAPH_TYPES = ("categories", "subcategories", "operatingSystems", "dataSources")


def _resolve_names(ids: Any, name_map: Dict[Any, str]) -> List[str]:
    """Map a relationship id-array to entity NAMES, dropping unresolved ids.
    This is what strips the presentation-only hash/sequential ids: the chat
    output speaks names, never opaque ids."""
    return [name_map[i] for i in (ids or []) if i in name_map]


def _v2_graph(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    cats = data.get("categories") or []
    subs = data.get("subcategories") or []
    oss = data.get("operatingSystems") or []
    srcs = data.get("dataSources") or []
    cat_map = {c.get("id"): c.get("name") for c in cats}
    sub_map = {s.get("id"): s.get("name") for s in subs}
    os_map = {o.get("id"): o.get("name") for o in oss}
    src_map = {s.get("id"): s.get("name") for s in srcs}
    return {
        "categories": [{
            "name": c.get("name"), "count": c.get("count", 0), "links": {
                "subcategories": _resolve_names(c.get("subcategories"), sub_map),
                "operatingSystems": _resolve_names(c.get("os"), os_map),
                "dataSources": _resolve_names(c.get("sources"), src_map),
            }} for c in cats],
        "subcategories": [{
            "name": s.get("name"), "count": s.get("count", 0), "links": {
                "categories": _resolve_names(s.get("categories"), cat_map),
                "operatingSystems": _resolve_names(s.get("os"), os_map),
                "dataSources": _resolve_names(s.get("sources"), src_map),
            }} for s in subs],
        "operatingSystems": [{
            "name": o.get("name"), "count": o.get("count", 0), "links": {
                "categories": _resolve_names(o.get("categories"), cat_map),
                "subcategories": _resolve_names(o.get("subcategories"), sub_map),
                "dataSources": _resolve_names(o.get("sources"), src_map),
            }} for o in oss],
        "dataSources": [{
            "name": s.get("name"), "count": s.get("count", 0), "links": {
                "categories": _resolve_names(s.get("categories"), cat_map),
                "subcategories": _resolve_names(s.get("subcategories"), sub_map),
                "operatingSystems": _resolve_names(s.get("os"), os_map),
            }} for s in srcs],
    }


def _v1_graph(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    # In v1 only subcategories carry cross-link arrays (→ os ids, source uuids)
    # plus their parent category name; the other entity lists are nodes only.
    cats = data.get("categories") or []
    oss = data.get("operatingSystems") or []
    subs = data.get("subcategories") or []
    srcs = data.get("dataSources") or []
    os_map = {o.get("id"): o.get("name") for o in oss}
    src_map = {s.get("id"): s.get("name") for s in srcs}
    return {
        "categories": [
            {"name": c.get("name"), "count": c.get("count", 0), "links": {}}
            for c in cats],
        "subcategories": [{
            "name": s.get("name"), "count": s.get("count", 0), "links": {
                "categories": [s.get("category")] if s.get("category") else [],
                "operatingSystems": _resolve_names(s.get("os"), os_map),
                "dataSources": _resolve_names(s.get("sources"), src_map),
            }} for s in subs],
        "operatingSystems": [
            {"name": o.get("name"), "count": o.get("count", 0), "links": {}}
            for o in oss],
        "dataSources": [
            {"name": s.get("name"), "count": s.get("count", 0), "links": {}}
            for s in srcs],
    }


def get_relationship_graph_impl(
    dashboard_service,
    *,
    version: str = "v1",
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    os: Optional[str] = None,
    integration_id: Optional[str] = None,
) -> ToolReturn:
    """Return the cross-entity relationship graph as ``(text, artifact)``.

    ``version='v1'`` → ``get_flower_data`` (filterable, subcategory cross-links);
    ``version='v2'`` → ``get_flower_data_v2`` (any-to-any). Presentation-only
    colour/icon/id-hash are stripped: relationship ids are resolved to entity
    NAMES. The graph is a force-graph/sankey shape the chat artifact union can't
    render yet, so it rides ``meta.graph`` with a node table as the artifact.
    """
    if dashboard_service is None:
        return _UNAVAILABLE, None
    if version not in _GRAPH_VERSIONS:
        return (
            f"Unknown relationship version {version!r}. Valid: "
            f"{', '.join(_GRAPH_VERSIONS)}.",
            None,
        )

    if version == "v2":
        data = dashboard_service.get_flower_data_v2() or {}
        graph = _v2_graph(data)
        total = data.get("totalAssets", 0)
    else:
        filters = {
            k: v for k, v in (
                ("category", category), ("subcategory", subcategory),
                ("os", os), ("integration_id", integration_id),
            ) if v is not None
        }
        data = dashboard_service.get_flower_data(filters or None) or {}
        graph = _v1_graph(data)
        total = data.get("totalAssets", 0)

    lines = [f"# Relationship graph ({version})", f"Total assets: {total:,}"]
    for etype in _GRAPH_TYPES:
        lines.append(f"- {etype}: {len(graph.get(etype, []))}")
    # A few concrete cross-links so the model sees the shape without dumping the
    # whole adjacency into context (the full graph rides meta.graph).
    samples: List[str] = []
    for etype in _GRAPH_TYPES:
        for e in graph.get(etype, []):
            links = {k: v for k, v in (e.get("links") or {}).items() if v}
            if links:
                joined = "; ".join(
                    f"{lt}: {', '.join(vs)}" for lt, vs in links.items()
                )
                samples.append(f"- [{etype}] {e['name']} → {joined}")
            if len(samples) >= 10:
                break
        if len(samples) >= 10:
            break
    if samples:
        lines.append("Cross-links (sample):")
        lines.extend(samples)

    series: List[Dict[str, Any]] = []
    for etype in _GRAPH_TYPES:
        for e in graph.get(etype, []):
            link_count = sum(len(v) for v in (e.get("links") or {}).values())
            series.append({
                "entity": e["name"], "type": etype,
                "count": e.get("count", 0), "links": link_count,
            })

    artifact = {
        "type": "table",
        "title": f"Relationship graph ({version})",
        "columns": [
            {"key": "entity", "label": "Entity"},
            {"key": "type", "label": "Type"},
            {"key": "count", "label": "Assets"},
            {"key": "links", "label": "Links"},
        ],
        "series": series,
        "meta": {
            "source_tool": _GRAPH_SOURCE,
            "version": version,
            "totalAssets": total,
            "graph": graph,
            "note": ("force-graph/sankey artifact types are not yet in the chat "
                     "artifact union; the resolved graph rides meta.graph"),
        },
    } if series else None
    return "\n".join(lines), artifact
