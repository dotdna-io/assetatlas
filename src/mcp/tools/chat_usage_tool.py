# SPDX-License-Identifier: AGPL-3.0-or-later
"""get_chatbot_usage — chatbot self-observability (read-only, admin).

Wraps ``ChatRepository.usage_summary`` — the exact query behind
``GET /chat/admin/usage`` (``src/api/blueprints/chat/routes.py``) — so an admin
can ask the assistant about its own activity ("token spend by model", "which
tools error most") in chat. No raw SQL here: it delegates to the repository,
the same object the route uses.

This ``*_impl`` helper is registered for the chat assistant in
``src/api/blueprints/chat/tool_bindings.py``. It is intentionally kept off the
standalone MCP server (chat and MCP diverge by design — see
tasks/chatbot-tool-parity.md Open Decision #6).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.mcp.tools._common import unknown_enum

# Surface area the chat tool advertises. The backend route clamps ``days`` to
# [1, 90]; chat mirrors the UI's presets (ChatbotUsageTab offers 7/30/90, the
# plan adds 1) so the model picks a known window rather than an arbitrary int.
_ALLOWED_DAYS: Tuple[int, ...] = (1, 7, 30, 90)

# Caps for the LLM-facing text preview. The full rows still ride the artifact
# (tool_calls) so nothing is lost — only the in-context text is bounded.
_TOP_USERS_SHOWN = 10
_TOP_PROVIDERS_SHOWN = 15
_TOP_TOOLS_SHOWN = 25
_RECENT_DOWNVOTES_SHOWN = 5

# Type alias mirrors the (text, artifact) contract the ToolRegistry accepts.
ToolReturn = Tuple[str, Optional[Dict[str, Any]]]


def _coerce_days(days: Any) -> Tuple[Optional[int], Optional[str]]:
    """Coerce ``days`` to an allowed int, or return a loud error string.

    Accepts a stringified int (LLMs sometimes send "7" for an integer arg) but
    rejects anything outside the advertised preset set — a silent clamp would
    answer for a *different* window than asked (the §4 loud-over-silent rule).
    """
    try:
        value = int(days)
    except (TypeError, ValueError):
        return None, unknown_enum("days", days, [str(d) for d in _ALLOWED_DAYS])
    if value not in _ALLOWED_DAYS:
        return None, unknown_enum("days", value, [str(d) for d in _ALLOWED_DAYS])
    return value, None


def _err_rate(count: int, errors: int) -> float:
    """Error rate as a percentage; 0.0 when a tool was never called."""
    return (errors / count * 100.0) if count else 0.0


def get_chatbot_usage_impl(chat_repo, *, days: int = 7) -> ToolReturn:
    """Aggregated chatbot activity for the last ``days`` days.

    Returns ``(text, artifact)``: a compact text summary (totals, top users,
    token spend per provider/model, tool-call reliability, recent downvotes)
    plus a ``table`` artifact ranking tool calls by volume with a computed
    error rate.
    """
    if chat_repo is None:
        return "Chatbot usage is not available (chat service not configured).", None

    resolved_days, err = _coerce_days(days)
    if err:
        return err, None

    data = chat_repo.usage_summary(days=resolved_days) or {}

    window = data.get("window_days", resolved_days)
    totals: Dict[str, Any] = data.get("totals") or {}
    top_users: List[Dict[str, Any]] = data.get("top_users") or []
    by_provider: List[Dict[str, Any]] = data.get("by_provider") or []
    tool_calls: List[Dict[str, Any]] = data.get("tool_calls") or []
    downvotes: List[Dict[str, Any]] = data.get("recent_downvotes") or []

    tokens_in = int(totals.get("tokens_input") or 0)
    tokens_out = int(totals.get("tokens_output") or 0)

    lines: List[str] = [
        f"# Chatbot usage — last {window} day(s)",
        "",
        "## Totals",
        f"- Messages: {int(totals.get('messages') or 0):,}",
        f"- Sessions: {int(totals.get('sessions') or 0):,}",
        f"- Active users: {int(totals.get('active_users') or 0):,}",
        f"- Tokens: {tokens_in:,} in / {tokens_out:,} out "
        f"({tokens_in + tokens_out:,} total)",
    ]

    lines.append("")
    lines.append("## Top users (by messages)")
    if top_users:
        for u in top_users[:_TOP_USERS_SHOWN]:
            name = u.get("username") or f"user {u.get('user_id')}"
            lines.append(f"- {name}: {int(u.get('messages') or 0):,} messages")
        if len(top_users) > _TOP_USERS_SHOWN:
            lines.append(f"- (+{len(top_users) - _TOP_USERS_SHOWN} more)")
    else:
        lines.append("- (no activity)")

    lines.append("")
    lines.append("## Token spend by provider / model")
    if by_provider:
        for p in by_provider[:_TOP_PROVIDERS_SHOWN]:
            # `or '?'` (not .get default): usage_summary always SELECTs these
            # columns, so the key is present but the value can be NULL for
            # legacy/error sessions — a .get default would never fire.
            lines.append(
                f"- {p.get('provider') or '?'}/{p.get('model') or '?'}: "
                f"{int(p.get('input_tokens') or 0):,} in / "
                f"{int(p.get('output_tokens') or 0):,} out "
                f"({int(p.get('messages') or 0):,} messages)"
            )
        if len(by_provider) > _TOP_PROVIDERS_SHOWN:
            lines.append(f"- (+{len(by_provider) - _TOP_PROVIDERS_SHOWN} more)")
    else:
        lines.append("- (no model usage)")

    lines.append("")
    lines.append("## Tool calls (by volume)")
    if tool_calls:
        for t in tool_calls[:_TOP_TOOLS_SHOWN]:
            count = int(t.get("count") or 0)
            errors = int(t.get("error_count") or 0)
            lines.append(
                f"- {t.get('tool_name', '?')}: {count:,} calls, "
                f"{errors} errors ({_err_rate(count, errors):.1f}%), "
                f"{int(t.get('timeout_count') or 0)} timeouts, "
                f"{int(t.get('denied_count') or 0)} denied, "
                f"avg {int(t.get('avg_duration_ms') or 0)} ms"
            )
        if len(tool_calls) > _TOP_TOOLS_SHOWN:
            lines.append(f"- (+{len(tool_calls) - _TOP_TOOLS_SHOWN} more)")
    else:
        lines.append("- (no tool calls)")

    if downvotes:
        lines.append("")
        lines.append(f"## Recent downvotes ({len(downvotes)})")
        for d in downvotes[:_RECENT_DOWNVOTES_SHOWN]:
            who = d.get("username") or f"user {d.get('user_id')}"
            comment = d.get("comment") or "(no comment)"
            lines.append(f"- [{d.get('created_at', '?')}] {who}: {comment}")
        if len(downvotes) > _RECENT_DOWNVOTES_SHOWN:
            lines.append(f"- (+{len(downvotes) - _RECENT_DOWNVOTES_SHOWN} more)")

    artifact = _tool_calls_artifact(tool_calls, window)
    return "\n".join(lines), artifact


def _tool_calls_artifact(
    tool_calls: List[Dict[str, Any]], window: int,
) -> Optional[Dict[str, Any]]:
    """A ``table`` artifact ranking tool calls by volume, with error rate.

    Chat artifacts are limited to donut/stacked_bar/table (validate_envelope
    drops anything else), so the reliability ranking rides a table — the most
    actionable "which tools error most" view, sortable in the UI.
    """
    if not tool_calls:
        return None
    series: List[Dict[str, Any]] = []
    for t in tool_calls:
        count = int(t.get("count") or 0)
        errors = int(t.get("error_count") or 0)
        series.append({
            "tool_name": t.get("tool_name", "?"),
            "count": count,
            "error_count": errors,
            "err_rate": round(_err_rate(count, errors), 1),
            "timeout_count": int(t.get("timeout_count") or 0),
            "denied_count": int(t.get("denied_count") or 0),
            "avg_duration_ms": int(t.get("avg_duration_ms") or 0),
        })
    return {
        "type": "table",
        "title": f"Chatbot tool calls — last {window} day(s)",
        "columns": [
            {"key": "tool_name", "label": "Tool"},
            {"key": "count", "label": "Calls"},
            {"key": "error_count", "label": "Errors"},
            {"key": "err_rate", "label": "Error %"},
            {"key": "timeout_count", "label": "Timeouts"},
            {"key": "denied_count", "label": "Denied"},
            {"key": "avg_duration_ms", "label": "Avg ms"},
        ],
        "series": series,
        "meta": {
            "source_tool": "get_chatbot_usage",
            "layout": "list",
            "window_days": window,
        },
    }
