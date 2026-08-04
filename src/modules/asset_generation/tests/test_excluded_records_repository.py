#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ExcludedRecordsRepository ip/mac syntactic validators (#681 A8).

`excluded_records.ip` is a Postgres ``inet`` column and `.mac` is ``macaddr``. The
pipeline writes raw *rejected* identifiers ('N/A', 'DHCP', garbage, ...) into them via
a bulk ``execute_values`` page insert, so a single value Postgres can't parse aborts
the entire page and the run's excluded-records audit trail is silently lost.

These validators decide whether a raw value would be accepted by ``inet`` / ``macaddr``;
an unparseable value must become NULL (the raw text is preserved in ``exclusion_details``
by the repository). They are strict *parse* checks only — NOT identity-validity checks
(virtual/placeholder MACs are still valid syntax here) and NOT normalizers (the repo
already has four divergent MAC impls; we add no fifth). Hence each test asserts a valid
value is returned *unchanged*, so the check can never silently start rewriting values.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import src.modules.asset_generation.repositories.excluded_records_repository as er_module
from src.modules.asset_generation.repositories.excluded_records_repository import (
    ExcludedRecordsRepository,
    _valid_inet,
    _valid_macaddr,
)


class TestValidInet:
    """_valid_inet returns the value unchanged when Postgres ``inet`` accepts it, else None."""

    @pytest.mark.parametrize(
        "value",
        [
            "10.0.0.1",  # plain IPv4
            "10.0.0.0/24",  # IPv4 with CIDR (inet accepts a network/prefix)
            "fe80::1",  # IPv6
            "192.168.1.255",
        ],
    )
    def test_valid_values_returned_unchanged(self, value):
        assert _valid_inet(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "N/A",
            "DHCP",
            "unknown",
            "",
            "999.1.1.1",  # octet out of range
            "10.0.0.0/99",  # impossible prefix length
            "not an ip",
            " 10.0.0.1",  # leading whitespace (Postgres inet rejects; validated as-is)
            "10.0.0.1\n",  # trailing newline
        ],
    )
    def test_invalid_values_become_none(self, value):
        assert _valid_inet(value) is None

    def test_none_passes_through(self):
        assert _valid_inet(None) is None


class TestValidMacaddr:
    """_valid_macaddr returns the value unchanged when it parses as a 6-byte MAC, else None."""

    @pytest.mark.parametrize(
        "value",
        [
            "00:11:22:33:44:55",  # colon-separated
            "00-11-22-33-44-55",  # hyphen-separated
            "0011.2233.4455",  # dot-separated (Cisco style)
            "001122334455",  # bare 12 hex
            "AABBCCDDEEFF",  # uppercase hex
        ],
    )
    def test_valid_values_returned_unchanged(self, value):
        assert _valid_macaddr(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "N/A",
            "DHCP",
            "ZZ:ZZ:ZZ:ZZ:ZZ:ZZ",  # non-hex digits
            "gg:hh:ii:jj:kk:ll",  # non-hex digits
            "00:11:22",  # too short (3 bytes)
            "00:11:22:33:44:55:66",  # too long (7 bytes)
            "",
            " 00:11:22:33:44:55",  # leading whitespace (validated as-is)
            "001122334455\n",  # trailing newline (fullmatch rejects)
        ],
    )
    def test_invalid_values_become_none(self, value):
        assert _valid_macaddr(value) is None

    def test_none_passes_through(self):
        assert _valid_macaddr(None) is None


class TestSaveExcludedRecordsSanitization:
    """save_excluded_records NULLs invalid ip/mac, preserves the raw value in exclusion_details,
    and never lets a non-dict exclusion_details turn one bad row into a whole-save failure (#681 A8).

    Exercises the repository's record-building/save logic with a mocked cursor — no live DB — so it
    runs in CI alongside the pure-validator tests (the DB round-trip is covered by the integration
    test). The tuple positions below must match ``column_order`` in the repository.
    """

    IP = 9
    MAC = 10
    EXCLUSION_DETAILS = 23

    def _saved_values(self, records):
        """Run save_excluded_records against a mocked cursor; return (values, saved_count)."""
        repo = ExcludedRecordsRepository(MagicMock())
        repo.db.conn_manager.is_scope_active.return_value = False
        cursor_ctx = repo.db.conn_manager.get_cursor.return_value
        cursor_ctx.__enter__.return_value = (MagicMock(), MagicMock())
        cursor_ctx.__exit__.return_value = False
        with patch.object(er_module, "execute_values") as mock_ev:
            count = repo.save_excluded_records(records, uuid4())
        # execute_values(cursor, insert_query, values, page_size=1000)
        values = mock_ev.call_args.args[2]
        return values, count

    def _details(self, row):
        raw = row[self.EXCLUSION_DETAILS]
        return json.loads(raw) if isinstance(raw, str) else (raw or {})

    def test_invalid_ip_nulled_and_raw_preserved(self):
        values, count = self._saved_values(
            [{"hostname": "bad-ip", "ip": "N/A", "exclusion_details": {"note": "x"}}]
        )
        assert count == 1
        assert values[0][self.IP] is None
        assert self._details(values[0]) == {"note": "x", "invalid_ip": "N/A"}

    def test_invalid_mac_nulled_and_raw_preserved(self):
        values, _ = self._saved_values([{"hostname": "bad-mac", "mac": "DHCP"}])
        assert values[0][self.MAC] is None
        assert self._details(values[0])["invalid_mac"] == "DHCP"

    def test_valid_ip_mac_pass_through_unchanged(self):
        values, _ = self._saved_values([{"ip": "10.0.0.1", "mac": "00:11:22:33:44:55"}])
        assert values[0][self.IP] == "10.0.0.1"
        assert values[0][self.MAC] == "00:11:22:33:44:55"
        # No invalid markers added for clean rows.
        assert "invalid_ip" not in self._details(values[0])
        assert "invalid_mac" not in self._details(values[0])

    @pytest.mark.parametrize("details", [None, float("nan"), "already-serialized"])
    def test_non_dict_exclusion_details_with_invalid_ip_does_not_crash(self, details):
        # _build_exclusion_details always returns a dict, but a None/NaN/str value must NOT let
        # one bad ip raise and lose the whole run's audit trail — the exact #681 A8 failure mode.
        values, count = self._saved_values(
            [{"ip": "N/A", "exclusion_details": details}]
        )
        assert count == 1
        assert values[0][self.IP] is None
        assert self._details(values[0])["invalid_ip"] == "N/A"
