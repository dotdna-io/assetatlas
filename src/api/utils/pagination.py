# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pagination Utilities

Provides decorators and models for consistent pagination across list endpoints.

Usage:
    from src.api.utils import paginate, PaginationParams

    @app.route('/api/users')
    @paginate(default=50, max=200)
    def list_users(page: int, limit: int, offset: int):
        users = db.get_users(limit=limit, offset=offset)
        total = db.count_users()
        return {
            'users': users,
            'total': total  # Required for pagination metadata
        }
"""

import logging
import math
from functools import wraps
from typing import Callable, Any, Dict, Optional
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================

class PaginationParams(BaseModel):
    """Query parameters for pagination"""
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int = Field(default=50, ge=1, le=10000, description="Items per page")
    offset: Optional[int] = Field(default=None, ge=0, description="Offset (calculated if not provided)")

    @validator('offset', always=True)
    def calculate_offset(cls, v, values):
        """Calculate offset from page and limit if not provided"""
        if v is not None:
            return v
        page = values.get('page', 1)
        limit = values.get('limit', 50)
        return (page - 1) * limit

    class Config:
        json_schema_extra = {
            "example": {
                "page": 1,
                "limit": 50
            }
        }


class PaginationMeta(BaseModel):
    """Pagination metadata for responses"""
    page: int = Field(description="Current page number")
    limit: int = Field(description="Items per page")
    total: int = Field(description="Total number of items")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")

    @classmethod
    def from_params(
        cls,
        page: int,
        limit: int,
        total: int
    ) -> 'PaginationMeta':
        """
        Create pagination metadata from parameters.

        Args:
            page: Current page number
            limit: Items per page
            total: Total number of items

        Returns:
            PaginationMeta instance
        """
        pages = math.ceil(total / limit) if limit > 0 else 0
        return cls(
            page=page,
            limit=limit,
            total=total,
            pages=pages,
            has_next=page < pages,
            has_prev=page > 1
        )

    class Config:
        json_schema_extra = {
            "example": {
                "page": 1,
                "limit": 50,
                "total": 247,
                "pages": 5,
                "has_next": True,
                "has_prev": False
            }
        }


# =============================================================================
# Pagination Decorator
# =============================================================================

def paginate(
    default: int = 50,
    max_limit: int = 10000,
    inject_params: bool = True
) -> Callable:
    """
    Decorator that adds pagination support to list endpoints.

    Extracts page/limit from query parameters, calculates offset, and injects
    pagination parameters into the function. Also automatically adds pagination
    metadata to the response.

    Args:
        default: Default limit if not specified (default: 50)
        max_limit: Maximum allowed limit (default: 200)
        inject_params: Whether to inject page, limit, offset as kwargs (default: True)

    Returns:
        Decorator function

    Usage:
        @app.route('/api/users')
        @paginate(default=50, max=200)
        def list_users(page: int, limit: int, offset: int):
            users = db.get_users(limit=limit, offset=offset)
            total = db.count_users()
            return {
                'users': users,
                'total': total  # Required for metadata generation
            }
            # Automatically becomes:
            # {
            #     'success': True,
            #     'data': {'users': [...]},
            #     'meta': {
            #         'pagination': {
            #             'page': 1,
            #             'limit': 50,
            #             'total': 247,
            #             'pages': 5,
            #             'has_next': True,
            #             'has_prev': False
            #         }
            #     }
            # }
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            from flask import request

            # Get pagination parameters from query string
            try:
                page = int(request.args.get('page', 1))
                limit = int(request.args.get('limit', default))
            except (ValueError, TypeError):
                from src.api.middleware.error_handler import ValidationError
                raise ValidationError(
                    "Invalid pagination parameters",
                    details={'page': 'must be an integer', 'limit': 'must be an integer'}
                )

            # Validate pagination parameters
            if page < 1:
                from src.api.middleware.error_handler import ValidationError
                raise ValidationError(
                    "Invalid page number",
                    details={'page': 'must be >= 1'}
                )

            if limit < 1 or limit > max_limit:
                from src.api.middleware.error_handler import ValidationError
                raise ValidationError(
                    "Invalid limit",
                    details={'limit': f'must be between 1 and {max_limit}'}
                )

            # Calculate offset
            offset = (page - 1) * limit

            # Inject pagination parameters if requested
            if inject_params:
                kwargs['page'] = page
                kwargs['limit'] = limit
                kwargs['offset'] = offset

            # Execute the endpoint function
            result = func(*args, **kwargs)

            # Handle tuple responses (data, status_code)
            if isinstance(result, tuple):
                data, status_code = result if len(result) == 2 else (result[0], 200)
            else:
                data, status_code = result, 200

            # Extract total count from response
            total = None
            if isinstance(data, dict):
                total = data.get('total')
                # Remove 'total' from data if it exists (will be in meta)
                if 'total' in data:
                    data = {k: v for k, v in data.items() if k != 'total'}

            # Generate pagination metadata
            if total is not None:
                pagination_meta = PaginationMeta.from_params(
                    page=page,
                    limit=limit,
                    total=total
                )

                # Add pagination to metadata
                meta = {'pagination': pagination_meta.dict()}

                # Wrap response with metadata
                wrapped = {
                    'success': True,
                    'data': data,
                    'meta': meta
                }

                return wrapped, status_code

            # No total count provided, return without pagination metadata
            logger.warning(
                f"Endpoint {func.__name__} decorated with @paginate but didn't return 'total' count"
            )
            return data, status_code

        return wrapper
    return decorator


# =============================================================================
# Helper Functions
# =============================================================================

def get_pagination_params(default_limit: int = 50, max_limit: int = 10000) -> PaginationParams:
    """
    Extract and validate pagination parameters from request.

    Args:
        default_limit: Default limit if not specified
        max_limit: Maximum allowed limit

    Returns:
        PaginationParams instance

    Raises:
        ValidationError: If parameters are invalid

    Example:
        from flask import request
        from src.api.utils.pagination import get_pagination_params

        @app.route('/api/users')
        def list_users():
            params = get_pagination_params()
            users = db.get_users(limit=params.limit, offset=params.offset)
            return {'users': users, 'total': db.count_users()}
    """
    from flask import request
    from src.api.middleware.error_handler import ValidationError

    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', default_limit))
    except (ValueError, TypeError):
        raise ValidationError(
            "Invalid pagination parameters",
            details={'page': 'must be an integer', 'limit': 'must be an integer'}
        )

    # Validate and clamp limit
    if limit < 1:
        limit = default_limit
    elif limit > max_limit:
        limit = max_limit

    # Validate page
    if page < 1:
        page = 1

    return PaginationParams(page=page, limit=limit)


def create_pagination_meta(
    page: int,
    limit: int,
    total: int
) -> Dict[str, Any]:
    """
    Create pagination metadata dictionary.

    Args:
        page: Current page number
        limit: Items per page
        total: Total number of items

    Returns:
        Pagination metadata dict

    Example:
        meta = create_pagination_meta(page=1, limit=50, total=247)
        # Returns:
        # {
        #     'page': 1,
        #     'limit': 50,
        #     'total': 247,
        #     'pages': 5,
        #     'has_next': True,
        #     'has_prev': False
        # }
    """
    pagination_meta = PaginationMeta.from_params(page, limit, total)
    return pagination_meta.dict()
