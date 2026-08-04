# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source-coverage tools for the AssetAtlas MCP server.

Tools:
- get_source_coverage: overlap + set-difference analysis across data
  sources. Answers "which assets are in A but not B?" / "how many assets
  come from only one source?" — impossible via existing tools, which
  can test array membership but not non-membership against a specific
  source.
- get_asset_source_breakdown: per-source raw view of one asset. Answers
  "what does each source know about this machine?" / "where does the
  OS value come from?" — get_asset returns only the merged view.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import (
    CHAT_TEXT_ROW_CAP,
    format_array,
    overflow_note,
    validate_uuid,
)

# A chat tool may return plain text (MCP path) or (text, artifact) (chat path).
ToolReturn = Tuple[str, Optional[Dict[str, Any]]]


def _coverage_table(
    title: str, columns: List[Tuple[str, str]],
    series: List[Dict[str, Any]], meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble a drilldown-carrying ``table`` artifact envelope. ``columns`` is
    a ``[(key, label)]`` list; each ``series`` entry already carries its own
    ``drilldown`` (§3.6)."""
    meta = {"source_tool": "get_source_coverage", "layout": "list", **meta}
    return {
        "type": "table",
        "title": title,
        "columns": [{"key": k, "label": label} for k, label in columns],
        "series": series,
        "meta": meta,
    }


def register_tools(mcp):
    """Register source-coverage tools on the FastMCP server."""

    @mcp.tool()
    def get_source_coverage(
        source_a: Optional[str] = None,
        source_b: Optional[str] = None,
        by_category: bool = False,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Analyse coverage across data sources.

        Without arguments: returns the single-vs-multi-source distribution
        (how many assets are seen by exactly one source vs two vs three…).

        With source_a alone: counts assets in that source.

        With both source_a and source_b: returns the three set sizes —
        A only (in A, not in B), B only, and A∩B (both).

        With by_category=True: per asset-category, the number of distinct
        ingesting sources and the multi-source asset count.

        A source name that matches zero assets is flagged loudly (likely a
        typo) rather than silently treated as an empty set.

        Args:
            source_a: Exact source name as it appears in assets.data_source[]
                (e.g. 'Intune', 'Microsoft Defender API', 'SentinelOne').
                Case-insensitive match.
            source_b: Optional second source for set differences.
            by_category: Per-category distinct-source coverage view.
        """
        return get_source_coverage_impl(
            get_db(ctx),
            source_a=source_a,
            source_b=source_b,
            by_category=by_category,
        )

    @mcp.tool()
    def get_asset_source_breakdown(
        asset_id: str,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Show what each ingesting source reports for a single asset.

        Answers "why do we think this laptop's owner is X?" / "what does
        each source say about this host?" — get_asset returns the merged
        view with arrays flattened; this one keeps per-source values
        side-by-side so disagreements stand out.

        Args:
            asset_id: Asset UUID.
        """
        return get_asset_source_breakdown_impl(get_db(ctx), asset_id=asset_id)


def _zero_source_warning(name: str) -> str:
    """Loud, single-source warning that a source name matched nothing — almost
    always a typo or wrong casing (§4: never silently treat it as an empty
    set, which would look like a real 'no overlap' answer)."""
    return (
        f"⚠ source {name!r} matched 0 assets (likely a typo or wrong casing). "
        "Call get_field_values on 'Data Source' to see the exact source-name "
        "strings recognised today."
    )


def get_source_coverage_impl(
    db,
    *,
    source_a: Optional[str] = None,
    source_b: Optional[str] = None,
    by_category: bool = False,
    with_artifact: bool = False,
) -> Union[str, ToolReturn]:
    def _ret(text: str, artifact: Optional[Dict[str, Any]] = None):
        return (text, artifact) if with_artifact else text

    build_drilldown = None
    if with_artifact:
        from src.api.blueprints.chat.artifacts import (  # lazy: keep the MCP
            build_drilldown,                              # import path Flask-free
        )

    # --- by_category: per-category distinct-source coverage ---
    # Checked before the source_a/source_b guards: it's a standalone mode that
    # ignores both, so a stray source_b must not block it.
    if by_category:
        rows = db.execute_query(
            """
            SELECT a.category,
                   COUNT(DISTINCT a.id) AS assets,
                   COUNT(DISTINCT s) AS distinct_sources,
                   COUNT(DISTINCT a.id) FILTER (
                       WHERE COALESCE(array_length(a.data_source, 1), 0) >= 2
                   ) AS multi_source
            FROM assets a
            LEFT JOIN LATERAL unnest(a.data_source) s ON TRUE
            WHERE a.merged_into IS NULL
            GROUP BY a.category
            ORDER BY distinct_sources DESC, assets DESC
            """
        ) or []
        if not rows:
            return _ret("No assets found.")
        lines = [
            "Per-category source coverage (distinct ingesting sources):",
            f"  {'category':24s} {'assets':>8s} {'sources':>8s} "
            f"{'multi-src':>10s}",
        ]
        series: List[Dict[str, Any]] = []
        # Text rows are capped so a deployment with many asset categories can't
        # bloat the context; the artifact below still carries every category.
        for idx, r in enumerate(rows):
            cat = r.get("category")
            cat_disp = cat or "(none)"
            assets = int(r.get("assets") or 0)
            dsrc = int(r.get("distinct_sources") or 0)
            multi = int(r.get("multi_source") or 0)
            if idx < CHAT_TEXT_ROW_CAP:
                lines.append(
                    f"  {str(cat_disp)[:24]:24s} {assets:>8d} {dsrc:>8d} "
                    f"{multi:>10d}"
                )
            entry: Dict[str, Any] = {
                "category": cat_disp, "assets": assets,
                "distinct_sources": dsrc, "multi_source": multi,
            }
            if with_artifact and cat is not None:
                entry["drilldown"] = build_drilldown(
                    [{"field": "Category", "op": "eq", "value": cat}])
            series.append(entry)
        note = overflow_note(
            len(rows), min(len(rows), CHAT_TEXT_ROW_CAP),
            noun="categories", hint="see the full table artifact")
        if note:
            lines.append(note)
        if not with_artifact:
            return "\n".join(lines)
        return "\n".join(lines), _coverage_table(
            "Source coverage by category",
            [("category", "Category"), ("assets", "Assets"),
             ("distinct_sources", "Distinct Sources"),
             ("multi_source", "Multi-source")],
            series, {"mode": "by_category"})

    if source_b and not source_a:
        return _ret("source_b requires source_a.")

    # No args → source-count distribution.
    if not source_a and not source_b:
        rows = db.execute_query(
            """
            SELECT COALESCE(array_length(data_source, 1), 0) AS n_sources,
                   COUNT(*) AS n_assets
            FROM assets
            WHERE merged_into IS NULL
            GROUP BY n_sources
            ORDER BY n_sources
            """
        ) or []
        total = sum(int(r["n_assets"]) for r in rows)
        if total == 0:
            return _ret("No assets found.")
        single = sum(int(r["n_assets"]) for r in rows if int(r["n_sources"]) == 1)
        multi = sum(int(r["n_assets"]) for r in rows if int(r["n_sources"]) >= 2)
        lines = [
            f"COUNT: {total} assets total.",
            f"  single-source: {single} ({100.0 * single / total:.1f}%)",
            f"  multi-source:  {multi} ({100.0 * multi / total:.1f}%)",
            "",
            "Distribution by number of ingesting sources:",
        ]
        for r in rows:
            n = int(r["n_sources"])
            cnt = int(r["n_assets"])
            pct = 100.0 * cnt / total if total else 0.0
            lines.append(f"  {n} source{'s' if n != 1 else ' '}: {cnt:>6d} ({pct:5.1f}%)")
        if not with_artifact:
            return "\n".join(lines)
        # Per-row drilldown on the Data Source Count virtual field so "show me
        # the single-source assets" re-runs as a query_assets call.
        series = [
            {"label": "single-source", "count": single,
             "drilldown": build_drilldown(
                 [{"field": "Data Source Count", "op": "eq", "value": 1}])},
            {"label": "multi-source", "count": multi,
             "drilldown": build_drilldown(
                 [{"field": "Data Source Count", "op": "gte", "value": 2}])},
        ]
        for r in rows:
            n = int(r["n_sources"])
            series.append({
                "label": f"{n} source(s)", "count": int(r["n_assets"]),
                "drilldown": build_drilldown(
                    [{"field": "Data Source Count", "op": "eq", "value": n}]),
            })
        return "\n".join(lines), _coverage_table(
            "Source-count distribution",
            [("label", "Bucket"), ("count", "Assets")],
            series, {"mode": "distribution", "total": total})

    # Single-source count.
    if source_a and not source_b:
        rows = db.execute_query(
            """
            SELECT COUNT(*) AS n FROM assets
            WHERE merged_into IS NULL
              AND EXISTS (
                SELECT 1 FROM unnest(data_source) s
                WHERE LOWER(s) = LOWER(%(a)s)
              )
            """,
            {"a": source_a},
        )
        n = int(rows[0]["n"]) if rows else 0
        # Warn loudly on a zero match — don't let a typo read as "0 assets".
        if n == 0:
            return _ret(_zero_source_warning(source_a))
        text = (
            f"COUNT: {n} assets currently ingested by source '{source_a}'. "
            f"(Use source_b to get an A-vs-B set breakdown.)"
        )
        if not with_artifact:
            return text
        series = [{
            "label": source_a, "count": n,
            "drilldown": build_drilldown(
                [{"field": "Data Source", "op": "any", "value": [source_a]}]),
        }]
        return text, _coverage_table(
            f"Assets in source '{source_a}'",
            [("label", "Source"), ("count", "Assets")],
            series, {"mode": "single_source", "source_a": source_a})

    # Two-source set-difference analysis.
    rows = db.execute_query(
        """
        WITH scope AS (
            SELECT id,
                   EXISTS (SELECT 1 FROM unnest(data_source) s
                           WHERE LOWER(s) = LOWER(%(a)s)) AS in_a,
                   EXISTS (SELECT 1 FROM unnest(data_source) s
                           WHERE LOWER(s) = LOWER(%(b)s)) AS in_b
            FROM assets
            WHERE merged_into IS NULL
        )
        SELECT
            COUNT(*) FILTER (WHERE in_a AND NOT in_b) AS a_only,
            COUNT(*) FILTER (WHERE in_b AND NOT in_a) AS b_only,
            COUNT(*) FILTER (WHERE in_a AND in_b) AS both,
            COUNT(*) FILTER (WHERE in_a) AS a_total,
            COUNT(*) FILTER (WHERE in_b) AS b_total,
            COUNT(*) AS all_assets
        FROM scope
        """,
        {"a": source_a, "b": source_b},
    )
    r = rows[0] if rows else {}
    a_only = int(r.get("a_only") or 0)
    b_only = int(r.get("b_only") or 0)
    both = int(r.get("both") or 0)
    a_total = int(r.get("a_total") or 0)
    b_total = int(r.get("b_total") or 0)
    all_assets = int(r.get("all_assets") or 0)

    if a_total == 0 and b_total == 0:
        return _ret(
            f"Neither '{source_a}' nor '{source_b}' matched any assets. "
            f"Call get_field_values on 'Data Source' to see the exact "
            f"source-name strings recognised today."
        )

    # Warn when EITHER source matched zero (was: only when BOTH did — the
    # confirmed bug). One empty side makes the set breakdown degenerate
    # (a typo silently looked like a real 'no overlap' answer — §4).
    warnings: List[str] = []
    if a_total == 0:
        warnings.append(_zero_source_warning(source_a))
    if b_total == 0:
        warnings.append(_zero_source_warning(source_b))

    lines = list(warnings)
    if warnings:
        lines.append("")
    lines += [
        f"COUNT: {all_assets} total assets in fleet.",
        f"  in '{source_a}' (total):        {a_total:>6d}",
        f"  in '{source_b}' (total):        {b_total:>6d}",
        "",
        "Set breakdown:",
        f"  in A only (not in B): {a_only:>6d}",
        f"  in B only (not in A): {b_only:>6d}",
        f"  in both A and B:      {both:>6d}",
    ]
    if a_total:
        lines.append(
            f"  coverage of A by B: {100.0 * both / a_total:.1f}% "
            f"({both}/{a_total} of A's assets are also in B)"
        )
    if b_total:
        lines.append(
            f"  coverage of B by A: {100.0 * both / b_total:.1f}% "
            f"({both}/{b_total} of B's assets are also in A)"
        )
    text = "\n".join(lines)
    if not with_artifact:
        return text

    # Each set carries a re-runnable Data Source any/none filter (§3.6).
    any_a = {"field": "Data Source", "op": "any", "value": [source_a]}
    any_b = {"field": "Data Source", "op": "any", "value": [source_b]}
    none_a = {"field": "Data Source", "op": "none", "value": [source_a]}
    none_b = {"field": "Data Source", "op": "none", "value": [source_b]}
    series = [
        {"label": f"{source_a} only", "count": a_only,
         "drilldown": build_drilldown([any_a, none_b])},
        {"label": f"{source_b} only", "count": b_only,
         "drilldown": build_drilldown([any_b, none_a])},
        {"label": "in both", "count": both,
         "drilldown": build_drilldown([any_a, any_b])},
    ]
    return text, _coverage_table(
        f"'{source_a}' vs '{source_b}' coverage",
        [("label", "Set"), ("count", "Assets")],
        series, {"mode": "two_source", "source_a": source_a,
                 "source_b": source_b, "a_total": a_total,
                 "b_total": b_total})


def get_asset_source_breakdown_impl(db, *, asset_id: str) -> str:
    err = validate_uuid(asset_id, "asset")
    if err:
        return err

    # First line: does the asset exist at all, and what's its merged view?
    asset = db.execute_query(
        """
        SELECT id, hostname, category, subcategory, os_normalized,
               manufacturer, model, owner
        FROM assets WHERE id = %(id)s::uuid
        """,
        {"id": asset_id},
    )
    if not asset:
        return f"Asset {asset_id} not found."
    a = asset[0]

    providers = db.execute_query(
        """
        SELECT data_source, hostname, fqdn, ip, mac, sn, os, os_normalized,
               manufacturer, model, category, subcategory, updated_at
        FROM asset_providers
        WHERE asset_id = %(id)s::uuid
        ORDER BY data_source
        """,
        {"id": asset_id},
    ) or []

    # Per-attribute agreement matrix: which source reports each value?
    # Any row with 2+ distinct non-null values → disagreement flag.
    attrs = [
        "hostname", "fqdn", "ip", "mac", "sn", "os_normalized",
        "manufacturer", "model", "category", "subcategory",
    ]

    lines = [
        f"# Asset {asset_id}",
        f"Merged view:",
        f"  hostname:      {format_array(a.get('hostname'), empty='(none)')}",
        f"  category:      {a.get('category')} / {a.get('subcategory')}",
        f"  os_normalized: {a.get('os_normalized')}",
        f"  manufacturer:  {format_array(a.get('manufacturer'), empty='(none)')}",
        f"  model:         {format_array(a.get('model'), empty='(none)')}",
        f"  owner:         {format_array(a.get('owner'), empty='(none)')}",
        "",
    ]
    if not providers:
        lines.append("No per-source rows found in asset_providers.")
        return "\n".join(lines)

    lines.append(f"## Per-source view ({len(providers)} sources)")
    for p in providers:
        lines.append("")
        lines.append(f"### {p['data_source']}")
        lines.append(f"  updated_at:    {p.get('updated_at')}")
        for attr in attrs:
            val = p.get(attr)
            if val is None or val == "":
                continue
            lines.append(f"  {attr+':':15s}{val}")

    # Disagreement summary — only include attributes with >1 distinct non-null
    # value across sources so the reader can spot lineage conflicts at a
    # glance without scanning every attribute.
    disagreements: List[str] = []
    for attr in attrs:
        values = {}
        for p in providers:
            v = p.get(attr)
            if v in (None, ""):
                continue
            # inet / macaddr come back as objects; coerce to str for comparison.
            key = str(v).strip().lower()
            values.setdefault(key, []).append(p["data_source"])
        if len(values) > 1:
            parts = []
            for k, srcs in values.items():
                parts.append(f"'{k}' ({', '.join(srcs)})")
            disagreements.append(f"  {attr}: " + " vs ".join(parts))

    if disagreements:
        lines.append("")
        lines.append("## Conflicting values across sources")
        lines.extend(disagreements)
    else:
        lines.append("")
        lines.append("All sources agree on every attribute they report.")

    return "\n".join(lines)
