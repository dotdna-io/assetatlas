# SPDX-License-Identifier: AGPL-3.0-or-later
"""``export_assets`` — stream the chosen view to a downloadable CSV (plan §2.A).

Kept SEPARATE from ``query_assets`` on purpose: streaming-to-file vs
rows-into-context is a different safety contract (a whole-inventory pull must
never enter the model context), and separate schemas keep both tools' required
params minimal.

It is NOT a new query path. It composes the SAME wrappers (``list_assets_query``
/ ``aggregate_assets_query``) for validation + the on-screen preview, and the
real download reuses the parameterized ``/api/assets/export.csv`` (list) and
``/api/assets/aggregate.csv`` (matrix) streaming routes: the table artifact
carries ``meta.query`` (incl the chosen ``fields``) so the frontend's Download
CSV button hits the right route with the right columns.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.mcp.tools._common import parse_filters_arg
from src.mcp.tools._query import (
    CHAT_LIST_FIELDS_ENCODED,
    _normalize_field_list,
    resolve_list_columns,
)
from src.mcp.tools.assets import (
    FieldResolutionError,
    aggregate_assets_query,
    list_assets_query,
)
from src.mcp.tools.query_assets import _coerce_fields
from src.modules.fields.operators import OperatorResolutionError

from src.api.blueprints.chat.artifacts import (
    build_table_from_aggregate,
    build_table_from_assets,
)

ToolReturn = Tuple[str, Optional[Dict[str, Any]]]

# A small on-screen preview so the user sees what they're about to download
# without the whole set entering the model context (the full set is the CSV).
_EXPORT_PREVIEW_ROWS = 10
# The aggregate matrix is bounded; show a generous preview (the CSV has it all).
_AGG_PREVIEW_ROWS = 50


def export_assets(
    db,
    resolver,
    *,
    target: str = "list",
    filters: Any = None,
    logic: str = "and",
    search: Optional[str] = None,
    fields: Any = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
    row_field: Optional[str] = None,
    col_field: Optional[str] = None,
    agg: str = "COUNT",
    metric_field: Optional[str] = None,
    show: str = "count",
) -> ToolReturn:
    """Dispatch by ``target`` (list | aggregate), returning ``(text, artifact)``;
    the artifact carries the download wiring."""
    if target == "list":
        return _export_list(
            db, resolver, filters=filters, logic=logic, search=search,
            fields=fields, sort_by=sort_by, sort_dir=sort_dir,
        )
    if target == "aggregate":
        return _export_aggregate(
            db, resolver, row_field=row_field, col_field=col_field, agg=agg,
            metric_field=metric_field, filters=filters, logic=logic, show=show,
        )
    return (
        f"Unknown target {target!r}. export_assets requires "
        "target = 'list' | 'aggregate'.",
        None,
    )


def _export_list(
    db, resolver, *, filters, logic, search, fields, sort_by, sort_dir,
) -> ToolReturn:
    parsed_filters, err = parse_filters_arg(filters)
    if err:
        return err, None
    effective_fields = _coerce_fields(fields) or CHAT_LIST_FIELDS_ENCODED
    try:
        preview_rows, total = list_assets_query(
            db, resolver, filters=parsed_filters or [], search=search, page=1,
            limit=_EXPORT_PREVIEW_ROWS, sort_by=sort_by, sort_dir=sort_dir,
            fields=effective_fields, logic=logic,
        )
    except (OperatorResolutionError, FieldResolutionError) as exc:
        return str(exc), None

    columns = resolve_list_columns(
        _normalize_field_list(effective_fields, resolver), resolver,
    )
    col_labels = ", ".join(label for _, label in columns)
    text = (
        f"Ready to export {total} asset(s) as CSV with {len(columns)} "
        f"column(s): {col_labels}. Use the Download CSV button on the table to "
        f"download the full set."
    )
    artifact = build_table_from_assets(
        preview_rows, total=total, source_tool="export_assets", columns=columns,
        query={
            "filters": filters or None,
            "search": search or None,
            "sort_by": sort_by or None,
            "sort_dir": sort_dir,
            "fields": effective_fields,
            "logic": logic,
        },
    ) if preview_rows else None
    return text, artifact


def _export_aggregate(
    db, resolver, *, row_field, col_field, agg, metric_field, filters, logic,
    show,
) -> ToolReturn:
    if not row_field:
        return (
            "export target='aggregate' requires 'row_field' (the field to "
            "group rows by).",
            None,
        )
    result, error_hint = aggregate_assets_query(
        db, resolver, row_field=row_field, col_field=col_field,
        filters=filters or [], agg=agg, metric_field=metric_field, logic=logic,
    )
    if error_hint is not None:
        return error_hint, None

    # Full matrix in the preview table (the CSV carries it all + totals).
    artifact = build_table_from_aggregate(
        result, row_field, col_field, max_rows=_AGG_PREVIEW_ROWS,
    )
    n_rows = len(result.get("rows") or [])
    pivot = f" × {col_field}" if col_field else ""
    agg_label = (agg or "COUNT").upper()
    text = (
        f"Ready to export the {row_field}{pivot} {agg_label} matrix as CSV "
        f"({n_rows} group(s)). Use the Download CSV button to download the full "
        f"matrix incl row/column totals."
    )
    if artifact is not None:
        meta = artifact.setdefault("meta", {})
        meta["source_tool"] = "export_assets"
        # 'aggregate' tells the frontend to download via /api/assets/aggregate.csv
        # rather than the list export.csv route.
        meta["layout"] = "aggregate"
        meta["query"] = {
            "row_field": row_field,
            "col_field": col_field or None,
            "agg": agg_label,
            "metric_field": metric_field or None,
            "filters": filters or None,
            "logic": logic,
            "show": show,
        }
    return text, artifact
