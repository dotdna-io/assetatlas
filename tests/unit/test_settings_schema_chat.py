# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.blueprints.chat.admin_config.models import CHAT_PROVIDERS
from src.api.modules.settings import settings_schema


def test_active_provider_allowlist_matches_supported_providers():
    # settings_manager.set() validates chat.active_provider against this
    # SettingDefinition's `options`, NOT the DB validation_rule column. When a
    # provider is added to CHAT_PROVIDERS (and the chat_providers CHECK
    # constraint), this list must be widened too — otherwise switching the
    # active provider to the new value is rejected with "Invalid value for
    # chat.active_provider" and the UI toggle 400s. 'foundry' was added to
    # CHAT_PROVIDERS but missed here, which silently blocked activating it.
    #
    # The options also carry a deliberate 'none' sentinel (every provider
    # disabled — see the SettingDefinition and get_active_provider), so the
    # allowlist is exactly the providers plus 'none'.
    defn = settings_schema.get_setting_definition("chat", "active_provider")
    assert set(defn.options) == set(CHAT_PROVIDERS) | {"none"}
    for provider in CHAT_PROVIDERS:
        assert settings_schema.validate_setting_value(
            "chat", "active_provider", provider,
        ), f"active_provider must accept supported provider {provider!r}"
    assert settings_schema.validate_setting_value(
        "chat", "active_provider", "none",
    ), "active_provider must accept the 'none' (chat-off) sentinel"


def test_chat_routing_settings_have_safe_defaults():
    assert settings_schema.get_default_value("chat", "tool_routing_enabled") is False
    assert settings_schema.get_default_value("chat", "tool_routing_top_k") == 5


def test_active_provider_hidden_from_generic_settings_renderer():
    # The bespoke provider Configuration tab owns chat.active_provider via
    # /api/chat/config. The generic schema-driven Settings renderer must skip
    # it (hidden=True) so the new Behavior tab doesn't render a duplicate,
    # drifting control. The row stays in the schema so settings_manager.set()
    # still validates writes from the provider tab.
    defn = settings_schema.get_setting_definition("chat", "active_provider")
    assert defn.hidden is True
    assert defn.to_dict()["hidden"] is True


def test_routing_settings_are_visible_in_generic_renderer():
    # The two routing knobs are the whole reason the Behavior tab exists — they
    # must stay visible (hidden=False) so the generic renderer shows them.
    for key in ("tool_routing_enabled", "tool_routing_top_k"):
        defn = settings_schema.get_setting_definition("chat", key)
        assert defn.hidden is False
        assert defn.to_dict()["hidden"] is False
