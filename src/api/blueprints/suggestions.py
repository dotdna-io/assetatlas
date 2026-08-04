# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Suggestions Blueprint

Provides semantic search suggestions for Explorer:
- GET /api/suggestions - Get field/value suggestions based on query
- GET /api/suggestions/health - Check suggestion service health
"""

import logging
from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required

from src.api.utils.caching import get_cached_group_mapping
from src.modules.integrations.utils.field_grouping import FieldGroupResolver

logger = logging.getLogger(__name__)

# Create blueprint
suggestions_bp = Blueprint('suggestions', __name__, url_prefix='/api/suggestions')


def get_suggestion_service():
    """Get suggestion service, preferring the preloaded instance from app context."""
    from src.services.embedding import SuggestionService

    # Use preloaded service from app startup if available
    service = getattr(current_app, 'suggestion_service', None)
    if service is not None:
        return service

    # Fallback: create on demand (first request before preload finishes)
    logger.info("Suggestion service not yet preloaded, creating on demand")
    service = SuggestionService()
    current_app.suggestion_service = service
    return service


@suggestions_bp.route('', methods=['GET'])
@jwt_required()
def get_suggestions():
    """
    Get field and value suggestions based on query.
    ---
    tags:
      - Suggestions
    summary: Get search suggestions
    description: |
      Returns semantically similar field suggestions based on the query.
      Uses embedding similarity to find fields that match the user's intent.

      **Performance:**
      - First request: ~2-3s (model loading)
      - Subsequent requests: ~50-100ms

      **Example queries:**
      - "machine name" → suggests "hostname"
      - "operating system" → suggests "os_normalized"
      - "serial" → suggests "sn"
    parameters:
      - name: q
        in: query
        type: string
        required: true
        description: Search query (min 2 characters)
        example: "machine name"
      - name: limit
        in: query
        type: integer
        required: false
        description: Maximum number of suggestions
        default: 8
        minimum: 1
        maximum: 20
      - name: include_values
        in: query
        type: boolean
        required: false
        description: Include value suggestions (slower)
        default: false
      - name: min_score
        in: query
        type: number
        required: false
        description: Minimum similarity score (0-1)
        default: 0.5
    responses:
      200:
        description: Suggestions found
        schema:
          type: object
          properties:
            suggestions:
              type: array
              items:
                type: object
                properties:
                  type:
                    type: string
                    enum: [field, value]
                  field_name:
                    type: string
                  display_name:
                    type: string
                  description:
                    type: string
                  score:
                    type: number
                  templates:
                    type: array
                    items:
                      type: string
            query:
              type: string
            query_time_ms:
              type: number
            total_found:
              type: integer
      400:
        description: Invalid query parameters
      500:
        description: Service error
    """
    # Get query parameter
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify({
            'error': 'Missing query parameter',
            'message': 'Parameter "q" is required'
        }), 400

    if len(query) < 2:
        return jsonify({
            'suggestions': [],
            'query': query,
            'query_time_ms': 0,
            'message': 'Query too short (min 2 characters)'
        })

    # Get optional parameters
    try:
        limit = int(request.args.get('limit', 8))
        limit = max(1, min(20, limit))  # Clamp to 1-20
    except ValueError:
        limit = 8

    include_values = request.args.get('include_values', 'false').lower() == 'true'

    try:
        min_score = float(request.args.get('min_score', 0.5))
        min_score = max(0.0, min(1.0, min_score))  # Clamp to 0-1
    except ValueError:
        min_score = 0.5

    try:
        service = get_suggestion_service()
        result = service.get_suggestions(
            query=query,
            limit=limit,
            include_values=include_values,
            min_score=min_score
        )

        # Deduplicate field suggestions by integration group
        try:
            group_mapping = get_cached_group_mapping()
            if group_mapping and result.get("suggestions"):
                resolver = FieldGroupResolver(group_mapping)
                seen = set()
                deduped = []
                for s in result["suggestions"]:
                    if s.get("type") == "field" and s.get("data_source"):
                        resolved_source = resolver.resolve_source(s["data_source"])
                        dedup_key = (resolved_source, s.get("display_name", ""))
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        s["data_source"] = resolved_source
                    deduped.append(s)
                result["suggestions"] = deduped
                result["total_found"] = len(deduped)
        except Exception as e:
            logger.warning(f"Failed to deduplicate suggestions by group: {e}")

        return jsonify(result)

    except Exception as e:
        logger.warning(f"Suggestion service unavailable for query '{query}': {e}")
        return jsonify({
            'suggestions': [],
            'query': query,
            'query_time_ms': 0,
            'total_found': 0,
            'service_unavailable': True,
            'message': f'Suggestion service is not available: {e}'
        })


@suggestions_bp.route('/health', methods=['GET'])
@jwt_required()
def health_check():
    """
    Check suggestion service health.
    ---
    tags:
      - Suggestions
    summary: Health check
    description: |
      Returns the health status of the suggestion service.
      Does not load the embedding model, so it's fast.
    responses:
      200:
        description: Service health status
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [healthy, unhealthy]
            chromadb_available:
              type: boolean
            fields_count:
              type: integer
            values_count:
              type: integer
            model_loaded:
              type: boolean
    """
    try:
        service = get_suggestion_service()
        health = service.health_check()
        status_code = 200 if health.get('status') == 'healthy' else 503
        return jsonify(health), status_code
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@suggestions_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_stats():
    """
    Get suggestion service statistics.
    ---
    tags:
      - Suggestions
    summary: Service statistics
    description: |
      Returns statistics about the suggestion service.
      Note: This will load the embedding model if not already loaded.
    responses:
      200:
        description: Service statistics
        schema:
          type: object
          properties:
            embedding_model:
              type: string
            embedding_loaded:
              type: boolean
            chromadb_fields_count:
              type: integer
            chromadb_values_count:
              type: integer
    """
    try:
        service = get_suggestion_service()
        stats = service.get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.exception(f"Error getting stats: {e}")
        return jsonify({
            'error': 'Failed to get statistics',
            'message': str(e)
        }), 500
