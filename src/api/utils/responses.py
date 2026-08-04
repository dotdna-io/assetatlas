# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Response Utilities

Provides consistent response formatting across all API endpoints with decorators
and helper functions for success/error responses.

Usage:
    from src.api.utils import response_wrapper, success_response

    @app.route('/api/resource')
    @response_wrapper
    def get_resource():
        return {'resource': data}  # Automatically wrapped in success response
"""

import logging
from functools import wraps
from typing import Callable, Any, Dict, Optional, Tuple, Union
from flask import jsonify
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# =============================================================================
# Response Models (Pydantic)
# =============================================================================

class SuccessResponse(BaseModel):
    """Standard success response format"""
    success: bool = True
    data: Any
    meta: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standard error response format"""
    success: bool = False
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None


class PaginatedResponse(BaseModel):
    """Paginated response format"""
    success: bool = True
    data: Any
    meta: Dict[str, Any]  # Includes pagination info


# =============================================================================
# Helper Functions
# =============================================================================

def success_response(
    data: Any,
    meta: Optional[Dict[str, Any]] = None,
    status_code: int = 200
) -> Tuple[Dict[str, Any], int]:
    """
    Create a standardized success response.

    Args:
        data: Response data (will be serialized to JSON)
        meta: Optional metadata (pagination, counts, etc.)
        status_code: HTTP status code (default: 200)

    Returns:
        Tuple of (response dict, status code)

    Example:
        return success_response({'user': user_data}, meta={'count': 1})
        # Returns: ({'success': True, 'data': {'user': ...}, 'meta': {'count': 1}}, 200)
    """
    response = {
        'success': True,
        'data': data
    }
    if meta:
        response['meta'] = meta

    return response, status_code


def error_response(
    error: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    status_code: int = 400
) -> Tuple[Dict[str, Any], int]:
    """
    Create a standardized error response.

    Args:
        error: Error type/code
        message: Human-readable error message
        details: Optional error details
        status_code: HTTP status code (default: 400)

    Returns:
        Tuple of (response dict, status code)

    Example:
        return error_response('validation_error', 'Name is required', status_code=400)
        # Returns: ({'success': False, 'error': 'validation_error', 'message': ...}, 400)
    """
    response = {
        'success': False,
        'error': error,
        'message': message
    }
    if details:
        response['details'] = details

    return response, status_code


# =============================================================================
# Response Wrapper Decorator
# =============================================================================

def response_wrapper(func: Callable) -> Callable:
    """
    Decorator that wraps endpoint responses in a standard format.

    This decorator automatically wraps successful responses in the format:
    {'success': True, 'data': <result>}

    It handles several return formats:
    1. Dict only: {'user': ...} -> {'success': True, 'data': {'user': ...}}
    2. Dict + status: ({'user': ...}, 201) -> ({'success': True, 'data': {...}}, 201)
    3. Already wrapped: {'success': True, 'data': ...} -> unchanged
    4. Tuple with (data, status, headers): Properly wrapped with all components

    Args:
        func: Endpoint function to wrap

    Returns:
        Wrapped function that returns consistent response format

    Example:
        @app.route('/api/users')
        @response_wrapper
        def list_users():
            return {'users': get_all_users()}
            # Automatically becomes: {'success': True, 'data': {'users': [...]}}

        @app.route('/api/users', methods=['POST'])
        @response_wrapper
        def create_user():
            user = create_user_in_db(request.json)
            return {'user': user}, 201
            # Becomes: ({'success': True, 'data': {'user': ...}}, 201)
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)

        # Handle different return formats
        if isinstance(result, tuple):
            # Format: (data, status_code) or (data, status_code, headers)
            if len(result) == 2:
                data, status_code = result
                headers = None
            elif len(result) == 3:
                data, status_code, headers = result
            else:
                raise ValueError(f"Invalid response tuple length: {len(result)}")

            # Check if already wrapped
            if isinstance(data, dict) and 'success' in data:
                # Already wrapped, return as-is
                if headers:
                    return data, status_code, headers
                return data, status_code

            # Wrap the data
            wrapped = {'success': True, 'data': data}
            if headers:
                return wrapped, status_code, headers
            return wrapped, status_code

        # Format: data only (dict or other)
        if isinstance(result, dict):
            # Check if already wrapped
            if 'success' in result:
                return result

            # Wrap the data
            return {'success': True, 'data': result}

        # For other types (Response objects, etc.), return as-is
        return result

    return wrapper


def json_response(func: Callable) -> Callable:
    """
    Decorator that ensures response is JSON and properly formatted.

    Similar to response_wrapper but always calls jsonify() on the result.

    Example:
        @app.route('/api/status')
        @json_response
        def get_status():
            return {'status': 'online'}
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)

        # Handle tuple responses
        if isinstance(result, tuple):
            if len(result) == 2:
                data, status_code = result
                return jsonify(data), status_code
            elif len(result) == 3:
                data, status_code, headers = result
                return jsonify(data), status_code, headers

        # Handle dict responses
        if isinstance(result, dict):
            return jsonify(result)

        # Return as-is for other types
        return result

    return wrapper


def with_meta(meta_func: Callable) -> Callable:
    """
    Decorator factory that adds metadata to responses.

    The meta_func should accept the result and return a dict of metadata.

    Args:
        meta_func: Function that generates metadata dict

    Returns:
        Decorator function

    Example:
        def add_count(result):
            return {'count': len(result['items'])}

        @app.route('/api/items')
        @with_meta(add_count)
        def list_items():
            return {'items': get_items()}
            # Returns: {'success': True, 'data': {'items': [...]}, 'meta': {'count': N}}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)

            # Extract data and status code
            if isinstance(result, tuple):
                data, status_code = result if len(result) == 2 else (result[0], 200)
            else:
                data, status_code = result, 200

            # Generate metadata
            meta = meta_func(data)

            # Wrap with metadata
            wrapped = {
                'success': True,
                'data': data,
                'meta': meta
            }

            return wrapped, status_code

        return wrapper
    return decorator
