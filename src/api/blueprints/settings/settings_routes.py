# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Settings API Endpoints

Provides REST API for application settings management.

NOTE: This handles application-level settings (features, UI, notifications, etc).
Infrastructure settings (logging, database) are managed via environment variables.

Endpoints:
- GET    /api/settings - Get all application settings
- GET    /api/settings/schema - Get settings schema
- GET    /api/settings/:category - Get settings by category
- PUT    /api/settings/:category/:key - Update single setting
- POST   /api/settings/:category/:key/reset - Reset to default
"""

import logging
from flask import Blueprint, request, current_app, jsonify
from flask_jwt_extended import jwt_required, current_user

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin
from src.api.utils.audit_logger import get_current_user_context
from src.api.blueprints.settings.category_hooks import (
    CATEGORY_HOOKS,
    HookValidationError,
)
from src.auth.services.email_service import EMAIL_NOT_CONFIGURED
from src.modules.integrations.shared.encryption import is_encrypted

logger = logging.getLogger(__name__)

# Create blueprint
settings_bp = Blueprint('settings', __name__, url_prefix='/api/settings')


def _is_confidence_threshold(category: str, key: str) -> bool:
    """True when a saved setting is one of the dashboard confidence thresholds.

    The dashboard materialized views bucket confidence high/medium/low through
    the ``confidence_threshold_{high,medium}()`` SQL functions, which read these
    settings. When either threshold changes the views must be refreshed for the
    new boundaries to take effect.
    """
    return category == 'asset_generation' and key in (
        'confidence_threshold_medium', 'confidence_threshold_high'
    )


@settings_bp.route('', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_all_settings():
    """
    Get all application settings.

    Returns:
        200: All settings grouped by category
    """
    settings_manager = current_app.settings_manager
    settings = settings_manager.get_all()

    return {
        'settings': settings,
        'categories': list(settings.keys())
    }


@settings_bp.route('/schema', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_settings_schema():
    """
    Get the settings schema definition.

    Returns:
        200: Settings schema with all available settings and their definitions
    """
    settings_manager = current_app.settings_manager
    schema = settings_manager.get_schema()

    return {
        'schema': schema
    }


EMAIL_PASSWORD_SENTINEL = '__SET__'


@settings_bp.route('/email', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_email_settings():
    """
    Get all email settings. The smtp_password is always redacted:
      - stored ENC:... ciphertext -> returned as the literal sentinel '__SET__'
      - empty string               -> returned as empty string
    """
    settings_manager = current_app.settings_manager
    category_settings = settings_manager.get_all(category='email').get('email', {})

    response = {}
    for key, entry in category_settings.items():
        value = entry.get('value') if isinstance(entry, dict) else entry
        if key == 'smtp_password':
            value = EMAIL_PASSWORD_SENTINEL if (isinstance(value, str) and is_encrypted(value)) else ''
        response[key] = value
    return response


@settings_bp.route('/email/test', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def send_email_test():
    """
    Send a test email to the specified recipient using the currently-saved
    email configuration.

    Deliberately usable while the outbound-email master toggle is off: this is
    the explicit admin-initiated test, and requiring the toggle first would mean
    arming live mail before you can verify the relay. `email_disabled` is
    therefore not a response of this endpoint — see
    ``EmailService.send_test_email``.

    Returns:
      200 {success: true}                              — on success
      400 {success: false, error: <smtplib msg>}       — SMTP or transport error
      409 {success: false, error: 'email_not_configured'} — missing required fields
    """
    data = request.get_json() or {}
    recipient = data.get('recipient')
    if not recipient or not isinstance(recipient, str):
        raise ValidationError('Missing required field: recipient')

    ok, err = current_app.email_service.send_test_email(recipient)

    if ok:
        return {'success': True}

    if err == EMAIL_NOT_CONFIGURED:
        return {'success': False, 'error': err}, 409

    return {'success': False, 'error': err or 'unknown error'}, 400


@settings_bp.route('/security/effective', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_security_effective():
    """Return the resolved security values that depend on env-var fallbacks.

    Today this is just ``public_base_url``: the resolver combines the DB
    setting with the ``ASSETATLAS_PUBLIC_BASE_URL`` env var. The Settings
    UI uses this so the Public Base URL input shows the env-var value when
    the DB row is empty, instead of presenting a blank field that hides
    the install's bootstrap default.

    Source values:
      - ``setting`` — the DB row is non-empty and being used
      - ``env``     — the DB row is empty; falling back to the env var
      - ``none``    — neither configured
    """
    from src.api.utils.public_base_url import resolve_public_base_url

    sm = current_app.settings_manager
    db_value = (sm.get('security', 'public_base_url', '') or '').strip()
    resolved = resolve_public_base_url(sm)

    if db_value:
        source = 'setting'
    elif resolved:
        source = 'env'
    else:
        source = 'none'

    return {
        'public_base_url': {
            'value': resolved,
            'source': source,
        },
    }


@settings_bp.route('/<category>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_category_settings(category: str):
    """
    Get settings for a specific category.

    Path Parameters:
        category: Settings category (features, ui, notifications, asset_generation, integrations)

    Returns:
        200: Settings for the category
        404: Category not found
    """
    settings_manager = current_app.settings_manager
    settings = settings_manager.get_all(category=category)

    if category not in settings:
        raise NotFoundError(f'Category "{category}" not found')

    return {
        'category': category,
        'settings': settings[category]
    }


@settings_bp.route('/<category>/<key>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_single_setting(category: str, key: str):
    """
    Get a single setting value.

    Path Parameters:
        category: Settings category
        key: Setting key

    Returns:
        200: Setting value
        404: Setting not found
    """
    settings_manager = current_app.settings_manager

    try:
        value = settings_manager.get(category, key)

        return {
            'category': category,
            'key': key,
            'value': value
        }
    except Exception as e:
        raise NotFoundError(f'Setting "{category}.{key}" not found')


@settings_bp.route('/<category>/<key>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def update_setting(category: str, key: str):
    """
    Update a single setting.

    Path Parameters:
        category: Settings category
        key: Setting key

    Request Body:
        {
            "value": <new_value>,
            "reason": "Why this change is being made (optional)"
        }

    Returns:
        200: Setting updated successfully
        400: Validation error
        404: Setting not found
    """
    settings_manager = current_app.settings_manager
    data = request.get_json()

    if 'value' not in data:
        raise ValidationError('Missing required field: value')

    value = data['value']
    reason = data.get('reason', 'Setting updated via API')

    hooks = CATEGORY_HOOKS.get(category)
    if hooks is not None:
        try:
            value = hooks.validate(key, value)
        except HookValidationError as exc:
            raise ValidationError(str(exc))
        value = hooks.pre_store(key, value)

    # Cross-field validation for confidence thresholds
    if category == 'asset_generation' and key in (
        'confidence_threshold_medium', 'confidence_threshold_high'
    ):
        if key == 'confidence_threshold_medium':
            current_high = settings_manager.get(
                'asset_generation', 'confidence_threshold_high'
            )
            if value >= current_high:
                raise ValidationError(
                    f'Medium threshold ({value}) must be less than '
                    f'high threshold ({current_high})'
                )
        elif key == 'confidence_threshold_high':
            current_medium = settings_manager.get(
                'asset_generation', 'confidence_threshold_medium'
            )
            if value <= current_medium:
                raise ValidationError(
                    f'High threshold ({value}) must be greater than '
                    f'medium threshold ({current_medium})'
                )

    # Get user context
    user_id, username = get_current_user_context()
    ip_address = request.remote_addr

    # Update setting
    success, error = settings_manager.set(
        category=category,
        key=key,
        value=value,
        user_id=user_id,
        username=username,
        reason=reason,
        ip_address=ip_address
    )

    if not success:
        if 'not found' in error.lower() or 'not defined' in error.lower():
            raise NotFoundError(error)
        else:
            raise ValidationError(error)

    # If this category redacts the audit values for this key, overwrite the
    # audit-log row that set() just inserted. Avoids leaking ciphertext to
    # audit consumers (e.g. Splunk forwarders) while keeping set() untouched.
    if hooks is not None and hooks.audit_redact(key):
        try:
            with current_app.connection_manager.get_cursor() as (cursor, conn):
                cursor.execute(
                    """
                    UPDATE settings_audit_log
                    SET old_value = '<redacted>', new_value = '<redacted>'
                    WHERE id = (
                        SELECT id FROM settings_audit_log
                        WHERE category = %s AND key = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    )
                    """,
                    (category, key),
                )
                conn.commit()
        except Exception as redact_exc:
            logger.warning(
                "audit-log redaction failed for %s.%s: %s",
                category, key, redact_exc,
            )

    # Get updated setting
    updated_value = settings_manager.get(category, key)

    # Sync pipeline schedule immediately when settings change
    if category == 'asset_generation' and key.startswith('pipeline_schedule_'):
        try:
            # Direct sync if scheduler is in this process (embedded mode)
            pipeline_service = getattr(current_app, 'pipeline_service', None)
            scheduler_service = getattr(current_app, 'scheduler_service', None)
            if pipeline_service and scheduler_service and getattr(scheduler_service, 'scheduler', None):
                pipeline_service.sync_schedule(scheduler_service.scheduler)
                logger.info(f"Pipeline schedule synced directly after {key} update")

            # Also set Redis flag for worker mode (separate scheduler process)
            from src.api.utils.caching import cache as flask_cache
            if hasattr(flask_cache, 'cache') and hasattr(flask_cache.cache, '_write_client'):
                redis_client = flask_cache.cache._write_client
                redis_client.setex('scheduler:settings_changed', 300, '1')
        except Exception as e:
            logger.warning(f"Failed to sync pipeline schedule after {key} update: {e}")

    # Refresh the dashboard materialized views when a confidence threshold
    # changes so the new high/medium/low boundaries take effect immediately.
    if _is_confidence_threshold(category, key):
        try:
            current_app.dashboard_repository.refresh_materialized_views()
        except Exception as exc:  # best-effort: the save already succeeded
            logger.warning("Threshold saved but MV refresh failed: %s", exc)

    return {
        'message': 'Setting updated successfully',
        'category': category,
        'key': key,
        'value': updated_value,
        'reason': reason
    }


@settings_bp.route('/<category>/<key>/reset', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def reset_setting(category: str, key: str):
    """
    Reset setting to default value from schema.

    Path Parameters:
        category: Settings category
        key: Setting key

    Request Body (optional):
        {
            "reason": "Why this reset is being made"
        }

    Returns:
        200: Setting reset successfully
        404: Setting not found
    """
    settings_manager = current_app.settings_manager
    data = request.get_json() or {}

    reason = data.get('reason', 'Setting reset to default via API')

    # Get user context
    user_id, username = get_current_user_context()

    # Reset setting
    success, error = settings_manager.reset(
        category=category,
        key=key,
        user_id=user_id,
        username=username,
        reason=reason
    )

    if not success:
        if 'not found' in error.lower() or 'not defined' in error.lower():
            raise NotFoundError(error)
        else:
            raise ValidationError(error)

    # Get reset value
    reset_value = settings_manager.get(category, key)

    # Mirror update_setting: resetting a confidence threshold changes the bucket
    # boundaries, so refresh the dashboard materialized views.
    if _is_confidence_threshold(category, key):
        try:
            current_app.dashboard_repository.refresh_materialized_views()
        except Exception as exc:  # best-effort: the reset already succeeded
            logger.warning("Threshold reset but MV refresh failed: %s", exc)

    return {
        'message': 'Setting reset to default successfully',
        'category': category,
        'key': key,
        'value': reset_value
    }


