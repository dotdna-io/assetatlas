# SPDX-License-Identifier: AGPL-3.0-or-later
import pandas as pd
import pytest
from src.modules.asset_generation.domain.mac_utils import canonical_mac_component


@pytest.mark.parametrize("raw,expected", [
    ("BC:24:11:22:33:44", "bc:24:11:22:33:44"),
    ("bc-24-11-22-33-44", "bc:24:11:22:33:44"),
    ("BC2411223344",      "bc:24:11:22:33:44"),
    ("bc:24:11:22:33:44:55:66", "bc:24:11:22:33:44"),  # extra octets truncated to 12 hex
    ("  BC:24:11:22:33:44  ", "bc:24:11:22:33:44"),
])
def test_canonical_forms(raw, expected):
    assert canonical_mac_component(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "unknown", "UNKNOWN", "bc:24:11", None])
def test_invalid_returns_none(bad):
    assert canonical_mac_component(bad) is None


# ---------------------------------------------------------------------------
# Non-string input (#973)
#
# `value` is untyped and str()-coerced on purpose: AssetEnricher.preprocess
# applies this straight down a DataFrame column (df['mac'].apply), and those
# cells hold whatever the source loaded, not necessarily strings.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), pd.NA, True, 0, 1234])
def test_non_string_under_twelve_hex_digits_returns_none(bad):
    # A record whose source carries no MAC leaves pandas' own missing markers in
    # the column. None — rather than a raise or a fabricated component — is what
    # keeps that record in the run without a MAC identity: str() coercion turns
    # these into 'nan' / '<NA>' / 'True' / '1234', and stripping the
    # non-alphanumerics leaves every one of them under the 12-hex-digit floor.
    assert canonical_mac_component(bad) is None


def test_numeric_mac_canonicalises_like_its_string_spelling():
    # The flip side of that coercion: a source exporting MACs unquoted lands a
    # bare int here, and str() is what recovers it. It has to reach the same
    # component the string spelling does, or one NIC becomes two identities
    # depending on which source happened to see it.
    assert canonical_mac_component(981234567890) == "98:12:34:56:78:90"
    assert canonical_mac_component(981234567890) == canonical_mac_component(
        "98:12:34:56:78:90"
    )
