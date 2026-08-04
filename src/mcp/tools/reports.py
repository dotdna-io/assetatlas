# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved report tools for the AssetAtlas MCP server.

Tools: list_reports, get_report
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import unknown_enum, validate_uuid

_MAX_PAGE_SIZE = 100
_DESC_PREVIEW_CHARS = 100

# Whitelisted sort keys → real columns (the value is interpolated, so it MUST
# come from this map, never from caller input).
_SORT_COLUMNS = {
    "updated": "updated_at",
    "created": "created_at",
    "title": "title",
}


def register_tools(mcp):
    """Register saved-report tools on the FastMCP server."""

    @mcp.tool()
    def list_reports(
        search: str | None = None,
        page: int = 1,
        limit: int = 50,
        author: str | None = None,
        sort: str = "updated",
        sort_dir: str = "desc",
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List saved reports (paginated, with a real total).

        Args:
            search: Partial match on title/description (ILIKE).
            page: 1-indexed page number.
            limit: Page size (1-100).
            author: Partial match on the creating user's username (ILIKE).
            sort: One of 'updated' (default), 'created', 'title'.
            sort_dir: 'asc' or 'desc' (default).
        """
        return list_reports_impl(
            get_db(ctx), search=search, page=page, limit=limit,
            author=author, sort=sort, sort_dir=sort_dir)

    @mcp.tool()
    def get_report(
        report_id: str,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return the definition of a saved report — each chart's persisted
        queryConfig (type, dimensions, aggregate, filters). Use run_report to
        EXECUTE these definitions and see their data."""
        return get_report_impl(get_db(ctx), report_id=report_id)


def list_reports_impl(
    db,
    *,
    search: str | None = None,
    page: int = 1,
    limit: int = 50,
    author: str | None = None,
    sort: str = "updated",
    sort_dir: str = "desc",
) -> str:
    page = max(page, 1)
    limit = max(1, min(limit, _MAX_PAGE_SIZE))
    offset = (page - 1) * limit
    if sort not in _SORT_COLUMNS:
        return unknown_enum("sort", sort, _SORT_COLUMNS.keys())
    sort_dir = (sort_dir or "desc").lower()
    if sort_dir not in ("asc", "desc"):
        return unknown_enum("sort_dir", sort_dir, ("asc", "desc"))
    sort_col = _SORT_COLUMNS[sort]

    wheres: List[str] = []
    params: Dict[str, Any] = {"lim": limit, "off": offset}
    if search:
        wheres.append("(r.title ILIKE %(q)s OR r.description ILIKE %(q)s)")
        params["q"] = f"%{search}%"
    if author:
        wheres.append("u.username ILIKE %(author)s")
        params["author"] = f"%{author}%"
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    # Real total COUNT (§4): the LIMIT+1 trick only told us "more exist", never
    # how many — so "show me all 47 reports" couldn't be answered honestly.
    total_rows = db.execute_query(
        f"SELECT COUNT(*) AS total FROM reports r "
        f"LEFT JOIN users u ON u.id = r.created_by {where_sql}",
        params,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows: List[Dict[str, Any]] = db.execute_query(
        f"""
        SELECT r.id, r.title, r.description, r.updated_at,
               jsonb_array_length(r.charts) AS chart_count,
               u.username AS author
        FROM reports r
        LEFT JOIN users u ON u.id = r.created_by
        {where_sql}
        ORDER BY r.{sort_col} {sort_dir.upper()}, r.id
        LIMIT %(lim)s OFFSET %(off)s
        """,
        params,
    ) or []

    if not rows:
        if search or author:
            crit = ", ".join(
                bit for bit in (f"search={search!r}" if search else None,
                                f"author={author!r}" if author else None) if bit
            )
            return f"No reports found ({crit})."
        return "No reports found."

    has_more = offset + len(rows) < total
    header = f"# Reports ({total} total, showing {len(rows)}"
    header += f", page {page})" if (page > 1 or has_more) else ")"
    lines = [header]
    for r in rows:
        desc = r.get("description") or ""
        desc_short = desc[:_DESC_PREVIEW_CHARS] if desc else "no description"
        author_str = r.get("author") or "unknown"
        n_charts = r.get("chart_count")
        chart_note = f"{n_charts} chart(s)" if n_charts is not None else ""
        lines.append(
            f"- {r['title']} [{r['id']}] — {desc_short} · by {author_str} · "
            f"{chart_note} · updated {r.get('updated_at')}"
        )
    if has_more:
        lines.append(
            f"_{total - (offset + len(rows))} more — raise `page` or refine "
            "`search`/`author`._"
        )
    return "\n".join(lines)


def _qc_field_label(v: Any) -> str:
    """Best-effort display label for a persisted chart field id WITHOUT a
    resolver (get_report shows the definition, it doesn't execute it).

    A field id is stored as an encoded string ('static:Hostname',
    'ext:Beszel:cpu') or a dict; pull a human-ish name from either."""
    if isinstance(v, dict):
        return str(v.get("name") or v.get("display_name") or v.get("label")
                   or v.get("field") or v.get("id") or v)
    if isinstance(v, str):
        # Encoded 'kind:Name' → 'Name'; 'ext:Source:field' → 'Source:field'.
        return v.split(":", 1)[1] if ":" in v else v
    return str(v) if v is not None else "?"


def _render_query_config(qc: Dict[str, Any]) -> List[str]:
    """Render a persisted chart ``queryConfig`` as a human-readable definition
    (NOT executed — that is run_report's job). Surfaces the query type, the
    select fields, the metric/aggregate, the window/interval/limits, and the
    filters so the user can see what each chart asks before running it."""
    out: List[str] = []
    qt = qc.get("queryType")
    if qt:
        out.append(f"type: {qt}")
    select = qc.get("select") or {}
    for slot, label in (("dimension", "dimension"), ("segment", "segment"),
                        ("xAxis", "x"), ("yAxis", "y"),
                        ("timeField", "time field"), ("sourceField", "source"),
                        ("targetField", "target"), ("rowField", "row"),
                        ("columnField", "column")):
        if select.get(slot) is not None:
            out.append(f"{label}: {_qc_field_label(select[slot])}")
    metric = select.get("metric")
    if isinstance(metric, dict):
        agg = metric.get("aggregate") or metric.get("function") or "COUNT"
        mf = metric.get("field_id")
        out.append(f"metric: {agg} of {_qc_field_label(mf)}" if mf is not None
                   else f"metric: {agg}")
    options = qc.get("options") or {}
    if options.get("interval"):
        out.append(f"interval: {options['interval']}")
    window = options.get("timeRange") or options.get("last")
    if window:
        out.append(f"window: {window}")
    for lim_key in ("dimensionLimit", "segmentLimit", "rowLimit", "colLimit",
                    "limit"):
        if options.get(lim_key) is not None:
            out.append(f"{lim_key}: {options[lim_key]}")
    if options.get("percentage") or qt == "trend_percentage":
        out.append("percentage: true")
    for fl in (qc.get("filters") or []):
        if isinstance(fl, dict):
            fld = _qc_field_label(fl.get("field_id") or fl.get("field"))
            op = fl.get("operator") or fl.get("op") or "?"
            out.append(f"filter: {fld} {op} {fl.get('value')!r}")
    comp = qc.get("comparisonFilters") or []
    if comp:
        out.append(f"comparison base: {len(comp)} filter(s)")
    return out


def get_report_impl(db, *, report_id: str) -> str:
    err = validate_uuid(report_id, "report")
    if err:
        return err

    rows = db.execute_query(
        """
        SELECT r.id, r.title, r.description, r.charts, r.created_by,
               r.updated_at, u.username AS author
        FROM reports r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.id = %(id)s::uuid
        """,
        {"id": report_id},
    )
    report: Optional[Dict[str, Any]] = rows[0] if rows else None
    if not report:
        return f"Report {report_id} not found."

    created_by = report.get("author") or report.get("created_by") or "unknown"
    description = report.get("description") or "_no description_"

    lines = [
        f"# {report['title']}",
        f"- id: {report['id']}",
        f"- created by: {created_by}",
        f"- updated: {report.get('updated_at')}",
        "",
        description,
    ]

    charts: List[Dict[str, Any]] = report.get("charts") or []
    if not charts:
        lines.append("")
        lines.append("_No charts defined._")
    else:
        lines.append("")
        lines.append(f"## Charts ({len(charts)})")
        for i, chart in enumerate(charts, start=1):
            qc = chart.get("queryConfig") if isinstance(chart, dict) else None
            ctype = chart.get("type")
            if not ctype and isinstance(qc, dict):
                ctype = qc.get("queryType")
            ctype = ctype or "?"
            ctitle = chart.get("title") or chart.get("name")
            if not ctitle:
                # Fall back to first key as a hint when title missing.
                first_key = next(iter(chart.keys()), "untitled")
                ctitle = first_key
            lines.append(f"{i}. {ctitle} ({ctype})")
            if isinstance(qc, dict) and qc:
                for ln in _render_query_config(qc):
                    lines.append(f"     - {ln}")
            else:
                lines.append("     - (no stored query — run_report can't "
                             "execute this one)")

    return "\n".join(lines)
