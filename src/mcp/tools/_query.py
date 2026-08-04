# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared query-grammar helpers for the asset tools.

This module is the single home for the field-identification + filter
normalization logic that both the standalone MCP server tools
(``src/mcp/tools/assets.py``) and the chat ``ToolRegistry`` bindings depend
on. It was lifted out of ``assets.py`` so later chat primitives (``query_assets``
etc.) can import the SAME resolution path instead of copy-pasting it — there is
deliberately no second "query engine" here. All field resolution delegates to
``FieldCatalog``/``FieldResolver`` and all operator resolution to
``OPERATOR_REGISTRY`` (via ``operators.resolve``); nothing post-filters rows in
Python.

``assets.py`` re-exports every public name below for back-compat, so existing
importers (the MCP server, ``artifacts.py``, tests) are unaffected.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.modules.fields.catalog import STATIC_FIELDS, VIRTUAL_FIELDS
from src.modules.fields.encoding import encode, decode, coerce_field_id
from src.modules.fields.models import InvalidFieldError, StaticField, VirtualField
from src.modules.fields.operators import (
    OperatorResolutionError,
    resolve as _resolve_operator,
)

logger = logging.getLogger(__name__)


class FieldResolutionError(ValueError):
    """Raised when a field name / encoded id / sort key resolves to no live
    catalog field.

    A ``ValueError`` subclass so the chat ``ToolRegistry.invoke`` — which
    already catches ``(ValueError, KeyError, LookupError)`` — surfaces it as a
    clean LLM-facing string, and so unknown FIELDS get the same loud-error
    treatment as unknown OPERATORS (``OperatorResolutionError``) instead of the
    old silent drop that widened the result set (§4 loud-error-over-drop).
    """


def _assert_field_resolves(
    field: Any, resolver, raw_field: Any, *, param: str = "filter field",
) -> None:
    """Raise ``FieldResolutionError`` when a resolver is available and ``field``
    — a structurally-valid encoded STRING or ``{field_id}`` dict/FieldId — is NOT
    a live catalog field.

    The bare-name path already validates via ``field_id_for_display_name``, but
    encoded / dict forms otherwise reach the repo unchecked — where a dead
    FILTER field is silently DROPPED (``_build_single_filter`` swallows the
    repo's ``InvalidFieldError``) and a dead SORT field raises an uncaught
    ``InvalidFieldError`` from the ORDER BY. Validating here keeps the §4
    loud-error contract on every field form. Without a resolver we can't consult
    the catalog, so we pass through (the repo stays the last line)."""
    if resolver is None:
        return
    try:
        fid = coerce_field_id(field)  # handles str (decode), dict, and typed forms
        resolver.resolve(fid)
    except (InvalidFieldError, ValueError) as exc:
        raise FieldResolutionError(
            _UNKNOWN_FIELD_HINT.format(param=param, name=raw_field)
        ) from exc


# Lower-case alias -> canonical display name (the form the FieldResolver
# catalog keys on). Lets the LLM pass "hostname" or "os_normalized" instead
# of having to remember the exact title-cased/spaced display name.
_STATIC_FIELD_ALIASES: Dict[str, str] = {}
for _display in STATIC_FIELDS:
    _k = _display.lower()
    _STATIC_FIELD_ALIASES[_k] = _display                    # hostname
    _STATIC_FIELD_ALIASES[_k.replace(" ", "_")] = _display  # os_normalized
    _STATIC_FIELD_ALIASES[_k.replace(" ", "")] = _display   # osnormalized


# Lower-case alias -> canonical virtual field name. Virtual fields (e.g.
# "data_source_count", "confidence_level") are backend-computed
# expressions; encoding them requires the `virtual:` prefix rather than
# `static:`. Keep the same three-alias shape as STATIC so the LLM can pass
# display, snake, or squished form.
_VIRTUAL_FIELD_ALIASES: Dict[str, str] = {}
for _vname, _vcfg in VIRTUAL_FIELDS.items():
    _disp = _vcfg.get("display_name", _vname)
    _VIRTUAL_FIELD_ALIASES[_vname.lower()] = _vname
    _VIRTUAL_FIELD_ALIASES[_disp.lower()] = _vname
    _VIRTUAL_FIELD_ALIASES[_disp.lower().replace(" ", "_")] = _vname
    _VIRTUAL_FIELD_ALIASES[_disp.lower().replace(" ", "")] = _vname


def _normalize_field_name(name: str, resolver=None) -> Optional[str]:
    """Map a bare field name to an encoded FieldId, or None. Static/virtual
    aliases resolve without a resolver; for every other kind (integration_group,
    extended_attribute, tag, posture) pass `resolver` so we consult the live
    catalog — that is how data-source-specific fields like 'Beszel - cpu'
    become reachable (issue #735 P5)."""
    if not name:
        return None
    if ":" in name:
        # Already encoded (static:Foo, ext:Source:attr, virtual:name, etc.)
        return name
    key = name.lower()
    display = _STATIC_FIELD_ALIASES.get(key)
    if display is not None:
        return f"static:{display.replace(' ', '%20')}"
    virtual_name = _VIRTUAL_FIELD_ALIASES.get(key)
    if virtual_name is not None:
        return f"virtual:{virtual_name}"
    if resolver is not None:
        fid = resolver.catalog().field_id_for_display_name(name)
        if fid is not None:
            return encode(fid)
    return None


def canonical_field_display_name(name: str) -> Optional[str]:
    """Return the canonical display name for a static/virtual field, given
    any of its accepted input forms (display, snake_case, squished, any case).
    Returns ``None`` for unknown names — keeps acronym casing intact
    (e.g. "os_normalized" → "OS Normalized", not "Os Normalized")."""
    if not name:
        return None
    key = name.lower()
    display = _STATIC_FIELD_ALIASES.get(key)
    if display is not None:
        return display
    virtual_name = _VIRTUAL_FIELD_ALIASES.get(key)
    if virtual_name is not None:
        # Virtual fields key on internal name; look up their display_name.
        vcfg = VIRTUAL_FIELDS.get(virtual_name, {})
        return vcfg.get("display_name", virtual_name)
    return None


# Shape of the hint we surface when the LLM passes a field we can't resolve.
# Kept consistent across tools so the model learns a single recovery strategy.
_UNKNOWN_FIELD_HINT = (
    "Unknown {param} '{name}'. Pass a bare static-field display name "
    "(e.g. 'Category', 'OS Normalized', 'Owner' — case-insensitive, "
    "underscores OK) or an already-encoded FieldId "
    "('static:Hostname', 'ext:Entra Devices:deviceOwnership', 'tag:...'). "
    "For non-static fields or ambiguous terms, call semantic_suggest first."
)


def _normalize_field_list(fields: Optional[str], resolver=None) -> Optional[List[str]]:
    """Parse + normalize a comma-separated field list into encoded FieldIds."""
    if not fields:
        return None
    out: List[str] = []
    for raw in (s.strip() for s in fields.split(",")):
        if not raw:
            continue
        enc = _normalize_field_name(raw, resolver)
        if enc:
            out.append(enc)
        else:
            logger.info("Dropping unknown field name %r from fields=", raw)
    return out or None


def _normalize_filters(filters: Any, resolver=None) -> Optional[List[Dict[str, Any]]]:
    """Convert the tool's documented filter shape into what the repo expects.

    Tool docs say:   ``[{"field": "owner", "op": "contains", "value": "..."}]``
    LLMs often pass: ``[{"field_id": "owner", ...}]`` (copying semantic_suggest
    output).
    Repo expects:    ``[{"field_id": {"kind": "static", "name": "Owner"},
    "op": ...}]``

    This normalizes all three forms into the repo shape, mapping bare names
    via _STATIC_FIELD_ALIASES. If a name misses the static/virtual tables and
    a resolver is provided, the catalog is queried by display name (covers
    data-source-specific fields like "Beszel - cpu"). A field that resolves to
    nothing raises ``FieldResolutionError`` (NOT dropped): a dropped filter
    silently widens the result set, which is both a correctness and an
    over-disclosure risk (§4). The caller converts the exception into its
    existing LLM-facing string error channel.
    """
    if not filters:
        return None
    if not isinstance(filters, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for f in filters:
        if not isinstance(f, dict):
            continue
        raw_field = f.get("field_id") if "field_id" in f else f.get("field")
        op = f.get("op")
        # A half-specified clause is dropped no longer: dropping it silently
        # widens the result set, the same §4 risk as an unresolvable field.
        if not raw_field:
            raise FieldResolutionError(
                "A filter is missing its 'field' (or 'field_id'). Each filter "
                "must be {field, op, value}."
            )
        if not op:
            raise OperatorResolutionError(
                f"A filter (field {raw_field!r}) is missing its 'op'. Each "
                "filter must be {field, op, value}; call describe_catalog for "
                "the valid operators."
            )

        # Validate + canonicalise the operator against the registry. Unknown
        # ops are rejected LOUDLY (raised) rather than passed through to the
        # repo, where _build_single_filter would log + drop the clause and
        # silently widen the result set. Resolving to the canonical code also
        # normalises alias spellings ("any of" -> "any", "is_empty" -> "empty")
        # before the repo sees them. Callers convert this into their existing
        # LLM-facing string error channel (MCP no-exceptions-at-boundary).
        try:
            op = _resolve_operator(op).code
        except OperatorResolutionError as exc:
            raise OperatorResolutionError(
                f"Unknown filter operator {op!r}."
            ) from exc

        if isinstance(raw_field, dict):
            field_id: Any = raw_field
            # A {field_id} dict (LLM copying semantic_suggest) is structurally
            # valid but may name a dead field; validate it like the bare path.
            _assert_field_resolves(field_id, resolver, raw_field)
        elif isinstance(raw_field, str) and ":" in raw_field:
            # Already an encoded FieldId (e.g. "ext:Beszel:cpu" handed out by
            # list_fields). Decode to the dict the repo's filter compiler
            # expects — mirrors _normalize_field_name's encoded-id passthrough
            # so encoded keys aren't silently dropped on the filter path.
            try:
                field_id = decode(raw_field).model_dump()
            except Exception as exc:
                # An undecodable encoded id is as unresolvable as an unknown
                # bare field — raise (don't drop) for the same reason (§4).
                raise FieldResolutionError(
                    _UNKNOWN_FIELD_HINT.format(
                        param="filter field", name=raw_field,
                    )
                ) from exc
            # Structurally decodable but maybe dead: the repo silently DROPS a
            # dead filter field (asset_repository._build_single_filter swallows
            # InvalidFieldError), so validate here to keep the loud-error
            # contract on the encoded path too (§4).
            _assert_field_resolves(field_id, resolver, raw_field)
        elif isinstance(raw_field, str):
            key = raw_field.lower()
            display = _STATIC_FIELD_ALIASES.get(key)
            if display is not None:
                field_id = {"kind": "static", "name": display}
            else:
                virtual_name = _VIRTUAL_FIELD_ALIASES.get(key)
                if virtual_name is not None:
                    field_id = {"kind": "virtual", "name": virtual_name}
                else:
                    fid = (resolver.catalog().field_id_for_display_name(raw_field)
                           if resolver is not None else None)
                    if fid is None:
                        raise FieldResolutionError(
                            _UNKNOWN_FIELD_HINT.format(
                                param="filter field", name=raw_field,
                            )
                        )
                    field_id = fid.model_dump()
        else:
            continue

        normalized.append({
            "field_id": field_id,
            "op": op,
            "value": f.get("value"),
        })
    return normalized or None


# repo.list_assets() aliases every column to its canonical encoded FieldId
# ("static:Hostname", "static:OS%20Normalized", ...). The row formatter and
# get_asset_by_id() both speak a flat snake_case shape, so we project
# the encoded shape back to that form here. Without this step, the formatter
# sees no hostname / category / OS and prints "(no hostname)" for everything
# — exactly the failure mode that caused LLMs to loop through get_asset per
# row after every list_assets call.
_ENCODED_TO_SNAKE: Dict[str, str] = {
    "static:Hostname": "hostname",
    "static:Category": "category",
    "static:Subcategory": "subcategory",
    "static:OS%20Normalized": "os_normalized",
    "static:Manufacturer": "manufacturer",
    "static:Model": "model",
    "static:Confidence%20Score": "confidence",
    "static:First%20Seen": "first_seen",
    "static:Last%20Seen": "last_seen",
    "static:IP%20Address": "ip",
    "static:MAC%20Address": "mac",
    "static:Serial%20Number": "sn",
    "static:Owner": "owner",
    "static:Location": "location",
    "static:FQDN": "fqdn",
    "static:Data%20Source": "data_source",
}


def _project_list_row_to_snake(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``row`` with encoded static:Xxx keys flattened to
    snake_case. Keys not in the known map pass through unchanged so extended
    attributes / tags keep their original form."""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        out[_ENCODED_TO_SNAKE.get(k, k)] = v
    return out


# Default field set fetched for BOTH the chat list preview and the CSV export,
# so the two agree (a download must match what the user saw). This is the SINGLE
# source for that default — the chat binding and /api/assets/export.csv both
# import it. It is a SUPERSET of the repo's minimal DEFAULT_LIST_FIELDS_ENCODED
# (which omits IP / Owner / Data Source / MAC / Serial / Location / etc.), so
# the table columns AND the LLM-text formatter both have the values they render
# (#742). Canonical encoded display names (percent-encoded); list_assets_query
# projects them back to snake keys via _ENCODED_TO_SNAKE.
CHAT_LIST_FIELDS_ENCODED = ",".join([
    # table columns (artifacts._LIST_ASSET_COLUMNS):
    "static:Hostname",
    "static:IP%20Address",
    "static:Category",
    "static:Subcategory",
    "static:OS%20Normalized",
    "static:Owner",
    "static:Data%20Source",
    "static:Last%20Seen",
    # text-only extras the LLM summary renders (assets._format_asset_row):
    "static:Location",
    "static:MAC%20Address",
    "static:Serial%20Number",
    "static:Manufacturer",
    "static:Model",
    "static:Confidence%20Score",
])


def _display_label_for_encoded(encoded_key: str, resolver=None) -> str:
    """Map an encoded FieldId to its catalog display label, so a rendered column
    is never an opaque key. Static/virtual resolve without a DB round-trip; every
    other kind (ext/group/tag/posture/core) asks the live catalog via the
    resolver so the label matches Explorer exactly, falling back to a label
    derived from the decoded parts, then to the raw key."""
    try:
        fid = decode(encoded_key)
    except Exception:
        return encoded_key
    if isinstance(fid, StaticField):
        return fid.name
    if isinstance(fid, VirtualField):
        builtin = VIRTUAL_FIELDS.get(fid.name)
        if builtin is not None:
            return builtin.get("display_name", fid.name)
        # User-defined (unified) virtual fields aren't in the built-in
        # VIRTUAL_FIELDS dict; resolve their label via the catalog-backed
        # resolver (the factory wires unified_fields_provider) so a CSV/list
        # header shows the field's display name, not the raw slug (#784). Falls
        # through to the slug below if there is no resolver or it can't resolve.
        if resolver is not None:
            try:
                return resolver.resolve(fid).display_name
            except Exception:
                pass
        return fid.name
    if resolver is not None:
        try:
            return resolver.resolve(fid).display_name
        except Exception:
            pass
    # Derive a readable label from the decoded parts (mirrors the catalog's own
    # "<source> - <attribute>" / name conventions) so an unresolvable field
    # still renders something human, never the encoded key.
    src = getattr(fid, "source", None) or getattr(fid, "group", None)
    attr = getattr(fid, "attribute", None)
    if src is not None and attr is not None:
        return f"{src} - {attr}"
    for name_attr in ("name", "category"):
        val = getattr(fid, name_attr, None)
        if val is not None:
            return val
    return encoded_key


def resolve_list_columns(
    encoded_fields: Optional[List[str]], resolver=None,
) -> List[Tuple[str, str]]:
    """Turn a resolved encoded-field list into ``(row_key, display_label)`` pairs
    that drive the list table + text artifact.

    ``row_key`` is the key the projected row dict uses: static fields are
    snake-cased via ``_ENCODED_TO_SNAKE`` (matching ``_project_list_row_to_snake``),
    every other kind keeps its raw encoded key. The encoded id is first
    CANONICALISED (``encode(decode(enc))``) because the repo aliases its row-dict
    keys to that canonical form — without this, a non-canonical encoded field
    (e.g. ``static:IP Address`` with a literal space) would yield a column key
    that never matches the projected row key, rendering a blank column.
    ``display_label`` comes from the catalog (see ``_display_label_for_encoded``)
    so columns render with their human name. Returns ``[]`` for an empty/None
    input; callers fall back to a default column set in that case.
    """
    columns: List[Tuple[str, str]] = []
    for enc in encoded_fields or []:
        try:
            canonical = encode(decode(enc))
        except Exception:
            canonical = enc
        row_key = _ENCODED_TO_SNAKE.get(canonical, canonical)
        columns.append((row_key, _display_label_for_encoded(enc, resolver)))
    return columns
