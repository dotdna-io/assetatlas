# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure helpers for Unified Fields: slugging, type-class inference, and
function/type compatibility. No DB, no Flask — heavily unit-tested."""

import re
from typing import List, Optional, Sequence

from src.modules.fields.types import FieldDataType

VALID_FUNCTIONS = ("coalesce", "union", "max", "min")

_TEXT = {FieldDataType.STRING, FieldDataType.STRING_ARRAY,
         FieldDataType.JSONB_ARRAY, FieldDataType.JSONB}
_NUMBER = {FieldDataType.INTEGER, FieldDataType.FLOAT}
_DATE = {FieldDataType.DATE, FieldDataType.TIMESTAMP}
_ARRAY = {FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return s or "field"


def type_class(dt: FieldDataType) -> str:
    if dt in _DATE:
        return "date"
    if dt in _NUMBER:
        return "number"
    if dt in _TEXT:
        return "text"
    return "other"


def _is_scalar(dt: FieldDataType) -> bool:
    return dt not in _ARRAY


def functions_for_types(source_types: Sequence[FieldDataType]) -> List[str]:
    classes = {type_class(t) for t in source_types}
    if len(classes) != 1 or "other" in classes:
        return []
    cls = next(iter(classes))
    funcs = ["coalesce", "union"]
    if cls in ("date", "number"):
        funcs += ["max", "min"]
    return funcs


def infer_data_type(function: str, source_types: Sequence[FieldDataType]) -> Optional[FieldDataType]:
    if not source_types:
        return None
    if function not in functions_for_types(source_types):
        return None
    classes = {type_class(t) for t in source_types}
    if len(classes) != 1 or "other" in classes:
        return None
    cls = next(iter(classes))
    if function == "union":
        return FieldDataType.STRING_ARRAY
    if cls == "text":
        return FieldDataType.STRING
    if cls == "number":
        return (FieldDataType.INTEGER
                if all(t == FieldDataType.INTEGER for t in source_types)
                else FieldDataType.FLOAT)
    if cls == "date":
        # Unify date+timestamp to TIMESTAMP — identical operator set, and the
        # JSONB-text cast helper (aa_try_ts) yields timestamptz anyway.
        return FieldDataType.TIMESTAMP
    return None


def validate_compatibility(function: str, source_types: Sequence[FieldDataType]) -> Optional[str]:
    """Return an error string, or None if valid."""
    if function not in VALID_FUNCTIONS:
        return f"Unknown function '{function}'"
    if len(source_types) < 2:
        return "Select at least two source fields"
    classes = {type_class(t) for t in source_types}
    if "other" in classes:
        return "Selected fields include an unsupported type"
    if len(classes) != 1:
        return "Selected fields must share a compatible type"
    if function not in functions_for_types(source_types):
        return f"'{function}' is not valid for {next(iter(classes))} fields"
    if function in ("coalesce", "max", "min") and any(not _is_scalar(t) for t in source_types):
        return f"'{function}' requires single-value (scalar) source fields"
    return None
