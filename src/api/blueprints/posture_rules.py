# SPDX-License-Identifier: AGPL-3.0-or-later
"""Posture Rules Blueprint — category reads, sub-rule CRUD, preview, execute."""

import logging
from flask import Blueprint, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.audit_logger import AuditLogger, get_current_user_context
from src.api.utils.audit_vocab import AuditAction, AuditResource
from src.api.utils.responses import response_wrapper, success_response
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin

from src.modules.posture_rules.models.rule import (
    PostureSubRuleCreate,
    PostureSubRuleUpdate,
    PostureSubRuleToggleRequest,
    PosturePreviewRequest,
    PostureOutputPreviewRequest,
    PostureCategoryUpdateRequest,
    PostureSubRuleOrderRequest,
)
from src.modules.fields import InvalidFieldError
from src.modules.posture_rules.services.sub_rule_management_service import (
    CategoryNotFoundError,
    SubRuleNotFoundError,
)

logger = logging.getLogger(__name__)


posture_rules_bp = Blueprint('posture_rules', __name__, url_prefix='/api/posture-rules')


@posture_rules_bp.route('', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def list_categories():
    service = current_app.posture_category_read_service
    return success_response(service.list_categories())


@posture_rules_bp.route('/<string:category_id>', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_category(category_id: str):
    service = current_app.posture_category_read_service
    result = service.get_category(category_id)
    if result is None:
        raise NotFoundError(f"Posture category '{category_id}' not found")
    return success_response(result)


@posture_rules_bp.route('/categories/<string:category_id>', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(PostureCategoryUpdateRequest)
@response_wrapper
def update_category(category_id: str, validated_data: PostureCategoryUpdateRequest):
    service = current_app.posture_sub_rule_mgmt_service
    try:
        result = service.update_applicable_categories(category_id, validated_data)
    except CategoryNotFoundError as e:
        raise NotFoundError(str(e))

    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.POSTURE_RULE,
        resource_id=category_id,
        action=AuditAction.UPDATE,
        user_id=user_id,
        username=username,
        details={'applicable_categories': validated_data.applicable_categories},
        reason=f'Posture category "{category_id}" applicable categories updated'
    )
    return success_response(result)


@posture_rules_bp.route('/<string:category_id>/sub-rules', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(PostureSubRuleCreate)
@response_wrapper
def create_sub_rule(category_id: str, validated_data: PostureSubRuleCreate):
    user_id, username = get_current_user_context()
    service = current_app.posture_sub_rule_mgmt_service
    try:
        result = service.create(category_id, validated_data, user_id=user_id, username=username)
    except CategoryNotFoundError as e:
        raise NotFoundError(str(e))
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    AuditLogger.log_config_change(
        resource=AuditResource.POSTURE_RULE,
        resource_id=str(result.get('id')),
        action=AuditAction.CREATE,
        user_id=user_id,
        username=username,
        details={
            'name': result.get('name'),
            'category_id': category_id,
            'enabled': result.get('enabled'),
        },
        reason=f'Posture sub-rule "{result.get("name")}" created'
    )
    return success_response(result, status_code=201)


@posture_rules_bp.route('/sub-rules/<string:sub_rule_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(PostureSubRuleUpdate)
@response_wrapper
def update_sub_rule(sub_rule_id: str, validated_data: PostureSubRuleUpdate):
    user_id, username = get_current_user_context()
    service = current_app.posture_sub_rule_mgmt_service
    try:
        result = service.update(sub_rule_id, validated_data, user_id=user_id, username=username)
    except SubRuleNotFoundError as e:
        raise NotFoundError(str(e))
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    AuditLogger.log_config_change(
        resource=AuditResource.POSTURE_RULE,
        resource_id=sub_rule_id,
        action=AuditAction.UPDATE,
        user_id=user_id,
        username=username,
        details={'name': result.get('name'), 'category_id': result.get('category_id')},
        reason=f'Posture sub-rule "{result.get("name")}" updated'
    )
    return success_response(result)


@posture_rules_bp.route('/sub-rules/<string:sub_rule_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def delete_sub_rule(sub_rule_id: str):
    user_id, username = get_current_user_context()
    service = current_app.posture_sub_rule_mgmt_service
    try:
        service.delete(sub_rule_id, user_id=user_id, username=username)
    except SubRuleNotFoundError as e:
        raise NotFoundError(str(e))

    AuditLogger.log_data_operation(
        resource=AuditResource.POSTURE_RULE,
        resource_id=sub_rule_id,
        action=AuditAction.DELETE,
        user_id=user_id,
        username=username,
        reason='Posture sub-rule deleted by admin'
    )
    return ('', 204)


@posture_rules_bp.route('/sub-rules/<string:sub_rule_id>/toggle', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(PostureSubRuleToggleRequest)
@response_wrapper
def toggle_sub_rule(sub_rule_id: str, validated_data: PostureSubRuleToggleRequest):
    user_id, username = get_current_user_context()
    service = current_app.posture_sub_rule_mgmt_service
    try:
        result = service.toggle(sub_rule_id, validated_data.enabled, user_id=user_id, username=username)
    except SubRuleNotFoundError as e:
        raise NotFoundError(str(e))

    # Normalized to enable/disable by resulting state — no 'toggle' action.
    enabled = result.get('enabled')
    AuditLogger.log_config_change(
        resource=AuditResource.POSTURE_RULE,
        resource_id=sub_rule_id,
        action=AuditAction.ENABLE if enabled else AuditAction.DISABLE,
        user_id=user_id,
        username=username,
        details={'enabled': enabled},
        reason=f'Posture sub-rule {"enabled" if enabled else "disabled"}'
    )
    return success_response(result)


@posture_rules_bp.route('/sub-rules/<string:sub_rule_id>/history', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_sub_rule_history(sub_rule_id: str):
    service = current_app.posture_sub_rule_mgmt_service
    entries = service.get_history(sub_rule_id)
    return success_response({"entries": entries})


@posture_rules_bp.route('/<string:category_id>/sub-rules/order', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(PostureSubRuleOrderRequest)
@response_wrapper
def reorder_sub_rules(category_id: str, validated_data: PostureSubRuleOrderRequest):
    service = current_app.posture_sub_rule_mgmt_service
    try:
        service.reorder_sub_rules(category_id, validated_data.ordered_ids)
    except CategoryNotFoundError as e:
        raise NotFoundError(str(e))
    except SubRuleNotFoundError as e:
        raise NotFoundError(str(e))

    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.POSTURE_RULE,
        resource_id=category_id,
        action=AuditAction.REORDER,
        user_id=user_id,
        username=username,
        details={'ordered_ids': validated_data.ordered_ids},
        reason=f'Posture sub-rules reordered in category "{category_id}"'
    )
    return ('', 204)


@posture_rules_bp.route('/sub-rules/preview-output', methods=['POST'])
@jwt_required()
@handle_errors
@validate_request(PostureOutputPreviewRequest)
@response_wrapper
def preview_output(validated_data: PostureOutputPreviewRequest):
    service = current_app.posture_preview_service
    try:
        result = service.preview_output(
            target_filters=[f.model_dump() for f in validated_data.target_filters],
            compliance_criteria=validated_data.compliance_criteria.model_dump(),
            output_mode=validated_data.output_mode,
            output_field_id=validated_data.output_field_id,
            output_text=validated_data.output_text,
        )
    except InvalidFieldError as e:
        raise ValidationError(str(e))
    return success_response(result)


@posture_rules_bp.route('/sub-rules/preview', methods=['POST'])
@jwt_required()
@handle_errors
@validate_request(PosturePreviewRequest)
@response_wrapper
def preview_sub_rule(validated_data: PosturePreviewRequest):
    service = current_app.posture_preview_service
    try:
        result = service.preview(
            target_filters=[f.model_dump() for f in validated_data.target_filters],
            compliance_criteria=validated_data.compliance_criteria.model_dump(),
        )
    except InvalidFieldError as e:
        raise ValidationError(str(e))
    return success_response(result)


@posture_rules_bp.route('/execute', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def execute_all():
    service = current_app.posture_evaluation_service
    result = service.evaluate_all()

    user_id, username = get_current_user_context()
    AuditLogger.log_bulk_operation(
        resource=AuditResource.POSTURE_RULE,
        action=AuditAction.EXECUTE,
        user_id=user_id,
        username=username,
        count=result.get('updated_count', 0),
        details={
            'skipped': result.get('skipped', False),
            'duration_ms': result.get('duration_ms'),
        },
        reason='Manual posture evaluation triggered'
    )
    return success_response(result)
