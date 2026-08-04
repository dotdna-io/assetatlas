# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from pathlib import Path

import pytest
from src.modules.merge_denylist.encoding import (
    MATCH_TYPES_BY_TYPE,
    SINGLE_VALUE_MATCH_TYPES,
    to_canonical_component,
    to_canonical_range,
)

# Every (identifier_type, match_type) pair to_canonical_component encodes, read
# off the pair rule itself so a type or match type gained later cannot quietly
# skip the blank-value check below. Range is excluded: it takes two bounds and
# has its own encoder.
_SINGLE_VALUE_PAIRS = [
    (identifier_type, match_type)
    for identifier_type, match_types in MATCH_TYPES_BY_TYPE.items()
    for match_type in match_types
    if match_type in SINGLE_VALUE_MATCH_TYPES
]

_FIXTURE_PATH = (
    Path(__file__).parents[2] / "fixtures" / "merge-denylist-match-types.json"
)


@pytest.mark.parametrize(
    "itype,raw,expected",
    [
        ("hostname", "WEB01", "web01"),
        ("hostname", "  Web01 ", "web01"),
        ("fqdn", "Web01.Corp.EXAMPLE", "web01.corp.example"),
        ("serial", "ABC123", "sn:abc123"),
        ("serial", "abc123", "sn:abc123"),
        ("mac", "BC-24-11-22-33-44", "bc:24:11:22:33:44"),
    ],
)
def test_encodes_to_component_form(itype, raw, expected):
    assert to_canonical_component(itype, raw) == expected


def test_bad_mac_raises():
    with pytest.raises(ValueError):
        to_canonical_component("mac", "not-a-mac")


@pytest.mark.parametrize("itype,match_type", _SINGLE_VALUE_PAIRS)
@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_blank_value_raises_for_every_identifier_type(itype, match_type, raw):
    # Blank has to fail for every type: an exact entry would otherwise encode to
    # '' for a hostname or fqdn and to the bare 'sn:' for a serial — components
    # no record ever carries, so the entry would sit in the admin's table looking
    # real while matching nothing. None is in the list because the encoder reads
    # the value through `(raw_value or "")`, making a missing value and a blank
    # one the same case. The check also runs ahead of the per-type encoders,
    # which is what makes the message the accurate one rather than a prefix
    # complaining about a missing '*'.
    with pytest.raises(ValueError, match="must not be blank"):
        to_canonical_component(itype, raw, match_type)


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        to_canonical_component("ip", "1.2.3.4")


# ---------------------------------------------------------------------------
# Pattern entries (#991)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "itype,raw,match_type,expected",
    [
        # The motivating case: one entry for the whole Proxmox guest-veth OUI,
        # spelled any of the ways an admin might type it.
        ("mac", "bc:24:11:*", "prefix", "bc:24:11:*"),
        ("mac", "BC-24-11-*", "prefix", "bc:24:11:*"),
        ("mac", "bc2411*", "prefix", "bc:24:11:*"),
        # Odd hex counts keep a dangling nibble, the way mac_vendor_ranges
        # already spells 28-bit MA-M prefixes ('AA:BB:CC:D').
        ("mac", "bc24111*", "prefix", "bc:24:11:1:*"),
        ("hostname", "WEB*", "prefix", "web*"),
        ("hostname", " web* ", "prefix", "web*"),
        ("fqdn", "*.Corp.LOCAL", "suffix", "*.corp.local"),
        ("hostname", "*-vdi", "suffix", "*-vdi"),
    ],
)
def test_encodes_patterns_to_canonical_spelling(itype, raw, match_type, expected):
    assert to_canonical_component(itype, raw, match_type) == expected


@pytest.mark.parametrize(
    "itype,raw,match_type",
    [
        # Interior globs are out of scope (#991) — same cost shape as regex.
        ("hostname", "web*01*", "prefix"),
        ("hostname", "*web*", "suffix"),
        ("mac", "bc:*:11:*", "prefix"),
        # A prefix that isn't one: no trailing '*' to anchor it.
        ("hostname", "web01", "prefix"),
        ("fqdn", "corp.local", "suffix"),
        # Bare '*' would denylist every hostname in the dataset.
        ("hostname", "*", "prefix"),
        ("hostname", "*", "suffix"),
        # Longer than a MAC — no component could ever start with it.
        ("mac", "bc:24:11:22:33:44:55:*", "prefix"),
        ("mac", "*", "prefix"),
        ("mac", "zz:zz:zz:*", "prefix"),
        # Combinations the schema rejects (migration 135's CHECK).
        ("serial", "abc*", "prefix"),
        ("serial", "*abc", "suffix"),
        ("mac", "*:24:11", "suffix"),
    ],
)
def test_rejects_patterns_that_can_never_match(itype, raw, match_type):
    with pytest.raises(ValueError):
        to_canonical_component(itype, raw, match_type)


@pytest.mark.parametrize(
    "raw,covers",
    [
        # 'b*' is what an admin typing the regex 'b.*' actually gets: a literal
        # 1-nibble prefix matching 1 MAC in 16 -- ~6% of the fleet silently
        # dropped from identity, which under-merges into duplicates (#991).
        ("b*", "1 MAC address in 16"),
        ("bc*", "1 MAC address in 256"),
        ("bc:2*", "1 MAC address in 4096"),
        # 20 bits: still short of an OUI, still 1 MAC in a million.
        ("bc241*", "1 MAC address in 1048576"),
    ],
)
def test_rejects_mac_prefix_shorter_than_an_oui(raw, covers):
    with pytest.raises(ValueError, match="at least 6 hex digits") as exc:
        to_canonical_component("mac", raw, "prefix")
    # The 400 has to tell the admin how much of the fleet they nearly denylisted
    # and what the shape should have been -- 'invalid prefix' is unactionable.
    assert covers in str(exc.value)
    assert "bc:24:11:*" in str(exc.value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The floor is exactly the shortest prefix IEEE assigns, so every real
        # vendor block still encodes: MA-L (24-bit), MA-M (28), MA-S (32 as
        # mac_vendor_ranges truncates it) -- the lengths find_vendor looks up.
        ("bc:24:11:*", "bc:24:11:*"),
        ("bc24111*", "bc:24:11:1:*"),
        ("bc2411af*", "bc:24:11:af:*"),
    ],
)
def test_accepts_mac_prefix_of_an_oui_or_longer(raw, expected):
    assert to_canonical_component("mac", raw, "prefix") == expected


@pytest.mark.parametrize("itype", ["hostname", "fqdn"])
@pytest.mark.parametrize("raw", ["web*", "*.corp.local", "*"])
def test_exact_entry_containing_a_wildcard_is_rejected(itype, raw):
    # An admin who leaves match_type on the default and types 'web*' means a
    # prefix. Stored as exact it would match nothing at all -- no component is
    # ever spelled with a '*' -- and sit there silently doing nothing, which is
    # exactly what this encoder exists to prevent.
    with pytest.raises(ValueError, match="prefix"):
        to_canonical_component(itype, raw, "exact")


def test_exact_entries_without_a_wildcard_are_unaffected():
    # Guards the rejection above from over-reaching: '*' is the only character
    # this rules out, and the pre-existing exact path is otherwise untouched.
    assert to_canonical_component("hostname", "web-01_x.y") == "web-01_x.y"
    assert to_canonical_component("serial", "AB*123") == "sn:ab*123"  # serial: no
    # prefix/suffix alternative exists, so '*' is taken as a literal character.


def test_range_needs_its_own_encoder():
    # 'range' is legal for mac, but it takes two bounds — the single-value
    # encoder must not silently treat one of them as the whole entry.
    with pytest.raises(ValueError):
        to_canonical_component("mac", "bc:24:11:00:00:00", "range")


def test_encodes_mac_range_to_bounds():
    value, start, end = to_canonical_range(
        "mac", "BC:24:11:00:00:00", "bc-24-11-ff-ff-ff"
    )
    assert value == "bc:24:11:00:00:00-bc:24:11:ff:ff:ff"
    assert start == 0xBC24_1100_0000
    assert end == 0xBC24_11FF_FFFF


def test_reversed_range_raises():
    # end < start can never match anything, so it never reaches the table.
    with pytest.raises(ValueError):
        to_canonical_range("mac", "bc:24:11:ff:ff:ff", "bc:24:11:00:00:00")


def test_single_mac_range_is_allowed():
    # start == end is degenerate but matches exactly one MAC — not "never".
    value, start, end = to_canonical_range(
        "mac", "bc:24:11:22:33:44", "bc:24:11:22:33:44"
    )
    assert value == "bc:24:11:22:33:44-bc:24:11:22:33:44"
    assert start == end


@pytest.mark.parametrize(
    "itype,start,end",
    [
        ("mac", "bc:24:11:00:00:00", None),
        ("mac", None, "bc:24:11:ff:ff:ff"),
        ("mac", "bc:24:11:00:00:00", "not-a-mac"),
        # A bound has to be hex to be an integer at all.
        ("mac", "bc:24:11:00:00:00", "zz:zz:zz:zz:zz:zz"),
        # Only mac takes ranges.
        ("hostname", "web01", "web99"),
        ("serial", "abc123", "abc999"),
    ],
)
def test_bad_range_raises(itype, start, end):
    with pytest.raises(ValueError):
        to_canonical_range(itype, start, end)


# ---------------------------------------------------------------------------
# Parity with the admin UI (#991)
# ---------------------------------------------------------------------------
#
# MergeDenylistTab builds its match-type selector from its own copy of the pair
# rule, so a type gained here and not there is a feature the UI silently never
# offers -- and a pair lost here and not there is a 400 the admin walks into.
# Same shared-fixture shape as tests/fixtures/field-id-encoding.json; the TS
# half is ui/prod/src/api/__tests__/merge-denylist.test.ts.


def test_match_type_pairs_match_the_ui_fixture():
    fixture = json.loads(_FIXTURE_PATH.read_text())["match_types_by_type"]
    assert {k: list(v) for k, v in MATCH_TYPES_BY_TYPE.items()} == fixture


def test_preview_fields_resolve_to_the_columns_the_ui_counts():
    # The preview asks /assets/values for these by display name. A rename in
    # STATIC_FIELDS would leave it counting nothing, with no other test failing.
    from src.modules.fields.catalog import STATIC_FIELDS

    fixture = json.loads(_FIXTURE_PATH.read_text())["preview_fields"]
    assert set(fixture) == set(MATCH_TYPES_BY_TYPE)
    for identifier_type, spec in fixture.items():
        assert STATIC_FIELDS.get(spec["display_name"]) == spec["column"], (
            f"the {identifier_type} preview field moved"
        )
