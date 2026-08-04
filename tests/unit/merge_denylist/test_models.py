# SPDX-License-Identifier: AGPL-3.0-or-later
"""DenylistEntryCreate — the API body's per-type validation (#991).

The point of validating here is that a malformed body becomes a 400 at the
blueprint (Pydantic -> ValidationError) instead of reaching Postgres and coming
back as a CHECK violation, which surfaces as a 500.
"""

import pytest
from pydantic import ValidationError

from src.modules.merge_denylist.models import DenylistEntryCreate


def test_match_type_defaults_to_exact():
    # The pre-#991 body shape must keep working verbatim.
    entry = DenylistEntryCreate(identifier_type="serial", raw_value="ABC123")
    assert entry.match_type == "exact"
    assert entry.range_start is None and entry.range_end is None


@pytest.mark.parametrize(
    "identifier_type,match_type,raw_value",
    [
        ("mac", "prefix", "bc:24:11:*"),
        ("hostname", "prefix", "web*"),
        ("hostname", "suffix", "*-vdi"),
        ("fqdn", "suffix", "*.corp.local"),
        ("fqdn", "prefix", "web*"),
    ],
)
def test_accepts_legal_type_and_match_pairs(identifier_type, match_type, raw_value):
    entry = DenylistEntryCreate(
        identifier_type=identifier_type, match_type=match_type, raw_value=raw_value
    )
    assert entry.match_type == match_type


@pytest.mark.parametrize(
    "identifier_type,match_type",
    [
        # Mirrors migration 135's merge_denylist_type_match_check.
        ("serial", "prefix"),
        ("serial", "suffix"),
        ("serial", "range"),
        ("mac", "suffix"),
        ("hostname", "range"),
        ("fqdn", "range"),
    ],
)
def test_rejects_illegal_type_and_match_pairs(identifier_type, match_type):
    with pytest.raises(ValidationError):
        DenylistEntryCreate(
            identifier_type=identifier_type, match_type=match_type, raw_value="x"
        )


def test_range_takes_bounds_instead_of_raw_value():
    entry = DenylistEntryCreate(
        identifier_type="mac",
        match_type="range",
        range_start="bc:24:11:00:00:00",
        range_end="bc:24:11:ff:ff:ff",
    )
    assert entry.raw_value is None
    assert entry.range_start == "bc:24:11:00:00:00"


@pytest.mark.parametrize(
    "body",
    [
        # A range needs both bounds.
        {"identifier_type": "mac", "match_type": "range",
         "range_start": "bc:24:11:00:00:00"},
        {"identifier_type": "mac", "match_type": "range",
         "range_end": "bc:24:11:ff:ff:ff"},
        # ...and takes no raw_value, so a body carrying both is ambiguous.
        {"identifier_type": "mac", "match_type": "range", "raw_value": "bc:24:11:*",
         "range_start": "bc:24:11:00:00:00", "range_end": "bc:24:11:ff:ff:ff"},
        # Single-value entries need a raw_value...
        {"identifier_type": "hostname", "match_type": "prefix"},
        {"identifier_type": "hostname"},
        # ...and no bounds.
        {"identifier_type": "hostname", "raw_value": "web01",
         "range_start": "bc:24:11:00:00:00"},
    ],
)
def test_rejects_mismatched_field_and_match_type(body):
    with pytest.raises(ValidationError):
        DenylistEntryCreate(**body)


def test_unknown_match_type_is_rejected():
    # Literal, so 'regex' can never even reach the encoder (#991 rejects it).
    with pytest.raises(ValidationError):
        DenylistEntryCreate(
            identifier_type="hostname", match_type="regex", raw_value="web.*"
        )


def test_extra_fields_are_still_forbidden():
    with pytest.raises(ValidationError):
        DenylistEntryCreate(
            identifier_type="hostname", raw_value="web01", enabled=True
        )


@pytest.mark.parametrize("field", ["raw_value", "range_start", "range_end"])
def test_values_are_still_length_bounded(field):
    body = {"identifier_type": "mac", "raw_value": "bc:24:11:22:33:44"}
    if field != "raw_value":
        body = {
            "identifier_type": "mac",
            "match_type": "range",
            "range_start": "bc:24:11:00:00:00",
            "range_end": "bc:24:11:ff:ff:ff",
        }
    body[field] = "x" * 256
    with pytest.raises(ValidationError):
        DenylistEntryCreate(**body)
