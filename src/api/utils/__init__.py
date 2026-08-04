# SPDX-License-Identifier: AGPL-3.0-or-later
"""
API Utilities

Utility functions and decorators for API endpoints including response formatting,
validation, and pagination.
"""

from .responses import response_wrapper, success_response, error_response
from .validation import validate_request, validate_query, require_resource
from .pagination import paginate, PaginationParams, PaginationMeta

__all__ = [
    # Response utilities
    'response_wrapper',
    'success_response',
    'error_response',

    # Validation decorators
    'validate_request',
    'validate_query',
    'require_resource',

    # Pagination utilities
    'paginate',
    'PaginationParams',
    'PaginationMeta',
]
