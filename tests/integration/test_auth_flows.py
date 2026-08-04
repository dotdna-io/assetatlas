# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-PR integration tests for the 7 critical auth user journeys.

These tests are NOT marked @pytest.mark.integration — they use the existing
blueprint_app fixture (mocked DB, no live services) and mock external
services (LDAP, OIDC, SMTP) at the service-class level.

Each test exercises 3-5 endpoints in sequence to verify the cross-PR flows
work end-to-end: JWT issuance, MFA enrollment, LDAP provisioning, OIDC dance,
password reset, invite onboarding, and lockout enforcement.
"""

import pyotp
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, ANY

from flask_jwt_extended import create_access_token, decode_token

from src.api.utils.caching import cache
from src.auth.services.ldap_service import LdapUserInfo
from src.auth.services.mfa_service import MfaService
from src.auth.services.oidc_service import OidcUserInfo


# ---------------------------------------------------------------------------
# Fixture: prime the JWT user cache for the conftest auth_headers identity
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _prime_jwt_cache(blueprint_app):
    """Prime the JWT cache for the test user; clear all cache state on teardown."""
    admin_entry = {
        'id': 'test-user-id',
        'username': 'testuser',
        'role': 'admin',
        'is_active': True,
        'email': 'testuser@test.local',
        'full_name': 'Test Admin',
    }
    with blueprint_app.app_context():
        cache.set('jwt_user:test-user-id', admin_entry, timeout=300)
    yield
    with blueprint_app.app_context():
        cache.clear()  # flush jwt_user:*, jwt_session:*, oidc_exchange:*, etc.


# ---------------------------------------------------------------------------
# Patch-target constants (all at module scope in auth.py)
# ---------------------------------------------------------------------------
_USER_REPO = 'src.api.blueprints.auth.UserRepository'
_SESSION_REPO = 'src.api.blueprints.auth.SessionRepository'
_MFA_REPO = 'src.api.blueprints.auth.UserMfaRepository'
_AUDIT_SVC = 'src.api.blueprints.auth.AuditService'
_PASSWORD_SVC = 'src.api.blueprints.auth.PasswordService'
_TOKEN_SVC = 'src.api.blueprints.auth.TokenService'
_LDAP_SVC = 'src.api.blueprints.auth.LdapService'
_OIDC_SVC = 'src.api.blueprints.auth.OidcService'
_INVITE_SVC = 'src.api.blueprints.auth.InviteService'
_PWD_RESET_REPO = 'src.api.blueprints.auth.PasswordResetRepository'

# Password-reset functions use local imports, so we patch at the source module.
_PW_RESET_SVC_LOCAL = 'src.auth.services.password_reset_service.PasswordResetService'
_USER_REPO_LOCAL = 'src.auth.repositories.user_repository.UserRepository'
_PWD_RESET_REPO_LOCAL = 'src.auth.repositories.password_reset_repository.PasswordResetRepository'
_AUDIT_SVC_LOCAL = 'src.auth.services.audit_service.AuditService'


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _token_meta(jti='jti-flow', username='alice', role='user'):
    return {
        'access_token_jti': jti,
        'user_claims': {'role': role, 'username': username},
    }


def _make_db_user(**overrides):
    base = {
        'id': 'user-uuid-flow',
        'username': 'alice',
        'email': 'alice@example.com',
        'full_name': 'Alice Flow',
        'role': 'user',
        'is_active': True,
        'email_verified': False,
        'must_change_password': False,
        'password_hash': '$2b$12$fakehashfakehashfakehash',
        'last_login_at': None,
        'created_at': datetime(2025, 1, 1),
        'failed_login_attempts': 0,
        'locked_until': None,
    }
    base.update(overrides)
    return base


def _wire_token_service(MockTS):
    """Preset TokenService static method returns for a successful issuance."""
    MockTS.generate_token_pair.return_value = _token_meta()
    MockTS.generate_refresh_token.return_value = 'raw-refresh-tok'
    MockTS.hash_refresh_token.return_value = 'hashed-refresh-tok'
    MockTS.calculate_token_expiration.return_value = datetime.utcnow() + timedelta(days=7)


def _login_local(client, blueprint_app, username='alice', password='pw',
                 db_user=None, mfa_enabled=False):
    """Perform a local login and return the raw response.

    After a successful login the returned real access token will be used for
    subsequent JWT-authenticated requests.  user_lookup_loader reads from the
    cache (key: jwt_user:<user_id>), so we prime it here so the token is
    usable immediately without a live DB.
    """
    db_user = db_user or _make_db_user()
    with patch(_USER_REPO) as UR, \
         patch(_MFA_REPO) as MR, \
         patch(_SESSION_REPO) as SR, \
         patch(_PASSWORD_SVC) as PS, \
         patch(_TOKEN_SVC) as TS, \
         patch(_AUDIT_SVC), \
         patch(_LDAP_SVC) as LDAP:
        LDAP.return_value.is_enabled.return_value = False
        UR.return_value.get_user_by_username.return_value = db_user
        PS.verify_password.return_value = True
        MR.return_value.count_enabled_methods.return_value = 1 if mfa_enabled else 0
        MR.return_value.list_enabled_methods.return_value = (
            [{'id': 'm1', 'mfa_secret': 'ENC:placeholder', 'label': 'A'}]
            if mfa_enabled else []
        )
        SR.return_value.create_session.return_value = 'sid-local'
        _wire_token_service(TS)

        # Also stub the session revocation check (check_if_token_revoked).
        SR.return_value.get_session_by_access_jti.return_value = {'id': 'sid-local', 'is_active': True}

        resp = client.post('/api/auth/login',
                           json={'username': username, 'password': password})

    # Prime the JWT caches so subsequent requests using the real token work:
    # - jwt_user:<id>        → user_lookup_loader won't hit the mocked DB
    # - jwt_session:<jti>    → check_if_token_revoked returns non-revoked
    if resp.status_code == 200 and 'access_token' in (resp.json or {}):
        user_id = db_user['id']
        cache_entry = {
            'id': user_id,
            'username': db_user.get('username', username),
            'role': db_user.get('role', 'user'),
            'is_active': True,
            'email': db_user.get('email', f'{username}@example.com'),
            'full_name': db_user.get('full_name', username),
        }
        with blueprint_app.app_context():
            from flask_jwt_extended import decode_token as _decode
            claims = _decode(resp.json['access_token'])
            jti = claims['jti']
            cache.set(f'jwt_user:{user_id}', cache_entry, timeout=300)
            cache.set(f'jwt_session:{jti}', {'id': 'sid-local', 'is_active': True}, timeout=300)

    return resp


# ---------------------------------------------------------------------------
# Scenario 1: Local user full lifecycle — login → MFA enroll → 2-step login
# ---------------------------------------------------------------------------

def test_local_user_full_lifecycle_login_then_mfa_then_2step_login(
    blueprint_app, client, auth_headers
):
    """
    1. Login without MFA → access_token issued immediately.
    2. POST /mfa/setup   → secret + provisioning_uri returned.
    3. POST /mfa/verify  → MFA enabled.
    4. Login again       → requires_mfa=true + mfa_challenge_token issued.
    5. POST /mfa/challenge with valid TOTP → access_token issued.
    """
    db_user = _make_db_user()

    # ── Step 1: First login (no MFA) ─────────────────────────────────────────
    r1 = _login_local(client, blueprint_app, db_user=db_user, mfa_enabled=False)
    assert r1.status_code == 200, r1.json
    access_token = r1.json.get('access_token')
    assert access_token, 'Expected access_token in first login response'
    assert 'requires_mfa' not in r1.json

    # Verify auth_method=local in the JWT (Task 1 claim)
    with blueprint_app.app_context():
        claims = decode_token(access_token)
    assert claims.get('auth_method') == 'local'

    # ── Step 2: Enroll MFA ────────────────────────────────────────────────────
    headers = {'Authorization': f'Bearer {access_token}'}
    with patch(_MFA_REPO) as MR, patch(_AUDIT_SVC):
        from uuid import UUID
        MR.return_value.replace_pending_method.return_value = UUID(
            '11111111-1111-1111-1111-111111111111'
        )
        r2 = client.post('/api/auth/mfa/setup', headers=headers)

    assert r2.status_code == 200, r2.json
    setup_body = r2.json
    assert 'secret' in setup_body
    assert 'provisioning_uri' in setup_body
    secret = setup_body['secret']

    # ── Step 3: Verify TOTP code → enable MFA ────────────────────────────────
    encrypted = MfaService.encrypt_secret(secret)
    good_code = pyotp.TOTP(secret).now()

    with patch(_MFA_REPO) as MR, patch(_USER_REPO), patch(_AUDIT_SVC):
        MR.return_value.get_pending_method.return_value = {
            'id': 'pending-id',
            'mfa_enabled': False,
            'mfa_secret': encrypted,
            'label': 'Authenticator app',
        }
        r3 = client.post('/api/auth/mfa/verify',
                         headers=headers, json={'code': good_code})

    assert r3.status_code == 200, r3.json
    assert r3.json.get('enabled') is True

    # ── Step 4: Second login — MFA challenge issued ───────────────────────────
    r4 = _login_local(client, blueprint_app, db_user=db_user, mfa_enabled=True)
    assert r4.status_code == 200, r4.json
    assert r4.json.get('requires_mfa') is True
    challenge_token = r4.json.get('mfa_challenge_token')
    assert challenge_token
    assert 'access_token' not in r4.json

    # ── Step 5: MFA challenge → final tokens ─────────────────────────────────
    # Prime the cache for db_user so the challenge-exchange token is usable.
    with blueprint_app.app_context():
        cache.set(f'jwt_user:{db_user["id"]}', {
            'id': db_user['id'], 'username': db_user['username'],
            'role': db_user['role'], 'is_active': True,
            'email': db_user['email'], 'full_name': db_user.get('full_name', ''),
        }, timeout=300)

    new_code = pyotp.TOTP(secret).now()
    with patch(_MFA_REPO) as MR, patch(_USER_REPO) as UR, \
         patch(_SESSION_REPO) as SR, patch(_TOKEN_SVC) as TS, patch(_AUDIT_SVC):
        UR.return_value.get_user_by_id.return_value = db_user
        MR.return_value.list_enabled_methods.return_value = [
            {'id': 'm1', 'mfa_secret': encrypted, 'label': 'Authenticator app'},
        ]
        SR.return_value.create_session.return_value = 'sid-mfa'
        _wire_token_service(TS)

        r5 = client.post('/api/auth/mfa/challenge',
                         json={'mfa_challenge_token': challenge_token, 'code': new_code})

    assert r5.status_code == 200, r5.json
    assert 'access_token' in r5.json
    assert 'refresh_token' in r5.json

    # Confirm auth_method=local in the challenge-exchange token too
    with blueprint_app.app_context():
        ch_claims = decode_token(r5.json['access_token'])
    assert ch_claims.get('auth_method') == 'local'


# ---------------------------------------------------------------------------
# Scenario 2: LDAP login provisions user, second login finds existing record
# ---------------------------------------------------------------------------

def test_ldap_login_provisions_then_second_login_finds_existing(
    blueprint_app, client
):
    """
    1. LDAP login → user provisioned, tokens issued, auth_method=ldap in JWT.
    2. LDAP login again → same user dict returned (no new row).
    Both calls should succeed with 200 + access_token.
    """
    ldap_info = LdapUserInfo(
        username='alice',
        user_dn='CN=alice,DC=corp,DC=com',
        email='alice@corp.com',
        full_name='Alice Corp',
        role='user',
        groups=[],
    )
    provisioned_user = {
        'id': 'ldap-user-uuid',
        'username': 'alice',
        'email': 'alice@corp.com',
        'role': 'user',
        'is_active': True,
        'created_at': '2025-01-01T00:00:00Z',
        'failed_login_attempts': 0,
        'locked_until': None,
    }

    # Track call count so both calls return the same user dict.
    # get_or_create_ldap_user is an instance method called with keyword args:
    # user_repo.get_or_create_ldap_user(username=..., email=..., full_name=..., role=...)
    call_count = {'n': 0}

    def _get_or_create(**kwargs):
        call_count['n'] += 1
        return provisioned_user

    with patch(_LDAP_SVC) as LDAP, \
         patch(_USER_REPO) as UR, \
         patch(_MFA_REPO) as MR, \
         patch(_SESSION_REPO) as SR, \
         patch(_TOKEN_SVC) as TS, \
         patch(_AUDIT_SVC) as AS:
        ldap_svc = LDAP.return_value
        ldap_svc.is_enabled.return_value = True
        ldap_svc.authenticate.return_value = ldap_info
        UR.return_value.get_or_create_ldap_user.side_effect = _get_or_create
        MR.return_value.count_enabled_methods.return_value = 0
        SR.return_value.create_session.return_value = 'sid-ldap'
        SR.return_value.get_session_by_access_jti.return_value = {
            'id': 'sid-ldap', 'is_active': True,
        }
        _wire_token_service(TS)

        # First login
        r1 = client.post('/api/auth/login',
                         json={'username': 'alice', 'password': 'ldap-pass'})
        assert r1.status_code == 200, r1.json
        assert 'access_token' in r1.json

        # Prime cache so the token is usable
        with blueprint_app.app_context():
            claims1 = decode_token(r1.json['access_token'])
            cache.set(f'jwt_user:{provisioned_user["id"]}', {
                'id': provisioned_user['id'],
                'username': provisioned_user['username'],
                'role': provisioned_user['role'],
                'is_active': True,
                'email': provisioned_user['email'],
                'full_name': 'Alice Corp',
            }, timeout=300)

        assert claims1.get('auth_method') == 'ldap'

        # Check audit row recorded login_ldap
        success_calls = [
            c for c in AS.return_value.log_auth.call_args_list
            if c.kwargs.get('success') is True
        ]
        assert any(c.kwargs.get('action') == 'login_ldap' for c in success_calls)

        # Second login — existing user found (get_or_create called again)
        r2 = client.post('/api/auth/login',
                         json={'username': 'alice', 'password': 'ldap-pass'})
        assert r2.status_code == 200, r2.json
        assert 'access_token' in r2.json

    # Both calls went to get_or_create (repository decides new vs existing)
    assert call_count['n'] == 2


# ---------------------------------------------------------------------------
# Scenario 3: OIDC full dance — login → callback → exchange
# ---------------------------------------------------------------------------

def test_oidc_full_dance_login_then_callback_then_exchange(
    blueprint_app, client
):
    """
    1. POST /oidc/login        → authorization_url returned.
    2. GET  /oidc/callback     → 302 to /oidc-complete?code=<exchange_code>.
    3. POST /oidc/exchange     → access_token + auth_method=oidc in JWT.
    """
    oidc_user_info = OidcUserInfo(
        subject='oidc-sub-abc',
        email='alice@idp.example.com',
        full_name='Alice IdP',
        role='user',
        groups=[],
    )
    provisioned_oidc_user = {
        'id': 'oidc-user-uuid',
        'username': 'alice',
        'email': 'alice@idp.example.com',
        'full_name': 'Alice IdP',
        'role': 'user',
        'is_active': True,
        'email_verified': True,
        'must_change_password': False,
        'last_login_at': None,
        'created_at': '2025-01-01T00:00:00',
        'password_hash': 'OIDC:unusable',
    }

    # ── Step 1: POST /oidc/login ──────────────────────────────────────────────
    with patch(_OIDC_SVC) as OidcCls, patch(_AUDIT_SVC):
        svc = OidcCls.return_value
        svc.is_enabled.return_value = True
        svc.build_authorization_url.return_value = (
            'https://idp.example.com/authorize?client_id=x&state=state-xyz',
            'state-xyz',
        )
        r1 = client.post('/api/auth/oidc/login')

    assert r1.status_code == 200, r1.json
    assert 'authorization_url' in r1.json

    # ── Step 2: GET /oidc/callback ────────────────────────────────────────────
    blueprint_app.settings_manager.get = (
        lambda cat, key, default=None: True if key == 'auto_create_users' else default
    )

    with patch(_OIDC_SVC) as OidcCls, \
         patch(_USER_REPO) as UR, \
         patch(_AUDIT_SVC):
        svc = OidcCls.return_value
        svc.is_enabled.return_value = True
        svc.exchange_code.return_value = oidc_user_info
        svc.frontend_complete_url.return_value = '/oidc-complete'
        UR.return_value.get_or_create_oidc_user.return_value = provisioned_oidc_user

        r2 = client.get('/api/auth/oidc/callback?code=auth-code-xyz&state=state-xyz')

    assert r2.status_code == 302, r2.data
    location = r2.headers['Location']
    assert '/oidc-complete' in location
    assert 'code=' in location

    # Extract the one-time exchange code from the redirect URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    exchange_code = qs['code'][0]

    # ── Step 3: POST /oidc/exchange ───────────────────────────────────────────
    with blueprint_app.app_context():
        cache.set(f'oidc_exchange:{exchange_code}',
                  {'user_id': 'oidc-user-uuid'}, timeout=300)

    with patch(_USER_REPO) as UR, \
         patch(_SESSION_REPO) as SR, \
         patch(_AUDIT_SVC), \
         patch(_TOKEN_SVC) as TS:
        UR.return_value.get_user_by_id.return_value = provisioned_oidc_user
        SR.return_value.create_session.return_value = 'sid-oidc'
        _wire_token_service(TS)

        r3 = client.post('/api/auth/oidc/exchange', json={'code': exchange_code})

    assert r3.status_code == 200, r3.json
    assert 'access_token' in r3.json
    assert r3.json['user']['is_oidc_user'] is True
    assert r3.json['user']['is_ldap_user'] is False

    with blueprint_app.app_context():
        claims = decode_token(r3.json['access_token'])
    assert claims.get('auth_method') == 'oidc'

    # Cache entry consumed (single-use)
    with blueprint_app.app_context():
        assert cache.get(f'oidc_exchange:{exchange_code}') is None

    # Restore default settings_manager behaviour
    blueprint_app.settings_manager.get = lambda cat, key, default=None: default


# ---------------------------------------------------------------------------
# Scenario 4: Password reset full flow
# ---------------------------------------------------------------------------

def test_password_reset_request_then_verify_then_login_with_new_password(
    blueprint_app, client
):
    """
    1. POST /password-reset/request with known email → 200 (generic success).
    2. POST /password-reset/verify with token + new_password → 200 + username.
    3. POST /login with new_password → 200 + access_token.

    The password-reset endpoints use local imports inside the function body,
    so we patch at the source module paths.
    """
    known_user = _make_db_user()
    raw_token = 't' * 64  # deterministic token for the test

    blueprint_app.email_service.send_password_reset_email.return_value = True
    blueprint_app.email_service.send_password_changed_notification.return_value = True
    blueprint_app.settings_manager.get = lambda cat, key, default=None: default

    # ── Step 1: Request password reset ───────────────────────────────────────
    with patch(_USER_REPO_LOCAL) as UR_loc, \
         patch(_PWD_RESET_REPO_LOCAL), \
         patch(_PW_RESET_SVC_LOCAL) as RS_loc, \
         patch(_AUDIT_SVC_LOCAL):
        UR_loc.return_value.get_user_by_email.return_value = known_user
        RS_loc.check_rate_limit.return_value = True
        RS_loc.TOKEN_EXPIRATION_HOURS = 1
        RS_loc.create_reset_token.return_value = {
            'token': raw_token,
            'token_id': 'reset-uuid-1',
            'expires_at': datetime.utcnow() + timedelta(hours=1),
            'user_email': known_user['email'],
            'username': known_user['username'],
        }

        r1 = client.post('/api/auth/password-reset/request',
                         json={'email': known_user['email']})

    assert r1.status_code == 200, r1.json
    assert r1.json.get('success') is True

    # ── Step 2: Verify token + set new password ───────────────────────────────
    with patch(_USER_REPO_LOCAL) as UR_loc, \
         patch(_PWD_RESET_REPO_LOCAL), \
         patch('src.auth.repositories.session_repository.SessionRepository'), \
         patch(_PW_RESET_SVC_LOCAL) as RS_loc, \
         patch(_AUDIT_SVC_LOCAL):
        RS_loc.validate_and_use_token.return_value = {
            'success': True,
            'username': known_user['username'],
            'user_id': known_user['id'],
        }
        UR_loc.return_value.get_user_by_id.return_value = known_user

        r2 = client.post('/api/auth/password-reset/verify',
                         json={'token': raw_token, 'new_password': 'NewPass-123!'})

    assert r2.status_code == 200, r2.json
    assert r2.json.get('success') is True
    assert r2.json.get('username') == known_user['username']

    # ── Step 3: Login with the new password ───────────────────────────────────
    r3 = _login_local(client, blueprint_app, db_user=known_user)
    assert r3.status_code == 200, r3.json
    assert 'access_token' in r3.json


# ---------------------------------------------------------------------------
# Scenario 5: Invite-only onboarding — admin creates → validate → complete → login
# ---------------------------------------------------------------------------

def test_invite_only_admin_create_then_validate_then_complete_then_login(
    blueprint_app, client, auth_headers
):
    """
    1. Admin POST /users with invite_only=true → 201 + invite_url.
    2. GET /onboarding/validate?token=X       → 200, valid=true.
    3. POST /onboarding/complete              → 200, success=true.
    4. POST /login with new password          → 200 + access_token.
    """
    user_uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    raw_invite_token = 'i' * 64  # deterministic token

    blueprint_app.email_service.send_invite_email.return_value = True

    def _settings_get(category, key, default=None):
        if category == 'security' and key == 'public_base_url':
            return 'https://example.test'
        if category == 'auth' and key == 'invite_token_ttl_hours':
            return 168
        return default

    blueprint_app.settings_manager.get = _settings_get

    new_user_db = _make_db_user(id=user_uuid, username='newuser',
                                email='new@example.com',
                                password_hash='$2b$12$fakehash')

    # ── Step 1: Admin creates invite-only user ────────────────────────────────
    with patch(_USER_REPO) as UR, \
         patch(_INVITE_SVC) as INV, \
         patch(_PWD_RESET_REPO), \
         patch(_AUDIT_SVC):
        UR.return_value.get_user_by_username.return_value = None
        UR.return_value.get_user_by_email.return_value = None
        UR.return_value.create_user.return_value = {'id': user_uuid}
        UR.return_value.get_user_by_id.return_value = new_user_db
        INV.DEFAULT_TTL_HOURS = 168
        INV.create_invite_token.return_value = {
            'token': raw_invite_token,
            'token_id': 'invite-token-uuid',
            'expires_at': datetime.utcnow() + timedelta(hours=168),
            'user_email': 'new@example.com',
            'username': 'newuser',
        }

        r1 = client.post(
            '/api/auth/users',
            json={
                'username': 'newuser',
                'email': 'new@example.com',
                'full_name': 'New User',
                'role': 'user',
                'invite_only': True,
            },
            headers=auth_headers('admin'),
        )

    assert r1.status_code == 201, r1.json
    assert 'invite_url' in r1.json
    assert raw_invite_token in r1.json['invite_url']

    # ── Step 2: Validate invite token ─────────────────────────────────────────
    with patch(_INVITE_SVC) as INV, \
         patch(_PWD_RESET_REPO), \
         patch(_AUDIT_SVC):
        INV.validate_invite_token.return_value = {
            'valid': True,
            'username': 'newuser',
            'email': 'new@example.com',
            'requires_mfa': False,
        }

        r2 = client.get(f'/api/auth/onboarding/validate?token={raw_invite_token}')

    assert r2.status_code == 200, r2.json
    assert r2.json.get('valid') is True
    assert r2.json.get('username') == 'newuser'

    # ── Step 3: Complete onboarding ───────────────────────────────────────────
    with patch(_INVITE_SVC) as INV, \
         patch(_PWD_RESET_REPO), \
         patch(_AUDIT_SVC):
        INV.consume_invite_token.return_value = {
            'success': True,
            'user_id': user_uuid,
            'username': 'newuser',
        }

        r3 = client.post('/api/auth/onboarding/complete',
                         json={'token': raw_invite_token, 'new_password': 'Str0ng-Pass!'})

    assert r3.status_code == 200, r3.json
    assert r3.json.get('success') is True
    assert r3.json.get('username') == 'newuser'

    # ── Step 4: Login with new credentials ────────────────────────────────────
    r4 = _login_local(client, blueprint_app, username='newuser', db_user=new_user_db)
    assert r4.status_code == 200, r4.json
    assert 'access_token' in r4.json

    # Restore default
    blueprint_app.settings_manager.get = lambda cat, key, default=None: default


# ---------------------------------------------------------------------------
# Scenario 6: MFA setup blocked for LDAP and OIDC users
# ---------------------------------------------------------------------------

def test_mfa_blocked_for_external_users(blueprint_app, client):
    """
    LDAP user logs in → tries /mfa/setup → 400 mfa_not_supported_for_external_users.
    OIDC user logs in → tries /mfa/setup → 400 mfa_not_supported_for_external_users.

    The check is done via the auth_method JWT claim — no DB lookup needed.
    """
    # ── LDAP token ────────────────────────────────────────────────────────────
    with blueprint_app.app_context():
        ldap_token = create_access_token(
            identity='ldap-user-id',
            additional_claims={'role': 'user', 'username': 'alice', 'auth_method': 'ldap'},
        )

    ldap_headers = {'Authorization': f'Bearer {ldap_token}'}

    # Seed the JWT user cache so user_lookup_loader can return a user dict
    with blueprint_app.app_context():
        cache.set('jwt_user:ldap-user-id', {
            'id': 'ldap-user-id',
            'username': 'alice',
            'role': 'user',
            'is_active': True,
            'email': 'alice@corp.com',
            'full_name': 'Alice LDAP',
        }, timeout=300)

    with patch(_MFA_REPO) as MR, patch(_AUDIT_SVC):
        r_ldap = client.post('/api/auth/mfa/setup', headers=ldap_headers)

    assert r_ldap.status_code == 400, r_ldap.json
    assert r_ldap.json.get('error') == 'mfa_not_supported_for_external_users'
    MR.return_value.replace_pending_method.assert_not_called()

    # ── OIDC token ────────────────────────────────────────────────────────────
    with blueprint_app.app_context():
        oidc_token = create_access_token(
            identity='oidc-user-id',
            additional_claims={'role': 'user', 'username': 'alice', 'auth_method': 'oidc'},
        )
        cache.set('jwt_user:oidc-user-id', {
            'id': 'oidc-user-id',
            'username': 'alice',
            'role': 'user',
            'is_active': True,
            'email': 'alice@idp.com',
            'full_name': 'Alice OIDC',
        }, timeout=300)

    oidc_headers = {'Authorization': f'Bearer {oidc_token}'}

    with patch(_MFA_REPO) as MR2, patch(_AUDIT_SVC):
        r_oidc = client.post('/api/auth/mfa/setup', headers=oidc_headers)

    assert r_oidc.status_code == 400, r_oidc.json
    assert r_oidc.json.get('error') == 'mfa_not_supported_for_external_users'
    MR2.return_value.replace_pending_method.assert_not_called()

    # Cleanup
    with blueprint_app.app_context():
        cache.delete('jwt_user:ldap-user-id')
        cache.delete('jwt_user:oidc-user-id')


# ---------------------------------------------------------------------------
# Scenario 7: Account lockout respected even when LDAP authentication succeeds
# ---------------------------------------------------------------------------

def test_local_lockout_blocks_ldap_login_path_too(blueprint_app, client):
    """
    Regression for PR 5 lockout-bypass fix.

    LDAP credentials are valid, but the provisioned local user is locked.
    The login endpoint must still return 401 account_locked — LDAP success
    cannot bypass the local lockout check.
    """
    ldap_info = LdapUserInfo(
        username='alice',
        user_dn='CN=alice,DC=corp,DC=com',
        email='alice@corp.com',
        full_name='Alice Corp',
        role='user',
        groups=[],
    )
    locked_user = {
        'id': 'locked-user-uuid',
        'username': 'alice',
        'email': 'alice@corp.com',
        'role': 'user',
        'is_active': True,
        'created_at': '2025-01-01T00:00:00Z',
        'failed_login_attempts': 6,
        'locked_until': datetime.utcnow() + timedelta(minutes=30),
    }

    with patch(_LDAP_SVC) as LDAP, \
         patch(_USER_REPO) as UR, \
         patch(_AUDIT_SVC) as AS:
        ldap_svc = LDAP.return_value
        ldap_svc.is_enabled.return_value = True
        ldap_svc.authenticate.return_value = ldap_info  # LDAP succeeds
        UR.return_value.get_or_create_ldap_user.return_value = locked_user

        r = client.post('/api/auth/login',
                        json={'username': 'alice', 'password': 'ldap-pass'})

    assert r.status_code == 401, r.json
    assert r.json.get('error') == 'account_locked'

    # Audit trail must record the lockout
    AS.return_value.log_auth.assert_any_call(
        action='login',
        success=False,
        user_id='locked-user-uuid',
        username='alice',
        failure_reason='account_locked',
        ip=ANY,
        user_agent=ANY,
    )
