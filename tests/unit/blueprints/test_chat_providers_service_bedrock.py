# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for AWS Bedrock support in ChatProvidersService.

Mirrors the existing test_chat_providers_service.py style — mocked repo,
mocked settings manager, no real DB. Covers the JSON-blob compose/parse,
preview masking, auth-mode switching, and effective-config assembly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.admin_config.models import ProviderConfigUpdate
from src.api.blueprints.chat.admin_config.repository import ProviderRow
from src.api.blueprints.chat.admin_config.service import ChatProvidersService
from src.modules.integrations.shared.encryption import decrypt, is_encrypted


def _service_with_bedrock_row(api_key_encrypted="", base_url="", model=""):
    """Build a service whose repo has a single aws_bedrock row preloaded."""
    row = ProviderRow(
        provider="aws_bedrock", model=model,
        api_key_encrypted=api_key_encrypted, base_url=base_url,
        prompt_cache=True, thinking_enabled=False,
    )
    repo = MagicMock(name="ChatProvidersRepository")
    state = {"row": row}

    def get(provider):
        return state["row"] if provider == "aws_bedrock" else None

    def list_all():
        return {"aws_bedrock": state["row"]}

    def upsert(provider, fields):
        merged = {**state["row"].__dict__, **fields, "provider": provider}
        state["row"] = ProviderRow(**merged)
        return state["row"]

    repo.get.side_effect = get
    repo.list_all.side_effect = list_all
    repo.upsert.side_effect = upsert

    sm = MagicMock(name="settings_manager")
    sm.get.return_value = "aws_bedrock"
    sm.set.return_value = (True, None)

    return ChatProvidersService(repo=repo, settings_manager=sm), state


class TestBedrockUpdateProvider:
    def test_access_keys_mode_writes_encrypted_json_blob(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            aws_region="us-east-1",
            auth_mode="access_keys",
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ))
        row = state["row"]
        assert row.model == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert row.base_url == "us-east-1"
        assert is_encrypted(row.api_key_encrypted)
        blob = json.loads(decrypt(row.api_key_encrypted))
        assert blob == {
            "auth_mode": "access_keys",
            "access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        }

    def test_assume_role_mode_writes_role_only_blob(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="eu-west-1",
            auth_mode="assume_role",
            role_arn="arn:aws:iam::123456789012:role/BedrockAccess",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob == {
            "auth_mode": "assume_role",
            "role_arn": "arn:aws:iam::123456789012:role/BedrockAccess",
            "external_id": "",
            "base_access_key_id": "",
            "base_secret_access_key": "",
        }
        assert state["row"].base_url == "eu-west-1"

    def test_switching_auth_mode_clears_other_mode_fields(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="access_keys",
            access_key_id="AKIA1",
            secret_access_key="s1",
        ))
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            auth_mode="assume_role",
            role_arn="arn:aws:iam::1:role/x",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob == {"auth_mode": "assume_role",
                        "role_arn": "arn:aws:iam::1:role/x",
                        "external_id": "",
                        "base_access_key_id": "",
                        "base_secret_access_key": ""}
        assert "access_key_id" not in blob
        assert "secret_access_key" not in blob

    def test_partial_update_preserves_existing_blob_fields(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="access_keys",
            access_key_id="AKIA1",
            secret_access_key="s1",
        ))
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            model="anthropic.claude-3-5-haiku-20241022-v1:0",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob == {"auth_mode": "access_keys",
                        "access_key_id": "AKIA1",
                        "secret_access_key": "s1"}
        assert state["row"].model == "anthropic.claude-3-5-haiku-20241022-v1:0"

    def test_assume_role_with_external_id_persists_both(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="assume_role",
            role_arn="arn:aws:iam::1:role/x",
            external_id="my-ext-id",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob == {
            "auth_mode": "assume_role",
            "role_arn": "arn:aws:iam::1:role/x",
            "external_id": "my-ext-id",
            "base_access_key_id": "",
            "base_secret_access_key": "",
        }

    def test_external_id_partial_update_preserves_role_arn(self):
        svc, state = _service_with_bedrock_row()
        # Initial save with both fields.
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="assume_role",
            role_arn="arn:aws:iam::1:role/x",
            external_id="orig",
        ))
        # Update only external_id; role_arn must stay.
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            external_id="updated",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob["role_arn"] == "arn:aws:iam::1:role/x"
        assert blob["external_id"] == "updated"

    def test_assume_role_with_base_creds_persists_both_keys(self):
        svc, state = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="assume_role",
            role_arn="arn:aws:iam::1:role/x",
            external_id="ext",
            base_access_key_id="AKIAEXAMPLE",
            base_secret_access_key="basesecret",
        ))
        blob = json.loads(decrypt(state["row"].api_key_encrypted))
        assert blob == {
            "auth_mode": "assume_role",
            "role_arn": "arn:aws:iam::1:role/x",
            "external_id": "ext",
            "base_access_key_id": "AKIAEXAMPLE",
            "base_secret_access_key": "basesecret",
        }


class TestBedrockAdminConfigPreview:
    def test_access_keys_row_returns_access_key_preview_never_secret(self):
        svc, _ = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
            auth_mode="access_keys",
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="topsecret",
        ))
        cfg = svc.get_admin_config(chat_enabled=True)
        bedrock = cfg.providers["aws_bedrock"]
        assert bedrock.auth_mode == "access_keys"
        assert bedrock.aws_region == "us-east-1"
        assert bedrock.access_key_id_preview == "••••MPLE"
        assert bedrock.role_arn == ""
        assert bedrock.api_key_configured is True
        dumped = bedrock.model_dump_json()
        assert "topsecret" not in dumped
        assert "wJalrXUtnFEMI" not in dumped

    def test_assume_role_row_returns_role_arn_no_access_key(self):
        svc, _ = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="eu-west-1",
            auth_mode="assume_role",
            role_arn="arn:aws:iam::1:role/x",
            external_id="my-ext-id",
        ))
        bedrock = svc.get_admin_config(chat_enabled=True).providers["aws_bedrock"]
        assert bedrock.auth_mode == "assume_role"
        assert bedrock.role_arn == "arn:aws:iam::1:role/x"
        assert bedrock.external_id == "my-ext-id"
        assert bedrock.access_key_id_preview == ""
        assert bedrock.api_key_configured is True

    def test_unconfigured_bedrock_row_shows_empty_preview(self):
        svc, _ = _service_with_bedrock_row()
        bedrock = svc.get_admin_config(chat_enabled=True).providers["aws_bedrock"]
        assert bedrock.auth_mode is None
        assert bedrock.access_key_id_preview == ""
        assert bedrock.role_arn == ""
        assert bedrock.api_key_configured is False


class TestBedrockEffectiveConfig:
    def test_returns_aws_keys_for_access_keys_mode(self):
        svc, _ = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            aws_region="us-east-1",
            auth_mode="access_keys",
            access_key_id="AKIA1",
            secret_access_key="s1",
        ))
        eff = svc.get_effective_config("aws_bedrock")
        assert eff["CHAT_LLM_PROVIDER"] == "aws_bedrock"
        assert eff["CHAT_LLM_MODEL"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert eff["CHAT_LLM_AWS_REGION"] == "us-east-1"
        assert eff["CHAT_LLM_AWS_AUTH"] == {
            "auth_mode": "access_keys",
            "access_key_id": "AKIA1",
            "secret_access_key": "s1",
        }

    def test_raises_when_region_empty(self):
        svc, _ = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            auth_mode="access_keys",
            access_key_id="AKIA1",
            secret_access_key="s1",
        ))
        with pytest.raises(ValueError, match="AWS region"):
            svc.get_effective_config("aws_bedrock")

    def test_raises_when_auth_mode_missing(self):
        svc, _ = _service_with_bedrock_row()
        svc.update_provider("aws_bedrock", ProviderConfigUpdate(
            aws_region="us-east-1",
        ))
        with pytest.raises(ValueError, match="auth_mode"):
            svc.get_effective_config("aws_bedrock")

    def test_raises_on_malformed_blob(self):
        from src.modules.integrations.shared.encryption import encrypt
        svc, state = _service_with_bedrock_row(
            api_key_encrypted=encrypt("not-a-json-blob"),
            base_url="us-east-1",
        )
        with pytest.raises(ValueError, match="malformed"):
            svc.get_effective_config("aws_bedrock")
