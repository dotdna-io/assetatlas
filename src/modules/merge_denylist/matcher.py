# SPDX-License-Identifier: AGPL-3.0-or-later
"""Match a canonical identity component against the merge denylist (#991)."""

from __future__ import annotations

from bisect import bisect_right
from typing import Iterable, List, Mapping, Optional, Tuple

# A canonical MAC component is 'xx:xx:xx:xx:xx:xx' -- see
# asset_generation.domain.mac_utils.canonical_mac_component, the single source of
# truth for how a MAC is spelled in the asset field and in asset_id_mappings.
_MAC_COMPONENT_LEN = 17
_MAC_COLON_POSITIONS = (2, 5, 8, 11, 14)
_MAC_HEX_LEN = 12
_SERIAL_PREFIX = "sn:"


def _lengths_desc(keys: "Iterable[str]") -> Tuple[int, ...]:
    """The distinct lengths present among the entries, longest first.

    This is the whole reason matching stays O(1) in the entry count: probes are
    driven by the lengths the *entries* happen to use, never by the component, so
    a 200-entry denylist costs the same per component as a 2-entry one.
    """
    return tuple(sorted({len(k) for k in keys}, reverse=True))


def _mac_hex(component: str) -> Optional[str]:
    """Separator-free hex of a MAC-shaped component, or None if it isn't one.

    Seam 2 (s03_load_mappings) has no type context -- just a bare component
    string -- so canonical shape is the only dispatcher available, and seam 1
    reads the same shapes. Serial components ('sn:...') are ruled out by the
    caller before this runs; hostnames, fqdns and IP addresses cannot put ':' in
    all five MAC positions.

    Case is NOT normalised here -- see DenylistMatcher.__contains__, which
    requires an already-canonical component.
    """
    if len(component) != _MAC_COMPONENT_LEN:
        return None
    for i in _MAC_COLON_POSITIONS:
        if component[i] != ":":
            return None
    hex_digits = component.replace(":", "")
    return hex_digits if len(hex_digits) == _MAC_HEX_LEN else None


class DenylistMatcher:
    """Is this canonical component denylisted?  O(1) in the number of entries.

    Built once per pipeline run and probed ~600k times per run (Stage 2 asset
    field assembly: ~440k; Stage 3: every asset_id_mappings row, 159k+ on dev),
    so a probe must not get more expensive as admins add entries. Patterns are
    therefore matched by inverting the loop: instead of testing the component
    against every entry, the component's own candidate keys are probed against
    sets built from the entries.

      MAC prefix        the trick AssetEnricher.find_vendor already uses for
                        vendor OUIs (asset_enricher.py:176) -- key on
                        separator-free hex and probe mac_hex[:L] for each prefix
                        length L present in the denylist. L <= 12, so at most 12
                        probes however many prefixes are stored.
      name prefix       probe name[:L] / name[-L:] for each literal length L
      / suffix          present in the denylist. The lengths come from the
                        entries, never from the component, so this does not
                        enumerate every prefix of the hostname.
      MAC range         the one shape needing per-entry work: bounds are sorted
                        and bisected, O(log N), and only MAC components pay it.

    Implements __contains__, so both seams keep reading `component in denylist`
    and a plain frozenset stays a drop-in -- Stage 2 passes frozenset() on its
    fail-open path, and exact-only matching costs exactly one set lookup, as it
    did before patterns existed.
    """

    __slots__ = (
        "_exact",
        "_has_patterns",
        "_mac_prefixes",
        "_mac_prefix_lengths",
        "_name_prefixes",
        "_name_prefix_lengths",
        "_name_suffixes",
        "_name_suffix_lengths",
        "_range_starts",
        "_range_max_ends",
    )

    def __init__(self, rows: "Iterable[Mapping[str, object]]" = ()) -> None:
        """Build from the enabled merge_denylist rows.

        Each row supplies identifier_type, match_type, identifier_value and --
        for a range -- range_start / range_end. Migration 135's CHECK constraints
        already confine those to the legal combinations, so every row is taken at
        face value here.
        """
        exact = set()
        mac_prefixes = set()
        name_prefixes = set()
        name_suffixes = set()
        ranges: List[Tuple[int, int]] = []

        for row in rows:
            match_type = row["match_type"]
            value = row["identifier_value"]
            if match_type == "exact":
                exact.add(value)
            elif match_type == "prefix":
                if row["identifier_type"] == "mac":
                    # 'bc:24:11:*' -> 'bc2411': neither ':' nor '*' is
                    # alphanumeric. See encoding.to_canonical_component.
                    mac_prefixes.add("".join(filter(str.isalnum, value)))
                else:
                    name_prefixes.add(value[:-1])  # 'web*' -> 'web'
            elif match_type == "suffix":
                name_suffixes.add(value[1:])  # '*.corp.local' -> '.corp.local'
            elif match_type == "range":
                ranges.append((row["range_start"], row["range_end"]))

        self._exact = frozenset(exact)
        self._mac_prefixes = frozenset(mac_prefixes)
        self._name_prefixes = frozenset(name_prefixes)
        self._name_suffixes = frozenset(name_suffixes)
        # Distinct lengths only. A slice of length L can only ever equal a stored
        # key of length L, so one flat set per kind is enough and the probe order
        # doesn't change the answer -- descending just keeps it deterministic.
        self._mac_prefix_lengths = _lengths_desc(mac_prefixes)
        self._name_prefix_lengths = _lengths_desc(name_prefixes)
        self._name_suffix_lengths = _lengths_desc(name_suffixes)

        ranges.sort()
        self._range_starts = [start for start, _ in ranges]
        # Running max of the ends: with ranges sorted by start, a component is
        # covered iff the widest end among the ranges starting at or before it
        # reaches it. Holds even when ranges overlap, which comparing against
        # that one range's own end would not. Bounds are non-negative (the
        # range_bounds CHECK), so 0 is a safe seed.
        self._range_max_ends: List[int] = []
        widest = 0
        for _, end in ranges:
            if end > widest:
                widest = end
            self._range_max_ends.append(widest)

        # Fast path: with no pattern entries -- the shape of every denylist until
        # #991, and still the common one -- a probe is the single frozenset
        # lookup it has always been.
        self._has_patterns = bool(
            mac_prefixes or name_prefixes or name_suffixes or ranges
        )

    def __contains__(self, component: str) -> bool:
        """Is this component denylisted?

        PRECONDITION: `component` is a canonical component string -- a str, and
        already lowercased. Both seams guarantee it (asset_validator lowercases
        every component it assembles; s03 lowercases each mapping key before
        probing), and the entries are stored lowercased by the encoder, so
        nothing is re-normalised on a path that runs ~600k times per run.
        An uppercase component would silently miss the prefix sets while still
        matching a range, and a non-str would raise rather than answer False.
        """
        if component in self._exact:
            return True
        if not self._has_patterns:
            return False
        if component.startswith(_SERIAL_PREFIX):
            return False  # serial entries are exact-only (migration 135 CHECK)
        mac_hex = _mac_hex(component)
        if mac_hex is not None:
            return self._mac_matches(mac_hex)
        return self._name_matches(component)

    def _mac_matches(self, mac_hex: str) -> bool:
        for length in self._mac_prefix_lengths:
            # No length guard: the encoder rejects prefixes longer than 12 hex
            # digits (nothing could start with them) and mac_hex is exactly 12.
            if mac_hex[:length] in self._mac_prefixes:
                return True
        if not self._range_starts:
            return False
        try:
            value = int(mac_hex, 16)
        except ValueError:
            # int() is the hex test -- _mac_hex() only proves the separators, and
            # something merely shaped like a MAC is not one. Stage 2 runs this
            # inside df.apply, where raising would take the pipeline down.
            return False
        i = bisect_right(self._range_starts, value) - 1
        return i >= 0 and value <= self._range_max_ends[i]

    def _name_matches(self, name: str) -> bool:
        size = len(name)
        for length in self._name_prefix_lengths:
            # A literal longer than the name can't be its prefix, and name[:L]
            # would silently compare a shorter slice.
            if length <= size and name[:length] in self._name_prefixes:
                return True
        for length in self._name_suffix_lengths:
            if length <= size and name[-length:] in self._name_suffixes:
                return True
        return False
