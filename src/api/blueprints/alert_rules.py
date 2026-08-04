# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Alert Rules Blueprint

User-facing endpoints for alert rule management. RBAC mirrors posture_rules:
- Read access for all authenticated users.
- Create/update/delete/toggle are admin-only.
- System-owned rules (source='system') are read-only even for admins.

Endpoints (8 total):
- GET    /api/alert-rules                       - List all rules
- GET    /api/alert-rules/<id>                  - Get single rule
- POST   /api/alert-rules                       - Create user rule (admin only)
- PUT    /api/alert-rules/<id>                  - Update user rule (admin only)
- DELETE /api/alert-rules/<id>                  - Delete user rule (admin only)
- PATCH  /api/alert-rules/<id>                  - Toggle enabled (admin only)
- PATCH  /api/alert-rules/<id>/anomaly-threshold - Merge anomaly threshold (admin only)
- POST   /api/alert-rules/preview               - Dry-run count (all authenticated)
"""

import logging
from flask import Blueprint, current_app
from flask_jwt_extended import jwt_required, current_user

from src.api.middleware.error_handler import handle_errors, NotFoundError, ForbiddenError
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin
from src.modules.alerts.repository import SystemRuleImmutableError
from src.modules.alerts.schemas import (
    CreateAlertRuleRequest,
    UpdateAlertRuleRequest,
    ToggleAlertRuleRequest,
    PreviewAlertRuleRequest,
    AnomalyThreshold,
)

logger = logging.getLogger(__name__)

alert_rules_bp = Blueprint('alert_rules', __name__, url_prefix='/api/alert-rules')


@alert_rules_bp.route('', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def list_rules():
    return current_app.alerts_service.get_all_rules()


@alert_rules_bp.route('/<rule_id>', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_rule(rule_id):
    rule = current_app.alerts_service.get_rule(rule_id)
    if rule is None:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})
    return rule


@alert_rules_bp.route('', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(CreateAlertRuleRequest)
@response_wrapper
def create_rule(validated_data: CreateAlertRuleRequest):
    result = current_app.alerts_service.create_rule(
        validated_data.model_dump(mode='json'),
        created_by=str(current_user.get('id')),
    )
    return result, 201


@alert_rules_bp.route('/<rule_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(UpdateAlertRuleRequest)
@response_wrapper
def update_rule(rule_id, validated_data: UpdateAlertRuleRequest):
    updates = validated_data.model_dump(mode='json', exclude_unset=True)
    try:
        result = current_app.alerts_service.update_rule(rule_id, updates)
    except SystemRuleImmutableError as e:
        raise ForbiddenError('System rules are not editable') from e
    if result is None:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})
    return result


@alert_rules_bp.route('/<rule_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_rule(rule_id):
    try:
        ok = current_app.alerts_service.delete_rule(rule_id)
    except SystemRuleImmutableError as e:
        raise ForbiddenError('System rules are not editable') from e
    if not ok:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})
    return '', 204


@alert_rules_bp.route('/<rule_id>', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ToggleAlertRuleRequest)
@response_wrapper
def toggle_rule(rule_id, validated_data: ToggleAlertRuleRequest):
    result = current_app.alerts_service.toggle_rule(rule_id, validated_data.enabled)
    if result is None:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})
    return result


@alert_rules_bp.route('/<rule_id>/anomaly-threshold', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(AnomalyThreshold)
@response_wrapper
def update_anomaly_threshold(rule_id, validated_data: AnomalyThreshold):
    # exclude_unset → only the keys the caller sent are merged; others preserved.
    # Drop explicit nulls too: every field is Optional, so a caller-sent
    # {"pct_medium": null} would otherwise survive exclude_unset and jsonb-merge
    # `pct_medium: null`, silently clobbering the stored value.
    patch = {k: v for k, v in validated_data.model_dump(mode='json', exclude_unset=True).items()
             if v is not None}
    result = current_app.alerts_service.update_anomaly_threshold(rule_id, patch)
    if result is None:
        raise NotFoundError(f'Anomaly rule {rule_id} not found',
                            details={'rule_id': rule_id})
    return result


@alert_rules_bp.route('/preview', methods=['POST'])
@jwt_required()
@handle_errors
@validate_request(PreviewAlertRuleRequest)
@response_wrapper
def preview_rule(validated_data: PreviewAlertRuleRequest):
    return current_app.alerts_service.preview_rule(
        target_filters=[c.model_dump() for c in validated_data.target_filters],
        compliance_criteria=validated_data.compliance_criteria.model_dump()
            if validated_data.compliance_criteria else None,
    )
