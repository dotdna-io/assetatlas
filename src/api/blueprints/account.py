# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-service Account Blueprint (#612).

Routes for the *current user* — never accept user_id in path or body.
JWT-required throughout. Mounted under /api/account/.
"""
import logging
from uuid import UUID

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, current_user
from pydantic import ValidationError

from src.api.middleware.error_handler import handle_errors
from src.auth.repositories.user_repository import UserRepository
from src.auth.repositories.user_mfa_repository import UserMfaRepository
from src.auth.services.audit_service import AuditService
from src.auth.services.password_service import PasswordService
from src.auth.models.auth import (
    AccountMfaListResponse,
    AccountMfaMethod,
    AccountMfaUpdateRequest,
    AccountProfileUpdateRequest,
    ChangePasswordRequest,
)

logger = logging.getLogger(__name__)

account_bp = Blueprint('account', __name__, url_prefix='/api/account')


# ----- helpers --------------------------------------------------------------

def _to_method_response(row: dict) -> AccountMfaMethod:
    """Project a `user_mfa` row onto the API response model. The schema has
    NOT NULL DEFAULT for type/label/mfa_enabled, so the values are always
    present — no defensive fallbacks needed.
    """
    return AccountMfaMethod(
        id=str(row['id']),
        type=row['type'],
        label=row['label'],
        enabled=bool(row['mfa_enabled']),
        created_at=row.get('created_at'),
    )


# ----- GET /mfa --------------------------------------------------------------

@account_bp.route('/mfa', methods=['GET'])
@jwt_required()
@handle_errors
def get_account_mfa():
    """List the current user's MFA methods (enrolled + pending).
    ---
    tags: [Account]
    responses:
      200: {description: Methods list}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404
    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    rows = mfa_repo.list_methods(current_user['id'])
    response = AccountMfaListResponse(
        methods=[_to_method_response(r) for r in rows],
    )
    return jsonify(response.model_dump(mode='json')), 200


@account_bp.route('/profile', methods=['PATCH'])
@jwt_required()
@handle_errors
def update_account_profile():
    """Update the current user's profile.
    ---
    tags: [Account]
    responses:
      200: {description: Updated user}
      400: {description: Validation error}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    try:
        body = AccountProfileUpdateRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']

    if body.full_name is None:
        return jsonify({
            'error': 'no_fields_to_update',
            'message': 'Provide at least one field to update.',
        }), 400

    updated = user_repo.update_user(user_id, full_name=body.full_name)

    # Drop the cached jwt_user dict so the next /auth/me reflects the new
    # full_name immediately. Without this, the user_lookup_loader serves
    # the pre-update record for up to 60s and the change appears not to
    # persist on reload.
    try:
        from src.api.utils.caching import cache
        cache.delete(f"jwt_user:{user_id}")
    except Exception as e:
        logger.warning(f"Cache invalidation failed for user {user_id}: {e}")

    audit_service.log_auth(
        action='user_display_name_changed_self',
        success=True,
        user_id=user_id,
        username=username,
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'new_full_name': body.full_name},
    )
    return jsonify({'success': True, 'user': updated}), 200


@account_bp.route('/password', methods=['POST'])
@jwt_required()
@handle_errors
def change_account_password():
    """Self-service password change for the current user (local accounts only).
    ---
    tags: [Account]
    responses:
      200: {description: Changed}
      400: {description: Validation error or weak password}
      401: {description: Wrong current password}
      403: {description: SSO/LDAP user — managed by IdP}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    try:
        body = ChangePasswordRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({'error': 'validation_error', 'details': e.errors(include_context=False)}), 400

    conn_manager = current_app.connection_manager
    user_repo = UserRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    # get_user_by_id strips password_hash; use get_user_by_username so the
    # current-password verify and is_external_user prefix check both have
    # access to the actual hash.
    user = user_repo.get_user_by_username(username)
    if not user:
        return jsonify({'error': 'user_not_found'}), 404

    if UserRepository.is_external_user(user):
        return jsonify({
            'error': 'managed_by_idp',
            'message': 'Your password is managed by your identity provider.',
        }), 403

    if not PasswordService.verify_password(body.current_password, user['password_hash']):
        audit_service.log_auth(
            action='user_password_changed_self',
            success=False,
            user_id=user_id,
            username=username,
            ip=ip_address,
            user_agent=user_agent,
            failure_reason='invalid_password',
        )
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'Current password is incorrect.',
        }), 401

    strength = PasswordService.validate_password_strength(body.new_password)
    if not strength['valid']:
        return jsonify({
            'error': 'weak_password',
            'message': '; '.join(strength['errors']),
        }), 400

    user_repo.update_password(user_id, PasswordService.hash_password(body.new_password))

    # update_password clears must_change_password in DB; drop the cached
    # jwt_user dict so the user_lookup_loader stops serving the pre-change
    # state. Without this, a user who just satisfied a forced password
    # change can be redirected back to /account?force=password for up to
    # the cache TTL (60s).
    try:
        from src.api.utils.caching import cache
        cache.delete(f"jwt_user:{user_id}")
    except Exception as e:
        logger.warning(f"Cache invalidation failed for user {user_id}: {e}")

    audit_service.log_auth(
        action='user_password_changed_self',
        success=True,
        user_id=user_id,
        username=username,
        ip=ip_address,
        user_agent=user_agent,
    )
    return jsonify({'success': True}), 200


# ----- TOTP enroll/verify (delegate to /auth/mfa/{setup,verify}) ------------

@account_bp.route('/mfa/totp/setup', methods=['POST'])
@jwt_required()
@handle_errors
def account_mfa_totp_setup():
    """Begin TOTP enrollment for the current user. Delegates to /auth/mfa/setup
    (which now accepts an optional ``label`` in the body and replaces any prior
    pending row). Works whether the user already has enrolled methods or not —
    this is also the "Add another method" entry point.
    ---
    tags: [Account]
    """
    from src.api.blueprints.auth import mfa_setup
    return mfa_setup()


@account_bp.route('/mfa/totp/verify', methods=['POST'])
@jwt_required()
@handle_errors
def account_mfa_totp_verify():
    """Complete TOTP enrollment for the current user. Delegates to
    /auth/mfa/verify (which audits `mfa_enabled` on success), and additionally
    emits the self-service `user_mfa_method_added` audit row when the
    underlying response is 200.
    ---
    tags: [Account]
    """
    from src.api.blueprints.auth import mfa_verify
    response = mfa_verify()
    # Flask handlers may return (resp, status) tuples or a Response.
    status = response[1] if isinstance(response, tuple) else response.status_code
    if status == 200 and current_user:
        conn_manager = current_app.connection_manager
        audit_service = AuditService(conn_manager)
        audit_service.log_auth(
            action='user_mfa_method_added',
            success=True,
            user_id=current_user['id'],
            username=current_user['username'],
            ip=request.remote_addr,
            user_agent=request.headers.get('User-Agent', ''),
            metadata={'method_type': 'totp'},
        )
    return response


@account_bp.route('/mfa/<method_id>', methods=['PATCH'])
@jwt_required()
@handle_errors
def update_account_mfa(method_id: str):
    """Rename one of the current user's MFA methods.
    ---
    tags: [Account]
    responses:
      200: {description: Updated}
      400: {description: Validation error}
      404: {description: Method not found}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    try:
        method_uuid = UUID(method_id)
    except ValueError:
        return jsonify({'error': 'method_not_found'}), 404

    try:
        body = AccountMfaUpdateRequest(**(request.json or {}))
    except ValidationError as e:
        return jsonify({
            'error': 'validation_error',
            'details': e.errors(include_context=False),
        }), 400

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']

    # Atomic ownership check: the UPDATE is scoped to (id, user_id), so a
    # method id belonging to another user updates 0 rows — same response as
    # "missing" so we don't leak the existence of other users' rows.
    if not mfa_repo.update_label_safely(user_id, method_uuid, body.label):
        return jsonify({
            'error': 'method_not_found',
            'message': 'No MFA method matches that identifier.',
        }), 404

    audit_service.log_auth(
        action='user_mfa_method_renamed',
        success=True,
        user_id=user_id,
        username=username,
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={'method_id': str(method_uuid), 'new_label': body.label.strip()},
    )
    return jsonify({'success': True}), 200


@account_bp.route('/mfa/<method_id>', methods=['DELETE'])
@jwt_required()
@handle_errors
def remove_account_mfa(method_id: str):
    """Remove an MFA method for the current user.

    Removing a method that would leave the user with zero ENABLED methods is
    refused (HTTP 400, ``must_keep_one_mfa_method``) — this preserves the
    invariant from #610 (every local user must keep at least one method
    enrolled). Pending (mfa_enabled=false) rows can always be removed because
    they don't count toward the enabled-method floor.

    The UI mirrors this guard with a disabled "Remove" button on the row that
    would trip it.
    ---
    tags: [Account]
    responses:
      200: {description: Removed}
      400: {description: Last enabled method — refused}
      404: {description: Method not found}
    """
    if not current_user:
        return jsonify({'error': 'user_not_found'}), 404

    try:
        method_uuid = UUID(method_id)
    except ValueError:
        return jsonify({'error': 'method_not_found'}), 404

    conn_manager = current_app.connection_manager
    mfa_repo = UserMfaRepository(conn_manager)
    audit_service = AuditService(conn_manager)
    user_id = current_user['id']
    username = current_user['username']

    # Atomic check + delete under a per-user advisory lock — concurrent
    # delete calls can't both pass the floor check and drop the user to
    # zero enabled methods.
    result = mfa_repo.delete_method_safely(user_id, method_uuid)
    if not result['deleted']:
        if result['reason'] == 'method_not_found':
            return jsonify({
                'error': 'method_not_found',
                'message': 'No MFA method matches that identifier.',
            }), 404
        if result['reason'] == 'must_keep_one_mfa_method':
            return jsonify({
                'error': 'must_keep_one_mfa_method',
                'message': 'You must keep at least one MFA method enrolled.',
            }), 400
    target = result['method']

    audit_service.log_auth(
        action='user_mfa_method_removed',
        success=True,
        user_id=user_id,
        username=username,
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent', ''),
        metadata={
            'method_type': target['type'],
            'method_id': str(method_uuid),
            'label': target['label'],
        },
    )
    return jsonify({'success': True}), 200
