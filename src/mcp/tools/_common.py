# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for MCP tool implementations."""

from __future__ import annotations

import json
import re
import uuid as _uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


# Canonical map of chat-facing time_range enums to Postgres INTERVAL
# literals. One source of truth — duplicating this per-tool has bitten us
# (see git log: "24h" -> "1 day" vs "24 hours", which both work but drifted).
# Tools SHOULD reject values outside this set rather than silently fall back;
# see resolve_time_range_or_error.
TIME_RANGE_TO_INTERVAL: Dict[str, str] = {
    "24h": "24 hours",
    "7d": "7 days",
    "30d": "30 days",
    "90d": "90 days",
}


def resolve_time_range_or_error(
    time_range: str,
    allowed: Optional[Iterable[str]] = None,
) -> Union[Tuple[str, None], Tuple[None, str]]:
    """Map a time_range enum to a PG interval literal, or return an error.

    A prior commit (a0291dd8) fixed "silent time_range fallbacks" — the
    earlier code would coerce unknown inputs to a 7d default and silently
    answer for the wrong window. This helper keeps that invariant across
    every tool.

    Pass ``allowed`` to restrict the set per tool (e.g. get_alerts accepts
    only 24h/7d/30d, not 90d).

    Returns ``(interval, None)`` on success, ``(None, error_message)``
    otherwise.
    """
    keys = list(allowed) if allowed is not None else list(TIME_RANGE_TO_INTERVAL.keys())
    if time_range not in keys:
        return None, unknown_enum("time_range", time_range, keys)
    return TIME_RANGE_TO_INTERVAL[time_range], None


def unknown_enum(param: str, value: Any, allowed: Iterable[str]) -> str:
    """Single-source error string for "value not in allowed set" conditions.

    Tools previously rolled their own and drifted between "Unknown <x>..
    Valid: ..." / "Known: ..." / "Valid values: ..." — the LLM wastes
    retries parsing the variation. Centralising fixes drift.
    """
    allowed_list = sorted(a for a in allowed if a is not None)
    return (
        f"Unknown {param} {value!r}. "
        f"Valid values: {allowed_list!r}."
    )


def format_array(val: Any, *, empty: str = "") -> str:
    """Render a PG text[] / list value as a comma-separated string.

    ``empty`` is returned for None / empty list / empty string. Scalars
    fall through to str(). Used across asset formatters that mix
    text[]-backed columns (hostname, manufacturer) with scalars.
    """
    if val is None:
        return empty
    if isinstance(val, list):
        joined = ", ".join(str(v) for v in val if v)
        return joined or empty
    s = str(val)
    return s if s else empty


def format_hostname(row: Dict[str, Any], *, fallback_key: str = "id") -> str:
    """Best-effort display string for an asset row's hostname.

    Handles the three shapes in circulation: a text[] from the assets
    table, a bare scalar from a denormalized query, and the None case
    where we fall back to the row's UUID (so the LLM always sees a
    stable identifier for the row).
    """
    host = row.get("hostname")
    if isinstance(host, list) and host:
        return ", ".join(str(h) for h in host if h) or str(row.get(fallback_key) or "(no hostname)")
    if host:
        return str(host)
    return str(row.get(fallback_key) or "(no hostname)")


def parse_filters_arg(
    filters: Any,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Parse a filters argument accepting either a Python list or a JSON
    string, returning (parsed_list, None) or (None, error_message).

    The chat often serialises filters as a JSON string-in-string; older
    callers pass a list directly. Handling both in one helper eliminates
    the three near-identical blocks at the top of list_assets_impl,
    aggregate_assets_impl, and get_field_values_impl.
    """
    if filters is None:
        return None, None
    if isinstance(filters, list):
        return filters, None
    if isinstance(filters, str):
        try:
            parsed = json.loads(filters)
        except json.JSONDecodeError as e:
            return None, f"Error: invalid JSON in filters parameter: {e}"
        if not isinstance(parsed, list):
            return (
                None,
                "Error: filters must be a JSON array of "
                "{field, op, value} objects.",
            )
        return parsed, None
    return (
        None,
        f"Error: filters must be a list or JSON string; got "
        f"{type(filters).__name__}.",
    )


def validate_uuid(value: str, entity: str) -> Optional[str]:
    """Return an LLM-facing error message if ``value`` isn't a UUID, else None.

    Tools surface validation errors as plain strings (never exceptions) so the
    LLM can correct itself. Pre-validating here keeps the DB out of the path
    when the argument is obviously malformed.
    """
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return (f"Invalid {entity} ID: {value!r}. "
                "Expected a UUID like '550e8400-e29b-41d4-a716-446655440000'.")
    return None


# Canonical text-preview row cap for list-shaped chat tools. The full data
# always lives in the artifact (or a CSV handoff); the LLM-facing TEXT shows at
# most this many rows so a large result can't blow the context window. Mirrors
# query_assets._CHAT_LIST_TEXT_ROWS — kept in sync as the single budget knob the
# plan's §8 #2/#5 asked to centralise. Tools with a smaller natural cap (e.g. a
# 14-row trend) keep theirs; this is the default ceiling for unbounded loops.
CHAT_TEXT_ROW_CAP = 25


def overflow_note(
    total: int, shown: int, *, noun: str = "rows", hint: str = "narrow with a filter",
) -> Optional[str]:
    """The canonical "N more not shown" overflow signal, or None when nothing is hidden.

    A capped list that prints only ``shown`` of ``total`` is a *different, narrower*
    answer than the full set — silently truncating it lets the model reason over a
    partial view as if it were complete (a correctness AND over-disclosure risk, plan
    §4). Every tool that caps its text MUST emit this so the omission is loud and the
    model knows how to widen. Single-sourced here so the wording never drifts (the
    same reason ``unknown_enum`` exists).

    ``total`` is the TRUE count (a real COUNT / window count), ``shown`` the number of
    rows actually rendered into the text. ``noun`` is used VERBATIM (pass the plural
    form, e.g. "sources" / "batches" / "categories") — the count is always ≥1 and the
    helper doesn't guess English plurals. Returns e.g.
    ``"[12 more sources not shown — page with offset.]"``.
    """
    hidden = int(total) - int(shown)
    if hidden <= 0:
        return None
    return f"[{hidden} more {noun} not shown — {hint}.]"


def truncate(s: Optional[str], max_chars: int) -> str:
    """Shorten ``s`` to ``max_chars`` chars, appending ``...`` when cut.

    Returns an empty string when ``s`` is falsy. The ``...`` marker is included
    in the final length so the result never exceeds ``max_chars``.
    """
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    if max_chars <= 3:
        return s[:max_chars]
    return s[:max_chars - 3] + "..."


# Tokens that mark a setting as sensitive. Matched word-boundary via _TOKEN_RE
# (not substring) so benign names like PRIMARY_KEY / CACHE_KEY aren't redacted.
_SENSITIVE_TOKENS = {"password", "secret", "token", "api_key", "apikey",
                     "credential", "credentials"}
_TOKEN_RE = re.compile(r"[^0-9a-z]+")


def is_sensitive_key(name: str) -> bool:
    """Return True when ``name`` looks like it holds a credential.

    Splits ``name`` on non-alphanumeric boundaries (underscores, hyphens, dots)
    and matches whole tokens against a known sensitive-token set. This avoids
    the ``"key" in lowered`` false positives (``PRIMARY_KEY``, ``CACHE_KEY``,
    ``LICENSE_KEY_LABEL``) that a substring check would produce.
    """
    if not name:
        return False
    tokens = {t for t in _TOKEN_RE.split(name.lower()) if t}
    return bool(tokens & _SENSITIVE_TOKENS)
