# SPDX-License-Identifier: AGPL-3.0-or-later
"""API error hierarchy shared between the API layer and domain modules.

These are plain ``Exception`` subclasses with an HTTP status/error-type
mapping attached. They live in the shared layer (not ``src/api``) because
domain code — repositories and services under ``src/modules/`` — raises them
to signal client-correctable conditions (conflicts, validation failures,
missing resources) without being allowed to import from ``src/api``
(see tests/unit/test_layering.py). The API layer's ``handle_errors``
decorator catches ``APIError`` and renders the JSON response; the classes
themselves carry no Flask coupling.

``src.api.middleware.error_handler`` re-exports the full hierarchy, so
existing ``from src.api.middleware.error_handler import ...`` callers keep
working against the same class objects.
"""

from typing import Any, Dict


class APIError(Exception):
    """Base class for all API errors"""
    status_code = 500
    error_type = 'internal_error'

    def __init__(self, message: str, details: Dict[str, Any] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON response"""
        result = {
            'error': self.error_type,
            'message': self.message
        }
        if self.details:
            result['details'] = self.details
        return result


class ValidationError(APIError):
    """Raised when request validation fails"""
    status_code = 400
    error_type = 'validation_error'


class NotFoundError(APIError):
    """Raised when requested resource is not found"""
    status_code = 404
    error_type = 'not_found'


class ConflictError(APIError):
    """Raised when request conflicts with existing resource"""
    status_code = 409
    error_type = 'conflict'


class AuthError(APIError):
    """Base class for authentication/authorization errors"""
    status_code = 401
    error_type = 'auth_error'


class UnauthorizedError(AuthError):
    """Raised when authentication is required but not provided"""
    status_code = 401
    error_type = 'unauthorized'


class ForbiddenError(AuthError):
    """Raised when user lacks permission for requested action"""
    status_code = 403
    error_type = 'forbidden'


class BadRequestError(APIError):
    """Raised when request is malformed or invalid"""
    status_code = 400
    error_type = 'bad_request'


class ServiceUnavailableError(APIError):
    """Raised when a feature is registered but its backing service is not
    initialized (e.g. chat blueprint registered but no LLM provider
    configured). Distinguishes "not configured yet" from "internal error"."""
    status_code = 503
    error_type = 'service_unavailable'
