# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for FieldMapper MAC-address normalization (``_convert_to_mac``).

Ingest must normalize the documented vendor MAC formats to PostgreSQL ``macaddr``
form (``xx:xx:xx:xx:xx:xx``) and reject malformed values, otherwise downstream
asset identity/grouping on the ``mac`` identifier breaks. These tests lock that
normalization contract through the public ``map()`` API — which input formats
normalize and which are rejected.
"""

import pytest

from src.modules.integrations.services.field_mapper import FieldMapper


class TestFieldMapperMacConversion:
    """MAC normalization via the public ``map()`` contract (mac is an identifier → array)."""

    @pytest.mark.parametrize(
        "raw_mac",
        [
            "00:0C:29:3E:4F:5A",  # colon-separated
            "00-0C-29-3E-4F-5A",  # dash-separated
            "000C.293E.4F5A",     # dotted (Cisco) notation
            "000C293E4F5A",       # bare, no separators
            "00:0c:29:3e:4f:5a",  # lowercase normalizes to upper
        ],
    )
    def test_documented_formats_normalize_to_macaddr(self, raw_mac):
        """Every documented input format yields the canonical xx:xx:... form."""
        mapper = FieldMapper(field_mapping={'mac': 'macAddress'})
        mapped = mapper.map({'macAddress': raw_mac})
        assert mapped['mac'] == ['00:0C:29:3E:4F:5A']

    @pytest.mark.parametrize(
        "bad_mac",
        [
            "00:0C:29:3E:4F",      # too short (10 hex chars)
            "00:0C:29:3E:4F:5A:6B",  # too long (14 hex chars)
            "ZZ0C293E4F5A",        # 12 chars but non-hex — exercises hex-char validation
            "not-a-mac",
        ],
    )
    def test_invalid_macs_are_dropped(self, bad_mac):
        """Malformed MACs convert to None and are filtered out (identifier → None)."""
        mapper = FieldMapper(field_mapping={'mac': 'macAddress'})
        mapped = mapper.map({'macAddress': bad_mac})
        assert mapped['mac'] is None

    @pytest.mark.parametrize(
        "raw_mac",
        [
            "00 0C 29 3E 4F 5A",       # space-separated
            "00\t0C\t29\t3E\t4F\t5A",  # tab-separated
            "00 0C-29:3E.4F5A",        # mixed separators + whitespace
            " 00:0C:29:3E:4F:5A ",     # surrounding whitespace (e.g. CSV cell trim)
        ],
    )
    def test_whitespace_and_mixed_separators_normalize(self, raw_mac):
        """_convert_to_mac must tolerate any separator style (spaces, tabs, mixed,
        surrounding whitespace), normalizing to the canonical xx:xx:... form — so a
        MAC isn't silently dropped at integration ingest over cosmetic formatting.
        Before this fix it stripped only [:\\-.] and dropped these (PR #707, finding 1)."""
        mapper = FieldMapper(field_mapping={'mac': 'macAddress'})
        mapped = mapper.map({'macAddress': raw_mac})
        assert mapped['mac'] == ['00:0C:29:3E:4F:5A']
