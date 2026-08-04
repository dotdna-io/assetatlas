# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enrichment dataset tools for the AssetAtlas MCP server.

Tools: list_enrichment_datasets
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import truncate, validate_uuid

logger = logging.getLogger(__name__)

_DEFAULT_PER_PAGE = 20
_MAX_PER_PAGE = 100
_DEFAULT_SAMPLE_LIMIT = 5
_MAX_SAMPLE_LIMIT = 100


def register_tools(mcp):
    """Register enrichment dataset tools on the FastMCP server."""

    @mcp.tool()
    def list_enrichment_datasets(
        dataset_id: str | None = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List enrichment datasets, or return details + sample rows for one.

        Args:
            dataset_id: Optional dataset UUID. When provided, return detail
                view including a few sample rows instead of the list view.
        """
        return list_enrichment_datasets_impl(get_db(ctx), dataset_id=dataset_id)


def list_enrichment_datasets_impl(
    db,
    *,
    enrichment_service=None,
    dataset_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = _DEFAULT_PER_PAGE,
    sample_limit: int = _DEFAULT_SAMPLE_LIMIT,
    preview: bool = False,
    ext_fields: bool = False,
) -> str:
    # ext_fields and preview delegate to the canonical EnrichmentService (the
    # matching logic must not be re-implemented — that would be a parallel
    # engine). list/detail read straight from the dataset/row tables.
    if ext_fields:
        return _ext_fields_form(enrichment_service)

    if preview:
        if dataset_id is None:
            return "preview=true requires a dataset_id."
        err = validate_uuid(dataset_id, "dataset")
        if err:
            return err
        return _preview_form(enrichment_service, dataset_id)

    if dataset_id is not None:
        err = validate_uuid(dataset_id, "dataset")
        if err:
            return err
        return _detail_form(db, dataset_id, sample_limit=sample_limit)

    return _list_form(db, search=search, status=status,
                      page=page, per_page=per_page)


def _list_form(db, *, search: str | None = None, status: str | None = None,
               page: int = 1, per_page: int = _DEFAULT_PER_PAGE) -> str:
    page = max(int(page or 1), 1)
    per_page = max(1, min(int(per_page or _DEFAULT_PER_PAGE), _MAX_PER_PAGE))
    offset = (page - 1) * per_page

    # Exclude soft-deleted datasets so the tool matches the UI (which hides
    # them); the old tool listed them.
    conditions = ["status != %(excluded)s"]
    params: Dict[str, Any] = {
        "excluded": "deleted", "lim": per_page, "off": offset,
    }
    if search:
        conditions.append("name ILIKE %(search)s")
        params["search"] = f"%{search}%"
    if status:
        conditions.append("status = %(status)s")
        params["status"] = status
    where_sql = " AND ".join(conditions)

    rows: List[Dict[str, Any]] = db.execute_query(
        f"""
        SELECT id::text, name, match_field, match_type, target_field,
               status, row_count, matched_count, uploaded_by,
               created_at, updated_at,
               COUNT(*) OVER() AS total_count
        FROM enrichment_datasets
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT %(lim)s OFFSET %(off)s
        """,
        params,
    ) or []

    if not rows:
        if search or status:
            return ("No enrichment datasets match the given filters. Widen "
                    "`search`/`status` to see more.")
        if page > 1:
            # Paged off the end — don't imply the system has no datasets.
            return (f"No enrichment datasets on page {page} — it is past the "
                    f"last page. Request page 1.")
        return "No enrichment datasets defined."

    total = (int(rows[0]["total_count"])
             if rows[0].get("total_count") is not None else len(rows))
    pages = (total + per_page - 1) // per_page if per_page else 1

    lines = [f"# Enrichment datasets (page {page} of {pages}, total {total})"]
    for r in rows:
        lines.append(
            f"- {r['name']} [{r.get('status')}] — "
            f"match: {r.get('match_field')} → {r.get('target_field')} · "
            f"{r.get('matched_count')}/{r.get('row_count')} matched · "
            f"created {r.get('created_at')} · id {r.get('id')}"
        )
    if page < pages:
        lines.append("")
        lines.append(
            f"_{total - page * per_page} more not shown — request page "
            f"{page + 1}, or narrow with `search`/`status`._"
        )
    return "\n".join(lines)


def _detail_form(db, dataset_id: str, *,
                 sample_limit: int = _DEFAULT_SAMPLE_LIMIT) -> str:
    sample_limit = max(0, min(int(sample_limit or 0), _MAX_SAMPLE_LIMIT))

    rows = db.execute_query(
        """
        SELECT id::text, name, match_field, match_type, target_field, status,
               row_count, matched_count, uploaded_by, created_at, updated_at
        FROM enrichment_datasets
        WHERE id = %(id)s::uuid AND status != %(excluded)s
        """,
        {"id": dataset_id, "excluded": "deleted"},
    )
    dataset: Optional[Dict[str, Any]] = rows[0] if rows else None
    if not dataset:
        return f"Enrichment dataset {dataset_id} not found."

    # Deterministic ORDER BY so the same sample is returned every call (the
    # old query had no ORDER BY → arbitrary rows). Skip the roundtrip when
    # there's nothing to fetch.
    samples: List[Dict[str, Any]] = []
    if (dataset.get("row_count") or 0) > 0 and sample_limit > 0:
        samples = db.execute_query(
            """
            SELECT match_value, enrich_value
            FROM enrichment_rows
            WHERE dataset_id = %(id)s::uuid
            ORDER BY match_value, enrich_value
            LIMIT %(lim)s
            """,
            {"id": dataset_id, "lim": sample_limit},
        ) or []

    lines = [
        f"# {dataset['name']}",
        f"- status: {dataset.get('status')}",
        f"- match: {dataset.get('match_field')} "
        f"({dataset.get('match_type')}) → {dataset.get('target_field')}",
        f"- rows: {dataset.get('matched_count')}/{dataset.get('row_count')} matched",
        f"- uploaded by: {dataset.get('uploaded_by')}",
        f"- created: {dataset.get('created_at')}",
        f"- updated: {dataset.get('updated_at')}",
    ]

    if samples:
        lines.append("")
        lines.append(f"## Sample rows (showing {len(samples)} of "
                     f"{dataset.get('row_count')})")
        for s in samples:
            lines.append(f"- {s.get('match_value')} → {s.get('enrich_value')}")

    return "\n".join(lines)


def _preview_form(enrichment_service, dataset_id: str) -> str:
    if enrichment_service is None:
        return ("Enrichment preview is not available in this context "
                "(enrichment service not configured).")
    try:
        result = enrichment_service.preview_dataset(dataset_id)
    except Exception:
        logger.exception("Enrichment preview failed for %s", dataset_id)
        return "Enrichment preview failed (see server logs)."
    if result is None:
        return f"Enrichment dataset {dataset_id} not found."

    lines = [
        f"# Preview: {result.get('dataset_name')}",
        f"- would match: {result.get('matched_count')} live asset(s)",
        f"- dataset rows: {result.get('total_rows')}",
        "_Counts assets the dataset's match values hit right now, before "
        "apply. Apply is a mutation and is not available from chat._",
    ]
    samples = result.get("sample_matches") or []
    if samples:
        lines.append("")
        lines.append(f"## Sample matches ({len(samples)})")
        for s in samples:
            host = s.get("hostname") or s.get("id") or "—"
            against = s.get("matched_against")
            enrich = s.get("enrich_value")
            detail = f"{host}"
            if against is not None:
                detail += f" (matched on {against!r})"
            if enrich is not None:
                detail += f" → {enrich}"
            lines.append(f"- {detail}")
    return "\n".join(lines)


def _ext_fields_form(enrichment_service) -> str:
    if enrichment_service is None:
        return ("Extended-attribute field discovery is not available in this "
                "context (enrichment service not configured).")
    try:
        fields = enrichment_service.list_ext_fields()
    except Exception:
        logger.exception("Enrichment ext-field discovery failed")
        return "Extended-attribute field discovery failed (see server logs)."

    fields = fields or []
    if not fields:
        return ("No extended-attribute match-field paths found in current "
                "asset data.")

    lines = [
        f"# Extended-attribute match fields ({len(fields)})",
        "Any `path` below can be used as an enrichment `match_field`.",
        "",
        "| path | source | key | sample |",
        "| --- | --- | --- | --- |",
    ]
    for f in fields:
        sample = f.get("sample_value")
        sample_s = truncate(str(sample), 40) if sample is not None else "—"
        lines.append(
            f"| {f.get('path')} | {f.get('source')} | {f.get('key')} "
            f"| {sample_s or '—'} |"
        )
    return "\n".join(lines)
