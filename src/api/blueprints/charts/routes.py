# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Charts API Routes

Defines the REST API endpoints for chart data queries.

Endpoints:
- POST /api/charts/query  - Unified query endpoint for all chart types
- GET  /api/charts/fields - Field catalog entries (all 7 buckets)
- GET  /api/charts/health - Lightweight health check

Created: 2025-11-19
"""

from flask import request, jsonify, current_app
from flask_jwt_extended import jwt_required
from . import charts_bp
from .models import (
    ChartQueryRequest,
    ChartQueryResponse,
    ChartQueryErrorResponse,
    ErrorDetail,
    ChartMetadata
)
from src.modules.charts.services.query_builder import QueryBuilder
from src.modules.charts.repositories.chart_repository import ChartRepository
from src.modules.fields import build_catalog_response, InvalidFieldError
from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS
from pydantic import ValidationError
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@charts_bp.route('/query', methods=['POST'])
@jwt_required()
def query_charts():
    """
    Unified chart query endpoint.

    Accepts a flexible query configuration and returns data formatted
    for the specified chart type.

    Request Body:
    {
        "queryType": "distribution|metric|trend|comparison|correlation",
        "from": { "table": "assets", "joins": [...] },
        "select": { "dimension": {"kind": "static", "name": "Category"}, ... },
        "filters": [...],
        "options": { "limit": 10, ... }
    }

    Response:
    {
        "success": true,
        "queryType": "distribution",
        "data": [...],
        "metadata": { "total": 100, "executionTime": 45, ... }
    }

    Returns:
        JSON response with chart data or error message
    """
    try:
        # Get request data
        query_config = request.get_json()

        if not query_config:
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='INVALID_REQUEST',
                    message='Request body must be valid JSON'
                )
            )
            return jsonify(error_response.model_dump()), 400

        # Validate request with Pydantic models
        try:
            validated_request = ChartQueryRequest(**query_config)
            logger.info(
                f"Chart query request validated: "
                f"queryType={validated_request.queryType.value}, "
                f"table={validated_request.from_.table}, "
                f"joins={len(validated_request.from_.joins)}, "
                f"filters={len(validated_request.filters)}"
            )

        except ValidationError as ve:
            # Pydantic validation failed - convert errors to JSON-safe format
            errors_safe = []
            for error in ve.errors():
                safe_error = {
                    'type': error.get('type'),
                    'loc': list(error.get('loc', [])),
                    'msg': error.get('msg'),
                    'input': str(error.get('input', ''))
                }
                errors_safe.append(safe_error)

            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='VALIDATION_ERROR',
                    message='Invalid request parameters',
                    details={'errors': errors_safe}
                )
            )
            logger.warning(f"Validation error: {errors_safe}")
            return jsonify(error_response.model_dump()), 400

        # Build SQL query using query builder
        try:
            # Read confidence thresholds from settings for confidence_level filter translation
            try:
                medium = current_app.settings_manager.get('asset_generation', 'confidence_threshold_medium')
                high = current_app.settings_manager.get('asset_generation', 'confidence_threshold_high')
                thresholds = (medium, high)
            except (RuntimeError, AttributeError):
                thresholds = DEFAULT_CONFIDENCE_THRESHOLDS
            builder = QueryBuilder(
                confidence_thresholds=thresholds,
                resolver=current_app.field_resolver,
            )
            sql_query, params = builder.build_query(validated_request)
            logger.info(f"SQL query built successfully with {len(params)} parameters")
            logger.debug(f"SQL: {sql_query}")

        except InvalidFieldError as invalid_err:
            # Unknown FieldId — resolver rejected it against the live catalog.
            logger.warning(f"InvalidFieldError: {invalid_err}")
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='INVALID_FIELD',
                    message=str(invalid_err),
                )
            )
            return jsonify(error_response.model_dump()), 400

        except ValueError as ve:
            # Deliberate validation raises from build_query (unsupported field
            # for a query type, virtual field in a select slot, non-time
            # timeField, history-unavailable field) are client errors, not 500s.
            logger.warning(f"Query build validation error: {ve}")
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(code='INVALID_QUERY', message=str(ve))
            )
            return jsonify(error_response.model_dump()), 400

        except Exception as qb_error:
            logger.error(f"Query builder error: {str(qb_error)}", exc_info=True)
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='QUERY_BUILD_ERROR',
                    message='Failed to build query',
                    details={'error': str(qb_error)}
                )
            )
            return jsonify(error_response.model_dump()), 500

        # Execute query using chart repository
        try:
            db_adapter = current_app.db_adapter
            chart_repo = ChartRepository(db_adapter)

            results, execution_time_ms = chart_repo.execute_query(sql_query, params)

            transformed_data = chart_repo.transform_results(
                results,
                validated_request.queryType.value
            )

            total_count = len(transformed_data)  # Simple count for now

            metadata = ChartMetadata(
                total=total_count,
                executionTime=execution_time_ms,
                cached=False,  # TODO: Implement caching in Phase 3
                timestamp=datetime.utcnow(),
                sql=sql_query if request.environ.get('FLASK_ENV') == 'development' else None
            )

            response = ChartQueryResponse(
                queryType=validated_request.queryType,
                data=transformed_data,
                metadata=metadata
            )

            logger.info(
                f"Chart query completed: {total_count} results, {execution_time_ms}ms"
            )

            return jsonify(response.model_dump()), 200

        except TimeoutError as timeout_err:
            logger.error(f"Query timeout: {str(timeout_err)}")
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='QUERY_TIMEOUT',
                    message='Query execution exceeded timeout limit',
                    details={'timeout': ChartRepository.QUERY_TIMEOUT}
                )
            )
            return jsonify(error_response.model_dump()), 504

        except Exception as db_error:
            logger.error(f"Database execution error: {str(db_error)}", exc_info=True)
            error_response = ChartQueryErrorResponse(
                error=ErrorDetail(
                    code='DATABASE_ERROR',
                    message='Failed to execute query',
                    details={'error': str(db_error) if request.environ.get('FLASK_ENV') == 'development' else None}
                )
            )
            return jsonify(error_response.model_dump()), 500

    except Exception as e:
        logger.error(f"Error processing chart query: {str(e)}", exc_info=True)
        error_response = ChartQueryErrorResponse(
            error=ErrorDetail(
                code='INTERNAL_ERROR',
                message='An error occurred processing your query',
                details={'error': str(e)} if request.environ.get('FLASK_ENV') == 'development' else None
            )
        )
        return jsonify(error_response.model_dump()), 500


@charts_bp.route('/fields', methods=['GET'])
@jwt_required()
def get_fields():
    """
    Expose the field catalog to the chart query builder UI.

    Returns all 7 buckets (target / advanced / extended_attributes /
    enrichment / tags / posture / virtual) so every field usable in a
    filter or SELECT slot is discoverable.

    Returns:
        JSON response with catalog entries
    """
    try:
        return jsonify(build_catalog_response(current_app.field_resolver.catalog())), 200
    except Exception as e:
        logger.error(f"Error getting fields: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': {'code': 'INTERNAL_ERROR', 'message': str(e)}
        }), 500


@charts_bp.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for the charts API.

    Returns:
        JSON response indicating service health
    """
    return jsonify({
        'success': True,
        'service': 'charts-api',
        'status': 'healthy',
        'version': '1.0.0'
    }), 200
