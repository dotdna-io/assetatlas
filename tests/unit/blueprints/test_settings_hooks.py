# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sanity tests for settings category hooks."""


def test_ldap_hooks_encrypt_bind_password():
    from src.api.blueprints.settings.category_hooks import LdapSettingsHooks
    hooks = LdapSettingsHooks()
    encrypted = hooks.pre_store('bind_password', 'plaintext-secret')
    assert encrypted.startswith('ENC:')
    assert encrypted != 'plaintext-secret'


def test_ldap_hooks_bind_password_audit_redacted():
    from src.api.blueprints.settings.category_hooks import LdapSettingsHooks
    hooks = LdapSettingsHooks()
    assert hooks.audit_redact('bind_password') is True
    assert hooks.audit_redact('server_url') is False


def test_ldap_hooks_pass_through_non_password_values():
    from src.api.blueprints.settings.category_hooks import LdapSettingsHooks
    hooks = LdapSettingsHooks()
    assert hooks.pre_store('server_url', 'ldaps://dc01.corp.com') == 'ldaps://dc01.corp.com'
    assert hooks.pre_store('bind_password', '') == ''


def test_oidc_hooks_encrypt_client_secret():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    encrypted = hooks.pre_store('client_secret', 'plaintext-secret')
    assert encrypted.startswith('ENC:')
    assert encrypted != 'plaintext-secret'


def test_oidc_hooks_client_secret_audit_redacted():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    assert hooks.audit_redact('client_secret') is True
    assert hooks.audit_redact('discovery_url') is False


def test_oidc_hooks_pass_through_non_secret_values():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    url = 'https://login.microsoftonline.com/...'
    assert hooks.pre_store('discovery_url', url) == url
    assert hooks.pre_store('client_secret', '') == ''


# ── Task 4.3: frontend_complete_url open-redirect guard ─────────────────────


def _flask_app_at(host: str = 'example.com'):
    """Build a minimal Flask app whose request_context() yields a request from `host`."""
    from flask import Flask
    app = Flask(__name__)
    return app


def test_frontend_complete_url_relative_accepted():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    # No request context needed — relative paths are unconditionally allowed.
    assert hooks.validate('frontend_complete_url', '/oidc-complete') == '/oidc-complete'
    assert hooks.validate('frontend_complete_url', '/path/to/page') == '/path/to/page'


def test_frontend_complete_url_empty_accepted():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    assert hooks.validate('frontend_complete_url', '') == ''


def test_frontend_complete_url_absolute_https_same_origin_accepted():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
    hooks = OidcSettingsHooks()
    app = _flask_app_at()
    with app.test_request_context('/api/settings/oidc/frontend_complete_url',
                                  base_url='https://example.com'):
        result = hooks.validate('frontend_complete_url',
                                'https://example.com/oidc-complete')
        assert result == 'https://example.com/oidc-complete'


def test_frontend_complete_url_absolute_https_different_origin_rejected():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks, HookValidationError
    hooks = OidcSettingsHooks()
    app = _flask_app_at()
    with app.test_request_context('/api/settings/oidc/frontend_complete_url',
                                  base_url='https://example.com'):
        import pytest
        with pytest.raises(HookValidationError, match='does not match'):
            hooks.validate('frontend_complete_url',
                           'https://attacker.com/oidc-complete')


def test_frontend_complete_url_http_rejected():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks, HookValidationError
    hooks = OidcSettingsHooks()
    app = _flask_app_at()
    with app.test_request_context('/api/settings/oidc/frontend_complete_url',
                                  base_url='https://example.com'):
        import pytest
        with pytest.raises(HookValidationError, match='https'):
            hooks.validate('frontend_complete_url',
                           'http://example.com/oidc-complete')


def test_frontend_complete_url_javascript_scheme_rejected():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks, HookValidationError
    hooks = OidcSettingsHooks()
    app = _flask_app_at()
    with app.test_request_context('/api/settings/oidc/frontend_complete_url',
                                  base_url='https://example.com'):
        import pytest
        with pytest.raises(HookValidationError, match='https'):
            hooks.validate('frontend_complete_url', 'javascript:alert(1)')


def test_frontend_complete_url_protocol_relative_rejected():
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks, HookValidationError
    hooks = OidcSettingsHooks()
    app = _flask_app_at()
    with app.test_request_context('/api/settings/oidc/frontend_complete_url',
                                  base_url='https://example.com'):
        import pytest
        with pytest.raises(HookValidationError, match='protocol-relative'):
            hooks.validate('frontend_complete_url', '//attacker.com/path')


def test_frontend_complete_url_absolute_https_outside_request_context_rejected():
    """Programmatic writes (no Flask request) must refuse absolute URLs."""
    from src.api.blueprints.settings.category_hooks import OidcSettingsHooks, HookValidationError
    hooks = OidcSettingsHooks()
    import pytest
    with pytest.raises(HookValidationError, match='request context'):
        hooks.validate('frontend_complete_url', 'https://example.com/oidc-complete')
