# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the shared query-grammar module src/mcp/tools/_query.py.

These import directly from _query (the new home) — complementing
test_normalize_filters_op_validation.py, which imports the same helpers via the
back-compat re-export in src.mcp.tools.assets. Together they pin BOTH the new
module AND the façade.

What matters here (the business rules, not just "it works"):
- the three accepted field grammars (bare / encoded / {field_id} dict) are
  interchangeable, so a field can't be addressed two ways with two results;
- field vs operator errors stay DISTINCT loud errors so the model gets the
  right corrective hint;
- confidence_level and 'Data Source' keep their bespoke repo handling (not
  routed through the generic path) — _query must not mangle them;
- a rendered column never surfaces an opaque encoded key.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.fields.models import InvalidFieldError
from src.modules.fields.operators import OperatorResolutionError
from src.mcp.tools._query import (
    CHAT_LIST_FIELDS_ENCODED,
    FieldResolutionError,
    _display_label_for_encoded,
    _normalize_field_list,
    _normalize_filters,
    resolve_list_columns,
)


def _one(filters):
    """Normalize a single-filter list and return the lone normalized entry."""
    out = _normalize_filters(filters)
    assert out is not None and len(out) == 1
    return out[0]


# ---------------------------------------------------------------------------
# Field identity round-trips across the three accepted grammars (§3.1)
# ---------------------------------------------------------------------------

def test_static_field_round_trips_bare_encoded_and_dict_identically():
    """A static field resolves to the SAME repo field_id whether the LLM passes
    a bare name, an encoded id, or a {field_id} dict. If these diverged, the
    model could get different results just by spelling the field differently."""
    bare = _one([{"field": "category", "op": "eq", "value": "Server"}])
    encoded = _one([{"field": "static:Category", "op": "eq", "value": "Server"}])
    as_dict = _one([{"field_id": {"kind": "static", "name": "Category"},
                     "op": "eq", "value": "Server"}])
    expected = {"kind": "static", "name": "Category"}
    assert bare["field_id"] == expected
    assert encoded["field_id"] == expected
    assert as_dict["field_id"] == expected


def test_virtual_field_round_trips_bare_and_encoded_without_db():
    """Virtual fields resolve via the alias table with no resolver/DB, in both
    bare and encoded form — the count virtuals must stay addressable offline."""
    bare = _one([{"field": "data_source_count", "op": "gte", "value": 2}])
    encoded = _one([{"field": "virtual:data_source_count",
                     "op": "gte", "value": 2}])
    expected = {"kind": "virtual", "name": "data_source_count"}
    assert bare["field_id"] == expected
    assert encoded["field_id"] == expected


# ---------------------------------------------------------------------------
# Field vs operator errors stay distinct loud errors
# ---------------------------------------------------------------------------

def test_unknown_field_raises_field_error_not_operator_error():
    """An unknown field raises FieldResolutionError (loud), so the result set is
    never silently widened (§4)."""
    with pytest.raises(FieldResolutionError):
        _normalize_filters([{"field": "no_such_field", "op": "eq", "value": "x"}])


def test_unknown_operator_still_raises_operator_error():
    """Unknown OPERATORS keep their own distinct loud error — the model needs
    operator-specific guidance, not a field hint, to self-correct."""
    with pytest.raises(OperatorResolutionError):
        _normalize_filters(
            [{"field": "category", "op": "nonsense_op", "value": "x"}]
        )


def test_half_specified_filter_raises_not_dropped():
    """A clause missing its field (or its op) is rejected loudly, NOT dropped —
    a dropped clause silently widens the result set, the same §4 risk as an
    unresolvable field."""
    with pytest.raises(FieldResolutionError):
        _normalize_filters([{"op": "eq", "value": "Server"}])          # no field
    with pytest.raises(OperatorResolutionError):
        _normalize_filters([{"field": "category", "value": "Server"}])  # no op


def test_encoded_dead_field_raises_when_resolver_present():
    """An encoded but DEAD field (decodes fine, not live in the catalog) raises
    instead of reaching the repo — which silently DROPS a dead filter field and
    widens the result (§4). The resolver is what makes the catalog check
    possible; the repo's _build_single_filter swallows the InvalidFieldError."""
    resolver = MagicMock()
    resolver.resolve.side_effect = InvalidFieldError("not live")
    with pytest.raises(FieldResolutionError):
        _normalize_filters(
            [{"field": "ext:DeadSource:attr", "op": "eq", "value": "x"}],
            resolver=resolver,
        )


def test_dict_dead_field_raises_when_resolver_present():
    """A {field_id} dict (LLM copying semantic_suggest) naming a dead field
    raises too — same loud contract as the encoded/bare forms."""
    resolver = MagicMock()
    resolver.resolve.side_effect = InvalidFieldError("not live")
    with pytest.raises(FieldResolutionError):
        _normalize_filters(
            [{"field_id": {"kind": "extended_attribute",
                           "source": "Dead", "attribute": "x"},
              "op": "eq", "value": "y"}],
            resolver=resolver,
        )


def test_encoded_live_field_passes_when_resolver_resolves():
    """An encoded field the resolver CAN resolve passes through unchanged (the
    validation only rejects DEAD fields, not every encoded one)."""
    resolver = MagicMock()       # .resolve(...) returns a MagicMock -> no raise
    out = _normalize_filters(
        [{"field": "ext:Beszel:cpu", "op": "eq", "value": "x"}],
        resolver=resolver,
    )
    assert out[0]["field_id"] == {
        "kind": "extended_attribute", "source": "Beszel", "attribute": "cpu",
    }


# ---------------------------------------------------------------------------
# Bespoke fields are passed through untouched (repo applies special handling)
# ---------------------------------------------------------------------------

def test_confidence_level_eq_and_any_pass_through_as_virtual():
    """confidence_level stays on its bespoke eq/any path: _query resolves it to
    the virtual field and the REPO applies the live-threshold clause. _query must
    not mangle it into a generic field (plan §3.1 / Open Decision #7)."""
    eq = _one([{"field": "confidence_level", "op": "eq", "value": "high"}])
    assert eq == {
        "field_id": {"kind": "virtual", "name": "confidence_level"},
        "op": "eq", "value": "high",
    }
    any_ = _one([{"field": "confidence_level", "op": "any",
                  "value": ["high", "medium"]}])
    assert any_["field_id"] == {"kind": "virtual", "name": "confidence_level"}
    assert any_["op"] == "any"


def test_data_source_filter_preserved_as_static_for_repo_group_expansion():
    """The 'Data Source' hidden group-name expansion lives in the repo; _query
    must hand it through as the static field untouched so the repo can OR in the
    group-name clause. Mangling it here would silently lose the expansion."""
    out = _one([{"field": "data_source", "op": "eq", "value": "Defender"}])
    assert out["field_id"] == {"kind": "static", "name": "Data Source"}


# ---------------------------------------------------------------------------
# resolve_list_columns / _display_label_for_encoded — never show an opaque key
# ---------------------------------------------------------------------------

def test_resolve_list_columns_labels_static_and_virtual_without_resolver():
    """Static + virtual columns get a human label with NO resolver/DB. row_key is
    the key the projected row dict uses: static is snake-cased, virtual keeps its
    encoded key (the repo aliases virtuals to that, and the projection leaves it)."""
    cols = resolve_list_columns(
        ["static:IP%20Address", "virtual:data_source_count"], resolver=None,
    )
    assert cols == [
        ("ip", "IP Address"),
        ("virtual:data_source_count", "Data Source Count"),
    ]


def test_resolve_list_columns_resolves_unified_virtual_via_resolver():
    """A user-defined (unified) virtual field is NOT in the built-in
    VIRTUAL_FIELDS dict, so its label must come from the catalog-backed resolver
    rather than falling back to the bare slug. Regression for #784: CSV/list
    headers showed the slug (e.g. 'lentity') instead of the display name."""
    resolver = MagicMock()
    resolver.resolve.return_value.display_name = "Legal Entity"
    cols = resolve_list_columns(["virtual:lentity"], resolver=resolver)
    assert cols == [("virtual:lentity", "Legal Entity")]


def test_resolve_list_columns_unified_virtual_falls_back_to_slug_without_resolver():
    """No resolver (or an unresolvable field) → graceful fallback to the slug,
    never a crash; the built-in virtuals keep resolving from VIRTUAL_FIELDS."""
    cols = resolve_list_columns(["virtual:lentity"], resolver=None)
    assert cols == [("virtual:lentity", "lentity")]


def test_resolve_list_columns_canonicalises_noncanonical_encoded_row_key():
    """A non-canonical encoded id (literal space, not %20) must map to the SAME
    snake row_key the repo's row dict uses — the repo aliases its columns to
    encode(decode(enc)). Without canonicalisation the column key 'static:IP
    Address' wouldn't match the projected row key 'ip' -> blank column."""
    cols = resolve_list_columns(["static:IP Address"], resolver=None)
    assert cols == [("ip", "IP Address")]


def test_resolve_list_columns_uses_catalog_label_for_ext_field():
    """An ext: column asks the resolver for the catalog display label (so it
    matches Explorer) and keeps its raw encoded key as the row_key (matching the
    repo row dict, which doesn't snake-project ext fields)."""
    rf = MagicMock()
    rf.display_name = "Beszel - cpu"
    resolver = MagicMock()
    resolver.resolve.return_value = rf
    cols = resolve_list_columns(["ext:Beszel:cpu"], resolver)
    assert cols == [("ext:Beszel:cpu", "Beszel - cpu")]


def test_display_label_falls_back_to_derived_label_when_unresolvable():
    """If the catalog can't resolve a field, the label is DERIVED from the
    decoded parts ('<source> - <attribute>' / the name), never the opaque
    encoded key — the user/LLM must always see something human."""
    resolver = MagicMock()
    resolver.resolve.side_effect = Exception("not in catalog")
    assert _display_label_for_encoded("ext:Beszel:cpu", resolver) == "Beszel - cpu"
    # A tag with no resolver derives to its name.
    assert _display_label_for_encoded("tag:Critical", resolver=None) == "Critical"


def test_default_chat_field_set_all_resolve_to_renderable_columns():
    """Every field in the shared default set resolves to a (row_key, label)
    column with no resolver (all static), so the default list preview is fully
    renderable — no default column silently disappears, and no label leaks an
    encoded key."""
    encoded = _normalize_field_list(CHAT_LIST_FIELDS_ENCODED, resolver=None)
    cols = resolve_list_columns(encoded, resolver=None)
    assert len(cols) == len(CHAT_LIST_FIELDS_ENCODED.split(","))
    assert all(":" not in label for _key, label in cols)
