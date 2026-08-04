# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolve enrichment ``match_field`` strings to canonical typed FieldIds.

An enrichment dataset stores ``match_field`` as text. The (fixed) UI submits the
canonical encoded field-id form produced by ``src.modules.fields.encoding.encode``
(e.g. ``static:Hostname``, ``ext:SentinelOne:siteName``, ``group:Entra:building``).
Datasets created before that change stored a legacy form: the bare asset column
name (``hostname``) or ``ext.<source>.<key>``. This module accepts both so existing
active datasets keep applying while new uploads use the canonical encoding.

Resolving the FieldId to SQL is the FieldResolver's job (it needs the live catalog);
this module is pure string parsing — no DB access — so it can run inside Pydantic
validation as well as the service layer.
"""

from typing import FrozenSet

from src.modules.fields.catalog import STATIC_FIELDS
from src.modules.fields.encoding import decode
from src.modules.fields.models import (
    FieldId,
    StaticField,
    ExtendedAttributeField,
)

# Bare column names the pre-canonical enrichment UI ever stored as match_field,
# mapped to their canonical static display name. Restricted to this historical
# set so a legacy value can only resolve to a field the old UI could produce.
_LEGACY_CORE_COLUMNS = ("hostname", "fqdn", "ip", "mac", "sn", "manufacturer", "model")
_COLUMN_TO_DISPLAY = {column: name for name, column in STATIC_FIELDS.items()}
LEGACY_CORE_MATCH_FIELDS = {
    column: _COLUMN_TO_DISPLAY[column] for column in _LEGACY_CORE_COLUMNS
}

# Field kinds an enrichment dataset may match on: core asset fields and
# data-source-specific fields. Tag / posture / virtual fields are out of scope —
# matching a CSV key against a classification or computed value is not meaningful.
ALLOWED_MATCH_KINDS: FrozenSet[str] = frozenset(
    {"static", "core_enrichment", "extended_attribute", "integration_group"}
)


def match_field_to_field_id(match_field: str) -> FieldId:
    """Parse a stored/submitted ``match_field`` string into a typed FieldId.

    Tries the canonical encoded form first, then the two legacy forms. Raises
    ``ValueError`` if the string is not a recognizable field identifier.
    """
    if not match_field:
        raise ValueError("match_field must be a non-empty string")

    # Canonical encoded field id (static:/core:/ext:/group:/tag:/posture_*:/virtual:)
    try:
        return decode(match_field)
    except ValueError:
        pass

    # Legacy bare core column (e.g. "hostname", "ip")
    if match_field in LEGACY_CORE_MATCH_FIELDS:
        return StaticField(kind="static", name=LEGACY_CORE_MATCH_FIELDS[match_field])

    # Legacy extended-attribute path "ext.<source>.<key>"
    if match_field.startswith("ext."):
        parts = match_field.split(".")
        if len(parts) == 3 and all(parts):
            return ExtendedAttributeField(
                kind="extended_attribute", source=parts[1], attribute=parts[2]
            )

    raise ValueError(f"Unrecognized enrichment match_field: {match_field!r}")


def is_allowed_match_kind(field_id: FieldId) -> bool:
    """True iff ``field_id`` is a kind enrichment is allowed to match on."""
    return getattr(field_id, "kind", None) in ALLOWED_MATCH_KINDS


def is_ip_field(field_id: FieldId) -> bool:
    """True iff ``field_id`` is the core IP Address column (the only CIDR target)."""
    return isinstance(field_id, StaticField) and STATIC_FIELDS.get(field_id.name) == "ip"
