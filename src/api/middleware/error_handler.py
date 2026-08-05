# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Error Handler Middleware

Provides consistent error handling across all API endpoints with custom exception classes
and a decorator for automatic error wrapping.

Usage:
    from src.api.middleware import handle_errors, ValidationError

    @app.route('/api/resource')
    @handle_errors
    def get_resource():
        if not valid:
            raise ValidationError("Invalid input")
        return {'data': resource}
"""

import logging
import os
from functools import wraps
from typing import Callable, Any, Tuple, Dict, Optional
from flask import jsonify, request
from flask_jwt_extended.exceptions import JWTExtendedException

logger = logging.getLogger(__name__)

# Import get_request_id for adding request IDs to error responses
def get_request_id():
    """Lazy import to avoid circular dependency"""
    try:
        from src.api.middleware.request_id import get_request_id as _get_request_id
        return _get_request_id()
    except:
        return None


def _get_sanitized_request_body() -> Optional[Dict[str, Any]]:
    """
    Get request body with sensitive data sanitized and large payloads truncated.

    Returns:
        Sanitized request body dict, or None if:
        - LOG_REQUEST_BODY_ON_ERROR is False
        - Request has no body
        - Body is not JSON
        - Body parsing fails
    """
    # Check if request body logging is enabled
    if not os.getenv('LOG_REQUEST_BODY_ON_ERROR', 'true').lower() in ('true', '1', 'yes'):
        return None

    try:
        # Only log JSON request bodies
        if not request.is_json:
            return {'_note': f'Non-JSON content type: {request.content_type}'}

        # Get request data
        data = request.get_json(silent=True)
        if not data:
            return None

        # Import sensitive data filter
        from src.api.utils.logging_config import SensitiveDataFilter
        sanitizer = SensitiveDataFilter()

        # Sanitize the data
        sanitized = sanitizer._sanitize_value(data)

        # Convert to string to check size
        import json
        body_str = json.dumps(sanitized, default=str)

        # Truncate if larger than 1KB (1024 characters)
        if len(body_str) > 1024:
            return {
                '_truncated': True,
                '_original_size': len(body_str),
                '_data': body_str[:1024] + '... [TRUNCATED]'
            }

        return sanitized

    except Exception as e:
        # Don't fail error handling if body parsing fails
        logger.debug(f"Failed to parse request body for error logging: {e}")
        return {'_error': f'Failed to parse body: {str(e)}'}



# =============================================================================
# Custom Exception Classes
# =============================================================================
# The hierarchy lives in src/modules/shared/exceptions.py so domain code
# (src/modules/) can raise these without importing from src.api (layering
# rule, tests/unit/test_layering.py). Re-exported here so existing
# `from src.api.middleware.error_handler import ...` callers keep working
# against the same class objects.

from src.modules.shared.exceptions import (  # noqa: F401
    APIError,
    ValidationError,
    NotFoundError,
    ConflictError,
    AuthError,
    UnauthorizedError,
    ForbiddenError,
    BadRequestError,
    ServiceUnavailableError,
)


# =============================================================================
# Error Handler Decorator
# =============================================================================

def handle_errors(func: Callable) -> Callable:
    """
    Decorator that wraps endpoint functions with consistent error handling.

    Catches exceptions and returns standardized JSON error responses:
    - APIError subclasses: Converted to JSON with appropriate status code
    - Pydantic ValidationError: Converted to 400 Bad Request
    - Other exceptions: Logged and converted to 500 Internal Server Error

    Args:
        func: Endpoint function to wrap

    Returns:
        Wrapped function with error handling

    Example:
        @app.route('/api/users/<int:user_id>')
        @handle_errors
        def get_user(user_id: int):
            user = db.get_user(user_id)
            if not user:
                raise NotFoundError(f"User {user_id} not found")
            return {'user': user}
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Execute the endpoint function
            return func(*args, **kwargs)

        except APIError as e:
            # Handle custom API errors
            request_body = _get_sanitized_request_body()
            log_extra = {'details': e.details}
            if request_body:
                log_extra['request_body'] = request_body

            logger.warning(
                f"{e.error_type}: {e.message}",
                extra=log_extra
            )
            return jsonify(e.to_dict()), e.status_code

        except JWTExtendedException as e:
            # Handle JWT authentication errors (missing token, invalid token, expired, etc.)
            request_body = _get_sanitized_request_body()
            log_extra = {}
            if request_body:
                log_extra['request_body'] = request_body

            logger.warning(
                f"JWT error in {func.__name__}: {str(e)}",
                extra=log_extra if log_extra else None
            )
            return jsonify({
                'error': 'unauthorized',
                'message': 'Authentication required',
                'request_id': get_request_id()
            }), 401

        except ValueError as e:
            # Handle Python ValueError as validation error
            request_body = _get_sanitized_request_body()
            log_extra = {}
            if request_body:
                log_extra['request_body'] = request_body

            logger.warning(
                f"ValueError in {func.__name__}: {str(e)}",
                extra=log_extra if log_extra else None
            )
            return jsonify({
                'error': 'validation_error',
                'message': str(e),
                'request_id': get_request_id()
            }), 400

        except KeyError as e:
            # Handle missing keys as validation errors
            request_body = _get_sanitized_request_body()
            log_extra = {}
            if request_body:
                log_extra['request_body'] = request_body

            logger.warning(
                f"KeyError in {func.__name__}: {str(e)}",
                extra=log_extra if log_extra else None
            )
            return jsonify({
                'error': 'validation_error',
                'message': f"Missing required field: {str(e)}",
                'request_id': get_request_id()
            }), 400

        except Exception as e:
            # Handle unexpected errors
            request_body = _get_sanitized_request_body()
            log_extra = {}
            if request_body:
                log_extra['request_body'] = request_body

            logger.error(
                f"Unexpected error in {func.__name__}: {str(e)}",
                extra=log_extra if log_extra else None,
                exc_info=True
            )
            return jsonify({
                'error': 'internal_error',
                'message': 'An unexpected error occurred',
                'request_id': get_request_id()
            }), 500

    return wrapper


def handle_pydantic_errors(func: Callable) -> Callable:
    """
    Decorator specifically for handling Pydantic validation errors.

    This is useful when endpoints use Pydantic models for request validation.

    Example:
        from pydantic import BaseModel, ValidationError as PydanticValidationError

        @app.route('/api/users', methods=['POST'])
        @handle_pydantic_errors
        def create_user():
            data = request.get_json()
            user = UserCreate(**data)  # May raise PydanticValidationError
            return {'user': create_user_in_db(user)}
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Check if it's a Pydantic validation error
            if e.__class__.__name__ == 'ValidationError':
                logger.warning(f"Pydantic validation error in {func.__name__}: {str(e)}")
                # Extract validation errors from Pydantic
                errors = []
                for error in e.errors():
                    errors.append({
                        'field': '.'.join(str(loc) for loc in error['loc']),
                        'message': error['msg'],
                        'type': error['type']
                    })
                return jsonify({
                    'error': 'validation_error',
                    'message': 'Request validation failed',
                    'details': {'errors': errors}
                }), 400
            raise

    return wrapper


# =============================================================================
# Error Handler Registration for Flask App
# =============================================================================

def register_error_handlers(app):
    """
    Register global error handlers for Flask app.

    Call this during app initialization to set up consistent error handling
    across the entire application.

    Args:
        app: Flask application instance

    Example:
        from src.api.middleware.error_handler import register_error_handlers

        app = Flask(__name__)
        register_error_handlers(app)
    """

    @app.errorhandler(APIError)
    def handle_api_error(error: APIError):
        """Handle all APIError subclasses"""
        return jsonify(error.to_dict()), error.status_code

    @app.errorhandler(400)
    def handle_bad_request(error):
        """Handle 400 Bad Request"""
        return jsonify({
            'error': 'bad_request',
            'message': str(error.description) if hasattr(error, 'description') else 'Bad request'
        }), 400

    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 Not Found"""
        return jsonify({
            'error': 'not_found',
            'message': 'Resource not found'
        }), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        """Handle 405 Method Not Allowed"""
        return jsonify({
            'error': 'method_not_allowed',
            'message': 'Method not allowed for this endpoint'
        }), 405

    @app.errorhandler(500)
    def handle_internal_error(error):
        """Handle 500 Internal Server Error"""
        request_body = _get_sanitized_request_body()
        log_extra = {}
        if request_body:
            log_extra['request_body'] = request_body

        logger.error(
            f"Internal server error: {str(error)}",
            extra=log_extra if log_extra else None,
            exc_info=True
        )
        return jsonify({
            'error': 'internal_error',
            'message': 'An unexpected error occurred'
        }), 500
