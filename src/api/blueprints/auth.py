# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Authentication Blueprint

Provides authentication and authorization endpoints for user management.

Endpoints:
- POST /api/auth/login - Authenticate user and obtain JWT tokens
- POST /api/auth/register - Register new user account
- POST /api/auth/refresh - Refresh access token using refresh token
- POST /api/auth/logout - Logout and revoke session
- GET /api/auth/me - Get current user information
"""

import logging
import secrets
from typing import Optional
from flask import Blueprint, request, jsonify, current_app, redirect
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
    current_user,
)
from pydantic import ValidationError
from datetime import datetime, timedelta

from src.api.middleware.error_handler import handle_errors
from src.api.utils.caching import cache
from src.api.utils.public_base_url import resolve_public_base_url
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin, require_self_or_admin, ROLE_HIERARCHY
from src.auth.models.auth import (
    LoginRequest,
    RegisterRequest,
    LoginResponse,
    LoginMfaRequiredResponse,
    LoginMfaEnrollmentRequiredResponse,
    MfaChallengeRequest,
    MfaEnrollSetupRequest,
    MfaEnrollVerifyRequest,
    RefreshTokenResponse,
    UserResponse,
    PasswordResetRequestModel,
    PasswordResetVerifyModel,
    CreateUserAdminRequest,
    CreateUserAdminResponse,
    UpdateUserAdminRequest,
    UserListParams,
    UserListResponse,
    ResendInviteResponse,
    OnboardingValidateResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    MfaStatusResponse,
    MfaSetupRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    MfaVerifyResponse,
    MfaDisableRequest,
    LdapTestRequest,
    LdapTestResponse,
    OidcTestDiscoveryResponse,
    OidcLoginResponse,
    OidcExchangeRequest,
)
from src.auth.repositories.user_repository import UserRepository
from src.auth.repositories.session_repository import SessionRepository
from src.auth.repositories.password_reset_repository import PasswordResetRepository
from src.auth.repositories.user_mfa_repository import (
    UserMfaRepository,
    DEFAULT_LABEL as MFA_DEFAULT_LABEL,
)
from src.auth.services.password_service import PasswordService
from src.auth.services.token_service import TokenService
from src.auth.services.audit_service import AuditService
from src.auth.services.invite_service import InviteService
from src.auth.services.mfa_service import MfaService
from src.auth.services.ldap_service import LdapService
from src.auth.services.oidc_service import OidcService

logger = logging.getLogger(__name__)


# Create blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _invalidate_user_caches(user_id, jti=None):
    """Drop cached user/session records so admin actions take effect immediately.

    Cache TTLs (60s for jwt_user, 30s for jwt_session) are otherwise the
    only signal that revokes a demoted admin's super_admin claims, a
    disabled user's session, or a reset password's old session.
    """
    try:
        cache.delete(f"jwt_user:{user_id}")
        if jti:
            cache.delete(f"jwt_session:{jti}")
    except Exception as e:
        # Cache being down/erroring must never block the admin action.
        # Worst case: the operation succeeds and the caches drain on TTL.
        logger.warning("Cache invalidation failed for user %s: %s", user_id, e)


def _jwt_is_external_user() -> bool:
    """True when the current JWT was issued via LDAP or OIDC sign-in.

    Tokens issued before the `auth_method` claim existed default to `'local'`
    for backward compatibility.
    """
    return get_jwt().get('auth_method', 'local') != 'local'


def _enforce_token_rate_limit(
    action: str,
    audit_service,
    ip_address,
    user_agent: str,
    *,
    message: str,
    setting_key: str = 'token_verify_rate_limit_per_ip_per_hour',
    default_limit: int = 20,
    username: str | None = None,
) -> tuple | None:
    """
    Apply per-IP rate limiting to a public token-handling endpoint.

    Returns None if the request is within the limit (caller proceeds),
    or a (response, status_code) tuple to return immediately if exceeded.

    Audit-log writes are deduped: only the first blocked request per
    (action, ip) per rolling window writes a row, preventing DB-write
    amplification under attack.

    Args:
        action: audit-log action string (also used as cache-key namespace).
        audit_service: AuditService instance for logging the dedup'd block.
        ip_address: source IP (None or empty disables limiting — fail open).
        user_agent: User-Agent header value for the audit row.
        message: human-readable 429 response message.
        setting_key: settings_manager key under category 'auth' (default targets
            the shared 'token_verify_rate_limit_per_ip_per_hour' setting).
        default_limit: fallback when the setting is not configured.
        username: optional username/email to include in the audit row
            (used by request_password_reset to record the attempted account).
    """
    from src.api.utils.caching import cache
    from src.auth.services.ip_rate_limiter import IPRateLimiter

    limit = current_app.settings_manager.get('auth', setting_key, default_limit)
    if IPRateLimiter.check_and_increment(
        action=action, ip=ip_address, limit=limit,
    ):
        return None

    audit_key = f"ratelimit_audited:{action}:{ip_address}"
    try:
        already_audited = bool(cache.get(audit_key))
    except Exception:
        already_audited = False
    if not already_audited:
        try:
            cache.set(audit_key, True, timeout=IPRateLimiter.DEFAULT_WINDOW_SECONDS)
        except Exception:
            pass
        audit_service.log_auth(
            action=action,
            success=False,
            username=username,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='IP rate limit exceeded',
        )
    return (
        jsonify({
            'error': 'rate_limit_exceeded',
            'message': message,
        }),
        429,
    )


def _maybe_require_mfa_enrollment(
    user: dict,
    *,
    auth_method: str,
    mfa_repo: 'UserMfaRepository',
    audit_service: 'AuditService',
    ip_address: str,
    user_agent: str,
    break_glass: bool = False,
    enabled_count: Optional[int] = None,
) -> Optional[tuple]:
    """If the user is local and has zero enabled MFA methods, issue a 5-min
    enrollment-required challenge token and return the (response, status_code)
    tuple for the caller to return.

    Returns None if no enrollment is required (caller proceeds with full-token
    issuance).

    ``break_glass=True`` is set by the super_admin local break-glass path: when
    a super_admin without MFA logs in via break-glass, we emit a warning audit
    row but DO NOT block them. super_admin local break-glass exempts from MFA
    enrollment so a misconfigured IdP combined with a lost authenticator cannot
    brick the only super_admin.

    ``enabled_count``: optional pre-computed count of enrolled (mfa_enabled=true)
    methods for the user. Pass this when the caller already loaded it (e.g.
    ``login()`` already inspects enabled methods to dispatch to the MFA
    challenge branch). When None, the helper looks it up itself.
    """
    if not MfaService.requires_mfa(auth_method=auth_method):
        return None

    # Defensive: LDAP/OIDC users would never reach here when auth_method is
    # correctly set, but the password_hash prefix is the historical gate and
    # stays as belt-and-suspenders against caller misuse.
    pw_hash = user.get('password_hash') or ''
    if isinstance(pw_hash, str) and (
        pw_hash.startswith('LDAP:') or pw_hash.startswith('OIDC:')
    ):
        return None

    if enabled_count is None:
        enabled_count = mfa_repo.count_enabled_methods(user['id'])
    if enabled_count > 0:
        # Caller must run the existing MFA-challenge branch — this helper only
        # handles the "no MFA enrolled yet" case.
        return None

    if break_glass:
        # Operator emergency escape hatch: log loudly but issue tokens. See
        # docstring above for the rationale.
        logger.warning(
            "super_admin %s bypassed MFA enrollment via local break-glass — "
            "operator has no MFA row.",
            user.get('username'),
        )
        audit_service.log_auth(
            action='super_admin_break_glass_bypassed_mfa_enrollment',
            success=True,
            user_id=user['id'],
            username=user.get('username'),
            ip=ip_address,
            user_agent=user_agent,
        )
        return None

    enrollment_token = secrets.token_urlsafe(32)
    cache.set(
        f'mfa_enrollment:{enrollment_token}',
        {'user_id': str(user['id'])},
        timeout=300,
    )
    audit_service.log_auth(
        action='login_mfa_enrollment_required',
        success=True,
        user_id=user['id'],
        username=user.get('username'),
        ip=ip_address,
        user_agent=user_agent,
    )
    response = LoginMfaEnrollmentRequiredResponse(
        requires_mfa_enrollment=True,
        mfa_enrollment_token=enrollment_token,
        expires_in=300,
    )
    return jsonify(response.model_dump(mode='json')), 200


def _access_token_ttl() -> timedelta:
    """Resolve the JWT access-token TTL from the security setting.

    Schema declares ``max_value=1440``; SettingsManager only enforces it on
    write, so clamp here as a second fence against stale or out-of-band DB
    values. Falls back to the env-driven JWT_ACCESS_TOKEN_EXPIRES config
    when the setting is missing or invalid.
    """
    from src.api.modules.settings.settings_schema import APPLICATION_SETTINGS

    fallback = current_app.config['JWT_ACCESS_TOKEN_EXPIRES']
    raw = current_app.settings_manager.get(
        'security', 'session_timeout_minutes',
    )
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return fallback
    if minutes <= 0:
        return fallback
    cap = APPLICATION_SETTINGS['security']['session_timeout_minutes'].max
    return timedelta(minutes=min(minutes, cap) if cap else minutes)


def _issue_session_tokens(
    user: dict, ip_address: str, user_agent: str, *, auth_method: str = 'local'
) -> tuple[str, str, int, str]:
    """Create a session and issue access + refresh JWTs.

    Returns (access_token, refresh_token, access_expires_in_seconds, session_id).
    Caller is responsible for the success audit log and UserResponse construction.

    ``auth_method`` must be one of ``'local'``, ``'ldap'``, or ``'oidc'``.  It is
    embedded in both the access token and the refresh token so that downstream
    endpoints can derive is_ldap_user / is_oidc_user from the JWT without an
    extra DB round-trip.
    """
    conn_manager = current_app.connection_manager
    session_repo = SessionRepository(conn_manager)
    user_repo = UserRepository(conn_manager)

    session_id = session_repo.create_session(
        user_id=user['id'], ip_address=ip_address, user_agent=user_agent,
    )
    token_metadata = TokenService.generate_token_pair(
        user_id=str(user['id']),
        username=user['username'],
        email=user['email'],
        role=user['role'],
        session_id=str(session_id),
    )
    access_jti = token_metadata['access_token_jti']
    user_claims = token_metadata['user_claims']

    access_ttl = _access_token_ttl()
    access_token = create_access_token(
        identity=user,
        additional_claims={'jti': access_jti, **user_claims, 'auth_method': auth_method},
        expires_delta=access_ttl,
    )
    refresh_token = create_refresh_token(
        identity=user,
        additional_claims={'session_id': str(session_id), 'auth_method': auth_method},
    )
    refresh_expiration = TokenService.calculate_token_expiration('refresh')
    session_repo.store_refresh_token(
        session_id=session_id,
        refresh_token_hash=TokenService.hash_refresh_token(refresh_token),
        access_token_jti=access_jti,
        expires_at=refresh_expiration,
    )
    user_repo.update_login_success(user['id'])

    # Password expiration (#611): only for local users. The setting
    # auth.password_expiration_days = 0 means "disabled" (NIST 800-63B).
    # We mutate the in-memory user dict so the response built by the
    # caller reflects the new state without an extra DB round-trip.
    if auth_method == 'local':
        try:
            max_age_days = int(
                current_app.settings_manager.get('auth', 'password_expiration_days', 180)
            )
        except (TypeError, ValueError):
            max_age_days = 180
        if max_age_days > 0 and user_repo.mark_password_expired_if_old(user['id'], max_age_days):
            user['must_change_password'] = True

    return (
        access_token,
        refresh_token,
        int(access_ttl.total_seconds()),
        str(session_id),
    )


# =============================================================================
# Authentication Endpoints
# =============================================================================

@auth_bp.route('/login', methods=['POST'])
@handle_errors
def login():
    """
    Authenticate user and obtain JWT tokens.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - password
          properties:
            username:
              type: string
              description: Username or email address
              example: admin
            password:
              type: string
              description: User password
              example: your-password
    responses:
      200:
        description: Login successful
        schema:
          type: object
          properties:
            access_token:
              type: string
              description: JWT access token (15 min expiration)
            refresh_token:
              type: string
              description: JWT refresh token (7 day expiration)
            token_type:
              type: string
              example: Bearer
            expires_in:
              type: integer
              description: Access token expiration in seconds
              example: 900
            user:
              type: object
              description: User information
      400:
        description: Validation error (invalid request data)
      401:
        description: Authentication failed (invalid credentials, account locked)
      500:
        description: Internal server error
    """
    # Get repositories
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    # Get request data
    try:
        data = LoginRequest(**request.json)
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors()
        }), 400

    username = data.username
    password = data.password

    # Get client information for audit
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    try:
        # ----- LDAP branch ---------------------------------------------------
        ldap_svc = LdapService(current_app.settings_manager)
        ldap_authenticated = False
        # `break_glass` is set True later by the super_admin local break-glass
        # path so the MFA-enrollment enforcement knows to log a bypass instead
        # of blocking the operator's recovery login.
        break_glass = False
        user = None

        if ldap_svc.is_enabled():
            ldap_info = ldap_svc.authenticate(username, password)
            if ldap_info is not None:
                # LDAP success — provision/match local user, then continue down
                # the success path (session creation, MFA branch, token issuance).
                try:
                    user = user_repo.get_or_create_ldap_user(
                        username=ldap_info.username,
                        email=ldap_info.email,
                        full_name=ldap_info.full_name,
                        role=ldap_info.role,
                        ip=ip_address,
                        user_agent=user_agent,
                    )
                    # Even with valid LDAP creds, respect a local lockout — otherwise an attacker
                    # who guessed the LDAP password could bypass the local 5-failed-attempts
                    # protection that exists for password-spray defense.
                    if user.get('failed_login_attempts', 0) >= 5 and user_repo.is_account_locked(user):
                        audit_service.log_auth(
                            action='login',
                            success=False,
                            user_id=user['id'],
                            username=user['username'],
                            failure_reason='account_locked',
                            ip=ip_address,
                            user_agent=user_agent,
                        )
                        return jsonify({
                            'error': 'account_locked',
                            'message': f'Account is locked due to multiple failed login attempts. Try again later.',
                        }), 401
                    ldap_authenticated = True
                    audit_service.log_auth(
                        action='login_ldap',
                        success=True,
                        user_id=user['id'],
                        username=user['username'],
                        ip=ip_address,
                        user_agent=user_agent,
                    )
                except ValueError:
                    audit_service.log_auth(
                        action='login',
                        success=False,
                        username=username,
                        failure_reason='account_disabled',
                        ip=ip_address,
                        user_agent=user_agent,
                    )
                    return jsonify({
                        'error': 'account_disabled',
                        'message': 'This account has been disabled',
                    }), 401
            elif not ldap_svc.allow_local_fallback():
                # super_admin can always log in with a real local bcrypt hash,
                # even when LDAP is the configured provider with no-fallback —
                # preserves a recovery path if the IdP is misconfigured. Any
                # other role gets the normal 401.
                local_user = (
                    user_repo.get_user_by_username(username)
                    or user_repo.get_user_by_email(username)
                )
                local_hash = (local_user or {}).get('password_hash') or ''
                is_local_super_admin = (
                    local_user is not None
                    and local_user.get('role') == 'super_admin'
                    and isinstance(local_hash, str)
                    and not (
                        local_hash.startswith('LDAP:') or local_hash.startswith('OIDC:')
                    )
                )
                if not is_local_super_admin:
                    audit_service.log_auth(
                        action='login',
                        success=False,
                        user_id=None,
                        username=username,
                        failure_reason='ldap_rejected_no_local_fallback',
                        ip=ip_address,
                        user_agent=user_agent,
                    )
                    return jsonify({
                        'error': 'authentication_failed',
                        'message': 'Invalid username or password',
                    }), 401
                # Mark this attempt so downstream local-verify and MFA-enrollment
                # logic can apply the break-glass branch (audit + MFA bypass).
                break_glass = True
        # ---- end LDAP branch -----------------------------------------------

        if not ldap_authenticated:
            # Find user by username or email
            user = user_repo.get_user_by_username(username)
            if not user:
                # Try finding by email
                user = user_repo.get_user_by_email(username)

            # User not found
            if not user:
                audit_service.log_auth(

                    action='login',
                    success=False,
                    username=username,
                    failure_reason='user_not_found',
                    ip=ip_address,
                    user_agent=user_agent
                )
                return jsonify({
                    'error': 'authentication_failed',
                    'message': 'Invalid username or password'
                }), 401

            user_id = user['id']

            # Check if account is active
            if not user.get('is_active', True):
                audit_service.log_auth(

                    action='login',
                    success=False,
                    user_id=user_id,
                    username=user['username'],
                    failure_reason='account_disabled',
                    ip=ip_address,
                    user_agent=user_agent
                )
                return jsonify({
                    'error': 'account_disabled',
                    'message': 'This account has been disabled'
                }), 401

            # Check if account is locked
            if user.get('failed_login_attempts', 0) >= 5 and user_repo.is_account_locked(user):
                audit_service.log_auth(
                    action='login',
                    success=False,
                    user_id=user_id,
                    username=user['username'],
                    failure_reason='account_locked',
                    ip=ip_address,
                    user_agent=user_agent
                )
                return jsonify({
                    'error': 'account_locked',
                    'message': f'Account is locked due to multiple failed login attempts. Try again later.'
                }), 401

            # Verify password
            password_hash = user['password_hash']
            if not PasswordService.verify_password(password, password_hash):
                # Increment failed attempts
                user_repo.increment_failed_attempts(user_id)

                # Distinguish a failed break-glass attempt from a normal
                # invalid_password row so SOC tooling can alert on it.
                failure_reason = (
                    'wrong_password_super_admin_break_glass'
                    if break_glass else 'invalid_password'
                )
                audit_service.log_auth(

                    action=('login_local_break_glass' if break_glass else 'login'),
                    success=False,
                    user_id=user_id,
                    username=user['username'],
                    failure_reason=failure_reason,
                    ip=ip_address,
                    user_agent=user_agent
                )

                return jsonify({
                    'error': 'authentication_failed',
                    'message': 'Invalid username or password'
                }), 401

        user_id = user['id']

        # ----- MFA branch ---------------------------------------------------
        mfa_repo = UserMfaRepository(conn_manager)
        enabled_mfa_count = mfa_repo.count_enabled_methods(user_id)
        if enabled_mfa_count > 0:
            challenge_token = secrets.token_urlsafe(32)
            cache.set(
                f'mfa_challenge:{challenge_token}',
                {'user_id': str(user_id), 'attempts': 0},
                timeout=300,
            )
            audit_service.log_auth(
                action='login_mfa_required',
                success=True,
                user_id=user_id,
                username=user['username'],
                ip=ip_address,
                user_agent=user_agent,
            )
            response = LoginMfaRequiredResponse(
                requires_mfa=True,
                mfa_challenge_token=challenge_token,
                expires_in=300,
            )
            return jsonify(response.model_dump(mode='json')), 200
        # ---- end MFA branch -----------------------------------------------

        # ----- MFA enrollment-required branch -------------------------------
        # Force every local user without an enrolled MFA method through the
        # enrollment wizard before issuing full session tokens.
        login_auth_method = 'ldap' if ldap_authenticated else 'local'
        enrollment_response = _maybe_require_mfa_enrollment(
            user,
            auth_method=login_auth_method,
            mfa_repo=mfa_repo,
            audit_service=audit_service,
            ip_address=ip_address,
            user_agent=user_agent,
            break_glass=break_glass,
            enabled_count=enabled_mfa_count,
        )
        if enrollment_response is not None:
            return enrollment_response
        # ---- end MFA enrollment-required branch ----------------------------

        # Password correct - create session and issue tokens
        access_token, refresh_token, expires_in, session_id = _issue_session_tokens(
            user, ip_address, user_agent, auth_method=login_auth_method,
        )

        # Audit log — LDAP logins already emitted a login_ldap row above; skip
        # the generic login row to avoid double-auditing the same success event.
        # Break-glass logins get their own action so the SOC can distinguish
        # an emergency super_admin recovery from a normal local login.
        if not ldap_authenticated:
            audit_service.log_auth(

                action=('login_local_break_glass' if break_glass else 'login'),
                success=True,
                user_id=user_id,
                username=user['username'],
                ip=ip_address,
                user_agent=user_agent
            )

        # Build response
        user_response = UserResponse(
            id=user['id'],
            username=user['username'],
            email=user['email'],
            full_name=user.get('full_name'),
            role=user['role'],
            is_active=user['is_active'],
            email_verified=user.get('email_verified', False),
            must_change_password=user.get('must_change_password', False),
            last_login_at=user.get('last_login_at'),
            created_at=user['created_at'],
            is_ldap_user=(login_auth_method == 'ldap'),
            is_oidc_user=False,
        )

        response = LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type='Bearer',
            expires_in=expires_in,
            user=user_response
        )

        logger.info(f"User {user['username']} logged in successfully")

        return jsonify(response.model_dump(mode='json')), 200

    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred during login'
        }), 500


@auth_bp.route('/register', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
def register():
    """
    Register new user account.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - email
            - password
          properties:
            username:
              type: string
              minLength: 3
              maxLength: 50
              description: Username (lowercase, alphanumeric, dots, underscores, hyphens)
              example: john.doe
            email:
              type: string
              format: email
              description: Email address
              example: john@example.com
            password:
              type: string
              minLength: 8
              maxLength: 128
              description: Password (min 8 chars, must contain uppercase, lowercase, digit, special char)
              example: StrongPassword123!
            full_name:
              type: string
              maxLength: 255
              description: Full name (optional)
              example: John Doe
    responses:
      201:
        description: User registered successfully
        schema:
          type: object
          properties:
            user:
              type: object
              description: User information
            message:
              type: string
              example: User registered successfully
      400:
        description: Validation error or username/email already exists
      500:
        description: Internal server error
    """
    # Get repositories
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    # Get request data
    try:
        data = RegisterRequest(**request.json)
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors()
        }), 400

    # Get client information for audit
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    try:
        # Check if username exists
        existing_user = user_repo.get_user_by_username(data.username)
        if existing_user:
            return jsonify({
                'error': 'username_exists',
                'message': 'Username already exists'
            }), 400

        # Check if email exists
        existing_email = user_repo.get_user_by_email(data.email)
        if existing_email:
            return jsonify({
                'error': 'email_exists',
                'message': 'Email already exists'
            }), 409

        # Hash password
        password_hash = PasswordService.hash_password(data.password)

        # Create user
        user_id = user_repo.create_user(
            username=data.username,
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            role='user'  # Default role
        )

        # Get created user
        user = user_repo.get_user_by_id(user_id)

        # Audit log
        audit_service.log_auth(

            action='register',
            success=True,
            user_id=user_id,
            username=data.username,
            ip=ip_address,
            user_agent=user_agent
        )

        # Build response
        user_response = UserResponse(
            id=user['id'],
            username=user['username'],
            email=user['email'],
            full_name=user.get('full_name'),
            role=user['role'],
            is_active=user['is_active'],
            email_verified=user.get('email_verified', False),
            must_change_password=user.get('must_change_password', False),
            last_login_at=user.get('last_login_at'),
            created_at=user['created_at']
        )

        logger.info(f"User {data.username} registered successfully")

        return jsonify({
            'user': user_response.model_dump(mode='json'),
            'message': 'User registered successfully'
        }), 201

    except Exception as e:
        logger.error(f"Registration error: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred during registration'
        }), 500


@auth_bp.route('/session-config', methods=['GET'])
@handle_errors
def get_session_config():
    """
    Get public session configuration (unauthenticated).
    Returns session timeout settings needed by the frontend before login.
    ---
    tags:
      - Authentication
    responses:
      200:
        description: Session configuration
        schema:
          type: object
          properties:
            session_timeout_minutes:
              type: integer
              example: 60
    """
    settings_manager = current_app.settings_manager
    timeout = settings_manager.get('security', 'session_timeout_minutes')
    return jsonify({'session_timeout_minutes': timeout}), 200


@auth_bp.route('/logout', methods=['POST'])
@jwt_required()
@handle_errors
def logout():
    """
    Logout and revoke current session.
    ---
    tags:
      - Authentication
    security:
      - BearerAuth: []
    parameters:
      - in: header
        name: Authorization
        required: true
        type: string
        description: Access token in Bearer format
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    responses:
      200:
        description: Logout successful
        schema:
          type: object
          properties:
            message:
              type: string
              example: Logout successful
      401:
        description: Invalid or expired access token
      500:
        description: Internal server error
    """
    # Get current user and JWT data
    user = current_user
    jwt_data = get_jwt()
    jti = jwt_data.get('jti')

    # Get repositories
    conn_manager = current_app.connection_manager
    session_repo = SessionRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    # Get client information for audit
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    try:
        # Find and deactivate session
        session = session_repo.get_session_by_access_jti(jti)
        if session:
            session_repo.deactivate_session(session['id'])

        # Invalidate JWT caches for security
        # This ensures the logged-out user cannot use cached credentials
        try:
            # Invalidate user data cache
            user_cache_key = f"jwt_user:{user['id']}"
            cache.delete(user_cache_key)

            # Invalidate session validation cache
            session_cache_key = f"jwt_session:{jti}"
            cache.delete(session_cache_key)

            logger.debug(f"Invalidated JWT caches for user {user['id']}")
        except Exception as cache_error:
            # Cache invalidation failure should not block logout
            logger.warning(f"Failed to invalidate caches on logout: {cache_error}")

        # Audit log
        audit_service.log_auth(

            action='logout',
            success=True,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent
        )

        logger.info(f"User {user['username']} logged out")

        body = {'message': 'Logout successful'}

        # RP-initiated logout: if the user signed in via OIDC and an operator
        # has registered a post_logout_redirect_uri, ask the IdP to terminate
        # its own session too. UI follows the URL after clearing localStorage.
        # On any failure, we just omit the field — local logout still succeeded.
        if jwt_data.get('auth_method') == 'oidc':
            try:
                logout_url = OidcService(current_app.settings_manager).build_logout_url()
                if logout_url:
                    body['oidc_logout_url'] = logout_url
            except Exception as e:
                logger.warning(f"Failed to build OIDC logout URL: {e}")

        return jsonify(body), 200

    except Exception as e:
        logger.error(f"Logout error: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred during logout'
        }), 500


@auth_bp.route('/me', methods=['GET'])
@jwt_required()
@handle_errors
def get_current_user():
    """
    Get current user information.
    ---
    tags:
      - Authentication
    security:
      - BearerAuth: []
    parameters:
      - in: header
        name: Authorization
        required: true
        type: string
        description: Access token in Bearer format
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    responses:
      200:
        description: User information retrieved successfully
        schema:
          type: object
          properties:
            id:
              type: string
              format: uuid
            username:
              type: string
            email:
              type: string
            full_name:
              type: string
            role:
              type: string
              enum: [user, admin, super_admin]
            is_active:
              type: boolean
            email_verified:
              type: boolean
            must_change_password:
              type: boolean
            last_login_at:
              type: string
              format: date-time
            created_at:
              type: string
              format: date-time
      401:
        description: Invalid or expired access token
      500:
        description: Internal server error
    """
    # Get current user from JWT
    user = current_user

    if not user:
        return jsonify({
            'error': 'user_not_found',
            'message': 'User not found'
        }), 401

    try:
        # Derive auth provider flags from the JWT claim — no extra DB call needed.
        jwt_auth_method = get_jwt().get('auth_method', 'local')

        # Build response
        user_response = UserResponse(
            id=user['id'],
            username=user['username'],
            email=user['email'],
            full_name=user.get('full_name'),
            role=user['role'],
            is_active=user['is_active'],
            email_verified=user.get('email_verified', False),
            must_change_password=user.get('must_change_password', False),
            last_login_at=user.get('last_login_at'),
            created_at=user['created_at'],
            is_ldap_user=(jwt_auth_method == 'ldap'),
            is_oidc_user=(jwt_auth_method == 'oidc'),
        )

        return jsonify(user_response.model_dump(mode='json')), 200

    except Exception as e:
        logger.error(f"Get current user error: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred'
        }), 500


@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
@handle_errors
def refresh_token():
    """
    Refresh access token using refresh token.
    ---
    tags:
      - Authentication
    security:
      - BearerAuth: []
    parameters:
      - in: header
        name: Authorization
        required: true
        type: string
        description: Refresh token in Bearer format
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    responses:
      200:
        description: New access token generated successfully
        schema:
          type: object
          properties:
            access_token:
              type: string
              description: New JWT access token
            refresh_token:
              type: string
              description: New refresh token (if rotation enabled)
            token_type:
              type: string
              example: Bearer
            expires_in:
              type: integer
              description: Access token expiration time in seconds
              example: 900
            user:
              type: object
              description: Current user information
              properties:
                id:
                  type: string
                username:
                  type: string
                role:
                  type: string
      401:
        description: Invalid or expired refresh token
        schema:
          type: object
          properties:
            error:
              type: string
              example: invalid_refresh_token
            message:
              type: string
              example: Refresh token is invalid or expired
      500:
        description: Internal server error
    """
    # Get current user and JWT data from refresh token
    user = current_user
    jwt_data = get_jwt()
    refresh_jti = jwt_data.get('jti')
    # Carry auth_method forward from the refresh token so the new access token
    # retains the same claim.  Default to 'local' for tokens issued before this
    # change (backward compat).
    auth_method = jwt_data.get('auth_method', 'local')

    # Get repositories
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    session_repo = SessionRepository(conn_manager)

    try:
        # Get user fresh from database
        user_data = user_repo.get_user_by_id(user['id'])
        if not user_data:
            logger.warning(f"User {user['id']} not found during token refresh")
            return jsonify({
                'error': 'user_not_found',
                'message': 'User account not found'
            }), 401

        # Check if user is active
        if not user_data.get('is_active', False):
            logger.warning(f"Inactive user {user_data['username']} attempted token refresh")
            return jsonify({
                'error': 'account_disabled',
                'message': 'Your account has been disabled'
            }), 401

        # Get session from JWT session_id claim
        session_id = jwt_data.get('session_id')
        if not session_id:
            logger.error("Refresh token missing session_id claim")
            return jsonify({
                'error': 'invalid_token',
                'message': 'Invalid refresh token format'
            }), 401

        session = session_repo.get_session_by_id(session_id)
        if not session or not session.get('is_active'):
            logger.warning(f"Session {session_id} not found or inactive for token refresh")
            return jsonify({
                'error': 'invalid_session',
                'message': 'Session has expired or been revoked'
            }), 401

        # Generate new access token (keep same session)
        user_id_str = str(user_data['id'])
        session_id_str = str(session_id)

        # Create new access token
        access_token_metadata = TokenService.generate_token_pair(
            user_id=user_id_str,
            username=user_data['username'],
            email=user_data['email'],
            role=user_data['role'],
            session_id=session_id_str
        )

        # Create new access token with Flask-JWT-Extended
        access_ttl = _access_token_ttl()
        new_access_token = create_access_token(
            identity=user_data,
            additional_claims={
                'username': user_data['username'],
                'email': user_data['email'],
                'role': user_data['role'],
                'session_id': session_id_str,
                'auth_method': auth_method,
            },
            expires_delta=access_ttl,
        )

        # Generate new refresh token (token rotation for security)
        new_refresh_token_raw = create_refresh_token(
            identity=user_data,
            additional_claims={
                'session_id': session_id_str,
                'auth_method': auth_method,
            }
        )

        # Hash the new refresh token
        new_refresh_token_hash = TokenService.hash_refresh_token(new_refresh_token_raw)

        # Get new token JTIs for tracking
        # Decode the new access token to get its JTI
        from flask_jwt_extended import decode_token
        new_access_decoded = decode_token(new_access_token)
        new_access_jti = new_access_decoded['jti']

        # Update session with new token info (also updates last_used_at)
        session_repo.update_session_tokens(
            session_id=session_id,
            refresh_token_hash=new_refresh_token_hash,
            access_token_jti=new_access_jti
        )

        logger.info(f"Token refreshed for user {user_data['username']}")

        # Build response
        response_data = {
            'access_token': new_access_token,
            'refresh_token': new_refresh_token_raw,
            'token_type': 'Bearer',
            'expires_in': int(access_ttl.total_seconds()),
            'user': {
                'id': user_id_str,
                'username': user_data['username'],
                'email': user_data['email'],
                'role': user_data['role']
            }
        }

        logger.debug(f"Refresh response keys: {list(response_data.keys())}")
        logger.debug(f"Refresh response has refresh_token: {'refresh_token' in response_data}")
        logger.debug(f"Refresh response has user: {'user' in response_data}")

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"Token refresh error: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred during token refresh'
        }), 500


# =============================================================================
# Password Reset Endpoints
# =============================================================================

@auth_bp.route('/password-reset/request', methods=['POST'])
@handle_errors
def request_password_reset():
    """
    Request password reset for an account.

    Sends a password reset email with a secure token if the account exists.
    Always returns success to prevent email enumeration attacks.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              format: email
              example: admin@assetatlas.local
    responses:
      200:
        description: Request processed (always returns success)
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            message:
              type: string
              example: If an account exists with that email, a password reset link has been sent.
      400:
        description: Validation error (invalid request data)
      429:
        description: Too many requests (rate limited)
      500:
        description: Internal server error
    """
    from src.auth.repositories.user_repository import UserRepository
    from src.auth.repositories.password_reset_repository import PasswordResetRepository
    from src.auth.services.password_reset_service import PasswordResetService
    from src.auth.services.audit_service import AuditService

    # Validate request data
    try:
        data = PasswordResetRequestModel(**request.json)
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors()
        }), 400

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    email = data.email
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    # Per-IP rate limit (fires before user lookup so the response is
    # identical for known/unknown emails — enumeration safe).
    rate_limited = _enforce_token_rate_limit(
        'password_reset_request', audit_service, ip_address, user_agent,
        message='Too many password reset requests from this network. Please try again later.',
        setting_key='password_reset_rate_limit_per_ip_per_hour',
        default_limit=5,
        username=email,
    )
    if rate_limited is not None:
        return rate_limited

    # Generic success message (prevents email enumeration)
    success_message = "If an account exists with that email, a password reset link has been sent."
    
    try:
        # Look up user by email
        user = user_repo.get_user_by_email(email)
        
        if not user:
            # User not found - still return success to prevent enumeration
            logger.info(f"Password reset requested for non-existent email: {email}")
            audit_service.log_auth(
                action='password_reset_request',
                success=False,
                username=email,
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='Email not found'
            )
            return jsonify({
                'success': True,
                'message': success_message
            }), 200
        
        user_id = user['id']
        username = user['username']
        
        # Check if account is active
        if not user.get('is_active', False):
            logger.warning(f"Password reset requested for inactive account: {username}")
            audit_service.log_auth(
                action='password_reset_request',
                success=False,
                user_id=user_id,
                username=username,
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='Account inactive'
            )
            # Still return success to prevent enumeration
            return jsonify({
                'success': True,
                'message': success_message
            }), 200
        
        # Check rate limit
        if not PasswordResetService.check_rate_limit(user_id, reset_repo):
            logger.warning(f"Password reset rate limit exceeded for user: {username}")
            audit_service.log_auth(
                action='password_reset_request',
                success=False,
                user_id=user_id,
                username=username,
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='Rate limit exceeded'
            )
            return jsonify({
                'error': 'rate_limit_exceeded',
                'message': 'Too many password reset requests. Please try again later.'
            }), 429
        
        # Create reset token
        reset_data = PasswordResetService.create_reset_token(
            user_id=user_id,
            reset_repo=reset_repo,
            user_repo=user_repo
        )
        
        # Send reset email
        email_sent = current_app.email_service.send_password_reset_email(
            to_email=email,
            username=username,
            reset_token=reset_data['token'],
            expires_in_hours=PasswordResetService.TOKEN_EXPIRATION_HOURS,
        )
        
        if email_sent:
            logger.info(f"Password reset email sent to {username} ({email})")
            audit_service.log_auth(
                action='password_reset_request',
                success=True,
                user_id=user_id,
                username=username,
                ip=ip_address,
                user_agent=user_agent
            )
        else:
            logger.error(f"Failed to send password reset email to {username}")
            audit_service.log_auth(
                action='password_reset_request',
                success=False,
                user_id=user_id,
                username=username,
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='Email send failed'
            )
        
        # Always return success (don't reveal if email was sent)
        return jsonify({
            'success': True,
            'message': success_message
        }), 200
        
    except Exception as e:
        logger.error(f"Error processing password reset request: {e}", exc_info=True)
        audit_service.log_auth(
            action='password_reset_request',
            success=False,
            username=email,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason=str(e)
        )
        # Still return success to prevent information leakage
        return jsonify({
            'success': True,
            'message': success_message
        }), 200


@auth_bp.route('/password-reset/verify', methods=['POST'])
@handle_errors
def verify_password_reset():
    """
    Complete password reset using token.

    Validates the reset token and updates the user's password.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - token
            - new_password
          properties:
            token:
              type: string
              minLength: 64
              maxLength: 64
              description: Password reset token from email
              example: a1b2c3d4e5f6...
            new_password:
              type: string
              minLength: 8
              maxLength: 128
              description: New password
              example: NewSecurePassword123!
    responses:
      200:
        description: Password reset successful
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            message:
              type: string
              example: Password has been reset successfully. Please login with your new password.
            username:
              type: string
              example: admin
      400:
        description: Validation error or invalid token/password
      500:
        description: Internal server error
    """
    from src.auth.repositories.user_repository import UserRepository
    from src.auth.repositories.password_reset_repository import PasswordResetRepository
    from src.auth.repositories.session_repository import SessionRepository
    from src.auth.services.password_reset_service import PasswordResetService
    from src.auth.services.audit_service import AuditService

    # Validate request data
    try:
        data = PasswordResetVerifyModel(**request.json)
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors()
        }), 400

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)
    session_repo = SessionRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    token = data.token
    new_password = data.new_password
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    rate_limited = _enforce_token_rate_limit(
        'password_reset_verify', audit_service, ip_address, user_agent,
        message='Too many password reset attempts from this network. Please try again later.',
    )
    if rate_limited is not None:
        return rate_limited

    try:
        # Validate token and reset password
        result = PasswordResetService.validate_and_use_token(
            token=token,
            new_password=new_password,
            reset_repo=reset_repo,
            user_repo=user_repo,
            session_repo=session_repo
        )
        
        username = result['username']
        user_id = result['user_id']
        
        # Get user email for notification
        user = user_repo.get_user_by_id(user_id)
        user_email = user.get('email') if user else None
        
        # Send password changed notification
        if user_email:
            current_app.email_service.send_password_changed_notification(
                to_email=user_email,
                username=username,
            )
        
        # Audit log
        audit_service.log_auth(
            action='password_reset_complete',
            success=True,
            user_id=user_id,
            username=username,
            ip=ip_address,
            user_agent=user_agent
        )
        
        logger.info(f"Password reset completed for user: {username}")
        
        return jsonify({
            'success': True,
            'message': 'Password has been reset successfully. Please login with your new password.',
            'username': username
        }), 200
        
    except ValueError as e:
        # Token invalid or password validation failed
        logger.warning(f"Password reset verification failed: {str(e)}")
        audit_service.log_auth(
            action='password_reset_complete',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason=str(e)
        )
        return jsonify({
            'error': 'invalid_token_or_password',
            'message': str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Error completing password reset: {e}", exc_info=True)
        audit_service.log_auth(
            action='password_reset_complete',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason=str(e)
        )
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while resetting your password. Please try again.'
        }), 500


@auth_bp.route('/forgot-password/lookup', methods=['POST'])
@handle_errors
def forgot_password_lookup():
    """Return the available recovery paths for the user-supplied username.

    Enumeration-safe: always returns ['email']. The TOTP path is exposed by
    /forgot-password/totp succeeding for users that have it enrolled — there
    is no preview that leaks enrollment state.

    Rate-limited per IP because the response is constant — without the cap an
    unauthenticated client could pound this endpoint with no signal back.
    ---
    tags: [Authentication]
    responses:
      200: {description: Paths available}
      400: {description: Validation error}
      429: {description: Rate-limit exceeded for this IP}
    """
    from src.auth.models.auth import (
        ForgotPasswordLookupRequest,
        ForgotPasswordLookupResponse,
    )

    audit_service = AuditService(current_app.connection_manager)
    rl = _enforce_token_rate_limit(
        action='forgot_password_lookup',
        audit_service=audit_service,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        message='Too many lookup attempts from this network. Please try again later.',
    )
    if rl is not None:
        return rl

    try:
        ForgotPasswordLookupRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'details': e.errors(include_context=False),
        }), 400
    response = ForgotPasswordLookupResponse(paths=['email'])
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/forgot-password/totp', methods=['POST'])
@handle_errors
def forgot_password_totp():
    """TOTP-based password recovery (#613).

    Reset a local user's password using only username + valid TOTP code, no
    email roundtrip. Local-only with TOTP enrolled is the prerequisite — when
    the prerequisite isn't met, the response is the same generic
    invalid_credentials so the failure is enumeration-safe.

    Per-username and per-IP rate limits: 5 failures / 15 minutes lock the
    username for 30 minutes (per-IP cap is the existing token_verify_rate_limit).
    Email-based reset remains available during a TOTP lockout.
    ---
    tags: [Authentication]
    responses:
      200: {description: Password reset, sessions revoked}
      400: {description: Validation, weak password, or generic invalid_credentials}
      429: {description: Locked out (per-username) or rate-limited (per-IP)}
    """
    from src.auth.models.auth import ForgotPasswordTotpRequest
    from src.auth.services.ip_rate_limiter import IPRateLimiter

    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    # Per-IP rate limit. Counter is namespaced by action so it doesn't collide
    # with /password-reset/verify or /mfa/challenge, but the limit value comes
    # from the shared `auth.token_verify_rate_limit_per_ip_per_hour` setting —
    # tightening that key tightens TOTP recovery too.
    rl = _enforce_token_rate_limit(
        action='password_reset_via_totp',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many recovery attempts from this network. Please try again later.',
    )
    if rl is not None:
        return rl

    try:
        body = ForgotPasswordTotpRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'details': e.errors(include_context=False),
        }), 400

    # Per-username lockout check (enumeration-safe).
    if IPRateLimiter.is_locked('totp_recovery_fail', body.username):
        audit_service.log_auth(
            action='password_reset_via_totp',
            success=False,
            username=body.username,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='username_locked_out',
        )
        return jsonify({
            'error': 'rate_limit_exceeded',
            'message': 'Too many failed attempts. Try email-based reset, or try again in 30 minutes.',
        }), 429

    user_repo = UserRepository(conn_manager)
    mfa_repo = UserMfaRepository(conn_manager)
    session_repo = SessionRepository(conn_manager)
    user = user_repo.get_user_by_username(body.username)

    # Generic failure: no such user, inactive, external, or no TOTP enrolled.
    if (
        not user
        or not user.get('is_active')
        or UserRepository.is_external_user(user)
    ):
        IPRateLimiter.record_failure(
            action='totp_recovery_fail', key=body.username,
            window_seconds=900, limit=5, lockout_seconds=1800,
        )
        audit_service.log_auth(
            action='password_reset_via_totp',
            success=False,
            username=body.username,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='ineligible_account',
        )
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'That username/code combination is not valid.',
        }), 400

    enabled_methods = mfa_repo.list_enabled_methods(user['id'])
    if not enabled_methods:
        IPRateLimiter.record_failure(
            action='totp_recovery_fail', key=body.username,
            window_seconds=900, limit=5, lockout_seconds=1800,
        )
        audit_service.log_auth(
            action='password_reset_via_totp',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='no_totp_enrolled',
        )
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'That username/code combination is not valid.',
        }), 400

    # Try the supplied code against every enrolled method. Decrypt failures
    # short-circuit to invalid_credentials if NO method decrypts — the user
    # gets the same generic enumeration-safe error either way.
    matched = False
    decrypt_failures = 0
    for method in enabled_methods:
        ciphertext = method.get('mfa_secret')
        if not ciphertext:
            continue
        try:
            secret = MfaService.decrypt_secret(ciphertext)
        except ValueError:
            decrypt_failures += 1
            continue
        if MfaService.verify_totp(secret, body.code):
            matched = True
            break

    if not matched and decrypt_failures == len(enabled_methods):
        audit_service.log_auth(
            action='password_reset_via_totp',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='secret_decrypt_failed',
        )
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'That username/code combination is not valid.',
        }), 400

    if not matched:
        IPRateLimiter.record_failure(
            action='totp_recovery_fail', key=body.username,
            window_seconds=900, limit=5, lockout_seconds=1800,
        )
        audit_service.log_auth(
            action='password_reset_via_totp',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='invalid_code',
        )
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'That username/code combination is not valid.',
        }), 400

    # Validate password strength only AFTER the TOTP code is verified. Doing
    # this earlier lets a distributed attacker probe rapidly with a weak
    # new_password without ever bumping the per-username failure counter
    # (record_failure only fires inside the user-lookup branches above).
    strength = PasswordService.validate_password_strength(body.new_password)
    if not strength['valid']:
        return jsonify({
            'error': 'weak_password',
            'message': '; '.join(strength['errors']),
        }), 400

    # Success: update password, revoke all sessions, drop cached jwt_user.
    user_repo.update_password(user['id'], PasswordService.hash_password(body.new_password))
    sessions_revoked = session_repo.deactivate_all_user_sessions(user['id'])
    try:
        from src.api.utils.caching import cache as _cache
        _cache.delete(f"jwt_user:{user['id']}")
    except Exception:  # pragma: no cover - cache invalidation is best-effort
        pass

    audit_service.log_auth(
        action='password_reset_via_totp',
        success=True,
        user_id=user['id'],
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        metadata={'sessions_revoked': sessions_revoked},
    )
    return jsonify({'success': True}), 200


# =============================================================================
# User Management Endpoints (Admin Only)
# =============================================================================

@auth_bp.route('/users', methods=['GET'])
@jwt_required()
@handle_errors
@require_admin()
def list_users():
    """
    List all users (admin only).

    Query parameters for filtering and pagination.
    ---
    tags:
      - User Management
    parameters:
      - in: query
        name: page
        type: integer
        default: 1
        description: Page number
      - in: query
        name: limit
        type: integer
        default: 50
        description: Items per page (max 100)
      - in: query
        name: role
        type: string
        enum: [user, admin, super_admin]
        description: Filter by role
      - in: query
        name: is_active
        type: boolean
        description: Filter by active status
    responses:
      200:
        description: List of users
        schema:
          type: object
          properties:
            users:
              type: array
              items:
                type: object
            total:
              type: integer
            page:
              type: integer
            limit:
              type: integer
            pages:
              type: integer
      403:
        description: Insufficient permissions
    """
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)

    # Parse query parameters
    try:
        params = UserListParams(
            page=int(request.args.get('page', 1)),
            limit=int(request.args.get('limit', 50)),
            role=request.args.get('role'),
            is_active=request.args.get('is_active')
        )
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid query parameters',
            'details': e.errors()
        }), 400

    try:
        # Get users list
        result = user_repo.list_users(
            page=params.page,
            limit=params.limit,
            role=params.role,
            is_active=params.is_active
        )

        # Convert to response model
        users = [UserResponse(**user) for user in result['users']]

        response = UserListResponse(
            users=users,
            total=result['total'],
            page=result['page'],
            limit=result['limit'],
            pages=result['pages']
        )

        return jsonify(response.dict()), 200

    except Exception as e:
        logger.error(f"Error listing users: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while retrieving users'
        }), 500


@auth_bp.route('/users/stats', methods=['GET'])
@jwt_required()
@handle_errors
@require_admin()
def get_user_stats():
    """
    Get user statistics (admin only).

    Returns user counts broken down by role and active status.
    ---
    tags:
      - User Management
    responses:
      200:
        description: User statistics
        schema:
          type: object
          properties:
            total:
              type: integer
              description: Total number of users
            active:
              type: integer
              description: Number of active users
            inactive:
              type: integer
              description: Number of inactive users
            by_role:
              type: object
              description: User count by role
              properties:
                user:
                  type: integer
                admin:
                  type: integer
                super_admin:
                  type: integer
      403:
        description: Insufficient permissions
    """
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)

    try:
        stats = user_repo.get_user_stats()
        return jsonify(stats), 200

    except Exception as e:
        logger.error(f"Error getting user stats: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while retrieving user statistics'
        }), 500


@auth_bp.route('/users/<user_id>', methods=['GET'])
@jwt_required()
@handle_errors
@require_self_or_admin('user_id')
def get_user(user_id):
    """
    Get user details by ID.

    Users can view their own profile, admins can view any profile.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: User details
      403:
        description: Insufficient permissions
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format'
        }), 400

    try:
        user = user_repo.get_user_by_id(user_uuid)

        if not user:
            return jsonify({
                'error': 'user_not_found',
                'message': 'User not found'
            }), 404

        user_response = UserResponse(**user)
        return jsonify(user_response.dict()), 200

    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while retrieving user'
        }), 500


@auth_bp.route('/users', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
def create_user():
    """
    Create a new user (admin only).

    Creates a user with optional role and temporary password requirements.
    ---
    tags:
      - User Management
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - email
            - password
          properties:
            username:
              type: string
              minLength: 3
              maxLength: 255
            email:
              type: string
              format: email
            password:
              type: string
              minLength: 8
            full_name:
              type: string
            role:
              type: string
              enum: [user, admin, super_admin]
              default: user
            must_change_password:
              type: boolean
              default: true
    responses:
      201:
        description: User created successfully
      400:
        description: Validation error or user already exists
      403:
        description: Insufficient permissions
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    # Validate request data
    try:
        data = CreateUserAdminRequest(**request.json)
    except ValidationError as e:
        # include_context=False strips ctx.error (a raw ValueError instance from
        # the password_required_unless_invite validator) which Flask cannot
        # JSON-serialize.
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors(include_context=False)
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    # Prevent role escalation: cannot assign a role higher than caller's own
    if data.role is not None:
        caller_level = ROLE_HIERARCHY.get(admin_user.get('role', 'user'), 999)
        target_level = ROLE_HIERARCHY.get(data.role, 999)
        if target_level < caller_level:
            return jsonify({
                'error': 'insufficient_permissions',
                'message': 'Cannot assign a role higher than your own'
            }), 403

    try:
        # Check if username already exists
        existing_user = user_repo.get_user_by_username(data.username)
        if existing_user:
            return jsonify({
                'error': 'username_exists',
                'message': 'Username already exists'
            }), 400

        # Check if email already exists
        existing_email = user_repo.get_user_by_email(data.email)
        if existing_email:
            return jsonify({
                'error': 'email_exists',
                'message': 'Email already exists'
            }), 409

        # Hash password OR generate a random unusable hash for invite-only users.
        if data.invite_only:
            password_hash = InviteService.make_unusable_password_hash()
        else:
            strength = PasswordService.validate_password_strength(data.password)
            if not strength['valid']:
                raise ValueError(
                    f"Password does not meet requirements: {'; '.join(strength['errors'])}"
                )
            password_hash = PasswordService.hash_password(data.password)

        # Create user
        created_user = user_repo.create_user(
            username=data.username,
            email=data.email,
            password_hash=password_hash,
            full_name=data.full_name,
            role=data.role,
        )

        user_id = created_user['id']
        new_user = user_repo.get_user_by_id(user_id)

        audit_service.log_auth(
            action='user_created',
            success=True,
            user_id=user_id,
            username=data.username,
            ip=ip_address,
            user_agent=user_agent,
            metadata={
                'created_by': admin_user.get('username'),
                'invite_only': data.invite_only,
                # The assigned role, as a plain string like the SSO provisioning
                # path stores it, lets the seeded auth-privileged-account-created
                # alert rule fire on manual admin/super_admin creation (#882/#883).
                'role': data.role.value,
            },
        )

        invite_url: Optional[str] = None
        invite_expires_at = None
        email_sent = False

        if data.invite_only:
            reset_repo = PasswordResetRepository(conn_manager)
            ttl_hours = int(current_app.settings_manager.get(
                'auth', 'invite_token_ttl_hours', InviteService.DEFAULT_TTL_HOURS
            ))
            invite = InviteService.create_invite_token(
                UUID(user_id), reset_repo, user_repo, ttl_hours=ttl_hours,
            )
            base_url = resolve_public_base_url(current_app.settings_manager)
            invite_url = (
                f"{base_url}/onboarding?token={invite['token']}"
                if base_url else f"/onboarding?token={invite['token']}"
            )
            invite_expires_at = invite['expires_at']

            audit_service.log_auth(
                action='invite_issued',
                success=True,
                user_id=user_id,
                username=data.username,
                ip=ip_address,
                user_agent=user_agent,
                metadata={'issued_by': admin_user.get('username')},
            )

            try:
                email_sent = current_app.email_service.send_invite_email(
                    to_email=data.email,
                    username=data.username,
                    invite_token=invite['token'],
                    expires_in_hours=ttl_hours,
                )
            except Exception as email_exc:
                logger.warning(
                    "invite email failed for %s (%s): %s",
                    data.username, data.email, email_exc,
                )
                email_sent = False

            logger.info(
                "User %s created by admin %s (invite-only); email_sent=%s",
                data.username, admin_user.get('username'), email_sent,
            )
        else:
            logger.info(
                "User %s created by admin %s (password set)",
                data.username, admin_user.get('username'),
            )
            try:
                email_sent = current_app.email_service.send_welcome_email(
                    to_email=data.email,
                    username=data.username,
                )
            except Exception as email_exc:
                logger.warning(
                    "welcome email failed for %s (%s): %s",
                    data.username, data.email, email_exc,
                )
                email_sent = False

        user_response = UserResponse(**new_user)
        response = CreateUserAdminResponse(
            user=user_response,
            invite_url=invite_url,
            invite_expires_at=invite_expires_at,
            email_sent=email_sent,
        )
        return jsonify(response.dict()), 201

    except ValueError as e:
        return jsonify({
            'error': 'validation_error',
            'message': str(e)
        }), 400
    except Exception as e:
        logger.error(f"Error creating user: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while creating user'
        }), 500


@auth_bp.route('/users/<user_id>', methods=['PUT'])
@jwt_required()
@handle_errors
@require_admin()
def update_user(user_id):
    """
    Update user details (admin only).

    Allows updating email, full name, and role. To change is_active,
    use PATCH /users/<id>/disable or /enable so the action is recorded
    as a distinct user_disabled / user_enabled audit event.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
      - in: body
        name: body
        schema:
          type: object
          properties:
            email:
              type: string
              format: email
            full_name:
              type: string
            role:
              type: string
              enum: [user, admin, super_admin]
    responses:
      200:
        description: User updated successfully
      400:
        description: Validation error (includes attempted is_active write)
      403:
        description: Insufficient permissions
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    # Validate request data
    try:
        data = UpdateUserAdminRequest(**request.json)
    except ValidationError as e:
        # Surface a redirect hint for the most common rejection (is_active),
        # since the raw "Extra inputs are not permitted" doesn't tell the
        # admin where to actually go to disable/enable a user.
        message = 'Invalid request data'
        if any('is_active' in (err.get('loc') or ()) for err in e.errors()):
            message = (
                "is_active cannot be changed via PUT /users/<id>. "
                "Use PATCH /api/auth/users/<id>/disable or "
                "/api/auth/users/<id>/enable so the action is recorded "
                "as a distinct user_disabled / user_enabled audit event."
            )
        return jsonify({
            'error': 'validation_error',
            'message': message,
            'details': [
                {k: v for k, v in err.items() if k != 'ctx'}
                for err in e.errors()
            ],
        }), 400

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format'
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    # Prevent role escalation: cannot assign a role higher than caller's own
    if data.role is not None:
        caller_level = ROLE_HIERARCHY.get(admin_user.get('role', 'user'), 999)
        target_level = ROLE_HIERARCHY.get(data.role, 999)
        if target_level < caller_level:
            return jsonify({
                'error': 'insufficient_permissions',
                'message': 'Cannot assign a role higher than your own'
            }), 403

    try:
        # Check if user exists
        user = user_repo.get_user_by_id(user_uuid)
        if not user:
            return jsonify({
                'error': 'user_not_found',
                'message': 'User not found'
            }), 404

        # Build updates dictionary (only include provided fields)
        updates = {}
        if data.email is not None:
            # Check if new email already exists
            existing_email = user_repo.get_user_by_email(data.email)
            if existing_email and existing_email['id'] != str(user_uuid):
                return jsonify({
                    'error': 'email_exists',
                    'message': 'Email already in use by another user'
                }), 409
            updates['email'] = data.email

        if data.full_name is not None:
            updates['full_name'] = data.full_name

        if data.role is not None:
            updates['role'] = data.role

        # Update user using the existing method signature
        if updates:
            updated_user = user_repo.update_user(
                user_uuid,
                email=updates.get('email'),
                full_name=updates.get('full_name'),
                role=updates.get('role')
            )

        # Get updated user
        updated_user = user_repo.get_user_by_id(user_uuid)

        # Role changes must invalidate the cached JWT user record so the
        # demoted/promoted user's claims are re-loaded from the DB on the
        # next request (otherwise stale role persists for up to 60s).
        if 'role' in updates:
            _invalidate_user_caches(user_uuid)

        # Audit: emit one row per changed field so the trail is specific.
        # Role changes keep emitting user_updated so existing
        # cache-invalidation tracking and any SOC rules filtering on that
        # action continue to fire.
        common_kwargs = dict(
            success=True,
            user_id=user_uuid,
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            metadata={'updated_by': admin_user.get('username')},
        )
        if 'email' in updates:
            audit_service.log_auth(
                action='user_email_changed_by_admin',
                **common_kwargs,
            )
        if 'full_name' in updates:
            audit_service.log_auth(
                action='user_display_name_changed_by_admin',
                **common_kwargs,
            )
        if 'role' in updates:
            # Record the old AND new role (not just THAT the role changed) so the
            # seeded auth-admin-role-granted rule can key an exact
            # non-privileged -> privileged escalation off this row alone, instead
            # of joining the target's *current* role. `user` was fetched before
            # the update, so user['role'] is the pre-change role; updated_user
            # reflects the persisted new role. Both come from DB dicts, so they
            # are plain lowercase strings matching the rule's IN list (#823).
            audit_service.log_auth(
                action='user_updated',
                **{
                    **common_kwargs,
                    'metadata': {
                        **common_kwargs['metadata'],
                        'changes': ['role'],
                        'old_role': user['role'],
                        'new_role': updated_user['role'],
                    },
                },
            )

        logger.info(f"User {user['username']} updated by admin {admin_user.get('username')}")

        user_response = UserResponse(**updated_user)
        return jsonify(user_response.dict()), 200

    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while updating user'
        }), 500


@auth_bp.route('/users/<user_id>/disable', methods=['PATCH'])
@jwt_required()
@handle_errors
@require_admin()
def disable_user(user_id):
    """
    Disable a user account (admin only).

    Soft delete - sets is_active to false and revokes all sessions.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: User disabled successfully
      403:
        description: Insufficient permissions
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    session_repo = SessionRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format'
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    try:
        # Check if user exists
        user = user_repo.get_user_by_id(user_uuid)
        if not user:
            return jsonify({
                'error': 'user_not_found',
                'message': 'User not found'
            }), 404

        # Prevent self-disable
        if str(user_uuid) == str(admin_user.get('id')):
            return jsonify({
                'error': 'cannot_disable_self',
                'message': 'You cannot disable your own account'
            }), 400

        # Check if already disabled
        if not user.get('is_active', True):
            return jsonify({
                'error': 'already_disabled',
                'message': 'User account is already disabled'
            }), 400

        # Deactivate user (soft delete - sets is_active to false)
        user_repo.deactivate_user(user_uuid)

        # Revoke all user sessions
        sessions_revoked = session_repo.deactivate_all_user_sessions(user_uuid)

        # Drop cached jwt_user record so the disabled user's session stops
        # passing revocation checks immediately (otherwise stale for up to 60s).
        _invalidate_user_caches(user_uuid)

        # Audit log
        audit_service.log_auth(
            action='user_disabled',
            success=True,
            user_id=user_uuid,
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            metadata={
                'disabled_by': admin_user.get('username'),
                'sessions_revoked': sessions_revoked
            }
        )

        logger.info(f"User {user['username']} disabled by admin {admin_user.get('username')}, {sessions_revoked} sessions revoked")

        return jsonify({
            'success': True,
            'message': f"User {user['username']} has been disabled",
            'sessions_revoked': sessions_revoked
        }), 200

    except Exception as e:
        logger.error(f"Error disabling user {user_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while disabling user'
        }), 500


@auth_bp.route('/users/<user_id>/enable', methods=['PATCH'])
@jwt_required()
@handle_errors
@require_admin()
def enable_user(user_id):
    """
    Enable a disabled user account (admin only).

    Reactivates a disabled user by setting is_active to true.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: User enabled successfully
      403:
        description: Insufficient permissions
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format'
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    try:
        # Check if user exists
        user = user_repo.get_user_by_id(user_uuid)
        if not user:
            return jsonify({
                'error': 'user_not_found',
                'message': 'User not found'
            }), 404

        # Check if already enabled
        if user.get('is_active', False):
            return jsonify({
                'error': 'already_enabled',
                'message': 'User account is already enabled'
            }), 400

        # Activate user
        user_repo.activate_user(user_uuid)

        # Drop cached jwt_user record so the negative-cached "disabled" state
        # doesn't keep blocking the now-re-enabled user for up to 60s.
        _invalidate_user_caches(user_uuid)

        # Audit log
        audit_service.log_auth(
            action='user_enabled',
            success=True,
            user_id=user_uuid,
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            metadata={
                'enabled_by': admin_user.get('username')
            }
        )

        logger.info(f"User {user['username']} enabled by admin {admin_user.get('username')}")

        return jsonify({
            'success': True,
            'message': f"User {user['username']} has been enabled"
        }), 200

    except Exception as e:
        logger.error(f"Error enabling user {user_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while enabling user'
        }), 500


@auth_bp.route('/users/<user_id>/password-reset', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
def admin_password_reset(user_id):
    """
    Admin-initiated password reset (#614).

    Sends a password-reset email to the target user. Distinct from the
    public /password-reset/request endpoint so the audit trail records
    `user_password_changed_by_admin` instead of `password_reset_request`.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: Reset email sent (or queued)
      400:
        description: Target is not a local-password user
      403:
        description: Caller is not an admin
      404:
        description: Target user not found
    """
    from uuid import UUID
    from src.auth.services.password_reset_service import PasswordResetService

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format',
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    user = user_repo.get_user_by_id(user_uuid)
    if not user:
        return jsonify({'error': 'user_not_found', 'message': 'User not found'}), 404

    if UserRepository.is_external_user(user):
        return jsonify({
            'error': 'non_local_user',
            'message': 'Cannot reset password for an LDAP or OIDC user.',
        }), 400

    try:
        reset_data = PasswordResetService.create_reset_token(
            user_id=user_uuid,
            reset_repo=reset_repo,
            user_repo=user_repo,
        )
        email_sent = current_app.email_service.send_password_reset_email(
            to_email=user['email'],
            username=user['username'],
            reset_token=reset_data['token'],
            expires_in_hours=PasswordResetService.TOKEN_EXPIRATION_HOURS,
        )
    except Exception as e:
        logger.error(
            f"Admin password reset failed for user {user_uuid}: {e}", exc_info=True
        )
        audit_service.log_auth(
            action='user_password_changed_by_admin',
            success=False,
            user_id=user_uuid,
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason=str(e)[:200],
            metadata={'reset_by': admin_user.get('username')},
        )
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while sending the reset email',
        }), 500

    audit_service.log_auth(
        action='user_password_changed_by_admin',
        success=bool(email_sent),
        user_id=user_uuid,
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        failure_reason=None if email_sent else 'Email send failed',
        metadata={'reset_by': admin_user.get('username')},
    )

    return jsonify({
        'success': True,
        'message': 'Password reset email sent',
        'email_sent': bool(email_sent),
    }), 200


@auth_bp.route('/users/<user_id>/force-password-change', methods=['PATCH'])
@jwt_required()
@handle_errors
@require_admin()
def force_password_change(user_id):
    """
    Force the user to change their password on next login (#614).

    Idempotent flip of `must_change_password=true`. Does NOT change the
    password, send any email, or revoke sessions.
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: Flag set
      400:
        description: Target is not a local-password user
      403:
        description: Caller is not an admin
      404:
        description: Target user not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({'error': 'invalid_id', 'message': 'Invalid user ID format'}), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    user = user_repo.get_user_by_id(user_uuid)
    if not user:
        return jsonify({'error': 'user_not_found', 'message': 'User not found'}), 404

    if UserRepository.is_external_user(user):
        return jsonify({
            'error': 'non_local_user',
            'message': 'Cannot force a password change for an LDAP or OIDC user.',
        }), 400

    user_repo.set_must_change_password(user_uuid)

    # Drop the target user's cached jwt_user record so /auth/me reflects
    # must_change_password=true on the next request. Without this, the
    # ProtectedRoute force-change redirect doesn't fire for up to 60s.
    _invalidate_user_caches(user_uuid)

    audit_service.log_auth(
        action='user_force_password_change_set',
        success=True,
        user_id=user_uuid,
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        metadata={'set_by': admin_user.get('username')},
    )

    return jsonify({
        'success': True,
        'message': f"User {user['username']} will be required to change their password on next login.",
    }), 200


@auth_bp.route('/users/<user_id>/reset-mfa', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
def reset_user_mfa(user_id):
    """
    Reset (delete) the target user's MFA enrollment (#614).

    Deletes the user_mfa row. Combined with #610's unconditional MFA
    enforcement for local users, the user is forced back through the
    enrollment wizard on next login.

    Self-targeting is blocked: an admin cannot reset their own MFA via
    this endpoint (avoids accidental self-lockout).
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: MFA row deleted
      400:
        description: Caller cannot target themselves
      403:
        description: Caller is not an admin
      404:
        description: Target user not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    mfa_repo = UserMfaRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({'error': 'invalid_id', 'message': 'Invalid user ID format'}), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    if str(user_uuid) == str(admin_user.get('id')):
        return jsonify({
            'error': 'cannot_reset_own_mfa',
            'message': (
                'You cannot reset your own MFA. Use the MFA section on your '
                'profile to disable and re-enroll.'
            ),
        }), 400

    user = user_repo.get_user_by_id(user_uuid)
    if not user:
        return jsonify({'error': 'user_not_found', 'message': 'User not found'}), 404

    deleted = mfa_repo.delete_all_methods(user_uuid)

    # Drop the target user's cached jwt_user record so MFA-state checks
    # see the reset immediately rather than after the 60s TTL.
    _invalidate_user_caches(user_uuid)

    audit_service.log_auth(
        action='user_mfa_reset_by_admin',
        success=True,
        user_id=user_uuid,
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        metadata={'reset_by': admin_user.get('username'), 'methods_removed': deleted},
    )

    return jsonify({
        'success': True,
        'message': f"MFA reset for {user['username']}. They will be re-enrolled on next login.",
    }), 200


@auth_bp.route('/users/<user_id>', methods=['DELETE'])
@jwt_required()
@handle_errors
@require_admin()
def delete_user(user_id):
    """
    Permanently delete a user (admin only).

    Hard delete - permanently removes the user from the database.
    WARNING: This action cannot be undone!
    ---
    tags:
      - User Management
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
        description: User UUID
    responses:
      200:
        description: User deleted successfully
      403:
        description: Insufficient permissions
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    session_repo = SessionRepository(conn_manager)
    audit_service = AuditService(conn_manager)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({
            'error': 'invalid_id',
            'message': 'Invalid user ID format'
        }), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')
    admin_user = current_user

    try:
        # Check if user exists
        user = user_repo.get_user_by_id(user_uuid)
        if not user:
            return jsonify({
                'error': 'user_not_found',
                'message': 'User not found'
            }), 404

        # Prevent self-deletion
        if str(user_uuid) == str(admin_user.get('id')):
            return jsonify({
                'error': 'cannot_delete_self',
                'message': 'You cannot delete your own account'
            }), 400

        # Store username for response before deletion
        username = user['username']

        # Revoke all user sessions first
        sessions_revoked = session_repo.deactivate_all_user_sessions(user_uuid)

        # Audit log BEFORE deleting (so foreign key constraint doesn't fail)
        audit_service.log_auth(
            action='user_deleted_permanent',
            success=True,
            user_id=user_uuid,
            username=username,
            ip=ip_address,
            user_agent=user_agent,
            metadata={
                'deleted_by': admin_user.get('username'),
                'sessions_revoked': sessions_revoked
            }
        )

        # Hard delete user (permanently removes from database)
        deleted = user_repo.delete_user(user_uuid)

        if not deleted:
            return jsonify({
                'error': 'delete_failed',
                'message': 'Failed to delete user'
            }), 500

        # Drop cached jwt_user record so any in-flight requests with the
        # deleted user's token fail fast instead of waiting for the 60s TTL.
        _invalidate_user_caches(user_uuid)

        logger.info(f"User {username} permanently deleted by admin {admin_user.get('username')}, {sessions_revoked} sessions revoked")

        return jsonify({
            'success': True,
            'message': f"User {username} has been permanently deleted",
            'sessions_revoked': sessions_revoked
        }), 200

    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'internal_error',
            'message': 'An error occurred while deleting user'
        }), 500


# =============================================================================
# Onboarding (invite-based) Endpoints
# =============================================================================

@auth_bp.route('/users/<user_id>/resend-invite', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
def resend_invite(user_id):
    """
    Issue a fresh onboarding invite link for a user (admin only).

    Invalidates any prior unused invite for this user. Returns the new link
    (so the admin can copy it) and best-effort sends the invite email.
    ---
    tags:
      - Onboarding
    responses:
      200:
        description: New invite issued
      404:
        description: User not found
    """
    from uuid import UUID

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    admin_user = current_user

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return jsonify({'error': 'invalid_user_id', 'message': 'Invalid user id'}), 400

    user = user_repo.get_user_by_id(user_uuid)
    if not user:
        return jsonify({'error': 'user_not_found', 'message': 'User not found'}), 404

    # Onboarded users have a real bcrypt hash and a different recovery path
    # (Reset Password). Issuing a fresh invite for them would let an admin
    # bypass the password-reset audit action and replay onboarding — refuse.
    if not user.get('onboarding_pending', False):
        return jsonify({
            'error': 'cannot_invite',
            'message': 'User has already completed onboarding. Use Reset Password instead.',
        }), 400

    ttl_hours = int(current_app.settings_manager.get(
        'auth', 'invite_token_ttl_hours', InviteService.DEFAULT_TTL_HOURS
    ))

    try:
        invite = InviteService.create_invite_token(
            user_uuid, reset_repo, user_repo, ttl_hours=ttl_hours,
        )
    except ValueError as e:
        return jsonify({'error': 'cannot_invite', 'message': str(e)}), 400

    base_url = resolve_public_base_url(current_app.settings_manager)
    invite_url = (
        f"{base_url}/onboarding?token={invite['token']}"
        if base_url else f"/onboarding?token={invite['token']}"
    )

    email_sent = False
    try:
        email_sent = current_app.email_service.send_invite_email(
            to_email=user['email'],
            username=user['username'],
            invite_token=invite['token'],
            expires_in_hours=ttl_hours,
        )
    except Exception as email_exc:
        logger.warning(
            "resend invite email failed for %s (%s): %s",
            user['username'], user['email'], email_exc,
        )

    audit_service.log_auth(
        action='invite_resent',
        success=True,
        user_id=user_id,
        username=user['username'],
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'issued_by': admin_user.get('username'), 'email_sent': email_sent},
    )

    response = ResendInviteResponse(
        success=True,
        invite_url=invite_url,
        invite_expires_at=invite['expires_at'],
        email_sent=email_sent,
    )
    return jsonify(response.dict()), 200


@auth_bp.route('/onboarding/validate', methods=['GET'])
@handle_errors
def validate_onboarding_token():
    """
    Validate (without consuming) an invite token.

    Public endpoint. Used by the wizard's first step to greet the user with
    their name. Always responds 200 with `{valid: bool, ...}` — never reveals
    why a token failed (avoids leaking whether the format is wrong, expired,
    or already used).
    ---
    tags:
      - Onboarding
    parameters:
      - in: query
        name: token
        required: true
        schema:
          type: string
    responses:
      200:
        description: Validation result
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    rate_limited = _enforce_token_rate_limit(
        'onboarding_validate', audit_service, request.remote_addr,
        request.headers.get('User-Agent', ''),
        message='Too many onboarding requests from this network. Please try again later.',
    )
    if rate_limited is not None:
        return rate_limited

    token = request.args.get('token', '')
    if not token:
        return jsonify(OnboardingValidateResponse(valid=False).dict()), 200

    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)

    try:
        result = InviteService.validate_invite_token(
            token,
            reset_repo,
            user_repo,
        )
    except Exception as e:
        logger.warning("Onboarding token validation error: %s", e)
        result = {"valid": False}

    return jsonify(OnboardingValidateResponse(**result).dict()), 200


@auth_bp.route('/onboarding/complete', methods=['POST'])
@handle_errors
def complete_onboarding():
    """
    Consume an invite token: set the user's password and activate the account.

    Public endpoint. The token is the only authentication needed.
    ---
    tags:
      - Onboarding
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [token, new_password]
    responses:
      200:
        description: Account activated
      400:
        description: Invalid token or password too weak
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)

    rate_limited = _enforce_token_rate_limit(
        'onboarding_complete', audit_service, request.remote_addr,
        request.headers.get('User-Agent', ''),
        message='Too many onboarding requests from this network. Please try again later.',
    )
    if rate_limited is not None:
        return rate_limited

    try:
        data = OnboardingCompleteRequest(**request.json)
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors(include_context=False),
        }), 400

    user_repo = UserRepository(conn_manager)
    reset_repo = PasswordResetRepository(conn_manager)

    try:
        result = InviteService.consume_invite_token(
            data.token, data.new_password, reset_repo, user_repo,
        )
    except ValueError as e:
        audit_service.log_auth(
            action='onboarding_complete',
            success=False,
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            failure_reason=str(e),
        )
        return jsonify({
            'error': 'invalid_token_or_password',
            'message': str(e),
        }), 400

    audit_service.log_auth(
        action='onboarding_complete',
        success=True,
        user_id=result['user_id'],
        username=result['username'],
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
    )

    return jsonify(OnboardingCompleteResponse(
        success=True,
        username=result['username'],
    ).dict()), 200


# =============================================================================
# MFA self-service endpoints
# =============================================================================

@auth_bp.route('/mfa/status', methods=['GET'])
@jwt_required()
@handle_errors
def mfa_status():
    """Return current user's MFA status.
    ---
    tags: [Authentication]
    responses:
      200: {description: MFA status}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    # External users (LDAP/OIDC) cannot enroll MFA in V1.
    if _jwt_is_external_user():
        return jsonify(MfaStatusResponse(enabled=False).model_dump(mode='json')), 200

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    user_id = current_user['id']

    response = MfaStatusResponse(
        enabled=mfa_repo.count_enabled_methods(user_id) > 0,
    )
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/mfa/setup', methods=['POST'])
@jwt_required()
@handle_errors
def mfa_setup():
    """Begin MFA enrollment: generate secret, store encrypted as a pending
    method, return method_id + secret + provisioning URI. mfa_enabled stays
    false until /verify. Multi-method (#608 follow-up): each call replaces
    any prior pending row, so re-fetching the QR rotates state cleanly. Users
    can have multiple ENABLED methods — this endpoint adds another one.
    ---
    tags: [Authentication]
    responses:
      200: {description: Provisioning info (method_id + secret + URI)}
      400: {description: Validation error or external user}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']

    # External users (LDAP/OIDC) cannot enroll MFA in V1.  Read from JWT —
    # no DB round-trip needed.
    if _jwt_is_external_user():
        return jsonify({
            'error': 'mfa_not_supported_for_external_users',
            'message': 'MFA enrollment is not available for SSO/LDAP users.',
        }), 400

    rl = _enforce_token_rate_limit(
        action='mfa_setup',
        audit_service=audit_service,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        message='Too many MFA setup attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    # Optional label from body. Empty body is fine for the auth-direct path
    # (no JSON sent) and the validator allows label=None.
    try:
        body = MfaSetupRequest(**(request.json or {})) if request.is_json else MfaSetupRequest()
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400
    raw_label = body.label

    secret = MfaService.generate_secret()
    encrypted = MfaService.encrypt_secret(secret)
    # Atomic: replace any prior pending row + insert the new one under a
    # per-user advisory lock so two concurrent /mfa/setup calls can't leave
    # orphan pending rows behind. Verified methods are not affected.
    method_id = mfa_repo.replace_pending_method(
        user_id, encrypted_secret=encrypted, label=raw_label,
    )
    label = (raw_label or '').strip() or MFA_DEFAULT_LABEL

    provisioning_uri = MfaService.provisioning_uri(secret, username=username)

    audit_service.log_auth(
        action='mfa_setup_initiated',
        success=True,
        user_id=user_id,
        username=username,
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'method_id': str(method_id), 'label': label},
    )

    response = MfaSetupResponse(
        method_id=str(method_id),
        secret=secret,
        provisioning_uri=provisioning_uri,
        label=label,
    )
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/mfa/verify', methods=['POST'])
@jwt_required()
@handle_errors
def mfa_verify():
    """Complete MFA enrollment: verify TOTP code, flip mfa_enabled=true.
    ---
    tags: [Authentication]
    responses:
      200: {description: MFA enabled}
      400: {description: Wrong code or no pending setup}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']

    try:
        body = MfaVerifyRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400

    pending = mfa_repo.get_pending_method(user_id)
    if not pending or not pending.get('mfa_secret'):
        return jsonify({'error': 'no_pending_setup', 'message': 'Run /mfa/setup first.'}), 400

    try:
        secret = MfaService.decrypt_secret(pending['mfa_secret'])
    except ValueError:
        audit_service.log_auth(
            action='mfa_verify',
            success=False,
            user_id=user_id,
            username=username,
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            failure_reason='secret_decrypt_failed',
        )
        return jsonify({
            'error': 'no_pending_setup',
            'message': 'MFA setup state is invalid. Re-run /mfa/setup.',
        }), 400
    if not MfaService.verify_totp(secret, body.code):
        audit_service.log_auth(
            action='mfa_verify',
            success=False,
            user_id=user_id,
            username=username,
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            failure_reason='invalid_code',
        )
        return jsonify({'error': 'invalid_code', 'message': 'TOTP code did not match.'}), 400

    mfa_repo.enable_method(pending['id'])

    audit_service.log_auth(
        action='mfa_enabled',
        success=True,
        user_id=user_id,
        username=username,
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'method_id': str(pending['id']), 'label': pending.get('label')},
    )

    response = MfaVerifyResponse(enabled=True)
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/mfa/disable', methods=['POST'])
@jwt_required()
@handle_errors
def mfa_disable():
    """Disable MFA entirely (remove every enrolled method). Requires current
    password AND a current TOTP code that matches any of the user's enrolled
    methods. Multi-method (#608 follow-up): the supplied code is matched
    against every enabled method's secret.

    Note: under #610 unconditional enforcement, the next login will force the
    user back through enrollment — `mfa_disable` is effectively "start over".
    Per-method removal lives at /api/account/mfa/<id>.
    ---
    tags: [Authentication]
    responses:
      200: {description: Disabled}
      400: {description: Wrong code or MFA not enabled}
      401: {description: Wrong password}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    if _jwt_is_external_user():
        return jsonify({
            'error': 'mfa_not_supported_for_external_users',
            'message': 'MFA enrollment is not available for SSO/LDAP users.',
        }), 400

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']

    try:
        body = MfaDisableRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400

    # get_user_by_id strips password_hash; use get_user_by_username so the
    # re-auth verify below has the actual hash to compare against.
    user = user_repo.get_user_by_username(current_user['username'])
    if not user:
        return jsonify({'error': 'user_not_found'}), 404

    # Re-auth: verify password
    if not PasswordService.verify_password(body.password, user['password_hash']):
        audit_service.log_auth(
            action='mfa_disable',
            success=False,
            user_id=user_id,
            username=user['username'],
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            failure_reason='invalid_password',
        )
        return jsonify({'error': 'invalid_credentials', 'message': 'Wrong password.'}), 401

    enabled = mfa_repo.list_enabled_methods(user_id)
    if not enabled:
        return jsonify({'error': 'mfa_not_enabled'}), 400

    # The code must match at least one of the user's enabled methods. We do
    # not leak which one — that would be useful for an attacker who has
    # somehow obtained the user's password but not their authenticator.
    matched = False
    decrypt_failures = 0
    for method in enabled:
        ciphertext = method.get('mfa_secret')
        if not ciphertext:
            continue
        try:
            secret = MfaService.decrypt_secret(ciphertext)
        except ValueError:
            decrypt_failures += 1
            continue
        if MfaService.verify_totp(secret, body.code):
            matched = True
            break

    if not matched:
        # If every secret failed to decrypt the failure isn't really the
        # user's code — surface that distinctly so operators can investigate
        # encryption-key rotation or DB tampering.
        reason = 'secret_decrypt_failed' if (
            decrypt_failures > 0 and decrypt_failures == len(enabled)
        ) else 'invalid_code'
        audit_service.log_auth(
            action='mfa_disable',
            success=False,
            user_id=user_id,
            username=user['username'],
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            failure_reason=reason,
        )
        return jsonify({'error': 'invalid_code'}), 400

    deleted = mfa_repo.delete_all_methods(user_id)
    audit_service.log_auth(
        action='mfa_disabled',
        success=True,
        user_id=user_id,
        username=user['username'],
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'methods_removed': deleted},
    )
    return jsonify({'success': True}), 200


@auth_bp.route('/mfa/challenge', methods=['POST'])
@handle_errors
def mfa_challenge():
    """Exchange an MFA challenge token + code for full access/refresh tokens.
    ---
    tags: [Authentication]
    responses:
      200: {description: Tokens issued}
      400: {description: Wrong code}
      401: {description: Invalid or expired challenge token}
      429: {description: Rate-limit exceeded for this IP}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    # Rate-limit per IP (reuses shared helper)
    rl = _enforce_token_rate_limit(
        action='mfa_challenge',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many MFA attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    try:
        body = MfaChallengeRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400

    cache_key = f'mfa_challenge:{body.mfa_challenge_token}'
    entry = cache.get(cache_key)
    if not entry:
        return jsonify({
            'error': 'invalid_or_expired_challenge',
            'message': 'MFA challenge expired or already used. Please log in again.',
        }), 401

    user_id = entry['user_id']
    attempts = int(entry.get('attempts', 0))

    user_repo = UserRepository(conn_manager)
    mfa_repo = UserMfaRepository(conn_manager)

    user = user_repo.get_user_by_id(user_id)
    enabled_methods = mfa_repo.list_enabled_methods(user_id) if user else []
    if not user or not user.get('is_active') or not enabled_methods:
        cache.delete(cache_key)
        return jsonify({'error': 'invalid_or_expired_challenge'}), 401

    code = body.code

    # Try every enrolled method's secret; accept on the first match. We do
    # not record which method matched — that surface would be useful only to
    # an attacker triangulating which device responded.
    matched_method_id = None
    decrypt_failures = 0
    for method in enabled_methods:
        ciphertext = method.get('mfa_secret')
        if not ciphertext:
            continue
        try:
            secret = MfaService.decrypt_secret(ciphertext)
        except ValueError:
            decrypt_failures += 1
            continue
        if MfaService.verify_totp(secret, code):
            matched_method_id = method['id']
            break

    if matched_method_id is None and decrypt_failures == len(enabled_methods):
        # Every secret failed to decrypt — encryption key rotation or DB
        # tampering. Treat as "challenge invalid"; force the user back through
        # login, where the operator can investigate.
        cache.delete(cache_key)
        audit_service.log_auth(
            action='login_mfa',
            success=False,
            user_id=user_id,
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='secret_decrypt_failed',
        )
        return jsonify({
            'error': 'invalid_or_expired_challenge',
            'message': 'MFA state is invalid. Please log in again.',
        }), 401

    totp_ok = matched_method_id is not None
    if not totp_ok:
        # NOTE: read-modify-write on `attempts` is not atomic. With Redis backend, two
        # concurrent requests with the same challenge token can each see attempts<5 and
        # both write attempts+1 — letting an attacker exceed the 5-attempt cap by parallel
        # probing. SimpleCache (test/dev) is single-process so this is fine. A proper fix
        # would use Redis INCR; the IP rate limit (`_enforce_token_rate_limit`) above is
        # the broader containment for now.
        attempts += 1
        if attempts >= 5:
            cache.delete(cache_key)
            audit_service.log_auth(
                action='login_mfa',
                success=False,
                user_id=user_id,
                username=user['username'],
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='too_many_attempts',
            )
        else:
            cache.set(cache_key, {'user_id': user_id, 'attempts': attempts}, timeout=300)
            audit_service.log_auth(
                action='login_mfa',
                success=False,
                user_id=user_id,
                username=user['username'],
                ip=ip_address,
                user_agent=user_agent,
                failure_reason='invalid_code',
            )
        return jsonify({'error': 'invalid_code', 'message': 'TOTP code did not match.'}), 400

    cache.delete(cache_key)

    # MFA is blocked for external users at enrollment, so mfa_challenge users
    # are always local — auth_method is always 'local' here.
    access_token, refresh_token, expires_in, _session_id = _issue_session_tokens(
        user, ip_address, user_agent, auth_method='local',
    )

    audit_service.log_auth(
        action='login_mfa',
        success=True,
        user_id=user_id,
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        metadata={'method': 'totp', 'method_id': str(matched_method_id)},
    )

    user_response = UserResponse(
        id=user['id'], username=user['username'], email=user['email'],
        full_name=user.get('full_name'), role=user['role'],
        is_active=user['is_active'],
        email_verified=user.get('email_verified', False),
        must_change_password=user.get('must_change_password', False),
        last_login_at=user.get('last_login_at'),
        created_at=user['created_at'],
    )
    response = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type='Bearer',
        expires_in=expires_in,
        user=user_response,
    )
    return jsonify(response.model_dump(mode='json')), 200


def _resolve_enrollment_token(token: str) -> tuple:
    """Look up an enrollment token + load the user.

    Returns (user_dict, mfa_repo, cache_key) on success, or a
    (response, status_code) tuple on failure for the caller to return.
    """
    cache_key = f'mfa_enrollment:{token}'
    entry = cache.get(cache_key)
    if not entry:
        return jsonify({
            'error': 'invalid_or_expired_enrollment',
            'message': 'MFA enrollment session expired. Please log in again.',
        }), 401

    user_id = entry['user_id']
    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    mfa_repo = UserMfaRepository(conn_manager)

    user = user_repo.get_user_by_id(user_id)
    if not user or not user.get('is_active'):
        cache.delete(cache_key)
        return jsonify({
            'error': 'invalid_or_expired_enrollment',
            'message': 'MFA enrollment session is no longer valid.',
        }), 401
    return user, mfa_repo, cache_key


@auth_bp.route('/mfa/enroll-setup', methods=['POST'])
@handle_errors
def mfa_enroll_setup():
    """First step of the post-login MFA enrollment flow (any local user with no MFA enrolled).

    Accepts the one-time enrollment token returned by /login (instead of a JWT —
    the user has no session yet) and returns the TOTP secret + provisioning URI.
    Rate-limited per IP. The enrollment token remains valid (single-use is enforced
    only by /mfa/enroll-verify) so the user can re-fetch the QR if their browser dies.
    ---
    tags: [Authentication]
    responses:
      200: {description: Provisioning info (secret + otpauth URI)}
      401: {description: Invalid or expired enrollment token}
      429: {description: Rate-limit exceeded for this IP}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    rl = _enforce_token_rate_limit(
        action='mfa_enroll_setup',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many MFA enrollment attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    try:
        body = MfaEnrollSetupRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors(include_context=False),
        }), 400

    resolved = _resolve_enrollment_token(body.mfa_enrollment_token)
    if isinstance(resolved, tuple) and len(resolved) == 2:
        # error tuple
        return resolved
    user, mfa_repo, _cache_key = resolved

    # Persist a fresh pending method each time so that a refresh of the QR
    # rotates state cleanly. Mirrors the JWT-protected /mfa/setup behaviour
    # (atomic replace under a per-user advisory lock).
    secret = MfaService.generate_secret()
    encrypted = MfaService.encrypt_secret(secret)
    method_id = mfa_repo.replace_pending_method(
        user['id'], encrypted_secret=encrypted,
    )
    label = MFA_DEFAULT_LABEL

    provisioning_uri = MfaService.provisioning_uri(secret, username=user['username'])

    audit_service.log_auth(
        action='mfa_enroll_setup',
        success=True,
        user_id=user['id'],
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
        metadata={'method_id': str(method_id)},
    )

    response = MfaSetupResponse(
        method_id=str(method_id),
        secret=secret,
        provisioning_uri=provisioning_uri,
        label=label,
    )
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/mfa/enroll-verify', methods=['POST'])
@handle_errors
def mfa_enroll_verify():
    """Second step of post-login MFA enrollment: verify TOTP, enable MFA, issue session tokens.

    Consumes the enrollment token (single-use). Returns full LoginResponse
    (access + refresh + user).
    ---
    tags: [Authentication]
    responses:
      200: {description: Tokens issued}
      400: {description: Wrong code or no pending setup}
      401: {description: Invalid or expired enrollment token}
      429: {description: Rate-limit exceeded for this IP}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    rl = _enforce_token_rate_limit(
        action='mfa_enroll_verify',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many MFA enrollment attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    try:
        body = MfaEnrollVerifyRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'message': 'Invalid request data',
            'details': e.errors(include_context=False),
        }), 400

    resolved = _resolve_enrollment_token(body.mfa_enrollment_token)
    if isinstance(resolved, tuple) and len(resolved) == 2:
        return resolved
    user, mfa_repo, cache_key = resolved

    pending = mfa_repo.get_pending_method(user['id'])
    if not pending or not pending.get('mfa_secret'):
        return jsonify({
            'error': 'no_pending_setup',
            'message': 'Run /mfa/enroll-setup first.',
        }), 400

    try:
        secret = MfaService.decrypt_secret(pending['mfa_secret'])
    except ValueError:
        audit_service.log_auth(
            action='mfa_enroll_verify',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='secret_decrypt_failed',
        )
        return jsonify({
            'error': 'no_pending_setup',
            'message': 'MFA setup state is invalid. Re-run /mfa/enroll-setup.',
        }), 400

    if not MfaService.verify_totp(secret, body.code):
        audit_service.log_auth(
            action='mfa_enroll_verify',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='invalid_code',
        )
        return jsonify({'error': 'invalid_code', 'message': 'TOTP code did not match.'}), 400

    # Activate MFA BEFORE issuing tokens — if the activation or token issuance
    # fails, the operator can re-run the flow.
    mfa_repo.enable_method(pending['id'])

    # Single-use: drop the enrollment token now that it's been consumed.
    cache.delete(cache_key)

    access_token, refresh_token, expires_in, _session_id = _issue_session_tokens(
        user, ip_address, user_agent, auth_method='local',
    )

    audit_service.log_auth(
        action='mfa_enabled',
        success=True,
        user_id=user['id'],
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
    )
    audit_service.log_auth(
        action='login_mfa_enrollment_completed',
        success=True,
        user_id=user['id'],
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
    )

    user_response = UserResponse(
        id=user['id'],
        username=user['username'],
        email=user['email'],
        full_name=user.get('full_name'),
        role=user['role'],
        is_active=user['is_active'],
        email_verified=user.get('email_verified', False),
        must_change_password=user.get('must_change_password', False),
        last_login_at=user.get('last_login_at'),
        created_at=user['created_at'],
        is_ldap_user=False,
        is_oidc_user=False,
    )
    response = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type='Bearer',
        expires_in=expires_in,
        user=user_response,
    )
    return jsonify(response.model_dump(mode='json')), 200


@auth_bp.route('/ldap/test', methods=['POST'])
@jwt_required()
@handle_errors
@require_admin()
@validate_request(LdapTestRequest)
def ldap_test(validated_data: LdapTestRequest):
    """Admin dry-run for LDAP settings — does NOT provision local user.
    ---
    tags: [Authentication]
    responses:
      200: {description: Diagnostic result (success or errors)}
      400: {description: Validation error}
      403: {description: Admin role required}
    """
    ldap_svc = LdapService(current_app.settings_manager)
    if not ldap_svc.is_enabled():
        return jsonify(LdapTestResponse(
            success=False,
            errors=['LDAP is not enabled in settings'],
        ).model_dump(mode='json')), 200

    result = ldap_svc.test_connection(validated_data.username, validated_data.password)
    response = LdapTestResponse(**result)
    return jsonify(response.model_dump(mode='json')), 200


# =============================================================================
# OIDC SSO Endpoints
# =============================================================================

@auth_bp.route('/oidc/test-discovery', methods=['GET'])
@jwt_required()
@handle_errors
@require_admin()
def oidc_test_discovery():
    """Admin diagnostic for OIDC discovery URL.
    ---
    tags: [Authentication]
    responses:
      200: {description: Diagnostic info or errors}
      403: {description: Admin role required}
    """
    oidc = OidcService(current_app.settings_manager)
    if not oidc.is_enabled():
        return jsonify(OidcTestDiscoveryResponse(
            success=False,
            errors=['OIDC is not enabled in settings'],
        ).model_dump(mode='json')), 200

    result = oidc.test_discovery()
    return jsonify(OidcTestDiscoveryResponse(**result).model_dump(mode='json')), 200


@auth_bp.route('/oidc/login', methods=['POST'])
@handle_errors
def oidc_login():
    """Initiate OIDC Authorization Code + PKCE flow.

    Returns the IdP authorization URL for the frontend to redirect the browser to.
    ---
    tags: [Authentication]
    responses:
      200: {description: authorization_url returned}
      503: {description: OIDC not enabled or discovery failed}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    rl = _enforce_token_rate_limit(
        action='oidc_login',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many SSO sign-in attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    oidc_svc = OidcService(current_app.settings_manager)
    if not oidc_svc.is_enabled():
        return jsonify({
            'error': 'oidc_not_enabled',
            'message': 'OIDC SSO is not configured or enabled.',
        }), 503

    try:
        authorization_url, _state = oidc_svc.build_authorization_url()
    except Exception as e:
        logger.warning("OIDC: failed to build authorization URL: %s", e)
        audit_service.log_auth(
            action='login_oidc_initiated',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='discovery_failed',
        )
        return jsonify({
            'error': 'oidc_unavailable',
            'message': 'OIDC provider is unreachable. Please try again later.',
        }), 503

    audit_service.log_auth(
        action='login_oidc_initiated',
        success=True,
        ip=ip_address,
        user_agent=user_agent,
    )
    return jsonify(OidcLoginResponse(authorization_url=authorization_url).model_dump(mode='json')), 200


@auth_bp.route('/oidc/callback', methods=['GET'])
@handle_errors
def oidc_callback():
    """OIDC callback — IdP redirects here after the user authenticates.

    Validates the authorization code + state, exchanges them for an ID token,
    provisions/matches the local user, generates a one-time exchange code, and
    redirects the browser to the configured frontend_complete_url.

    Always returns a 302 redirect — never JSON.
    ---
    tags: [Authentication]
    responses:
      302: {description: Redirect to frontend (success or error)}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    def _fail_redirect(error_code: str) -> object:
        return redirect(f'/login?error={error_code}', code=302)

    # IdP-side error (e.g. user cancelled)
    error = request.args.get('error')
    if error:
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason=f'idp_error:{error}',
        )
        return _fail_redirect('oidc_failed')

    code = request.args.get('code', '')
    state = request.args.get('state', '')
    if not code or not state:
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='exchange_failed',
        )
        return _fail_redirect('oidc_invalid_state')

    oidc_svc = OidcService(current_app.settings_manager)

    # Exchange code → OidcUserInfo (None means state expired, nonce mismatch, etc.)
    user_info = oidc_svc.exchange_code(code, state)
    if user_info is None:
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='exchange_failed',
        )
        return _fail_redirect('oidc_invalid_state')

    if not user_info.email:
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='no_email_claim',
        )
        return _fail_redirect('oidc_no_email')

    # Provision or look up local user
    user_repo = UserRepository(conn_manager)
    auto_create = current_app.settings_manager.get('oidc', 'auto_create_users', True)
    try:
        if auto_create:
            # Email-domain allowlist (default empty = all domains allowed).
            # Only consulted on the auto-create path; existing-user logins are
            # not gated by the allowlist.
            allowed_raw = current_app.settings_manager.get(
                'oidc', 'allowed_email_domains', ''
            ) or ''
            allowed_domains = {
                d.strip().lower()
                for d in allowed_raw.split(',')
                if d.strip()
            }
            if allowed_domains:
                # user_info.email is already validated as non-empty above.
                _, _, email_domain = user_info.email.rpartition('@')
                email_domain = email_domain.lower()
                if email_domain not in allowed_domains:
                    audit_service.log_auth(
                        action='login_oidc',
                        success=False,
                        username=user_info.email,
                        ip=ip_address,
                        user_agent=user_agent,
                        failure_reason='oidc_email_domain_not_allowed',
                        metadata={
                            'attempted_domain': email_domain,
                            'allowed_domains': sorted(allowed_domains),
                        },
                    )
                    return _fail_redirect('oidc_email_domain_not_allowed')

            user = user_repo.get_or_create_oidc_user(
                email=user_info.email,
                full_name=user_info.full_name,
                role=user_info.role,
                ip=ip_address,
                user_agent=user_agent,
            )
        else:
            user = user_repo.get_user_by_email(user_info.email)
            if user is None:
                audit_service.log_auth(
                    action='login_oidc',
                    success=False,
                    ip=ip_address,
                    user_agent=user_agent,
                    failure_reason='user_not_provisioned',
                )
                return _fail_redirect('oidc_user_not_provisioned')
    except ValueError:
        # Inactive account
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='account_disabled',
        )
        return _fail_redirect('account_disabled')

    if not user.get('is_active', True):
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            user_id=user['id'],
            username=user['username'],
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='account_disabled',
        )
        return _fail_redirect('account_disabled')

    # Generate one-time exchange code (32-byte URL-safe)
    exchange_code = secrets.token_urlsafe(32)
    cache.set(
        f'oidc_exchange:{exchange_code}',
        {'user_id': str(user['id'])},
        timeout=300,
    )

    audit_service.log_auth(
        action='login_oidc',
        success=True,
        user_id=user['id'],
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
    )

    complete_url = oidc_svc.frontend_complete_url()
    separator = '&' if '?' in complete_url else '?'
    return redirect(f'{complete_url}{separator}code={exchange_code}', code=302)


@auth_bp.route('/oidc/exchange', methods=['POST'])
@handle_errors
@validate_request(OidcExchangeRequest)
def oidc_exchange(validated_data: OidcExchangeRequest):
    """Exchange the one-time OIDC code for access + refresh tokens.

    The exchange code is single-use (atomically consumed). IP rate-limited.
    ---
    tags: [Authentication]
    responses:
      200: {description: LoginResponse with tokens + user}
      401: {description: Code expired or already used}
      429: {description: IP rate limit exceeded}
    """
    conn_manager = current_app.connection_manager
    audit_service = AuditService(conn_manager)
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    rl = _enforce_token_rate_limit(
        action='oidc_exchange',
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
        message='Too many SSO exchange attempts. Try again in an hour.',
    )
    if rl is not None:
        return rl

    cache_key = f'oidc_exchange:{validated_data.code}'
    entry = cache.get(cache_key)
    if not entry:
        return jsonify({
            'error': 'invalid_or_expired_code',
            'message': 'SSO exchange code is invalid or has already been used. Please sign in again.',
        }), 401

    # Atomically consume: delete then proceed (single-use guarantee)
    cache.delete(cache_key)

    user_id = entry['user_id']
    user_repo = UserRepository(conn_manager)

    user = user_repo.get_user_by_id(user_id)
    if not user or not user.get('is_active'):
        audit_service.log_auth(
            action='login_oidc',
            success=False,
            user_id=user_id,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='account_disabled',
        )
        return jsonify({
            'error': 'account_disabled',
            'message': 'This account has been disabled.',
        }), 401

    # MFA enrollment enforcement. OIDC users are external and the helper
    # short-circuits for them — IdP-side MFA is the right enforcement layer —
    # but we keep the call so the architecture stays consistent across all 3
    # login paths.
    mfa_repo = UserMfaRepository(conn_manager)
    enrollment_response = _maybe_require_mfa_enrollment(
        user,
        auth_method='oidc',
        mfa_repo=mfa_repo,
        audit_service=audit_service,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if enrollment_response is not None:
        return enrollment_response

    # OIDC exchange always means auth_method='oidc' — derive flags from that
    # directly rather than doing an extra get_user_by_username DB call.
    access_token, refresh_token, expires_in, _session_id = _issue_session_tokens(
        user, ip_address, user_agent, auth_method='oidc',
    )

    audit_service.log_auth(
        action='login_oidc',
        success=True,
        user_id=user_id,
        username=user['username'],
        ip=ip_address,
        user_agent=user_agent,
    )

    user_response = UserResponse(
        id=user['id'],
        username=user['username'],
        email=user['email'],
        full_name=user.get('full_name'),
        role=user['role'],
        is_active=user['is_active'],
        email_verified=user.get('email_verified', False),
        must_change_password=user.get('must_change_password', False),
        last_login_at=user.get('last_login_at'),
        created_at=user['created_at'],
        is_ldap_user=False,
        is_oidc_user=True,
    )
    response = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type='Bearer',
        expires_in=expires_in,
        user=user_response,
    )
    return jsonify(response.model_dump(mode='json')), 200


# =============================================================================
# SSO Providers — public, no JWT
# =============================================================================

@auth_bp.route('/sso-providers', methods=['GET'])
@handle_errors
def get_sso_providers():
    """Public endpoint that exposes which SSO providers are enabled, for the LoginPage UI.
    ---
    tags:
      - Authentication
    responses:
      200:
        description: Map of provider → enabled flag + display label
    """
    sm = current_app.settings_manager
    svc = OidcService(sm)
    return jsonify({
        'oidc': {
            'enabled': svc.is_enabled(),
            'provider_name': svc.provider_name(),
            'provider_kind': svc.provider_kind(),
        },
    }), 200
