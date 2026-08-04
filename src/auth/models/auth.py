# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Authentication Request and Response Pydantic Models

Models for authentication endpoints including login, registration,
password management, and user profile operations.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, validator, ConfigDict
from enum import Enum


# =============================================================================
# Enums
# =============================================================================

class UserRole(str, Enum):
    """User role types"""
    user = "user"
    admin = "admin"
    super_admin = "super_admin"


# =============================================================================
# Authentication Request Models
# =============================================================================

class LoginRequest(BaseModel):
    """Request model for user login"""
    username: str = Field(
        min_length=1,
        max_length=255,
        description="Username or email address"
    )
    password: str = Field(
        min_length=1,
        max_length=128,
        description="User password"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "username": "john.doe",
                "password": "SecurePassword123!"
            }
        }


class RegisterRequest(BaseModel):
    """Request model for user registration"""
    username: str = Field(
        min_length=3,
        max_length=255,
        pattern=r'^[a-zA-Z0-9._-]+$',
        description="Username (alphanumeric, dots, dashes, underscores)"
    )
    email: EmailStr = Field(description="Email address")
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Password (min 8 chars, must meet strength requirements)"
    )
    full_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Full name"
    )

    @validator('username')
    def validate_username(cls, v):
        """Validate username doesn't start or end with special chars"""
        if v.startswith(('.', '-', '_')) or v.endswith(('.', '-', '_')):
            raise ValueError("Username cannot start or end with special characters")
        return v.lower()  # Normalize to lowercase

    class Config:
        json_schema_extra = {
            "example": {
                "username": "john.doe",
                "email": "john.doe@example.com",
                "password": "SecurePassword123!",
                "full_name": "John Doe"
            }
        }


class ChangePasswordRequest(BaseModel):
    """Request model for changing password"""
    current_password: str = Field(
        min_length=1,
        max_length=128,
        description="Current password"
    )
    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="New password (min 8 chars, must meet strength requirements)"
    )

    @validator('new_password')
    def validate_passwords_different(cls, v, values):
        """Ensure new password is different from current"""
        if 'current_password' in values and v == values['current_password']:
            raise ValueError("New password must be different from current password")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "current_password": "OldPassword123!",
                "new_password": "NewPassword456!"
            }
        }


class ForgotPasswordRequest(BaseModel):
    """Request model for password reset request"""
    email: EmailStr = Field(description="Email address")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "john.doe@example.com"
            }
        }


class ResetPasswordRequest(BaseModel):
    """Request model for password reset with token"""
    token: str = Field(
        min_length=1,
        description="Password reset token from email"
    )
    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="New password"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "token": "abc123...",
                "new_password": "NewPassword123!"
            }
        }


# =============================================================================
# User Management Request Models
# =============================================================================

class UpdateUserRequest(BaseModel):
    """Request model for updating user profile"""
    email: Optional[EmailStr] = Field(default=None, description="New email address")
    full_name: Optional[str] = Field(default=None, max_length=255, description="New full name")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "newemail@example.com",
                "full_name": "John A. Doe"
            }
        }


class UpdateUserAdminRequest(BaseModel):
    """Request model for admin updating any user.

    `is_active` is intentionally NOT settable here — the underlying
    UserRepository.update_user does not accept it, and silently dropping
    the field would let callers think disable/enable worked when it didn't.
    Use the dedicated PATCH /users/<id>/disable and /enable endpoints
    instead, which also emit user_disabled / user_enabled audit rows.

    Sending `is_active` in the request body is rejected as a validation
    error pointing the caller at the right endpoint.
    """
    email: Optional[EmailStr] = Field(default=None, description="New email address")
    full_name: Optional[str] = Field(default=None, max_length=255, description="New full name")
    role: Optional[UserRole] = Field(default=None, description="New role")

    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "full_name": "John Doe",
                "role": "admin",
            }
        },
    )


class CreateUserAdminRequest(BaseModel):
    """Request model for admin creating a user.

    For an invite-based create (admin sends an invite link instead of setting
    the password), pass `invite_only=True` and omit `password`. The created
    user starts disabled-for-login (random unusable password_hash) until they
    consume the invite link.
    """
    username: str = Field(
        min_length=3,
        max_length=255,
        pattern=r'^[a-zA-Z0-9._-]+$',
        description="Username"
    )
    email: EmailStr = Field(description="Email address")
    invite_only: bool = Field(
        default=False,
        description="When true, no password is set; user activates via emailed invite link."
    )
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Initial password (omit when invite_only=True)"
    )
    full_name: Optional[str] = Field(default=None, max_length=255)
    role: UserRole = Field(default=UserRole.user, description="User role")
    must_change_password: bool = Field(
        default=True,
        description="Require password change on first login (ignored when invite_only=True)"
    )

    @validator('username')
    def validate_username(cls, v):
        if v.startswith(('.', '-', '_')) or v.endswith(('.', '-', '_')):
            raise ValueError("Username cannot start or end with special characters")
        return v.lower()

    @validator('password', always=True)
    def password_required_unless_invite(cls, v, values):
        # Field-order matters: `invite_only` must be declared BEFORE `password`
        # in this class so that values['invite_only'] is populated when this
        # validator runs. Pydantic v1 validators see only previously-validated
        # fields via `values`.
        invite_only = values.get('invite_only', False)
        has_password = v is not None and v != ''
        if invite_only and has_password:
            raise ValueError("password must not be set when invite_only is true")
        if not invite_only and not has_password:
            raise ValueError("password is required when invite_only is false")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "username": "new.user",
                "email": "new.user@example.com",
                "invite_only": True,
                "full_name": "New User",
                "role": "user"
            }
        }


class UserListParams(BaseModel):
    """Query parameters for listing users"""
    page: int = Field(default=1, ge=1, description="Page number")
    limit: int = Field(default=50, ge=1, le=100, description="Items per page")
    role: Optional[UserRole] = Field(default=None, description="Filter by role")
    is_active: Optional[bool] = Field(default=None, description="Filter by active status")

    class Config:
        json_schema_extra = {
            "example": {
                "page": 1,
                "limit": 50,
                "role": "user",
                "is_active": True
            }
        }


# =============================================================================
# Response Models
# =============================================================================

class UserResponse(BaseModel):
    """Response model for user information (excludes password)"""
    id: str = Field(description="User UUID")
    username: str = Field(description="Username")
    email: str = Field(description="Email address")
    full_name: Optional[str] = Field(default=None, description="Full name")
    role: UserRole = Field(description="User role")
    is_active: bool = Field(description="Account active status")
    email_verified: bool = Field(description="Email verification status")
    must_change_password: bool = Field(description="Must change password on next login")
    last_login_at: Optional[datetime] = Field(default=None, description="Last login timestamp")
    created_at: datetime = Field(description="Account creation timestamp")
    is_ldap_user: bool = Field(default=False, description="True when the user authenticates via LDAP only")
    is_oidc_user: bool = Field(default=False, description="True when the user authenticates via OIDC SSO only")
    onboarding_pending: bool = Field(default=False, description="True when the user was created invite-only and has not yet completed onboarding (password_hash carries the INVITE: sentinel)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "username": "john.doe",
                "email": "john.doe@example.com",
                "full_name": "John Doe",
                "role": "user",
                "is_active": True,
                "email_verified": True,
                "must_change_password": False,
                "last_login_at": "2025-11-12T20:00:00Z",
                "created_at": "2025-11-01T10:00:00Z"
            }
        }


class LoginResponse(BaseModel):
    """Response model for successful login"""
    access_token: str = Field(description="JWT access token (15 min expiration)")
    refresh_token: str = Field(description="JWT refresh token (7 day expiration)")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(description="Access token expiration in seconds")
    user: UserResponse = Field(description="Authenticated user information")

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIs...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                "token_type": "Bearer",
                "expires_in": 900,
                "user": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "username": "john.doe",
                    "email": "john.doe@example.com",
                    "full_name": "John Doe",
                    "role": "user",
                    "is_active": True,
                    "email_verified": True,
                    "must_change_password": False,
                    "last_login_at": "2025-11-12T20:00:00Z",
                    "created_at": "2025-11-01T10:00:00Z"
                }
            }
        }


class LoginMfaRequiredResponse(BaseModel):
    """Returned by /api/auth/login when the user has MFA enabled.

    The caller must POST {mfa_challenge_token, code} to /api/auth/mfa/challenge
    to exchange for the actual access/refresh tokens.
    """
    requires_mfa: bool = Field(default=True)
    mfa_challenge_token: str = Field(description="Opaque 32-byte URL-safe token, valid 5 min")
    expires_in: int = Field(description="Seconds until the challenge token expires")


class MfaChallengeRequest(BaseModel):
    """Request body for POST /api/auth/mfa/challenge"""
    mfa_challenge_token: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=6, description="TOTP code")


class MfaStatusResponse(BaseModel):
    """Response model for GET /api/auth/mfa/status"""
    enabled: bool = Field(description="Whether MFA is currently enabled for the user")


class MfaSetupRequest(BaseModel):
    """Optional body for POST /api/auth/mfa/setup. Empty body is accepted.
    ``label`` lets the user name the new method (defaults to "Authenticator app").
    """
    label: Optional[str] = Field(None, max_length=64)


class MfaSetupResponse(BaseModel):
    """Response model for POST /api/auth/mfa/setup"""
    method_id: str = Field(description="UUID of the newly-created pending method row")
    secret: str = Field(description="Base32-encoded TOTP secret (display once for manual entry)")
    provisioning_uri: str = Field(description="otpauth:// URI for QR code rendering")
    label: str = Field(description="Display label assigned to the new method")


class MfaVerifyRequest(BaseModel):
    """Request body for POST /api/auth/mfa/verify"""
    code: str = Field(min_length=6, max_length=8, description="6-digit TOTP code")


class MfaVerifyResponse(BaseModel):
    """Response for POST /api/auth/mfa/verify"""
    enabled: bool = Field(description="True after successful verification")


class MfaDisableRequest(BaseModel):
    """Request body for POST /api/auth/mfa/disable"""
    password: str = Field(min_length=1, description="Current account password (re-auth)")
    code: str = Field(min_length=6, max_length=6, description="Current TOTP code")


class LoginMfaEnrollmentRequiredResponse(BaseModel):
    """Returned by /api/auth/login when an admin must enroll in MFA before tokens are issued.

    The caller must POST /api/auth/mfa/enroll-setup (gets QR/secret) then
    /api/auth/mfa/enroll-verify (gets full session tokens) using the enrollment
    token below. The token is single-purpose: it does NOT grant any other access.
    """
    requires_mfa_enrollment: bool = Field(default=True)
    mfa_enrollment_token: str = Field(description="Opaque 32-byte URL-safe token, valid 5 min")
    expires_in: int = Field(description="Seconds until the enrollment token expires")


class MfaEnrollSetupRequest(BaseModel):
    """Request body for POST /api/auth/mfa/enroll-setup (no JWT — uses enrollment token)."""
    mfa_enrollment_token: str = Field(min_length=1)


class MfaEnrollVerifyRequest(BaseModel):
    """Request body for POST /api/auth/mfa/enroll-verify (no JWT — uses enrollment token)."""
    mfa_enrollment_token: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=8, description="6-digit TOTP code")


class RefreshTokenResponse(BaseModel):
    """Response model for token refresh"""
    access_token: str = Field(description="New JWT access token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(description="Access token expiration in seconds")

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIs...",
                "token_type": "Bearer",
                "expires_in": 900
            }
        }


class SessionResponse(BaseModel):
    """Response model for user session information"""
    id: str = Field(description="Session UUID")
    user_id: str = Field(description="User UUID")
    ip_address: Optional[str] = Field(default=None, description="Client IP address")
    user_agent: Optional[str] = Field(default=None, description="Client user agent")
    is_active: bool = Field(description="Session active status")
    created_at: datetime = Field(description="Session creation timestamp")
    last_used_at: datetime = Field(description="Last activity timestamp")
    expires_at: datetime = Field(description="Session expiration timestamp")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "456e4567-e89b-12d3-a456-426614174001",
                "user_id": "123e4567-e89b-12d3-a456-426614174000",
                "ip_address": "192.168.1.100",
                "user_agent": "Mozilla/5.0...",
                "is_active": True,
                "created_at": "2025-11-12T20:00:00Z",
                "last_used_at": "2025-11-12T21:30:00Z",
                "expires_at": "2025-11-19T20:00:00Z"
            }
        }


class UserListResponse(BaseModel):
    """Response model for paginated user list"""
    users: List[UserResponse] = Field(description="List of users")
    total: int = Field(description="Total number of users")
    page: int = Field(description="Current page number")
    limit: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")

    class Config:
        json_schema_extra = {
            "example": {
                "users": [
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "username": "john.doe",
                        "email": "john.doe@example.com",
                        "full_name": "John Doe",
                        "role": "user",
                        "is_active": True,
                        "email_verified": True,
                        "must_change_password": False,
                        "last_login_at": "2025-11-12T20:00:00Z",
                        "created_at": "2025-11-01T10:00:00Z"
                    }
                ],
                "total": 100,
                "page": 1,
                "limit": 50,
                "pages": 2
            }
        }


class CreateUserAdminResponse(BaseModel):
    """Response from admin user-create. Includes invite_url when invite_only=true."""
    user: UserResponse = Field(description="The newly created user")
    invite_url: Optional[str] = Field(
        default=None,
        description="Onboarding link to share with the user (only when invite_only=true)"
    )
    invite_expires_at: Optional[datetime] = Field(
        default=None,
        description="Invite link expiration timestamp (only when invite_only=true)"
    )
    email_sent: bool = Field(
        default=False,
        description="Whether the welcome/invite email was actually sent"
    )


class OnboardingValidateResponse(BaseModel):
    """Response from GET /api/auth/onboarding/validate.

    Returns valid=False without leaking whether the token format is wrong vs
    expired vs already used — the wizard shows a generic error in any case.
    """
    valid: bool = Field(description="Whether the invite token is valid and usable")
    username: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    requires_mfa: bool = Field(
        default=False,
        description="Whether the user must enroll MFA before first login (always true for local users; false for LDAP/OIDC)"
    )


class OnboardingCompleteRequest(BaseModel):
    """Request body for POST /api/auth/onboarding/complete."""
    token: str = Field(
        min_length=64,
        max_length=64,
        description="Invite token from the email/copy link"
    )
    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="The password the user wants to use"
    )


class OnboardingCompleteResponse(BaseModel):
    """Response from POST /api/auth/onboarding/complete."""
    success: bool
    username: str
    message: str = Field(default="Account activated. You can now sign in.")


class ResendInviteResponse(BaseModel):
    """Response from POST /api/auth/users/<id>/resend-invite."""
    success: bool = True
    invite_url: str = Field(description="The new onboarding link the admin can share")
    invite_expires_at: datetime
    email_sent: bool


class PasswordStrengthResponse(BaseModel):
    """Response model for password strength check"""
    valid: bool = Field(description="Whether password meets requirements")
    score: int = Field(ge=0, le=5, description="Password strength score (0-5)")
    strength: str = Field(description="Strength label (very weak, weak, fair, good, strong)")
    errors: List[str] = Field(default_factory=list, description="Validation errors")

    class Config:
        json_schema_extra = {
            "example": {
                "valid": True,
                "score": 5,
                "strength": "strong",
                "errors": []
            }
        }


# =============================================================================
# Password Reset Models
# =============================================================================

class PasswordResetRequestModel(BaseModel):
    """Request model for password reset."""

    email: EmailStr = Field(
        ...,
        description="Email address of the account"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com"
            }
        }
    )


class PasswordResetRequestResponse(BaseModel):
    """Response model for password reset request."""

    success: bool = Field(..., description="Whether request was processed")
    message: str = Field(..., description="User-friendly message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "If an account exists with that email, a password reset link has been sent."
            }
        }
    )


class PasswordResetVerifyModel(BaseModel):
    """Request model for password reset verification/completion."""

    token: str = Field(
        ...,
        description="Password reset token from email",
        min_length=64,
        max_length=64
    )
    new_password: str = Field(
        ...,
        description="New password",
        min_length=8,
        max_length=128
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "a" * 64,  # 64-character hex token
                "new_password": "NewSecurePassword123!"
            }
        }
    )


class PasswordResetVerifyResponse(BaseModel):
    """Response model for successful password reset."""

    success: bool = Field(..., description="Whether reset was successful")
    message: str = Field(..., description="Success message")
    username: Optional[str] = Field(None, description="Username of reset account")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Password has been reset successfully. Please login with your new password.",
                "username": "admin"
            }
        }
    )


# =============================================================================
# LDAP Diagnostic Models
# =============================================================================

class LdapTestRequest(BaseModel):
    """Request body for POST /api/auth/ldap/test"""
    username: str = Field(min_length=1, description="LDAP username/email/UPN to test")
    password: str = Field(min_length=1, description="Password for the LDAP test user")


class LdapTestResponse(BaseModel):
    """Response body for POST /api/auth/ldap/test"""
    success: bool = Field(description="True if the dry-run succeeded end-to-end")
    user_dn: Optional[str] = Field(default=None, description="DN of the matched user (if found)")
    mapped_role: Optional[str] = Field(default=None, description="Resolved AssetAtlas role (if matched)")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Selected user attributes")
    errors: List[str] = Field(default_factory=list, description="Diagnostic error messages")


# =============================================================================
# OIDC Models
# =============================================================================

class OidcTestDiscoveryResponse(BaseModel):
    """Response for GET /api/auth/oidc/test-discovery"""
    success: bool = Field(description="True if discovery succeeded")
    issuer: Optional[str] = Field(default=None, description="Issuer URL from OIDC metadata")
    authorization_endpoint: Optional[str] = Field(default=None)
    token_endpoint: Optional[str] = Field(default=None)
    jwks_uri: Optional[str] = Field(default=None)
    errors: List[str] = Field(default_factory=list)


class OidcLoginResponse(BaseModel):
    """Response body for POST /api/auth/oidc/login"""
    authorization_url: str = Field(description="IdP authorization URL — redirect the browser here")


class OidcExchangeRequest(BaseModel):
    """Request body for POST /api/auth/oidc/exchange"""
    code: str = Field(min_length=1, description="One-time exchange code from the OIDC callback redirect")


# ===== Account self-service (#612) ==========================================

class AccountMfaMethod(BaseModel):
    id: str  # UUID of the user_mfa row
    type: str  # 'totp' for now
    label: str  # user-supplied display label
    enabled: bool  # True for verified methods; False for pending enrollments
    created_at: Optional[datetime] = None


class AccountMfaListResponse(BaseModel):
    methods: List[AccountMfaMethod] = Field(default_factory=list)


class AccountMfaUpdateRequest(BaseModel):
    """Body for PATCH /api/account/mfa/<id>. Used to rename a method."""
    label: str = Field(min_length=1, max_length=64)


class AccountProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, max_length=200)


# Self-service password change reuses ChangePasswordRequest defined above
# (same field bounds + the new-vs-current validator).


# ===== Forgot-password (#613) ===============================================

class ForgotPasswordLookupRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)


class ForgotPasswordLookupResponse(BaseModel):
    # paths is a subset of ['email', 'totp']
    paths: List[str] = Field(default_factory=list)


class ForgotPasswordTotpRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    code: str = Field(..., min_length=6, max_length=8)
    new_password: str = Field(..., min_length=8, max_length=128)
