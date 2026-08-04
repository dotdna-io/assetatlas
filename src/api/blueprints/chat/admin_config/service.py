# SPDX-License-Identifier: AGPL-3.0-or-later
"""ChatProvidersService — business logic for per-provider chat config.

Wraps :class:`ChatProvidersRepository` with:

* api_key encryption on write, decryption on read.
* Active-provider toggle stored in ``system_settings`` under
  ``chat.active_provider``.
* DTO assembly for the admin UI (masking api keys; never leaks ciphertext).
* Effective-config builder that emits the same ``CHAT_LLM_*`` dict shape the
  existing :func:`make_llm_client` factory consumes.

AWS Bedrock packs its credentials into a JSON blob written to
``api_key_encrypted`` (single encrypted column reused). The blob's shape
depends on ``auth_mode``:

* ``{"auth_mode": "access_keys", "access_key_id": "...", "secret_access_key": "..."}``
* ``{"auth_mode": "assume_role", "role_arn": "..."}``

The Bedrock region piggybacks on ``base_url`` (column reuse — different
meaning per provider, no schema change required).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from src.modules.integrations.shared.encryption import (
    decrypt, encrypt, is_encrypted,
)

from .models import (CHAT_PROVIDERS, AdminChatConfig, ChatProvider,
                     ProviderConfig, ProviderConfigUpdate)
from .repository import ChatProvidersRepository, ProviderRow

logger = logging.getLogger(__name__)


_DEFAULT_PROVIDER: ChatProvider = "anthropic"
# Sentinel stored in the `chat.active_provider` setting when the admin turns
# every provider off. Distinct from "unset" (which falls back to the default).
_NONE_PROVIDER = "none"


class ProviderNotConfiguredError(ValueError):
    """The provider is incompletely set up — not a malfunction.

    Every fresh install raises this from `get_effective_config` before an admin
    has entered an API key, so callers that treat it as a failure end up logging
    an ERROR with a full traceback for the expected state (#1104). The point of
    the distinct type is to let a caller separate "nobody has configured this
    yet" from "configuration exists and something went wrong reading it", which
    a bare ValueError cannot express.

    Subclasses ValueError deliberately: several callers already catch that (and
    the broader `except Exception`), so this narrows the signal without changing
    anyone's control flow.
    """


class ChatProvidersService:
    """Coordinates the chat_providers table and the active-provider setting."""

    def __init__(self, repo: ChatProvidersRepository,
                 settings_manager: Any) -> None:
        self.repo = repo
        self.settings_manager = settings_manager

    # ------------------------------------------------------------------
    # Active-provider toggle
    # ------------------------------------------------------------------

    def get_active_provider(self) -> Optional[ChatProvider]:
        value = self.settings_manager.get("chat", "active_provider", None)
        if value == _NONE_PROVIDER:
            return None  # every provider explicitly disabled
        if value in CHAT_PROVIDERS:
            return value  # type: ignore[return-value]
        return _DEFAULT_PROVIDER

    def is_configured(self) -> bool:
        """Whether chat is usable, as a DB fact rather than a per-process one.

        ``/api/health`` reports this as ``features.chat``, which the UI reads to
        decide whether to mount the chat widget. It used to report
        ``app.chat_service is not None``, which answers a different question —
        whether the gunicorn worker that happened to serve that poll had already
        built its service. After a first-time provider save that was true of one
        worker in four, so the widget usually stayed hidden, and the user could
        never send the chat request that would have healed the others.

        Note ``get_active_provider`` falls back to the default provider name
        when the setting is missing, so a name alone proves nothing on a fresh
        install — only that its credentials resolve does.
        """
        active = self.get_active_provider()
        if active is None:
            return False
        try:
            self.get_effective_config(active)
        except ProviderNotConfiguredError:
            return False
        return True

    def get_active_config_fingerprint(self) -> Tuple[str, Optional[str]]:
        """Cheap staleness key for the in-memory LLM client.

        Returns ``(active_provider, active_row_updated_at_iso)``. The tuple
        changes when:

        * the admin toggles the active provider (first element flips), or
        * the admin edits the row of the currently-active provider (second
          element advances).

        Editing a non-active provider's row leaves the tuple unchanged, so
        we don't churn the live client for irrelevant edits. Every gunicorn
        worker calls this once per chat turn and rebuilds its client only
        when the value differs from the one it cached at the last build —
        which is how config writes from the PATCH-handling worker propagate
        to the others without a container restart.

        Reads ``system_settings`` directly via SQL rather than through
        :meth:`get_active_provider` — that path goes via SettingsManager,
        whose 30 s per-process cache would mask the active-provider flip
        on every worker that didn't handle the PATCH.
        """
        raw_active, raw_ts = self.repo.get_active_fingerprint_raw()
        if raw_active in CHAT_PROVIDERS:
            active = raw_active
            ts = raw_ts
        else:
            # Unknown or missing setting → fall back to the same default
            # get_active_provider() does, plus one extra round-trip for
            # its row's timestamp. Hit only on misconfig.
            active = _DEFAULT_PROVIDER
            ts = self.repo.get_updated_at(_DEFAULT_PROVIDER)
        return (active, ts.isoformat() if ts is not None else None)

    def set_active_provider(
        self, provider: Optional[ChatProvider], *, user_id: str,
        username: str, ip: Optional[str],
    ) -> None:
        # None disables every provider (persisted as the 'none' sentinel);
        # a concrete provider must be one we know how to build a client for.
        if provider is None:
            value = _NONE_PROVIDER
            reason = "All chat providers disabled via admin settings"
        else:
            self._require_known_provider(provider)
            value = provider
            reason = "Active chat provider switched via admin settings"
        ok, err = self.settings_manager.set(
            "chat", "active_provider", value,
            user_id=user_id, username=username,
            reason=reason,
            ip_address=ip,
        )
        if not ok:
            raise ValueError(f"Failed to set active provider: {err}")

    # ------------------------------------------------------------------
    # Per-provider config update
    # ------------------------------------------------------------------

    def update_provider(
        self, provider: ChatProvider, update: ProviderConfigUpdate,
    ) -> ProviderRow:
        """Apply a partial update to one provider's row.

        For non-Bedrock providers: ``api_key`` is plaintext from the UI and is
        encrypted before write; passing ``api_key=""`` explicitly clears it.
        Other ``aws_*`` fields are ignored.

        For ``aws_bedrock``: composes the JSON blob from ``auth_mode`` and the
        relevant per-mode fields, encrypts it, and writes to
        ``api_key_encrypted``. Sending ``auth_mode`` clears the *other* mode's
        fields. ``aws_region`` is persisted to ``base_url``."""
        self._require_known_provider(provider)

        if provider == "aws_bedrock":
            return self._update_bedrock(update)

        fields: Dict[str, Any] = {}
        if update.model is not None:
            fields["model"] = update.model
        if update.base_url is not None:
            fields["base_url"] = update.base_url
        if update.prompt_cache is not None:
            fields["prompt_cache"] = update.prompt_cache
        if update.thinking_enabled is not None:
            fields["thinking_enabled"] = update.thinking_enabled
        if update.foundry_protocol is not None:
            fields["foundry_protocol"] = update.foundry_protocol
        if update.api_key is not None:
            fields["api_key_encrypted"] = (
                encrypt(update.api_key) if update.api_key else ""
            )

        return self.repo.upsert(provider, fields)

    def _update_bedrock(self, update: ProviderConfigUpdate) -> ProviderRow:
        """Bedrock-specific update path. Composes the JSON auth blob from the
        current stored blob (if any) plus the inbound fields, handling auth-mode
        switches by dropping the other mode's fields."""
        existing = self.repo.get("aws_bedrock")
        existing_blob = _decode_bedrock_blob(
            existing.api_key_encrypted if existing else "",
        )
        target_mode = update.auth_mode or existing_blob.get("auth_mode")

        fields: Dict[str, Any] = {}
        if update.model is not None:
            fields["model"] = update.model
        if update.aws_region is not None:
            fields["base_url"] = update.aws_region
        if update.prompt_cache is not None:
            fields["prompt_cache"] = update.prompt_cache
        if update.thinking_enabled is not None:
            fields["thinking_enabled"] = update.thinking_enabled

        auth_touched = any(
            getattr(update, f) is not None
            for f in ("auth_mode", "access_key_id", "secret_access_key",
                      "role_arn", "external_id",
                      "base_access_key_id", "base_secret_access_key")
        )
        if auth_touched:
            blob = _compose_bedrock_blob(
                target_mode=target_mode,
                update=update,
                existing_blob=existing_blob,
            )
            if blob:
                fields["api_key_encrypted"] = encrypt(json.dumps(blob))
            else:
                fields["api_key_encrypted"] = ""

        if not fields:
            return existing or self.repo.get("aws_bedrock")  # type: ignore[return-value]
        return self.repo.upsert("aws_bedrock", fields)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get_admin_config(self, *, chat_enabled: bool) -> AdminChatConfig:
        """Build the full admin DTO. Decrypts each api_key only long enough
        to compute its preview, then drops the plaintext."""
        rows = self.repo.list_all()
        providers: Dict[ChatProvider, ProviderConfig] = {}
        for p in CHAT_PROVIDERS:
            row = rows.get(p) or _empty_row(p)
            providers[p] = _row_to_provider_config(row)
        return AdminChatConfig(
            active_provider=self.get_active_provider(),
            chat_enabled=chat_enabled,
            providers=providers,
        )

    def get_effective_config(self, provider: ChatProvider) -> Dict[str, Any]:
        """Return the ``CHAT_LLM_*`` dict ``make_llm_client`` expects.

        For non-Bedrock providers, decrypts the stored api key and emits the
        legacy six-key dict. For Bedrock, parses the JSON blob and emits two
        extra keys: ``CHAT_LLM_AWS_REGION`` and ``CHAT_LLM_AWS_AUTH``.

        Raises ``ProviderNotConfiguredError`` with an actionable message when
        required fields are missing (empty key for hosted providers, missing
        region/auth_mode for Bedrock) — the normal state before an admin has
        finished setup, and NOT something to log as a failure. A malformed
        Bedrock blob still raises a plain ``ValueError``: stored credentials
        that cannot be parsed are a real fault, not an unfinished form.
        """
        self._require_known_provider(provider)
        row = self.repo.get(provider)
        if row is None:
            raise ProviderNotConfiguredError(f"No configuration row for provider {provider!r}")

        if provider == "aws_bedrock":
            if not row.base_url:
                raise ProviderNotConfiguredError(
                    "AWS region is not set. Configure it under "
                    "Settings → Chatbot → AWS Bedrock."
                )
            blob = _decode_bedrock_blob(row.api_key_encrypted, raise_on_malformed=True)
            mode = blob.get("auth_mode")
            if mode not in ("access_keys", "assume_role"):
                raise ProviderNotConfiguredError(
                    "AWS Bedrock auth_mode is not set. Choose Access keys "
                    "or Assume role under Settings → Chatbot → AWS Bedrock."
                )
            if mode == "access_keys" and not (
                blob.get("access_key_id") and blob.get("secret_access_key")
            ):
                raise ProviderNotConfiguredError(
                    "AWS Bedrock access key and secret are required for "
                    "access-keys mode."
                )
            if mode == "assume_role" and not blob.get("role_arn"):
                raise ProviderNotConfiguredError(
                    "AWS Bedrock role ARN is required for assume-role mode."
                )
            return {
                "CHAT_LLM_PROVIDER": "aws_bedrock",
                "CHAT_LLM_MODEL": row.model,
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": row.prompt_cache,
                "CHAT_LLM_THINKING_ENABLED": row.thinking_enabled,
                "CHAT_LLM_AWS_REGION": row.base_url,
                "CHAT_LLM_AWS_AUTH": blob,
            }

        if provider == "foundry":
            return _foundry_effective_config(row)

        api_key = _decrypt_api_key(row.api_key_encrypted)
        if not api_key and provider != "openai_compat":
            raise ProviderNotConfiguredError(
                f"Provider {provider!r} has no API key configured. "
                f"Set one under Settings → Chatbot."
            )
        return {
            "CHAT_LLM_PROVIDER": provider,
            "CHAT_LLM_MODEL": row.model,
            "CHAT_LLM_API_KEY": api_key,
            "CHAT_LLM_BASE_URL": row.base_url,
            "CHAT_PROMPT_CACHE": row.prompt_cache,
            "CHAT_LLM_THINKING_ENABLED": row.thinking_enabled,
        }

    def bedrock_test_overlay(
        self, draft: "ProviderConfigUpdate",
    ) -> Dict[str, Any]:
        """CHAT_LLM_AWS_* overlay for a Bedrock connection test.

        The /test route's flat field overlay can't carry Bedrock's region or
        auth secrets (they compose an encrypted JSON blob, not env keys), so a
        pre-save Test would validate the *saved* credentials — or fail outright
        on first-time setup. Merge the draft's region + auth fields over the
        saved blob (the same ``_compose_bedrock_blob`` logic the save path
        uses) so Test validates what's on the form. Fields the operator left
        untouched fall back to the saved values; region falls back to the saved
        row.
        """
        existing = self.repo.get("aws_bedrock")
        existing_blob = _decode_bedrock_blob(
            existing.api_key_encrypted if existing else "",
        )
        target_mode = draft.auth_mode or existing_blob.get("auth_mode")
        blob = _compose_bedrock_blob(
            target_mode=target_mode, update=draft, existing_blob=existing_blob,
        )
        overlay: Dict[str, Any] = {}
        region = draft.aws_region
        if region is None and existing is not None:
            region = existing.base_url
        if region is not None:
            overlay["CHAT_LLM_AWS_REGION"] = region
        if blob:
            overlay["CHAT_LLM_AWS_AUTH"] = blob
        return overlay

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_known_provider(provider: Any) -> None:
        if provider not in CHAT_PROVIDERS:
            raise ValueError(
                f"Invalid provider {provider!r}; allowed: {list(CHAT_PROVIDERS)}"
            )


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _empty_row(provider: ChatProvider) -> ProviderRow:
    return ProviderRow(
        provider=provider, model="", api_key_encrypted="",
        base_url="", prompt_cache=True, thinking_enabled=True,
    )


def _decrypt_api_key(stored: str) -> str:
    """Return the plaintext api key, treating unprefixed values as already-
    plaintext. Old chat_llm_api_key rows backfilled by migration 104 are
    plaintext until the next save re-encrypts them."""
    if not stored:
        return ""
    if not is_encrypted(stored):
        return stored
    try:
        return decrypt(stored)
    except Exception:
        logger.error("Failed to decrypt stored chat api key; treating as unset",
                     exc_info=True)
        return ""


def _decode_bedrock_blob(
    stored: str, *, raise_on_malformed: bool = False,
) -> Dict[str, Any]:
    """Decrypt + parse the Bedrock JSON blob. Empty stored values return {}.

    If ``raise_on_malformed`` is true, propagates a ``ValueError`` with a
    clear message; otherwise returns ``{}`` so unconfigured rows still render
    in the admin UI."""
    plaintext = _decrypt_api_key(stored)
    if not plaintext:
        return {}
    try:
        parsed = json.loads(plaintext)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    if raise_on_malformed:
        raise ValueError(
            "AWS Bedrock credentials are malformed in the database; "
            "re-save them under Settings → Chatbot → AWS Bedrock."
        )
    return {}


def _compose_bedrock_blob(
    *, target_mode: Optional[str],
    update: ProviderConfigUpdate,
    existing_blob: Dict[str, Any],
) -> Dict[str, Any]:
    """Produce the new JSON blob to encrypt + write.

    Returns ``{}`` when the new state has no auth_mode at all.
    """
    if target_mode == "access_keys":
        base = existing_blob if existing_blob.get("auth_mode") == "access_keys" else {}
        return {
            "auth_mode": "access_keys",
            "access_key_id": (
                update.access_key_id
                if update.access_key_id is not None
                else base.get("access_key_id", "")
            ),
            "secret_access_key": (
                update.secret_access_key
                if update.secret_access_key is not None
                else base.get("secret_access_key", "")
            ),
        }
    if target_mode == "assume_role":
        base = existing_blob if existing_blob.get("auth_mode") == "assume_role" else {}
        return {
            "auth_mode": "assume_role",
            "role_arn": (
                update.role_arn
                if update.role_arn is not None
                else base.get("role_arn", "")
            ),
            "external_id": (
                update.external_id
                if update.external_id is not None
                else base.get("external_id", "")
            ),
            "base_access_key_id": (
                update.base_access_key_id
                if update.base_access_key_id is not None
                else base.get("base_access_key_id", "")
            ),
            "base_secret_access_key": (
                update.base_secret_access_key
                if update.base_secret_access_key is not None
                else base.get("base_secret_access_key", "")
            ),
        }
    return {}


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 4:
        return "••••"
    return "••••" + secret[-4:]


def _row_to_provider_config(row: ProviderRow) -> ProviderConfig:
    if row.provider == "aws_bedrock":
        blob = _decode_bedrock_blob(row.api_key_encrypted)
        mode = blob.get("auth_mode") if blob.get("auth_mode") in (
            "access_keys", "assume_role",
        ) else None
        access_key_id = blob.get("access_key_id", "")
        role_arn = blob.get("role_arn", "")
        secret = blob.get("secret_access_key", "")
        configured = bool(
            (mode == "access_keys" and access_key_id and secret)
            or (mode == "assume_role" and role_arn)
        )
        return ProviderConfig(
            provider="aws_bedrock",
            model=row.model,
            api_key_preview="",
            api_key_configured=configured,
            base_url="",
            prompt_cache=row.prompt_cache,
            thinking_enabled=row.thinking_enabled,
            aws_region=row.base_url,
            auth_mode=mode,  # type: ignore[arg-type]
            access_key_id_preview=_mask_secret(access_key_id),
            role_arn=role_arn,
            external_id=blob.get("external_id", ""),
            base_access_key_id_preview=_mask_secret(
                blob.get("base_access_key_id", "") or ""
            ),
        )

    if row.provider == "foundry":
        plaintext = _decrypt_api_key(row.api_key_encrypted)
        return ProviderConfig(
            provider="foundry",
            model=row.model,
            api_key_preview=_mask_secret(plaintext),
            api_key_configured=bool(plaintext),
            base_url=row.base_url,
            prompt_cache=row.prompt_cache,
            thinking_enabled=row.thinking_enabled,
            foundry_protocol=row.foundry_protocol or None,
        )

    plaintext = _decrypt_api_key(row.api_key_encrypted)
    return ProviderConfig(
        provider=row.provider,
        model=row.model,
        api_key_preview=_mask_secret(plaintext),
        api_key_configured=bool(plaintext),
        base_url=row.base_url,
        prompt_cache=row.prompt_cache,
        thinking_enabled=row.thinking_enabled,
    )


def _foundry_effective_config(row: ProviderRow) -> Dict[str, Any]:
    """Build the CHAT_LLM_* dict for the foundry provider.

    Validates that all four required fields (model, api_key, base_url,
    foundry_protocol) are present and raises ProviderNotConfiguredError with a
    clear message naming the missing field.
    """
    api_key = _decrypt_api_key(row.api_key_encrypted)
    if not row.foundry_protocol:
        raise ProviderNotConfiguredError(
            "Microsoft Foundry protocol is not set. Choose one under "
            "Settings → Chatbot → Microsoft Foundry."
        )
    if not row.base_url:
        raise ProviderNotConfiguredError(
            "Microsoft Foundry Target URL is not set. Configure it under "
            "Settings → Chatbot → Microsoft Foundry."
        )
    if not api_key:
        raise ProviderNotConfiguredError(
            "Microsoft Foundry API key is not set. Configure it under "
            "Settings → Chatbot → Microsoft Foundry."
        )
    return {
        "CHAT_LLM_PROVIDER": "foundry",
        "CHAT_LLM_MODEL": row.model,
        "CHAT_LLM_API_KEY": api_key,
        "CHAT_LLM_BASE_URL": row.base_url,
        "CHAT_LLM_FOUNDRY_PROTOCOL": row.foundry_protocol,
        "CHAT_PROMPT_CACHE": row.prompt_cache,
        "CHAT_LLM_THINKING_ENABLED": False,
    }
