# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encode a denylist entry into the pipeline's canonical component form."""

from __future__ import annotations

from typing import Optional, Tuple

from src.modules.asset_generation.domain.mac_utils import canonical_mac_component

ALLOWED_TYPES = ("hostname", "fqdn", "mac", "serial")

# Which match types each identifier type accepts (#991). Mirrors the
# merge_denylist_type_match_check CHECK in migration 135 -- keep the two in step.
# Regex, MAC vendor families, CIDR masks and interior globs ('web*01*') are
# deliberately out of scope; the rationale is on #991.
MATCH_TYPES_BY_TYPE = {
    "hostname": ("exact", "prefix", "suffix"),
    "fqdn": ("exact", "prefix", "suffix"),
    "mac": ("exact", "prefix", "range"),
    "serial": ("exact",),
}

# A range is two bounds rather than one value, so it has its own encoder.
SINGLE_VALUE_MATCH_TYPES = ("exact", "prefix", "suffix")

WILDCARD = "*"
_HEX_DIGITS = frozenset("0123456789abcdef")
_MAC_HEX_LEN = 12  # a canonical MAC component is 12 hex digits
# A 24-bit MA-L OUI is the shortest prefix IEEE assigns a vendor, so it is also
# the shortest prefix an admin can mean. Shorter is a typo or a regex attempt --
# see _mac_prefix.
_MAC_PREFIX_MIN_HEX = 6


def check_type_and_match(identifier_type: str, match_type: str) -> None:
    """Raise ValueError unless this (identifier_type, match_type) pair is legal.

    The single source of truth for the pair rule on the Python side, shared with
    DenylistEntryCreate so the API rejects a pair with the same message the
    encoder would; migration 135's merge_denylist_type_match_check mirrors it in
    the DB for callers that bypass both.
    """
    if identifier_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported identifier_type: {identifier_type!r}")
    allowed = MATCH_TYPES_BY_TYPE[identifier_type]
    if match_type not in allowed:
        raise ValueError(
            f"{identifier_type} entries support match_type "
            f"{', '.join(allowed)}, not {match_type!r}"
        )


def to_canonical_component(
    identifier_type: str,
    raw_value: str,
    match_type: str = "exact",
) -> str:
    """Return the component string the pipeline stores/compares for this entry.

    exact -- the component itself: hostname/fqdn lowercased; serial 'sn:' +
    lowercased; mac canonical colon form.
    prefix/suffix -- the pattern, canonically spelled: 'bc:24:11:*' for a MAC,
    'web*' / '*.corp.local' for a hostname or fqdn.

    Raises ValueError on an unknown type, an (identifier_type, match_type) pair
    the schema rejects, a blank value, or a pattern that can never match (so the
    API returns 400 rather than storing an entry that can never match). Range
    entries take two bounds -- see to_canonical_range().
    """
    check_type_and_match(identifier_type, match_type)
    if match_type not in SINGLE_VALUE_MATCH_TYPES:
        raise ValueError(
            f"{match_type!r} entries take a start and an end value; "
            f"use to_canonical_range()"
        )
    raw = (raw_value or "").strip()
    if not raw:
        raise ValueError("identifier value must not be blank")

    if match_type == "prefix":
        return _mac_prefix(raw) if identifier_type == "mac" else _name_prefix(raw)
    if match_type == "suffix":
        return _name_suffix(raw)

    if identifier_type == "mac":
        mac = canonical_mac_component(raw)
        if mac is None:
            raise ValueError(f"not a valid MAC address: {raw_value!r}")
        return mac
    if identifier_type == "serial":
        return f"sn:{raw.lower()}"
    # hostname / fqdn
    if WILDCARD in raw:
        # Now that '*' means something, an exact 'web*' is a mis-picked
        # match_type rather than a hostname: no component is ever spelled with a
        # wildcard, so the entry would sit there matching nothing.
        raise ValueError(
            f"an exact {identifier_type} cannot contain '{WILDCARD}': {raw!r} "
            f"-- use match_type prefix ('web*') or suffix ('*.corp.local')"
        )
    return raw.lower()


def to_canonical_range(
    identifier_type: str,
    range_start: Optional[str],
    range_end: Optional[str],
) -> Tuple[str, int, int]:
    """Return (identifier_value, start, end) for a range entry.

    identifier_value is the canonical '<start>-<end>' spelling, stored and
    uniqueness-keyed like any other entry; the two ints are the 48-bit bounds the
    matcher bisects. Raises ValueError if the identifier type takes no ranges, a
    bound is missing or unparseable, or the range is reversed -- a range whose end
    precedes its start can never match.
    """
    check_type_and_match(identifier_type, "range")
    start_raw = (range_start or "").strip()
    end_raw = (range_end or "").strip()
    if not start_raw or not end_raw:
        raise ValueError("a range entry needs both a start and an end value")

    start, start_int = _mac_bound(start_raw)
    end, end_int = _mac_bound(end_raw)
    if end_int < start_int:
        raise ValueError(f"range end {end_raw!r} is before its start {start_raw!r}")
    return f"{start}-{end}", start_int, end_int


def _mac_bound(raw: str) -> Tuple[str, int]:
    """A range bound as (canonical MAC, 48-bit int); ValueError if it isn't one.

    Stricter than the exact-mac path, which inherits canonical_mac_component's
    alnum-only cleaning: a bound has to be hex to be an integer at all.
    """
    mac = canonical_mac_component(raw)
    if mac is None:
        raise ValueError(f"not a valid MAC address: {raw!r}")
    hex_digits = mac.replace(":", "")
    if not set(hex_digits) <= _HEX_DIGITS:
        raise ValueError(f"not a valid MAC address: {raw!r}")
    return mac, int(hex_digits, 16)


def _mac_prefix(raw: str) -> str:
    """Canonical MAC prefix: 'BC-24-11-*' and 'bc2411*' both -> 'bc:24:11:*'.

    The '*' must be the last character and the only one -- interior wildcards are
    out of scope (#991). The hex before it is colon-grouped in pairs the way
    mac_vendor_ranges already spells MA-M/MA-S prefixes ('AA:BB:CC:D'), so an
    odd-length prefix keeps its dangling nibble: 'bc24111*' -> 'bc:24:11:1:*'.

    Prefixes shorter than a 24-bit OUI are refused: each missing hex digit
    widens the entry 16-fold, and an admin who types the regex 'b.*' gets the
    literal 1-nibble prefix 'b', silently dropping 1 MAC in 16 from identity.
    That is the same job as the checks above -- refusing an entry that can never
    do what it says -- for an entry that matches far too much rather than
    nothing (#991).
    """
    if not raw.endswith(WILDCARD) or WILDCARD in raw[:-1]:
        raise ValueError(
            f"a MAC prefix must end with '*' and contain no other '*': {raw!r}"
        )
    hex_digits = "".join(filter(str.isalnum, raw[:-1])).lower()
    if not hex_digits:
        # '*' alone denylists every MAC in the dataset.
        raise ValueError(f"a MAC prefix needs hex digits before '*': {raw!r}")
    if not set(hex_digits) <= _HEX_DIGITS:
        # Before the length floor, so 'z*' is diagnosed as non-hex rather than
        # as too short.
        raise ValueError(f"not a valid MAC prefix: {raw!r}")
    if len(hex_digits) < _MAC_PREFIX_MIN_HEX:
        raise ValueError(
            f"a MAC prefix must be at least {_MAC_PREFIX_MIN_HEX} hex digits -- "
            f"a 24-bit OUI, the shortest prefix IEEE assigns a vendor. {raw!r} "
            f"is {len(hex_digits)} and would denylist 1 MAC address in "
            f"{16 ** len(hex_digits)}; use the whole OUI, e.g. 'bc:24:11:*'."
        )
    if len(hex_digits) > _MAC_HEX_LEN:
        # A MAC component is 12 hex digits, so nothing could ever start with it.
        raise ValueError(
            f"a MAC prefix cannot exceed {_MAC_HEX_LEN} hex digits: {raw!r}"
        )
    grouped = ":".join(hex_digits[i:i + 2] for i in range(0, len(hex_digits), 2))
    return f"{grouped}:{WILDCARD}"


def _name_prefix(raw: str) -> str:
    """Canonical hostname/fqdn prefix: 'WEB*' -> 'web*'."""
    value = raw.lower()
    if not value.endswith(WILDCARD) or WILDCARD in value[:-1]:
        raise ValueError(
            f"a prefix must end with '*' and contain no other '*': {raw!r}"
        )
    literal = value[:-1]
    if not literal:
        # '*' alone denylists every hostname and fqdn in the dataset, emptying
        # the asset field of every record that has one.
        raise ValueError("a prefix needs at least one character before '*'")
    return f"{literal}{WILDCARD}"


def _name_suffix(raw: str) -> str:
    """Canonical hostname/fqdn suffix: '*.Corp.Local' -> '*.corp.local'."""
    value = raw.lower()
    if not value.startswith(WILDCARD) or WILDCARD in value[1:]:
        raise ValueError(
            f"a suffix must start with '*' and contain no other '*': {raw!r}"
        )
    literal = value[1:]
    if not literal:
        raise ValueError("a suffix needs at least one character after '*'")
    return f"{WILDCARD}{literal}"
