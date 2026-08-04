# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Role-Based Access Control (RBAC) Middleware

Provides decorators for protecting API endpoints with role-based permissions.
Includes comprehensive audit logging for all authorization events.

Usage:
    from src.auth.middleware.rbac import require_role, require_admin, require_super_admin

    @app.route('/admin/users')
    @jwt_required()
    @require_admin()
    def list_users():
        # Only accessible to admin and super_admin users
        pass

    @app.route('/super-admin/settings')
    @jwt_required()
    @require_super_admin()
    def update_settings():
        # Only accessible to super_admin users
        pass
"""

from functools import wraps
from flask import jsonify, request
from flask_jwt_extended import get_jwt, current_user
from typing import List, Callable
import logging

# Import comprehensive audit logger
from src.api.utils.audit_logger import AuditLogger
from src.api.utils.audit_vocab import AuditAction

logger = logging.getLogger(__name__)


# Role hierarchy (lower value = higher privilege)
ROLE_HIERARCHY = {
    'super_admin': 1,
    'admin': 2,
    'user': 3
}


def has_required_role(user_role: str, required_role: str) -> bool:
    """
    Check if user's role meets or exceeds the required role.

    Uses role hierarchy where super_admin > admin > user.

    Args:
        user_role: User's current role
        required_role: Minimum required role

    Returns:
        bool: True if user has sufficient privileges

    Example:
        has_required_role('admin', 'user') -> True (admin can access user resources)
        has_required_role('user', 'admin') -> False (user cannot access admin resources)
        has_required_role('super_admin', 'admin') -> True (super_admin can access admin resources)
    """
    user_level = ROLE_HIERARCHY.get(user_role, 999)
    required_level = ROLE_HIERARCHY.get(required_role, 999)

    return user_level <= required_level


def require_role(required_role: str) -> Callable:
    """
    Decorator to require a specific role or higher.

    Uses role hierarchy: super_admin > admin > user
    Logs all authorization checks to audit log.

    Args:
        required_role: Minimum required role ('user', 'admin', or 'super_admin')

    Returns:
        Decorator function

    Example:
        @app.route('/admin/dashboard')
        @jwt_required()
        @require_role('admin')
        def admin_dashboard():
            # Accessible to admin and super_admin
            pass
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Get current user from JWT
            user = current_user
            resource_path = request.path if request else 'unknown'

            if not user:
                logger.warning(f"Access denied: User not found in JWT")
                return jsonify({
                    'error': 'unauthorized',
                    'message': 'User not found'
                }), 401

            # Check if user account is active
            if not user.get('is_active', False):
                logger.warning(f"Access denied: User {user.get('username')} account is inactive")

                # Audit log: Access denied (inactive account)
                AuditLogger.log_authz_event(
                    event='access_check',
                    user_id=str(user.get('id')),
                    username=user.get('username'),
                    resource=resource_path,
                    action=AuditAction.ACCESS,
                    allowed=False,
                    details={'required_role': required_role, 'user_role': user.get('role', 'user')},
                    reason='Account is inactive'
                )

                return jsonify({
                    'error': 'account_disabled',
                    'message': 'Your account has been disabled'
                }), 403

            # Check role
            user_role = user.get('role', 'user')

            if not has_required_role(user_role, required_role):
                logger.warning(
                    f"Access denied: User {user.get('username')} (role: {user_role}) "
                    f"attempted to access resource requiring role: {required_role}"
                )

                # Audit log: Access denied (insufficient role)
                AuditLogger.log_authz_event(
                    event='access_check',
                    user_id=str(user.get('id')),
                    username=user.get('username'),
                    resource=resource_path,
                    action=AuditAction.ACCESS,
                    allowed=False,
                    details={
                        'required_role': required_role,
                        'user_role': user_role,
                        'role_hierarchy': f"{user_role} < {required_role}"
                    },
                    reason=f'Insufficient permissions: requires {required_role} or higher'
                )

                return jsonify({
                    'error': 'insufficient_permissions',
                    'message': f'This resource requires {required_role} role or higher',
                    'required_role': required_role,
                    'user_role': user_role
                }), 403

            # Access granted - only log at debug level (no audit log for routine operations)
            # Audit logging successful auth checks on EVERY request causes disk I/O overhead
            # Only failures and sensitive operations should be audited
            logger.debug(f"Access granted: User {user.get('username')} (role: {user_role})")

            return fn(*args, **kwargs)

        return wrapper
    return decorator


def require_roles(allowed_roles: List[str]) -> Callable:
    """
    Decorator to require one of multiple specific roles (exact match).

    Unlike require_role which uses hierarchy, this checks for exact role match.

    Args:
        allowed_roles: List of allowed roles

    Returns:
        Decorator function

    Example:
        @app.route('/moderator/actions')
        @jwt_required()
        @require_roles(['admin', 'super_admin'])
        def moderator_actions():
            # Accessible only to admin and super_admin (not to regular users)
            pass
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Get current user from JWT
            user = current_user

            if not user:
                logger.warning(f"Access denied: User not found in JWT")
                return jsonify({
                    'error': 'unauthorized',
                    'message': 'User not found'
                }), 401

            # Check if user account is active
            if not user.get('is_active', False):
                logger.warning(f"Access denied: User {user.get('username')} account is inactive")
                return jsonify({
                    'error': 'account_disabled',
                    'message': 'Your account has been disabled'
                }), 403

            # Check if user's role is in allowed roles
            user_role = user.get('role', 'user')

            if user_role not in allowed_roles:
                logger.warning(
                    f"Access denied: User {user.get('username')} (role: {user_role}) "
                    f"attempted to access resource requiring one of: {', '.join(allowed_roles)}"
                )
                return jsonify({
                    'error': 'insufficient_permissions',
                    'message': f'This resource requires one of these roles: {", ".join(allowed_roles)}',
                    'allowed_roles': allowed_roles,
                    'user_role': user_role
                }), 403

            # Access granted
            logger.debug(f"Access granted: User {user.get('username')} (role: {user_role})")
            return fn(*args, **kwargs)

        return wrapper
    return decorator


def require_admin() -> Callable:
    """
    Decorator to require admin or super_admin role.

    Convenience decorator equivalent to @require_role('admin').

    Returns:
        Decorator function

    Example:
        @app.route('/admin/users')
        @jwt_required()
        @require_admin()
        def manage_users():
            # Accessible to admin and super_admin
            pass
    """
    return require_role('admin')


def require_super_admin() -> Callable:
    """
    Decorator to require super_admin role only.

    Convenience decorator for super admin-only resources.

    Returns:
        Decorator function

    Example:
        @app.route('/super-admin/system-settings')
        @jwt_required()
        @require_super_admin()
        def system_settings():
            # Only accessible to super_admin
            pass
    """
    return require_role('super_admin')


def require_self_or_admin(user_id_param: str = 'user_id') -> Callable:
    """
    Decorator to require user to be accessing their own resource OR be an admin.

    Useful for endpoints like /users/<user_id> where users can access their own
    data but admins can access anyone's data.
    Logs all authorization checks to audit log.

    Args:
        user_id_param: Name of the URL parameter containing the user ID

    Returns:
        Decorator function

    Example:
        @app.route('/users/<user_id>/profile')
        @jwt_required()
        @require_self_or_admin('user_id')
        def get_user_profile(user_id):
            # Users can access their own profile, admins can access any profile
            pass
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Get current user from JWT
            user = current_user
            resource_path = request.path if request else 'unknown'

            if not user:
                logger.warning(f"Access denied: User not found in JWT")
                return jsonify({
                    'error': 'unauthorized',
                    'message': 'User not found'
                }), 401

            # Check if user account is active
            if not user.get('is_active', False):
                logger.warning(f"Access denied: User {user.get('username')} account is inactive")

                # Audit log: Access denied (inactive account)
                AuditLogger.log_authz_event(
                    event='self_or_admin_check',
                    user_id=str(user.get('id')),
                    username=user.get('username'),
                    resource=resource_path,
                    action=AuditAction.ACCESS,
                    allowed=False,
                    details={'user_role': user.get('role', 'user')},
                    reason='Account is inactive'
                )

                return jsonify({
                    'error': 'account_disabled',
                    'message': 'Your account has been disabled'
                }), 403

            # Get target user ID from URL parameters
            target_user_id = kwargs.get(user_id_param)
            current_user_id = str(user.get('id'))
            user_role = user.get('role', 'user')

            # Allow if user is accessing their own resource
            if target_user_id == current_user_id:
                logger.debug(f"Access granted: User {user.get('username')} accessing own resource")

                # Audit log: Access granted (own resource)
                AuditLogger.log_authz_event(
                    event='self_or_admin_check',
                    user_id=current_user_id,
                    username=user.get('username'),
                    resource=resource_path,
                    action=AuditAction.ACCESS_OWN_RESOURCE,
                    allowed=True,
                    details={'target_user_id': target_user_id, 'user_role': user_role}
                )

                return fn(*args, **kwargs)

            # Allow if user is admin or super_admin
            if has_required_role(user_role, 'admin'):
                logger.debug(f"Access granted: Admin {user.get('username')} accessing user resource")

                # Audit log: Access granted (admin accessing other user's resource)
                AuditLogger.log_authz_event(
                    event='self_or_admin_check',
                    user_id=current_user_id,
                    username=user.get('username'),
                    resource=resource_path,
                    action=AuditAction.ADMIN_ACCESS_USER_RESOURCE,
                    allowed=True,
                    details={
                        'target_user_id': target_user_id,
                        'user_role': user_role,
                        'access_type': 'admin_override'
                    }
                )

                return fn(*args, **kwargs)

            # Deny access
            logger.warning(
                f"Access denied: User {user.get('username')} (role: {user_role}) "
                f"attempted to access another user's resource"
            )

            # Audit log: Access denied (attempted to access another user's resource)
            AuditLogger.log_authz_event(
                event='self_or_admin_check',
                user_id=current_user_id,
                username=user.get('username'),
                resource=resource_path,
                action=AuditAction.ACCESS,
                allowed=False,
                details={
                    'target_user_id': target_user_id,
                    'user_role': user_role,
                    'attempted_access': 'other_user_resource'
                },
                reason='User can only access their own resources'
            )

            return jsonify({
                'error': 'insufficient_permissions',
                'message': 'You can only access your own resources'
            }), 403

        return wrapper
    return decorator
