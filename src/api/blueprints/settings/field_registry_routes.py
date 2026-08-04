# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Field Registry Routes - CRUD for field descriptions in Settings.

Endpoints:
- GET    /api/settings/field-registry                         - list all fields
- PUT    /api/settings/field-registry/<field_name>            - update description/display_name
- POST   /api/settings/field-registry/<field_name>/regenerate - LLM regen of description + synonyms
- POST   /api/settings/synonyms/regenerate                   - start "Regenerate All" (descriptions + synonyms) in background
- GET    /api/settings/synonyms/status                       - background regen progress
- GET    /api/settings/field-descriptions                    - LLM toggle state + provider availability
- PUT    /api/settings/field-descriptions                    - enable/disable LLM field-description generation
"""

import logging
import threading
from flask import Blueprint, current_app, request
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, ValidationError, NotFoundError
from src.api.utils.audit_logger import get_current_user_context
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

field_registry_bp = Blueprint(
    'field_registry', __name__, url_prefix='/api/settings'
)


# Track background synonym-regeneration state. The full rebuild calls the LLM
# once per field (minutes on many-field installs), which blows past the browser
# and gateway request timeouts — so it runs in a background thread and the UI
# polls /synonyms/status. Mirrors admin_embedding's refresh pattern.
_synonym_lock = threading.Lock()
_synonym_status: dict = {'running': False, 'last_result': None}


def _run_synonyms_regen_in_background(app):
    """Regenerate descriptions + synonyms for all extended fields in a
    background thread with app context.

    Runs ``run_full_refresh`` — the "Regenerate All" action — which regenerates
    non-manual extended-field descriptions and rebuilds their synonyms. Core
    (static) fields are left untouched. The ``finally`` wraps the
    ``app_context()`` so ``running`` is always cleared — even if entering the
    context fails — never leaving the job stuck 'running'.
    """
    try:
        with app.app_context():
            result = app.embedding_integration_service.run_full_refresh()
            errors = result.get('errors') if isinstance(result, dict) else None
            if errors:
                # Surface partial failures via the `error` key the UI inspects,
                # so a run that hit errors can't show a green "done" toast.
                _synonym_status['last_result'] = {
                    'error': 'Completed with errors: '
                             + '; '.join(str(e) for e in errors)}
                logger.warning(
                    f"Background full refresh completed with errors: {errors}")
            else:
                _synonym_status['last_result'] = {
                    'ok': True,
                    'message': 'Descriptions and synonyms regenerated'}
                logger.info(
                    "Background full refresh (descriptions + synonyms) complete")
    except Exception as e:
        _synonym_status['last_result'] = {'error': str(e)}
        logger.error(f"Background full refresh failed: {e}")
    finally:
        _synonym_status['running'] = False


@field_registry_bp.route('/field-registry', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def list_fields():
    """List all field registry entries (with per-field synonyms)."""
    from src.services.embedding.synonym_generator import (
        convert_field_name_to_api_format,
    )
    registry = current_app.field_registry_service
    fields = registry.get_all_fields()

    # Per-field aliases come from the pre-generated synonym lookup, inverted
    # to field→[terms]. The service (and its file) may be absent → no synonyms.
    svc = current_app.embedding_integration_service
    synonyms_by_api = svc.get_synonyms_by_api_field() if svc else {}

    return {
        'fields': [
            {
                'id': f.id,
                'field_name': f.field_name,
                'field_type': f.field_type,
                'display_name': f.display_name,
                'description': f.description,
                'description_source': f.description_source,
                'data_source': f.data_source,
                'searchable': f.searchable,
                'synonyms': synonyms_by_api.get(
                    convert_field_name_to_api_format(
                        f.field_name, f.data_source), []),
                'updated_at': f.updated_at.isoformat() if f.updated_at else None,
            }
            for f in fields
        ],
        'total': len(fields),
    }


@field_registry_bp.route('/field-registry/<field_name>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def update_field(field_name: str):
    """Update a field's description and/or display_name."""
    body = request.get_json(silent=True) or {}
    description = body.get('description', '').strip()
    display_name = body.get('display_name', '').strip()

    if not description:
        raise ValidationError('Description cannot be empty')
    if not display_name:
        raise ValidationError('Display name cannot be empty')

    registry = current_app.field_registry_service
    field = registry.get_field_by_name(field_name)
    if not field:
        raise NotFoundError(f'Field "{field_name}" not found')

    registry.update_description(field_name, description, 'manual', display_name=display_name)

    updated = registry.get_field_by_name(field_name)
    return {
        'field_name': updated.field_name,
        'description': updated.description,
        'description_source': updated.description_source,
        'updated_at': updated.updated_at.isoformat() if updated.updated_at else None,
    }


@field_registry_bp.route(
    '/field-registry/<field_name>/regenerate', methods=['POST']
)
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def regenerate_field(field_name: str):
    """Regenerate the LLM description AND synonyms for a single field.

    Description is regenerated first (synonym generation reads it), then the
    field's synonyms are replaced. Core (static) fields are rejected — their
    descriptions and synonyms are curated and never regenerated.
    """
    from src.services.embedding.synonym_generator import (
        convert_field_name_to_api_format,
    )
    registry = current_app.field_registry_service
    field = registry.get_field_by_name(field_name)
    if not field:
        raise NotFoundError(f'Field "{field_name}" not found')
    if field.field_type != 'extended':
        raise ValidationError(
            'Only data-source (extended) fields can be regenerated')

    # A full "Regenerate All" runs in the background and does its own
    # read-modify-write of the shared synonym lookup; refuse a concurrent
    # per-field regen rather than race it (same-process guard).
    if _synonym_status['running']:
        return {'success': False,
                'error': 'A full regeneration is in progress; '
                         'try again shortly.'}, 409

    from src.services.embedding.pipeline_llm import build_pipeline_description_generator
    generator = build_pipeline_description_generator(
        current_app.db_adapter, current_app.chat_providers_service,
    )
    if not generator.is_available():
        return {'success': False, 'error': 'No LLM provider configured'}, 503

    result = generator.generate_description(
        field_name=field_name,
        context=f"Data source: {field.data_source}" if field.data_source else None,
    )
    if not result.success:
        return {'success': False,
                'error': f'LLM generation failed: {result.error}'}, 500
    registry.update_description(field_name, result.description, 'llm')

    # Synonyms — replace this field's entries using the fresh description.
    # Non-fatal: a synonym failure must not discard the regenerated description.
    synonyms: list = []
    svc = current_app.embedding_integration_service
    if svc:
        try:
            svc.regenerate_field_synonyms(field_name)
        except Exception as e:
            logger.warning(
                f"Field synonym regeneration failed for '{field_name}': {e}")
        # get_synonyms_by_api_field() degrades to {} on any error, so the
        # lookup below can't raise.
        api_name = convert_field_name_to_api_format(
            field_name, field.data_source)
        synonyms = svc.get_synonyms_by_api_field().get(api_name, [])

    # update_description just set this (extended) field's description to
    # result.description with source 'llm' — return those directly rather than
    # re-reading the row.
    return {
        'field_name': field_name,
        'description': result.description,
        'description_source': 'llm',
        'synonyms': synonyms,
    }


@field_registry_bp.route('/synonyms/regenerate', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def regenerate_synonyms():
    """Start "Regenerate All" (descriptions + synonyms) in the background.

    Regenerates non-manual descriptions and rebuilds synonyms for every
    extended field — Core (static) fields are left untouched. This calls the
    LLM once per field, which can run for minutes — past the browser and
    gateway request timeouts. So we kick it off in a background thread and
    return immediately; the client polls /synonyms/status for completion.
    """
    service = current_app.embedding_integration_service
    if not service:
        return {'success': False,
                'error': 'Embedding integration service not available'}, 503

    with _synonym_lock:
        if _synonym_status['running']:
            return {'status': 'already_running',
                    'message': 'Synonym regeneration is already in progress'}
        _synonym_status['running'] = True
        _synonym_status['last_result'] = None

    app = current_app._get_current_object()
    try:
        threading.Thread(
            target=_run_synonyms_regen_in_background, args=(app,), daemon=True,
        ).start()
    except Exception as e:
        with _synonym_lock:
            _synonym_status['running'] = False
        logger.error(f"Failed to start synonym regeneration thread: {e}")
        return {'success': False,
                'error': 'Failed to start synonym regeneration'}, 500

    return {'status': 'started',
            'message': 'Synonym regeneration running in background. '
                       'Poll /synonyms/status for progress.'}


@field_registry_bp.route('/synonyms/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def synonyms_regen_status():
    """Report background synonym-regeneration progress."""
    return {
        'running': _synonym_status['running'],
        'last_result': _synonym_status['last_result'],
    }


@field_registry_bp.route('/field-descriptions', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_field_descriptions_config():
    """Toggle state + whether a chat LLM provider is usable for generation."""
    from src.services.embedding.pipeline_llm import build_pipeline_description_generator
    enabled = bool(current_app.settings_manager.get(
        'embedding', 'llm_descriptions_enabled', True))
    generator = build_pipeline_description_generator(
        current_app.db_adapter, current_app.chat_providers_service,
    )
    try:
        active = current_app.chat_providers_service.get_active_provider()
    except Exception:
        active = ''
    return {
        'enabled': enabled,
        'provider_available': generator.is_available(),
        'active_provider': active,
    }


@field_registry_bp.route('/field-descriptions', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def set_field_descriptions_enabled():
    """Enable/disable automatic LLM field-description generation."""
    data = request.get_json() or {}
    if 'enabled' not in data:
        raise ValidationError('Missing required field: enabled')
    enabled = bool(data['enabled'])
    user_id, username = get_current_user_context()
    ok, err = current_app.settings_manager.set(
        'embedding', 'llm_descriptions_enabled', enabled,
        user_id=user_id, username=username,
        reason=data.get('reason', 'Field-description LLM toggle updated via API'),
        ip_address=request.remote_addr,
    )
    if not ok:
        raise ValidationError(err or 'Failed to update setting')
    return {'enabled': enabled}
