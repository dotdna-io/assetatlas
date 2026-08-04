# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Validation Utilities

Provides decorators for request validation using Pydantic models and resource
existence checks, plus ensure_uuid for UUID path parameters.

Usage:
    from src.api.utils import validate_request, validate_query, require_resource
    from pydantic import BaseModel

    class UserCreate(BaseModel):
        name: str
        email: str

    @app.route('/api/users', methods=['POST'])
    @validate_request(UserCreate)
    def create_user(validated_data: UserCreate):
        # validated_data is a Pydantic model instance
        return {'user': create_user_in_db(validated_data.dict())}
"""

import logging
from functools import wraps
from typing import Callable, Type, Any, Optional
from uuid import UUID
from flask import request
from pydantic import BaseModel, ValidationError as PydanticValidationError

from src.api.middleware.error_handler import ValidationError as APIValidationError, NotFoundError

logger = logging.getLogger(__name__)


# =============================================================================
# Request Body Validation Decorator
# =============================================================================

def validate_request(model: Type[BaseModel], param_name: str = 'validated_data') -> Callable:
    """
    Decorator that validates request body using a Pydantic model.

    The validated data is passed to the endpoint function as a keyword argument.

    Args:
        model: Pydantic model class for validation
        param_name: Name of the parameter to inject (default: 'validated_data')

    Returns:
        Decorator function

    Raises:
        APIValidationError: If validation fails

    Example:
        class UserCreate(BaseModel):
            name: str
            email: EmailStr
            age: int = 18

        @app.route('/api/users', methods=['POST'])
        @validate_request(UserCreate)
        def create_user(validated_data: UserCreate):
            user = db.create_user(**validated_data.dict())
            return {'user': user}, 201
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Get JSON data from request
                data = request.get_json()
                if data is None:
                    raise APIValidationError(
                        "Request body must be valid JSON",
                        details={'content_type': request.content_type}
                    )

                # Validate using Pydantic model
                validated = model(**data)

                # Inject validated data into function kwargs
                kwargs[param_name] = validated

                return func(*args, **kwargs)

            except PydanticValidationError as e:
                # Format Pydantic validation errors
                errors = []
                for error in e.errors():
                    errors.append({
                        'field': '.'.join(str(loc) for loc in error['loc']),
                        'message': error['msg'],
                        'type': error['type']
                    })

                # Inline the field+message tuples so the failure is greppable
                # in plain text logs (the `extra` dict is invisible under the
                # default formatter). Truncate value-tracebacks Pydantic
                # appends to the message at the first newline.
                summary = "; ".join(
                    f"{e['field']}: {e['message'].splitlines()[0]}" for e in errors
                ) or "(no field details)"
                logger.warning(
                    f"Request validation failed for {func.__name__}: {summary}",
                    extra={'errors': errors}
                )

                raise APIValidationError(
                    "Request validation failed",
                    details={'errors': errors}
                )

            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"Invalid request data for {func.__name__}: {str(e)}")
                raise APIValidationError(str(e))

        return wrapper
    return decorator


# =============================================================================
# Query Parameter Validation Decorator
# =============================================================================

def validate_query(model: Type[BaseModel], param_name: str = 'query_params') -> Callable:
    """
    Decorator that validates query parameters using a Pydantic model.

    The validated query params are passed to the endpoint function as a keyword argument.

    Args:
        model: Pydantic model class for validation
        param_name: Name of the parameter to inject (default: 'query_params')

    Returns:
        Decorator function

    Raises:
        APIValidationError: If validation fails

    Example:
        class ListParams(BaseModel):
            page: int = 1
            limit: int = 50
            search: Optional[str] = None

        @app.route('/api/users')
        @validate_query(ListParams)
        def list_users(query_params: ListParams):
            users = db.get_users(
                page=query_params.page,
                limit=query_params.limit,
                search=query_params.search
            )
            return {'users': users}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Get query parameters
                query_data = request.args.to_dict()

                # Convert types (query params are always strings)
                # Pydantic will handle type conversion
                validated = model(**query_data)

                # Inject validated query params into function kwargs
                kwargs[param_name] = validated

                return func(*args, **kwargs)

            except PydanticValidationError as e:
                # Format Pydantic validation errors
                errors = []
                for error in e.errors():
                    errors.append({
                        'field': '.'.join(str(loc) for loc in error['loc']),
                        'message': error['msg'],
                        'type': error['type']
                    })

                logger.warning(
                    f"Query parameter validation failed for {func.__name__}",
                    extra={'errors': errors}
                )

                raise APIValidationError(
                    "Query parameter validation failed",
                    details={'errors': errors}
                )

        return wrapper
    return decorator


# =============================================================================
# Path Parameter Validation
# =============================================================================

def ensure_uuid(value: str, entity: str) -> str:
    """
    Validate a UUID path parameter, returning it canonically spelled.

    Repositories cast an id straight to `%(id)s::uuid`. Postgres raises on a
    spelling it cannot parse, and psycopg2's error is not a ValueError, so
    `handle_errors` falls through to its catch-all and answers a client typo
    with a 500 (#973). Handlers call this first so the cast never sees one.

    Returns the canonical spelling rather than the caller's, because Python
    parses forms Postgres rejects — 'urn:uuid:...', a hyphen after any group of
    one — so checking alone would move the 500 rather than remove it.

    The MCP layer's `_common.validate_uuid` asks the same question but returns a
    message for the LLM to read, since tools never raise. An API handler raises
    instead, so the `@handle_errors` chain it already carries shapes the reply.

    Args:
        value: The raw path parameter
        entity: Resource name for the error message (e.g. 'asset')

    Returns:
        The UUID in canonical lowercase 8-4-4-4-12 form

    Raises:
        APIValidationError: If value is not a UUID (400)

    Example:
        @app.route('/api/assets/<asset_id>/merge-keys')
        @jwt_required()
        @handle_errors
        @response_wrapper
        def get_merge_keys(asset_id: str):
            return service.get_merge_keys(ensure_uuid(asset_id, 'asset'))
    """
    try:
        return str(UUID(value))
    except ValueError:
        raise APIValidationError(
            f"Invalid {entity} ID: {value!r}. Expected a UUID like "
            "'550e8400-e29b-41d4-a716-446655440000'."
        )


# =============================================================================
# Resource Existence Validation Decorator
# =============================================================================

def require_resource(
    service: Any,
    id_param: str,
    resource_name: str = 'Resource',
    get_method: str = 'get_by_id',
    inject_as: Optional[str] = None
) -> Callable:
    """
    Decorator that checks if a resource exists before executing the endpoint.

    Optionally injects the resource into the function kwargs.

    Args:
        service: Service object with a get method
        id_param: Name of the URL parameter containing the resource ID
        resource_name: Name of resource for error messages (default: 'Resource')
        get_method: Name of the method to call on service (default: 'get_by_id')
        inject_as: If provided, inject the resource into kwargs with this name

    Returns:
        Decorator function

    Raises:
        NotFoundError: If resource doesn't exist

    Example:
        @app.route('/api/users/<int:user_id>')
        @require_resource(user_service, 'user_id', 'User', inject_as='user')
        def get_user(user_id: int, user: dict):
            # user is already fetched and validated
            return {'user': user}

        @app.route('/api/users/<int:user_id>', methods=['DELETE'])
        @require_resource(user_service, 'user_id', 'User')
        def delete_user(user_id: int):
            # Resource existence is validated, but not injected
            user_service.delete(user_id)
            return {'success': True}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get the resource ID from kwargs
            resource_id = kwargs.get(id_param)
            if resource_id is None:
                raise ValueError(f"Missing required parameter: {id_param}")

            # Fetch the resource
            try:
                get_func = getattr(service, get_method)
                resource = get_func(resource_id)
            except AttributeError:
                raise ValueError(f"Service does not have method: {get_method}")

            # Check if resource exists
            if resource is None:
                logger.info(f"{resource_name} not found: {resource_id}")
                raise NotFoundError(
                    f"{resource_name} not found",
                    details={'id': resource_id}
                )

            # Inject resource if requested
            if inject_as:
                kwargs[inject_as] = resource

            return func(*args, **kwargs)

        return wrapper
    return decorator


# =============================================================================
# Field Validation Helpers
# =============================================================================

def require_fields(required_fields: list) -> Callable:
    """
    Decorator that checks if required fields are present in request JSON.

    Args:
        required_fields: List of required field names

    Returns:
        Decorator function

    Raises:
        APIValidationError: If required fields are missing

    Example:
        @app.route('/api/users', methods=['POST'])
        @require_fields(['name', 'email'])
        def create_user():
            data = request.get_json()
            return {'user': create_user_in_db(data)}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json()
            if data is None:
                raise APIValidationError("Request body must be valid JSON")

            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                raise APIValidationError(
                    "Missing required fields",
                    details={'missing_fields': missing_fields}
                )

            return func(*args, **kwargs)

        return wrapper
    return decorator


def validate_enum(field: str, allowed_values: list) -> Callable:
    """
    Decorator that validates a field value is in allowed set.

    Args:
        field: Field name to validate
        allowed_values: List of allowed values

    Returns:
        Decorator function

    Raises:
        APIValidationError: If field value is not in allowed values

    Example:
        @app.route('/api/users', methods=['POST'])
        @validate_enum('role', ['admin', 'user', 'guest'])
        def create_user():
            data = request.get_json()
            return {'user': create_user_in_db(data)}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json()
            if data is None:
                raise APIValidationError("Request body must be valid JSON")

            value = data.get(field)
            if value is not None and value not in allowed_values:
                raise APIValidationError(
                    f"Invalid value for {field}",
                    details={
                        'field': field,
                        'value': value,
                        'allowed_values': allowed_values
                    }
                )

            return func(*args, **kwargs)

        return wrapper
    return decorator
