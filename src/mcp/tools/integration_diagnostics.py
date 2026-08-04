# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration diagnostics (MCP) + the chat ``get_integration`` tool.

MCP server tool: ``get_integration_diagnostics`` (unchanged — still mounted on
the standalone MCP server).

Chat tool: ``get_integration`` — a read-only, admin-gated view of ONE
integration (resolved by name case-insensitively OR id) across
status / config / insights / timeline / schedule / stats. It folds the old
``get_integration_status`` + ``get_integration_diagnostics`` chat tools
(tasks/chatbot-tool-parity.md §2.E), takes freshness from
``DashboardService.get_data_sources`` (the integration-aware view the homepage
uses) instead of a bespoke join, and ALWAYS redacts credentials — it never
decrypts config (``include_credentials`` is not exposed to chat) and masks the
authoritative sensitive keys (``IntegrationRepository.SENSITIVE_FIELDS``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.modules.settings.settings_schema import get_confidence_thresholds
from src.modules.integrations.repositories.integration_repository import (
    IntegrationRepository,
)
from src.modules.integrations.shared.encryption import is_encrypted
from src.mcp.context import AppContext
from src.mcp.tools._common import (
    is_sensitive_key,
    truncate,
    unknown_enum,
    validate_uuid,
)
from src.mcp.tools.integration_admin import (
    _resolve_integration,
    get_integration_schedule_impl,
)

DiagnosticsKind = Literal["insights", "timeline", "both"]
_VALID_KINDS: tuple[DiagnosticsKind, ...] = ("insights", "timeline", "both")
_MAX_TIMELINE_POINTS = 30
_TOP_FIELDS = 5
_TOP_CATEGORIES = 5


def register_tools(mcp):
    """Register integration-diagnostics tools on the FastMCP server."""

    @mcp.tool()
    def get_integration_diagnostics(
        integration_id: str,
        kind: str = "both",
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return diagnostics for one integration: stale/missing-attribute insights and/or recent sync timeline."""
        integration_service = ctx.request_context.lifespan_context.integration_service
        if integration_service is None:
            return "Integration service not available."
        sm = ctx.request_context.lifespan_context.settings_manager
        medium, high = get_confidence_thresholds(sm)
        return get_integration_diagnostics_impl(
            integration_service,
            integration_id=integration_id,
            kind=kind,
            medium=medium,
            high=high,
        )


def get_integration_diagnostics_impl(
    integration_service,
    *,
    integration_id: str,
    kind: DiagnosticsKind = "both",
    medium: Optional[float] = None,
    high: Optional[float] = None,
) -> str:
    if integration_service is None:
        return "Integration service not available."

    if kind not in _VALID_KINDS:
        return (
            f"Unknown kind '{kind}'. "
            f"Expected one of: {', '.join(_VALID_KINDS)}."
        )

    err = validate_uuid(integration_id, "integration")
    if err:
        return err

    iid = uuid.UUID(integration_id)

    if kind == "insights":
        return _render_insights(integration_service, iid, integration_id, medium=medium, high=high)
    if kind == "timeline":
        return _render_timeline(integration_service, iid, integration_id)

    # kind == "both": short-circuit only when BOTH sub-calls report not-found.
    # One 404 alongside a successful section is surfaced inline so the caller
    # still sees the good data instead of losing it to a blanket "not found".
    insights_section = _render_insights(integration_service, iid, integration_id, medium=medium, high=high)
    timeline_section = _render_timeline(integration_service, iid, integration_id)

    not_found_msg = f"Integration {integration_id} not found."
    if insights_section == not_found_msg and timeline_section == not_found_msg:
        return not_found_msg

    return f"{insights_section}\n\n{timeline_section}"


def _render_insights(integration_service, iid: uuid.UUID, integration_id: str, *, medium: float = None, high: float = None) -> str:
    try:
        result = integration_service.get_insights(iid, medium=medium, high=high)
    except Exception as e:
        return f"# Insights\n\nInsights unavailable: {e}"
    if result.get("success") is False:
        return f"Integration {integration_id} not found."
    return _format_insights(result)


def _render_timeline(
    integration_service, iid: uuid.UUID, integration_id: str,
    *, days: Optional[int] = None,
) -> str:
    try:
        result = (
            integration_service.get_timeline(iid, days=days)
            if days is not None
            else integration_service.get_timeline(iid)
        )
    except Exception as e:
        return f"# Timeline\n\nTimeline unavailable: {e}"
    if result.get("success") is False:
        return f"Integration {integration_id} not found."
    return _format_timeline(result)


def _format_insights(result: Dict[str, Any]) -> str:
    name = result.get("integration_name", "unknown")
    indicators = result.get("indicators") or {}

    stale = indicators.get("stale_assets") or {}
    stale_count = stale.get("count", 0)
    stale_total = stale.get("total", 0)
    stale_pct = stale.get("percentage", 0.0)
    threshold = stale.get("threshold_days", 30)

    missing = indicators.get("missing_attributes") or {}
    fields: Dict[str, Any] = missing.get("fields") or {}
    worst_field = missing.get("worst_gap_field")
    worst_pct = missing.get("worst_gap_pct", 0.0)

    # The service returns conflicting_attributes either as a list of
    # {field_name, distinct_count, examples} dicts (legacy / some paths) or
    # as an aggregate {'total_conflicts': N, 'by_field': {field: count, ...}}
    # dict (the shape that SentinelOne actually returns). Previous code
    # assumed only the list shape and crashed with 'str has no attribute get'
    # when it iterated the dict's string keys.
    conflicts_raw = indicators.get("conflicting_attributes")
    if isinstance(conflicts_raw, dict):
        by_field = conflicts_raw.get("by_field") or {}
        conflicts: List[Dict[str, Any]] = [
            {"field_name": k, "distinct_count": v} for k, v in by_field.items()
        ]
        total_conflicts = conflicts_raw.get("total_conflicts", sum(by_field.values()))
    elif isinstance(conflicts_raw, list):
        conflicts = conflicts_raw
        total_conflicts = len(conflicts)
    else:
        conflicts = []
        total_conflicts = 0

    expected = indicators.get("missing_expected_assets") or {}
    categories: List[Dict[str, Any]] = expected.get("categories") or []
    total_missing = expected.get("total_estimated_missing", 0)

    lines: List[str] = [
        f"# Insights — {name}",
        "",
        "## Stale assets",
        f"- {stale_count}/{stale_total} stale ({stale_pct}%, threshold {threshold}d)",
        "",
        "## Missing attributes",
    ]

    if worst_field:
        lines.append(f"- Worst gap: {worst_field} ({worst_pct}%)")
    else:
        lines.append("- Worst gap: none")

    if fields:
        sorted_fields = sorted(
            fields.items(),
            key=lambda kv: (kv[1] or {}).get("gap", 0),
            reverse=True,
        )[:_TOP_FIELDS]
        lines.append(f"- Top {len(sorted_fields)} fields by gap:")
        for fname, info in sorted_fields:
            gap = (info or {}).get("gap", 0)
            lines.append(f"  - {fname}: {gap}%")
    else:
        lines.append("- No field coverage data.")

    lines.append("")
    lines.append("## Conflicting attributes")
    if conflicts:
        lines.append(f"- {total_conflicts} total conflicts across {len(conflicts)} fields")
        for c in conflicts:
            field_name = c.get("field_name", "?")
            distinct = c.get("distinct_count", c.get("count", 0))
            examples = c.get("examples") or []
            ex_str = ", ".join(str(x) for x in examples[:3])
            lines.append(
                f"  - {field_name}: {distinct} distinct values"
                + (f" (examples: {ex_str})" if ex_str else "")
            )
    else:
        lines.append("- No conflicts.")

    lines.append("")
    lines.append("## Missing expected assets")
    lines.append(f"- Estimated missing: {total_missing}")
    if categories:
        top_cats = sorted(
            categories,
            key=lambda c: c.get("estimated_missing", 0),
            reverse=True,
        )[:_TOP_CATEGORIES]
        lines.append(f"- Top {len(top_cats)} categories:")
        for c in top_cats:
            cat = c.get("category", "?")
            miss = c.get("estimated_missing", 0)
            lines.append(f"  - {cat}: {miss}")
    else:
        lines.append("- None.")

    return "\n".join(lines)


def _format_timeline(result: Dict[str, Any]) -> str:
    rng = result.get("range", "?")
    data: List[Dict[str, Any]] = result.get("data") or []

    if not data:
        return f"# Timeline — last {rng}\n\nNo timeline data available."

    # Render the most-recent window; the earlier points are dropped from the
    # text (not silently — the header states the true count and the note below
    # flags how many earlier points were omitted).
    points = data[-_MAX_TIMELINE_POINTS:]
    omitted = len(data) - len(points)

    # Discover columns: always prefer date first, then every other key found
    # in the first point. Fallback keys help when shape is slightly different.
    first = points[0]
    keys = list(first.keys())
    # Put "date" first if present
    if "date" in keys:
        keys = ["date"] + [k for k in keys if k != "date"]

    header_cells = [k for k in keys]
    lines = [
        f"# Timeline — last {rng} ({len(data)} data points)",
        "",
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join("---" for _ in header_cells) + " |",
    ]
    for p in points:
        row = [str(p.get(k, "—")) for k in keys]
        lines.append("| " + " | ".join(row) + " |")
    if omitted > 0:
        lines.append("")
        lines.append(f"[{omitted} earlier data point(s) not shown — showing the "
                     f"most recent {len(points)}; narrow the window for older.]")
    return "\n".join(lines)


# ===========================================================================
# get_integration (chat tool, plan §2.E) — folds status + diagnostics + config
# + schedule + stats for one integration. Read-only, admin-gated.
# ===========================================================================

_INTEGRATION_VIEWS = (
    "status", "config", "insights", "timeline", "schedule", "stats",
)
_DEFAULT_TIMELINE_DAYS = 30
_MAX_TIMELINE_DAYS = 365      # matches get_dashboard_trend's ceiling
_MAX_CONFIG_ROWS = 50         # cap field_mapping/transformations/filters output

# Authoritative sensitive-config keys (the SAME set the encryption layer uses on
# write), lower-cased for matching. Reused — not re-derived — so chat redaction
# can't drift from the server's.
_SENSITIVE_CONFIG_KEYS = {f.lower() for f in IntegrationRepository.SENSITIVE_FIELDS}

# Auth-bearing HTTP header names. A custom_http integration can hand-roll auth
# by stashing a credential in a static header (config["headers"], stored
# verbatim/plaintext at rest — see custom_http_connector). SENSITIVE_FIELDS and
# is_sensitive_key key on credential *field* names (password/api_key/token/...)
# and miss header names, so an "Authorization: Bearer ..." static header would
# otherwise render in clear text. Matched case-insensitively, at any depth.
_SENSITIVE_HEADER_NAMES = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "x-api-token", "x-auth-token", "x-auth", "api-key",
})

_REDACTED = "***redacted***"


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _is_secret_config(key: Any, value: Any) -> bool:
    """A config entry is secret if its key is an authoritative sensitive field
    or auth header, its name looks credential-ish, OR its value is already
    encrypted at rest.

    The encryption check is the belt-and-suspenders: a custom connector could
    store a secret under a non-standard key that nonetheless got encrypted, and
    that ciphertext must still never reach the transcript.
    """
    k = str(key).lower()
    if k in _SENSITIVE_CONFIG_KEYS or k in _SENSITIVE_HEADER_NAMES:
        return True
    if is_sensitive_key(str(key)):
        return True
    if isinstance(value, str) and is_encrypted(value):
        return True
    return False


def _redact_config(value: Any) -> Any:
    """Deep-redact a config value: mask any sensitive key (or already-encrypted
    string) at ANY nesting depth, returning a redacted copy.

    A shallow top-level scan would render a credential stashed in a nested dict
    (e.g. ``config["headers"]["Authorization"]``, which custom_http stores
    plaintext) verbatim — this walks the whole structure so the tool's
    "credentials ALWAYS redacted" contract holds for nested configs too.
    """
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for k, v in value.items():
            if _is_secret_config(k, v):
                out[k] = _REDACTED if v not in (None, "") else "(not set)"
            else:
                out[k] = _redact_config(v)
        return out
    if isinstance(value, list):
        return [_redact_config(v) for v in value]
    if isinstance(value, str) and is_encrypted(value):
        return _REDACTED
    return value


def get_integration_impl(
    integration_service,
    dashboard_service,
    *,
    integration: Any,
    view: str = "status",
    timeline_days: int = _DEFAULT_TIMELINE_DAYS,
    schedule_count: int = 5,
    medium: Optional[float] = None,
    high: Optional[float] = None,
) -> str:
    """One integration, six views (resolved by name case-insensitively or id).

    ``status`` health/asset-count/freshness; ``config`` full
    config+field_mapping+transformations+data_filters (credentials redacted);
    ``insights`` stale/missing-attribute indicators; ``timeline`` recent sync
    activity; ``schedule`` cron + next runs; ``stats`` batch/record aggregates.
    """
    if integration_service is None:
        return "Integration service not available."
    if view not in _INTEGRATION_VIEWS:
        return unknown_enum("view", view, _INTEGRATION_VIEWS)

    integ, err = _resolve_integration(integration_service, integration)
    if err:
        return err

    if view == "status":
        return _render_status(integration_service, dashboard_service, integ)
    if view == "config":
        return _render_config(integ)
    if view == "insights":
        return _render_insights(
            integration_service, integ.id, str(integ.id),
            medium=medium, high=high,
        )
    if view == "timeline":
        days = max(1, min(int(timeline_days), _MAX_TIMELINE_DAYS))
        return _render_timeline(
            integration_service, integ.id, str(integ.id), days=days,
        )
    if view == "schedule":
        return get_integration_schedule_impl(
            integration_service, integration=str(integ.id), count=schedule_count,
        )
    return _render_stats(integration_service, integ)  # view == "stats"


def _freshness_for(dashboard_service, integration_id) -> Optional[Dict[str, Any]]:
    """The DashboardService data-source entry for this integration, or None.

    Uses the integration-aware homepage view (``view='individual'`` so a source
    maps 1:1 to an integration id) — the same numbers the dashboard shows, not a
    bespoke freshness join. A dashboard hiccup degrades to None rather than
    failing the whole tool.
    """
    if dashboard_service is None:
        return None
    try:
        result = dashboard_service.get_data_sources(view="individual") or {}
    except Exception:
        return None
    for src in result.get("sources") or []:
        if str(src.get("id")) == str(integration_id):
            return src
    return None


def _status_value(integ) -> str:
    status = integ.status
    return status.value if hasattr(status, "value") else str(status)


def _render_status(integration_service, dashboard_service, integ) -> str:
    fresh = _freshness_for(dashboard_service, integ.id)
    lines = [
        f"# Integration: {integ.name} (id={integ.id})",
        f"  type: {integ.integration_type}  category: {integ.category}",
    ]
    if integ.group_name:
        lines.append(f"  group: {integ.group_name}")
    lines.append(f"  enabled state: {_status_value(integ)}")

    if fresh is not None:
        lines.append(f"  health: {fresh.get('status', 'unknown')}")
        lines.append(
            f"  assets: {int(fresh.get('assetCount') or 0):,}  "
            f"(query_assets mode=list filter \"Data Source\"='{integ.name}' "
            "to list them)"
        )
        lines.append(f"  last sync: {fresh.get('lastSyncDisplay', 'Never')}")
        lines.append(f"  last run: {fresh.get('lastRunDisplay', 'Never')}")
        lines.append(f"  confidence: {fresh.get('confidence', 0)}%")
    else:
        last_sync = (
            integ.last_sync_at.isoformat()
            if getattr(integ, "last_sync_at", None) else "never"
        )
        lines.append(f"  last sync: {last_sync}")
        lines.append(
            "  (asset count + freshness unavailable — dashboard service "
            "not wired)"
        )

    if integ.last_sync_status:
        lines.append(f"  last sync status: {integ.last_sync_status}")
        if (integ.last_sync_status or "").lower() == "failed" and integ.last_error:
            lines.append(f"  last error: {truncate(str(integ.last_error), 200)}")

    if integ.last_connection_test_status:
        lines.append(f"  connection test: {integ.last_connection_test_status}")
        if (integ.last_connection_test_status != "success"
                and integ.last_connection_test_message):
            lines.append(
                "  connection error: "
                + truncate(str(integ.last_connection_test_message), 200)
            )
    return "\n".join(lines)


def _render_config(integ) -> str:
    config: Dict[str, Any] = integ.config or {}
    lines = [
        f"# Integration config — {integ.name} (id={integ.id})",
        f"  type: {integ.integration_type}  category: {integ.category}  "
        f"source_method: {integ.source_method}",
        "",
        "## Connection config",
    ]
    if config:
        # Redact the WHOLE structure (incl. nested headers/auth) before render.
        redacted = _redact_config(config)
        for key in sorted(redacted.keys()):
            lines.append(f"  {key}: {truncate(_stringify(redacted[key]), 200)}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("## Field mapping")
    lines.extend(_render_map_block(integ.field_mapping or {}, sep=" -> "))

    lines.append("")
    lines.append("## Transformations")
    lines.extend(_render_map_block(integ.transformations or {}, sep=": "))

    data_filters = integ.data_filters or []
    lines.append("")
    lines.append(f"## Data filters ({len(data_filters)})")
    if data_filters:
        for filt in data_filters[:_MAX_CONFIG_ROWS]:
            lines.append(f"  - {truncate(_stringify(filt), 200)}")
        if len(data_filters) > _MAX_CONFIG_ROWS:
            lines.append(f"  (+{len(data_filters) - _MAX_CONFIG_ROWS} more)")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _render_map_block(mapping: Dict[str, Any], *, sep: str) -> List[str]:
    if not mapping:
        return ["  (none)"]
    items = list(mapping.items())
    out = [f"  {k}{sep}{truncate(_stringify(v), 120)}"
           for k, v in items[:_MAX_CONFIG_ROWS]]
    if len(items) > _MAX_CONFIG_ROWS:
        out.append(f"  (+{len(items) - _MAX_CONFIG_ROWS} more)")
    return out


def _render_stats(integration_service, integ) -> str:
    stats = integration_service.batch_repository.get_batch_stats(integ.id) or {}
    lines = [
        f"# Integration stats — {integ.name} (id={integ.id})",
        f"  batches: {stats.get('total_batches', 0)} total "
        f"({stats.get('completed', 0)} completed, "
        f"{stats.get('failed', 0)} failed, "
        f"{stats.get('running', 0)} running, "
        f"{stats.get('partial', 0)} partial)",
        f"  success rate: {(stats.get('success_rate') or 0):.1f}%",
        f"  raw records ingested: {stats.get('total_records', 0)}",
        f"  records mapped: {stats.get('total_mapped', 0)}",
        f"  assets created: {stats.get('total_assets_created', 0)}",
        f"  assets updated: {stats.get('total_assets_updated', 0)}",
    ]
    last = stats.get("last_successful_batch")
    if last:
        lines.append(
            f"  last successful batch: {last.get('id')} at "
            f"{last.get('completed_at')} "
            f"({last.get('raw_records_count', 0)} records)"
        )
    return "\n".join(lines)
