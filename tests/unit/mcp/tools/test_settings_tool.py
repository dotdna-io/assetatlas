# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_settings MCP/chat tool.

Security focus: value-redaction must follow the SERVER's authoritative
secret-marking — the per-category ``audit_redact`` hooks
(``src/api/blueprints/settings/category_hooks.py``) and the ``ENC:`` encryption
marker the ``/api/settings/email`` route trusts (``is_encrypted``) — instead of
relying on a hand-rolled 7-token key-name allowlist that leaked credential
settings whose key names fell outside it (aws_access_key_id, signing_key,
passphrase, ...). The name heuristic is retained only as an additive safety
net, so the change can only ever redact MORE, never less.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import src.api.utils.public_base_url as public_base_url_module
from src.mcp.tools import settings_tool
from src.mcp.tools._common import is_sensitive_key
from src.mcp.tools.settings_tool import get_settings_impl


def _setting(value, default=None, description="", vtype="string",
             updated_at=None, updated_by=None):
    return {
        "value": value,
        "default": default if default is not None else value,
        "description": description,
        "type": vtype,
        "min": None,
        "max": None,
        "section": None,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


# --------------------------------------------------------------------------- #
# values view — happy paths (unchanged behaviour)                             #
# --------------------------------------------------------------------------- #

def test_no_args_renders_all_categories():
    all_settings = {
        "chat": {
            "CHAT_ENABLED": _setting(True, default=False,
                                     description="Enable chat", vtype="bool"),
        },
        "pipeline": {
            "BATCH_SIZE": _setting(100, default=50,
                                   description="Batch size", vtype="int"),
        },
    }
    mgr = MagicMock()
    mgr.get_all.return_value = all_settings

    out = get_settings_impl(mgr)

    mgr.get_all.assert_called_once_with()
    assert "# System settings" in out
    assert "## chat (1 settings)" in out
    assert "## pipeline (1 settings)" in out
    assert "CHAT_ENABLED" in out
    assert "BATCH_SIZE" in out


def test_one_category_renders_section():
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "chat": {
            "CHAT_ENABLED": _setting(True, default=False,
                                     description="Enable chat",
                                     vtype="bool"),
        }
    }
    out = get_settings_impl(mgr, category="chat")

    mgr.get_all.assert_called_once_with(category="chat")
    assert "# Settings — chat" in out
    assert "CHAT_ENABLED" in out


def test_specific_key_happy_path():
    mgr = MagicMock()
    mgr.get.return_value = True
    mgr.get_all.return_value = {
        "chat": {
            "CHAT_ENABLED": _setting(True, default=False,
                                     description="Enable chat",
                                     vtype="bool",
                                     updated_at="2026-04-20",
                                     updated_by="admin"),
        }
    }

    out = get_settings_impl(mgr, category="chat", key="CHAT_ENABLED")

    mgr.get.assert_called_once_with("chat", "CHAT_ENABLED")
    assert "# chat.CHAT_ENABLED" in out
    assert "- value: True" in out
    assert "- description: Enable chat" in out
    assert "- updated: 2026-04-20" in out
    assert "- updated by: admin" in out


def test_key_without_category_errors():
    mgr = MagicMock()
    out = get_settings_impl(mgr, key="CHAT_ENABLED")
    assert "Must specify 'category'" in out


def test_missing_setting_returns_not_found():
    mgr = MagicMock()
    mgr.get.return_value = None
    mgr.get_all.return_value = {"chat": {}}

    out = get_settings_impl(mgr, category="chat", key="MISSING_KEY")

    assert "not found" in out.lower()
    assert "chat.MISSING_KEY" in out


# --------------------------------------------------------------------------- #
# Redaction — the security fix                                                #
# --------------------------------------------------------------------------- #

def test_encrypted_value_redacted_even_when_key_name_not_sensitive():
    """THE acceptance test for the security fix.

    A credential stored encrypted (``ENC:`` marker — the same ``is_encrypted``
    signal the ``/api/settings/email`` route trusts) under a key whose name the
    old 7-token allowlist does NOT flag must be redacted. The OLD behaviour
    exposed the at-rest ciphertext of the credential to the chat transcript;
    the server treats any ``ENC:`` value as a secret regardless of key name.

    FAILS on the old token-only redaction; PASSES on the new
    server-authoritative redaction.
    """
    # Prove the OLD predicate would NOT have flagged this key — so the test
    # exercises the fix, not pre-existing behaviour.
    assert is_sensitive_key("aws_access_key_id") is False

    mgr = MagicMock()
    mgr.get_all.return_value = {
        "integrations": {
            "aws_access_key_id": _setting("ENC:gAAAAA-fake-ciphertext-zzz"),
        }
    }

    out = get_settings_impl(mgr, category="integrations")

    assert "ENC:gAAAAA-fake-ciphertext-zzz" not in out
    assert "<redacted>" in out


def test_declared_secret_redacted_via_category_hook(monkeypatch):
    """A category hook's ``audit_redact()`` is the server's authoritative
    'this key is a secret' predicate. get_settings must honour it even for a
    CLEARTEXT value under a key the 7-token allowlist misses — proving the
    redaction consults the server hooks, not just key-name tokens.
    """
    class _StubHook:
        def audit_redact(self, key):
            return key == "unlock_phrase"

    monkeypatch.setattr(settings_tool, "CATEGORY_HOOKS", {"vault": _StubHook()})

    # The name heuristic would NOT catch this key, and the value is cleartext
    # (not ENC:), so ONLY the server hook can redact it.
    assert is_sensitive_key("unlock_phrase") is False

    mgr = MagicMock()
    mgr.get_all.return_value = {
        "vault": {"unlock_phrase": _setting("cleartext-passphrase-xyz")}
    }

    out = get_settings_impl(mgr, category="vault")

    assert "cleartext-passphrase-xyz" not in out
    assert "<redacted>" in out


def test_real_declared_secret_is_redacted():
    """Regression guard: a real schema secret (oidc.client_secret) stays
    redacted via the server hook authority, not the dropped token list."""
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "oidc": {"client_secret": _setting("ENC:gAAAAA-real-oidc-secret")},
    }

    out = get_settings_impl(mgr, category="oidc")

    assert "ENC:gAAAAA-real-oidc-secret" not in out
    assert "<redacted>" in out


def test_name_based_secret_still_redacted():
    """The retained name heuristic still redacts an obviously-named secret
    (additive safety net — never a regression)."""
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "auth": {
            "SECRET_TOKEN": _setting("super-secret-raw-value",
                                     default="",
                                     description="A secret",
                                     vtype="string"),
        }
    }

    out = get_settings_impl(mgr, category="auth")

    assert "<redacted>" in out
    assert "super-secret-raw-value" not in out


def test_benign_key_containing_key_substring_not_redacted():
    # Regression: the word-boundary check treats "key" as a whole token only,
    # so compound names with "_KEY" pass through and are not redacted.
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "cache": {
            "PRIMARY_KEY": _setting("id", default="id",
                                    description="Cache primary key column",
                                    vtype="string"),
        }
    }

    out = get_settings_impl(mgr, category="cache")

    assert "<redacted>" not in out
    assert "id" in out


# --------------------------------------------------------------------------- #
# Exceptions must NOT leak raw {e} (cross-cutting §4)                          #
# --------------------------------------------------------------------------- #

def test_get_all_exception_returns_generic_message_without_raw_error():
    mgr = MagicMock()
    mgr.get_all.side_effect = RuntimeError("db down host=secret-db.internal")

    out = get_settings_impl(mgr)

    # Generic, actionable — never the raw exception text (which can carry
    # connection strings / hostnames into the shared transcript).
    assert "unavailable" in out.lower()
    assert "db down" not in out
    assert "secret-db.internal" not in out


def test_none_manager_returns_unavailable():
    out = get_settings_impl(None)
    assert out == "Settings manager not available."


# --------------------------------------------------------------------------- #
# view enum + sub-views                                                       #
# --------------------------------------------------------------------------- #

def test_unknown_view_returns_loud_error_and_queries_nothing():
    mgr = MagicMock()
    out = get_settings_impl(mgr, view="bogus")
    assert "view" in out.lower()
    assert "bogus" in out
    mgr.get_all.assert_not_called()
    mgr.get.assert_not_called()


def test_schema_view_renders_definitions():
    mgr = MagicMock()
    mgr.get_schema.return_value = {
        "asset_generation": {
            "enable_ml_predictions": {
                "type": "boolean", "default": False,
                "description": "Enable ML", "min": None, "max": None,
                "options": None,
            },
        }
    }
    out = get_settings_impl(mgr, view="schema")

    mgr.get_schema.assert_called_once_with()
    assert "enable_ml_predictions" in out
    assert "boolean" in out
    assert "Enable ML" in out


def test_email_view_redacts_password_with_sentinel():
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "email": {
            "smtp_host": _setting("smtp.example.com"),
            "smtp_password": _setting("ENC:gAAAAA-real-smtp-cipher"),
            "smtp_username": _setting("relayuser"),
        }
    }
    out = get_settings_impl(mgr, view="email")

    mgr.get_all.assert_called_once_with(category="email")
    # Mirror /api/settings/email: encrypted password -> '__SET__' sentinel.
    assert "ENC:gAAAAA-real-smtp-cipher" not in out
    assert "__SET__" in out
    # Non-secret email fields shown as-is.
    assert "smtp.example.com" in out


def test_security_effective_view_reports_resolved_url_and_source(monkeypatch):
    monkeypatch.setattr(
        public_base_url_module, "resolve_public_base_url",
        lambda sm: "https://assetatlas.example.com",
    )
    mgr = MagicMock()
    mgr.get.return_value = "https://assetatlas.example.com"  # DB row present

    out = get_settings_impl(mgr, view="security-effective")

    assert "https://assetatlas.example.com" in out
    # DB row non-empty -> source is the saved setting.
    assert "setting" in out


def test_reliability_view_renders_per_source_scores():
    mgr = MagicMock()
    mgr.get.return_value = {"endpoint security": 0.95, "other": 0.50}

    out = get_settings_impl(mgr, view="reliability")

    mgr.get.assert_called_once_with("asset_generation",
                                    "source_reliability_scores")
    assert "endpoint security" in out
    assert "0.95" in out


def test_value_containing_pipe_is_escaped_so_table_is_not_broken():
    """The real ldap.user_search_filter default contains a literal '|' (LDAP
    OR). Rendered raw it would split the markdown row into extra columns and
    corrupt the table the LLM parses; it must be backslash-escaped."""
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "ldap": {
            "user_search_filter": _setting(
                "(&(objectClass=person)(|(sAMAccountName={u})(mail={u})))"),
        }
    }

    out = get_settings_impl(mgr, category="ldap")

    assert "\\|" in out          # the pipe is escaped
    assert "(|(" not in out      # the raw unescaped sequence is gone


def test_email_view_redacts_non_password_encrypted_key():
    """Defensive: the email view must route every non-password key through the
    authoritative redaction, so a future encrypted email field can't surface as
    raw ciphertext just because it isn't named 'smtp_password'."""
    mgr = MagicMock()
    mgr.get_all.return_value = {
        "email": {
            "smtp_oauth_token": _setting("ENC:gAAAAA-future-secret"),
            "smtp_host": _setting("smtp.example.com"),
        }
    }

    out = get_settings_impl(mgr, view="email")

    assert "ENC:gAAAAA-future-secret" not in out
    assert "<redacted>" in out
    assert "smtp.example.com" in out
