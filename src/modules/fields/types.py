# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared enums for field data types and filter operators.

Moved from src/modules/tag_rules/config/field_mappings.py as part of the
FieldId restructure (issue #455).
"""

from enum import Enum


class FieldDataType(str, Enum):
    STRING = "string"
    STRING_ARRAY = "string_array"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    JSONB = "jsonb"
    JSONB_ARRAY = "jsonb_array"


class FilterOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not contains"
    STARTS_WITH = "starts with"
    ENDS_WITH = "ends with"
    GREATER_THAN = "greater than"
    LESS_THAN = "less than"
    GREATER_THAN_OR_EQUAL = "greater than or equal"
    LESS_THAN_OR_EQUAL = "less than or equal"
    IN = "any of"
    IS_NULL = "is empty"
    IS_NOT_NULL = "is not empty"
    ALL_OF = "all of"
    NONE_OF = "none of"
    IS_TRUE = "is true"
    IS_FALSE = "is false"
    BETWEEN = "between"
    WITHIN_LAST = "in the last"

    @classmethod
    def _missing_(cls, value):
        # The frontend stores operators in an underscored form ("is_empty",
        # "not_equals") for internal state and select-option keys. Accept
        # those at the API boundary so payloads built from FE state validate
        # without callers having to denormalize. The canonical stored form
        # is still the spaced display string (the enum's .value).
        if isinstance(value, str):
            normalized = value.replace("_", " ")
            for member in cls:
                if member.value == normalized:
                    return member
            # Legacy aliases — older posture/tag rules persisted "in" before
            # the enum canonicalised to "any of". Map them so the API and the
            # pipeline (which already tolerates the legacy form via raw SQL)
            # agree, and the posture wizard's preview can reload existing
            # rules without erroring.
            _ALIASES = {
                "in": cls.IN,
            }
            if value in _ALIASES:
                return _ALIASES[value]
            # Delegate to the operator registry so canonical codes
            # (eq/neq/any/empty/all/none/...) parse. Map the resolved code back
            # to the enum member whose value is the spaced label. Every
            # OPERATOR_REGISTRY code has a member here (guarded by
            # test_every_registry_code_resolves_through_filter_operator), so a
            # code the registry / operator_specs can emit never falls through to
            # ValueError — the tag/posture rule wizards surface all/none.
            try:
                from src.modules.fields.operators import resolve as _resolve_op
                code = _resolve_op(value).code
            except Exception:
                return None
            _CODE_TO_MEMBER = {
                "eq": cls.EQUALS, "neq": cls.NOT_EQUALS,
                "contains": cls.CONTAINS, "ncontains": cls.NOT_CONTAINS,
                "starts": cls.STARTS_WITH, "ends": cls.ENDS_WITH,
                "gt": cls.GREATER_THAN, "lt": cls.LESS_THAN,
                "gte": cls.GREATER_THAN_OR_EQUAL, "lte": cls.LESS_THAN_OR_EQUAL,
                "any": cls.IN, "empty": cls.IS_NULL, "notempty": cls.IS_NOT_NULL,
                "all": cls.ALL_OF, "none": cls.NONE_OF,
                "is_true": cls.IS_TRUE, "is_false": cls.IS_FALSE,
                "between": cls.BETWEEN, "within_last": cls.WITHIN_LAST,
            }
            return _CODE_TO_MEMBER.get(code)
        return None
