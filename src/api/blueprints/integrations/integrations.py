# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integrations CRUD Blueprint

Handles Create, Read, Update, Delete operations for integrations,
plus connection testing, sampling, and operational controls.

Endpoints:
    GET    /api/integrations                      - List integrations
    POST   /api/integrations                      - Create integration
    GET    /api/integrations/<id>                 - Get integration details
    PUT    /api/integrations/<id>                 - Update integration
    DELETE /api/integrations/<id>                 - Delete integration
    POST   /api/integrations/<id>/test            - Test connection
    POST   /api/integrations/<id>/sample          - Get sample data
    GET    /api/integrations/<id>/schema          - Get field schema
    POST   /api/integrations/<id>/run             - Manual run
    POST   /api/integrations/<id>/enable          - Enable integration
    POST   /api/integrations/<id>/disable         - Disable integration
    GET    /api/integrations/stats                - Get statistics
    GET    /api/integrations/logos                - List available logos
    POST   /api/integrations/logos/upload         - Upload a custom logo
    GET    /api/integrations/logos/file/<name>    - Serve an uploaded logo
    GET    /api/integrations/<id>/insights        - Data quality insights
    GET    /api/integrations/<id>/timeline        - Asset added/removed timeline
"""

import logging
import os
import re
import tempfile
import threading
from flask import Blueprint, request, jsonify, current_app, send_from_directory, abort
from flask_jwt_extended import jwt_required, current_user
from uuid import UUID

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError, ConflictError
from src.auth.middleware.rbac import require_admin
from src.modules.integrations.services.transformation_validator import validate_field_mapping
from src.modules.integrations.services.logo_processor import (
    LogoUploadError,
    process_upload as process_logo_upload,
)
from src.modules.integrations.models import IntegrationStatus
from src.api.utils.audit_logger import AuditLogger, get_current_user_context
from src.api.utils.audit_vocab import AuditAction, AuditResource
from src.api.modules.settings.settings_schema import get_confidence_thresholds

logger = logging.getLogger(__name__)

# Create blueprint
integrations_bp = Blueprint('integrations_crud', __name__)


# =============================================================================
# Integration CRUD
# =============================================================================

@integrations_bp.route('', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def list_integrations():
    """
    List all configured integrations.

    Query parameters:
        status: Filter by status (enabled/disabled/error)
        integration_type: Filter by connector type
        exclude_manual: Exclude manual/file-based test integrations (default: true)

    Returns:
        200: List of integrations (credentials hidden)
    """
    integration_service = current_app.integration_service
    # Get query parameters
    status = request.args.get('status')
    integration_type = request.args.get('integration_type')
    exclude_manual = request.args.get('exclude_manual', 'true').lower() == 'true'

    # Convert status string to enum
    status_enum = None
    if status:
        try:
            status_enum = IntegrationStatus(status.lower())
        except ValueError:
            raise ValidationError(f'Status must be one of: enabled, disabled, error')

    # Get integrations
    integrations = integration_service.list_integrations(
        status=status_enum,
        integration_type=integration_type
    )

    # Filter out manual/file-based integrations if requested
    if exclude_manual:
        integrations = [
            i for i in integrations
            if not (i.config and isinstance(i.config, dict) and 'file_source' in i.config)
        ]

    # Fetch latest batch data for ALL integrations in a single query (eliminates N+1 problem)
    latest_batches = integration_service.batch_repository.get_latest_batches_for_all_integrations()

    # Enrich integrations with latest batch data
    enriched_integrations = []
    for integration in integrations:
        integration_dict = integration.to_dict()

        # Remove sensitive/large config data from list view for performance
        # Full config is available in GET /integrations/{id}
        if 'config' in integration_dict:
            # Keep only essential metadata, remove large/sensitive data
            config = integration_dict.get('config', {})
            if isinstance(config, dict):
                # Remove file_data (large base64 content) and sensitive credentials
                sanitized_config = {}
                for key, value in config.items():
                    # Skip large binary data and sensitive credentials
                    if key in ('file_data', 'client_secret', 'api_key', 'password', 'token'):
                        continue
                    # Skip values that are too large (likely file content)
                    if isinstance(value, str) and len(value) > 1000:
                        continue
                    sanitized_config[key] = value
                integration_dict['config'] = sanitized_config
            else:
                del integration_dict['config']

        # Look up batch info from the pre-fetched data
        integration_id_str = str(integration.id)
        if integration_id_str in latest_batches:
            batch_info = latest_batches[integration_id_str]
            integration_dict['latest_batch_records'] = batch_info['raw_records_count']
            integration_dict['latest_batch_started_at'] = batch_info['started_at']
            integration_dict['latest_batch_status'] = batch_info['status']
            integration_dict['latest_batch_assets'] = batch_info['assets_created_count']
            integration_dict['last_sync_at'] = batch_info.get('last_sync_at')
            integration_dict['last_run_at'] = batch_info.get('last_run_at')
        else:
            integration_dict['latest_batch_records'] = 0
            integration_dict['latest_batch_started_at'] = None
            integration_dict['latest_batch_status'] = None
            integration_dict['latest_batch_assets'] = 0
            integration_dict['last_sync_at'] = None
            integration_dict['last_run_at'] = None

        enriched_integrations.append(integration_dict)

    return jsonify({
        'success': True,
        'count': len(enriched_integrations),
        'integrations': enriched_integrations
    }), 200


@integrations_bp.route('', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def create_integration():
    """
    Create a new integration.

    Request body:
        {
            "name": "Production Entra ID",
            "integration_type": "azure_ad",
            "config": {
                "tenant_id": "xxx",
                "client_id": "yyy",
                "client_secret": "zzz"
            },
            "field_mapping": {}
        }

    Returns:
        201: Created integration
        400: Invalid request
        409: Integration name already exists
    """
    integration_service = current_app.integration_service
    data = request.get_json()

    # Log incoming request for debugging (with sensitive fields redacted)
    safe_data = {k: v for k, v in data.items() if k != 'config'}
    safe_config = {}
    if 'config' in data:
        # Redact sensitive fields from config
        sensitive_fields = ['api_token', 'client_secret', 'password', 'api_key', 'secret', 'token']
        for key, value in data['config'].items():
            if any(field in key.lower() for field in sensitive_fields):
                safe_config[key] = '***REDACTED***'
            else:
                safe_config[key] = value
        safe_data['config'] = safe_config

    logger.info(f"Received create integration request: {safe_data}")
    logger.info(f"Field mapping received: {data.get('field_mapping', {})}")

    # Validate required fields
    required = ['name', 'integration_type', 'config']
    missing = [field for field in required if field not in data]
    if missing:
        raise ValidationError(f'Missing required fields: {", ".join(missing)}')

    # Validate field_mapping if provided (includes transformations)
    field_mapping = data.get('field_mapping', {})
    if field_mapping:
        is_valid, validation_errors = validate_field_mapping(field_mapping)
        if not is_valid:
            logger.warning(f"Field mapping validation failed: {validation_errors}")
            raise ValidationError(f'Invalid field mapping: {validation_errors}')

    # Create integration
    try:
        integration = integration_service.create_integration(
            name=data['name'],
            integration_type=data['integration_type'],
            config=data['config'],
            field_mapping=data.get('field_mapping', {}),
            transformations=data.get('transformations', {}),
            data_filters=data.get('data_filters', []),
            preserve_fields=data.get('preserve_fields', []),
            category=data.get('data_source_category'),
            logo=data.get('logo'),
            description=data.get('description'),
            last_connection_test_status=data.get('last_connection_test_status'),
            last_connection_test_message=data.get('last_connection_test_message'),
            source_method=data.get('source_method', 'direct')
        )

        logger.info(f"Created integration: {integration.name} (ID: {integration.id})")

        # Audit log: Integration created
        user_id, username = get_current_user_context()
        AuditLogger.log_config_change(
            resource=AuditResource.INTEGRATION,
            resource_id=str(integration.id),
            action=AuditAction.CREATE,
            user_id=user_id,
            username=username,
            details={
                'name': integration.name,
                'integration_type': integration.integration_type,
                'category': integration.category,
                'logo': integration.logo,
                'description': integration.description,
                'config': safe_config  # Sanitized config without secrets
            },
            reason='New integration created'
        )

        return jsonify({
            'success': True,
            'message': 'Integration created successfully',
            'integration': integration.to_dict()
        }), 201

    except ValueError as e:
        raise ValidationError(f'Invalid integration type: {str(e)}')
    except Exception as e:
        # Check for duplicate name error
        if 'unique constraint' in str(e).lower() or 'already exists' in str(e).lower():
            raise ConflictError(f"An integration named '{data['name']}' already exists. Please choose a different name.")
        raise


@integrations_bp.route('/<uuid:integration_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration(integration_id: UUID):
    """
    Get integration details.

    Query parameters:
        include_credentials: Include decrypted credentials (default: false)

    Returns:
        200: Integration details
        404: Integration not found
    """
    integration_service = current_app.integration_service
    include_credentials = request.args.get('include_credentials', 'false').lower() == 'true'

    integration = integration_service.get_integration(
        integration_id,
        include_credentials=include_credentials
    )

    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    integration_dict = integration.to_dict()

    # Override the legacy integrations.last_sync_at column with the canonical
    # pipeline-completion timestamp, and add last_run_at (last successful
    # connector ingestion).
    timestamps = (
        integration_service.batch_repository
        .get_latest_sync_and_run_for_integration(integration.id)
    )
    integration_dict['last_sync_at'] = timestamps['last_sync_at']
    integration_dict['last_run_at'] = timestamps['last_run_at']

    return jsonify({
        'success': True,
        'integration': integration_dict,
    }), 200


@integrations_bp.route('/<uuid:integration_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
def update_integration(integration_id: UUID):
    """
    Update integration.

    Request body can contain any updatable fields:
        {
            "name": "New name",
            "config": {...},
            "field_mapping": {...},
            "status": "disabled"
        }

    Returns:
        200: Updated integration
        404: Integration not found
    """
    integration_service = current_app.integration_service
    data = request.get_json()

    # Get old integration for audit trail (before update)
    old_integration = integration_service.get_integration(integration_id, include_credentials=False)
    if not old_integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Validate field_mapping if provided in update data (includes transformations)
    field_mapping = data.get('field_mapping')
    if field_mapping is not None:  # Check for None explicitly (empty dict {} is valid)
        is_valid, validation_errors = validate_field_mapping(field_mapping)
        if not is_valid:
            logger.warning(f"Field mapping validation failed for integration {integration_id}: {validation_errors}")
            raise ValidationError(f'Invalid field mapping: {validation_errors}')

    integration = integration_service.update_integration(integration_id, data)

    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    logger.info(f"Updated integration ID: {integration_id}")

    # Audit log: Integration updated - track what changed
    user_id, username = get_current_user_context()
    changes = {}

    # Track changes in updatable fields. preserve_fields (DSS classification) is
    # tracked here too: toggling DSS on a framework-named field leaves
    # field_mapping byte-identical ({field:field} either way) and only flips
    # preserve_fields, so without this the change would leave no audit record (#809).
    trackable_fields = ['name', 'status', 'category', 'logo', 'description', 'schedule', 'preserve_fields']
    for field in trackable_fields:
        if field in data:
            old_value = getattr(old_integration, field, None)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {'old': old_value, 'new': new_value}

    # Track config changes (sanitize secrets)
    if 'config' in data:
        changes['config'] = {'old': 'updated', 'new': 'updated'}  # Don't log actual config values

    # Track field_mapping changes
    if 'field_mapping' in data:
        changes['field_mapping'] = {'old': 'updated', 'new': 'updated'}

    # Only log if there were actual changes
    if changes:
        AuditLogger.log_config_change(
            resource=AuditResource.INTEGRATION,
            resource_id=str(integration_id),
            action=AuditAction.UPDATE,
            user_id=user_id,
            username=username,
            changes=changes,
            reason='Integration configuration updated'
        )

    return jsonify({
        'success': True,
        'message': 'Integration updated successfully',
        'integration': integration.to_dict()
    }), 200


@integrations_bp.route('/<uuid:integration_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def delete_integration(integration_id: UUID):
    """
    Delete integration.

    Returns:
        200: Integration deleted
        404: Integration not found
    """
    integration_service = current_app.integration_service

    # Get integration details before deletion for audit trail
    integration = integration_service.get_integration(integration_id, include_credentials=False)
    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Store details for audit log
    integration_name = integration.name
    integration_type = integration.integration_type
    integration_category = integration.category

    # Delete the integration
    deleted = integration_service.delete_integration(integration_id)

    if not deleted:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    logger.info(f"Deleted integration ID: {integration_id}")

    # Audit log: Integration deleted (CRITICAL severity)
    user_id, username = get_current_user_context()
    AuditLogger.log_data_operation(
        resource=AuditResource.INTEGRATION,
        resource_id=str(integration_id),
        action=AuditAction.DELETE,
        user_id=user_id,
        username=username,
        details={
            'name': integration_name,
            'integration_type': integration_type,
            'category': integration_category
        },
        reason='Integration deleted by admin'
    )

    return jsonify({
        'success': True,
        'message': 'Integration deleted successfully'
    }), 200


# =============================================================================
# Integration Operations
# =============================================================================

@integrations_bp.route('/<uuid:integration_id>/test', methods=['GET', 'POST'])
@jwt_required()
@require_admin()
@handle_errors
def test_connection(integration_id: UUID):
    """
    Test integration connection.

    Methods:
        GET: test the saved config.
        POST: optionally test a candidate config from the wizard. Body
              ``{"config": {...}}`` — non-sensitive keys override the saved
              config; sensitive keys fall back to the saved decrypted value
              if absent/empty/still-encrypted in the candidate. The result
              is NOT persisted on the integration row (it describes unsaved
              edits, not the running config).

    Returns:
        200: Test result (may contain success: false if test failed)

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    candidate_config = None
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        candidate_config = body.get('config')
    result = integration_service.test_connection(
        integration_id, candidate_config=candidate_config
    )
    status_code = 200 if result['success'] else 400
    return jsonify(result), status_code


@integrations_bp.route('/<uuid:integration_id>/sample', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def get_sample_data(integration_id: UUID):
    """
    Get sample data from integration.

    Request body (optional):
        {
            "limit": 5,
            "config": {...}  // candidate config from wizard (edit mode);
                             // sensitive keys fall back to the saved value
        }

    Returns:
        200: Sample data

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    data = request.get_json() or {}
    limit = data.get('limit', 5)
    candidate_config = data.get('config')

    result = integration_service.get_sample_data(
        integration_id, limit=limit, candidate_config=candidate_config
    )
    status_code = 200 if result['success'] else 400
    return jsonify(result), status_code


@integrations_bp.route('/<uuid:integration_id>/schema', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_field_schema(integration_id: UUID):
    """
    Get field schema for integration (for mapping configuration).

    Returns:
        200: Field schema

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    result = integration_service.get_field_schema(integration_id)
    status_code = 200 if result['success'] else 400
    return jsonify(result), status_code


@integrations_bp.route('/<uuid:integration_id>/run', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def run_integration(integration_id: UUID):
    """
    Manually run integration to collect data.

    Starts the sync in a background thread and returns 202 immediately.
    The UI polls GET /integrations/<id>/batches to track progress.

    Returns:
        202: Accepted (sync running in background)
        400: Validation error (not found, disabled, already running)
    """
    integration_service = current_app.integration_service

    # Pre-flight checks (fast, synchronous)
    integration = integration_service.get_integration(integration_id, include_credentials=False)
    if not integration:
        return jsonify({'success': False, 'message': 'Integration not found', 'error': 'NOT_FOUND'}), 400
    if not integration.is_enabled():
        return jsonify({'success': False, 'message': 'Integration is not enabled', 'error': 'DISABLED'}), 400

    # Run the sync in a background thread so the HTTP request returns
    # immediately.  The advisory lock inside run_integration() prevents
    # concurrent runs.  We need a copy of the app for the thread to
    # push an application context.
    app = current_app._get_current_object()

    def _run_in_background():
        with app.app_context():
            try:
                app.integration_service.run_integration(integration_id)
            except Exception:
                logger.exception("Background integration run failed for %s", integration_id)

    thread = threading.Thread(
        target=_run_in_background,
        name=f"integration-run-{integration_id}",
        daemon=True,
    )
    thread.start()

    return jsonify({
        'success': True,
        'message': f'Integration "{integration.name}" sync started in background',
        'integration_id': str(integration_id),
    }), 202


@integrations_bp.route('/<uuid:integration_id>/enable', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
def enable_integration(integration_id: UUID):
    """
    Enable integration.

    Returns:
        200: Integration enabled
        404: Integration not found
    """
    integration_service = current_app.integration_service

    # Get integration name for audit trail
    integration = integration_service.get_integration(integration_id, include_credentials=False)
    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    enabled = integration_service.enable_integration(integration_id)

    if not enabled:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Audit log: Integration enabled
    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.INTEGRATION,
        resource_id=str(integration_id),
        action=AuditAction.ENABLE,
        user_id=user_id,
        username=username,
        changes={'status': {'old': 'disabled', 'new': 'enabled'}},
        reason=f'Integration "{integration.name}" enabled by admin'
    )

    return jsonify({
        'success': True,
        'message': 'Integration enabled successfully'
    }), 200


@integrations_bp.route('/<uuid:integration_id>/disable', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
def disable_integration(integration_id: UUID):
    """
    Disable integration.

    Returns:
        200: Integration disabled
        404: Integration not found
    """
    integration_service = current_app.integration_service

    # Get integration name for audit trail
    integration = integration_service.get_integration(integration_id, include_credentials=False)
    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    disabled = integration_service.disable_integration(integration_id)

    if not disabled:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Audit log: Integration disabled
    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.INTEGRATION,
        resource_id=str(integration_id),
        action=AuditAction.DISABLE,
        user_id=user_id,
        username=username,
        changes={'status': {'old': 'enabled', 'new': 'disabled'}},
        reason=f'Integration "{integration.name}" disabled by admin'
    )

    return jsonify({
        'success': True,
        'message': 'Integration disabled successfully'
    }), 200


# =============================================================================
# Statistics
# =============================================================================

@integrations_bp.route('/stats', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_stats():
    """
    Get integration statistics.

    Returns:
        200: Statistics
    """
    integration_service = current_app.integration_service
    stats = integration_service.get_integration_stats()

    return jsonify({
        'success': True,
        'stats': stats
    }), 200


# =============================================================================
# Logo Management
# =============================================================================

@integrations_bp.route('/logo-map', methods=['GET'])
@jwt_required()
@handle_errors
def get_logo_map():
    """
    Get a mapping of integration names to their logo paths.

    This endpoint is used by various pages (explorer, alerts, etc.) to resolve
    logos for data source names without needing to join with integrations table.

    Returns:
        200: {
            "success": true,
            "logoMap": {
                "Integration Name": "/logos/filename.png",
                ...
            }
        }

    Note: ``integration.logo`` persists in two shapes — a bare filename
    for bundled-library logos ("jamf.png") and a full path for
    user-uploaded customs ("/api/integrations/logos/file/<sha>.png").
    Bundled values get the ``/logos/`` prefix; uploaded values are
    already absolute paths and pass through untouched.
    """
    integration_service = current_app.integration_service

    try:
        integrations = integration_service.list_integrations()

        logo_map = {}
        for integration in integrations:
            if integration.logo:
                logo_map[integration.name] = (
                    integration.logo
                    if integration.logo.startswith('/')
                    else f"/logos/{integration.logo}"
                )

        return jsonify({
            'success': True,
            'logoMap': logo_map
        }), 200

    except Exception as e:
        logger.error(f"Error getting logo map: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Failed to get logo map: {str(e)}'
        }), 500


_LOGO_IMAGE_EXTS = ('.png', '.svg', '.webp', '.jpg', '.jpeg')

# Uploaded logos use content-addressed filenames (SHA-256 hex + extension).
# Anything outside that shape is rejected at the file-serving boundary so a
# crafted path like "../config.yml" can never reach send_from_directory.
_UPLOADED_LOGO_FILENAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|svg)$")


def _bundled_logos_dir() -> str:
    """Path to the image-baked bundled logos directory."""
    # Blueprint file is at: src/api/blueprints/integrations/integrations.py
    # ui/prod/public/logos is reached by walking up 5 levels to repo root.
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    return os.path.join(base_dir, 'ui', 'prod', 'public', 'logos')


def _uploaded_logos_dir() -> str:
    """Path to the writable uploaded-logos directory (volume-mounted in prod)."""
    return current_app.config['UPLOADED_LOGOS_DIR']


def _list_logo_filenames(directory: str) -> list:
    """Return sorted list of image filenames in a directory, or [] if missing."""
    if not directory or not os.path.isdir(directory):
        return []
    return sorted(
        f for f in os.listdir(directory)
        if not f.startswith('.') and f.lower().endswith(_LOGO_IMAGE_EXTS)
    )


@integrations_bp.route('/logos', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def list_available_logos():
    """
    Get available logos (bundled library + previously-uploaded customs).

    Bundled logos are baked into the image at ``ui/prod/public/logos/`` and
    served by the UI container at ``/logos/<name>``. Uploaded logos live on
    a persistent volume and are served by this API at
    ``/api/integrations/logos/file/<name>``.

    Returns:
        200: ``{"success": true, "logos": [{filename, path, source}, ...]}``
            * ``filename`` — basename on disk
            * ``path`` — URL the UI should set as ``<img src=…>``
            * ``source`` — ``"library"`` for bundled, ``"uploaded"`` otherwise

        Backwards-compat clients that read ``logos`` as ``string[]``
        receive an extra ``filenames`` array of bundled-library filenames
        only (preserves the v1 shape so older callers keep working).
    """
    bundled = _list_logo_filenames(_bundled_logos_dir())
    uploaded = _list_logo_filenames(_uploaded_logos_dir())

    items = [
        {'filename': f, 'path': f'/logos/{f}', 'source': 'library'}
        for f in bundled
    ] + [
        {
            'filename': f,
            'path': f'/api/integrations/logos/file/{f}',
            'source': 'uploaded',
        }
        for f in uploaded
    ]

    return jsonify({
        'success': True,
        'logos': items,
        # Legacy field — pre-issue-637 callers expected logos: string[].
        # New consumers should ignore this.
        'filenames': bundled,
    }), 200


@integrations_bp.route('/logos/upload', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def upload_logo():
    """
    Upload a custom logo.

    Accepts ``multipart/form-data`` with a single ``file`` field. The file
    is validated (size, format), re-encoded to a normalised PNG (rasters)
    or sanitised SVG, and stored under the uploaded-logos directory using
    a SHA-256 content-hash filename so identical uploads dedupe naturally.

    Returns:
        201: ``{"success": true, "logo": {filename, path, source}}``
        400: Validation failure (size, format, sanitisation).
    """
    if 'file' not in request.files:
        raise ValidationError(
            "No file uploaded. Send a multipart/form-data POST with a 'file' field."
        )
    storage = request.files['file']
    if not storage or not storage.filename:
        raise ValidationError("Uploaded file is empty.")

    raw_bytes = storage.read()
    max_bytes = current_app.config.get('UPLOADED_LOGO_MAX_BYTES', 1024 * 1024)

    try:
        processed = process_logo_upload(
            filename=storage.filename,
            raw_bytes=raw_bytes,
            max_bytes=max_bytes,
        )
    except LogoUploadError as exc:
        # All processor failures map cleanly to 400; the message is
        # already user-facing.
        raise ValidationError(str(exc)) from exc

    upload_dir = _uploaded_logos_dir()
    os.makedirs(upload_dir, exist_ok=True)
    target_path = os.path.join(upload_dir, processed.filename)

    # Idempotent — duplicate uploads collapse onto the same hashed path.
    if not os.path.exists(target_path):
        # Atomic write to a per-request unique temp file in the same dir,
        # then os.replace onto the target. Using a deterministic
        # `<sha>.png.part` would race when two callers upload identical
        # bytes simultaneously: both miss the existence check, both open
        # the same temp path (the second truncates the first), and the
        # second os.replace then fails because the first already
        # consumed the .part. NamedTemporaryFile guarantees a unique
        # name; os.replace is atomic on POSIX.
        fd, tmp_path = tempfile.mkstemp(
            prefix=processed.filename + '.',
            suffix='.part',
            dir=upload_dir,
        )
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(processed.content)
            os.replace(tmp_path, target_path)
        except Exception:
            # Best-effort cleanup if we crashed before the rename.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    user_id, username = get_current_user_context()
    logger.info(
        "Logo uploaded by %s: %s (%d bytes)",
        username, processed.filename, len(processed.content),
    )
    AuditLogger.log_config_change(
        resource=AuditResource.INTEGRATION_LOGO,
        resource_id=processed.filename,
        action=AuditAction.CREATE,
        user_id=user_id,
        username=username,
        details={
            'filename': processed.filename,
            'mime_type': processed.mime_type,
            'bytes': len(processed.content),
            'original_filename': storage.filename,
        },
        reason='Custom logo uploaded',
    )

    return jsonify({
        'success': True,
        'logo': {
            'filename': processed.filename,
            'path': f'/api/integrations/logos/file/{processed.filename}',
            'source': 'uploaded',
        },
    }), 201


@integrations_bp.route('/logos/file/<path:filename>', methods=['GET'])
def serve_uploaded_logo(filename: str):
    """
    Serve an uploaded logo file.

    Auth-free on purpose: ``<img src=…>`` requests don't carry the
    Authorization header, and the served bytes are non-sensitive vendor
    icons. Path-traversal is blocked at three layers: regex (must look
    like a content-hash filename), Werkzeug's ``send_from_directory``
    (rejects ``..`` segments), and the directory existence check.

    Note: intentionally NOT decorated with ``@handle_errors`` — that
    wrapper turns ``abort(404)`` into a 500 because HTTPException is
    caught by its bottom ``except Exception`` arm. Static file serving
    doesn't need API error wrapping; Flask's built-in 404 handler is
    correct here.
    """
    if not _UPLOADED_LOGO_FILENAME_RE.match(filename):
        abort(404)

    upload_dir = _uploaded_logos_dir()
    if not os.path.isdir(upload_dir):
        abort(404)

    response = send_from_directory(
        upload_dir,
        filename,
        max_age=60 * 60 * 24 * 30,  # content-addressed → cache for 30 days
    )
    # Hashed filename = immutable resource. Tell caches they can hold it
    # forever without revalidation.
    response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
    # Disable direct_passthrough so middleware that introspects the
    # response body (slow-request logger, audit middleware) doesn't blow
    # up with "implicit sequence conversion ... direct passthrough mode".
    # Logos are small (≤256x256 PNG, ≤1 MB SVG), so loading them into
    # memory once is harmless.
    response.direct_passthrough = False
    return response


# =============================================================================
# Integration Insights & Timeline
# =============================================================================

@integrations_bp.route('/<uuid:integration_id>/insights', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_insights(integration_id: UUID):
    """
    Get data quality insights for a specific integration.

    The response includes:
      - ``indicators``: stale_assets, missing_attributes,
        conflicting_attributes, missing_expected_assets
      - ``posture_scores``: ``visibility``, ``quality``, ``completeness``
        as integers in [0, 100].
        - ``visibility`` = 100 - stale_pct (stale = last_seen older than
          30 days). 0 if the source reports no assets.
        - ``quality`` = weighted confidence avg over assets reported by
          this source: ``(100*high + 50*medium + 0*low) / total``.
          Bucket boundaries: high >= 0.70, medium >= 0.45, low < 0.45.
        - ``completeness`` = mean ``coverage_pct`` over the categories
          this source reports any assets for. Categories the source
          doesn't touch are ignored, not penalised.

    Returns:
        200: Data quality insights
        404: Integration not found
    """
    service = current_app.integration_service
    medium, high = get_confidence_thresholds(current_app.settings_manager)
    result = service.get_insights(integration_id, medium=medium, high=high)

    if result.get('error') == 'NOT_FOUND':
        raise NotFoundError(result.get('message', 'Integration not found'))

    return jsonify(result), 200


@integrations_bp.route('/<uuid:integration_id>/timeline', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_timeline(integration_id: UUID):
    """
    Get asset added/removed timeline for a specific integration.

    Query params:
        days: Look-back window in days (1–365, default 30). Out-of-range
            values are clamped; non-integer values fall back to the default.

    Returns:
        200: Asset timeline data
        404: Integration not found
    """
    days = request.args.get('days', default=30, type=int)
    if days is None:
        days = 30
    days = max(1, min(365, days))

    service = current_app.integration_service
    result = service.get_timeline(integration_id, days=days)

    if result.get('error') == 'NOT_FOUND':
        raise NotFoundError(result.get('message', 'Integration not found'))

    return jsonify(result), 200
