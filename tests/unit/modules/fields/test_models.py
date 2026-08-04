# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for src/modules/fields/models.py — FieldId discriminated union."""

import pytest
from pydantic import BaseModel, ValidationError

from src.modules.fields.models import (
    FieldId,
    StaticField,
    CoreEnrichmentField,
    ExtendedAttributeField,
    IntegrationGroupField,
    TagField,
    PostureField,
    VirtualField,
    InvalidFieldError,
)


class _Wrapper(BaseModel):
    """Helper so we can exercise the FieldId union via a real model."""
    field_id: FieldId


class TestFieldIdParsing:
    def test_static_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "static", "name": "Hostname"}})
        assert isinstance(w.field_id, StaticField)
        assert w.field_id.name == "Hostname"

    def test_core_enrichment_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "core_enrichment", "name": "Public IP"}})
        assert isinstance(w.field_id, CoreEnrichmentField)

    def test_extended_attribute_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {
            "kind": "extended_attribute",
            "source": "Intune Full",
            "attribute": "deviceEnrollmentType_s",
        }})
        assert isinstance(w.field_id, ExtendedAttributeField)
        assert w.field_id.source == "Intune Full"
        assert w.field_id.attribute == "deviceEnrollmentType_s"

    def test_integration_group_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {
            "kind": "integration_group",
            "group": "Jamf",
            "attribute": "deviceType",
        }})
        assert isinstance(w.field_id, IntegrationGroupField)

    def test_tag_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "tag", "name": "ProductionAsset"}})
        assert isinstance(w.field_id, TagField)

    def test_posture_field_state_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "posture", "category": "secured", "read": "state"}})
        assert isinstance(w.field_id, PostureField)
        assert w.field_id.read == "state"

    def test_posture_field_output_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "posture", "category": "secured", "read": "output"}})
        assert w.field_id.read == "output"

    def test_virtual_field_parses(self):
        w = _Wrapper.model_validate({"field_id": {"kind": "virtual", "name": "confidence_level"}})
        assert isinstance(w.field_id, VirtualField)


class TestFieldIdRejections:
    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {"kind": "malicious", "name": "x"}})

    def test_missing_kind_rejected(self):
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {"name": "Hostname"}})

    def test_missing_required_for_static_rejected(self):
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {"kind": "static"}})  # no name

    def test_missing_required_for_extended_rejected(self):
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {"kind": "extended_attribute", "source": "X"}})  # no attribute

    def test_extra_keys_rejected(self):
        # extra="forbid" — prevents shape smuggling
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {
                "kind": "static", "name": "Hostname", "extra": "smuggled"
            }})

    def test_invalid_posture_read_rejected(self):
        with pytest.raises(ValidationError):
            _Wrapper.model_validate({"field_id": {"kind": "posture", "category": "secured", "read": "unknown"}})

    def test_unknown_virtual_name_not_rejected_at_pydantic_layer(self):
        """VirtualField.name is deliberately an open string. The authoritative
        list of virtual names lives in the runtime catalog (VIRTUAL_FIELDS in
        catalog.py), and the resolver raises InvalidFieldError when a name
        doesn't resolve. Pinning the pydantic model to a closed enum forced a
        two-place change for every new virtual field and blocked dynamic
        registration — not worth the marginal early-rejection benefit.
        """
        obj = _Wrapper.model_validate(
            {"field_id": {"kind": "virtual", "name": "not_a_real_virtual"}}
        )
        assert obj.field_id.name == "not_a_real_virtual"


class TestInvalidFieldError:
    def test_is_exception(self):
        assert issubclass(InvalidFieldError, Exception)

    def test_carries_message(self):
        err = InvalidFieldError("hello")
        assert str(err) == "hello"
