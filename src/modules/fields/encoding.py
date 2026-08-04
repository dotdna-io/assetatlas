# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical bijective encoding between FieldId (typed object) and string.

Used for:
- Explorer CSV query parameters (?fields=static:hostname,tag:x)
- JSON response row dict keys (keys must be strings)

Format: "<prefix>:<segment>[:<segment>]" with each segment URL-percent-encoded
via urllib.parse.quote(seg, safe=''). Invariant: encode(decode(s)) == s for
every valid s; decode(encode(f)) == f for every valid FieldId.
"""

from urllib.parse import quote, unquote

from pydantic import TypeAdapter

from src.modules.fields.models import (
    FieldId,
    StaticField,
    CoreEnrichmentField,
    ExtendedAttributeField,
    IntegrationGroupField,
    TagField,
    PostureField,
    VirtualField,
)

FIELD_ID_ADAPTER: TypeAdapter[FieldId] = TypeAdapter(FieldId)


def coerce_field_id(raw) -> FieldId:
    """Accept a typed FieldId instance, a dict, or an encoded string and return
    a FieldId.

    Used at service/repo boundaries where JSONB rows arrive as dicts, Pydantic
    inputs arrive pre-typed, and the alert-rule wizard stores the encoded string
    form produced by ``encodeFieldId`` (e.g. ``"static:Category"``). The string
    branch delegates to ``decode`` — the canonical inverse of the encoding the
    wizard writes — so string, dict, and typed inputs all resolve identically
    downstream. Without it the encoded string passed through unresolved and the
    compiler silently dropped the filter, matching the whole fleet (#822).

    A non-encoded string (no valid prefix) raises ``ValueError`` from ``decode``:
    genuinely bad data must fail loudly into callers' skip-with-warning handling
    rather than be silently guessed.
    """
    if isinstance(raw, str):
        return decode(raw)
    if isinstance(raw, dict):
        return FIELD_ID_ADAPTER.validate_python(raw)
    return raw


def encode(field_id) -> str:
    """Encode a FieldId (or a dict that parses to one) to canonical string form."""
    if isinstance(field_id, dict):
        field_id = FIELD_ID_ADAPTER.validate_python(field_id)

    if isinstance(field_id, StaticField):
        return f"static:{quote(field_id.name, safe='')}"
    if isinstance(field_id, CoreEnrichmentField):
        return f"core:{quote(field_id.name, safe='')}"
    if isinstance(field_id, ExtendedAttributeField):
        return f"ext:{quote(field_id.source, safe='')}:{quote(field_id.attribute, safe='')}"
    if isinstance(field_id, IntegrationGroupField):
        return f"group:{quote(field_id.group, safe='')}:{quote(field_id.attribute, safe='')}"
    if isinstance(field_id, TagField):
        return f"tag:{quote(field_id.name, safe='')}"
    if isinstance(field_id, PostureField):
        prefix = "posture_state" if field_id.read == "state" else "posture_output"
        return f"{prefix}:{quote(field_id.category, safe='')}"
    if isinstance(field_id, VirtualField):
        return f"virtual:{quote(field_id.name, safe='')}"
    raise ValueError(f"unknown FieldId variant: {type(field_id).__name__}")


def decode(s: str) -> FieldId:
    """Parse a canonical string back to a typed FieldId. Raises ValueError on malformed input."""
    if not s:
        raise ValueError("empty string is not a valid encoded FieldId")

    parts = s.split(":")
    prefix = parts[0]
    segments = [unquote(p) for p in parts[1:]]

    def _expect(n: int) -> None:
        if len(segments) != n:
            raise ValueError(
                f"encoded FieldId '{s}' has {len(segments)} segment(s); "
                f"kind '{prefix}' expects {n}"
            )

    if prefix == "static":
        _expect(1)
        return StaticField(kind="static", name=segments[0])
    if prefix == "core":
        _expect(1)
        return CoreEnrichmentField(kind="core_enrichment", name=segments[0])
    if prefix == "ext":
        _expect(2)
        return ExtendedAttributeField(kind="extended_attribute", source=segments[0], attribute=segments[1])
    if prefix == "group":
        _expect(2)
        return IntegrationGroupField(kind="integration_group", group=segments[0], attribute=segments[1])
    if prefix == "tag":
        _expect(1)
        return TagField(kind="tag", name=segments[0])
    if prefix == "posture_state":
        _expect(1)
        return PostureField(kind="posture", category=segments[0], read="state")
    if prefix == "posture_output":
        _expect(1)
        return PostureField(kind="posture", category=segments[0], read="output")
    if prefix == "virtual":
        _expect(1)
        return VirtualField(kind="virtual", name=segments[0])
    raise ValueError(f"unknown encoded FieldId prefix: '{prefix}'")
