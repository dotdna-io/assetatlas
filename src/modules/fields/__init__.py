# SPDX-License-Identifier: AGPL-3.0-or-later
# src/modules/fields/__init__.py
"""Public exports for the fields module."""

from src.modules.fields.types import FieldDataType, FilterOperator
from src.modules.fields.models import (
    FieldId,
    InvalidFieldError,
    StaticField,
    CoreEnrichmentField,
    ExtendedAttributeField,
    IntegrationGroupField,
    TagField,
    PostureField,
    VirtualField,
)
from src.modules.fields.encoding import (
    encode,
    decode,
    coerce_field_id,
    FIELD_ID_ADAPTER,
)
from src.modules.fields.catalog import (
    FieldCatalog, CatalogEntry, build_catalog_response,
    build_external_fields_response, EXTERNAL_FIELD_KINDS,
)
from src.modules.fields.resolver import FieldResolver, ResolvedField
from src.modules.fields.operators import (
    Operator, OPERATOR_REGISTRY, OperatorResolutionError,
    resolve as resolve_operator, operators_for,
)

__all__ = [
    "FieldDataType", "FilterOperator",
    "FieldId", "InvalidFieldError",
    "StaticField", "CoreEnrichmentField", "ExtendedAttributeField",
    "IntegrationGroupField", "TagField", "PostureField", "VirtualField",
    "encode", "decode", "coerce_field_id", "FIELD_ID_ADAPTER",
    "FieldCatalog", "CatalogEntry", "build_catalog_response",
    "build_external_fields_response", "EXTERNAL_FIELD_KINDS",
    "FieldResolver", "ResolvedField",
    "Operator", "OPERATOR_REGISTRY", "OperatorResolutionError",
    "resolve_operator", "operators_for",
]
