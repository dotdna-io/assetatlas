# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Transformation Validation Service

Validates field transformation configurations before they are saved to the database.
Ensures transformations are well-formed and will execute correctly during data collection.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple


logger = logging.getLogger(__name__)


class TransformationValidator:
    """
    Validates field transformation configurations.

    Validates:
    - Transformation structure and format
    - Operation types and parameters
    - Regex pattern compilation
    - Conditional logic structure
    - Field references (optional, requires available_fields)
    """

    # Valid operation types
    VALID_OPERATION_TYPES = {
        'field', 'static', 'current_timestamp', 'trim', 'uppercase', 'lowercase',
        'regex_extract', 'regex_replace', 'coalesce', 'conditional'
    }

    # Operations that require specific parameters
    OPERATION_REQUIREMENTS = {
        'field': ['field'],  # Must have 'field' parameter
        'static': ['value'],  # Must have 'value' parameter
        'regex_extract': ['pattern'],  # Must have 'pattern' parameter
        'regex_replace': ['pattern'],  # Must have 'pattern' parameter
        'coalesce': ['fields', 'default']  # Must have fields array and default value
    }

    def __init__(self, available_fields: Optional[List[str]] = None):
        """
        Initialize validator.

        Args:
            available_fields: Optional list of available source fields for validation
        """
        self.available_fields = set(available_fields) if available_fields else None

    def validate_field_mapping(self, field_mapping: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate an entire field mapping configuration.

        Args:
            field_mapping: Dictionary of field mappings
                Example: {
                    "hostname": "deviceName",
                    "os": {
                        "type": "transform",
                        "operations": [...]
                    }
                }

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        if not isinstance(field_mapping, dict):
            return False, ["field_mapping must be a dictionary"]

        errors = []

        for target_field, source_path in field_mapping.items():
            # Validate target field name
            if not isinstance(target_field, str) or not target_field:
                errors.append(f"Invalid target field name: {target_field}")
                continue

            # Check if this is a transformation
            if isinstance(source_path, dict) and source_path.get('type') == 'transform':
                is_valid, transform_errors = self.validate_transformation(source_path)
                if not is_valid:
                    errors.extend([f"Field '{target_field}': {err}" for err in transform_errors])
            elif isinstance(source_path, list):
                if not source_path:
                    errors.append(f"Field '{target_field}': Empty source list")
                for i, path in enumerate(source_path):
                    if not isinstance(path, str) or not path.strip():
                        errors.append(
                            f"Field '{target_field}': Invalid source path at index {i}"
                        )
                    elif self.available_fields is not None and path not in self.available_fields:
                        base_field = path.split('.')[0].split('[')[0]
                        if base_field not in self.available_fields:
                            errors.append(
                                f"Field '{target_field}': Source field '{path}' not found in available fields"
                            )
            elif isinstance(source_path, str):
                # Simple field mapping - validate field reference if available_fields is set
                if self.available_fields and source_path not in self.available_fields:
                    errors.append(f"Field '{target_field}': Source field '{source_path}' not found in available fields")
            else:
                errors.append(f"Field '{target_field}': Invalid mapping type (must be string, list of strings, or transformation object)")

        return len(errors) == 0, errors

    def validate_transformation(self, transformation: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate a transformation configuration.

        Args:
            transformation: Transformation configuration
                Example: {
                    "type": "transform",
                    "operations": [
                        {"type": "field", "field": "operatingSystem"},
                        {"type": "static", "value": " "},
                        {"type": "field", "field": "osVersion"}
                    ]
                }

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check structure
        if not isinstance(transformation, dict):
            return False, ["Transformation must be a dictionary"]

        if transformation.get('type') != 'transform':
            errors.append(f"Transformation type must be 'transform', got: {transformation.get('type')}")

        operations = transformation.get('operations')
        if not operations:
            errors.append("Transformation must have 'operations' list")
            return False, errors

        if not isinstance(operations, list):
            errors.append("Transformation 'operations' must be a list")
            return False, errors

        if len(operations) == 0:
            errors.append("Transformation must have at least one operation")
            return False, errors

        # Validate each operation
        for idx, operation in enumerate(operations):
            op_errors = self._validate_operation(operation, f"Operation {idx + 1}")
            errors.extend(op_errors)

        # Check that transformation has at least one data source (field, static, current_timestamp, or coalesce)
        has_data_source = any(
            op.get('type') in ['field', 'static', 'current_timestamp', 'coalesce', 'conditional'] for op in operations
        )
        if not has_data_source:
            errors.append("Transformation must have at least one 'field', 'static', 'current_timestamp', or 'coalesce' operation as a data source")

        return len(errors) == 0, errors

    def _validate_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """
        Validate a single operation.

        Args:
            operation: Operation configuration
            context: Context string for error messages (e.g., "Operation 1")

        Returns:
            List of error messages
        """
        errors = []

        if not isinstance(operation, dict):
            return [f"{context}: Operation must be a dictionary"]

        op_type = operation.get('type')
        if not op_type:
            errors.append(f"{context}: Operation must have 'type' field")
            return errors

        # Check if operation type is valid
        if op_type not in self.VALID_OPERATION_TYPES:
            errors.append(f"{context}: Invalid operation type '{op_type}'. Valid types: {', '.join(sorted(self.VALID_OPERATION_TYPES))}")
            return errors

        # Validate operation-specific requirements
        if op_type in self.OPERATION_REQUIREMENTS:
            required_params = self.OPERATION_REQUIREMENTS[op_type]
            for param in required_params:
                if param not in operation:
                    errors.append(f"{context}: '{op_type}' operation requires '{param}' parameter")

        # Validate specific operation types
        if op_type == 'field':
            errors.extend(self._validate_field_operation(operation, context))
        elif op_type == 'static':
            errors.extend(self._validate_static_operation(operation, context))
        elif op_type == 'regex_extract':
            errors.extend(self._validate_regex_extract_operation(operation, context))
        elif op_type == 'regex_replace':
            errors.extend(self._validate_regex_replace_operation(operation, context))
        elif op_type == 'coalesce':
            errors.extend(self._validate_coalesce_operation(operation, context))
        elif op_type == 'conditional':
            errors.extend(self._validate_conditional_operation(operation, context))

        return errors

    def _validate_field_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate field operation."""
        errors = []

        field_path = operation.get('field')
        if not field_path or not isinstance(field_path, str):
            errors.append(f"{context}: 'field' parameter must be a non-empty string")
            return errors

        # Validate field reference if available_fields is set
        if self.available_fields:
            # Handle dot notation (e.g., "network.ipAddress")
            base_field = field_path.split('.')[0].split('[')[0]
            if base_field not in self.available_fields:
                errors.append(f"{context}: Field '{field_path}' not found in available source fields")

        return errors

    def _validate_static_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate static operation."""
        errors = []

        if 'value' not in operation:
            errors.append(f"{context}: 'static' operation requires 'value' parameter")
        # Note: value can be any type (string, number, etc.) or even empty string

        return errors

    def _validate_regex_extract_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate regex_extract operation."""
        errors = []

        pattern = operation.get('pattern')
        if not pattern or not isinstance(pattern, str):
            errors.append(f"{context}: 'pattern' must be a non-empty string")
            return errors

        # Try to compile the regex pattern
        try:
            re.compile(pattern)
        except re.error as e:
            errors.append(f"{context}: Invalid regex pattern '{pattern}': {str(e)}")

        # Validate group parameter if present
        if 'group' in operation:
            group = operation.get('group')
            if not isinstance(group, int) or group < 0:
                errors.append(f"{context}: 'group' must be a non-negative integer")

        return errors

    def _validate_regex_replace_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate regex_replace operation."""
        errors = []

        pattern = operation.get('pattern')
        if not pattern or not isinstance(pattern, str):
            errors.append(f"{context}: 'pattern' must be a non-empty string")
            return errors

        # Try to compile the regex pattern
        try:
            re.compile(pattern)
        except re.error as e:
            errors.append(f"{context}: Invalid regex pattern '{pattern}': {str(e)}")

        # Replacement is optional (defaults to empty string)
        if 'replacement' in operation:
            replacement = operation.get('replacement')
            if not isinstance(replacement, str):
                errors.append(f"{context}: 'replacement' must be a string")

        return errors

    def _validate_coalesce_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate coalesce operation."""
        errors = []

        # Validate fields parameter
        fields = operation.get('fields')
        if not fields:
            errors.append(f"{context}: 'coalesce' operation requires 'fields' parameter")
        elif not isinstance(fields, list):
            errors.append(f"{context}: 'fields' must be a list")
        else:
            if len(fields) == 0:
                errors.append(f"{context}: 'fields' list must contain at least one field")

            # Validate each field in the list
            for idx, field_path in enumerate(fields):
                if not isinstance(field_path, str):
                    errors.append(f"{context}: Field at index {idx} must be a string")
                elif not field_path:
                    errors.append(f"{context}: Field at index {idx} cannot be empty")
                elif self.available_fields:
                    # Validate field reference if available_fields is set
                    base_field = field_path.split('.')[0].split('[')[0]
                    if base_field not in self.available_fields:
                        errors.append(f"{context}: Field '{field_path}' at index {idx} not found in available source fields")

        # Validate default parameter (must exist but can be empty string)
        if 'default' not in operation:
            errors.append(f"{context}: 'coalesce' operation requires 'default' parameter")
        elif not isinstance(operation.get('default'), str):
            errors.append(f"{context}: 'default' must be a string")

        return errors


    def _validate_conditional_operation(self, operation: Dict[str, Any], context: str) -> List[str]:
        """Validate conditional operation."""
        errors = []

        # Valid condition types
        VALID_CONDITION_TYPES = {'field_exists', 'field_equals', 'field_empty', 'field_contains'}
        # Condition types that require a 'value' parameter
        CONDITION_REQUIRES_VALUE = {'field_equals', 'field_contains'}
        # Valid branch operation types
        VALID_BRANCH_TYPES = {'field', 'static'}

        # Validate condition
        condition = operation.get('condition')
        if not condition or not isinstance(condition, dict):
            errors.append(f"{context}: 'conditional' operation requires 'condition' parameter")
            return errors

        condition_type = condition.get('type')
        if not condition_type:
            errors.append(f"{context}: Condition must have 'type' field")
        elif condition_type not in VALID_CONDITION_TYPES:
            errors.append(
                f"{context}: Invalid condition type '{condition_type}'. "
                f"Valid types: {', '.join(sorted(VALID_CONDITION_TYPES))}"
            )
        else:
            # Validate condition-specific requirements
            if not condition.get('field'):
                errors.append(f"{context}: Condition must have 'field' parameter")

            if condition_type in CONDITION_REQUIRES_VALUE and 'value' not in condition:
                errors.append(f"{context}: '{condition_type}' condition requires 'value' parameter")

        # Validate 'then' branch
        then_ops = operation.get('then')
        if then_ops is not None:
            if not isinstance(then_ops, list):
                errors.append(f"{context}: 'then' must be a list")
            else:
                for idx, branch_op in enumerate(then_ops):
                    if not isinstance(branch_op, dict):
                        errors.append(f"{context}: 'then' operation {idx + 1} must be a dictionary")
                    else:
                        branch_type = branch_op.get('type')
                        if branch_type not in VALID_BRANCH_TYPES:
                            errors.append(
                                f"{context}: 'then' operation {idx + 1} type can only be "
                                f"'field' or 'static', got '{branch_type}'"
                            )

        # Validate 'else' branch (optional)
        else_ops = operation.get('else')
        if else_ops is not None:
            if not isinstance(else_ops, list):
                errors.append(f"{context}: 'else' must be a list")
            else:
                for idx, branch_op in enumerate(else_ops):
                    if not isinstance(branch_op, dict):
                        errors.append(f"{context}: 'else' operation {idx + 1} must be a dictionary")
                    else:
                        branch_type = branch_op.get('type')
                        if branch_type not in VALID_BRANCH_TYPES:
                            errors.append(
                                f"{context}: 'else' operation {idx + 1} type can only be "
                                f"'field' or 'static', got '{branch_type}'"
                            )

        return errors


def validate_field_mapping(field_mapping: Dict[str, Any],
                          available_fields: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """
    Convenience function to validate field mapping.

    Args:
        field_mapping: Field mapping configuration
        available_fields: Optional list of available source fields

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    validator = TransformationValidator(available_fields)
    return validator.validate_field_mapping(field_mapping)


def validate_transformation(transformation: Dict[str, Any],
                           available_fields: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """
    Convenience function to validate a single transformation.

    Args:
        transformation: Transformation configuration
        available_fields: Optional list of available source fields

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    validator = TransformationValidator(available_fields)
    return validator.validate_transformation(transformation)
