# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the self-service /api/account/* blueprint (#612)."""
from datetime import timedelta
from unittest.mock import patch, MagicMock

import pytest
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from src.api.blueprints.account import account_bp
from src.api.blueprints.auth import auth_bp  # for mfa_setup/mfa_verify reuse


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['JWT_SECRET_KEY'] = 'test-secret'
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(seconds=900)
    app.config['CACHE_TYPE'] = 'SimpleCache'
    jwt = JWTManager(app)
    from src.api.utils.caching import cache
    cache.init_app(app)

    test_user = {
        'id': 'test-user-id',
        'username': 'alice',
        'email': 'alice@test.com',
        'role': 'user',
        'is_active': True,
        'password_hash': '$2b$12$abc',
    }

    @jwt.user_identity_loader
    def _id(user):
        return str(user['id']) if isinstance(user, dict) else str(user)

    @jwt.user_lookup_loader
    def _lookup(_h, _data):
        return test_user

    app.connection_manager = MagicMock()
    app.settings_manager = MagicMock()
    app.settings_manager.get.return_value = 20

    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_header(app):
    user = {'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com', 'role': 'user'}
    with app.app_context():
        token = create_access_token(identity=user, additional_claims={'auth_method': 'local'})
    return {'Authorization': f'Bearer {token}'}


MFA_REPO = 'src.api.blueprints.account.UserMfaRepository'
USER_REPO = 'src.api.blueprints.account.UserRepository'
AUDIT_SVC = 'src.api.blueprints.account.AuditService'


class TestAccountMfaList:
    def test_returns_empty_when_no_methods(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.list_methods.return_value = []
            r = client.get('/api/account/mfa', headers=auth_header)
        assert r.status_code == 200
        assert r.json == {'methods': []}

    def test_returns_each_enrolled_method(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.list_methods.return_value = [
                {
                    'id': 'aaaa1111-1111-1111-1111-111111111111',
                    'user_id': 'test-user-id', 'type': 'totp',
                    'label': 'Phone', 'mfa_enabled': True,
                    'mfa_secret': 'ENC:a', 'created_at': None,
                },
                {
                    'id': 'bbbb2222-2222-2222-2222-222222222222',
                    'user_id': 'test-user-id', 'type': 'totp',
                    'label': 'Tablet', 'mfa_enabled': True,
                    'mfa_secret': 'ENC:b', 'created_at': None,
                },
            ]
            r = client.get('/api/account/mfa', headers=auth_header)
        assert r.status_code == 200
        methods = r.json['methods']
        assert len(methods) == 2
        assert {m['label'] for m in methods} == {'Phone', 'Tablet'}
        # Real UUIDs round-tripped, not the placeholder 'totp' from the singleton era.
        assert all(len(m['id']) == 36 for m in methods)
        assert all(m['type'] == 'totp' for m in methods)
        assert all(m['enabled'] is True for m in methods)

    def test_pending_methods_carry_enabled_false_flag(self, client, auth_header):
        """Pending rows are returned but flagged so the UI can hide them."""
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC), patch(USER_REPO):
            MR.return_value.list_methods.return_value = [
                {
                    'id': 'cccc3333-3333-3333-3333-333333333333',
                    'user_id': 'test-user-id', 'type': 'totp',
                    'label': 'Pending', 'mfa_enabled': False,
                    'mfa_secret': 'ENC:c', 'created_at': None,
                },
            ]
            r = client.get('/api/account/mfa', headers=auth_header)
        assert r.status_code == 200
        assert r.json['methods'][0]['enabled'] is False


class TestAccountProfileUpdate:
    def test_updates_full_name(self, client, auth_header):
        with patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS:
            UR.return_value.update_user.return_value = {
                'id': 'test-user-id', 'username': 'alice',
                'email': 'alice@test.com', 'full_name': 'Alice Doe',
                'role': 'user', 'is_active': True,
                'created_at': None, 'updated_at': None,
            }
            r = client.patch(
                '/api/account/profile',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'full_name': 'Alice Doe'},
            )
        assert r.status_code == 200
        UR.return_value.update_user.assert_called_once_with(
            'test-user-id', full_name='Alice Doe'
        )
        AS.return_value.log_auth.assert_called_once()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_display_name_changed_self'
        assert kwargs['success'] is True

    def test_invalidates_jwt_user_cache(self, client, auth_header):
        # Without the cache.delete call, the next /auth/me would serve the
        # stale dict from the user_lookup_loader (60s TTL) and the change
        # would appear not to persist.
        with patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.utils.caching.cache') as CACHE:
            UR.return_value.update_user.return_value = {
                'id': 'test-user-id', 'username': 'alice',
                'email': 'alice@test.com', 'full_name': 'Alice Doe',
                'role': 'user', 'is_active': True,
                'created_at': None, 'updated_at': None,
            }
            r = client.patch(
                '/api/account/profile',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'full_name': 'Alice Doe'},
            )
        assert r.status_code == 200
        CACHE.delete.assert_any_call('jwt_user:test-user-id')

    def test_rejects_invalid_full_name(self, client, auth_header):
        r = client.patch(
            '/api/account/profile',
            headers={**auth_header, 'Content-Type': 'application/json'},
            json={'full_name': 'x' * 500},
        )
        assert r.status_code == 400
        assert r.json['error'] == 'validation_error'

    def test_rejects_empty_body(self, client, auth_header):
        # An empty body or one with no recognised fields should not silently
        # succeed — distinguishes "nothing to do" from "updated".
        with patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS:
            r = client.patch(
                '/api/account/profile',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'no_fields_to_update'
        UR.return_value.update_user.assert_not_called()
        AS.return_value.log_auth.assert_not_called()


class TestAccountPasswordChange:
    def test_changes_password_for_local_user(self, client, auth_header):
        with patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS, \
             patch('src.api.blueprints.account.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$realhash',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            PS.verify_password.return_value = True
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            PS.hash_password.return_value = '$2b$12$newhash'

            r = client.post(
                '/api/account/password',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'current_password': 'OldPass1!', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 200
        # Must use get_user_by_username, not get_user_by_id — the latter
        # strips password_hash, so verify_password would raise KeyError.
        UR.return_value.get_user_by_username.assert_called_once_with('alice')
        UR.return_value.get_user_by_id.assert_not_called()
        UR.return_value.update_password.assert_called_once_with(
            'test-user-id', '$2b$12$newhash'
        )
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_password_changed_self'
        assert kwargs['success'] is True

    def test_invalidates_jwt_user_cache(self, client, auth_header):
        # update_password clears must_change_password in DB; the cache must
        # be dropped so a user who just satisfied a forced change isn't
        # redirected back to /account?force=password for the cache TTL.
        with patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.account.PasswordService') as PS, \
             patch('src.api.utils.caching.cache') as CACHE:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$realhash',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            PS.verify_password.return_value = True
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            PS.hash_password.return_value = '$2b$12$newhash'

            r = client.post(
                '/api/account/password',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'current_password': 'OldPass1!', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 200
        CACHE.delete.assert_any_call('jwt_user:test-user-id')

    def test_rejects_for_ldap_user(self, client, auth_header):
        with patch(USER_REPO) as UR, patch(AUDIT_SVC):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True,
                'password_hash': 'LDAP:xxx',
            }
            UR.is_external_user = staticmethod(lambda u: True)
            r = client.post(
                '/api/account/password',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'current_password': 'something1', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 403
        assert r.json['error'] == 'managed_by_idp'

    def test_rejects_wrong_current_password(self, client, auth_header):
        with patch(USER_REPO) as UR, patch(AUDIT_SVC) as AS, \
             patch('src.api.blueprints.account.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$realhash',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            PS.verify_password.return_value = False

            r = client.post(
                '/api/account/password',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'current_password': 'wrong123', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 401
        assert r.json['error'] == 'invalid_credentials'
        UR.return_value.update_password.assert_not_called()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_password_changed_self'
        assert kwargs['success'] is False

    def test_rejects_weak_new_password(self, client, auth_header):
        # ChangePasswordRequest validates min_length=8 in pydantic, so a short
        # password fails BEFORE PasswordService.validate_password_strength runs.
        # Use a long-but-weak password instead.
        with patch(USER_REPO) as UR, patch(AUDIT_SVC), \
             patch('src.api.blueprints.account.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'test-user-id', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$realhash',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            PS.verify_password.return_value = True
            PS.validate_password_strength.return_value = {
                'valid': False, 'errors': ['no special character'],
            }
            r = client.post(
                '/api/account/password',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'current_password': 'OldPass1!', 'new_password': 'allLowerCase'},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'weak_password'


class TestAccountMfaTotpSetup:
    def _setup_mocks(self, MR, *, method_id='11111111-1111-1111-1111-111111111111'):
        from uuid import UUID
        MR.return_value.replace_pending_method.return_value = UUID(method_id)

    def test_starts_setup_when_no_methods(self, client, auth_header):
        with patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'):
            self._setup_mocks(MR)
            r = client.post('/api/account/mfa/totp/setup', headers=auth_header)
        assert r.status_code == 200
        assert 'method_id' in r.json
        assert 'secret' in r.json
        assert 'provisioning_uri' in r.json

    def test_setup_allowed_for_add_another(self, client, auth_header):
        """Multi-method: re-running setup is the 'Add another method' path —
        must not reject when verified methods already exist."""
        with patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'):
            self._setup_mocks(MR)
            MR.return_value.count_enabled_methods.return_value = 2
            r = client.post('/api/account/mfa/totp/setup', headers=auth_header)
        assert r.status_code == 200
        assert r.json.get('error') is None

    def test_setup_accepts_custom_label(self, client, auth_header):
        with patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'):
            self._setup_mocks(MR)
            r = client.post(
                '/api/account/mfa/totp/setup',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'label': 'Tablet'},
            )
        assert r.status_code == 200
        assert r.json['label'] == 'Tablet'


class TestAccountMfaTotpVerify:
    def test_completes_enrollment(self, client, auth_header):
        pending_id = '22222222-2222-2222-2222-222222222222'
        with patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.MfaService') as MS, \
             patch('src.api.blueprints.account.AuditService') as AS_ACCT:
            MR.return_value.get_pending_method.return_value = {
                'id': pending_id, 'mfa_enabled': False,
                'mfa_secret': 'ENC:x', 'label': 'Phone',
            }
            MS.decrypt_secret.return_value = 'BASE32SECRET'
            MS.verify_totp.return_value = True
            r = client.post(
                '/api/account/mfa/totp/verify',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'code': '123456'},
            )
        assert r.status_code == 200
        assert r.json == {'enabled': True}
        MR.return_value.enable_method.assert_called_once_with(pending_id)
        # account-blueprint emits its own audit row in addition to the
        # mfa_enabled row from the delegated handler
        actions = [c.kwargs.get('action') for c in AS_ACCT.return_value.log_auth.call_args_list]
        assert 'user_mfa_method_added' in actions


class TestAccountMfaDelete:
    """Endpoint delegates to delete_method_safely (atomic check+delete under
    a per-user advisory lock). The handler just maps the {deleted, reason}
    result to the right HTTP status."""

    METHOD_ID = '33333333-3333-3333-3333-333333333333'

    def _target(self, *, mfa_enabled=True, label='Phone', user_id='test-user-id'):
        return {
            'id': self.METHOD_ID, 'user_id': user_id,
            'type': 'totp', 'label': label,
            'mfa_enabled': mfa_enabled, 'mfa_secret': 'ENC:x',
        }

    def test_rejects_when_only_enabled_method(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            MR.return_value.delete_method_safely.return_value = {
                'deleted': False,
                'reason': 'must_keep_one_mfa_method',
                'method': self._target(),
            }
            r = client.delete(f'/api/account/mfa/{self.METHOD_ID}', headers=auth_header)
        assert r.status_code == 400
        assert r.json['error'] == 'must_keep_one_mfa_method'

    def test_removes_when_other_enabled_methods_exist(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC) as AS:
            MR.return_value.delete_method_safely.return_value = {
                'deleted': True, 'method': self._target(),
            }
            r = client.delete(f'/api/account/mfa/{self.METHOD_ID}', headers=auth_header)
        assert r.status_code == 200
        MR.return_value.delete_method_safely.assert_called_once()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_mfa_method_removed'

    def test_removing_pending_row(self, client, auth_header):
        """The repo handles the pending-vs-enabled distinction; the handler
        just trusts the 'deleted: True' result."""
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            MR.return_value.delete_method_safely.return_value = {
                'deleted': True, 'method': self._target(mfa_enabled=False),
            }
            r = client.delete(f'/api/account/mfa/{self.METHOD_ID}', headers=auth_header)
        assert r.status_code == 200

    def test_returns_404_when_method_not_found(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            MR.return_value.delete_method_safely.return_value = {
                'deleted': False, 'reason': 'method_not_found', 'method': None,
            }
            r = client.delete(f'/api/account/mfa/{self.METHOD_ID}', headers=auth_header)
        assert r.status_code == 404
        assert r.json['error'] == 'method_not_found'

    def test_returns_404_when_method_belongs_to_other_user(self, client, auth_header):
        """The repo's WHERE clause filters by (id, user_id), so a cross-user
        attempt yields the same not_found result."""
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            MR.return_value.delete_method_safely.return_value = {
                'deleted': False, 'reason': 'method_not_found', 'method': None,
            }
            r = client.delete(f'/api/account/mfa/{self.METHOD_ID}', headers=auth_header)
        assert r.status_code == 404
        assert r.json['error'] == 'method_not_found'

    def test_returns_404_for_non_uuid_id(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            r = client.delete('/api/account/mfa/not-a-uuid', headers=auth_header)
        assert r.status_code == 404
        assert r.json['error'] == 'method_not_found'
        # Bad input is rejected before touching the repo.
        MR.return_value.delete_method_safely.assert_not_called()


class TestAccountMfaRename:
    METHOD_ID = '55555555-5555-5555-5555-555555555555'

    def test_renames_own_method(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC) as AS:
            MR.return_value.update_label_safely.return_value = True
            r = client.patch(
                f'/api/account/mfa/{self.METHOD_ID}',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'label': 'New name'},
            )
        assert r.status_code == 200
        MR.return_value.update_label_safely.assert_called_once()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_mfa_method_renamed'

    def test_rejects_empty_label(self, client, auth_header):
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            r = client.patch(
                f'/api/account/mfa/{self.METHOD_ID}',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'label': ''},
            )
        assert r.status_code == 400
        MR.return_value.update_label_safely.assert_not_called()

    def test_returns_404_when_method_belongs_to_other_user(self, client, auth_header):
        """update_label_safely returns False when (id, user_id) doesn't
        match — handler maps that to 404."""
        with patch(MFA_REPO) as MR, patch(AUDIT_SVC):
            MR.return_value.update_label_safely.return_value = False
            r = client.patch(
                f'/api/account/mfa/{self.METHOD_ID}',
                headers={**auth_header, 'Content-Type': 'application/json'},
                json={'label': 'mine now'},
            )
        assert r.status_code == 404
