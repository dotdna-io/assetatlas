# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-13: Test that 'windows 2019/10' and bare 'windows YYYY' normalize correctly.

Bug: Raw inputs like 'Windows 2019/10' (ambiguous Server 2019 / Windows 10)
fall to tier 4 fallback, producing garbled output. Bare year numbers like
2008, 2012, 2016, 2019, 2022, 2025 always mean Windows Server.
"""
import pytest
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.os_normalization.hybrid_cpe_normalizer import (
    HybridCPENormalizer,
)


@pytest.fixture
def normalizer():
    return HybridCPENormalizer()


class TestBareWindowsYearResolvesToServer:
    """Bare 'windows YYYY' (no 'server' keyword) should resolve to windows_server."""

    @pytest.mark.parametrize("raw_input,expected_product", [
        ("Windows 2019", "windows_server_2019"),
        ("Windows 2022", "windows_server_2022"),
        ("Windows 2016", "windows_server_2016"),
        ("Windows 2012", "windows_server_2012"),
        ("Windows 2008", "windows_server_2008"),
        ("Windows 2025", "windows_server_2025"),
        ("windows 2019", "windows_server_2019"),
        ("windows 2019 enterprise", "windows_server_2019"),
        ("windows 2019 datacenter", "windows_server_2019"),
        ("Microsoft Windows 2019", "windows_server_2019"),
        ("Windows 2012 R2", "windows_server_2012"),
    ])
    def test_bare_year_resolves_to_server(self, normalizer, raw_input, expected_product):
        result = normalizer.normalize(raw_input)
        assert result.product == expected_product, (
            f"Input {raw_input!r}: expected product={expected_product!r}, "
            f"got product={result.product!r}, os_normalized={result.os_normalized!r}, tier={result.tier}"
        )

    @pytest.mark.parametrize("raw_input,expected_product", [
        ("Windows 2019", "windows_server_2019"),
        ("Windows 2022", "windows_server_2022"),
        ("Windows 2016", "windows_server_2016"),
    ])
    def test_bare_year_does_not_fall_to_tier4(self, normalizer, raw_input, expected_product):
        result = normalizer.normalize(raw_input)
        assert result.tier < 4, (
            f"Input {raw_input!r}: should not fall to tier 4 fallback "
            f"(got tier={result.tier}, os_normalized={result.os_normalized!r})"
        )


class TestSlashAmbiguityResolution:
    """'Windows 2019/10' style inputs should resolve to server, not garbled output."""

    @pytest.mark.parametrize("raw_input,expected_product", [
        ("Windows 2019/10", "windows_server_2019"),
        ("windows 2019/10", "windows_server_2019"),
        ("Windows 2022/11", "windows_server_2022"),
        ("Windows 2016/10", "windows_server_2016"),
    ])
    def test_slash_ambiguity_resolves_to_server(self, normalizer, raw_input, expected_product):
        result = normalizer.normalize(raw_input)
        assert result.product == expected_product, (
            f"Input {raw_input!r}: expected product={expected_product!r}, "
            f"got product={result.product!r}, os_normalized={result.os_normalized!r}"
        )

    def test_no_slash_in_normalized_output(self, normalizer):
        result = normalizer.normalize("Windows 2019/10")
        assert "/" not in (result.os_normalized or ""), (
            f"Slash survived in os_normalized: {result.os_normalized!r}"
        )

    @pytest.mark.parametrize("raw_input", [
        "Windows 2019/10",
        "windows 2019/10",
        "Windows 2022/11",
    ])
    def test_slash_input_does_not_fall_to_tier4(self, normalizer, raw_input):
        result = normalizer.normalize(raw_input)
        assert result.tier < 4, (
            f"Input {raw_input!r}: should not fall to tier 4 "
            f"(got tier={result.tier}, os_normalized={result.os_normalized!r})"
        )
