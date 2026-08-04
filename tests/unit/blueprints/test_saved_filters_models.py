# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from pydantic import ValidationError

from src.api.blueprints.saved_filters.models import (
    SavedFilterCreate, SavedFilterUpdate,
)


def test_create_requires_name_and_payload():
    m = SavedFilterCreate(name="Servers", payload={"version": 1})
    assert m.name == "Servers"
    assert m.visibility == "private"  # default


def test_create_rejects_blank_name():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="", payload={"version": 1})


def test_create_rejects_unknown_visibility():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="x", payload={"version": 1}, visibility="public")


def test_create_rejects_non_object_payload():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="x", payload=["not", "an", "object"])


def test_create_rejects_nan_in_payload():
    # NaN serialises to invalid JSON for JSONB; reject at the edge.
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="x", payload={"bad": float("nan")})


def test_create_rejects_oversize_payload():
    big = {"blob": "a" * 70000}
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="x", payload=big)


def test_create_forbids_extra_fields():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="x", payload={"version": 1}, surprise=1)


def test_update_all_optional():
    m = SavedFilterUpdate()
    assert m.name is None and m.payload is None and m.visibility is None


def test_create_strips_surrounding_whitespace_in_name():
    # " Servers " and "Servers" must not become distinct rows past the
    # case-insensitive unique index.
    m = SavedFilterCreate(name="  Servers  ", payload={"version": 1})
    assert m.name == "Servers"


def test_create_rejects_whitespace_only_name():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="   ", payload={"version": 1})
