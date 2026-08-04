# SPDX-License-Identifier: AGPL-3.0-or-later
"""DenylistMatcher: exact entries plus the pattern entries added by #991.

Entries are built through the real encoder rather than hand-spelled canonical
strings, so these double as a round-trip guard: the matcher un-spells what
to_canonical_component() spells, and if either side changes the spelling of a
pattern alone, these fail.
"""

import pytest

from src.modules.merge_denylist.encoding import (
    to_canonical_component,
    to_canonical_range,
)
from src.modules.merge_denylist.matcher import DenylistMatcher


def _entry(identifier_type, raw, match_type="exact"):
    """A merge_denylist row for a single-value entry, as the DB would hold it."""
    return {
        "identifier_type": identifier_type,
        "match_type": match_type,
        "identifier_value": to_canonical_component(identifier_type, raw, match_type),
        "range_start": None,
        "range_end": None,
    }


def _range_entry(start, end):
    value, start_int, end_int = to_canonical_range("mac", start, end)
    return {
        "identifier_type": "mac",
        "match_type": "range",
        "identifier_value": value,
        "range_start": start_int,
        "range_end": end_int,
    }


# ---------------------------------------------------------------------------
# Exact entries — the pre-#991 behavior, which must be untouched
# ---------------------------------------------------------------------------

def test_empty_matcher_matches_nothing():
    m = DenylistMatcher()
    assert "web01" not in m
    assert "bc:24:11:22:33:44" not in m
    assert "sn:abc123" not in m


def test_exact_entries_still_match_only_themselves():
    m = DenylistMatcher([
        _entry("hostname", "web01"),
        _entry("mac", "bc:24:11:22:33:44"),
        _entry("serial", "ABC123"),
    ])
    assert "web01" in m
    assert "bc:24:11:22:33:44" in m
    assert "sn:abc123" in m
    assert "web02" not in m
    assert "bc:24:11:22:33:45" not in m
    assert "sn:abc124" not in m


def test_exact_entry_does_not_match_a_longer_component():
    # An exact entry is not a prefix: 'web01' must not take out 'web011'.
    m = DenylistMatcher([_entry("hostname", "web01")])
    assert "web011" not in m
    assert "web01.corp.local" not in m


# ---------------------------------------------------------------------------
# MAC prefix
# ---------------------------------------------------------------------------

def test_mac_prefix_covers_the_whole_vendor_range():
    # The motivating case: one entry knocks out every Proxmox guest veth MAC.
    m = DenylistMatcher([_entry("mac", "bc:24:11:*", "prefix")])
    assert "bc:24:11:22:33:44" in m
    assert "bc:24:11:00:00:00" in m
    assert "bc:24:11:ff:ff:ff" in m
    # A different OUI is untouched — the point of a prefix over a blanket ban.
    assert "aa:bb:cc:22:33:44" not in m
    assert "bc:24:12:22:33:44" not in m


def test_mac_prefix_with_an_odd_nibble():
    # 7 hex — a 28-bit MA-M block, and the shortest odd-nibble prefix the
    # encoder's OUI floor allows (#991).
    m = DenylistMatcher([_entry("mac", "bc:24:11:1*", "prefix")])
    assert "bc:24:11:12:33:44" in m
    assert "bc:24:11:1f:22:33" in m
    assert "bc:24:11:22:33:44" not in m


def test_several_prefix_lengths_coexist():
    # The probe is mac_hex[:L] for each distinct L in the denylist, so a coarse
    # OUI and a finer sub-block have to both keep working.
    m = DenylistMatcher([
        _entry("mac", "bc:24:11:*", "prefix"),
        _entry("mac", "aa:bb:cc:dd:*", "prefix"),
    ])
    assert "bc:24:11:99:99:99" in m
    assert "aa:bb:cc:dd:00:01" in m
    assert "aa:bb:cc:de:00:01" not in m


def test_mac_prefix_does_not_match_a_hostname_with_the_same_text():
    # Namespaces are separate: shape dispatch sends a hostname to the name
    # patterns, never to the MAC hex keys.
    m = DenylistMatcher([_entry("mac", "bc:24:11:*", "prefix")])
    assert "bc2411server" not in m
    assert "bc:24:11" not in m  # not a full component, so not MAC-shaped


# ---------------------------------------------------------------------------
# MAC range
# ---------------------------------------------------------------------------

def test_mac_range_is_inclusive_of_both_bounds():
    m = DenylistMatcher([
        _range_entry("bc:24:11:00:00:10", "bc:24:11:00:00:20"),
    ])
    assert "bc:24:11:00:00:10" in m  # start
    assert "bc:24:11:00:00:18" in m  # inside
    assert "bc:24:11:00:00:20" in m  # end
    assert "bc:24:11:00:00:0f" not in m  # one below
    assert "bc:24:11:00:00:21" not in m  # one above


def test_disjoint_ranges_are_bisected_independently():
    m = DenylistMatcher([
        _range_entry("00:00:00:00:00:10", "00:00:00:00:00:20"),
        _range_entry("bc:24:11:00:00:00", "bc:24:11:00:00:ff"),
        _range_entry("ff:ff:00:00:00:00", "ff:ff:00:00:00:05"),
    ])
    assert "00:00:00:00:00:15" in m
    assert "bc:24:11:00:00:7f" in m
    assert "ff:ff:00:00:00:03" in m
    # In the gaps between them.
    assert "00:00:00:00:00:21" not in m
    assert "bc:24:11:00:01:00" not in m
    assert "ff:ff:00:00:00:06" not in m


def test_overlapping_ranges_do_not_hide_each_other():
    # Bisect lands on the last range that starts at or before the component; if
    # the matcher only compared against that one range's end, a component inside
    # a wider earlier range would escape. The running max of ends is what makes
    # this correct.
    m = DenylistMatcher([
        _range_entry("bc:24:11:00:00:00", "bc:24:11:ff:ff:ff"),  # wide
        _range_entry("bc:24:11:00:00:05", "bc:24:11:00:00:06"),  # narrow, inside
    ])
    assert "bc:24:11:aa:bb:cc" in m  # only the wide range covers this
    assert "bc:24:11:00:00:05" in m
    assert "bc:24:12:00:00:00" not in m


def test_range_does_not_match_a_component_that_only_looks_like_a_mac():
    # A 17-char string with colons in the MAC positions but non-hex digits is a
    # name, not a MAC. It must answer False, not raise out of Stage 2's df.apply.
    m = DenylistMatcher([_range_entry("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")])
    assert "zz:zz:zz:zz:zz:zz" not in m


# ---------------------------------------------------------------------------
# hostname / fqdn prefix + suffix
# ---------------------------------------------------------------------------

def test_hostname_prefix():
    m = DenylistMatcher([_entry("hostname", "web*", "prefix")])
    assert "web01" in m
    assert "web" in m  # the literal itself
    assert "webserver.corp.local" in m
    assert "db01" not in m
    assert "myweb01" not in m  # prefix, not "contains"


def test_fqdn_suffix():
    m = DenylistMatcher([_entry("fqdn", "*.corp.local", "suffix")])
    assert "web01.corp.local" in m
    assert "db.corp.local" in m
    assert "web01.corp.example" not in m
    assert "corp.local" not in m  # the '.' is part of the literal


def test_suffix_longer_than_the_component_does_not_match():
    # name[-L:] silently returns the whole name when L > len(name); the length
    # guard is what stops 'ab' matching the suffix '*.corp.local'.
    m = DenylistMatcher([_entry("fqdn", "*.corp.local", "suffix")])
    assert "ab" not in m
    assert "local" not in m


def test_prefix_and_suffix_are_different_namespaces():
    m = DenylistMatcher([
        _entry("hostname", "vdi*", "prefix"),
        _entry("hostname", "*vdi", "suffix"),
    ])
    assert "vdi-pool-01" in m   # prefix
    assert "pool-01-vdi" in m   # suffix
    assert "pool-vdi-01" not in m  # interior — out of scope by design (#991)


def test_name_patterns_do_not_match_macs_or_serials():
    m = DenylistMatcher([
        _entry("hostname", "bc*", "prefix"),
        _entry("hostname", "*44", "suffix"),
    ])
    assert "bc:24:11:22:33:44" not in m  # MAC-shaped -> MAC patterns only
    assert "sn:bc2411" not in m          # serial entries are exact-only


def test_serial_component_never_matches_a_pattern():
    # migration 135 confines serial to exact, and the matcher short-circuits on
    # the 'sn:' prefix — a serial must not be probed against name patterns.
    m = DenylistMatcher([
        _entry("hostname", "sn*", "prefix"),
        _entry("hostname", "*abc123", "suffix"),
    ])
    assert "sn:abc123" not in m


# ---------------------------------------------------------------------------
# Mixed denylists
# ---------------------------------------------------------------------------

def test_exact_and_patterns_coexist():
    m = DenylistMatcher([
        _entry("hostname", "web01"),
        _entry("hostname", "vdi*", "prefix"),
        _entry("fqdn", "*.corp.local", "suffix"),
        _entry("mac", "bc:24:11:*", "prefix"),
        _entry("serial", "ABC123"),
        _range_entry("aa:00:00:00:00:00", "aa:00:00:00:00:ff"),
    ])
    assert "web01" in m
    assert "vdi-01" in m
    assert "host.corp.local" in m
    assert "bc:24:11:22:33:44" in m
    assert "sn:abc123" in m
    assert "aa:00:00:00:00:7f" in m
    # Nothing that shouldn't.
    assert "web02" not in m
    assert "srv-01" not in m
    assert "host.corp.example" not in m
    assert "aa:bb:cc:dd:ee:ff" not in m
    assert "sn:xyz789" not in m


def test_probes_assume_an_already_lowercased_component():
    # __contains__ documents that it takes canonical components: both seams
    # lowercase before probing, and the ~600k probes/run are why nothing is
    # re-normalised here. Pinning it so the precondition can't rot silently --
    # an uppercase MAC misses the prefix set but still hits a range, which is
    # the surprising half.
    prefixed = DenylistMatcher([_entry("mac", "bc:24:11:*", "prefix")])
    assert "bc:24:11:22:33:44" in prefixed
    assert "BC:24:11:22:33:44" not in prefixed  # not canonical -> not matched

    ranged = DenylistMatcher([_range_entry("bc:24:11:00:00:00", "bc:24:11:ff:ff:ff")])
    assert "BC:24:11:22:33:44" in ranged  # int(hex, 16) doesn't care about case


@pytest.mark.parametrize(
    "component",
    ["", "a", "sn:", ":", "::::::", "1.2.3.4", "bc:24:11:22:33:4", "not a component"],
)
def test_odd_components_answer_false_rather_than_raise(component):
    # Both seams probe whatever the dataset produced; the matcher is a predicate
    # and must never be the thing that breaks asset generation.
    m = DenylistMatcher([
        _entry("mac", "bc:24:11:*", "prefix"),
        _entry("hostname", "web*", "prefix"),
        _entry("fqdn", "*.corp.local", "suffix"),
        _range_entry("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"),
    ])
    assert component not in m
