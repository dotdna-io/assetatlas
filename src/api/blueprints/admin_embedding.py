# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Admin Embedding Blueprint

Endpoints for managing the embedding/synonym system:
- POST /api/admin/embedding/refresh - trigger incremental or full update
- GET  /api/admin/embedding/status  - system status overview
"""

import logging
import threading
from flask import Blueprint, current_app, request
from flask_jwt_extended import jwt_required, get_jwt

from src.api.middleware.error_handler import handle_errors
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

admin_embedding_bp = Blueprint(
    'admin_embedding', __name__, url_prefix='/api/admin/embedding'
)

# Track background refresh state
_refresh_lock = threading.Lock()
_refresh_status = {'running': False, 'last_result': None}


def _run_refresh_in_background(app, mode):
    """Run embedding refresh in a background thread with app context.

    The ``finally`` wraps the ``app_context()`` so ``running`` is always cleared
    — even if entering the context fails — never leaving a refresh stuck 'running'.
    """
    try:
        with app.app_context():
            service = app.embedding_integration_service
            if mode == 'full':
                result = service.run_full_refresh()
            else:
                result = service.run_incremental_update()
            _refresh_status['last_result'] = result
            logger.info(f"Background embedding refresh ({mode}) complete: {result}")
    except Exception as e:
        _refresh_status['last_result'] = {'mode': mode, 'error': str(e)}
        logger.error(f"Background embedding refresh ({mode}) failed: {e}")
    finally:
        _refresh_status['running'] = False


@admin_embedding_bp.route('/refresh', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def refresh_embeddings():
    """Trigger embedding system refresh (runs in background)."""
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'incremental')

    if mode == 'full':
        claims = get_jwt()
        role = claims.get('role', '')
        if role != 'super_admin':
            return {'success': False,
                    'error': 'Full refresh requires super_admin role'}, 403

    service = current_app.embedding_integration_service
    if service is None:
        return {'success': False,
                'error': 'Embedding integration service not available'}, 503

    with _refresh_lock:
        if _refresh_status['running']:
            return {'status': 'already_running', 'message': 'Refresh is already in progress'}
        _refresh_status['running'] = True
        _refresh_status['last_result'] = None

    app = current_app._get_current_object()
    try:
        threading.Thread(
            target=_run_refresh_in_background, args=(app, mode), daemon=True
        ).start()
    except Exception as e:
        with _refresh_lock:
            _refresh_status['running'] = False
        logger.error(f"Failed to start embedding refresh thread: {e}")
        return {'success': False, 'error': 'Failed to start embedding refresh'}, 500

    return {'status': 'started', 'mode': mode, 'message': 'Refresh running in background. Check /status for progress.'}


@admin_embedding_bp.route('/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def embedding_status():
    """Get embedding system status."""
    service = current_app.embedding_integration_service
    if service is None:
        return {'success': False,
                'error': 'Embedding integration service not available'}, 503

    status = service.get_status()
    status['refresh'] = {
        'running': _refresh_status['running'],
        'last_result': _refresh_status['last_result'],
    }
    return status
