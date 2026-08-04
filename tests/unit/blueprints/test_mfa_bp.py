# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for MFA self-service endpoints."""
from datetime import timedelta
from unittest.mock import patch, MagicMock, ANY

import pytest
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from src.api.blueprints.auth import auth_bp
from src.auth.services.mfa_service import MfaService


# ---- Fixtures --------------------------------------------------------------

@pytest.fixture
def app():
    """Minimal Flask app with auth_bp registered."""
    app = Flask(__name__)
    app.config['JWT_SECRET_KEY'] = 'test-secret'
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(seconds=900)
    app.config['CACHE_TYPE'] = 'SimpleCache'
    jwt = JWTManager(app)

    # Initialise Flask-Caching (required by mfa_challenge and _enforce_token_rate_limit)
    from src.api.utils.caching import cache
    cache.init_app(app)

    # Stub user returned by current_user
    test_user = {
        'id': 'test-user-id',
        'username': 'alice',
        'email': 'alice@test.com',
        'role': 'user',
        'is_active': True,
    }

    # Mirror production: identity_loader converts dict → str(id)
    @jwt.user_identity_loader
    def user_identity_loader(user):
        if isinstance(user, dict):
            return str(user['id'])
        return str(user)

    # Mirror production: lookup_loader returns the stub user
    @jwt.user_lookup_loader
    def user_lookup_loader(_jwt_header, jwt_data):
        return test_user

    app.connection_manager = MagicMock()
    app.settings_manager = MagicMock()
    app.settings_manager.get.return_value = 20  # rate limit default

    app.register_blueprint(auth_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_header(app):
    """Build a Bearer header for test-user-id (local auth)."""
    user = {'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com', 'role': 'user'}
    with app.app_context():
        token = create_access_token(identity=user, additional_claims={'auth_method': 'local'})
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def ldap_auth_header(app):
    """Build a Bearer header with auth_method='ldap'."""
    user = {'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com', 'role': 'user'}
    with app.app_context():
        token = create_access_token(identity=user, additional_claims={'auth_method': 'ldap'})
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def oidc_auth_header(app):
    """Build a Bearer header with auth_method='oidc'."""
    user = {'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com', 'role': 'user'}
    with app.app_context():
        token = create_access_token(identity=user, additional_claims={'auth_method': 'oidc'})
    return {'Authorization': f'Bearer {token}'}


# Patch path constants
MFA_REPO = 'src.api.blueprints.auth.UserMfaRepository'
USER_REPO = 'src.api.blueprints.auth.UserRepository'
AUDIT_SVC = 'src.api.blueprints.auth.AuditService'
PWD_SVC = 'src.auth.services.password_service.PasswordService'


# ---- /mfa/status -----------------------------------------------------------

class TestMfaStatus:
    def test_returns_disabled_when_no_methods(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.count_enabled_methods.return_value = 0
            r = client.get('/api/auth/mfa/status', headers=auth_header)
        assert r.status_code == 200
        assert r.json == {'enabled': False}

    def test_returns_enabled_with_one_method(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.count_enabled_methods.return_value = 1
            r = client.get('/api/auth/mfa/status', headers=auth_header)
        assert r.status_code == 200
        assert r.json == {'enabled': True}

    def test_returns_enabled_with_multiple_methods(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.count_enabled_methods.return_value = 3
            r = client.get('/api/auth/mfa/status', headers=auth_header)
        assert r.status_code == 200
        assert r.json == {'enabled': True}


# ---- /mfa/setup -------------------------------------------------------------

class TestMfaSetup:
    def _setup_mocks(self, MR, *, method_id='11111111-1111-1111-1111-111111111111'):
        """Wire up the multi-method repo stubs that mfa_setup needs."""
        from uuid import UUID
        MR.return_value.replace_pending_method.return_value = UUID(method_id)

    def test_returns_method_id_secret_and_uri(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            self._setup_mocks(MR)
            r = client.post('/api/auth/mfa/setup', headers=auth_header)
        assert r.status_code == 200
        body = r.json
        assert 'method_id' in body
        assert 'secret' in body and len(body['secret']) == 32
        assert body['provisioning_uri'].startswith('otpauth://totp/AssetAtlas:alice')
        assert body['label'] == 'Authenticator app'  # default
        # Each setup atomically replaces the pending row.
        MR.return_value.replace_pending_method.assert_called_once()

    def test_setup_accepts_and_returns_custom_label(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            self._setup_mocks(MR)
            r = client.post('/api/auth/mfa/setup', headers=auth_header,
                            json={'label': 'Phone'})
        assert r.status_code == 200
        assert r.json['label'] == 'Phone'
        # Repo received the supplied label.
        kwargs = MR.return_value.replace_pending_method.call_args.kwargs
        assert kwargs['label'] == 'Phone'

    def test_setup_allowed_when_methods_already_enrolled(self, client, auth_header):
        """Multi-method: re-running setup must NOT be rejected when other
        methods are already enabled — that's the 'Add another method' path."""
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            self._setup_mocks(MR)
            MR.return_value.count_enabled_methods.return_value = 2
            r = client.post('/api/auth/mfa/setup', headers=auth_header)
        assert r.status_code == 200
        assert r.json.get('error') is None

    def test_setup_blocked_for_ldap_user(self, client, ldap_auth_header):
        # No DB call needed: the JWT auth_method='ldap' claim is sufficient.
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            r = client.post('/api/auth/mfa/setup', headers=ldap_auth_header)
        assert r.status_code == 400
        assert r.json['error'] == 'mfa_not_supported_for_external_users'
        MR.return_value.replace_pending_method.assert_not_called()

    def test_setup_blocked_for_oidc_user(self, client, oidc_auth_header):
        # No DB call needed: the JWT auth_method='oidc' claim is sufficient.
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            r = client.post('/api/auth/mfa/setup', headers=oidc_auth_header)
        assert r.status_code == 400
        assert r.json['error'] == 'mfa_not_supported_for_external_users'
        MR.return_value.replace_pending_method.assert_not_called()

    def test_rate_limited_after_threshold(self, app, client, auth_header):
        original_get = app.settings_manager.get

        def _strict_get(cat, key, default=None):
            if key == 'token_verify_rate_limit_per_ip_per_hour':
                return 2
            return default

        app.settings_manager.get = _strict_get
        try:
            # Clear any pre-existing counter for this test IP + action.
            with app.app_context():
                from src.api.utils.caching import cache
                cache.delete('ratelimit:mfa_setup:127.0.0.1')

            with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
                self._setup_mocks(MR)
                client.post('/api/auth/mfa/setup', headers=auth_header)
                client.post('/api/auth/mfa/setup', headers=auth_header)
                # Third request exceeds limit=2
                r = client.post('/api/auth/mfa/setup', headers=auth_header)

            assert r.status_code == 429
            assert r.json['error'] == 'rate_limit_exceeded'
        finally:
            app.settings_manager.get = original_get
            with app.app_context():
                from src.api.utils.caching import cache
                cache.delete('ratelimit:mfa_setup:127.0.0.1')

    def test_setup_treats_legacy_token_without_auth_method_as_local(self, app, client):
        """Tokens without auth_method claim default to 'local' and are not blocked."""
        user = {'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com', 'role': 'user'}
        with app.app_context():
            # Issue token WITHOUT auth_method claim (legacy token shape)
            token = create_access_token(identity=user)
        headers = {'Authorization': f'Bearer {token}'}

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC):
            UR.return_value.get_user_by_id.return_value = {'username': 'alice'}
            self._setup_mocks(MR)
            r = client.post('/api/auth/mfa/setup', headers=headers)
        # Not blocked as external — proceeds (might 200 with secret, or hit rate limit etc.)
        assert r.json.get('error') != 'mfa_not_supported_for_external_users', \
            f"External-user guard fired for legacy token: {r.json}"


# ---- /mfa/verify ------------------------------------------------------------

class TestMfaVerify:
    PENDING_ID = '22222222-2222-2222-2222-222222222222'

    def _pending(self, secret_ciphertext):
        return {
            'id': self.PENDING_ID,
            'mfa_enabled': False,
            'mfa_secret': secret_ciphertext,
            'label': 'Phone',
        }

    def test_verify_with_correct_code_enables_pending_method(self, client, auth_header):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        import pyotp
        good_code = pyotp.TOTP(secret).now()

        with patch(MFA_REPO) as MR, patch(USER_REPO), patch(AUDIT_SVC):
            MR.return_value.get_pending_method.return_value = self._pending(encrypted)
            r = client.post('/api/auth/mfa/verify', headers=auth_header, json={'code': good_code})

        assert r.status_code == 200
        assert r.json['enabled'] is True
        MR.return_value.enable_method.assert_called_once_with(self.PENDING_ID)

    def test_verify_with_wrong_code_returns_400(self, client, auth_header):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        with patch(MFA_REPO) as MR, patch(USER_REPO), patch(AUDIT_SVC):
            MR.return_value.get_pending_method.return_value = self._pending(encrypted)
            r = client.post('/api/auth/mfa/verify', headers=auth_header, json={'code': '000000'})
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_code'
        MR.return_value.enable_method.assert_not_called()

    def test_verify_without_pending_method_returns_400(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(USER_REPO), patch(AUDIT_SVC):
            MR.return_value.get_pending_method.return_value = None
            r = client.post('/api/auth/mfa/verify', headers=auth_header, json={'code': '123456'})
        assert r.status_code == 400
        assert r.json['error'] == 'no_pending_setup'

    def test_verify_with_corrupt_secret_returns_no_pending_setup(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS, \
             patch('src.api.blueprints.auth.MfaService') as MS:
            UR.return_value.get_user_by_id.return_value = {'username': 'alice'}
            MR.return_value.get_pending_method.return_value = self._pending('ENC:corrupt')
            MS.decrypt_secret.side_effect = ValueError('boom')
            r = client.post('/api/auth/mfa/verify', headers=auth_header, json={'code': '123456'})
        assert r.status_code == 400
        assert r.json['error'] == 'no_pending_setup'
        MR.return_value.enable_method.assert_not_called()
        AS.return_value.log_auth.assert_any_call(
            action='mfa_verify', success=False, user_id='test-user-id', username='alice',
            ip=ANY, user_agent=ANY, failure_reason='secret_decrypt_failed',
        )


# ---- /mfa/disable -----------------------------------------------------------

class TestMfaDisable:
    """`/mfa/disable` removes EVERY enrolled method (multi-method). Code must
    match at least one secret. Combined with #610 the next login forces
    re-enrollment."""

    def _enabled_with_secret(self, encrypted, *, mid='m1', label='Authenticator app'):
        return [{'id': mid, 'mfa_secret': encrypted, 'label': label}]

    def test_disable_requires_correct_password_and_code(self, client, auth_header):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        import pyotp
        good_code = pyotp.TOTP(secret).now()

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'hash', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = self._enabled_with_secret(encrypted)
            MR.return_value.delete_all_methods.return_value = 1
            PS.verify_password.return_value = True
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'pw', 'code': good_code})
        assert r.status_code == 200
        assert r.json == {'success': True}
        MR.return_value.delete_all_methods.assert_called_once()
        # Must use get_user_by_username, not get_user_by_id — the latter
        # strips password_hash, so verify_password would raise KeyError.
        UR.return_value.get_user_by_username.assert_called_once_with('alice')

    def test_disable_accepts_code_matching_any_enrolled_method(self, client, auth_header):
        secret_a = MfaService.generate_secret()
        secret_b = MfaService.generate_secret()
        import pyotp
        # Code generated from method B; method A doesn't match; impl must keep trying.
        code_b = pyotp.TOTP(secret_b).now()

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'h', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'a', 'mfa_secret': MfaService.encrypt_secret(secret_a), 'label': 'A'},
                {'id': 'b', 'mfa_secret': MfaService.encrypt_secret(secret_b), 'label': 'B'},
            ]
            PS.verify_password.return_value = True
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'pw', 'code': code_b})
        assert r.status_code == 200
        MR.return_value.delete_all_methods.assert_called_once()

    def test_disable_rejects_wrong_password(self, client, auth_header):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        import pyotp
        good_code = pyotp.TOTP(secret).now()

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'hash', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = self._enabled_with_secret(encrypted)
            PS.verify_password.return_value = False
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'wrong', 'code': good_code})
        assert r.status_code == 401
        assert r.json['error'] == 'invalid_credentials'
        MR.return_value.delete_all_methods.assert_not_called()

    def test_disable_rejects_wrong_code(self, client, auth_header):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'hash', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = self._enabled_with_secret(encrypted)
            PS.verify_password.return_value = True
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'pw', 'code': '000000'})
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_code'
        MR.return_value.delete_all_methods.assert_not_called()

    def test_disable_when_no_methods_enrolled_returns_400(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'hash', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = []
            PS.verify_password.return_value = True
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'pw', 'code': '000000'})
        assert r.status_code == 400
        assert r.json['error'] == 'mfa_not_enabled'

    def test_disable_with_all_secrets_corrupt_returns_400_invalid_code(self, client, auth_header):
        """When every enrolled method's secret fails to decrypt, the audit row
        records `secret_decrypt_failed` (not `invalid_code`) so operators can
        investigate."""
        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.MfaService') as MS:
            UR.return_value.get_user_by_username.return_value = {'password_hash': 'hash', 'username': 'alice'}
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'a', 'mfa_secret': 'ENC:corrupt', 'label': 'A'},
            ]
            PS.verify_password.return_value = True
            MS.decrypt_secret.side_effect = ValueError('boom')
            r = client.post('/api/auth/mfa/disable', headers=auth_header,
                            json={'password': 'pw', 'code': '123456'})
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_code'
        MR.return_value.delete_all_methods.assert_not_called()
        AS.return_value.log_auth.assert_any_call(
            action='mfa_disable', success=False, user_id='test-user-id', username='alice',
            ip=ANY, user_agent=ANY, failure_reason='secret_decrypt_failed',
        )


# ---- /mfa/challenge --------------------------------------------------------

class TestMfaChallenge:

    def _seed_challenge(self, app, user_id='u1', token='challtok'):
        """Helper: write the challenge entry into the test cache."""
        from src.api.utils.caching import cache
        with app.app_context():
            cache.set(f'mfa_challenge:{token}', {'user_id': user_id, 'attempts': 0}, timeout=300)
        return token

    def test_challenge_with_correct_totp_returns_tokens(self, app, client):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        import pyotp
        good_code = pyotp.TOTP(secret).now()
        token = self._seed_challenge(app, user_id='u1')

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': encrypted, 'label': 'A'},
            ]
            r = client.post('/api/auth/mfa/challenge',
                            json={'mfa_challenge_token': token, 'code': good_code})
        assert r.status_code == 200
        assert 'access_token' in r.json
        assert 'refresh_token' in r.json
        UR.return_value.update_login_success.assert_called_once_with('u1')

        # Challenge consumed (no replay)
        from src.api.utils.caching import cache
        with app.app_context():
            assert cache.get(f'mfa_challenge:{token}') is None

    def test_challenge_with_wrong_code_increments_attempts_and_returns_400(self, app, client):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        token = self._seed_challenge(app, user_id='u1')

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC):
            UR.return_value.get_user_by_id.return_value = {'id': 'u1', 'username': 'alice',
                                                           'is_active': True}
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': encrypted, 'label': 'A'},
            ]
            r = client.post('/api/auth/mfa/challenge',
                            json={'mfa_challenge_token': token, 'code': '000000'})
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_code'
        # Challenge still exists (allows retry, but attempts incremented)
        from src.api.utils.caching import cache
        with app.app_context():
            entry = cache.get(f'mfa_challenge:{token}')
        assert entry is not None
        assert entry['attempts'] == 1

    def test_challenge_after_5_attempts_invalidates_token(self, app, client):
        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        from src.api.utils.caching import cache
        token = 'tt'
        with app.app_context():
            cache.set(f'mfa_challenge:{token}', {'user_id': 'u1', 'attempts': 4}, timeout=300)

        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC):
            UR.return_value.get_user_by_id.return_value = {'id': 'u1', 'username': 'alice',
                                                           'is_active': True}
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': encrypted, 'label': 'A'},
            ]
            r = client.post('/api/auth/mfa/challenge',
                            json={'mfa_challenge_token': token, 'code': '000000'})
        assert r.status_code == 400  # 5th wrong attempt
        # 6th call: challenge deleted
        r2 = client.post('/api/auth/mfa/challenge',
                         json={'mfa_challenge_token': token, 'code': '000000'})
        assert r2.status_code == 401
        assert r2.json['error'] == 'invalid_or_expired_challenge'

    def test_challenge_with_unknown_token_returns_401(self, client):
        r = client.post('/api/auth/mfa/challenge',
                        json={'mfa_challenge_token': 'nope', 'code': '000000'})
        assert r.status_code == 401
        assert r.json['error'] == 'invalid_or_expired_challenge'

    def test_challenge_with_corrupt_secret_invalidates_and_returns_401(self, app, client):
        token = self._seed_challenge(app, user_id='u1')
        with patch(MFA_REPO) as MR, patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS, \
             patch('src.api.blueprints.auth.MfaService') as MS:
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': 'ENC:bad', 'label': 'A'},
            ]
            MS.decrypt_secret.side_effect = ValueError('boom')
            r = client.post('/api/auth/mfa/challenge',
                            json={'mfa_challenge_token': token, 'code': '000000'})
        assert r.status_code == 401
        assert r.json['error'] == 'invalid_or_expired_challenge'
        # Cache entry was deleted
        with app.app_context():
            from src.api.utils.caching import cache
            assert cache.get(f'mfa_challenge:{token}') is None
        # Failure audit recorded
        AS.return_value.log_auth.assert_any_call(
            action='login_mfa', success=False, user_id='u1', username='alice',
            ip=ANY, user_agent=ANY, failure_reason='secret_decrypt_failed',
        )


# ---- /mfa/backup-codes/regenerate (removed in #609) ------------------------

class TestMfaBackupCodesEndpointRemoved:
    """Locks in that the regenerate-backup-codes endpoint no longer exists."""

    def test_post_returns_404(self, client):
        r = client.post('/api/auth/mfa/backup-codes/regenerate', json={'code': '123456'})
        assert r.status_code == 404

    def test_get_returns_404(self, client):
        r = client.get('/api/auth/mfa/backup-codes/regenerate')
        assert r.status_code == 404
