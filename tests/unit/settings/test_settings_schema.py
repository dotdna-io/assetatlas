# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for confidence threshold settings schema."""

import pytest
from src.api.modules.settings.settings_schema import (
    APPLICATION_SETTINGS,
    SettingDefinition,
    validate_setting_value,
    get_setting_definition,
    get_default_value,
)


class TestSettingDefinitionToDict:
    """SettingDefinition.to_dict() is what GET /api/settings/schema serializes
    to the UI. The `hidden` flag keeps a setting in the schema (so
    settings_manager.set() still validates writes to it) while letting the
    generic schema-driven renderer skip it — used for keys a bespoke tab
    already owns (e.g. chat.active_provider)."""

    def test_to_dict_includes_hidden_defaulting_false(self):
        defn = SettingDefinition(
            setting_type='boolean', default=False, description='x'
        )
        d = defn.to_dict()
        assert 'hidden' in d, "schema endpoint must expose `hidden` to the UI"
        assert d['hidden'] is False

    def test_to_dict_reflects_hidden_true(self):
        defn = SettingDefinition(
            setting_type='boolean', default=False, description='x', hidden=True
        )
        assert defn.hidden is True
        assert defn.to_dict()['hidden'] is True


class TestUiTabCategoriesPresent:
    """The settings UI exposes one tab per category under /settings/system/.

    GET /api/settings/<category> returns 404 when the category is not in
    APPLICATION_SETTINGS — even if migrations seeded rows for it. Lock the
    contract so adding a new tab cannot ship without a matching schema entry.
    """

    # Mirror ui/prod/src/pages/settings/SystemGroupPage.tsx TABS.
    UI_TAB_CATEGORIES = ('retention', 'security', 'logging', 'email', 'ldap', 'oidc')

    @pytest.mark.parametrize('category', UI_TAB_CATEGORIES)
    def test_ui_tab_category_in_schema(self, category):
        assert category in APPLICATION_SETTINGS, (
            f"Settings UI exposes /settings/system/{category} but the category "
            f"is missing from APPLICATION_SETTINGS — GET /api/settings/{category} "
            "will return 404."
        )
        assert APPLICATION_SETTINGS[category], (
            f"Category {category!r} is empty — UI will render no fields."
        )


class TestLdapSchema:
    """LDAP category mirrors migration 088."""

    EXPECTED_KEYS = {
        'enabled', 'server_url', 'use_ssl', 'verify_cert',
        'connect_timeout_seconds', 'bind_dn', 'bind_password', 'base_dn',
        'user_search_filter', 'attribute_mail', 'attribute_full_name',
        'attribute_groups', 'group_to_role_mapping', 'default_role',
        'allow_local_fallback',
    }

    def test_all_keys_present(self):
        assert set(APPLICATION_SETTINGS['ldap'].keys()) == self.EXPECTED_KEYS

    def test_default_role_options(self):
        defn = get_setting_definition('ldap', 'default_role')
        assert defn.options == ['user', 'admin', 'super_admin']

    def test_group_to_role_mapping_is_json_dict(self):
        defn = get_setting_definition('ldap', 'group_to_role_mapping')
        assert defn.type == 'json'
        assert defn.default == {}

    def test_enabled_is_hidden_and_defaults_false(self):
        """LdapSettingsTab renders `enabled` as its own master switch (#1149).

        Without hidden=True the generic renderer draws it as well, so the tab
        offers the same flag twice through two different controls.
        """
        defn = get_setting_definition('ldap', 'enabled')
        assert defn.hidden is True
        assert defn.to_dict()['hidden'] is True
        # A fresh install has no directory auth until an admin turns it on.
        assert defn.default is False

    def test_only_enabled_is_hidden(self):
        """Everything else must stay in the grid — hiding a key it doesn't
        own would make that setting unreachable from the UI."""
        for key, defn in APPLICATION_SETTINGS['ldap'].items():
            if key == 'enabled':
                continue
            assert defn.hidden is False, f"ldap.{key} unexpectedly hidden"


class TestOidcSchema:
    """OIDC category mirrors migrations 089 + 091 + 103."""

    EXPECTED_KEYS = {
        'enabled', 'provider_name', 'discovery_url', 'client_id',
        'client_secret', 'redirect_uri', 'frontend_complete_url', 'scopes',
        'claim_mail', 'claim_name', 'claim_groups', 'group_to_role_mapping',
        'default_role', 'allow_local_fallback', 'auto_create_users',
        'allowed_email_domains', 'http_timeout_seconds',
        # Migration 103 (RP-initiated logout); read by
        # OidcService.build_logout_url. Empty string omits the redirect param.
        'post_logout_redirect_uri',
    }

    def test_all_keys_present(self):
        assert set(APPLICATION_SETTINGS['oidc'].keys()) == self.EXPECTED_KEYS

    def test_auto_create_users_default_false(self):
        # Migration 091 hardened the default to false for fresh installs.
        assert get_default_value('oidc', 'auto_create_users') is False

    def test_frontend_complete_url_default(self):
        assert get_default_value('oidc', 'frontend_complete_url') == '/oidc-complete'

    def test_enabled_is_hidden_and_defaults_false(self):
        """OidcSettingsTab renders `enabled` as its own master switch (#1149)."""
        defn = get_setting_definition('oidc', 'enabled')
        assert defn.hidden is True
        assert defn.to_dict()['hidden'] is True
        # A fresh install has no SSO until an admin configures and enables it.
        assert defn.default is False

    def test_only_enabled_is_hidden(self):
        for key, defn in APPLICATION_SETTINGS['oidc'].items():
            if key == 'enabled':
                continue
            assert defn.hidden is False, f"oidc.{key} unexpectedly hidden"


class TestTeamsSchema:
    """Teams category mirrors migrations 096 + 097."""

    def test_enabled_is_hidden_and_defaults_false(self):
        """ChatbotTeamsTab renders `enabled` as its own master switch (#1227).

        The tab is bespoke (it talks to teamsApi, not CategorySettings), so
        nothing draws the flag twice today — the marker states the contract
        so the first tab pointed at the category can't quietly break it, the
        way ldap.enabled and oidc.enabled do for theirs.
        """
        defn = get_setting_definition('teams', 'enabled')
        assert defn.hidden is True
        assert defn.to_dict()['hidden'] is True
        # A fresh install has no Teams bridge until an admin turns it on.
        assert defn.default is False

    def test_only_enabled_is_hidden(self):
        """Everything else must stay reachable from a generic renderer."""
        for key, defn in APPLICATION_SETTINGS['teams'].items():
            if key == 'enabled':
                continue
            assert defn.hidden is False, f"teams.{key} unexpectedly hidden"


class TestAuthSchema:
    """The Security tab renders an extra `auth` section so admins can change
    password expiration. settings_manager.set() rejects keys missing from the
    schema, so an unmounted entry would make the field read-only at runtime."""

    def test_password_expiration_days_present(self):
        defn = get_setting_definition('auth', 'password_expiration_days')
        assert defn.type == 'integer'
        assert defn.default == 180
        assert defn.min == 0
        assert defn.max == 3650

    def test_zero_disables_expiration(self):
        # 0 must validate (NIST 800-63B opt-out path).
        assert validate_setting_value('auth', 'password_expiration_days', 0) is True

    def test_negative_rejected(self):
        assert validate_setting_value('auth', 'password_expiration_days', -1) is False

    def test_above_max_rejected(self):
        assert validate_setting_value('auth', 'password_expiration_days', 3651) is False


class TestChatActiveProviderSchema:
    """The chat.active_provider options list is consulted by
    ``settings_manager.set`` to validate the value the admin UI sends when
    toggling the active provider. If a provider is wired up everywhere
    (Pydantic Literal, DB enum, migration 105) but missing here, saves that
    activate it 400 with ``Invalid value for chat.active_provider``."""

    # 'none' is not a provider and is deliberately absent from the Literal: it
    # is the sentinel ChatProvidersService.set_active_provider(None) persists
    # when the admin disables every provider from the grid (#926) —
    # `_NONE_PROVIDER` in src/api/blueprints/chat/admin_config/service.py. It
    # must still be an option, because SettingDefinition.validate rejects any
    # value outside `options`, so dropping it would 400 the disable-all save.
    NON_PROVIDER_OPTIONS = {'none'}

    def test_options_match_chat_providers_literal_plus_sentinels(self):
        from src.api.blueprints.chat.admin_config.models import CHAT_PROVIDERS

        defn = get_setting_definition('chat', 'active_provider')
        assert set(defn.options) == set(CHAT_PROVIDERS) | self.NON_PROVIDER_OPTIONS, (
            "settings_schema chat.active_provider options drifted from the "
            "ChatProvider Literal + NON_PROVIDER_OPTIONS — a missing provider "
            "won't validate at settings_manager.set() and admin save will 400; "
            "an unexpected extra is a value nothing knows how to act on."
        )
        # Same guard stated as behaviour: turning chat off must not 400.
        assert validate_setting_value('chat', 'active_provider', 'none') is True

    def test_aws_bedrock_validates(self):
        assert validate_setting_value('chat', 'active_provider', 'aws_bedrock') is True


class TestConfidenceThresholdSchema:
    """Confidence threshold settings exist and validate correctly."""

    def test_medium_threshold_exists(self):
        defn = get_setting_definition('asset_generation', 'confidence_threshold_medium')
        assert defn.type == 'float'
        assert defn.default == 0.45

    def test_high_threshold_exists(self):
        defn = get_setting_definition('asset_generation', 'confidence_threshold_high')
        assert defn.type == 'float'
        assert defn.default == 0.70

    def test_medium_default_value(self):
        assert get_default_value('asset_generation', 'confidence_threshold_medium') == 0.45

    def test_high_default_value(self):
        assert get_default_value('asset_generation', 'confidence_threshold_high') == 0.70

    def test_medium_validates_in_range(self):
        assert validate_setting_value('asset_generation', 'confidence_threshold_medium', 0.5) is True

    def test_medium_rejects_out_of_range(self):
        assert validate_setting_value('asset_generation', 'confidence_threshold_medium', -0.1) is False
        assert validate_setting_value('asset_generation', 'confidence_threshold_medium', 1.1) is False

    def test_high_validates_in_range(self):
        assert validate_setting_value('asset_generation', 'confidence_threshold_high', 0.8) is True

    def test_high_rejects_out_of_range(self):
        assert validate_setting_value('asset_generation', 'confidence_threshold_high', -0.1) is False
        assert validate_setting_value('asset_generation', 'confidence_threshold_high', 1.1) is False


class TestLoggingSchema:
    """Logging category defaults.

    `health_check_log_level` has no `system_settings` seed row, so a fresh
    install (and any install that never set the key) resolves it through this
    schema default. Defaulting to DEBUG put health-check request logs below the
    normal INFO threshold, making probes to /api/health & friends invisible in
    the API log — see #1140.
    """

    def test_health_check_log_level_defaults_to_info(self):
        assert get_default_value('logging', 'health_check_log_level') == 'INFO'

    def test_health_check_log_level_still_offers_debug_and_warning(self):
        """Operators who find INFO noisy must still be able to opt back down."""
        options = get_setting_definition('logging', 'health_check_log_level').options
        assert options == ['DEBUG', 'INFO', 'WARNING']

    def test_health_check_log_level_default_is_a_valid_option(self):
        assert validate_setting_value(
            'logging', 'health_check_log_level',
            get_default_value('logging', 'health_check_log_level'),
        ) is True


def test_monitoring_retention_defaults_empty_and_hidden():
    assert get_default_value("monitoring", "retention") == ""
    assert get_setting_definition("monitoring", "retention").hidden is True


@pytest.mark.parametrize("value", ["", "7d", "15d", "30d", "90d", "1y", "12h"])
def test_monitoring_retention_accepts_empty_or_single_unit(value):
    assert validate_setting_value("monitoring", "retention", value) is True


@pytest.mark.parametrize("value", ["4w2d", "1y2w", "30", "abc", "forever", "30 d"])
def test_monitoring_retention_rejects_compound_and_junk(value):
    assert validate_setting_value("monitoring", "retention", value) is False
