# SPDX-License-Identifier: AGPL-3.0-or-later
"""FieldId discriminated union: typed field identifier used across all
subsystems that filter, select, or aggregate over asset fields.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class InvalidFieldError(Exception):
    """Raised when a FieldId references a field that does not exist in
    the live catalog (e.g. unknown extended_attribute source, non-existent
    tag, unregistered virtual). Pydantic shape errors raise ValidationError
    instead and are caught by the request-parsing layer.
    """
    pass


_STRICT = ConfigDict(extra="forbid")


class StaticField(BaseModel):
    model_config = _STRICT
    kind: Literal["static"]
    name: str


class CoreEnrichmentField(BaseModel):
    model_config = _STRICT
    kind: Literal["core_enrichment"]
    name: str


class ExtendedAttributeField(BaseModel):
    model_config = _STRICT
    kind: Literal["extended_attribute"]
    source: str
    attribute: str


class IntegrationGroupField(BaseModel):
    model_config = _STRICT
    kind: Literal["integration_group"]
    group: str
    attribute: str


class TagField(BaseModel):
    model_config = _STRICT
    kind: Literal["tag"]
    name: str


class PostureField(BaseModel):
    model_config = _STRICT
    kind: Literal["posture"]
    category: str
    read: Literal["state", "output"]


class VirtualField(BaseModel):
    model_config = _STRICT
    kind: Literal["virtual"]
    # Open name — the catalog (VIRTUAL_FIELDS in catalog.py) is the
    # authoritative list of registered virtual fields. Previously pinned
    # to Literal["confidence_level"], which blocked every other virtual
    # field at pydantic validation time and forced a two-place change to
    # add one. The resolver already raises InvalidFieldError for names
    # not in the catalog, so validation is not lost.
    name: str


FieldId = Annotated[
    Union[
        StaticField,
        CoreEnrichmentField,
        ExtendedAttributeField,
        IntegrationGroupField,
        TagField,
        PostureField,
        VirtualField,
    ],
    Field(discriminator="kind"),
]
