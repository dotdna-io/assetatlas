# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core asset tools for the AssetAtlas MCP server.

Tools: search_assets, get_asset, list_assets, aggregate_assets,
get_field_values
"""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db, get_resolver
from src.mcp.tools._common import (
    format_array as _format_array,
    parse_filters_arg,
    validate_uuid,
)
from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields.models import InvalidFieldError
from src.modules.fields.operators import OperatorResolutionError

# Field-grammar helpers live in _query.py (lifted out of this module so the chat
# primitives share the SAME resolution path). Imported here for internal use and
# RE-EXPORTED for back-compat: the MCP server, artifacts.py, and existing tests
# still import these names from src.mcp.tools.assets.
from src.mcp.tools._query import (
    FieldResolutionError,
    _UNKNOWN_FIELD_HINT,
    _assert_field_resolves,
    _normalize_field_list,
    _normalize_field_name,
    _normalize_filters,
    _project_list_row_to_snake,
)
from src.mcp.tools._query import (  # noqa: F401  (re-exported, not used here)
    _ENCODED_TO_SNAKE,
    _STATIC_FIELD_ALIASES,
    _VIRTUAL_FIELD_ALIASES,
    canonical_field_display_name,
)

logger = logging.getLogger(__name__)


# Fixed key-field set for the text row when no explicit columns are given
# (MCP stdio + search paths). Identity + assignment first (what the user most
# often asks about), then hardware / confidence, so a one-line scan tells the
# model whether it already has what the user wanted.
_DEFAULT_ROW_FIELDS: List[Tuple[str, str]] = [
    ("owner", "Owner"),
    ("location", "Location"),
    ("os_normalized", "OS"),
    ("ip", "IP"),
    ("mac", "MAC"),
    ("sn", "Serial"),
    ("manufacturer", "Manufacturer"),
    ("model", "Model"),
    ("confidence", "Confidence"),
]


# Posture matrix — mirrors the production UI (InfoCards.tsx POSTURE_CHECKS +
# categoryMap). The UI derives posture entirely from each provider's
# source_category / source_type / source_name (substring keyword match); there
# is NO stored posture state for an asset, so we replicate the same derivation
# here for parity. Keyed by lowercased asset category; each entry is
# (display label, keyword group). Categories with no entry get no checks.
_POSTURE_CHECKS: Dict[str, List[Tuple[str, str]]] = {
    "workstation": [
        ("Endpoint Security", "edr"),
        ("Domain Joined", "domain"),
        ("Device Management", "management"),
        ("Vulnerability Scanning", "vulnerability"),
        ("Monitoring", "monitoring"),
    ],
    "server": [
        ("Endpoint Security", "edr"),
        ("Domain Joined", "domain"),
        ("Vulnerability Scanning", "vulnerability"),
        ("Monitoring", "monitoring"),
    ],
    "network": [
        ("Vulnerability Scanning", "vulnerability"),
        ("Monitoring", "monitoring"),
    ],
    "mobile": [
        ("Device Management", "management"),
    ],
}

_POSTURE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "edr": ("edr", "endpoint security", "endpoint protection"),
    "vulnerability": ("vulnerability", "vuln", "scanner"),
    "domain": ("directory", "ad", "ldap", "active directory"),
    "management": ("mdm", "device management", "uem"),
    "monitoring": ("monitoring", "siem", "observability"),
}

# Per-provider field grouping — mirrors AssetService._organize_provider_fields
# so the chat detail buckets fields the same way the UI's DataSources panel does.
_PROVIDER_GENERAL_MAP: List[Tuple[str, str]] = [
    ("hostname", "Hostname"), ("os", "OS"), ("model", "Model"),
    ("sn", "Serial Number"), ("manufacturer", "Manufacturer"),
]
_PROVIDER_NETWORK_MAP: List[Tuple[str, str]] = [
    ("ip", "IP Address"), ("mac", "MAC Address"), ("fqdn", "FQDN"),
]
# Attributes compared across sources for the conflict roll-up (the per-source
# columns get_asset_providers returns). Folds get_asset_source_breakdown.
_PROVIDER_CONFLICT_ATTRS: List[str] = [
    "hostname", "fqdn", "ip", "mac", "sn", "os", "manufacturer", "model",
]


def _render_row_value(row_key: str, val: Any) -> Optional[str]:
    """Format one asset field value for a text row, or ``None`` to skip it.
    confidence -> percent; arrays via _format_array (skipped when empty);
    everything else stringified. Shared by the fixed and column-driven paths so
    they can't drift."""
    if val is None:
        return None
    if row_key == "confidence":
        # confidence is NUMERIC(3,2) -> psycopg2 returns a Decimal, which is
        # neither int nor float; include it (and bool would never reach here) so
        # the score renders as a percent ("85%") not the raw "0.85".
        if isinstance(val, (int, float, Decimal)):
            return f"{val:.0%}"
        return f"{val}"
    if isinstance(val, list):
        return _format_array(val) or None
    return f"{val}"


def _format_asset_row(
    asset: Dict[str, Any],
    columns: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Format a single asset as a concise text block.

    When ``columns`` (a resolved ``[(row_key, display_label)]`` list) is given,
    render exactly those fields with their catalog labels — so the text mirrors
    the selected table columns — skipping hostname/category which already head
    the row. Without it, fall back to the fixed key-field set (unchanged)."""
    lines = []
    aid = str(asset.get("id", ""))
    hostname = _format_array(asset.get("hostname"))
    category = asset.get("category") or ""
    lines.append(f"[{aid}] {hostname or '(no hostname)'} — {category}")

    row_fields = columns if columns else _DEFAULT_ROW_FIELDS
    for row_key, label in row_fields:
        if columns and row_key in ("hostname", "category"):
            continue  # already shown in the header line
        if row_key not in asset:
            continue
        rendered = _render_row_value(row_key, asset.get(row_key))
        if rendered is not None:
            lines.append(f"  {label}: {rendered}")

    return "\n".join(lines)


def register_tools(mcp):
    """Register all asset tools on the FastMCP server."""

    @mcp.tool()
    def search_assets(
        query: str,
        limit: int = 20,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Search assets by hostname, IP, MAC address, or serial number.

        Performs fuzzy matching on hostnames and serial numbers, exact matching
        on IP and MAC addresses. Results are ranked by match quality.

        Args:
            query: Search term (hostname, IP, MAC, or serial number)
            limit: Maximum results to return (1-100, default 20)
        """
        return search_assets_impl(get_db(ctx), get_resolver(ctx),
                                  query=query, limit=limit)

    @mcp.tool()
    def get_asset(
        asset_id: str,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get full details for a single asset by its UUID.

        Returns all fields including data sources, tags, confidence breakdown,
        and extended attributes from each provider.

        Args:
            asset_id: Asset UUID (e.g. '550e8400-e29b-41d4-a716-446655440000')
        """
        return get_asset_impl(get_db(ctx), get_resolver(ctx),
                              asset_id=asset_id)

    @mcp.tool()
    def list_assets(
        filters: Optional[Union[str, list]] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        sort_by: Optional[str] = None,
        sort_dir: str = "desc",
        fields: Optional[str] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List assets with filtering, pagination, and sorting.

        Args:
            filters: JSON array of filter objects, each with 'field', 'op',
                     and 'value'. Operators (canonical codes): eq, neq,
                     contains, ncontains, starts, ends, gt, gte, lt, lte, any,
                     all, none, between, within_last, empty, notempty.
                     Array fields (data_source, owner, hostname, ip, ...)
                     support the set ops (pass the code, not the prose):
                     any (matches at least one of [list]),
                     all (contains every value in [list]),
                     none (contains no value in [list]).
                     Scalar number/date fields support gt/gte/lt/lte,
                     between (value [lo, hi], inclusive), and within_last
                     (date only; value a relative duration like "30d"/"24h",
                     units s/m/h/d/w). Unknown operators are rejected.
                     Example: '[{"field":"category","op":"eq",
                     "value":"Server"}]'
                     Count fields support numeric ops, e.g.
                     '[{"field":"data_source_count","op":"gte","value":2}]'
                     to find multi-source assets.
            search: Free-text search across hostname, IP, MAC, serial, and
                    other fields
            page: Page number (1-indexed, default 1)
            limit: Results per page (1-200, default 50)
            sort_by: Field to sort by. Examples: 'hostname', 'category',
                     'confidence', 'last_seen', 'data_source_count' (sort
                     by how many sources see each asset), 'asset_label'.
            sort_dir: Sort direction ('asc' or 'desc', default 'desc')
            fields: Comma-separated list of fields to include
                    (e.g. 'hostname,ip,category,confidence'). Virtual fields
                    available: data_source_count, hostname_count, ip_count,
                    fqdn_count, user_count, owner_count, asset_label
                    (primary display identifier).
        """
        return list_assets_impl(
            get_db(ctx),
            get_resolver(ctx),
            filters=filters,
            search=search,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_dir=sort_dir,
            fields=fields,
        )

    @mcp.tool()
    def aggregate_assets(
        row_field: str,
        col_field: Optional[str] = None,
        filters: Optional[Union[str, list]] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Group assets and count by one or two fields (pivot table).

        Useful for breakdowns like category counts, OS distribution per
        category, or data source coverage analysis.

        Args:
            row_field: Field to group rows by. Examples: 'category',
                       'os_normalized', 'data_source', 'data_source_count'
                       (e.g. how many assets are seen by 1, 2, 3+ sources),
                       'hostname_count' (single- vs multi-hostname assets).
            col_field: Optional second field for 2D pivot. Examples:
                       'os_eol_status', 'data_source_count' for a
                       category × source-coverage matrix.
            filters: Optional JSON array of filter objects (same format as
                     list_assets)
        """
        return aggregate_assets_impl(
            get_db(ctx), get_resolver(ctx),
            row_field=row_field, col_field=col_field, filters=filters,
        )

    @mcp.tool()
    def get_field_values(
        field: str,
        search: Optional[str] = None,
        limit: int = 50,
        filters: Optional[Union[str, list]] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get distinct values for a field with occurrence counts.

        Useful for discovering what values exist (categories, OS versions,
        data sources) and understanding their distribution.

        Args:
            field: Field name. Examples: 'category', 'os_normalized',
                   'data_source', 'manufacturer'. Count virtuals work too:
                   'data_source_count', 'hostname_count', etc., return
                   the distribution of cardinalities (e.g. how many
                   assets have 1 source vs 2 vs 3+).
            search: Optional prefix filter to narrow results
            limit: Maximum values to return (1-200, default 50)
            filters: Optional JSON array of context filters (same format as
                     list_assets)
        """
        return get_field_values_impl(
            get_db(ctx), get_resolver(ctx),
            field=field, search=search, limit=limit, filters=filters,
        )


def search_assets_query(
    db, resolver, *, query: str, limit: int = 20
) -> List[dict]:
    """Clamp ``limit``, run the repo search, and return the raw match list.

    Returns an empty list when nothing matches.  The ``match_field``
    annotation added by the repo is preserved in each dict as-is.
    The chat binding uses this split to build a table artifact from the
    raw list while ``search_assets_impl`` builds the LLM summary.
    """
    limit = max(1, min(limit, 100))
    repo = AssetRepository(db, resolver)
    return repo.search_assets(query, limit=limit)


def format_search_results(results: List[dict], *, query: str) -> str:
    """Format raw search results as the LLM-facing text string.

    Pure function — no DB access.  Extracted so the chat binding can call
    ``search_assets_query`` once and pass the result to BOTH this formatter
    and the artifact builder without a second repo hit.
    """
    if not results:
        return f"No assets found matching '{query}'."

    lines = [f"Found {len(results)} asset(s) matching '{query}':\n"]
    for asset in results:
        match_field = asset.get("match_field", "")
        row_text = _format_asset_row(asset)
        lines.append(f"{row_text}  Matched on: {match_field}\n")

    return "\n".join(lines)


def search_assets_impl(db, resolver, *, query: str, limit: int = 20) -> str:
    results = search_assets_query(db, resolver, query=query, limit=limit)
    return format_search_results(results, query=query)


def get_asset_query(db, resolver, *, asset_id: str) -> Optional[dict]:
    """Validate the UUID and fetch the raw asset dict from the repo.

    Returns ``None`` in two cases:
    - ``asset_id`` fails UUID validation (no repo call is made).
    - The asset is not found in the database.

    The chat binding uses this split to build a detail-table artifact from
    the raw dict while ``get_asset_impl`` builds the full LLM summary.
    """
    err = validate_uuid(asset_id, "asset")
    if err:
        return None
    repo = AssetRepository(db, resolver)
    return repo.get_asset_by_id(asset_id)


def _first_or_join(vals: Any) -> Optional[str]:
    """Render a geo list (e.g. ``geo_countries``) as a comma-joined string."""
    if not vals:
        return None
    if isinstance(vals, list):
        parts = [str(v) for v in vals if v]
        return ", ".join(parts) if parts else None
    return str(vals)


def _ip_split_and_geo(
    asset: Dict[str, Any],
) -> Tuple[List[Any], List[Any], Dict[str, Optional[str]]]:
    """Split private/public IPs + extract geo, reading the SAME keys the UI does
    (extended_attributes.AssetAtlas.ip_private/ip_public/geo_*; private falls
    back to the merged ``ip`` array — InfoCards.tsx NetworkCard)."""
    ext = asset.get("extended_attributes")
    aa = ext.get("AssetAtlas") if isinstance(ext, dict) else None
    aa = aa if isinstance(aa, dict) else {}

    # Mirror the UI fallback (InfoCards: `aa?.ip_private || asset.ip`): use the
    # enrichment's private list whenever the key is present — even an EMPTY list
    # (asset has only public IPs) — and fall back to the merged `ip` array only
    # when AssetAtlas reported no ip_private at all. Python's empty list is
    # falsy (unlike JS), so a plain `or` here would mislabel public IPs as
    # private for an asset whose ip_private is explicitly [].
    ipp = aa.get("ip_private")
    ip_private = ipp if ipp is not None else (asset.get("ip") or [])
    ip_public = aa.get("ip_public") or []
    if not isinstance(ip_private, list):
        ip_private = [ip_private]
    if not isinstance(ip_public, list):
        ip_public = [ip_public]

    geo = {
        "country": _first_or_join(aa.get("geo_countries")),
        "region": _first_or_join(aa.get("geo_regions")),
        "city": _first_or_join(aa.get("geo_cities")),
    }
    return list(ip_private), list(ip_public), geo


def _derive_posture(
    category: Optional[str], providers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Replicate the UI posture matrix: per category check, the contributing
    providers are those whose source_category/type/name contains any keyword for
    that check (InfoCards.tsx getPostureStatus). ``passed`` = at least one."""
    cat = (category or "").strip().lower()
    checks = []
    for label, key in _POSTURE_CHECKS.get(cat, []):
        keywords = _POSTURE_KEYWORDS.get(key, ())
        badges: List[str] = []
        for p in providers or []:
            haystacks = (
                str(p.get("source_category") or "").lower(),
                str(p.get("source_type") or "").lower(),
                str(p.get("source_name") or "").lower(),
            )
            if any(kw in h for kw in keywords for h in haystacks):
                name = (p.get("source_name") or p.get("source_type")
                        or p.get("data_source"))
                if name and name not in badges:
                    badges.append(name)
        checks.append({"label": label, "passed": bool(badges),
                       "providers": badges})
    return {"category": cat, "checks": checks}


def _unique_str_values(rows: List[Dict[str, Any]], col: str) -> List[str]:
    out: List[str] = []
    for row in rows:
        v = row.get(col)
        if v is None:
            continue
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def _organize_provider_extended(dsf: Any) -> Dict[str, str]:
    """Flatten a provider's data_source_fields JSONB to {display_key: value} —
    mirrors AssetService._organize_provider_fields (nested dicts flatten, lists
    join, scalars stringify) so chat shows the VALUES, not a field count."""
    extended: Dict[str, str] = {}
    if not isinstance(dsf, dict):
        return extended
    for key, value in dsf.items():
        display_key = key.replace("_", " ").title()
        if isinstance(value, dict):
            for nk, nv in value.items():
                extended[nk.replace("_", " ").title()] = (
                    str(nv) if nv is not None else "-")
        elif isinstance(value, list):
            extended[display_key] = ", ".join(str(i) for i in value)
        else:
            extended[display_key] = str(value) if value is not None else "-"
    return extended


def _group_providers(providers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group raw get_asset_providers rows by data source + organize their fields
    (general/network/extended), mirroring AssetService._build_providers /
    _merge_provider_rows so the chat detail matches the UI's per-source panels."""
    from collections import OrderedDict
    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for row in providers or []:
        key = row.get("data_source") or row.get("source_name") or "unknown"
        grouped.setdefault(key, []).append(row)

    result: List[Dict[str, Any]] = []
    for source_id, rows in grouped.items():
        first = rows[0]
        general: Dict[str, str] = {}
        for col, label in _PROVIDER_GENERAL_MAP:
            vals = _unique_str_values(rows, col)
            if vals:
                general[label] = ", ".join(vals)
        network: Dict[str, str] = {}
        for col, label in _PROVIDER_NETWORK_MAP:
            vals = _unique_str_values(rows, col)
            if vals:
                network[label] = ", ".join(vals)
        merged_dsf: Dict[str, Any] = {}
        for row in rows:
            dsf = row.get("data_source_fields")
            if not isinstance(dsf, dict):
                continue
            for k, v in dsf.items():
                if k not in merged_dsf:
                    merged_dsf[k] = v
                elif merged_dsf[k] != v:
                    existing = merged_dsf[k]
                    if not isinstance(existing, list):
                        existing = [existing]
                    if v not in existing:
                        existing.append(v)
                    merged_dsf[k] = existing
        extended = _organize_provider_extended(merged_dsf)
        result.append({
            "source_id": source_id,
            "source_name": first.get("source_name") or source_id,
            "source_type": first.get("source_type") or "unknown",
            "source_category": (first.get("source_category")
                                or first.get("data_source_category")),
            "last_sync": first.get("last_sync_at"),
            "record_count": len(rows),
            "general": general,
            "network": network,
            "extended": extended,
            "field_count": len(general) + len(network) + len(extended),
        })
    return result


def _derive_conflicts(providers: List[Dict[str, Any]]) -> List[str]:
    """Per-attribute disagreements across sources — folds
    get_asset_source_breakdown's conflict roll-up into get_asset."""
    conflicts: List[str] = []
    for attr in _PROVIDER_CONFLICT_ATTRS:
        values: Dict[str, List[str]] = {}
        for p in providers or []:
            v = p.get(attr)
            if v in (None, ""):
                continue
            k = str(v).strip().lower()
            label = p.get("source_name") or p.get("data_source") or "?"
            values.setdefault(k, [])
            if label not in values[k]:
                values[k].append(label)
        if len(values) > 1:
            parts = [f"'{k}' ({', '.join(srcs)})" for k, srcs in values.items()]
            conflicts.append(f"{attr}: " + " vs ".join(parts))
    return conflicts


def compute_asset_sections(
    asset: Dict[str, Any],
    providers: List[Dict[str, Any]],
    *,
    source: Optional[str] = None,
    field_search: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive the UI-fidelity single-asset view from a raw asset dict + its
    get_asset_providers rows. Pure (no DB). Shared by the text formatter AND the
    artifact builder so they can't drift.

    ``source`` bounds the per-source ext-attr detail to one provider
    (case-insensitive on data_source/source_name); ``field_search`` bounds each
    provider's ext-attrs to keys/values containing the term. Posture + conflicts
    are always computed over ALL providers (they are summaries)."""
    ip_private, ip_public, geo = _ip_split_and_geo(asset)
    all_grouped = _group_providers(providers)

    grouped = all_grouped
    source_matched = True
    if source:
        s = source.strip().lower()
        grouped = [g for g in all_grouped
                   if s in str(g["source_id"]).lower()
                   or s in str(g["source_name"]).lower()]
        source_matched = bool(grouped)

    if field_search:
        fs = field_search.strip().lower()
        for g in grouped:
            g["extended"] = {
                k: v for k, v in g["extended"].items()
                if fs in k.lower() or fs in str(v).lower()
            }

    return {
        "ip_private": ip_private,
        "ip_public": ip_public,
        "geo": geo,
        "mac_vendor": _format_array(asset.get("mac_vendor_enriched")) or None,
        "posture": _derive_posture(asset.get("category"), providers),
        "providers": grouped,
        "providers_total": len(all_grouped),
        "available_sources": [g["source_name"] for g in all_grouped],
        "source_filter": source,
        "source_matched": source_matched,
        "field_search": field_search,
        "conflicts": _derive_conflicts(providers),
    }


def resolve_asset_identifier(
    db, resolver, identifier: str, *, limit: int = 10,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a UUID or human identifier (hostname/ip/fqdn/mac/serial) to a
    single asset id. Returns ``(asset_id, None)`` on a unique resolution, or
    ``(None, error)`` LOUDLY when nothing matches or the term is ambiguous —
    never silently picking the first of several (that would answer about a
    different asset than intended). Reuses the SAME resolution the external API
    uses (repo.query_assets ``q`` free-text across the identity fields)."""
    if identifier is None or not str(identifier).strip():
        return None, ("Provide an identifier: a UUID, hostname, IP, FQDN, MAC, "
                      "or serial number.")
    ident = str(identifier).strip()

    # A valid UUID resolves directly — no lookup needed.
    if validate_uuid(ident, "asset") is None:
        return ident, None

    repo = AssetRepository(db, resolver)
    rows, total = repo.query_assets(q=ident, limit=limit)
    if not rows:
        return None, (
            f"No asset matches {ident!r} (searched hostname, IP, FQDN, MAC, "
            f"serial). Use query_assets to search more broadly."
        )
    if len(rows) > 1:
        lines = [
            f"{total} assets match {ident!r}; specify which by its UUID "
            f"(or narrow with query_assets):"
        ]
        for r in rows[:10]:
            host = _format_array(r.get("hostname")) or "(no hostname)"
            lines.append(f"  {r.get('id')}  {host}")
        if total > len(rows[:10]):
            lines.append(f"  … +{total - len(rows[:10])} more")
        return None, "\n".join(lines)
    return str(rows[0].get("id")), None


def _fmt_sync(ts: Any) -> Optional[str]:
    """Compact display for a provider last_sync timestamp."""
    if ts is None:
        return None
    return str(ts)[:19].replace("T", " ")


def _kv_inline(d: Dict[str, Any]) -> str:
    """Render a small {label: value} dict as 'label: value; label: value'."""
    return "; ".join(f"{k}: {v}" for k, v in d.items())


# Per-provider extended-attribute VALUES are unbounded — a single integration
# can expose hundreds of custom fields — so cap how many we inline into the
# detail text. The full set still reaches the user via the asset artifact; the
# `field_search` param narrows to matching keys/values when a specific one is
# wanted. Without this, one richly-integrated asset could blow the context.
_MAX_PROVIDER_EXT_FIELDS = 40


def _kv_inline_capped(d: Dict[str, Any], cap: int) -> str:
    """Like ``_kv_inline`` but show at most ``cap`` pairs, appending a loud
    '(+N more …)' marker when the dict is larger so the omission isn't silent."""
    items = list(d.items())
    head = "; ".join(f"{k}: {v}" for k, v in items[:cap])
    hidden = len(items) - cap
    if hidden > 0:
        head += (f"  (+{hidden} more field(s) not shown — use `field_search` "
                 f"to target one)")
    return head


def format_asset_detail(
    asset: dict,
    *,
    asset_id: str,
    tags: List[dict],
    providers: List[dict],
    source: Optional[str] = None,
    field_search: Optional[str] = None,
) -> str:
    """Format a raw asset dict + repo-fetched tags/providers as the LLM-facing
    detail string, at UI fidelity: private/public IP split + geo, a posture
    matrix with contributing provider badges, per-source provider panels with
    extended-attribute VALUES (not counts) + last_sync, and cross-source
    conflicts (folding get_asset_source_breakdown).

    Pure function — no DB access. ``source``/``field_search`` bound the
    per-source ext-attr output (see ``compute_asset_sections``)."""
    sections = compute_asset_sections(
        asset, providers, source=source, field_search=field_search,
    )
    lines = [f"Asset: {asset_id}\n"]

    # Identity (IPs move to the Network section so the public/private split
    # isn't duplicated by a merged 'IP Address' line).
    for key, label in [
        ("hostname", "Hostname"),
        ("fqdn", "FQDN"),
        ("mac", "MAC Address"),
        ("sn", "Serial Number"),
    ]:
        val = _format_array(asset.get(key))
        if val:
            lines.append(f"  {label}: {val}")

    # Classification
    lines.append(f"\n  Category: {asset.get('category') or '(none)'}")
    lines.append(f"  Subcategory: {asset.get('subcategory') or '(none)'}")
    os_val = _format_array(asset.get("os_normalized"))
    if os_val:
        lines.append(f"  OS: {os_val}")
    if asset.get("os_eol_status"):
        lines.append(f"  OS EOL Status: {asset['os_eol_status']}")

    # Hardware
    for key, label in [
        ("manufacturer", "Manufacturer"),
        ("model", "Model"),
        ("mac_vendor_enriched", "MAC Vendor"),
    ]:
        val = _format_array(asset.get(key))
        if val:
            lines.append(f"  {label}: {val}")

    # Ownership / Location
    for key, label in [
        ("owner", "Owner"),
        ("location", "Location"),
        ("ipam_location", "IPAM Location"),
    ]:
        val = _format_array(asset.get(key))
        if val:
            lines.append(f"  {label}: {val}")

    # Network: private vs public IPs + geo
    net_lines = []
    if sections["ip_private"]:
        net_lines.append(f"    Private IP: {_format_array(sections['ip_private'])}")
    if sections["ip_public"]:
        net_lines.append(f"    Public IP: {_format_array(sections['ip_public'])}")
    geo = sections["geo"]
    for glabel, gkey in [("Country", "country"), ("Region", "region"),
                         ("City", "city")]:
        if geo.get(gkey):
            net_lines.append(f"    {glabel}: {geo[gkey]}")
    if net_lines:
        lines.append("\n  Network:")
        lines.extend(net_lines)

    # Confidence
    conf = asset.get("confidence")
    if conf is not None:
        lines.append(f"\n  Confidence: {conf:.0%}")
    breakdown = asset.get("confidence_breakdown")
    if breakdown and isinstance(breakdown, dict):
        lines.append(f"  Sources: {breakdown.get('source_count', '?')}")
        if breakdown.get("conflicting_fields"):
            conflicts = ', '.join(breakdown['conflicting_fields'])
            lines.append(f"  Confidence conflicts: {conflicts}")
        if breakdown.get("missing_fields"):
            missing = ', '.join(breakdown['missing_fields'])
            lines.append(f"  Missing: {missing}")

    # Data sources
    ds = _format_array(asset.get("data_source"))
    if ds:
        lines.append(f"\n  Data Sources: {ds}")

    # Timestamps
    for key, label in [
        ("first_seen", "First Seen"),
        ("last_seen", "Last Seen"),
    ]:
        if asset.get(key):
            lines.append(f"  {label}: {asset[key]}")

    # Tags
    if tags:
        tag_strs = [t["tag_name"] for t in tags]
        lines.append(f"\n  Tags: {', '.join(tag_strs)}")

    # Posture matrix with contributing provider badges
    posture = sections["posture"]
    if posture["checks"]:
        lines.append(f"\n  Posture ({posture['category']}):")
        for c in posture["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            badge = f" — {', '.join(c['providers'])}" if c["providers"] else ""
            lines.append(f"    [{mark}] {c['label']}{badge}")
    elif posture["category"]:
        lines.append(
            f"\n  Posture: no checks defined for category "
            f"'{posture['category']}'."
        )

    # Per-source provider panels (extended-attribute VALUES + last_sync)
    grouped = sections["providers"]
    if source and not sections["source_matched"]:
        avail = ", ".join(sections["available_sources"]) or "(none)"
        lines.append(
            f"\n  No data source matching {source!r}. Available: {avail}."
        )
    elif grouped:
        shown = f" (filtered to {source!r})" if source else ""
        lines.append(f"\n  Providers ({sections['providers_total']}){shown}:")
        for g in grouped:
            sync = _fmt_sync(g["last_sync"])
            sync_str = f" — synced {sync}" if sync else ""
            lines.append(
                f"    ▸ {g['source_name']} ({g['source_type']}){sync_str}")
            if g["general"]:
                lines.append(f"        General: {_kv_inline(g['general'])}")
            if g["network"]:
                lines.append(f"        Network: {_kv_inline(g['network'])}")
            if g["extended"]:
                lines.append(
                    "        Extended: "
                    + _kv_inline_capped(g["extended"], _MAX_PROVIDER_EXT_FIELDS))

    # Cross-source conflicts
    if sections["conflicts"]:
        lines.append("\n  Conflicting values across sources:")
        for c in sections["conflicts"]:
            lines.append(f"    {c}")

    return "\n".join(lines)


def get_asset_impl(db, resolver, *, asset_id: str) -> str:
    err = validate_uuid(asset_id, "asset")
    if err:
        return err

    asset = get_asset_query(db, resolver, asset_id=asset_id)

    if not asset:
        return f"Asset not found: {asset_id}"

    repo = AssetRepository(db, resolver)
    tags = repo.get_asset_tags(asset_id)
    providers = repo.get_asset_providers(asset_id)
    return format_asset_detail(asset, asset_id=asset_id, tags=tags,
                               providers=providers)


def list_assets_query(
    db,
    resolver,
    *,
    filters: Optional[Union[str, list]] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    fields: Optional[str] = None,
    logic: str = "and",
) -> Tuple[List[dict], int]:
    """Normalize args, run the repo list query, and return ``(rows, total)``.

    Applies the same limit clamp (1-200), page clamp (≥1), filter
    normalization, field encoding, and sort normalization as the impl.
    Returns ``([], 0)`` when the repo finds nothing.

    The chat binding uses this split to build a table artifact from the
    raw rows while ``list_assets_impl`` builds the LLM summary string.
    """
    limit = max(1, min(limit, 200))
    page = max(1, page)

    parsed_filters, err = parse_filters_arg(filters)
    if err:
        return [], 0

    parsed_filters = _normalize_filters(parsed_filters, resolver=resolver)
    parsed_fields = _normalize_field_list(fields, resolver)

    # Normalize sort_by the same way. An unresolvable sort_by RAISES rather
    # than being dropped: silently sorting in default order returns a DIFFERENT
    # result than the user asked for, with no signal (§4). Mirrors
    # _normalize_filters' loud-error stance; the caller surfaces it as a string.
    normalized_sort = sort_by
    if sort_by:
        resolved_sort = _normalize_field_name(sort_by, resolver)
        if resolved_sort is None:
            raise FieldResolutionError(
                _UNKNOWN_FIELD_HINT.format(param="sort_by", name=sort_by)
            )
        # A sort_by passed as an encoded id is passed through unvalidated by
        # _normalize_field_name; verify it's a live field so an encoded-dead
        # sort raises loudly here instead of as an uncaught InvalidFieldError
        # from the repo's ORDER BY (§4).
        if ":" in sort_by:
            _assert_field_resolves(
                resolved_sort, resolver, sort_by, param="sort_by",
            )
        normalized_sort = resolved_sort

    repo = AssetRepository(db, resolver)
    results, total = repo.list_assets(
        page=page,
        limit=limit,
        search=search,
        fields=parsed_fields,
        sort_by=normalized_sort,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        filters=parsed_filters,
        logic=logic,
    )
    # Project encoded FieldId keys to snake-case so the chat binding
    # (which feeds the result into build_table_from_assets) and any
    # other consumer get the same canonical keys that get_asset_by_id /
    # search_assets already return.
    return [_project_list_row_to_snake(r) for r in results], total


def format_list_assets(
    results: List[dict],
    *,
    total: int,
    page: int,
    limit: int,
    max_rows: Optional[int] = None,
    columns: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Format raw list_assets results as the LLM-facing text string.

    Pure function — no DB access. ``page`` must already be clamped (≥1),
    and ``limit`` must already be clamped (≤200), as list_assets_query
    does internally.
    When ``max_rows`` is set and more rows were fetched than that, only the
    first ``max_rows`` are rendered into the text (to keep the LLM's context
    lean); a note points the model at the full table + CSV download. Default
    ``None`` renders every row (list_assets_impl / MCP stdio path, unchanged).
    ``columns`` (a resolved ``[(row_key, label)]`` list) makes each row mirror
    the selected table columns; ``None`` uses the fixed key-field set.
    """
    if not results:
        return "No assets found matching the given criteria."

    total_pages = (total + limit - 1) // limit
    lines = [
        f"Assets: {total} total, showing page {page}/{total_pages}"
        f" ({len(results)} results)\n"
    ]

    shown = results if max_rows is None else results[:max_rows]
    for asset in shown:
        lines.append(_format_asset_row(asset, columns=columns))
        lines.append("")

    if max_rows is not None and len(results) > max_rows:
        hidden = len(results) - max_rows
        lines.append(
            f"... (+{hidden} more shown to the user in a table; "
            f"the full {total} can be downloaded as CSV)"
        )

    if page < total_pages:
        lines.append(
            f"[Page {page}/{total_pages} — use page={page + 1} for next]"
        )

    return "\n".join(lines)


def list_assets_impl(
    db,
    resolver,
    *,
    filters: Optional[Union[str, list]] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    fields: Optional[str] = None,
) -> str:
    # parse_filters_arg is pure; calling it here only to surface the error
    # string. list_assets_query parses again internally — accepted as cheap.
    _, err = parse_filters_arg(filters)
    if err:
        return err

    # An unknown operator (_normalize_filters), unknown filter field
    # (_normalize_filters), or unresolvable sort_by (list_assets_query) is
    # rejected loudly; surface it to the LLM as a string rather than letting the
    # exception cross the tool boundary.
    try:
        results, total = list_assets_query(
            db, resolver,
            filters=filters,
            search=search,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_dir=sort_dir,
            fields=fields,
        )
    except (OperatorResolutionError, FieldResolutionError) as exc:
        return str(exc)

    # Re-derive page/limit after clamping (mirrors what query did internally).
    limit = max(1, min(limit, 200))
    page = max(1, page)

    return format_list_assets(results, total=total, page=page, limit=limit)


def aggregate_assets_query(
    db,
    resolver,
    *,
    row_field: str,
    col_field: Optional[str] = None,
    filters: Optional[Union[str, list]] = None,
    agg: str = "COUNT",
    metric_field: Optional[str] = None,
    logic: str = "and",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate args, normalize fields, and run the aggregation query.

    Returns ``(result, error_hint)`` with exactly one slot populated:

    - ``result``: the repository output dict
      (``{"rows", "columns", "data", "grand_total"}``) on success.
    - ``error_hint``: the LLM-facing string on validation failure
      (unknown field/metric, malformed filters, or an invalid agg combo).

    An empty result is still success — ``result["rows"]`` may be ``[]``.
    ``agg``/``metric_field`` select the aggregate function (COUNT default;
    SUM/AVG/MIN/MAX/COUNT_DISTINCT aggregate ``metric_field``); ``logic`` is the
    flat AND/OR combiner for ``filters``. The chat binding uses this split to
    build a donut artifact from the raw repo dict while a formatter builds the
    summary.
    """
    # Normalize row/col field names so the LLM can pass display names
    # ("OS Normalized"), snake_case ("os_normalized"), or already-encoded
    # FieldIds ("static:OS Normalized"). Without this, every bare name
    # silently returns "No data found" because decode_field_id() rejects it.
    encoded_row = _normalize_field_name(row_field, resolver)
    if encoded_row is None:
        return None, _UNKNOWN_FIELD_HINT.format(
            param="row_field", name=row_field,
        )

    encoded_col: Optional[str] = None
    if col_field:
        encoded_col = _normalize_field_name(col_field, resolver)
        if encoded_col is None:
            return None, _UNKNOWN_FIELD_HINT.format(
                param="col_field", name=col_field,
            )

    # Resolve the metric field for non-COUNT aggregates the same way, with the
    # same loud-error contract — a bad metric is rejected up front, not as an
    # opaque SQL error mid-query.
    agg_norm = (agg or "COUNT").upper()
    encoded_metric: Optional[str] = None
    if agg_norm != "COUNT":
        if not metric_field:
            return None, (
                f"Aggregate {agg_norm} needs a metric_field (the numeric field "
                f"to {agg_norm.lower().replace('_', ' ')}). COUNT needs none."
            )
        encoded_metric = _normalize_field_name(metric_field, resolver)
        if encoded_metric is None:
            return None, _UNKNOWN_FIELD_HINT.format(
                param="metric_field", name=metric_field,
            )

    parsed_filters, err = parse_filters_arg(filters)
    if err:
        return None, err

    try:
        parsed_filters = _normalize_filters(parsed_filters, resolver=resolver)
    except (OperatorResolutionError, FieldResolutionError) as exc:
        return None, str(exc)
    repo = AssetRepository(db, resolver)
    try:
        result = repo.aggregate_assets(
            row_field=encoded_row,
            col_field=encoded_col,
            filters=parsed_filters,
            agg=agg_norm,
            metric_field=encoded_metric,
            logic=logic,
        )
    except (ValueError, InvalidFieldError) as exc:
        # e.g. SUM over an array field, unknown agg, or an unresolvable metric
        # (InvalidFieldError is NOT a ValueError) — surface as a clean string.
        return None, str(exc)
    return result, None


def format_aggregate_result(
    result: Dict[str, Any],
    row_field: str,
    col_field: Optional[str] = None,
) -> str:
    """Render the aggregation repo output as the LLM-facing summary string."""
    rows = result.get("rows", [])
    if not rows:
        return f"No data found for aggregation by '{row_field}'."

    grand_total = result.get("grand_total", 0)
    columns = result.get("columns", [])
    data = result.get("data", {})

    # Lead with the count on its own line. When the user asks a "how many"
    # question the model should quote THIS number — not sum rows, not cite a
    # single row, not derive a percentage. Keeping the label short ("COUNT:")
    # and free-standing makes it mechanically easy to pick out.
    if columns:
        # 2D pivot table. Cap the visible columns to the top-N by column
        # total — otherwise high-cardinality col_fields ("OS Normalized" has
        # ~150 distinct values) produce a several-thousand-character line
        # that weak LLMs mis-align and cite the wrong cell (e.g. reading
        # the Total column as a specific value). The truncation is a
        # presentation-layer change; all cells are still queried and the
        # bottom-line grand_total stays exact.
        _MAX_COLS = 20
        col_totals_map = {
            c: sum(data.get(r, {}).get(c, 0) for r in rows) for c in columns
        }
        sorted_cols = sorted(
            columns, key=lambda c: col_totals_map[c], reverse=True
        )
        shown_cols = sorted_cols[:_MAX_COLS]
        hidden_cols = sorted_cols[_MAX_COLS:]
        hidden_total = sum(col_totals_map[c] for c in hidden_cols)

        lines = [
            f"COUNT: {grand_total} assets match the filter.",
            f"Breakdown: {row_field} × {col_field} "
            f"(cells are counts; row/col totals at the edges)",
        ]
        if hidden_cols:
            lines.append(
                f"[Showing top {len(shown_cols)} of"
                f" {len(columns)} {col_field} "
                f"columns by total; {len(hidden_cols)} more grouped as "
                f"'(other)' below, totaling {hidden_total}. For a deeper"
                f" cut, call aggregate_assets with"
                f" row_field={col_field!r} and a filter on {row_field!r}.]"
            )
        lines.append("")

        # Header
        header_cols = shown_cols + (["(other)"] if hidden_cols else [])
        header = (
            f"{'':30s}"
            + "".join(f"{c:>12s}" for c in header_cols)
            + f"{'Total':>12s}"
        )
        lines.append(header)
        lines.append("-" * len(header))

        for row in rows:
            row_data = data.get(row, {})
            row_total = sum(row_data.values())
            cells = "".join(f"{row_data.get(c, 0):>12d}" for c in shown_cols)
            if hidden_cols:
                row_other = sum(row_data.get(c, 0) for c in hidden_cols)
                cells += f"{row_other:>12d}"
            lines.append(f"{row:30s}{cells}{row_total:>12d}")

        # Column totals
        col_total_cells = "".join(
            f"{col_totals_map[c]:>12d}" for c in shown_cols
        )
        if hidden_cols:
            col_total_cells += f"{hidden_total:>12d}"
        lines.append("-" * len(header))
        lines.append(f"{'Total':30s}{col_total_cells}{grand_total:>12d}")
    else:
        # 1D aggregation
        lines = [
            f"COUNT: {grand_total} assets match the filter.",
            f"Breakdown by {row_field} (counts per distinct value):\n",
        ]
        for row in rows:
            count = data.get(row, {}).get("count", 0)
            pct = (count / grand_total * 100) if grand_total > 0 else 0
            bar = "█" * int(pct / 2)
            lines.append(f"  {row:30s} {count:>6d}  ({pct:5.1f}%) {bar}")

    return "\n".join(lines)


def aggregate_assets_impl(
    db,
    resolver,
    *,
    row_field: str,
    col_field: Optional[str] = None,
    filters: Optional[Union[str, list]] = None,
) -> str:
    result, error_hint = aggregate_assets_query(
        db, resolver,
        row_field=row_field, col_field=col_field, filters=filters,
    )
    if error_hint is not None:
        return error_hint
    assert result is not None  # invariant: exactly one of (result, hint)
    return format_aggregate_result(result, row_field, col_field)


def get_field_values_query(
    db,
    resolver,
    *,
    field: str,
    search: Optional[str] = None,
    limit: int = 50,
    filters: Optional[Union[str, list]] = None,
    logic: str = "and",
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[int], Optional[str]]:
    """Validate args, normalize the field/filters, and run the distinct-value
    query, returning ``(values, total_distinct, error_hint)`` with the error
    slot populated only on failure.

    The chat ``query_assets`` field_values mode uses this split to build a
    value/count table artifact from the raw list while ``get_field_values_impl``
    builds the LLM text. ``limit`` is passed through UN-clamped — the caller
    applies its own cap (the MCP impl 200, query_assets 500) so the two surfaces
    can differ without this helper having to know about either.
    """
    encoded_field = _normalize_field_name(field, resolver)
    if encoded_field is None:
        return None, None, _UNKNOWN_FIELD_HINT.format(param="field", name=field)

    parsed_filters, err = parse_filters_arg(filters)
    if err:
        return None, None, err

    try:
        parsed_filters = _normalize_filters(parsed_filters, resolver=resolver)
    except (OperatorResolutionError, FieldResolutionError) as exc:
        return None, None, str(exc)
    repo = AssetRepository(db, resolver)
    values, total_distinct = repo.get_field_values(
        field=encoded_field,
        limit=limit,
        search=search,
        filters=parsed_filters,
        logic=logic,
    )
    return values, total_distinct, None


def get_field_values_impl(
    db,
    resolver,
    *,
    field: str,
    search: Optional[str] = None,
    limit: int = 50,
    filters: Optional[Union[str, list]] = None,
) -> str:
    limit = max(1, min(limit, 200))

    values, total_distinct, error_hint = get_field_values_query(
        db, resolver, field=field, search=search, limit=limit, filters=filters,
    )
    if error_hint is not None:
        return error_hint

    if not values:
        return f"No values found for field '{field}'."

    top_n = len(values)
    lines = [
        f"Field '{field}': {total_distinct} distinct values"
        f" (showing top {top_n}):\n"
    ]
    for v in values:
        lines.append(f"  {v['value']:40s} {v['count']:>6d}")

    if total_distinct > top_n:
        remaining = total_distinct - top_n
        lines.append(f"\n  [{remaining} more values not shown]")

    return "\n".join(lines)
