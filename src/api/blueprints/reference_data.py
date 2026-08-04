# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reference Data admin endpoints."""

import logging
import os
from flask import Blueprint, current_app, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from src.api.middleware.error_handler import handle_errors
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin
from src.modules.reference_data.sources import (
    GitHubReleaseSource,
    UploadedBytesSource,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

# Forks must set REFERENCE_BUNDLE_REPO to their own bundle repo
# (e.g. "your-org/assetatlas-reference-bundles"). The placeholder below is
# intentionally a non-existent slug so a misconfigured deploy fails loudly
# at the upstream-fetch step rather than silently pulling from someone else's
# bundle repo.
DEFAULT_BUNDLE_REPO = '<your-org>/assetatlas-reference-bundles'
BUNDLE_REPO = os.getenv('REFERENCE_BUNDLE_REPO', DEFAULT_BUNDLE_REPO)

reference_data_bp = Blueprint('reference_data', __name__,
                              url_prefix='/api/reference-data')


@reference_data_bp.route('/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_status():
    return current_app.reference_data_service.get_status()


@reference_data_bp.route('/check', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def check_for_updates():
    """Read-only: describe what would be applied if the admin clicks Apply."""
    try:
        return current_app.reference_data_service.check_upstream(repo=BUNDLE_REPO)
    except UpstreamUnavailableError as e:
        # "Upstream unreachable" is a normal check outcome, not a server fault:
        # the request was processed and the answer is "couldn't reach upstream".
        # Return 200 so the UI renders it via its onSuccess `upstream_unavailable`
        # branch instead of surfacing a raw 502 Bad Gateway to the user. Log it
        # so the outcome still leaves a server-side trace — a 200 wouldn't show
        # up in 5xx metrics/logs, and nothing below this catch logs the failure.
        logger.warning("reference-data upstream check unavailable: %s", e)
        return {'status': 'upstream_unavailable', 'message': str(e)}, 200


@reference_data_bp.route('/apply-from-upstream', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def apply_from_upstream():
    """Apply a release fetched from the public bundle repo. Body: {version?: str}.

    Without `version`, applies the latest release. With `version`, applies that
    specific release — used for rolling back to a known-good prior version.
    """
    body = request.get_json(silent=True) or {}
    version = body.get('version') or None
    src = GitHubReleaseSource(repo=BUNDLE_REPO, version=version)
    try:
        result = current_app.reference_data_service.apply_bundle(
            src, initiated_by=get_jwt_identity(),
        )
    except UpstreamUnavailableError as e:
        return {'status': 'upstream_unavailable', 'message': str(e)}, 502
    return result.model_dump(mode='json')


@reference_data_bp.route('/upload', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def upload_bundle():
    f = request.files.get('bundle')
    if f is None:
        return {'error': 'multipart field "bundle" required'}, 400
    data = f.read()
    if not data:
        return {'error': 'empty file'}, 400
    src = UploadedBytesSource(data=data, uploaded_filename=f.filename or '')
    result = current_app.reference_data_service.apply_bundle(
        src, initiated_by=get_jwt_identity(),
    )
    return result.model_dump(mode='json')
