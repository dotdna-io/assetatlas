# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Edit Blueprint

Handles reclassify, tag, and merge operations on assets.

Endpoints (22 total):
- PUT  /api/assets/<asset_id>/classify                       - Reclassify asset
- GET  /api/assets/categories                                 - Valid category/subcategory list
- POST /api/assets/categories/<category>/subcategories        - Add a custom subcategory
- GET  /api/assets/<asset_id>/tags                            - List manual tags
- POST /api/assets/<asset_id>/tags                            - Add a tag
- DELETE /api/assets/<asset_id>/tags/<tag_id>                 - Remove a tag
- POST /api/assets/merge                                      - Execute merge
- POST /api/assets/<asset_id>/unmerge                         - Undo a merge
- GET  /api/assets/<asset_id>/merge-history                   - Get merge history
- GET  /api/assets/<asset_id>/merge-preview                   - Preview merge
- GET  /api/assets/<asset_id>/label-override                  - Get asset_label override
- PUT  /api/assets/<asset_id>/label-override                  - Set asset_label override
- DELETE /api/assets/<asset_id>/label-override                - Clear asset_label override
- GET  /api/assets/<asset_id>/category-override               - Category override state + prediction
- DELETE /api/assets/<asset_id>/category-override             - Restore predicted category
- POST /api/assets/classify-bulk                             - Bulk reclassify by filter
- POST /api/assets/classify-bulk/clear                       - Bulk clear category overrides by filter
- POST /api/assets/tags-bulk                                 - Bulk add one manual tag by filter
- GET  /api/assets/tags-bulk/candidates                      - Manual tags on the filtered set
- POST /api/assets/tags-bulk/remove                          - Bulk remove one manual tag by filter
"""

import json
import logging
import re
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.models.requests import AssetFilter
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import ensure_uuid, validate_request
from src.auth.middleware.rbac import require_admin
from src.modules.assets.models.split import SplitRequest
from src.api.blueprints.pipeline import _get_pipeline_next_run

# Shared by every filter-scoped bulk action (reclassify, clear override, tag,
# untag) and by the untag picker, which resolves through the same helper so a
# filter too wide to act on is also too wide to pick for.
# Changing the value changes the client-visible message and details.cap payload.
BULK_ACTION_CAP = 1000

# ColorPicker emits #rrggbb and the column is varchar(7); anything else would be
# a truncation error (500) rather than the 400 a bad request deserves.
_HEX_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
_DEFAULT_TAG_COLOR = '#64748b'
_MAX_TAG_NAME_LEN = 100

logger = logging.getLogger(__name__)

# Create blueprint
asset_edit_bp = Blueprint('asset_edit', __name__, url_prefix='/api/assets')


# =============================================================================
# Classification & Filter-Scoped Bulk Endpoints
# =============================================================================

@asset_edit_bp.route('/<asset_id>/classify', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def reclassify_asset(asset_id: str):
    """
    Reclassify an asset with a new category and subcategory.
    ---
    tags:
      - Asset Edit
    summary: Reclassify asset
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Asset UUID
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [category, subcategory]
            properties:
              category:
                type: string
              subcategory:
                type: string
    responses:
      200:
        description: Asset reclassified successfully
      400:
        description: Missing required fields
      404:
        description: Asset not found
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'category' not in data or 'subcategory' not in data:
        raise ValidationError(
            'Both category and subcategory are required',
            details={'missing': [f for f in ('category', 'subcategory') if f not in data]},
        )

    reclassify_service = current_app.reclassify_service

    try:
        result = reclassify_service.reclassify_asset(
            asset_id=asset_id,
            category=data['category'],
            subcategory=data['subcategory'],
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return result


def _resolve_bulk_ids(data):
    """Validate filters (loud-fail), resolve matching ids, enforce the cap.

    Returns the id list (<= cap). Raises ValidationError on a malformed filter
    or when the match set exceeds the cap — in both cases NOTHING is written.

    An optional ``alert_id`` narrows the target set to the assets that alert
    names (#1011). Without it, an alert drilldown (filters=[]) would resolve
    every asset while the count beside the button read the alert's size — the
    write silently landing on the fleet. Canonicalised via ensure_uuid before
    the repository casts it to ::uuid, so a client typo is a 400, not the 500
    the raw cast would raise (#973/#999).
    """
    raw_filters = data.get('filters') or []
    if not isinstance(raw_filters, list):
        raise ValidationError('filters must be a JSON array')
    try:
        filters = [AssetFilter(**f).model_dump() for f in raw_filters]
    except Exception as exc:
        raise ValidationError(f'Invalid filters: {exc}')

    raw_alert_id = data.get('alert_id')
    if raw_alert_id is not None and not isinstance(raw_alert_id, str):
        # ensure_uuid's UUID() raises AttributeError (not ValueError) on a
        # non-string, which handle_errors would answer 500 — this raw path has
        # no Pydantic gate to reject it first, so check the type here.
        raise ValidationError('alert_id must be a string')
    alert_id = ensure_uuid(raw_alert_id, 'alert') if raw_alert_id else None

    search = data.get('search')
    asset_ids = current_app.asset_service.get_matching_asset_ids(
        filters=filters, search=search, alert_id=alert_id,
        limit=BULK_ACTION_CAP + 1,
    )
    if len(asset_ids) > BULK_ACTION_CAP:
        raise ValidationError(
            f'Filter matches more than {BULK_ACTION_CAP} assets. '
            f'Narrow your filter and try again.',
            details={'cap': BULK_ACTION_CAP},
        )
    return asset_ids


@asset_edit_bp.route('/classify-bulk', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def reclassify_bulk():
    """Reclassify every asset matching the given Explorer filter.
    ---
    tags:
      - Asset Edit
    summary: Bulk reclassify by filter
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [category, subcategory]
            properties:
              filters: {type: array, items: {type: object}}
              search: {type: string}
              category: {type: string}
              subcategory: {type: string}
    responses:
      200: {description: Assets reclassified}
      400: {description: Missing fields, invalid filter, or over the cap}
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'category' not in data or 'subcategory' not in data:
        raise ValidationError(
            'Both category and subcategory are required',
            details={'missing': [f for f in ('category', 'subcategory') if f not in data]},
        )

    asset_ids = _resolve_bulk_ids(data)

    try:
        return current_app.reclassify_service.reclassify_bulk(
            asset_ids=asset_ids,
            category=data['category'],
            subcategory=data['subcategory'],
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))


@asset_edit_bp.route('/classify-bulk/clear', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def clear_bulk_overrides():
    """Reset category overrides to predicted for every asset matching the filter.
    ---
    tags:
      - Asset Edit
    summary: Bulk clear category overrides by filter
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            properties:
              filters: {type: array, items: {type: object}}
              search: {type: string}
    responses:
      200: {description: Overrides cleared}
      400: {description: Invalid filter or over the cap}
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()
    asset_ids = _resolve_bulk_ids(data)
    return current_app.reclassify_service.clear_overrides_bulk(asset_ids=asset_ids, user=user_id)


def _validate_tag_name(data):
    """Validate the tag_name field shared by the bulk tag routes.

    Present, a string, non-empty after strip — returned trimmed.
    """
    raw_name = data.get('tag_name')
    if raw_name is None:
        raise ValidationError('tag_name is required', details={'missing': ['tag_name']})
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValidationError('tag_name must be a non-empty string')
    return raw_name.strip()


def _validate_tag_input(data):
    """Validate the bulk tag fields, returning (tag_name, tag_color).

    Both checks exist to keep a bad request a 400 rather than a DB error:
    tag_name lands in a varchar(100) and tag_color in a varchar(7), where an
    over-long value is a truncation failure, not a rejection. tag_color is
    optional and falls back to the same default the per-asset route uses.
    """
    tag_name = _validate_tag_name(data)
    if len(tag_name) > _MAX_TAG_NAME_LEN:
        raise ValidationError(
            f'tag_name must be {_MAX_TAG_NAME_LEN} characters or fewer',
            details={'max_length': _MAX_TAG_NAME_LEN},
        )

    raw_color = data.get('tag_color')
    if raw_color is None:
        return tag_name, _DEFAULT_TAG_COLOR
    if not isinstance(raw_color, str) or not _HEX_COLOR_RE.match(raw_color):
        raise ValidationError(f'tag_color must be a hex colour like {_DEFAULT_TAG_COLOR}')
    return tag_name, raw_color


@asset_edit_bp.route('/tags-bulk', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def add_tag_bulk():
    """Apply one manual tag to every asset matching the given Explorer filter.
    ---
    tags:
      - Asset Edit
    summary: Bulk add a manual tag by filter
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [tag_name]
            properties:
              filters: {type: array, items: {type: object}}
              search: {type: string}
              alert_id: {type: string}
              tag_name: {type: string, description: "Non-empty after strip; 100 chars max"}
              tag_color: {type: string, default: "#64748b"}
    responses:
      200: {description: Tag applied}
      400: {description: Missing/invalid tag, invalid filter, or over the cap}
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    # Validate before resolving: a malformed tag shouldn't cost a filter query.
    tag_name, tag_color = _validate_tag_input(data)
    asset_ids = _resolve_bulk_ids(data)

    try:
        return current_app.tag_service.add_tag_bulk(
            asset_ids=asset_ids,
            tag_name=tag_name,
            tag_color=tag_color,
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))


@asset_edit_bp.route('/tags-bulk/candidates', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def tag_bulk_candidates():
    """List the manual tags carried by the assets matching the given filter.
    ---
    tags:
      - Asset Edit
    summary: Manual tags available for bulk removal, with per-tag counts
    parameters:
      - name: filters
        in: query
        type: string
        description: JSON array of AssetFilter objects
      - name: search
        in: query
        type: string
      - name: alert_id
        in: query
        type: string
    responses:
      200: {description: "Candidates (possibly empty) with carrier counts"}
      400: {description: Invalid filter or over the cap}
    """
    # A GET can't carry the action's JSON body, so filters ride the query string
    # the way /assets/values takes them. Decode only — the resulting dict goes
    # through the same _resolve_bulk_ids as the POST routes, so the filter
    # validation, the #1011 alert narrowing and the cap are not duplicated here.
    raw_filters = request.args.get('filters')
    if raw_filters:
        try:
            filters = json.loads(raw_filters)
        except json.JSONDecodeError as exc:
            raise ValidationError(f'Invalid filters parameter: {exc}')
    else:
        filters = []

    # Deliberately the same cap as the action this picker serves: a filter too
    # wide to untag is too wide to pick for, and the dialog gets one message
    # instead of a picker that loads and an action that then fails.
    asset_ids = _resolve_bulk_ids({
        'filters': filters,
        'search': request.args.get('search'),
        'alert_id': request.args.get('alert_id'),
    })

    return {'candidates': current_app.tag_service.get_bulk_tag_candidates(asset_ids)}


@asset_edit_bp.route('/tags-bulk/remove', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def remove_tag_bulk():
    """Remove one manual tag from every asset matching the given Explorer filter.
    ---
    tags:
      - Asset Edit
    summary: Bulk remove a manual tag by filter
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [tag_name]
            properties:
              filters: {type: array, items: {type: object}}
              search: {type: string}
              alert_id: {type: string}
              tag_name: {type: string, description: "Non-empty after strip"}
    responses:
      200: {description: "Tag removed from the assets that carried it"}
      400: {description: Missing/invalid tag, invalid filter, or over the cap}
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    # Validate before resolving: a malformed tag shouldn't cost a filter query.
    # No length ceiling, unlike the apply route — nothing is written here, and a
    # name longer than the column just matches nothing.
    tag_name = _validate_tag_name(data)
    asset_ids = _resolve_bulk_ids(data)

    try:
        return current_app.tag_service.remove_tag_bulk(
            asset_ids=asset_ids,
            tag_name=tag_name,
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))


@asset_edit_bp.route('/categories', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_categories():
    """
    Get the list of valid categories and subcategories.
    ---
    tags:
      - Asset Edit
    summary: List valid categories
    responses:
      200:
        description: Categories retrieved successfully
    """
    reclassify_service = current_app.reclassify_service
    return {"categories": reclassify_service.get_categories()}


@asset_edit_bp.route('/categories/<category>/subcategories', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def add_custom_subcategory(category: str):
    """
    Add a custom subcategory under an existing category.
    ---
    tags:
      - Asset Edit
    summary: Add custom subcategory
    parameters:
      - name: category
        in: path
        type: string
        required: true
        description: Existing catalog category name
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [name]
            properties:
              name:
                type: string
                description: New subcategory name (trimmed; must be unique within the category, case-insensitive)
    responses:
      200:
        description: Subcategory added successfully
      400:
        description: Invalid input or duplicate
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'name' not in data:
        raise ValidationError(
            'name is required',
            details={'missing': ['name']},
        )

    reclassify_service = current_app.reclassify_service

    try:
        return reclassify_service.add_custom_subcategory(
            category=category,
            name=data['name'],
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))


# =============================================================================
# Tag Endpoints
# =============================================================================

@asset_edit_bp.route('/<asset_id>/tags', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_tags(asset_id: str):
    """
    List manual tags for an asset.
    ---
    tags:
      - Asset Edit
    summary: List asset tags
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Tags retrieved successfully
      404:
        description: Asset not found
    """
    tag_service = current_app.tag_service

    tags = tag_service.get_tags(asset_id=asset_id)
    return {"tags": tags}


@asset_edit_bp.route('/<asset_id>/tags', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def add_tag(asset_id: str):
    """
    Add a manual tag to an asset.
    ---
    tags:
      - Asset Edit
    summary: Add tag to asset
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [tag_name]
            properties:
              tag_name:
                type: string
              tag_color:
                type: string
                default: "#64748b"
    responses:
      200:
        description: Tag added successfully
      400:
        description: Missing tag_name
      404:
        description: Asset not found
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'tag_name' not in data:
        raise ValidationError(
            'tag_name is required',
            details={'missing': ['tag_name']},
        )

    tag_service = current_app.tag_service

    try:
        result = tag_service.add_tag(
            asset_id=asset_id,
            tag_name=data['tag_name'],
            tag_color=data.get('tag_color', '#64748b'),
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return result


@asset_edit_bp.route('/<asset_id>/tags/<tag_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def remove_tag(asset_id: str, tag_id: str):
    """
    Remove a manual tag from an asset.
    ---
    tags:
      - Asset Edit
    summary: Remove tag from asset
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
      - name: tag_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Tag removed successfully
      404:
        description: Asset or tag not found
    """
    user_id = get_jwt_identity()
    tag_service = current_app.tag_service

    try:
        tag_service.remove_tag(
            asset_id=asset_id,
            tag_id=tag_id,
        )
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return {"message": "Tag removed successfully"}


# =============================================================================
# Merge Endpoints
# =============================================================================

@asset_edit_bp.route('/merge', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def execute_merge():
    """
    Execute a merge of two assets.
    ---
    tags:
      - Asset Edit
    summary: Merge two assets
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [primary_asset_id, secondary_asset_id, merge_strategy]
            properties:
              primary_asset_id:
                type: string
              secondary_asset_id:
                type: string
              merge_strategy:
                type: string
    responses:
      200:
        description: Merge executed successfully
      400:
        description: Missing required fields
      404:
        description: Asset not found
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    required = ('primary_asset_id', 'secondary_asset_id', 'merge_strategy')
    missing = [f for f in required if f not in data]
    if missing:
        raise ValidationError(
            f'Missing required fields: {", ".join(missing)}',
            details={'missing': missing},
        )

    merge_service = current_app.merge_service

    try:
        result = merge_service.execute_merge(
            primary_id=data['primary_asset_id'],
            secondary_id=data['secondary_asset_id'],
            strategy=data['merge_strategy'],
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return result


@asset_edit_bp.route('/<asset_id>/unmerge', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def unmerge_asset(asset_id: str):
    """
    Undo a previous merge, restoring the secondary asset.
    ---
    tags:
      - Asset Edit
    summary: Unmerge an asset
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Primary asset UUID
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [secondary_asset_id]
            properties:
              secondary_asset_id:
                type: string
    responses:
      200:
        description: Asset unmerged successfully
      400:
        description: Invalid request
      404:
        description: Merge record not found
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'secondary_asset_id' not in data:
        raise ValidationError(
            'secondary_asset_id is required',
            details={'missing': ['secondary_asset_id']},
        )

    merge_service = current_app.merge_service

    try:
        result = merge_service.unmerge(
            primary_id=asset_id,
            secondary_id=data['secondary_asset_id'],
            user=user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return result


@asset_edit_bp.route('/<asset_id>/merge-history', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_merge_history(asset_id: str):
    """
    Get merge history for an asset.
    ---
    tags:
      - Asset Edit
    summary: Get asset merge history
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Merge history retrieved
      404:
        description: Asset not found
    """
    merge_service = current_app.merge_service
    history = merge_service.get_merge_history(asset_id)
    return {"merge_history": history}


@asset_edit_bp.route('/<asset_id>/merge-preview', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def merge_preview(asset_id: str):
    """
    Preview the result of merging two assets.
    ---
    tags:
      - Asset Edit
    summary: Preview asset merge
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
      - name: target_id
        in: query
        type: string
        required: true
    responses:
      200:
        description: Merge preview generated
      400:
        description: Missing target_id
      404:
        description: Asset not found
    """
    target_id = request.args.get('target_id')

    if not target_id:
        raise ValidationError(
            'target_id query parameter is required',
            details={'missing': ['target_id']},
        )

    merge_service = current_app.merge_service

    try:
        result = merge_service.preview_merge(
            primary_id=asset_id,
            secondary_id=target_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))

    return result


@asset_edit_bp.route('/<asset_id>/split-preview', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def split_preview(asset_id: str):
    """Admin: preview how an over-merged asset would split.

    Query: ?bridge=<component> to preview a specific cut; omitted = auto-detect.
    Returns {bridge, clusters, would_split, warnings, next_run_at}.
    """
    bridge = request.args.get('bridge')
    out = current_app.asset_split_service.preview(asset_id, bridge=bridge)
    out['next_run_at'] = _get_pipeline_next_run(current_app.pipeline_service)
    return out


@asset_edit_bp.route('/<asset_id>/split', methods=['POST'], endpoint='split')
@jwt_required()
@require_admin()
@handle_errors
@validate_request(SplitRequest)
@response_wrapper
def split_asset(asset_id: str, validated_data: SplitRequest):
    """Admin: register a split (denylist bridge + evict non-primary mappings).

    Applies on the next scheduled pipeline run. Returns {primary_id, split_into,
    next_run_at}.
    """
    try:
        result = current_app.asset_split_service.execute(
            asset_id,
            bridge=validated_data.bridge,
            primary_cluster=validated_data.primary_cluster,
            clusters=validated_data.clusters,
            performed_by=get_jwt_identity(),
        )
    except ValueError as exc:
        raise ValidationError(str(exc))
    result['next_run_at'] = _get_pipeline_next_run(current_app.pipeline_service)
    return result, 202


# =============================================================================
# Asset Label Override Endpoints (issue #639)
# =============================================================================

@asset_edit_bp.route('/<asset_id>/label-override', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_label_override(asset_id: str):
    """
    Get the current manual asset_label override for an asset.
    ---
    tags:
      - Asset Edit
    summary: Get asset label override
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Override returned (label may be null when no override exists)
    """
    svc = current_app.asset_label_override_service
    row = svc.get_override(asset_id)
    if not row:
        return {"label": None}
    return {
        "label": row.get("label"),
        "overridden_by": row.get("overridden_by"),
        "updated_at": (
            row["updated_at"].isoformat()
            if hasattr(row.get("updated_at"), "isoformat")
            else row.get("updated_at")
        ),
    }


@asset_edit_bp.route('/<asset_id>/label-override', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def set_label_override(asset_id: str):
    """
    Set or update the manual asset_label override for an asset.
    ---
    tags:
      - Asset Edit
    summary: Set asset label override
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [label]
            properties:
              label:
                type: string
                description: Non-empty after strip(). To clear, use DELETE.
    responses:
      200:
        description: Override saved
      400:
        description: Missing or empty label
      404:
        description: Asset not found
    """
    data = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()

    if 'label' not in data:
        raise ValidationError(
            'label is required',
            details={'missing': ['label']},
        )

    svc = current_app.asset_label_override_service
    try:
        return svc.set_override(asset_id=asset_id, label=data['label'], user=user_id)
    except ValueError as exc:
        raise ValidationError(str(exc))
    except LookupError as exc:
        raise NotFoundError(str(exc))


@asset_edit_bp.route('/<asset_id>/label-override', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def clear_label_override(asset_id: str):
    """
    Clear the manual asset_label override for an asset (idempotent).
    ---
    tags:
      - Asset Edit
    summary: Clear asset label override
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Override cleared
      404:
        description: Asset not found
    """
    user_id = get_jwt_identity()
    svc = current_app.asset_label_override_service
    try:
        return svc.clear_override(asset_id=asset_id, user=user_id)
    except LookupError as exc:
        raise NotFoundError(str(exc))


# =============================================================================
# Category Override Endpoints (issue #1155)
# =============================================================================

@asset_edit_bp.route('/<asset_id>/category-override', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_category_override(asset_id: str):
    """
    Get the manual category-override state for an asset.

    Deliberately NOT admin-gated, matching get_label_override: the Explorer edit
    modal issues this read for every user who can open it, and an admin gate on a
    route the UI calls unconditionally shows up as an empty panel rather than an
    error (#1144). Nothing here is privileged — it is the asset's own
    classification provenance.

    Answered from `category_overrides.active`, never
    `assets.category_is_overridden`: the pipeline's TRUNCATE + INSERT rebuild
    resets that flag to its DB DEFAULT, so it reads false on every override older
    than one run (#1270).
    ---
    tags:
      - Asset Edit
    summary: Get category override state and the pipeline's prediction
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Asset UUID
    responses:
      200:
        description: >
          Override state. `is_overridden` false (with null attribution) when the
          asset carries none — not a 404. The `*_predicted` pair may be null on
          rows written before migration 143, in which case restoring leaves the
          live values alone until the next pipeline run.
      400:
        description: asset_id is not a UUID
    """
    asset_id = ensure_uuid(asset_id, 'asset')
    state = current_app.reclassify_service.get_override_state(asset_id)
    overridden_at = state.get("overridden_at")
    return {
        "asset_id": state.get("asset_id"),
        "is_overridden": bool(state.get("is_overridden")),
        "overridden_by": state.get("overridden_by"),
        "overridden_at": (
            overridden_at.isoformat()
            if hasattr(overridden_at, "isoformat")
            else overridden_at
        ),
        "reason": state.get("reason"),
        "category_predicted": state.get("category_predicted"),
        "subcategory_predicted": state.get("subcategory_predicted"),
    }


@asset_edit_bp.route('/<asset_id>/category-override', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def clear_category_override(asset_id: str):
    """
    Clear one asset's category override, restoring the pipeline's prediction.

    Delegates to the same `clear_overrides_bulk` the filter-scoped dialog uses,
    with a one-element list. Clearing correctly is three writes in one
    transaction — deactivate the override row, reset the live row to the
    predicted pair, write the `category_override_cleared` chronicle event — and
    that method already does all three, with the COALESCE restore from #1154 and
    the order-independent EXISTS guard from #1270. `delete_override` alone would
    do only the first, and a second single-asset path would drift from the bulk
    one silently. Admin-gated to match PUT /<asset_id>/classify and
    POST /classify-bulk/clear.

    Idempotent: clearing an asset that carries no override reports
    `cleared: false` rather than failing.
    ---
    tags:
      - Asset Edit
    summary: Restore an asset to its predicted category
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Asset UUID
    responses:
      200:
        description: >
          `cleared` is true when an active override was found and undone, false
          when there was nothing to undo.
      400:
        description: asset_id is not a UUID
    """
    asset_id = ensure_uuid(asset_id, 'asset')
    user_id = get_jwt_identity()
    # Canonicalised above for a reason beyond the ::uuid cast: clear_overrides_bulk
    # narrows by string-matching these ids against psycopg2's lowercase UUIDs, so
    # a caller's uppercase spelling would match nothing, clear nothing, and still
    # answer 200.
    result = current_app.reclassify_service.clear_overrides_bulk(
        asset_ids=[asset_id],
        user=user_id,
    ) or {}
    return {"asset_id": asset_id, "cleared": bool(result.get("cleared", 0))}
