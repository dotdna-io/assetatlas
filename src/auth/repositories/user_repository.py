# SPDX-License-Identifier: AGPL-3.0-or-later
"""
User database repository.

Handles all user-related database operations including CRUD,
account lockout, and authentication tracking.
"""
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from uuid import UUID
import logging
import secrets

from psycopg2 import extras, IntegrityError

logger = logging.getLogger(__name__)


class UserRepository:
    """Handles all user database operations."""

    UNUSABLE_HASH_PREFIX_LDAP = "LDAP:"
    UNUSABLE_HASH_PREFIX_OIDC = "OIDC:"
    # Sentinel written by InviteService for invite-only accounts that have
    # not yet completed onboarding. Mirrors InviteService.UNUSABLE_HASH_PREFIX
    # — duplicated here so the SELECT queries don't drag in a service import.
    UNUSABLE_HASH_PREFIX_INVITE = "INVITE:"

    def __init__(self, conn_manager):
        """
        Initialize user repository.

        Args:
            conn_manager: PostgreSQL connection manager instance
        """
        self.conn_manager = conn_manager

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        full_name: Optional[str] = None,
        role: str = 'user',
        created_by: Optional[UUID] = None
    ) -> Dict:
        """
        Create new user.

        Args:
            username: Unique username
            email: Unique email
            password_hash: Bcrypt hashed password
            full_name: Optional full name
            role: user, admin, or super_admin (default: user)
            created_by: UUID of user who created this user (for admin creation)

        Returns:
            User dictionary with created user details

        Raises:
            ValueError: If username or email already exists
            Exception: For other database errors
        """
        query = """
            INSERT INTO users (username, email, password_hash, full_name, role, created_by)
            VALUES (%(username)s, %(email)s, %(password_hash)s, %(full_name)s, %(role)s, %(created_by)s)
            RETURNING id, username, email, full_name, role, is_active, created_at
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {
                    'username': username,
                    'email': email,
                    'password_hash': password_hash,
                    'full_name': full_name,
                    'role': role,
                    'created_by': str(created_by) if created_by else None
                })

                result = dict(cursor.fetchone())
                conn.commit()

                logger.info(f"User created: {username} (ID: {result['id']})")
                return result

        except Exception as e:
            error_msg = str(e).lower()
            if 'unique constraint' in error_msg or 'duplicate key' in error_msg:
                if 'username' in error_msg:
                    raise ValueError(f'Username "{username}" already exists')
                elif 'email' in error_msg:
                    raise ValueError(f'Email "{email}" already exists')
            logger.error(f"Error creating user {username}: {e}")
            raise

    def get_or_create_ldap_user(
        self,
        username: str,
        email: str,
        full_name: str,
        role: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Look up an existing user by username, then by email. If neither matches,
        provision a new user with an unusable password hash (the user can only
        authenticate via LDAP from then on; local password reset is meaningless
        for them and is blocked at the API layer).

        Args:
            username: The username the user typed (also used as the local username
                      on first provisioning).
            email: Email from LDAP mail attribute.
            full_name: Full name from LDAP displayName attribute.
            role: Role mapped from LDAP groups (user|admin|super_admin).
            ip: Client IP of the login request, recorded on the user_created
                audit event when a new user is provisioned.
            user_agent: Client user agent of the login request, recorded on the
                user_created audit event when a new user is provisioned.

        Returns:
            Existing or newly-created user dict.

        Raises:
            ValueError: If a matching user exists but is inactive (admin must
                        re-activate before the user can sign in).
        """
        # Try by username first
        existing = self.get_user_by_username(username)
        if not existing:
            existing = self.get_user_by_email(email)

        if existing:
            if not existing.get('is_active', False):
                raise ValueError(
                    f"User {username!r} matches an existing inactive account; "
                    "an admin must re-activate the account before LDAP login can succeed."
                )
            return existing

        # No match — provision a new user
        unusable_hash = self.UNUSABLE_HASH_PREFIX_LDAP + secrets.token_urlsafe(48)

        insert_query = """
            INSERT INTO users (username, email, password_hash, full_name, role, is_active, email_verified)
            VALUES (%(username)s, %(email)s, %(password_hash)s, %(full_name)s, %(role)s, true, true)
            RETURNING id
        """
        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(insert_query, {
                    'username': username,
                    'email': email,
                    'password_hash': unusable_hash,
                    'full_name': full_name,
                    'role': role,
                })
                new_id = cursor.fetchone()['id']
                conn.commit()
                logger.info("LDAP-provisioned user %s (%s) with role %s", username, new_id, role)
                self._emit_provisioning_audit(
                    new_id, username, source='ldap', role=role,
                    ip=ip, user_agent=user_agent,
                )
                return self.get_user_by_id(new_id)
        except IntegrityError:
            # Concurrent first-login for the same user — another request won the
            # race and inserted the row between our get_user_by_username/email
            # check and this INSERT. Re-fetch and return that row instead of 500ing.
            logger.info("LDAP get_or_create race for %r — refetching", username)
            existing = self.get_user_by_username(username) or self.get_user_by_email(email)
            if existing:
                return existing
            raise

    def get_or_create_oidc_user(
        self,
        email: str,
        full_name: str,
        role: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Look up an existing user by email; if no match, provision a new user with
        username derived from the local part of the email (the IdP doesn't always
        give us a sensible username).

        Args:
            email: From the OIDC ID token's email claim.
            full_name: From the name claim.
            role: Mapped from the IdP's group claim.
            ip: Client IP of the login request, recorded on the user_created
                audit event when a new user is provisioned.
            user_agent: Client user agent of the login request, recorded on the
                user_created audit event when a new user is provisioned.

        Returns:
            Existing or newly-created user dict.

        Raises:
            ValueError: If a matching user exists but is inactive.
        """
        existing = self.get_user_by_email(email)
        if existing:
            if not existing.get('is_active', False):
                raise ValueError(
                    f"User with email {email!r} matches an existing inactive account; "
                    "an admin must re-activate the account before SSO sign-in can succeed."
                )
            return existing

        # Derive username from email local part; append numeric suffix on collision.
        username_base = email.split('@', 1)[0].lower()
        username = username_base
        suffix = 0
        while self.get_user_by_username(username) is not None:
            suffix += 1
            username = f"{username_base}{suffix}"
            if suffix > 99:
                raise ValueError(f"Could not derive a unique username from email {email!r}")

        unusable_hash = self.UNUSABLE_HASH_PREFIX_OIDC + secrets.token_urlsafe(48)

        insert_query = """
            INSERT INTO users (username, email, password_hash, full_name, role, is_active, email_verified)
            VALUES (%(username)s, %(email)s, %(password_hash)s, %(full_name)s, %(role)s, true, true)
            RETURNING id
        """
        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(insert_query, {
                    'username': username,
                    'email': email,
                    'password_hash': unusable_hash,
                    'full_name': full_name,
                    'role': role,
                })
                new_id = cursor.fetchone()['id']
                conn.commit()
                logger.info("OIDC-provisioned user %s (%s) with role %s", username, new_id, role)
                self._emit_provisioning_audit(
                    new_id, username, source='oidc', role=role,
                    ip=ip, user_agent=user_agent,
                )
                return self.get_user_by_id(new_id)
        except IntegrityError:
            # Concurrent first-login for the same email — another request won the
            # race between get_user_by_email/while-suffix-search and this INSERT.
            # Re-fetch by email (the stable identity in the OIDC flow) and return it.
            logger.info("OIDC get_or_create race for %r — refetching by email", email)
            existing = self.get_user_by_email(email)
            if existing:
                return existing
            raise

    def _emit_provisioning_audit(
        self, user_id: UUID, username: str, *, source: str, role: str,
        ip: Optional[str] = None, user_agent: Optional[str] = None,
    ) -> None:
        """Emit a ``user_created`` auth audit event for SSO auto-provisioning.

        LDAP/OIDC first-login provisioning is the primary account-creation vector
        in SSO deployments, yet it previously wrote no audit row — so the seeded
        auth alert rules never saw those accounts (#826). This mirrors the manual
        admin create-user path (``auth.py``), recording the ``source``
        (ldap/oidc) and the assigned ``role`` in ``metadata`` and the request's
        ``ip``/``user_agent`` on the event so an SSO-provisioned account carries
        the same source detail as every other auth event (and its sibling
        login_ldap/login_oidc row).

        ``AuditService.log_auth`` swallows its own failures, so an audit-write
        error can never break the login that triggered provisioning — the same
        guarantee the manual create-user path relies on.
        """
        # Local import: this repository deliberately keeps service imports out of
        # module scope (see the UNUSABLE_HASH_PREFIX_INVITE note above) so these
        # data-access queries don't drag in the service layer.
        from src.auth.services.audit_service import AuditService

        AuditService(self.conn_manager).log_auth(
            action='user_created',
            success=True,
            user_id=user_id,
            username=username,
            ip=ip,
            user_agent=user_agent,
            metadata={'source': source, 'role': role},
        )

    def get_user_by_id(self, user_id: UUID) -> Optional[Dict]:
        """
        Get user by ID (excludes password_hash).

        Args:
            user_id: User UUID

        Returns:
            User dictionary if found, None otherwise
        """
        query = """
            SELECT id, username, email, full_name, role, is_active,
                   failed_login_attempts, locked_until, last_login_at,
                   email_verified, must_change_password, created_at, updated_at,
                   (COALESCE(password_hash, '') LIKE %(ldap_prefix)s) AS is_ldap_user,
                   (COALESCE(password_hash, '') LIKE %(oidc_prefix)s) AS is_oidc_user,
                   (COALESCE(password_hash, '') LIKE %(invite_prefix)s) AS onboarding_pending
            FROM users
            WHERE id = %(user_id)s
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {
                    'user_id': str(user_id),
                    'ldap_prefix': f'{self.UNUSABLE_HASH_PREFIX_LDAP}%',
                    'oidc_prefix': f'{self.UNUSABLE_HASH_PREFIX_OIDC}%',
                    'invite_prefix': f'{self.UNUSABLE_HASH_PREFIX_INVITE}%',
                })
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting user by ID {user_id}: {e}")
            raise

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """
        Get user by username (case-insensitive, includes password_hash for auth).

        Args:
            username: Username to search for

        Returns:
            User dictionary if found (including password_hash), None otherwise
        """
        query = """
            SELECT id, username, email, password_hash, full_name, role, is_active,
                   failed_login_attempts, locked_until, last_login_at,
                   email_verified, must_change_password, created_at
            FROM users
            WHERE LOWER(username) = LOWER(%(username)s)
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'username': username})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            raise

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """
        Get user by email (case-insensitive, includes password_hash for auth).

        Args:
            email: Email address to search for

        Returns:
            User dictionary if found (including password_hash), None otherwise
        """
        query = """
            SELECT id, username, email, password_hash, full_name, role, is_active,
                   failed_login_attempts, locked_until, last_login_at,
                   email_verified, must_change_password, created_at
            FROM users
            WHERE LOWER(email) = LOWER(%(email)s)
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'email': email})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting user by email {email}: {e}")
            raise

    def update_login_success(self, user_id: UUID) -> None:
        """
        Reset failed attempts and update last login timestamp.

        Called after successful authentication.

        Args:
            user_id: User UUID
        """
        query = """
            UPDATE users
            SET failed_login_attempts = 0,
                locked_until = NULL,
                last_login_at = NOW(),
                updated_at = NOW()
            WHERE id = %(user_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                conn.commit()
                logger.debug(f"Updated login success for user {user_id}")

        except Exception as e:
            logger.error(f"Error updating login success for user {user_id}: {e}")
            raise

    def increment_failed_attempts(self, user_id: UUID) -> int:
        """
        Increment failed login attempts and lock account if threshold reached.

        Account locks after 5 failed attempts for 30 minutes.

        Args:
            user_id: User UUID

        Returns:
            New failed_attempts count

        Raises:
            Exception: For database errors
        """
        # Increment attempts
        query = """
            UPDATE users
            SET failed_login_attempts = failed_login_attempts + 1,
                updated_at = NOW()
            WHERE id = %(user_id)s
            RETURNING failed_login_attempts
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                result = cursor.fetchone()
                failed_attempts = result['failed_login_attempts']

                # Lock account if threshold reached (5 attempts)
                if failed_attempts >= 5:
                    lock_until = datetime.utcnow() + timedelta(minutes=30)
                    lock_query = """
                        UPDATE users
                        SET locked_until = %(lock_until)s,
                            updated_at = NOW()
                        WHERE id = %(user_id)s
                    """
                    cursor.execute(lock_query, {
                        'user_id': str(user_id),
                        'lock_until': lock_until
                    })
                    logger.warning(
                        f"User {user_id} locked until {lock_until} after {failed_attempts} failed attempts"
                    )

                conn.commit()
                return failed_attempts

        except Exception as e:
            logger.error(f"Error incrementing failed attempts for user {user_id}: {e}")
            raise

    def is_account_locked(self, user: Dict) -> bool:
        """
        Check if account is currently locked.

        Args:
            user: User dictionary with 'locked_until' field

        Returns:
            True if account is locked, False otherwise
        """
        if user.get('locked_until'):
            # Handle both timezone-aware and naive datetimes
            locked_until = user['locked_until']
            now = datetime.now(locked_until.tzinfo) if locked_until.tzinfo else datetime.utcnow()
            return now < locked_until
        return False

    def list_users(
        self,
        page: int = 1,
        limit: int = 50,
        role: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> Dict:
        """
        List users with pagination and filters.

        Args:
            page: Page number (1-indexed)
            limit: Results per page (max 100)
            role: Filter by role (user/admin/super_admin)
            is_active: Filter by active status

        Returns:
            Dictionary with:
            {
                'users': List[Dict],
                'total': int,
                'page': int,
                'limit': int,
                'pages': int
            }
        """
        # Enforce max limit
        limit = min(limit, 100)

        # Build filters
        filters = []
        params = {'offset': (page - 1) * limit, 'limit': limit}

        if role:
            filters.append("role = %(role)s")
            params['role'] = role

        if is_active is not None:
            filters.append("is_active = %(is_active)s")
            params['is_active'] = is_active

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        # Get total count
        count_query = f"SELECT COUNT(*) as total FROM users {where_clause}"

        # Derive ldap/oidc/invite flags in SQL so password_hash never leaves Postgres.
        users_query = f"""
            SELECT id, username, email, full_name, role, is_active,
                   last_login_at, email_verified, must_change_password, created_at,
                   (COALESCE(password_hash, '') LIKE %(ldap_prefix)s) AS is_ldap_user,
                   (COALESCE(password_hash, '') LIKE %(oidc_prefix)s) AS is_oidc_user,
                   (COALESCE(password_hash, '') LIKE %(invite_prefix)s) AS onboarding_pending
            FROM users
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                # Get total count
                cursor.execute(count_query, params)
                total = cursor.fetchone()['total']

                # Get users
                params['ldap_prefix'] = f'{self.UNUSABLE_HASH_PREFIX_LDAP}%'
                params['oidc_prefix'] = f'{self.UNUSABLE_HASH_PREFIX_OIDC}%'
                params['invite_prefix'] = f'{self.UNUSABLE_HASH_PREFIX_INVITE}%'
                cursor.execute(users_query, params)
                users = [dict(row) for row in cursor.fetchall()]

                pages = (total + limit - 1) // limit  # Ceiling division

                return {
                    'users': users,
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': pages
                }

        except Exception as e:
            logger.error(f"Error listing users: {e}")
            raise

    def update_password(self, user_id: UUID, new_password_hash: str) -> None:
        """
        Update user password and clear must_change_password flag.

        Args:
            user_id: User UUID
            new_password_hash: New bcrypt hashed password
        """
        query = """
            UPDATE users
            SET password_hash = %(password_hash)s,
                must_change_password = false,
                password_changed_at = NOW(),
                updated_at = NOW()
            WHERE id = %(user_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {
                    'user_id': str(user_id),
                    'password_hash': new_password_hash
                })
                conn.commit()
                logger.info(f"Password updated for user {user_id}")

        except Exception as e:
            logger.error(f"Error updating password for user {user_id}: {e}")
            raise

    def mark_password_expired_if_old(self, user_id: UUID, max_age_days: int) -> bool:
        """If the user is local AND their password is older than max_age_days,
        flip must_change_password=true. Returns True when the flag was set,
        False otherwise (including disabled / non-positive max_age_days, and
        LDAP/OIDC users whose password_hash carries the protocol prefix).

        Idempotent at the SQL level — re-running for an already-flagged user
        is a no-op.
        """
        if max_age_days <= 0:
            return False

        query = """
            UPDATE users
            SET must_change_password = true,
                updated_at = NOW()
            WHERE id = %(user_id)s
              AND must_change_password = false
              AND COALESCE(password_hash, '') NOT LIKE %(ldap_prefix)s
              AND COALESCE(password_hash, '') NOT LIKE %(oidc_prefix)s
              AND password_changed_at < NOW() - %(days)s * INTERVAL '1 day'
            RETURNING id
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {
                    'user_id': str(user_id),
                    'days': max_age_days,
                    'ldap_prefix': f'{self.UNUSABLE_HASH_PREFIX_LDAP}%',
                    'oidc_prefix': f'{self.UNUSABLE_HASH_PREFIX_OIDC}%',
                })
                row = cursor.fetchone()
                conn.commit()
                if row is not None:
                    logger.info(
                        f"Password expired for user {user_id} (>{max_age_days}d) — must_change_password flag set"
                    )
                    return True
                return False

        except Exception as e:
            logger.error(f"Error checking password expiration for user {user_id}: {e}")
            raise

    def set_must_change_password(self, user_id: UUID) -> None:
        """Idempotently set must_change_password=true. No other side effects."""
        query = """
            UPDATE users
            SET must_change_password = true,
                updated_at = NOW()
            WHERE id = %(user_id)s
        """
        with self.conn_manager.get_cursor() as (cursor, conn):
            cursor.execute(query, {'user_id': str(user_id)})
            conn.commit()
            logger.info(f"must_change_password flag set for user {user_id}")

    def deactivate_user(self, user_id: UUID) -> None:
        """
        Deactivate user account.

        Args:
            user_id: User UUID
        """
        query = """
            UPDATE users
            SET is_active = false,
                updated_at = NOW()
            WHERE id = %(user_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                conn.commit()
                logger.info(f"User {user_id} deactivated")

        except Exception as e:
            logger.error(f"Error deactivating user {user_id}: {e}")
            raise

    def activate_user(self, user_id: UUID) -> None:
        """
        Activate user account.

        Args:
            user_id: User UUID
        """
        query = """
            UPDATE users
            SET is_active = true,
                updated_at = NOW()
            WHERE id = %(user_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                conn.commit()
                logger.info(f"User {user_id} activated")

        except Exception as e:
            logger.error(f"Error activating user {user_id}: {e}")
            raise

    def update_user(
        self,
        user_id: UUID,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        role: Optional[str] = None
    ) -> Dict:
        """
        Update user profile fields.

        Args:
            user_id: User UUID
            email: New email (optional)
            full_name: New full name (optional)
            role: New role (optional)

        Returns:
            Updated user dictionary

        Raises:
            ValueError: If email already exists
        """
        updates = []
        params = {'user_id': str(user_id)}

        if email is not None:
            updates.append("email = %(email)s")
            params['email'] = email

        if full_name is not None:
            updates.append("full_name = %(full_name)s")
            params['full_name'] = full_name

        if role is not None:
            updates.append("role = %(role)s")
            params['role'] = role

        if not updates:
            # No updates, just return current user
            return self.get_user_by_id(user_id)

        updates.append("updated_at = NOW()")

        query = f"""
            UPDATE users
            SET {', '.join(updates)}
            WHERE id = %(user_id)s
            RETURNING id, username, email, full_name, role, is_active, created_at, updated_at
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                result = dict(cursor.fetchone())
                conn.commit()
                logger.info(f"User {user_id} updated")
                return result

        except Exception as e:
            error_msg = str(e).lower()
            if 'unique constraint' in error_msg or 'duplicate key' in error_msg:
                if 'email' in error_msg:
                    raise ValueError(f'Email "{email}" already exists')
            logger.error(f"Error updating user {user_id}: {e}")
            raise

    def delete_user(self, user_id: UUID) -> bool:
        """
        Delete user (hard delete).

        Note: Use deactivate_user() instead for soft delete.

        Args:
            user_id: User UUID

        Returns:
            True if deleted, False if not found
        """
        query = "DELETE FROM users WHERE id = %(user_id)s"

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                deleted = cursor.rowcount > 0
                conn.commit()

                if deleted:
                    logger.info(f"User {user_id} deleted")
                else:
                    logger.warning(f"User {user_id} not found for deletion")

                return deleted

        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            raise

    def get_user_stats(self) -> Dict:
        """
        Get user statistics including total count, active count, and breakdown by role.

        Returns:
            Dictionary with statistics: {
                'total': int,
                'active': int,
                'inactive': int,
                'by_role': {
                    'user': int,
                    'admin': int,
                    'super_admin': int
                }
            }
        """
        query = """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_active = true) as active,
                COUNT(*) FILTER (WHERE is_active = false) as inactive,
                COUNT(*) FILTER (WHERE role = 'user') as role_user,
                COUNT(*) FILTER (WHERE role = 'admin') as role_admin,
                COUNT(*) FILTER (WHERE role = 'super_admin') as role_super_admin
            FROM users
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query)
                result = dict(cursor.fetchone())

                stats = {
                    'total': int(result['total']),
                    'active': int(result['active']),
                    'inactive': int(result['inactive']),
                    'by_role': {
                        'user': int(result['role_user']),
                        'admin': int(result['role_admin']),
                        'super_admin': int(result['role_super_admin'])
                    }
                }

                logger.debug(f"User stats: {stats}")
                return stats

        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            raise

    @staticmethod
    def is_ldap_user(user: Dict) -> bool:
        """Detect an LDAP-provisioned user by their unusable password hash prefix."""
        hash_val = user.get('password_hash') if user else None
        return isinstance(hash_val, str) and hash_val.startswith(
            UserRepository.UNUSABLE_HASH_PREFIX_LDAP
        )

    @staticmethod
    def is_oidc_user(user: Dict) -> bool:
        """Detect an OIDC-provisioned user by their unusable password hash prefix."""
        hash_val = user.get('password_hash') if user else None
        return isinstance(hash_val, str) and hash_val.startswith(
            UserRepository.UNUSABLE_HASH_PREFIX_OIDC
        )

    @staticmethod
    def is_external_user(user: Dict) -> bool:
        """True for users whose local password hash is unusable (LDAP or OIDC)."""
        return UserRepository.is_ldap_user(user) or UserRepository.is_oidc_user(user)
