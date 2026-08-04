# SPDX-License-Identifier: AGPL-3.0-or-later
"""Posture compliance tools for the AssetAtlas MCP server.

Tools:
- list_posture_rules — catalogue of posture rules and their enabled state
- get_posture_compliance — per-asset compliance roll-up across posture
  categories (pass / fail / n/a counts + per-category breakdown). Answers
  "how many assets are unsecured?" / "what's our compliance posture?" —
  aggregate_assets can't reach the posture_rules JSONB paths.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.blueprints.chat.artifacts import build_drilldown
from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import unknown_enum


def register_tools(mcp):
    """Register posture-compliance tools on the FastMCP server."""

    @mcp.tool()
    def list_posture_rules(
        enabled_only: bool = False,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List posture compliance categories and their sub-rules.

        Args:
            enabled_only: When True, only include enabled sub-rules and skip
                categories that end up with zero sub-rules after filtering.
        """
        return list_posture_rules_impl(get_db(ctx), enabled_only=enabled_only)

    @mcp.tool()
    def get_posture_compliance(
        category: Optional[str] = None,
        asset_category: Optional[str] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return per-asset posture state counts across posture categories.

        For each posture category (secured, monitored, domain_joined,
        device_management, vulnerability_scanning), counts assets in each
        state (pass / fail / n/a / null). Optionally narrows to one posture
        category or to assets in a specific asset category.

        Args:
            category: Optional posture category name (e.g. 'secured',
                'monitored', 'domain_joined'). When set, only that category's
                breakdown is returned.
            asset_category: Optional assets.category filter (e.g. 'server',
                'workstation', 'mobile').
        """
        return get_posture_compliance_impl(
            get_db(ctx),
            category=category,
            asset_category=asset_category,
        )


def list_posture_rules_impl(db, *, enabled_only: bool = False) -> str:
    # Single LEFT JOIN avoids an N+1 (one query per category).
    # posture_sub_rules columns are aliased so we can detect NULL (no rules).
    sub_rule_where = "AND sr.enabled = TRUE" if enabled_only else ""
    rows: List[Dict[str, Any]] = db.execute_query(
        f"""
        SELECT c.id            AS cat_id,
               c.name          AS cat_name,
               c.applicable_categories,
               sr.id           AS sr_id,
               sr.name         AS sr_name,
               sr.description  AS sr_description,
               sr.enabled      AS sr_enabled
        FROM posture_categories c
        LEFT JOIN posture_sub_rules sr
               ON sr.category_id = c.id {sub_rule_where}
        ORDER BY c.display_order, c.name, sr.name
        """
    ) or []

    if not rows:
        return "No posture categories defined."

    by_cat: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        cid = row["cat_id"]
        cat = by_cat.setdefault(cid, {
            "name": row["cat_name"],
            "applicable": row.get("applicable_categories"),
            "sub_rules": [],
        })
        if row.get("sr_id") is not None:
            cat["sub_rules"].append({
                "name": row["sr_name"],
                "description": row.get("sr_description"),
                "enabled": row.get("sr_enabled"),
            })

    sections: List[str] = []
    for cat in by_cat.values():
        if enabled_only and not cat["sub_rules"]:
            continue

        applicable = cat["applicable"]
        applies_to = ", ".join(str(x) for x in applicable) if applicable else "all"

        section = [
            f"## {cat['name']}",
            f"- applies to: {applies_to}",
            "",
            "Sub-rules:",
        ]
        if cat["sub_rules"]:
            for sr in cat["sub_rules"]:
                state = "enabled" if sr["enabled"] else "disabled"
                desc = sr["description"] or "no description"
                section.append(f"- {sr['name']} [{state}] — {desc}")
        else:
            section.append("- (none)")
        sections.append("\n".join(section))

    if not sections:
        return "No posture categories defined."

    return "\n\n".join(sections)


# The posture_rules JSONB is shaped:
#   {"<category>": {"state": "pass"|"fail"|"n/a"|null, "output": <str|null>}}
# We COUNT(*) FILTER by JSONB text extraction per-category to get a clean
# states-per-category matrix in one pass.
_KNOWN_POSTURE_CATEGORIES = (
    "secured",
    "monitored",
    "domain_joined",
    "device_management",
    "vulnerability_scanning",
)


def get_posture_compliance_impl(
    db,
    *,
    category: Optional[str] = None,
    asset_category: Optional[str] = None,
) -> str:
    # Pull the live posture category set from the catalog table rather
    # than trust the static tuple above — that's the authoritative list and
    # it keeps us from silently dropping newly-added categories. Match is
    # case-insensitive so users can type "Secured" or "secured".
    cat_rows = db.execute_query(
        "SELECT name FROM posture_categories ORDER BY name"
    ) or []
    categories = [r["name"] for r in cat_rows] or list(_KNOWN_POSTURE_CATEGORIES)
    cat_by_lower = {c.lower(): c for c in categories}

    canonical_category: Optional[str] = None
    if category:
        canonical_category = cat_by_lower.get(category.lower())
        if canonical_category is None:
            return unknown_enum("posture category", category, categories)

    params: Dict[str, Any] = {}
    where = ["merged_into IS NULL"]
    if asset_category:
        # Case-insensitive to match the chat's filter norms.
        where.append("LOWER(category) = LOWER(%(asset_category)s)")
        params["asset_category"] = asset_category
    where_sql = " AND ".join(where)

    target = [canonical_category] if canonical_category else categories

    # posture_rules.<category>.state values as observed in practice:
    #   "true"  → asset passes the rule
    #   "false" → asset fails the rule
    #   "n/a"   → rule not applicable to this asset class
    #   NULL    → not yet evaluated
    # We also accept pass/fail here so downstream renames don't silently
    # under-count.
    _PASS_VALUES = ("true", "pass")
    _FAIL_VALUES = ("false", "fail")
    _NA_VALUES = ("n/a",)

    def _in_list_literal(vals) -> str:
        return "(" + ", ".join(f"'{v}'" for v in vals) + ")"

    pass_in = _in_list_literal(_PASS_VALUES)
    fail_in = _in_list_literal(_FAIL_VALUES)
    na_in = _in_list_literal(_NA_VALUES)

    # One query builds the whole {category -> {state -> count}} matrix.
    # The category strings come from the catalog table (trusted) and are
    # rendered as lowercase JSON keys to match the stored shape.
    select_parts: List[str] = ["COUNT(*) AS total"]
    for c in target:
        # JSONB keys are stored lowercase_snake_case; ensure we look them up
        # that way regardless of what the catalog display string looks like.
        jsonb_key = c.lower().replace(" ", "_").replace("'", "''")
        col_pass = _posture_col_name(c, "pass")
        col_fail = _posture_col_name(c, "fail")
        col_na = _posture_col_name(c, "n/a")
        col_null = _posture_col_name(c, "null")
        select_parts.extend([
            f"COUNT(*) FILTER (WHERE LOWER(posture_rules->'{jsonb_key}'->>'state') IN {pass_in}) AS {col_pass}",
            f"COUNT(*) FILTER (WHERE LOWER(posture_rules->'{jsonb_key}'->>'state') IN {fail_in}) AS {col_fail}",
            f"COUNT(*) FILTER (WHERE LOWER(posture_rules->'{jsonb_key}'->>'state') IN {na_in}) AS {col_na}",
            f"COUNT(*) FILTER (WHERE posture_rules->'{jsonb_key}'->>'state' IS NULL) AS {col_null}",
        ])
    select_sql = ",\n               ".join(select_parts)
    agg = db.execute_query(
        f"SELECT {select_sql} FROM assets WHERE {where_sql}",
        params,
    )
    row = agg[0] if agg else {}
    total = int(row.get("total") or 0)

    if total == 0:
        scope = f" in asset category '{asset_category}'" if asset_category else ""
        return f"COUNT: 0 assets match the scope{scope}."

    scope_bits = []
    if asset_category:
        scope_bits.append(f"asset_category={asset_category}")
    if canonical_category:
        scope_bits.append(f"posture_category={canonical_category}")
    scope_str = f" ({', '.join(scope_bits)})" if scope_bits else ""

    lines = [
        f"COUNT: {total} assets in scope{scope_str}.",
        "",
    ]

    header = f"{'category':25s} {'pass':>7s} {'fail':>7s} {'n/a':>7s} {'null':>7s}"
    lines.append(header)
    lines.append("-" * len(header))
    for c in target:
        p = int(row.get(_posture_col_name(c, "pass")) or 0)
        f_ = int(row.get(_posture_col_name(c, "fail")) or 0)
        na = int(row.get(_posture_col_name(c, "n/a")) or 0)
        nu = int(row.get(_posture_col_name(c, "null")) or 0)
        lines.append(f"{c:25s} {p:>7d} {f_:>7d} {na:>7d} {nu:>7d}")
    lines.append("-" * len(header))
    lines.append(
        "pass = asset meets rule; fail = violates; n/a = rule not applicable "
        "to this asset class; null = not yet evaluated."
    )

    # Totals across categories (only meaningful in all-categories mode)
    if not canonical_category:
        any_fail = db.execute_query(
            f"""
            SELECT COUNT(*) AS n FROM assets WHERE {where_sql}
              AND EXISTS (
                SELECT 1 FROM jsonb_each(posture_rules) kv
                WHERE LOWER(kv.value->>'state') IN {fail_in}
              )
            """,
            params,
        )
        any_fail_n = int(any_fail[0]["n"]) if any_fail else 0
        lines.append("")
        lines.append(
            f"Assets failing at least one posture rule: {any_fail_n} "
            f"({100.0 * any_fail_n / total:.1f}% of {total})"
        )

    return "\n".join(lines)


def _posture_col_name(category: str, state: str) -> str:
    """Build a safe SQL column alias from a posture category + state. Limited
    to [a-z0-9_] so the aggregate SQL above never contains anything beyond
    whitelisted identifiers from the catalog table."""
    safe_cat = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in category.lower())
    safe_state = {"pass": "pass", "fail": "fail", "n/a": "na", "null": "nulls"}.get(state, "x")
    return f"c_{safe_cat}_{safe_state}"


def format_posture_subrule_lines(sr: Dict[str, Any]) -> List[str]:
    """Full single posture sub-rule definition WITH its id, target_filters,
    compliance_criteria, output, display order, applicable asset categories, and
    last-edit provenance — used by the unified ``list_rules`` chat tool.

    ``_category_name`` / ``_applicable`` are the parent posture category's name
    and ``applicable_categories``, attached by the lister when flattening
    categories → sub-rules (the sub-rule row itself doesn't carry them)."""
    name = sr.get("name") or "—"
    lines = [f"## {name}  (id={sr.get('id')})"]
    cat = sr.get("_category_name") or sr.get("category_id")
    lines.append(
        f"- posture category: {cat} | enabled: {sr.get('enabled')} | "
        f"display_order: {sr.get('display_order')}")
    if sr.get("description"):
        lines.append(f"- description: {sr['description']}")
    if sr.get("target_filters"):
        lines.append(f"- target_filters: {json.dumps(sr['target_filters'])}")
    if sr.get("compliance_criteria"):
        lines.append(
            f"- compliance_criteria: {json.dumps(sr['compliance_criteria'])}")
    if sr.get("output_mode"):
        lines.append(
            f"- output_mode: {sr.get('output_mode')} | "
            f"output_value: {json.dumps(sr.get('output_value'))}")
    applicable = sr.get("_applicable")
    if applicable is not None:
        rendered = ", ".join(str(a) for a in applicable) if applicable else "all"
        lines.append(f"- applicable asset categories: {rendered}")
    edit = sr.get("last_edit")
    if edit:
        who = edit.get("changed_by_username") or edit.get("changed_by")
        lines.append(
            f"- last edit: {edit.get('action')} by {who} at {edit.get('at')}")
    return lines


# --- get_compliance_matrix ---------------------------------------------------
# Posture-category × asset-category coverage matrix (+ per-sub-rule breakdown),
# each cell/row carrying a re-runnable drilldown filter (§3.6/§4). Replaces the
# flat get_posture_compliance roll-up.

_GROUP_BY = ("none", "asset_category", "sub_rule")
# posture_rules-><cat>->>'state' value that means the asset PASSES the category
# (matches PostureRepository.get_category_stats' "pass" FILTER).
_PASS_STATE = "true"
# Cap asset-id lists attached when drilldown=True; the re-runnable filter is the
# contract, the ids are a bounded convenience.
_MAX_DRILLDOWN_IDS = 200


def _coverage_pct(covered: int, total: int) -> float:
    return round(100.0 * covered / total, 1) if total else 0.0


def _covered_drilldown(cat_id, asset_cat, query_engine, db, resolve_ids):
    """Drilldown to the COVERED (passing) assets of one (posture, asset) cell:
    an asset-category filter (when scoped) + ``posture_state:<cat_id> = true``.
    The filters round-trip the query grammar so query_assets can re-run them;
    ``resolve_ids`` additionally materializes the asset ids via the canonical
    engine (bounded)."""
    chat: List[Dict[str, Any]] = []
    compiler: List[Dict[str, Any]] = []
    if asset_cat is not None:
        if asset_cat == "Unknown":
            chat.append({"field": "Category", "op": "empty"})
            compiler.append({"field_id": {"kind": "static", "name": "Category"},
                             "operator": "empty", "value": None})
        else:
            chat.append({"field": "Category", "op": "eq", "value": asset_cat})
            compiler.append({"field_id": {"kind": "static", "name": "Category"},
                             "operator": "eq", "value": asset_cat})
    chat.append({"field": f"posture_state:{cat_id}", "op": "eq",
                 "value": _PASS_STATE})
    compiler.append({"field_id": {"kind": "posture", "category": cat_id,
                                  "read": "state"},
                     "operator": "eq", "value": _PASS_STATE})
    dd = build_drilldown(chat, col_value=asset_cat)
    if resolve_ids and query_engine is not None and db is not None:
        _attach_asset_ids(dd, compiler, query_engine, db)
    return dd


def _attach_asset_ids(drilldown, compiler_filters, query_engine, db):
    """Best-effort: resolve a drilldown's matching asset ids via the canonical
    compiler. The re-runnable filter remains the contract if this fails."""
    try:
        ids = sorted(str(i) for i in
                     query_engine.asset_ids_matching(compiler_filters, db, "AND"))
    except Exception:  # noqa: BLE001 — ids are a convenience, never the contract
        return
    drilldown["asset_id_count"] = len(ids)
    drilldown["asset_ids"] = ids[:_MAX_DRILLDOWN_IDS]


def get_compliance_matrix_impl(
    *,
    posture_repository=None,
    posture_category_read_service=None,
    posture_preview_service=None,
    query_engine=None,
    db=None,
    category: Optional[str] = None,
    asset_category: Optional[str] = None,
    group_by: str = "none",
    drilldown: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Posture compliance as a covered/total matrix.

    group_by='none' (default): per posture category covered/fail/n_a/total
    (supersedes get_posture_compliance). group_by='asset_category': the full
    posture × asset category matrix, one cell per (posture, asset). group_by=
    'sub_rule': per posture sub-rule matched/pass/fail computed LIVE via the
    posture preview service. Each cell/row carries a re-runnable drilldown
    filter; ``drilldown=True`` also resolves the matching asset ids.
    """
    if group_by not in _GROUP_BY:
        return unknown_enum("group_by", group_by, _GROUP_BY), None
    if posture_repository is None:
        return "Posture repository not available.", None

    cats = posture_repository.list_categories() or []
    name_by_id = {c["id"]: (c.get("name") or c["id"]) for c in cats}
    id_lookup: Dict[str, Any] = {}
    for c in cats:
        id_lookup[str(c["id"]).lower()] = c["id"]
        if c.get("name"):
            id_lookup[c["name"].lower()] = c["id"]

    cat_id_filter = None
    if category:
        cat_id_filter = id_lookup.get(str(category).lower())
        if cat_id_filter is None:
            valid = sorted(set(name_by_id.values()))
            return unknown_enum("posture category", category, valid), None

    if group_by == "sub_rule":
        return _matrix_sub_rule(
            posture_category_read_service, posture_preview_service,
            cat_id_filter, asset_category, drilldown, query_engine, db)

    stats = posture_repository.get_category_stats() or {}
    ordered_ids = [c["id"] for c in cats] or list(stats.keys())
    target_ids = [cat_id_filter] if cat_id_filter else ordered_ids

    if group_by == "asset_category":
        return _matrix_by_asset_category(
            stats, target_ids, name_by_id, asset_category, drilldown,
            query_engine, db)
    return _matrix_none(
        stats, target_ids, name_by_id, asset_category, drilldown,
        query_engine, db)


def _matrix_none(stats, target_ids, name_by_id, asset_category, drilldown,
                 query_engine, db):
    series: List[Dict[str, Any]] = []
    lines = ["# Posture compliance (by posture category)", ""]

    if asset_category:
        present = any(
            asset_category in (stats.get(cid, {}).get("by_asset_category") or {})
            for cid in target_ids)
        if not present:
            return (f"No evaluated assets in asset category {asset_category!r} "
                    f"for the selected posture categories.", None)
        lines.append(f"(scoped to asset category: {asset_category})")
        header = (f"{'posture category':25s} {'covered':>9s} {'total':>9s} "
                  f"{'coverage':>9s}")
        cols = [{"key": "posture_category", "label": "Posture Category"},
                {"key": "covered", "label": "Covered"},
                {"key": "total", "label": "Total"},
                {"key": "coverage_pct", "label": "Coverage %"}]
        lines += [header, "-" * len(header)]
        for cid in target_ids:
            cell = ((stats.get(cid, {}).get("by_asset_category") or {})
                    .get(asset_category, {"covered": 0, "total": 0}))
            covered = int(cell.get("covered", 0))
            total = int(cell.get("total", 0))
            pct = _coverage_pct(covered, total)
            name = name_by_id.get(cid, cid)
            lines.append(f"{name:25s} {covered:>9d} {total:>9d} {pct:>8.1f}%")
            series.append({
                "posture_category": name, "covered": covered, "total": total,
                "coverage_pct": pct,
                "drilldown": _covered_drilldown(cid, asset_category,
                                                query_engine, db, drilldown)})
    else:
        header = (f"{'posture category':25s} {'covered':>9s} {'fail':>7s} "
                  f"{'n/a':>7s} {'total':>9s} {'coverage':>9s}")
        cols = [{"key": "posture_category", "label": "Posture Category"},
                {"key": "covered", "label": "Covered"},
                {"key": "fail", "label": "Fail"},
                {"key": "n_a", "label": "N/A"},
                {"key": "total", "label": "Total"},
                {"key": "coverage_pct", "label": "Coverage %"}]
        lines += [header, "-" * len(header)]
        for cid in target_ids:
            e = stats.get(cid) or {}
            covered = int(e.get("pass", 0))
            fail = int(e.get("fail", 0))
            na = int(e.get("n_a", 0))
            total = covered + fail + na
            pct = _coverage_pct(covered, total)
            name = name_by_id.get(cid, cid)
            lines.append(f"{name:25s} {covered:>9d} {fail:>7d} {na:>7d} "
                         f"{total:>9d} {pct:>8.1f}%")
            series.append({
                "posture_category": name, "covered": covered, "fail": fail,
                "n_a": na, "total": total, "coverage_pct": pct,
                "drilldown": _covered_drilldown(cid, None, query_engine, db,
                                                drilldown)})

    lines += ["", "covered = assets passing the posture category; "
              "total = evaluated assets (excludes not-yet-evaluated)."]
    artifact = {
        "type": "table", "title": "Posture compliance by category",
        "columns": cols, "series": series,
        "meta": {"source_tool": "get_compliance_matrix", "group_by": "none",
                 "asset_category": asset_category}}
    return "\n".join(lines), artifact


def _matrix_by_asset_category(stats, target_ids, name_by_id, asset_category,
                              drilldown, query_engine, db):
    asset_cats: List[str] = []
    for cid in target_ids:
        for ac in (stats.get(cid, {}).get("by_asset_category") or {}):
            if ac not in asset_cats:
                asset_cats.append(ac)
    asset_cats.sort()
    if asset_category:
        if asset_category not in asset_cats:
            return (f"No evaluated assets in asset category {asset_category!r} "
                    f"for the selected posture categories.", None)
        asset_cats = [asset_category]
    if not asset_cats:
        return "No posture coverage data available.", None

    cols = [{"key": "posture_category", "label": "Posture Category"},
            {"key": "asset_category", "label": "Asset Category"},
            {"key": "covered", "label": "Covered"},
            {"key": "total", "label": "Total"},
            {"key": "coverage_pct", "label": "Coverage %"}]
    series: List[Dict[str, Any]] = []
    lines = ["# Posture compliance matrix (posture category × asset category)",
             "", "Each cell = covered/total (covered = assets passing that "
             "posture category).", ""]
    grid_header = (f"{'posture/asset':22s} "
                   + " ".join(f"{ac[:12]:>14s}" for ac in asset_cats))
    lines += [grid_header, "-" * len(grid_header)]
    for cid in target_ids:
        name = name_by_id.get(cid, cid)
        by = stats.get(cid, {}).get("by_asset_category") or {}
        cells_txt = []
        for ac in asset_cats:
            cell = by.get(ac, {"covered": 0, "total": 0})
            covered = int(cell.get("covered", 0))
            total = int(cell.get("total", 0))
            cells_txt.append(f"{covered}/{total}".rjust(14))
            series.append({
                "posture_category": name, "asset_category": ac,
                "covered": covered, "total": total,
                "coverage_pct": _coverage_pct(covered, total),
                "drilldown": _covered_drilldown(cid, ac, query_engine, db,
                                                drilldown)})
        lines.append(f"{name[:22]:22s} " + " ".join(cells_txt))

    artifact = {
        "type": "table", "title": "Posture compliance matrix",
        "columns": cols, "series": series,
        "meta": {"source_tool": "get_compliance_matrix",
                 "group_by": "asset_category", "asset_category": asset_category}}
    return "\n".join(lines), artifact


def _matrix_sub_rule(read_svc, preview_svc, cat_id_filter, asset_category,
                     drilldown, query_engine, db):
    if read_svc is None or preview_svc is None:
        return "Posture sub-rule preview services not available.", None
    categories = (read_svc.list_categories() or {}).get("categories") or []

    scope_filter = None
    if asset_category:
        if asset_category == "Unknown":
            scope_filter = {"field_id": {"kind": "static", "name": "Category"},
                            "operator": "empty", "value": None}
        else:
            scope_filter = {"field_id": {"kind": "static", "name": "Category"},
                            "operator": "eq", "value": asset_category}

    cols = [{"key": "posture_category", "label": "Posture Category"},
            {"key": "sub_rule", "label": "Sub-rule"},
            {"key": "matched", "label": "Matched"},
            {"key": "pass", "label": "Pass"},
            {"key": "fail", "label": "Fail"}]
    series: List[Dict[str, Any]] = []
    lines = ["# Posture compliance by sub-rule", ""]
    if asset_category:
        lines.append(f"(scoped to asset category: {asset_category})")
    header = (f"{'posture category':20s} {'sub-rule':28s} {'matched':>8s} "
              f"{'pass':>6s} {'fail':>6s}")
    lines += [header, "-" * len(header)]

    for cat in categories:
        cid = cat.get("id")
        if cat_id_filter and cid != cat_id_filter:
            continue
        cname = cat.get("name") or cid
        for sr in (cat.get("sub_rules") or []):
            tf = list(sr.get("target_filters") or [])
            if scope_filter is not None:
                tf = [scope_filter] + tf
            cc = sr.get("compliance_criteria") or {"logic": "all",
                                                   "conditions": []}
            res = preview_svc.preview(tf, cc)
            matched = res.get("matched", 0)
            passed = res.get("pass", 0)
            failed = res.get("fail", 0)
            srname = sr.get("name") or "—"
            lines.append(f"{str(cname)[:20]:20s} {srname[:28]:28s} "
                         f"{matched:>8d} {passed:>6d} {failed:>6d}")
            dd_filters = [{"field_id": c.get("field_id"), "op": c.get("operator"),
                           "value": c.get("value")} for c in tf]
            dd = build_drilldown(dd_filters)
            if drilldown:
                _attach_asset_ids(dd, tf, query_engine, db)
            series.append({"posture_category": cname, "sub_rule": srname,
                           "matched": matched, "pass": passed, "fail": failed,
                           "drilldown": dd})

    if not series:
        return "No posture sub-rules match the selected scope.", None
    artifact = {
        "type": "table", "title": "Posture compliance by sub-rule",
        "columns": cols, "series": series,
        "meta": {"source_tool": "get_compliance_matrix", "group_by": "sub_rule",
                 "asset_category": asset_category}}
    return "\n".join(lines), artifact
