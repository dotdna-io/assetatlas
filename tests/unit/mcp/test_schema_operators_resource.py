# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drift guard for the MCP ``assetatlas://schema/operators`` resource.

WHY: ``OPERATORS_BY_TYPE`` in ``src/mcp/resources/schema.py`` is the LLM-facing
reference for which operators apply to each field type. It is hand-maintained,
so it can silently fall behind the canonical registry — which is exactly what
happened before this guard existed (``ncontains``/``all``/``none`` were missing
for string/array types, and ``jsonb``/``jsonb_array`` were absent entirely),
telling the model a smaller operator set than the API actually accepts. These
tests fail if the resource ever diverges from ``operators_for()`` again.
"""
import pytest

from src.mcp.resources.schema import OPERATORS_BY_TYPE
from src.modules.fields.operators import operators_for
from src.modules.fields.types import FieldDataType

# Resource type-name -> (FieldDataType, is_array) — the same identity the API
# resolves a field's operator set from.
_TYPE_MAP = {
    "string": (FieldDataType.STRING, False),
    "string_array": (FieldDataType.STRING_ARRAY, True),
    "integer": (FieldDataType.INTEGER, False),
    "float": (FieldDataType.FLOAT, False),
    "boolean": (FieldDataType.BOOLEAN, False),
    "date": (FieldDataType.DATE, False),
    "timestamp": (FieldDataType.TIMESTAMP, False),
    "jsonb": (FieldDataType.JSONB, False),
    "jsonb_array": (FieldDataType.JSONB_ARRAY, True),
}


@pytest.mark.parametrize("type_name", sorted(_TYPE_MAP))
def test_resource_operator_set_matches_registry(type_name):
    data_type, is_array = _TYPE_MAP[type_name]
    registry = {op.code for op in operators_for(data_type, is_array)}
    resource = [code for code, _desc in OPERATORS_BY_TYPE[type_name]]
    assert len(resource) == len(set(resource)), f"{type_name}: duplicate operator codes {resource}"
    assert set(resource) == registry, (
        f"{type_name}: resource {sorted(set(resource))} != registry {sorted(registry)}"
    )


def test_every_registry_type_is_listed():
    # No field data type may be missing from the LLM-facing reference.
    assert set(OPERATORS_BY_TYPE) == set(_TYPE_MAP)


def test_descriptions_are_nonempty_strings():
    for type_name, ops in OPERATORS_BY_TYPE.items():
        for code, desc in ops:
            assert isinstance(desc, str) and desc.strip(), f"{type_name}:{code} has no description"
