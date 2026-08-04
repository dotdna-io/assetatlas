# SPDX-License-Identifier: AGPL-3.0-or-later
"""System settings tools for the AssetAtlas MCP/chat server.

Tools: get_settings

Redaction follows the SERVER's authoritative secret-marking rather than a
re-derived key-name allowlist:

- ``CATEGORY_HOOKS[category].audit_redact(key)`` — the per-category hook the
  settings blueprint uses to decide a key is a secret
  (``src/api/blueprints/settings/category_hooks.py``).
- ``is_encrypted(value)`` — the ``ENC:`` marker the ``/api/settings/email``
  read route trusts; any value stored encrypted is a secret regardless of its
  key name (closes the leak for keys like ``aws_access_key_id`` /
  ``signing_key`` / ``passphrase`` that the old 7-token allowlist missed).
- ``is_sensitive_key(key)`` — the prior key-name heuristic, retained ONLY as an
  additive safety net, so this can only ever redact more, never less.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.blueprints.settings.category_hooks import CATEGORY_HOOKS
from src.mcp.context import AppContext
from src.mcp.tools._common import is_sensitive_key, truncate, unknown_enum
from src.modules.integrations.shared.encryption import is_encrypted

_DESC_TRUNC_CHARS = 80

# Views supported by get_settings. ``values`` is the live settings; the rest
# mirror dedicated settings-blueprint surfaces.
_ALLOWED_VIEWS = ("values", "schema", "reliability", "email",
                  "security-effective")

# Generic, non-leaky failure message. NEVER format a raw exception ({e}) into
# tool output — it can carry connection strings / hostnames into a shared chat
# transcript (cross-cutting §4). Detail goes to the server log instead.
_UNAVAILABLE = "Settings unavailable (see server logs)."

# Mirror src/api/blueprints/settings/settings_routes.py:EMAIL_PASSWORD_SENTINEL.
_EMAIL_PASSWORD_SENTINEL = "__SET__"

logger = logging.getLogger(__name__)


def register_tools(mcp):
    """Register system-settings tools on the FastMCP server."""

    @mcp.tool()
    def get_settings(
        category: str | None = None,
        key: str | None = None,
        view: str = "values",
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return system settings.

        view=values (default): all categories, one category, or a single key.
        view=schema: setting definitions (type/default/range/options).
        view=reliability: per-source reliability scores.
        view=email: SMTP config (password redacted to a sentinel).
        view=security-effective: resolved public_base_url + its source.
        """
        settings_manager = ctx.request_context.lifespan_context.settings_manager
        if settings_manager is None:
            return "Settings manager not available."
        return get_settings_impl(
            settings_manager, category=category, key=key, view=view,
        )


# --------------------------------------------------------------------------- #
# Redaction (server-authoritative)                                            #
# --------------------------------------------------------------------------- #

def _is_secret_setting(category: str | None, key: str, value: Any) -> bool:
    """True when the SERVER treats this setting as a secret.

    Order mirrors the server's own signals; any one is sufficient.
    """
    # 1) Per-category hook declaration (authoritative). audit_redact() is pure
    #    for every registered hook (no Flask request context needed).
    hooks = CATEGORY_HOOKS.get(category) if category else None
    if hooks is not None:
        try:
            if hooks.audit_redact(key):
                return True
        except Exception:
            # A misbehaving hook must never cause a value to be EXPOSED — fall
            # through to the remaining (also-redacting) checks.
            logger.warning("audit_redact failed for %s.%s", category, key,
                           exc_info=True)
    # 2) Value stored encrypted at rest — never surface ciphertext, whatever
    #    the key is named (same is_encrypted the /settings/email route trusts).
    if isinstance(value, str) and is_encrypted(value):
        return True
    # 3) Additive key-name safety net (never the sole authority).
    return is_sensitive_key(key)


def _redact(category: str | None, key: str, value: Any) -> Any:
    return "<redacted>" if _is_secret_setting(category, key, value) else value


def _cell(value: Any) -> str:
    """Escape a value for a single markdown table cell.

    Flatten newlines and escape pipes so a value containing ``|`` can't break
    the table the LLM parses — e.g. the real ``ldap.user_search_filter``
    default ``(&(objectClass=person)(|(sAMAccountName={username})...)))``.
    """
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


# --------------------------------------------------------------------------- #
# Entry point + view dispatch                                                 #
# --------------------------------------------------------------------------- #

def get_settings_impl(
    settings_manager,
    *,
    category: str | None = None,
    key: str | None = None,
    view: str = "values",
) -> str:
    if settings_manager is None:
        return "Settings manager not available."

    if view not in _ALLOWED_VIEWS:
        return unknown_enum("view", view, _ALLOWED_VIEWS)

    if view == "schema":
        return _render_schema(settings_manager, category)
    if view == "reliability":
        return _render_reliability(settings_manager)
    if view == "email":
        return _render_email(settings_manager)
    if view == "security-effective":
        return _render_security_effective(settings_manager)

    # view == "values"
    if category is None and key is not None:
        return "Must specify 'category' when requesting a specific 'key'."

    if category is not None and key is not None:
        return _render_single_key(settings_manager, category, key)

    if category is not None:
        return _render_one_category(settings_manager, category)

    return _render_all_categories(settings_manager)


# --------------------------------------------------------------------------- #
# values view                                                                 #
# --------------------------------------------------------------------------- #

def _render_all_categories(settings_manager) -> str:
    try:
        data: Dict[str, Dict[str, Dict[str, Any]]] = settings_manager.get_all() or {}
    except Exception:
        logger.exception("get_settings: get_all() failed")
        return _UNAVAILABLE

    if not data:
        return "No settings defined."

    lines: List[str] = ["# System settings", ""]
    for cat in sorted(data.keys()):
        entries = data[cat] or {}
        lines.append(f"## {cat} ({len(entries)} settings)")
        lines.append("| Key | Value | Default | Description |")
        lines.append("| --- | --- | --- | --- |")
        for k in sorted(entries.keys()):
            entry = entries[k] or {}
            value = _cell(_redact(cat, k, entry.get("value")))
            default = _cell(_redact(cat, k, entry.get("default")))
            desc = _cell(truncate(entry.get("description"), _DESC_TRUNC_CHARS) or "—")
            lines.append(f"| {k} | {value} | {default} | {desc} |")
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_one_category(settings_manager, category: str) -> str:
    try:
        data: Dict[str, Dict[str, Dict[str, Any]]] = (
            settings_manager.get_all(category=category) or {}
        )
    except Exception:
        logger.exception("get_settings: get_all(category=%s) failed", category)
        return _UNAVAILABLE

    entries = (data.get(category) or {}) if data else {}
    if not entries:
        return f"No settings found for category '{category}'."

    lines: List[str] = [f"# Settings — {category}"]
    lines.append("| Key | Value | Default | Type | Description |")
    lines.append("| --- | --- | --- | --- | --- |")
    for k in sorted(entries.keys()):
        entry = entries[k] or {}
        value = _cell(_redact(category, k, entry.get("value")))
        default = _cell(_redact(category, k, entry.get("default")))
        vtype = entry.get("type") or "—"
        desc = _cell(truncate(entry.get("description"), _DESC_TRUNC_CHARS) or "—")
        lines.append(f"| {k} | {value} | {default} | {vtype} | {desc} |")

    return "\n".join(lines).rstrip()


def _render_single_key(settings_manager, category: str, key: str) -> str:
    try:
        value = settings_manager.get(category, key)
    except Exception:
        logger.exception("get_settings: get(%s, %s) failed", category, key)
        return _UNAVAILABLE

    entry: Dict[str, Any] = {}
    try:
        data = settings_manager.get_all(category=category) or {}
        entry = (data.get(category) or {}).get(key) or {}
    except Exception:
        logger.exception("get_settings: get_all(category=%s) failed", category)
        return _UNAVAILABLE

    if value is None and not entry:
        return f"Setting {category}.{key} not found."

    if value is None:
        value = entry.get("value")

    if value is None and not entry.get("default") and not entry.get("description"):
        return f"Setting {category}.{key} not found."

    display_value = _redact(category, key, value)
    default = _redact(category, key, entry.get("default"))
    vtype = entry.get("type") or "—"
    desc = entry.get("description") or "—"
    updated_at = entry.get("updated_at") or "never"
    updated_by = entry.get("updated_by") or "—"

    lines = [
        f"# {category}.{key}",
        f"- value: {display_value}",
        f"- default: {default}",
        f"- type: {vtype}",
        f"- description: {desc}",
        f"- updated: {updated_at}",
        f"- updated by: {updated_by}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# schema view                                                                 #
# --------------------------------------------------------------------------- #

def _render_schema(settings_manager, category: str | None) -> str:
    try:
        schema: Dict[str, Dict[str, Dict[str, Any]]] = (
            settings_manager.get_schema() or {}
        )
    except Exception:
        logger.exception("get_settings: get_schema() failed")
        return _UNAVAILABLE

    if category is not None:
        cat_schema = schema.get(category)
        if not cat_schema:
            return f"No schema found for category '{category}'."
        schema = {category: cat_schema}

    if not schema:
        return "No settings schema defined."

    lines: List[str] = ["# Settings schema", ""]
    for cat in sorted(schema.keys()):
        defs = schema[cat] or {}
        lines.append(f"## {cat} ({len(defs)} settings)")
        lines.append("| Key | Type | Default | Min | Max | Options | Description |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for k in sorted(defs.keys()):
            d = defs[k] or {}
            vtype = d.get("type") or "—"
            default = _cell(_redact(cat, k, d.get("default")))
            vmin = d.get("min") if d.get("min") is not None else "—"
            vmax = d.get("max") if d.get("max") is not None else "—"
            options = d.get("options")
            options_str = _cell(", ".join(str(o) for o in options)) if options else "—"
            desc = _cell(truncate(d.get("description"), _DESC_TRUNC_CHARS) or "—")
            lines.append(
                f"| {k} | {vtype} | {default} | {vmin} | {vmax} "
                f"| {options_str} | {desc} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# reliability view (asset_generation.source_reliability_scores)               #
# --------------------------------------------------------------------------- #

def _render_reliability(settings_manager) -> str:
    try:
        scores = settings_manager.get(
            "asset_generation", "source_reliability_scores"
        )
    except Exception:
        logger.exception("get_settings: reliability lookup failed")
        return _UNAVAILABLE

    if not isinstance(scores, dict) or not scores:
        return "No source reliability scores configured."

    lines: List[str] = [
        "# Source reliability scores",
        "_Per data-source-category trust weight (0.0–1.0); higher = more "
        "trusted._",
        "",
        "| Source category | Reliability |",
        "| --- | --- |",
    ]
    # Highest trust first, then name for stable ordering on ties.
    for source, score in sorted(
        scores.items(), key=lambda kv: (-_as_float(kv[1]), str(kv[0]))
    ):
        lines.append(f"| {_cell(source)} | {score} |")
    return "\n".join(lines)


def _as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# email view (mirror GET /api/settings/email redaction)                       #
# --------------------------------------------------------------------------- #

def _render_email(settings_manager) -> str:
    try:
        data = settings_manager.get_all(category="email") or {}
    except Exception:
        logger.exception("get_settings: email lookup failed")
        return _UNAVAILABLE

    entries = (data.get("email") or {}) if data else {}
    if not entries:
        return "No email settings configured."

    lines: List[str] = [
        "# Email (SMTP) settings",
        "| Key | Value |",
        "| --- | --- |",
    ]
    for k in sorted(entries.keys()):
        entry = entries[k] or {}
        value = entry.get("value") if isinstance(entry, dict) else entry
        if k == "smtp_password":
            # Mirror the blueprint: ENC ciphertext -> sentinel, else cleared.
            value = (
                _EMAIL_PASSWORD_SENTINEL
                if (isinstance(value, str) and is_encrypted(value))
                else ""
            )
        else:
            # Any OTHER email key still flows through the authoritative
            # redaction so a future encrypted field can't leak as raw ciphertext.
            value = _redact("email", k, value)
        lines.append(f"| {k} | {_cell(value)} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# security-effective view (mirror GET /api/settings/security/effective)       #
# --------------------------------------------------------------------------- #

def _render_security_effective(settings_manager) -> str:
    try:
        from src.api.utils.public_base_url import resolve_public_base_url

        db_value = (
            settings_manager.get("security", "public_base_url", "") or ""
        ).strip()
        resolved = resolve_public_base_url(settings_manager)
    except Exception:
        logger.exception("get_settings: security-effective resolve failed")
        return _UNAVAILABLE

    if db_value:
        source = "setting"
    elif resolved:
        source = "env"
    else:
        source = "none"

    return "\n".join([
        "# Security — effective values",
        f"- public_base_url: {resolved or '(unset)'}",
        f"- source: {source}",
    ])
