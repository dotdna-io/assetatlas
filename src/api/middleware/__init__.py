# SPDX-License-Identifier: AGPL-3.0-or-later
"""
API Middleware

Middleware components for error handling, validation, and request processing.
"""

from .error_handler import (
    handle_errors,
    APIError,
    ValidationError,
    NotFoundError,
    ConflictError,
    AuthError,
    UnauthorizedError,
    ForbiddenError
)

__all__ = [
    'handle_errors',
    'APIError',
    'ValidationError',
    'NotFoundError',
    'ConflictError',
    'AuthError',
    'UnauthorizedError',
    'ForbiddenError'
]
