# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL layer for the chat_providers table.

One row per provider. Stored values are opaque to the repository — the
service handles api_key encryption and active-provider toggling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from src.database.postgres_adapter import PostgreSQLAdapter

from .models import CHAT_PROVIDERS, ChatProvider


@dataclass
class ProviderRow:
    """Mirrors a row in chat_providers."""

    provider: ChatProvider
    model: str
    api_key_encrypted: str
    base_url: str
    prompt_cache: bool
    thinking_enabled: bool
    foundry_protocol: str = ""   # '' for non-foundry rows


# Columns that ``upsert`` is allowed to touch — keeps callers honest.
_UPDATABLE_COLUMNS = {
    "model", "api_key_encrypted", "base_url",
    "prompt_cache", "thinking_enabled",
    "foundry_protocol",
}


class ChatProvidersRepository:
    """CRUD for the chat_providers table."""

    def __init__(self, db: PostgreSQLAdapter):
        self.db = db

    def list_all(self) -> Dict[ChatProvider, ProviderRow]:
        """Return all provider rows keyed by provider, including any
        seeded-but-empty rows so the admin UI always has all three cards."""
        sql = (
            "SELECT provider, model, api_key_encrypted, base_url, "
            "       prompt_cache, thinking_enabled, foundry_protocol "
            "  FROM chat_providers"
        )
        rows = self.db.execute_query(sql) or []
        return {r["provider"]: _row_from_dict(r) for r in rows
                if r["provider"] in CHAT_PROVIDERS}

    def get_updated_at(self, provider: ChatProvider) -> Optional[datetime]:
        """Return only the row's last-modified timestamp.

        Cheap staleness probe for every chat turn — used by ChatService to
        detect that another gunicorn worker (or external SQL) edited the
        active provider's row since this worker built its in-memory client.
        """
        sql = (
            "SELECT updated_at FROM chat_providers "
            " WHERE provider = %(provider)s"
        )
        rows = self.db.execute_query(sql, {"provider": provider}) or []
        return rows[0]["updated_at"] if rows else None

    def get_active_fingerprint_raw(
        self,
    ) -> Tuple[Optional[str], Optional[datetime]]:
        """Read the active-provider name (from ``system_settings``) and
        its row's ``updated_at`` (from ``chat_providers``) in one shot.

        Bypasses :class:`SettingsManager` deliberately — that cache has a
        30 s TTL and is only invalidated on the worker that wrote the
        setting, so other workers would observe a stale active-provider
        name and skip the rebuild they actually needed. Reading
        ``system_settings`` directly here closes that window to
        Postgres' MVCC visibility (microseconds within the same DB).

        Returns the raw stored value as-is; callers normalise unknown
        values to the chat default. ``(None, None)`` means no
        ``chat.active_provider`` row exists yet.
        """
        sql = (
            "SELECT ss.value         AS active_provider, "
            "       cp.updated_at    AS active_updated_at "
            "  FROM (SELECT value FROM system_settings "
            "         WHERE category = 'chat' "
            "           AND key = 'active_provider') ss "
            "  LEFT JOIN chat_providers cp "
            "    ON cp.provider = ss.value"
        )
        rows = self.db.execute_query(sql) or []
        if not rows:
            return (None, None)
        return (rows[0]["active_provider"], rows[0]["active_updated_at"])

    def get(self, provider: ChatProvider) -> Optional[ProviderRow]:
        sql = (
            "SELECT provider, model, api_key_encrypted, base_url, "
            "       prompt_cache, thinking_enabled, foundry_protocol "
            "  FROM chat_providers "
            " WHERE provider = %(provider)s"
        )
        rows = self.db.execute_query(sql, {"provider": provider}) or []
        return _row_from_dict(rows[0]) if rows else None

    def upsert(self, provider: ChatProvider,
               fields: Dict[str, Any]) -> ProviderRow:
        """UPDATE the named columns on the row for ``provider``.

        The migration seeds one row per provider, so this is always an
        UPDATE in practice. Refuses unknown columns and empty payloads to
        keep the SQL safe from caller typos."""
        bad = set(fields) - _UPDATABLE_COLUMNS
        if bad:
            raise ValueError(f"Unknown columns: {sorted(bad)}")
        if not fields:
            raise ValueError("upsert called with no fields")

        set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
        params = {**fields, "provider": provider}
        sql = (
            f"UPDATE chat_providers "
            f"   SET {set_clause}, updated_at = NOW() "
            f" WHERE provider = %(provider)s "
            f"RETURNING provider, model, api_key_encrypted, base_url, "
            f"          prompt_cache, thinking_enabled, foundry_protocol"
        )
        rows = self.db.execute_query(sql, params) or []
        if not rows:
            raise ValueError(f"Unknown provider {provider!r}")
        return _row_from_dict(rows[0])


def _row_from_dict(d: Dict[str, Any]) -> ProviderRow:
    return ProviderRow(
        provider=d["provider"],
        model=d.get("model") or "",
        api_key_encrypted=d.get("api_key_encrypted") or "",
        base_url=d.get("base_url") or "",
        prompt_cache=bool(d.get("prompt_cache", True)),
        thinking_enabled=bool(d.get("thinking_enabled", True)),
        foundry_protocol=d.get("foundry_protocol") or "",
    )
