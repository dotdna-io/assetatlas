-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration 025: Create Initial Super Admin User
-- ============================================================================
-- Description: Creates the first super admin user for AssetAtlas backend API.
--              This user can then create additional admin/user accounts.
-- Created: 2025-11-12
-- Author: AssetAtlas - Phase 5 Implementation
-- ============================================================================

-- ============================================================================
-- IMPORTANT: Security Notice
-- ============================================================================
-- This migration creates a super admin account in a LOCKED state.
--
--   - password_hash is a deliberately invalid bcrypt sentinel — no password
--     can authenticate against it.
--   - is_active = false — login is rejected even if the hash is replaced
--     before the account is activated.
--
-- To activate the account, either:
--   * Run the installer (deploy/installer/install.sh), which sets a random
--     password and activates the user, OR
--   * Run scripts/reset_admin_password.py <password>, which sets the
--     supplied password and activates the user.
-- ============================================================================

-- ============================================================================
-- SECTION 1: Create Super Admin User
-- ============================================================================

INSERT INTO users (
    username,
    email,
    password_hash,
    full_name,
    role,
    is_active,
    email_verified,
    must_change_password,
    created_at
)
VALUES (
    'admin',
    'admin@assetatlas.local',
    -- Deliberately invalid bcrypt sentinel — no password authenticates against
    -- this. Installer or scripts/reset_admin_password.py replaces it.
    '$2b$12$LOCKED.RUN.INSTALLER.OR.RESET.SCRIPT.TO.SET.A.REAL.PASSWORD',
    'System Administrator',
    'super_admin',
    false,  -- Locked until installer / reset script activates the account
    true,
    true,   -- Force password change on first login
    NOW()
)
ON CONFLICT (username) DO NOTHING;  -- Don't fail if admin already exists

-- ============================================================================
-- SECTION 2: Log Creation in Audit Log
-- ============================================================================

INSERT INTO auth_audit_log (
    user_id,
    username,
    action,
    success,
    ip_address,
    metadata,
    created_at
)
SELECT
    id,
    'admin',
    'user_created',
    true,
    '127.0.0.1'::inet,
    jsonb_build_object(
        'role', 'super_admin',
        'created_by', 'migration_025',
        'must_change_password', true
    ),
    NOW()
FROM users
WHERE username = 'admin';


-- ============================================================================
-- SECTION 3: Verification
-- ============================================================================

DO $$
DECLARE
    admin_exists BOOLEAN;
    admin_role VARCHAR;
BEGIN
    -- Check if admin user exists
    SELECT EXISTS(SELECT 1 FROM users WHERE username = 'admin') INTO admin_exists;

    IF NOT admin_exists THEN
        RAISE EXCEPTION 'FAILED: Super admin user was not created';
    END IF;

    -- Verify admin has correct role
    SELECT role INTO admin_role FROM users WHERE username = 'admin';

    IF admin_role != 'super_admin' THEN
        RAISE EXCEPTION 'FAILED: Admin user does not have super_admin role';
    END IF;

    RAISE NOTICE '✅ SUCCESS: Super admin user created (LOCKED)';
    RAISE NOTICE '   Username: admin';
    RAISE NOTICE '   Email: admin@assetatlas.local';
    RAISE NOTICE '   Role: super_admin';
    RAISE NOTICE '';
    RAISE NOTICE '⚠️  Account is INACTIVE and has no usable password.';
    RAISE NOTICE '⚠️  Activate it via the installer (deploy/installer/install.sh)';
    RAISE NOTICE '⚠️  or by running: scripts/reset_admin_password.py <password>';
END $$;

-- Show created user details (excluding password hash)
SELECT
    id,
    username,
    email,
    full_name,
    role,
    is_active,
    email_verified,
    must_change_password,
    created_at
FROM users
WHERE username = 'admin';
