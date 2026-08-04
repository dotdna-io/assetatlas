# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audit trail tools for the AssetAtlas MCP/chat server.

Tools: get_audit_trail

Mirrors the UI audit route (``src/api/blueprints/audit_trail/routes.py``): a
real total COUNT (mirroring the JOIN), partial ILIKE match on username + path,
status_code OR status_class (e.g. "5xx"), and start/end dates PARSED with a
clean error string on bad input. ``request_body`` is redacted at write time by
the audit middleware (``_sanitize_body``). ``response_summary`` is stored RAW
(the middleware never sanitizes it — a login/refresh response carries access
tokens), so we redact it on display using the server's sensitive-field set
before it can reach the LLM.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.middleware.audit_middleware import _SENSITIVE_FIELDS
from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import truncate, unknown_enum
from src.modules.fields.operators import _escape_like

_MAX_PAGE_SIZE = 100
_PATH_TRUNC_CHARS = 60
_BODY_TRUNC_CHARS = 80

# status_class -> [min, max) HTTP status range.
_STATUS_CLASSES: Dict[str, Tuple[int, int]] = {
    "1xx": (100, 200),
    "2xx": (200, 300),
    "3xx": (300, 400),
    "4xx": (400, 500),
    "5xx": (500, 600),
}

logger = logging.getLogger(__name__)


def register_tools(mcp):
    """Register audit-trail tools on the FastMCP server."""

    @mcp.tool()
    def get_audit_trail(
        username: str | None = None,
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        status_class: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        per_page: int = 50,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Query the API audit trail.

        Filters: username (partial), method, path (partial), status_code OR
        status_class (e.g. "5xx"), start_date/end_date (ISO-8601). Paginated
        with a real total. Entries include request_body/response_summary as
        stored (already server-redacted).
        """
        return get_audit_trail_impl(
            get_db(ctx),
            username=username,
            method=method,
            path=path,
            status_code=status_code,
            status_class=status_class,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per_page=per_page,
        )


def _parse_dt(label: str, raw: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """Parse an ISO-8601 date/datetime, or return a clean LLM-facing error.

    The previous tool passed the raw string straight to ``timestamp >= %s``,
    so an unparseable value raised inside psycopg2 → HTTP 500. Parsing here
    turns bad input into an actionable string instead.
    """
    if not raw:
        return None, None
    try:
        return datetime.fromisoformat(str(raw)), None
    except (ValueError, TypeError):
        return None, (
            f"Invalid {label} {raw!r}: expected an ISO-8601 date or datetime "
            f"(e.g. '2026-04-01' or '2026-04-01T13:30:00Z')."
        )


def _cell(value: Any, max_chars: int) -> str:
    """Render a value safe for a single markdown table cell."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str, separators=(",", ":"))
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    return truncate(text, max_chars)


def _safe_response_summary(summary: Any) -> Any:
    """Redact a response body that may carry a secret before showing it.

    The audit middleware sanitizes ``request_body`` at write time but stores
    ``response_summary`` raw (the first 500 chars of the JSON response). A
    login/refresh/token-exchange response carries access/refresh tokens, so
    reuse the SERVER's authoritative sensitive-field set and redact the whole
    cell when any of those field names appears — surfacing even a token prefix
    to the LLM/chat transcript would be a credential leak.
    """
    if not summary or not isinstance(summary, str):
        return summary
    low = summary.lower()
    if any(token in low for token in _SENSITIVE_FIELDS):
        return "<redacted: response body may contain a secret>"
    return summary


def get_audit_trail_impl(
    db,
    *,
    username: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    status_class: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> str:
    page = max(page, 1)
    per_page = max(1, min(per_page, _MAX_PAGE_SIZE))

    # --- Validate inputs to loud strings BEFORE any DB work ----------------- #
    start_dt, err = _parse_dt("start_date", start_date)
    if err:
        return err
    end_dt, err = _parse_dt("end_date", end_date)
    if err:
        return err

    status_range: Optional[Tuple[int, int]] = None
    if status_class is not None:
        if status_code is not None:
            return ("Specify either status_code or status_class, not both.")
        normalized = str(status_class).lower()
        if normalized not in _STATUS_CLASSES:
            return unknown_enum("status_class", status_class,
                                sorted(_STATUS_CLASSES))
        status_range = _STATUS_CLASSES[normalized]

    # --- Build WHERE -------------------------------------------------------- #
    conditions: List[str] = ["1=1"]
    params: Dict[str, Any] = {}

    if username:
        # Partial, case-insensitive (mirrors the UI route, issue #634).
        conditions.append("u.username ILIKE %(username)s")
        params["username"] = f"%{username}%"
    if method:
        # str() so a non-string the chat registry doesn't type-check (it skips
        # JSON-Schema type validation) can't crash .upper().
        conditions.append("a.method = %(method)s")
        params["method"] = str(method).upper()
    if path:
        # Partial, case-insensitive; escape the user's % and _ via the shared
        # _escape_like helper (which also str()-coerces) so they match
        # literally rather than acting as SQL wildcards.
        conditions.append("a.path ILIKE %(path)s ESCAPE '\\'")
        params["path"] = f"%{_escape_like(path)}%"
    if status_code is not None:
        conditions.append("a.status_code = %(status_code)s")
        params["status_code"] = status_code
    if status_range is not None:
        conditions.append(
            "a.status_code >= %(status_min)s AND a.status_code < %(status_max)s"
        )
        params["status_min"], params["status_max"] = status_range
    if start_dt is not None:
        conditions.append("a.timestamp >= %(start_date)s")
        params["start_date"] = start_dt
    if end_dt is not None:
        conditions.append("a.timestamp <= %(end_date)s")
        params["end_date"] = end_dt

    where_sql = " AND ".join(conditions)

    # --- Real total COUNT (mirrors the data query's JOIN) ------------------- #
    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM audit_trail a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE {where_sql}
    """
    try:
        count_rows: List[Dict[str, Any]] = db.execute_query(count_sql, params) or []
    except Exception:
        logger.exception("get_audit_trail: count query failed")
        return "Audit trail unavailable (see server logs)."
    total = count_rows[0].get("total", 0) if count_rows else 0

    if not total:
        return "No audit trail entries match the given filters."

    total_pages = (total + per_page - 1) // per_page

    # --- Fetch the page ----------------------------------------------------- #
    data_params = dict(params)
    data_params["limit"] = per_page
    data_params["offset"] = (page - 1) * per_page
    data_sql = f"""
        SELECT a.id, a.timestamp, u.username, a.method, a.path, a.status_code,
               a.ip_address, a.duration_ms, a.request_id,
               a.request_body, a.response_summary
        FROM audit_trail a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE {where_sql}
        ORDER BY a.timestamp DESC, a.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    try:
        rows: List[Dict[str, Any]] = db.execute_query(data_sql, data_params) or []
    except Exception:
        logger.exception("get_audit_trail: data query failed")
        return "Audit trail unavailable (see server logs)."

    header = f"# Audit trail — {total} total (page {page} of {total_pages})"

    if not rows:
        return (f"{header}\n_No entries on this page; "
                f"there are {total_pages} page(s)._")

    lines = [
        header,
        "| Time | User | Method | Path | Status | IP | Duration | Request "
        "| Body | Response |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('timestamp')} | {r.get('username')} | {r.get('method')} "
            f"| {_cell(r.get('path'), _PATH_TRUNC_CHARS)} | {r.get('status_code')} "
            f"| {r.get('ip_address')} | {r.get('duration_ms')}ms "
            f"| {r.get('request_id')} | {_cell(r.get('request_body'), _BODY_TRUNC_CHARS)} "
            f"| {_cell(_safe_response_summary(r.get('response_summary')), _BODY_TRUNC_CHARS)} |"
        )

    shown = (page - 1) * per_page + len(rows)
    remaining = total - shown
    if remaining > 0:
        lines.append(
            f"_{remaining} more not shown (page {page} of {total_pages}) — "
            f"narrow with username/path/status/date filters or request a later "
            f"`page`._"
        )

    return "\n".join(lines)
