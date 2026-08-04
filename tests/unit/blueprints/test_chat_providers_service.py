# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ChatProvidersService and supporting models.

These cover the core business logic — encryption roundtrip, active-provider
toggle, admin DTO assembly — without touching a real database. The repository
is mocked; integration tests for the SQL layer live elsewhere.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.admin_config.models import (
    AdminChatConfig, ChatProvider, ProviderConfigUpdate,
)
from src.api.blueprints.chat.admin_config.repository import ProviderRow
from src.api.blueprints.chat.admin_config.service import ChatProvidersService
from src.modules.integrations.shared.encryption import encrypt, is_encrypted


def _row(
    provider: ChatProvider = "anthropic",
    model: str = "claude-sonnet-4-6",
    api_key_encrypted: str = "",
    base_url: str = "",
    prompt_cache: bool = True,
    thinking_enabled: bool = True,
) -> ProviderRow:
    return ProviderRow(
        provider=provider,
        model=model,
        api_key_encrypted=api_key_encrypted,
        base_url=base_url,
        prompt_cache=prompt_cache,
        thinking_enabled=thinking_enabled,
    )


def _make_service(rows=None, active="anthropic"):
    """Build a service with a mocked repo (in-memory dict) and settings manager."""
    rows = rows or [
        _row("anthropic", model="claude-sonnet-4-6"),
        _row("openai", model="gpt-4o-mini"),
        _row("openai_compat", model=""),
    ]
    repo = MagicMock(name="ChatProvidersRepository")
    by_provider = {r.provider: r for r in rows}

    def list_all():
        return dict(by_provider)

    def get(provider):
        return by_provider.get(provider)

    def upsert(provider, fields):
        existing = by_provider[provider]
        merged = {**existing.__dict__, **fields, "provider": provider}
        new = ProviderRow(**merged)
        by_provider[provider] = new
        return new

    repo.list_all.side_effect = list_all
    repo.get.side_effect = get
    repo.upsert.side_effect = upsert

    sm = MagicMock(name="settings_manager")
    state = {"active_provider": active}

    def sm_get(category, key, default=None):
        if category == "chat" and key == "active_provider":
            return state["active_provider"]
        return default

    def sm_set(category, key, value, **_kw):
        if category == "chat" and key == "active_provider":
            state["active_provider"] = value
            return True, None
        return True, None

    sm.get.side_effect = sm_get
    sm.set.side_effect = sm_set

    return ChatProvidersService(repo=repo, settings_manager=sm), repo, sm, state


def test_chat_providers_includes_bedrock():
    from src.api.blueprints.chat.admin_config.models import CHAT_PROVIDERS
    assert "aws_bedrock" in CHAT_PROVIDERS


def test_bedrock_test_overlay_uses_draft_region_and_keys_when_unsaved():
    # Regression: the /test route dropped every AWS field, so testing a
    # first-time Bedrock config (no saved row) validated nothing the operator
    # typed and always failed with "AWS region is required". The overlay must
    # carry the draft region + freshly-entered keys straight through.
    svc, _repo, _sm, _state = _make_service(rows=[_row("anthropic")])
    draft = ProviderConfigUpdate(
        aws_region="us-west-2",
        auth_mode="access_keys",
        access_key_id="AKIA_DRAFT",
        secret_access_key="secret_draft",
    )
    overlay = svc.bedrock_test_overlay(draft)
    assert overlay["CHAT_LLM_AWS_REGION"] == "us-west-2"
    assert overlay["CHAT_LLM_AWS_AUTH"] == {
        "auth_mode": "access_keys",
        "access_key_id": "AKIA_DRAFT",
        "secret_access_key": "secret_draft",
    }


def test_bedrock_test_overlay_keeps_saved_secret_when_draft_omits_it():
    # Editing region only (the secret left "unchanged", so the UI omits it)
    # must validate against the SAVED secret, not blank it — and the draft
    # region must still win over the saved one. Otherwise Test silently
    # validates stale credentials.
    saved_blob = {
        "auth_mode": "access_keys",
        "access_key_id": "AKIA_SAVED",
        "secret_access_key": "secret_saved",
    }
    bedrock_row = _row(
        "aws_bedrock",
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key_encrypted=encrypt(json.dumps(saved_blob)),
        base_url="eu-central-1",
    )
    svc, _repo, _sm, _state = _make_service(
        rows=[_row("anthropic"), bedrock_row],
    )
    draft = ProviderConfigUpdate(aws_region="us-east-1", auth_mode="access_keys")
    overlay = svc.bedrock_test_overlay(draft)
    assert overlay["CHAT_LLM_AWS_REGION"] == "us-east-1"
    assert overlay["CHAT_LLM_AWS_AUTH"]["access_key_id"] == "AKIA_SAVED"
    assert overlay["CHAT_LLM_AWS_AUTH"]["secret_access_key"] == "secret_saved"


class TestProviderConfigUpdateModel:
    def test_partial_fields_allowed(self):
        u = ProviderConfigUpdate(api_key="sk-abc")
        assert u.api_key == "sk-abc"
        assert u.model is None
        assert u.prompt_cache is None

    def test_empty_payload_is_valid(self):
        u = ProviderConfigUpdate()
        assert u.model_dump(exclude_none=True) == {}


class TestGetActiveProvider:
    def test_returns_setting_value(self):
        svc, _, _, _ = _make_service(active="openai")
        assert svc.get_active_provider() == "openai"

    def test_default_when_unset(self):
        svc, _, sm, _ = _make_service()
        sm.get.side_effect = lambda *a, **k: None
        # No setting present → service falls back to "anthropic" default.
        assert svc.get_active_provider() == "anthropic"

    def test_invalid_value_falls_back(self):
        svc, _, sm, _ = _make_service()
        sm.get.side_effect = lambda *a, **k: "bedrock"
        assert svc.get_active_provider() == "anthropic"

    def test_returns_none_when_disabled(self):
        # The 'none' sentinel means the admin turned every provider off;
        # it is distinct from "unset" (which falls back to the default).
        svc, _, sm, _ = _make_service()
        sm.get.side_effect = lambda *a, **k: "none"
        assert svc.get_active_provider() is None


class TestSetActiveProvider:
    def test_persists_setting(self):
        svc, _, sm, state = _make_service(active="anthropic")
        svc.set_active_provider(
            "openai", user_id="u1", username="alice", ip="1.2.3.4",
        )
        assert state["active_provider"] == "openai"
        assert sm.set.call_count == 1

    def test_rejects_unknown_provider(self):
        svc, _, _, _ = _make_service()
        with pytest.raises(ValueError, match="Invalid provider"):
            svc.set_active_provider(
                "bedrock", user_id="u1", username="alice", ip=None,
            )

    def test_none_disables_all_providers(self):
        # Passing None persists the 'none' sentinel (chat disabled), without
        # tripping the known-provider guard.
        svc, _, sm, state = _make_service(active="anthropic")
        svc.set_active_provider(
            None, user_id="u1", username="alice", ip=None,
        )
        assert state["active_provider"] == "none"
        assert sm.set.call_count == 1


class TestIsConfigured:
    """The DB-backed answer to "should the UI show the chat widget".

    /api/health used to report `app.chat_service is not None`, which is a
    per-gunicorn-worker fact. With four workers, a first-time provider save
    left three of them reporting chat unavailable, so the widget stayed hidden
    and the user could never send the request that heals a cold worker.
    """

    def test_true_when_the_active_provider_has_credentials(self):
        svc, _, _, _ = _make_service(
            rows=[_row("anthropic", api_key_encrypted=encrypt("sk-real"))],
            active="anthropic",
        )
        assert svc.is_configured() is True

    def test_false_on_a_fresh_install_with_no_api_key(self):
        """The trap: get_active_provider falls back to the DEFAULT provider
        name when the setting is missing, so a name alone proves nothing —
        only that its credentials resolve does."""
        svc, _, _, _ = _make_service(
            rows=[_row("anthropic", api_key_encrypted="")], active=None,
        )
        assert svc.get_active_provider() is not None, (
            "precondition: the fallback still names a provider"
        )
        assert svc.is_configured() is False

    def test_false_when_every_provider_is_explicitly_disabled(self):
        svc, _, _, _ = _make_service(
            rows=[_row("anthropic", api_key_encrypted=encrypt("sk-real"))],
            active="none",
        )
        assert svc.is_configured() is False


class TestGetActiveConfigFingerprint:
    """Verify the fingerprint tracks both the active toggle AND the
    active row's updated_at — equality is what other gunicorn workers
    use to decide whether to rebuild their in-memory LLM client."""

    def test_changes_when_active_toggle_changes(self):
        from datetime import datetime, timezone
        svc, repo, _, _ = _make_service(active="anthropic")
        # Each fingerprint call reads (active_provider, row_updated_at)
        # in one DB hit; the fingerprint still has to change because the
        # active provider name flipped.
        repo.get_active_fingerprint_raw.side_effect = [
            ("anthropic", datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)),
            ("openai", datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)),
        ]
        fp1 = svc.get_active_config_fingerprint()
        fp2 = svc.get_active_config_fingerprint()
        assert fp1 != fp2
        assert fp1[0] == "anthropic" and fp2[0] == "openai"

    def test_changes_when_active_row_updated_at_changes(self):
        from datetime import datetime, timezone
        svc, repo, _, _ = _make_service(active="anthropic")
        repo.get_active_fingerprint_raw.side_effect = [
            ("anthropic", datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)),
            # Simulate an admin edit on the active row by advancing its ts.
            ("anthropic", datetime(2026, 5, 15, 12, 0, 1, tzinfo=timezone.utc)),
        ]
        fp1 = svc.get_active_config_fingerprint()
        fp2 = svc.get_active_config_fingerprint()
        assert fp1 != fp2
        assert fp1[0] == fp2[0] == "anthropic"

    def test_handles_missing_setting_falls_back_to_default(self):
        """If ``system_settings.chat.active_provider`` is missing or holds
        an unknown value, the fingerprint must mirror the same fallback
        ``get_active_provider`` uses — otherwise the cached fingerprint
        on first build wouldn't match what make_llm_client built against."""
        from datetime import datetime, timezone
        svc, repo, _, _ = _make_service()
        repo.get_active_fingerprint_raw.return_value = (None, None)
        repo.get_updated_at.return_value = datetime(
            2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc,
        )
        fp = svc.get_active_config_fingerprint()
        assert fp[0] == "anthropic"  # _DEFAULT_PROVIDER
        repo.get_updated_at.assert_called_once_with("anthropic")

    def test_follows_db_not_settings_manager(self):
        """Cross-worker staleness detection requires the freshest possible
        read. SettingsManager has a 30 s per-process cache only invalidated
        on the worker that called ``set``; if the fingerprint relied on it,
        other workers would observe a stale active-provider name and skip
        the rebuild they actually needed.

        Regression guard: stub the settings manager to return a different
        provider than the DB does and assert the fingerprint follows the
        DB.
        """
        from datetime import datetime, timezone
        svc, repo, _, state = _make_service(active="anthropic")
        # SettingsManager caches "anthropic" (the value at last refresh).
        state["active_provider"] = "anthropic"
        # The DB has been updated to "openai_compat" by another worker.
        repo.get_active_fingerprint_raw.return_value = (
            "openai_compat",
            datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        fp = svc.get_active_config_fingerprint()
        assert fp[0] == "openai_compat", (
            "fingerprint must reflect the DB, not the SettingsManager cache"
        )


class TestUpdateProvider:
    def test_encrypts_api_key_on_write(self):
        svc, repo, _, _ = _make_service()
        svc.update_provider(
            "anthropic", ProviderConfigUpdate(api_key="sk-plain"),
        )
        # Repo received an encrypted value, never the plaintext.
        kwargs = repo.upsert.call_args.kwargs
        fields = kwargs.get("fields") or repo.upsert.call_args.args[1]
        assert is_encrypted(fields["api_key_encrypted"])
        assert "sk-plain" not in fields["api_key_encrypted"]

    def test_empty_api_key_clears_stored_key(self):
        svc, repo, _, _ = _make_service(rows=[
            _row("anthropic", api_key_encrypted="ENC:already-set"),
            _row("openai"),
            _row("openai_compat"),
        ])
        svc.update_provider("anthropic", ProviderConfigUpdate(api_key=""))
        fields = repo.upsert.call_args.args[1]
        assert fields["api_key_encrypted"] == ""

    def test_skips_api_key_when_not_supplied(self):
        svc, repo, _, _ = _make_service(rows=[
            _row("anthropic", api_key_encrypted="ENC:keep-this"),
            _row("openai"),
            _row("openai_compat"),
        ])
        svc.update_provider(
            "anthropic", ProviderConfigUpdate(model="claude-opus-4-7"),
        )
        fields = repo.upsert.call_args.args[1]
        assert "api_key_encrypted" not in fields  # untouched
        assert fields["model"] == "claude-opus-4-7"

    def test_partial_update_only_passes_supplied_fields(self):
        svc, repo, _, _ = _make_service()
        svc.update_provider(
            "openai_compat",
            ProviderConfigUpdate(base_url="http://localhost:8000",
                                 thinking_enabled=False),
        )
        fields = repo.upsert.call_args.args[1]
        assert set(fields.keys()) == {"base_url", "thinking_enabled"}
        assert fields["base_url"] == "http://localhost:8000"
        assert fields["thinking_enabled"] is False

    def test_rejects_unknown_provider(self):
        svc, _, _, _ = _make_service()
        with pytest.raises(ValueError, match="Invalid provider"):
            svc.update_provider(
                "bedrock",  # type: ignore[arg-type]
                ProviderConfigUpdate(model="x"),
            )


class TestGetAdminConfig:
    def test_returns_all_providers(self):
        svc, _, _, _ = _make_service()
        cfg = svc.get_admin_config(chat_enabled=True)
        assert isinstance(cfg, AdminChatConfig)
        assert set(cfg.providers.keys()) == {
            "anthropic", "openai", "openai_compat", "aws_bedrock", "foundry",
        }

    def test_active_provider_reflected(self):
        svc, _, _, _ = _make_service(active="openai")
        cfg = svc.get_admin_config(chat_enabled=True)
        assert cfg.active_provider == "openai"

    def test_chat_enabled_passthrough(self):
        svc, _, _, _ = _make_service()
        assert svc.get_admin_config(chat_enabled=False).chat_enabled is False
        assert svc.get_admin_config(chat_enabled=True).chat_enabled is True

    def test_api_key_masked_when_set(self):
        from src.modules.integrations.shared.encryption import encrypt
        svc, _, _, _ = _make_service(rows=[
            _row("anthropic",
                 api_key_encrypted=encrypt("sk-something-secret-1234")),
            _row("openai"),
            _row("openai_compat"),
        ])
        cfg = svc.get_admin_config(chat_enabled=True)
        ant = cfg.providers["anthropic"]
        assert ant.api_key_configured is True
        assert ant.api_key_preview.startswith("•")
        # Never leak the start of the plaintext key — only last 4 chars.
        assert "sk-something" not in ant.api_key_preview
        assert ant.api_key_preview.endswith("1234")

    def test_api_key_blank_when_unset(self):
        svc, _, _, _ = _make_service()
        cfg = svc.get_admin_config(chat_enabled=True)
        op = cfg.providers["openai"]
        assert op.api_key_configured is False
        assert op.api_key_preview == ""

    def test_preview_is_last_4_of_decrypted_key(self):
        svc, _, _, _ = _make_service()
        # Save a real plaintext key so decrypt yields something stable.
        svc.update_provider(
            "anthropic", ProviderConfigUpdate(api_key="sk-abcdefXYZ9"),
        )
        cfg = svc.get_admin_config(chat_enabled=True)
        assert cfg.providers["anthropic"].api_key_preview.endswith("XYZ9")


class TestGetEffectiveConfig:
    def test_returns_chat_llm_dict_for_active_provider(self):
        svc, _, _, _ = _make_service()
        # Plant an api key.
        svc.update_provider(
            "anthropic", ProviderConfigUpdate(api_key="sk-aaa-bbb"),
        )
        eff = svc.get_effective_config("anthropic")
        assert eff["CHAT_LLM_PROVIDER"] == "anthropic"
        assert eff["CHAT_LLM_MODEL"] == "claude-sonnet-4-6"
        assert eff["CHAT_LLM_API_KEY"] == "sk-aaa-bbb"
        assert eff["CHAT_PROMPT_CACHE"] is True

    def test_decrypts_plaintext_legacy_value(self):
        # Backfilled rows from migration 104 may carry plaintext api keys
        # because old chat_llm_api_key was never encrypted. Service should
        # treat unprefixed values as already-decrypted.
        svc, _, _, _ = _make_service(rows=[
            _row("anthropic", api_key_encrypted="legacy-plaintext-key"),
            _row("openai"),
            _row("openai_compat"),
        ])
        eff = svc.get_effective_config("anthropic")
        assert eff["CHAT_LLM_API_KEY"] == "legacy-plaintext-key"

    def test_raises_when_active_api_key_empty(self):
        svc, _, _, _ = _make_service()  # No api keys set
        with pytest.raises(ValueError, match="API key"):
            svc.get_effective_config("anthropic")

    def test_openai_compat_includes_base_url(self):
        svc, _, _, _ = _make_service()
        svc.update_provider(
            "openai_compat",
            ProviderConfigUpdate(api_key="x", base_url="http://vllm:8000",
                                 model="qwen3"),
        )
        eff = svc.get_effective_config("openai_compat")
        assert eff["CHAT_LLM_BASE_URL"] == "http://vllm:8000"
        assert eff["CHAT_LLM_THINKING_ENABLED"] is True
        assert eff["CHAT_LLM_MODEL"] == "qwen3"

    def test_openai_compat_allows_empty_api_key(self):
        # Self-hosted vLLM/Ollama deployments don't need a key.
        svc, _, _, _ = _make_service()
        svc.update_provider(
            "openai_compat",
            ProviderConfigUpdate(base_url="http://vllm:8000", model="qwen3"),
        )
        eff = svc.get_effective_config("openai_compat")
        assert eff["CHAT_LLM_API_KEY"] == ""
        assert eff["CHAT_LLM_BASE_URL"] == "http://vllm:8000"
