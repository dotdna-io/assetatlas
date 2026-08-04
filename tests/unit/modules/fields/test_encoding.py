# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for src/modules/fields/encoding.py — FieldId ↔ string bijection."""

import json
from pathlib import Path

import pytest

from src.modules.fields.encoding import encode, decode, coerce_field_id
from src.modules.fields.models import (
    StaticField, CoreEnrichmentField, ExtendedAttributeField,
    IntegrationGroupField, TagField, PostureField, VirtualField,
)

_FIXTURE_PATH = Path(__file__).parents[3] / "fixtures" / "field-id-encoding.json"


def _load_fixtures():
    return json.loads(_FIXTURE_PATH.read_text())


class TestEncode:
    @pytest.mark.parametrize("case", _load_fixtures(), ids=lambda c: c["encoded"])
    def test_encode_matches_fixture(self, case):
        # Pydantic reparse to get the typed instance
        from pydantic import TypeAdapter
        from src.modules.fields.models import FieldId
        field_id = TypeAdapter(FieldId).validate_python(case["field_id"])
        assert encode(field_id) == case["encoded"]


class TestDecode:
    @pytest.mark.parametrize("case", _load_fixtures(), ids=lambda c: c["encoded"])
    def test_decode_matches_fixture(self, case):
        decoded = decode(case["encoded"])
        # decode returns a typed FieldId model instance; compare by dump
        assert decoded.model_dump() == case["field_id"]


class TestBijection:
    @pytest.mark.parametrize("case", _load_fixtures(), ids=lambda c: c["encoded"])
    def test_encode_decode_roundtrip(self, case):
        s = case["encoded"]
        assert encode(decode(s)) == s


class TestDecodeRejections:
    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            decode("bogus:Hostname")

    def test_missing_segments_raises(self):
        with pytest.raises(ValueError):
            decode("ext:only-one-segment")

    def test_too_many_segments_raises(self):
        with pytest.raises(ValueError):
            decode("static:Hostname:extra")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            decode("")

    def test_invalid_posture_variant_raises(self):
        with pytest.raises(ValueError):
            decode("posture_bogus:secured")


class TestCoerceFieldId:
    """coerce_field_id normalizes every FieldId form to a typed FieldId.

    #822: the wizard stores target_filters[].field as the encoded STRING from
    encodeFieldId (e.g. "static:Category"). Before the fix, coerce_field_id only
    handled dicts/typed instances and passed strings through unchanged, so the
    compiler's resolver could not resolve them — the filter was silently dropped
    and filter alert rules matched the entire fleet. coerce_field_id must decode
    the string form so string, dict, and typed inputs all resolve identically.
    """

    def test_coerce_encoded_static_string_resolves(self):
        assert coerce_field_id("static:Category") == StaticField(
            kind="static", name="Category"
        )

    def test_coerce_encoded_ext_string_percent_decoded(self):
        # %20 -> space must survive coercion, exactly as decode() does it.
        assert coerce_field_id("ext:Corp%20SentinelOne:siteId") == ExtendedAttributeField(
            kind="extended_attribute", source="Corp SentinelOne", attribute="siteId"
        )

    def test_coerce_dict_form_unchanged(self):
        assert coerce_field_id({"kind": "static", "name": "Category"}) == StaticField(
            kind="static", name="Category"
        )

    def test_coerce_typed_form_passthrough(self):
        fid = StaticField(kind="static", name="Category")
        assert coerce_field_id(fid) is fid

    def test_coerce_non_encoded_string_raises(self):
        # A bare name with no valid prefix is genuinely bad data — raise loudly so
        # callers' skip-with-warning handling applies; do NOT silently guess.
        with pytest.raises(ValueError):
            coerce_field_id("Category")
