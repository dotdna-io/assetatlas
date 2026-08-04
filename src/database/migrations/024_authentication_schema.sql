-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration 024: Authentication & Security Infrastructure
-- ============================================================================
-- Description: Creates comprehensive authentication system with JWT support,
--              user management, session tracking, audit logging, and MFA support.
-- Created: 2025-11-12
-- Author: AssetAtlas - Phase 5 Implementation
-- ============================================================================

-- ============================================================================
-- SECTION 1: Users Table
-- ============================================================================
-- Core user accounts with password hashing, roles, and account lockout
-- ============================================================================

CREATE TABLE users (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Authentication credentials
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,

    -- User information
    full_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user' NOT NULL,

    -- Account status
    is_active BOOLEAN DEFAULT true NOT NULL,
    email_verified BOOLEAN DEFAULT false NOT NULL,
    must_change_password BOOLEAN DEFAULT false NOT NULL,

    -- Account lockout mechanism
    failed_login_attempts INTEGER DEFAULT 0 NOT NULL,
    locked_until TIMESTAMP WITH TIME ZONE,

    -- Activity tracking
    last_login_at TIMESTAMP WITH TIME ZONE,

    -- Audit trail
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    created_by UUID REFERENCES users(id)
);

-- Indexes for user table
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_is_active ON users(is_active);
CREATE INDEX idx_users_locked_until ON users(locked_until) WHERE locked_until IS NOT NULL;

-- Add constraint for role values
ALTER TABLE users
    ADD CONSTRAINT chk_user_role
    CHECK (role IN ('user', 'admin', 'super_admin'));

-- Comments for documentation
COMMENT ON TABLE users IS
'User accounts for AssetAtlas backend API. Supports JWT authentication, role-based access control,
and account lockout for security.';

COMMENT ON COLUMN users.password_hash IS
'Bcrypt hashed password (12 rounds). Never store plaintext passwords.';

COMMENT ON COLUMN users.role IS
'User role for RBAC: user (standard access), admin (management functions), super_admin (full access).';

COMMENT ON COLUMN users.failed_login_attempts IS
'Counter for failed login attempts. Resets to 0 on successful login. Account locks after 5 failed attempts.';

COMMENT ON COLUMN users.locked_until IS
'Timestamp until which the account is locked. NULL means account is not locked. Locks for 30 minutes after 5 failed attempts.';

COMMENT ON COLUMN users.must_change_password IS
'Flag requiring password change on next login. Used when admin creates user or after password reset.';


-- ============================================================================
-- SECTION 2: User Sessions Table
-- ============================================================================
-- Tracks active user sessions with refresh tokens for JWT authentication
-- ============================================================================

CREATE TABLE user_sessions (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User reference
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Token management
    refresh_token_hash VARCHAR(255) NOT NULL UNIQUE,
    access_token_jti VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Session tracking
    ip_address INET,
    user_agent TEXT,
    is_active BOOLEAN DEFAULT true NOT NULL,

    -- Activity tracking
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Indexes for session table
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_expires_at ON user_sessions(expires_at);
CREATE INDEX idx_user_sessions_is_active ON user_sessions(is_active);
CREATE INDEX idx_user_sessions_jti ON user_sessions(access_token_jti);
CREATE INDEX idx_user_sessions_refresh_token_hash ON user_sessions(refresh_token_hash);

-- Comments for documentation
COMMENT ON TABLE user_sessions IS
'Active user sessions tracking for JWT authentication. Each session has a refresh token that can be used
to obtain new access tokens. Sessions expire after 7 days or can be manually revoked.';

COMMENT ON COLUMN user_sessions.refresh_token_hash IS
'SHA-256 hash of the refresh token. Never store raw tokens in the database.';

COMMENT ON COLUMN user_sessions.access_token_jti IS
'JWT ID (jti) of the current access token. Used for immediate revocation via Redis blacklist.';

COMMENT ON COLUMN user_sessions.is_active IS
'Session status. Set to false when user logs out or admin revokes session.';


-- ============================================================================
-- SECTION 3: Password Reset Tokens Table
-- ============================================================================
-- Manages password reset flow with secure, time-limited tokens
-- ============================================================================

CREATE TABLE password_reset_tokens (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User reference
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Token management
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Status tracking
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for password reset tokens
CREATE INDEX idx_password_reset_tokens_user_id ON password_reset_tokens(user_id);
CREATE INDEX idx_password_reset_tokens_expires_at ON password_reset_tokens(expires_at);
CREATE INDEX idx_password_reset_tokens_token_hash ON password_reset_tokens(token_hash);

-- Comments for documentation
COMMENT ON TABLE password_reset_tokens IS
'One-time-use tokens for password reset flow. Tokens expire after 1 hour and can only be used once.';

COMMENT ON COLUMN password_reset_tokens.token_hash IS
'SHA-256 hash of the reset token sent via email. Never store raw tokens in the database.';

COMMENT ON COLUMN password_reset_tokens.used_at IS
'Timestamp when token was used. NULL means token has not been used yet.';


-- ============================================================================
-- SECTION 4: Authentication Audit Log Table
-- ============================================================================
-- Comprehensive logging of all authentication events for security monitoring
-- ============================================================================

CREATE TABLE auth_audit_log (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User reference (nullable for failed attempts on non-existent users)
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    username VARCHAR(255),

    -- Event details
    action VARCHAR(100) NOT NULL,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255),

    -- Request context
    ip_address INET,
    user_agent TEXT,

    -- Additional context
    metadata JSONB,

    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Indexes for audit log
CREATE INDEX idx_auth_audit_log_user_id ON auth_audit_log(user_id);
CREATE INDEX idx_auth_audit_log_action ON auth_audit_log(action);
CREATE INDEX idx_auth_audit_log_created_at ON auth_audit_log(created_at DESC);
CREATE INDEX idx_auth_audit_log_success ON auth_audit_log(success);
CREATE INDEX idx_auth_audit_log_username ON auth_audit_log(username);

-- Comments for documentation
COMMENT ON TABLE auth_audit_log IS
'Security audit log for all authentication events. Records both successful and failed authentication attempts,
including login, logout, token refresh, password reset, and account lockout events.';

COMMENT ON COLUMN auth_audit_log.action IS
'Authentication action: login_success, login_failed, logout, token_refreshed, password_reset_requested,
password_reset_completed, account_locked, user_registered, etc.';

COMMENT ON COLUMN auth_audit_log.failure_reason IS
'Reason for failure: invalid_password, user_not_found, account_locked, account_disabled, session_expired, etc.';

COMMENT ON COLUMN auth_audit_log.metadata IS
'Additional context for the event in JSON format (e.g., session_id, failed_attempts, etc.).';


-- ============================================================================
-- SECTION 5: Multi-Factor Authentication (MFA) Table
-- ============================================================================
-- Future support for TOTP-based two-factor authentication
-- ============================================================================

CREATE TABLE user_mfa (
    -- Primary key (one MFA config per user)
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    -- MFA status
    mfa_enabled BOOLEAN DEFAULT false NOT NULL,

    -- TOTP configuration
    mfa_secret TEXT,
    backup_codes TEXT[],

    -- Audit trail
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Comments for documentation
COMMENT ON TABLE user_mfa IS
'Multi-factor authentication configuration for users. Currently supports TOTP (Time-based One-Time Password)
via apps like Google Authenticator or Authy. Implementation planned for Phase 5 post-launch.';

COMMENT ON COLUMN user_mfa.mfa_secret IS
'Base32-encoded TOTP secret key. Encrypted at rest. Used to generate 6-digit verification codes.';

COMMENT ON COLUMN user_mfa.backup_codes IS
'Array of one-time backup codes for account recovery. Each code can only be used once. Hashed before storage.';


-- ============================================================================
-- SECTION 6: Trigger for updated_at Timestamp
-- ============================================================================
-- Automatically update updated_at column on row modification
-- ============================================================================

-- Create trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to users table
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to user_mfa table
CREATE TRIGGER update_user_mfa_updated_at BEFORE UPDATE ON user_mfa
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================================================
-- SECTION 7: Verification Queries
-- ============================================================================
-- Use these to verify the migration was successful
-- ============================================================================

-- Verify all tables were created
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') AND
       EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_sessions') AND
       EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'password_reset_tokens') AND
       EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'auth_audit_log') AND
       EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_mfa') THEN
        RAISE NOTICE 'SUCCESS: All authentication tables created';
    ELSE
        RAISE EXCEPTION 'FAILED: One or more authentication tables not found';
    END IF;
END $$;

-- Show table structures
SELECT
    'users' as table_name,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;

SELECT
    'user_sessions' as table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'user_sessions'
ORDER BY ordinal_position;

-- Verify indexes were created
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename IN ('users', 'user_sessions', 'password_reset_tokens', 'auth_audit_log', 'user_mfa')
ORDER BY tablename, indexname;

-- Verify triggers were created
SELECT
    trigger_name,
    event_object_table,
    action_timing,
    event_manipulation
FROM information_schema.triggers
WHERE event_object_table IN ('users', 'user_mfa')
ORDER BY event_object_table, trigger_name;
