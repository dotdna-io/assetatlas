# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tag Rules Blueprint

Handles all tag rule management endpoints including CRUD operations,
rule execution, preview, and statistics.

Endpoints (11 total):
- POST   /api/tag-rules - Create tag rule
- GET    /api/tag-rules - List tag rules
- GET    /api/tag-rules/<rule_id> - Get tag rule
- PUT    /api/tag-rules/<rule_id> - Update tag rule
- DELETE /api/tag-rules/<rule_id> - Delete tag rule
- POST   /api/tag-rules/<rule_id>/toggle - Toggle rule enabled status
- POST   /api/tag-rules/preview - Preview rule matches
- POST   /api/tag-rules/<rule_id>/execute - Execute specific rule
- POST   /api/tag-rules/execute-all - Execute all enabled rules
- GET    /api/tag-rules/fields - Get available fields
- GET    /api/tag-rules/<rule_id>/statistics - Get rule statistics
"""

import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required, current_user
from typing import Dict, Any

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.responses import response_wrapper, success_response
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin
from src.api.utils.audit_logger import AuditLogger, get_current_user_context
from src.api.utils.audit_vocab import AuditAction, AuditResource
from src.modules.fields import build_catalog_response, InvalidFieldError
from src.modules.tag_rules.services.tags_column_service import refresh_tags_column
from src.modules.tag_rules.models.rule import (
    TagRuleCreate,
    TagRuleUpdate,
    PreviewCountRequest
)

logger = logging.getLogger(__name__)

# Create blueprint
tag_rules_bp = Blueprint('tag_rules', __name__, url_prefix='/api/tag-rules')


# =============================================================================
# CRUD Endpoints
# =============================================================================

@tag_rules_bp.route('', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(TagRuleCreate)
@response_wrapper
def create_rule(validated_data: TagRuleCreate):
    """
    Create a new tag rule.

    The rule will be automatically executed after creation if enabled.

    Request Body:
        {
            "name": "Windows Server Classification",
            "description": "Tags all Windows Server assets",
            "type": "classification",
            "enabled": true,
            "target_filters": [...],
            "tags": {"label": "Windows-Server", "color": "#0078d4"}
        }

    Returns:
        201: Rule created successfully with rule details
        400: Invalid request data
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    # Create rule (auto-executes if enabled)
    try:
        created_rule = rule_mgmt_service.create_rule(validated_data)
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    # Audit log: Tag rule created
    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.TAG_RULE,
        resource_id=created_rule.id,
        action=AuditAction.CREATE,
        user_id=user_id,
        username=username,
        details={
            'name': created_rule.name,
            'type': created_rule.type,
            'enabled': created_rule.enabled,
            'tag_name': created_rule.tags.get('label') if created_rule.tags else None
        },
        reason=f'Tag rule "{created_rule.name}" created'
    )

    return {
        'rule': created_rule.model_dump(mode='json'),
        'message': 'Rule created successfully'
    }, 201


@tag_rules_bp.route('', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def list_rules():
    """
    List all tag rules with pagination and filtering.

    Query Parameters:
        page (int): Page number (default: 1)
        limit (int): Results per page (default: 50, max: 100)
        type (str): Filter by type ("classification" or "compliance")
        enabled (bool): Filter by enabled status

    Returns:
        200: List of rules with pagination info
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    # Parse query parameters
    page = request.args.get('page', 1, type=int)
    limit = min(request.args.get('limit', 50, type=int), 100)
    rule_type = request.args.get('type', None, type=str)
    enabled_only = request.args.get('enabled', 'false', type=str).lower() == 'true'

    # Get rules
    rules_list = rule_mgmt_service.list_rules(
        page=page,
        limit=limit,
        rule_type=rule_type,
        enabled_only=enabled_only
    )

    return {
        'rules': [rule.model_dump(mode='json') for rule in rules_list.rules],
        'pagination': {
            'total': rules_list.total,
            'page': rules_list.page,
            'limit': rules_list.limit,
            'pages': (rules_list.total + limit - 1) // limit
        }
    }


@tag_rules_bp.route('/<rule_id>', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_rule(rule_id: str):
    """
    Get a specific rule by ID.

    Path Parameters:
        rule_id (str): Rule ID

    Returns:
        200: Rule details
        404: Rule not found
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    rule = rule_mgmt_service.get_rule(rule_id)

    if not rule:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    return {'rule': rule.model_dump(mode='json')}


@tag_rules_bp.route('/<rule_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(TagRuleUpdate)
@response_wrapper
def update_rule(rule_id: str, validated_data: TagRuleUpdate):
    """
    Update an existing rule.

    The rule will be automatically re-executed after update if enabled.

    Path Parameters:
        rule_id (str): Rule ID

    Request Body:
        {
            "name": "Updated Name",
            "enabled": false,
            ...
        }

    Returns:
        200: Rule updated successfully
        404: Rule not found
        400: Invalid request data
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    # Get old rule for audit trail
    old_rule = rule_mgmt_service.get_rule(rule_id)
    if not old_rule:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    # Update rule (auto-executes if enabled)
    try:
        updated_rule = rule_mgmt_service.update_rule(rule_id, validated_data)
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    if not updated_rule:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    # Audit log: Tag rule updated - track what changed
    user_id, username = get_current_user_context()
    changes = {}

    # Track changes in key fields
    if validated_data.name is not None and old_rule.name != validated_data.name:
        changes['name'] = {'old': old_rule.name, 'new': validated_data.name}
    if validated_data.enabled is not None and old_rule.enabled != validated_data.enabled:
        changes['enabled'] = {'old': old_rule.enabled, 'new': validated_data.enabled}
    if validated_data.target_filters is not None:
        changes['target_filters'] = {'old': 'updated', 'new': 'updated'}
    if validated_data.tags is not None:
        changes['tags'] = {'old': 'updated', 'new': 'updated'}

    if changes:
        AuditLogger.log_config_change(
            resource=AuditResource.TAG_RULE,
            resource_id=rule_id,
            action=AuditAction.UPDATE,
            user_id=user_id,
            username=username,
            changes=changes,
            reason=f'Tag rule "{updated_rule.name}" updated'
        )

    return {
        'rule': updated_rule.model_dump(mode='json'),
        'message': 'Rule updated successfully'
    }


@tag_rules_bp.route('/<rule_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_rule(rule_id: str):
    """
    Delete a rule.

    Path Parameters:
        rule_id (str): Rule ID

    Returns:
        200: Rule deleted successfully
        404: Rule not found
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    # Get rule details before deletion for audit trail
    rule = rule_mgmt_service.get_rule(rule_id)
    if not rule:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    # Store details for audit log
    rule_name = rule.name
    rule_type = rule.type
    rule_tag = rule.tags.get('label') if rule.tags else None

    deleted = rule_mgmt_service.delete_rule(rule_id)

    if not deleted:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    # Audit log: Tag rule deleted (CRITICAL severity)
    user_id, username = get_current_user_context()
    AuditLogger.log_data_operation(
        resource=AuditResource.TAG_RULE,
        resource_id=rule_id,
        action=AuditAction.DELETE,
        user_id=user_id,
        username=username,
        details={
            'name': rule_name,
            'type': rule_type,
            'tag_name': rule_tag
        },
        reason=f'Tag rule "{rule_name}" deleted by admin'
    )

    return {'message': 'Rule deleted successfully'}


@tag_rules_bp.route('/<rule_id>/toggle', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def toggle_rule(rule_id: str):
    """
    Toggle a rule's enabled status.

    Path Parameters:
        rule_id (str): Rule ID

    Returns:
        200: Rule toggled successfully
        404: Rule not found
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    rule = rule_mgmt_service.get_rule(rule_id)

    if not rule:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    # Toggle enabled status
    updated_rule = rule_mgmt_service.update_rule(
        rule_id,
        TagRuleUpdate(enabled=not rule.enabled)
    )

    # Audit log: Rule toggled — normalized to enable/disable by resulting state
    user_id, username = get_current_user_context()
    AuditLogger.log_config_change(
        resource=AuditResource.TAG_RULE,
        resource_id=rule_id,
        action=AuditAction.ENABLE if updated_rule.enabled else AuditAction.DISABLE,
        user_id=user_id,
        username=username,
        changes={'enabled': {'old': rule.enabled, 'new': updated_rule.enabled}},
        reason=f'Tag rule "{updated_rule.name}" {"enabled" if updated_rule.enabled else "disabled"}'
    )

    return {
        'rule': updated_rule.model_dump(mode='json'),
        'message': f'Rule {"enabled" if updated_rule.enabled else "disabled"} successfully'
    }


# =============================================================================
# Preview & Count Endpoints
# =============================================================================

@tag_rules_bp.route('/preview', methods=['POST'])
@jwt_required()
@handle_errors
@validate_request(PreviewCountRequest)
@response_wrapper
def preview_rule(validated_data: PreviewCountRequest):
    """
    Preview asset counts for a rule without creating it.

    Request Body:
        {
            "target_filters": [...],
            "compliance_criteria": {...}  // optional
        }

    Returns:
        200: Preview counts
        400: Invalid request
        500: Server error
    """
    preview_service = current_app.preview_service

    # Get preview
    try:
        if validated_data.compliance_criteria:
            # Compliance preview
            # Convert Pydantic models to dicts
            target_filters_dict = [f.model_dump() for f in validated_data.target_filters]
            compliance_criteria_dict = validated_data.compliance_criteria.model_dump()

            total, passed, failed, time_ms = preview_service.preview_compliance(
                target_filters_dict,
                compliance_criteria_dict
            )

            return {
                'total_matching': total,
                'pass_count': passed,
                'fail_count': failed,
                'execution_time_ms': time_ms
            }
        else:
            # Simple count
            # Convert Pydantic models to dicts
            target_filters_dict = [f.model_dump() for f in validated_data.target_filters]

            count, time_ms = preview_service.count_matching_assets(
                target_filters_dict
            )

            return {
                'total_matching': count,
                'pass_count': None,
                'fail_count': None,
                'execution_time_ms': time_ms
            }
    except InvalidFieldError as e:
        raise ValidationError(str(e))


# =============================================================================
# Execution Endpoints
# =============================================================================

@tag_rules_bp.route('/<rule_id>/execute', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def execute_rule_endpoint(rule_id: str):
    """
    Manually execute a specific rule.

    Path Parameters:
        rule_id (str): Rule ID

    Query Parameters:
        async (bool): Run asynchronously (not implemented yet, runs sync for now)

    Returns:
        200: Execution completed
        404: Rule not found
        500: Server error
    """
    execution_service = current_app.execution_service

    try:
        # Execute rule
        result = execution_service.execute_rule(
            rule_id=rule_id,
            triggered_by="manual",
            triggered_by_user=request.headers.get('X-User-Email')  # Optional user tracking
        )

        if result['status'] == 'failed':
            raise ValidationError(
                result.get('error_message', 'Unknown error'),
                details={'execution_id': result.get('execution_id')}
            )

        # Refresh denormalized tags column after single-rule execution
        refresh_tags_column(current_app.db_adapter)

        return {
            'result': result,
            'message': 'Rule executed successfully'
        }

    except InvalidFieldError as e:
        raise ValidationError(str(e))
    except ValueError as e:
        raise NotFoundError(str(e), details={'rule_id': rule_id})


@tag_rules_bp.route('/execute-all', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def execute_all_rules():
    """
    Execute all enabled rules.

    This is typically called from the asset generation pipeline,
    but can also be triggered manually.

    Returns:
        200: Execution completed
        500: Server error
    """
    execution_service = current_app.execution_service

    try:
        result = execution_service.execute_all_enabled_rules(
            triggered_by="manual"
        )
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    # Audit log: Bulk operation - execute all rules
    user_id, username = get_current_user_context()
    AuditLogger.log_bulk_operation(
        resource=AuditResource.TAG_RULE,
        action=AuditAction.EXECUTE_ALL,
        user_id=user_id,
        username=username,
        count=result.get('total_executed', 0),
        details={
            'successful': result.get('successful', 0),
            'failed': result.get('failed', 0),
            'total_assets_affected': result.get('total_assets_affected', 0)
        },
        reason='Manual bulk execution of all enabled tag rules'
    )

    return {
        'result': result,
        'message': 'All rules executed'
    }


# =============================================================================
# Field Metadata Endpoints
# =============================================================================

@tag_rules_bp.route('/fields', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_fields():
    """
    Get available fields and their operators.

    Returns:
        200: Field catalog entries for the rule builder
        500: Server error
    """
    return build_catalog_response(
        current_app.field_resolver.catalog(),
        include_buckets=["target", "advanced", "extended_attributes", "tags", "enrichment", "virtual", "posture", "unified"],
    )


# =============================================================================
# History & Statistics Endpoints
# =============================================================================

@tag_rules_bp.route('/<rule_id>/statistics', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_rule_statistics(rule_id: str):
    """
    Get execution statistics for a rule.

    Path Parameters:
        rule_id (str): Rule ID

    Returns:
        200: Statistics
        404: Rule not found
        500: Server error
    """
    rule_mgmt_service = current_app.rule_mgmt_service

    stats = rule_mgmt_service.get_rule_statistics(rule_id)

    if not stats:
        raise NotFoundError(f'Rule {rule_id} not found', details={'rule_id': rule_id})

    return {'statistics': stats}
