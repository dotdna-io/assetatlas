# SPDX-License-Identifier: AGPL-3.0-or-later
"""Longest-prefix-first MAC-vendor matching (issue #681 A9b).

``AssetEnricher.enrich_mac_vendor`` used to look up only the 24-bit OUI
(``mac[:8]`` of the normalized address). The IEEE registry shares a 24-bit OUI
across multiple smaller MA-M (28-bit) / MA-S (36-bit) blocks, each assigned to a
*different* vendor. Looking up only the 24-bit slice attributes every device
under a shared OUI to whichever vendor last wrote that OUI row, so IoT/embedded
devices that live in a finer block get the wrong vendor (or none at all).

The fix keys the lookup by separator-free hex and tries the longest stored
prefix the MAC can match before falling back to the 24-bit OUI, so a
more-specific block wins. It must also understand the two prefix encodings that
actually occur in ``mac_vendor_ranges`` (confirmed by inspecting the data):

* IEEE-scraper colon format, truncated to <=8 hex -- ``AA:BB:CC`` (24-bit),
  ``AA:BB:CC:D`` (28-bit), ``8C:1F:64:AF`` (MA-S, 36-bit stored truncated to 32).
* CIDR notation from a second importer -- ``04:11:19:10/28``: the ``/28``
  *netmask* (not the literal digits) defines the 28-bit prefix ``0411191``.

The longest-prefix tests below fail on the pre-fix 24-bit-only lookup (it returns
the shared-OUI vendor) and pass once longest-prefix matching is in place.
"""

import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


def _enrich(mac_vendor_rows, macs):
    """Enrich ``macs`` against a synthetic vendor table; return the
    enriched-vendor column as a plain list.

    ``mac_vendor_rows`` is a list of ``(mac_prefix, vendor)`` tuples.
    """
    enricher = AssetEnricher(postgres_adapter=None)
    mac_vendor_df = pd.DataFrame(mac_vendor_rows, columns=["mac_prefix", "vendor"])
    df = pd.DataFrame({"mac": macs})
    out = enricher.enrich_mac_vendor(df, mac_vendor_df)
    return out["mac_vendor_enriched"].tolist()


def test_longer_ma_m_prefix_wins_over_shared_24bit_oui():
    """A MAC inside a 28-bit MA-M block resolves to the MA-M vendor, not the
    vendor of the shared 24-bit OUI it sits under -- the whole point of A9b."""
    rows = [
        ("AA:BB:CC", "Generic OUI Vendor"),  # 24-bit MA-L
        ("AA:BB:CC:D", "Specific MA-M Vendor"),  # 28-bit MA-M under the same OUI
    ]
    # 0xD high-nibble of octet 4 -> inside the AA:BB:CC:D /28 block.
    assert _enrich(rows, ["AA:BB:CC:D5:11:22"]) == ["Specific MA-M Vendor"]


def test_oui_fallback_when_mac_is_outside_every_finer_block():
    """A MAC under the OUI but outside any finer block falls back to the 24-bit
    vendor -- trying longer prefixes first must not lose the OUI match."""
    rows = [
        ("AA:BB:CC", "Generic OUI Vendor"),
        ("AA:BB:CC:D", "Specific MA-M Vendor"),
    ]
    # 0x7 high-nibble -> not the D block -> 24-bit fallback.
    assert _enrich(rows, ["AA:BB:CC:71:11:22"]) == ["Generic OUI Vendor"]


def test_cidr_slash28_notation_defines_the_prefix_by_netmask():
    """CIDR-format rows (``04:11:19:10/28``) must key by the /28 netmask
    (7 hex ``0411191``), not by the literal alnum digits (``0411191028``).
    Each /28 sub-block of the shared OUI resolves to its own vendor; a MAC in no
    sub-block falls back to the OUI vendor."""
    rows = [
        ("04:11:19", "Shared OUI Co"),
        ("04:11:19:10/28", "Acentury"),  # octet4 high-nibble 1
        ("04:11:19:20/28", "Alethea"),  # octet4 high-nibble 2
    ]
    macs = [
        "04:11:19:15:AA:BB",  # high-nibble 1 -> Acentury
        "04:11:19:2F:00:00",  # high-nibble 2 -> Alethea
        "04:11:19:90:00:00",  # high-nibble 9 -> no sub-block -> OUI
    ]
    assert _enrich(rows, macs) == ["Acentury", "Alethea", "Shared OUI Co"]


def test_unknown_prefix_returns_none():
    """A MAC whose OUI is absent from the table enriches to None."""
    rows = [("AA:BB:CC", "Generic OUI Vendor")]
    out = _enrich(rows, ["FF:FF:FF:00:00:00"])
    assert pd.isna(out[0])


def test_junk_and_short_macs_return_none_without_crashing():
    """Invalid / too-short / sentinel MACs must not raise and must enrich to
    None (no spurious prefix match)."""
    rows = [("AA:BB:CC", "Generic OUI Vendor")]
    out = _enrich(rows, ["XYZ", "", "unknown", "00:11"])
    assert all(pd.isna(v) for v in out)


def test_blank_vendor_rows_are_not_indexed():
    """A prefix whose vendor cell is blank/whitespace must NOT enrich a MAC to an
    empty string -- mac_vendor_ranges contains 35 such rows. A blank finer block
    must not shadow the real coarser-OUI vendor, and a MAC matching only a blank
    prefix must read as no-match (None), not ''."""
    rows = [
        ("AA:BB:CC", "Real OUI Vendor"),
        ("AA:BB:CC:D", "   "),  # blank 28-bit block under a real OUI -> ignored
        ("11:22:33", ""),  # blank OUI -> no-match, must not enrich to ''
    ]
    # In the blank /28 block -> fall back to the real OUI vendor (not '').
    # Under the blank OUI -> no-match (not ''). pandas 3 represents the
    # no-match sentinel as NaN in the enriched column (the sibling tests
    # above assert with pd.isna); the contract is "missing, never ''".
    out = _enrich(rows, ["AA:BB:CC:D5:00:00", "11:22:33:44:55:66"])
    assert out[0] == "Real OUI Vendor"
    assert pd.isna(out[1]) and out[1] != ""
