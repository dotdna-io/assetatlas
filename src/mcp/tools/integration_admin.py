# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration ingestion-debug & catalog tools for the AssetAtlas chatbot.

Read-only, admin-gated tools that wrap the integration service/repository
layer (never raw SQL) to give the chatbot parity with the Integrations UI's
ingestion-debug and catalog surfaces:

- ``list_integration_batches`` — ingestion batch history for one integration.
- ``get_batch`` — one batch's detail / raw source records / mapped records.
- ``get_integration_mapping`` — raw->canonical mapping preview / raw sample /
  inferred field schema.
- ``list_integrations`` — configured integrations / groups / connector
  catalog / global batch-record stats.
- ``get_integration_schedule`` — cron schedule + next runs for one integration.

Every tool resolves an integration reference by id (UUID) OR by name
(case-insensitive). None of them mutate anything. Bad input (unknown
integration, unknown enum) returns a loud error STRING — never a silently
empty result that would read as "nothing is there".

These ``*_impl`` helpers are registered for the chat assistant in
``src/api/blueprints/chat/tool_bindings.py``. They are intentionally kept off
the standalone MCP server (chat and MCP diverge by design — see
tasks/chatbot-tool-parity.md Open Decision #6).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.modules.integrations.repositories.raw_asset_repository import (
    RawAssetRepository,
)
from src.modules.integrations.shared import cron_validator
from src.mcp.tools._common import (
    CHAT_TEXT_ROW_CAP,
    overflow_note,
    truncate,
    unknown_enum,
    validate_uuid,
)

# Batch lifecycle states. Sourced from the authoritative DB CHECK constraint
# `ingestion_batches_status_check` (running/mapping/completed/failed) plus
# 'partial', which mapping_service writes on a partial map. Validated so an
# unknown status filter errors loudly instead of returning an empty list that
# reads as "never ran" — but the set MUST stay complete, or a real status
# (e.g. 'mapping') gets falsely rejected.
_BATCH_STATUSES = ("running", "mapping", "completed", "failed", "partial")

_MAX_BATCH_LIMIT = 200   # mirrors GET /<id>/batches (batches.py)
_MAX_RECORD_LIMIT = 500  # mirrors GET /<id>/batches/<b>/records (batches.py)

_BATCH_VIEWS = ("detail", "raw", "mapped")

_MAPPING_VIEWS = ("mapping_preview", "raw_sample", "schema")
_MAPPING_STATUSES = ("all", "ok", "dropped")
_SCHEMA_SAMPLE_SIZE = 200  # mirrors GET /<id>/raw-data schema sample (batches.py)

_INTEGRATION_VIEWS = ("integrations", "groups", "connectors", "stats")

_MAX_SCHEDULE_COUNT = 20  # mirrors GET /<id>/schedule/next-runs (schedules.py)


def _resolve_integration(
    integration_service, ref: Any,
) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve an integration by id (UUID) or name (case-insensitive).

    Returns ``(integration, None)`` on success or ``(None, error_string)`` on
    failure. The error names the integrations that DO exist so the model can
    correct itself rather than be left with an empty result.
    """
    if ref is None or not str(ref).strip():
        return None, "An integration name or id is required."
    ref = str(ref).strip()

    # A UUID is never a display name — resolve it directly and don't fall back
    # to a name scan (which could mask a genuine wrong-id with a stale list).
    try:
        iid = uuid.UUID(ref)
    except (ValueError, AttributeError, TypeError):
        iid = None

    if iid is not None:
        integration = integration_service.get_integration(iid)
        if integration is not None:
            return integration, None
        return None, (
            f"No integration with id {ref}. "
            f"{_known_integrations_hint(integration_service)}"
        )

    integrations = integration_service.list_integrations() or []
    matches = [i for i in integrations if (i.name or "").lower() == ref.lower()]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        ids = ", ".join(f"{i.name} (id={i.id})" for i in matches)
        return None, (
            f"Multiple integrations are named {ref!r}; refer to one by id: {ids}."
        )
    return None, (
        f"No integration named {ref!r}. "
        f"{_known_integrations_hint(integration_service, integrations)}"
    )


def _known_integrations_hint(
    integration_service, integrations: Optional[List[Any]] = None,
) -> str:
    """A bounded, sorted list of configured integration names for error text."""
    if integrations is None:
        integrations = integration_service.list_integrations() or []
    names = sorted((i.name or "") for i in integrations if i.name)
    if not names:
        return "No integrations are configured."
    shown = names[:25]
    suffix = f" (+{len(names) - 25} more)" if len(names) > 25 else ""
    return "Known integrations: " + ", ".join(shown) + suffix + "."


def _overflow_note(offset: int, shown_count: int, total: int) -> Optional[str]:
    """The 'N more not shown' overflow line for a capped page, or None.

    One source of truth for the off-by-one so the three paged renderers
    (batches, batch records, mapping preview) can't drift.
    """
    remaining = total - (offset + shown_count)
    if remaining > 0:
        return f"{remaining} more not shown — page with offset."
    return None


def _duration_seconds(batch: Dict[str, Any]) -> Optional[int]:
    started = batch.get("started_at")
    completed = batch.get("completed_at")
    if started and completed:
        try:
            return int((completed - started).total_seconds())
        except (TypeError, AttributeError):
            return None
    return None


def list_integration_batches_impl(
    integration_service,
    *,
    integration: Any,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Ingestion batch history for one integration (status, timing, counts)."""
    integ, err = _resolve_integration(integration_service, integration)
    if err:
        return err

    if status is not None and status not in _BATCH_STATUSES:
        return unknown_enum("status", status, _BATCH_STATUSES)

    limit = max(1, min(int(limit), _MAX_BATCH_LIMIT))
    offset = max(0, int(offset))

    repo = integration_service.batch_repository
    batches = repo.list_batches(integ.id, limit, offset, status) or []
    total = repo.count_batches(integ.id, status)

    label = f"'{integ.name}' (id={integ.id})"
    filt = f" status={status}" if status else ""

    if total == 0:
        return (
            f"No batches found for integration {label}{filt}. "
            "Total: 0."
        )

    header = [
        f"Ingestion batches for {label}{filt}",
        f"Showing {len(batches)} of {total} (offset {offset}).",
    ]
    overflow = _overflow_note(offset, len(batches), total)
    if overflow:
        header.append(overflow)

    lines: List[str] = []
    for b in batches:
        dur = _duration_seconds(b)
        dur_str = f"{dur}s" if dur is not None else "—"
        # Full batch id — the model copies it verbatim into get_batch to drill
        # into this batch's records; a truncated id would break that hand-off.
        row = (
            f"- [{b.get('status', '?')}] {b.get('id')}  "
            f"started {b.get('started_at') or '—'}  "
            f"duration {dur_str}  "
            f"records {b.get('raw_records_count') or 0} / "
            f"mapped {b.get('mapped_records_count') or 0} / "
            f"assets {b.get('assets_created_count') or 0}"
        )
        error_message = b.get("error_message")
        if error_message:
            row += f"  — ERROR: {truncate(str(error_message), 200)}"
        lines.append(row)

    return "\n".join(header + [""] + lines)


def _format_batch_summary(batch: Dict[str, Any]) -> str:
    dur = _duration_seconds(batch)
    dur_str = f"{dur}s" if dur is not None else "—"
    lines = [
        f"Batch {batch.get('id')}",
        f"  integration_id: {batch.get('integration_id')}",
        f"  status: {batch.get('status', '?')}",
        f"  started: {batch.get('started_at') or '—'}  "
        f"completed: {batch.get('completed_at') or '—'}  duration: {dur_str}",
        f"  records: raw {batch.get('raw_records_count') or 0} / "
        f"mapped {batch.get('mapped_records_count') or 0} / "
        f"assets created {batch.get('assets_created_count') or 0}",
    ]
    updated = batch.get("assets_updated_count")
    if updated is not None:
        lines[-1] += f" / updated {updated}"
    error_message = batch.get("error_message")
    if error_message:
        lines.append(f"  ERROR: {truncate(str(error_message), 300)}")
    return "\n".join(lines)


def _compact_record(rec: Dict[str, Any]) -> str:
    """Render one record dict as compact key=value pairs (values truncated)."""
    parts: List[str] = []
    for key, value in rec.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            sval = json.dumps(value, default=str, ensure_ascii=False)
        else:
            sval = str(value)
        parts.append(f"{key}={truncate(sval, 200)}")
    return "  ".join(parts)


def _render_record_page(
    label: str, records: List[Dict[str, Any]], total: int, offset: int,
) -> str:
    if total == 0:
        return f"No {label} for this batch. Total: 0."
    header = [f"{label}: showing {len(records)} of {total} (offset {offset})."]
    overflow = _overflow_note(offset, len(records), total)
    if overflow:
        header.append(overflow)
    body = [
        f"#{offset + i + 1}  {_compact_record(rec)}"
        for i, rec in enumerate(records)
    ]
    return "\n".join(header + [""] + body)


def get_batch_impl(
    integration_service,
    *,
    batch_id: Any,
    view: str = "detail",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """One batch's detail, raw source records, or mapped records (paginated)."""
    err = validate_uuid(batch_id, "batch")
    if err:
        return err
    if view not in _BATCH_VIEWS:
        return unknown_enum("view", view, _BATCH_VIEWS)

    limit = max(1, min(int(limit), _MAX_RECORD_LIMIT))
    offset = max(0, int(offset))
    bid = uuid.UUID(str(batch_id))

    batch = integration_service.batch_repository.get_batch(bid)
    if batch is None:
        return f"Batch {batch_id} not found."

    summary = _format_batch_summary(batch)
    if view == "detail":
        return summary

    if view == "raw":
        records = integration_service.raw_data_repository.get_records(
            bid, limit, offset,
        ) or []
        total = integration_service.raw_data_repository.count_records(bid)
        body = _render_record_page("raw source records", records, total, offset)
    else:  # view == "mapped"
        repo = RawAssetRepository(integration_service.conn_manager)
        records = repo.get_by_batch(bid, limit, offset) or []
        total = repo.count_by_batch(bid)
        body = _render_record_page("mapped records", records, total, offset)

    return f"{summary}\n\n{body}"


def get_integration_mapping_impl(
    integration_service,
    mapping_preview_service,
    *,
    integration: Any,
    view: str = "mapping_preview",
    status: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Raw->canonical mapping preview, a raw sample, or the inferred schema."""
    integ, err = _resolve_integration(integration_service, integration)
    if err:
        return err
    if view not in _MAPPING_VIEWS:
        return unknown_enum("view", view, _MAPPING_VIEWS)

    limit = max(1, min(int(limit), _MAX_RECORD_LIMIT))
    offset = max(0, int(offset))
    label = f"'{integ.name}' (id={integ.id})"

    if view == "mapping_preview":
        return _render_mapping_preview(
            mapping_preview_service, integ, label, status, limit, offset,
        )
    return _render_raw_sample(integration_service, integ, label, view, limit, offset)


def _render_mapping_preview(
    mapping_preview_service, integ, label, status, limit, offset,
) -> str:
    if status not in _MAPPING_STATUSES:
        return unknown_enum("status", status, _MAPPING_STATUSES)
    if mapping_preview_service is None:
        return (
            "Mapping preview is not available "
            "(mapping preview service not configured)."
        )

    result = mapping_preview_service.get_mapping_preview(
        str(integ.id), limit=limit, offset=offset, status=status,
    ) or {}
    if not result.get("success", False):
        # ``error`` here is the service's own structured message
        # ("Integration not found"), not a raw exception — safe to surface.
        return (
            f"Mapping preview unavailable for {label}: "
            f"{result.get('error', 'unknown error')}."
        )

    total = result.get("total_records", 0)
    filtered = result.get("filtered_total", total)
    dropped = result.get("dropped_count", 0)
    columns = result.get("columns") or []
    source_map = result.get("source_map") or {}
    records = result.get("records") or []

    batch_id = result.get("batch_id")
    batch_part = f"Batch {batch_id}: " if batch_id else ""
    header = [
        f"Mapping preview for {label} [status={status}]",
        f"{batch_part}{total} raw records, "
        f"{dropped} would be DROPPED by the pipeline.",
        f"Matching status={status}: {filtered}.",
    ]
    note = result.get("error")
    if note:
        header.append(f"Note: {note}")
    if columns:
        header.append("Mapped columns: " + ", ".join(str(c) for c in columns))
    if source_map:
        pairs = [f"{k} -> {v}" for k, v in list(source_map.items())[:25]]
        header.append("Source map (raw -> canonical): " + "; ".join(pairs))

    if not records:
        header.append("\nNo records to preview.")
        return "\n".join(header)

    header.append(
        f"\nRecords (showing {len(records)} of {filtered}, offset {offset}):"
    )
    rec_lines: List[str] = []
    for rec in records:
        if rec.get("dropped"):
            reasons = rec.get("drop_reasons") or []
            flag = "DROPPED: " + ("; ".join(str(r) for r in reasons)
                                  or "unspecified")
        else:
            flag = "OK"
        rec_lines.append(f"#{rec.get('row_num', '?')} [{flag}]")
        raw = rec.get("raw")
        if raw is not None:
            rec_lines.append(
                "  raw: "
                + truncate(json.dumps(raw, default=str, ensure_ascii=False), 300)
            )
        mapped = rec.get("mapped")
        if mapped is not None:
            rec_lines.append(
                "  mapped: "
                + truncate(json.dumps(mapped, default=str, ensure_ascii=False), 300)
            )

    overflow = _overflow_note(offset, len(records), filtered)
    if overflow:
        rec_lines.append(f"\n{overflow}")
    return "\n".join(header + rec_lines)


def _render_raw_sample(integration_service, integ, label, view, limit, offset) -> str:
    from src.modules.integrations.services.field_schema import (
        compute_field_schema,
    )

    batch = integration_service.batch_repository.get_latest_completed_batch(
        integ.id,
    )
    if batch is None:
        return (
            f"No completed batch for {label} — nothing to sample yet. Total: 0."
        )
    bid = batch["id"]

    # Infer the schema from a fixed sample, mirroring the raw-data endpoint.
    sample = integration_service.raw_data_repository.get_records(
        bid, _SCHEMA_SAMPLE_SIZE, 0,
    ) or []
    schema = compute_field_schema(sample)
    schema_lines = [f"Inferred field schema ({len(schema)} fields):"]
    for field_desc in schema[:60]:
        schema_lines.append(
            f"- {field_desc.get('path')} ({field_desc.get('type')}, "
            f"{field_desc.get('coverage')}% coverage) "
            f"e.g. {truncate(str(field_desc.get('sample')), 80)}"
        )
    if len(schema) > 60:
        schema_lines.append(f"... {len(schema) - 60} more fields not shown.")

    if view == "schema":
        return (
            f"Field schema for {label} (batch {bid}):\n" + "\n".join(schema_lines)
        )

    # view == "raw_sample": a page of raw records plus the inferred schema.
    records = integration_service.raw_data_repository.get_records(
        bid, limit, offset,
    ) or []
    total = integration_service.raw_data_repository.count_records(bid)
    body = _render_record_page("raw source records", records, total, offset)
    return (
        f"Raw sample for {label} (batch {bid}):\n{body}\n\n"
        + "\n".join(schema_lines)
    )


def list_integrations_impl(
    integration_service,
    integration_group_service,
    *,
    view: str = "integrations",
) -> str:
    """The integration catalog: configured list / groups / connectors / stats."""
    if view not in _INTEGRATION_VIEWS:
        return unknown_enum("view", view, _INTEGRATION_VIEWS)

    if view == "integrations":
        return _render_integration_list(integration_service)
    if view == "groups":
        return _render_groups(integration_service, integration_group_service)
    if view == "connectors":
        return _render_connectors(integration_service)
    return _render_stats(integration_service)  # view == "stats"


def _status_str(integration: Any) -> str:
    status = integration.status
    return status.value if hasattr(status, "value") else str(status)


def _render_integration_list(integration_service) -> str:
    integrations = integration_service.list_integrations() or []
    lines = [f"Configured integrations ({len(integrations)}):"]
    if not integrations:
        lines.append("(none configured)")
        return "\n".join(lines)
    # Only scan every integration's batches once we know there's something to
    # annotate (the scan is wasted work on an empty fleet).
    latest = (
        integration_service.batch_repository
        .get_latest_batches_for_all_integrations() or {}
    )
    ordered = sorted(integrations, key=lambda x: (x.name or "").lower())
    for i in ordered[:CHAT_TEXT_ROW_CAP]:
        grp = f", group={i.group_name}" if i.group_name else ""
        lines.append(
            f"- {i.name} (id={i.id}) — type={i.integration_type}, "
            f"category={i.category}, status={_status_str(i)}{grp}"
        )
        batch = latest.get(str(i.id))
        if batch:
            last_run = (batch.get("last_run_at") or batch.get("last_sync_at")
                        or batch.get("started_at"))
            lines.append(
                f"    latest batch: {batch.get('raw_records_count', 0)} records "
                f"/ {batch.get('assets_created_count', 0)} assets "
                f"(status={batch.get('status', '?')}), last run {last_run or 'never'}"
            )
    note = overflow_note(
        len(ordered), min(len(ordered), CHAT_TEXT_ROW_CAP),
        noun="integrations", hint="narrow with `view='stats'` or ask by name")
    if note:
        lines.append(note)
    return "\n".join(lines)


def _render_groups(integration_service, integration_group_service) -> str:
    if integration_group_service is None:
        return (
            "Integration groups are not available "
            "(group service not configured)."
        )
    groups = integration_group_service.list_groups() or []
    integrations = integration_service.list_integrations() or []

    members_by_group: Dict[str, List[Any]] = {}
    for i in integrations:
        if i.group_id is not None:
            members_by_group.setdefault(str(i.group_id), []).append(i)

    lines = [f"Integration groups ({len(groups)}):"]
    if not groups:
        lines.append("(no groups configured)")
        return "\n".join(lines)
    for group in groups[:CHAT_TEXT_ROW_CAP]:
        gid = str(group.get("id"))
        lines.append(
            f"- {group.get('name')} (id={gid}) — "
            f"type={group.get('integration_type')}, "
            f"members={group.get('member_count', 0)}"
        )
        members = sorted(members_by_group.get(gid, []),
                         key=lambda x: (x.name or "").lower())
        # member_count (above) is the real per-group total; cap the rendered
        # members so one large group can't dominate the listing.
        for member in members[:CHAT_TEXT_ROW_CAP]:
            lines.append(
                f"    - {member.name} (id={member.id}, "
                f"status={_status_str(member)})"
            )
        mnote = overflow_note(
            len(members), min(len(members), CHAT_TEXT_ROW_CAP),
            noun="members", hint="query this group by id for the full roster")
        if mnote:
            lines.append("    " + mnote)
    gnote = overflow_note(
        len(groups), min(len(groups), CHAT_TEXT_ROW_CAP),
        noun="groups", hint="ask by group id")
    if gnote:
        lines.append(gnote)
    return "\n".join(lines)


def _config_schema_fields(schema: Any) -> List[str]:
    """Field names (with a required marker) from a connector config schema.

    Only field NAMES are surfaced — never values — so this carries no secrets.
    """
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    return [
        f"{name} (required)" if name in required else str(name)
        for name in props
    ]


def _render_connectors(integration_service) -> str:
    connectors = integration_service.get_available_connectors() or []
    lines = [f"Available connector types ({len(connectors)}):"]
    if not connectors:
        lines.append("(none registered)")
        return "\n".join(lines)
    ordered_conn = sorted(
        connectors,
        key=lambda c: str(c.get("display_name") or c.get("integration_type") or ""),
    )
    for connector in ordered_conn[:CHAT_TEXT_ROW_CAP]:
        flags: List[str] = []
        # connector_role ('technology' vs 'transport') is the canonical
        # direct-vs-routed indicator. direct_connector is just the type string
        # echoed for technology connectors (None for transport), so rendering
        # it separately would be redundant — and `"direct" if x else ...` would
        # be wrong since x is a string/None, never a bool.
        role = connector.get("connector_role")
        if role:
            flags.append(f"role={role}")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"- {connector.get('integration_type')}: "
            f"{connector.get('display_name')} "
            f"(category={connector.get('category')}){flag_str}"
        )
        fields = _config_schema_fields(connector.get("config_schema"))
        if fields:
            lines.append("    config: " + ", ".join(fields))
    cnote = overflow_note(
        len(ordered_conn), min(len(ordered_conn), CHAT_TEXT_ROW_CAP),
        noun="connector types", hint="ask for a specific connector type")
    if cnote:
        lines.append(cnote)
    return "\n".join(lines)


def _render_stats(integration_service) -> str:
    counts = integration_service.get_integration_stats() or {}
    batch = integration_service.batch_repository.get_global_batch_stats() or {}
    return "\n".join([
        "Integration stats:",
        f"  integrations: {counts.get('total', 0)} total "
        f"({counts.get('enabled', 0)} enabled, "
        f"{counts.get('disabled', 0)} disabled, "
        f"{counts.get('error', 0)} error)",
        f"  connector types available: {counts.get('available_connectors', 0)}",
        "",
        "Ingestion batch totals (all integrations):",
        f"  batches: {batch.get('total', 0)} total "
        f"({batch.get('completed', 0)} completed, "
        f"{batch.get('failed', 0)} failed, "
        f"{batch.get('partial', 0)} partial, "
        f"{batch.get('processing', 0)} running)",
        f"  raw records ingested: {batch.get('total_records', 0)}",
        f"  records mapped: {batch.get('total_mapped', 0)}",
        f"  assets created: {batch.get('total_assets_created', 0)}",
        f"  assets updated: {batch.get('total_assets_updated', 0)}",
        f"  success rate: {(batch.get('success_rate') or 0):.1f}%",
    ])


def get_integration_schedule_impl(
    integration_service,
    *,
    integration: Any,
    count: int = 5,
) -> str:
    """Cron schedule, timezone, stored next run, run state, and next N runs."""
    integ, err = _resolve_integration(integration_service, integration)
    if err:
        return err

    count = max(1, min(int(count), _MAX_SCHEDULE_COUNT))
    cron = integ.schedule_cron
    timezone = integ.schedule_timezone or "UTC"
    label = f"'{integ.name}' (id={integ.id})"

    lines = [
        f"Schedule for {label}",
        f"  enabled: {integ.schedule_enabled}",
        f"  cron: {cron or '(none)'}",
        f"  timezone: {timezone}",
        f"  stored next_run_at: {integ.next_run_at or 'none'}",
        f"  currently running: {integ.is_running}",
    ]

    if not cron:
        lines.append("")
        lines.append("No cron schedule configured for this integration.")
        return "\n".join(lines)

    description = cron_validator.get_cron_description(cron)
    if description:
        lines[2] = f"  cron: {cron} — {description}"

    next_runs = cron_validator.calculate_next_runs(cron, timezone, count) or []
    lines.append("")
    if next_runs:
        lines.append(f"Next {len(next_runs)} runs:")
        lines.extend(f"  - {run}" for run in next_runs)
    else:
        lines.append(
            "Next runs could not be computed (invalid cron or timezone)."
        )
    return "\n".join(lines)
