# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for vendor_validators.py

Tests vendor-specific serial number validation and metadata extraction
for Dell, HP, Apple, Cisco, and Lenovo validators, plus VendorDetector.
"""

import pytest
from src.modules.asset_generation.domain.vendor_validators import (
    DellSNValidator,
    HPSNValidator,
    AppleSNValidator,
    CiscoSNValidator,
    LenovoSNValidator,
    VendorDetector,
)


# =============================================================================
# DellSNValidator
# =============================================================================


class TestDellSNValidator:
    """Test Dell Service Tag validation and metadata extraction."""

    @pytest.fixture
    def validator(self):
        return DellSNValidator()

    def test_vendor_name(self, validator):
        """Test vendor_name property returns 'Dell'."""
        assert validator.vendor_name == "Dell"

    # -- validate_format --

    def test_valid_7char_alphanumeric(self, validator):
        """Valid 7-char alphanumeric string passes."""
        assert validator.validate_format("J8HGF32") is True

    def test_valid_7char_lowercase(self, validator):
        """Lowercase input is accepted (uppercased internally)."""
        assert validator.validate_format("j8hgf32") is True

    def test_valid_7char_with_whitespace(self, validator):
        """Leading/trailing whitespace is stripped."""
        assert validator.validate_format("  J8HGF32  ") is True

    def test_too_short(self, validator):
        """Strings shorter than 7 chars are rejected."""
        assert validator.validate_format("ABC12") is False

    def test_too_long(self, validator):
        """Strings longer than 7 chars are rejected."""
        assert validator.validate_format("ABCD1234") is False

    def test_non_alphanumeric(self, validator):
        """Strings with special characters are rejected."""
        assert validator.validate_format("ABC-123") is False
        assert validator.validate_format("ABC 123") is False
        assert validator.validate_format("ABC_12!") is False

    def test_all_same_char(self, validator):
        """All-same-character strings are rejected."""
        assert validator.validate_format("AAAAAAA") is False
        assert validator.validate_format("1111111") is False

    def test_none_input(self, validator):
        """None input returns False."""
        assert validator.validate_format(None) is False

    def test_empty_string(self, validator):
        """Empty string returns False."""
        assert validator.validate_format("") is False

    def test_non_string_input(self, validator):
        """Non-string input returns False."""
        assert validator.validate_format(1234567) is False

    # -- extract_metadata --

    def test_extract_metadata_valid(self, validator):
        """Valid Dell SN returns metadata with vendor and format info."""
        meta = validator.extract_metadata("J8HGF32")
        assert meta["vendor"] == "Dell"
        assert meta["format"] == "Service Tag (7-char)"
        assert meta["validated"] is True

    def test_extract_metadata_invalid(self, validator):
        """Invalid SN returns empty dict."""
        assert validator.extract_metadata("SHORT") == {}
        assert validator.extract_metadata(None) == {}


# =============================================================================
# HPSNValidator
# =============================================================================


class TestHPSNValidator:
    """Test HP serial number validation and metadata extraction."""

    @pytest.fixture
    def validator(self):
        return HPSNValidator()

    def test_vendor_name(self, validator):
        """Test vendor_name property returns 'HP'."""
        assert validator.vendor_name == "HP"

    # -- validate_format --

    def test_valid_10char(self, validator):
        """Standard 10-char HP serial passes."""
        assert validator.validate_format("CND1234ABC") is True

    def test_valid_8char(self, validator):
        """8-char HP serial passes."""
        assert validator.validate_format("AB12CD34") is True

    def test_valid_12char(self, validator):
        """12-char HP serial passes."""
        assert validator.validate_format("CND1234ABCDE") is True

    def test_valid_9char(self, validator):
        """9-char HP serial passes."""
        assert validator.validate_format("CND12345A") is True

    def test_too_short(self, validator):
        """Strings shorter than 8 chars are rejected."""
        assert validator.validate_format("ABC1234") is False

    def test_too_long(self, validator):
        """Strings longer than 12 chars are rejected."""
        assert validator.validate_format("A" * 13) is False

    def test_non_alphanumeric(self, validator):
        """Strings with special characters are rejected."""
        assert validator.validate_format("CND-1234AB") is False

    def test_none_input(self, validator):
        """None returns False."""
        assert validator.validate_format(None) is False

    def test_empty_string(self, validator):
        """Empty string returns False."""
        assert validator.validate_format("") is False

    # -- extract_metadata --

    def test_extract_metadata_valid(self, validator):
        """Valid HP SN returns base metadata."""
        meta = validator.extract_metadata("CND1234ABC")
        assert meta["vendor"] == "HP"
        assert meta["validated"] is True
        assert "HP SN" in meta["format"]

    def test_extract_metadata_country_cn(self, validator):
        """SN starting with 'CN' includes China hint."""
        meta = validator.extract_metadata("CND1234ABC")
        assert meta["manufacture_country_hint"] == "China"

    def test_extract_metadata_country_us(self, validator):
        """SN starting with 'US' includes USA hint."""
        meta = validator.extract_metadata("US12345678")
        assert meta["manufacture_country_hint"] == "USA"

    def test_extract_metadata_country_sg(self, validator):
        """SN starting with 'SG' includes Singapore hint."""
        meta = validator.extract_metadata("SG12345678")
        assert meta["manufacture_country_hint"] == "Singapore"

    def test_extract_metadata_country_my(self, validator):
        """SN starting with 'MY' includes Malaysia hint."""
        meta = validator.extract_metadata("MY12345678")
        assert meta["manufacture_country_hint"] == "Malaysia"

    def test_extract_metadata_country_tw(self, validator):
        """SN starting with 'TW' includes Taiwan hint."""
        meta = validator.extract_metadata("TW12345678")
        assert meta["manufacture_country_hint"] == "Taiwan"

    def test_extract_metadata_no_country_match(self, validator):
        """SN without matching country code has no hint."""
        meta = validator.extract_metadata("XY12345678")
        assert "manufacture_country_hint" not in meta

    def test_extract_metadata_invalid(self, validator):
        """Invalid SN returns empty dict."""
        assert validator.extract_metadata("SHORT") == {}

    def test_extract_metadata_format_includes_length(self, validator):
        """Format string includes the SN length."""
        meta = validator.extract_metadata("ABCD5678EF")
        assert "10-char" in meta["format"]

        meta8 = validator.extract_metadata("AB12CD34")
        assert "8-char" in meta8["format"]


# =============================================================================
# AppleSNValidator
# =============================================================================


class TestAppleSNValidator:
    """Test Apple serial number validation and metadata extraction."""

    @pytest.fixture
    def validator(self):
        return AppleSNValidator()

    def test_vendor_name(self, validator):
        """Test vendor_name property returns 'Apple'."""
        assert validator.vendor_name == "Apple"

    # -- validate_format: 12-char (middle format) --

    def test_valid_12char_with_known_year_week(self, validator):
        """12-char SN with valid year/week chars passes."""
        # Factory=ABC, Year=C(2010-H1), Week=1(week 1)
        assert validator.validate_format("ABCC1DEFGH12") is True

    def test_valid_12char_year_z_week_x(self, validator):
        """12-char SN with year Z and week X passes."""
        # Year=Z(2019-H2), Week=X(week 26)
        assert validator.validate_format("ABCZXDEFGH12") is True

    def test_invalid_12char_bad_year(self, validator):
        """12-char SN with invalid year char (A not in YEAR_MAP_12) fails."""
        assert validator.validate_format("ABCA1DEFGH12") is False

    def test_invalid_12char_bad_year_e(self, validator):
        """12-char SN with year E (skipped in Apple encoding) fails."""
        assert validator.validate_format("ABCE1DEFGH12") is False

    def test_invalid_12char_bad_week(self, validator):
        """12-char SN with invalid week char fails."""
        # 'A' is not in WEEK_MAP_12
        assert validator.validate_format("ABCCADEFGH12") is False

    def test_invalid_12char_week_zero(self, validator):
        """12-char SN with week char '0' (not in map) fails."""
        assert validator.validate_format("ABCC0DEFGH12") is False

    # -- validate_format: 11-char (old format) --

    def test_valid_11char(self, validator):
        """11-char SN with valid year and week passes."""
        # Factory=AB, Year=0(2010), Week=12
        assert validator.validate_format("AB012FGHIJK") is True

    def test_valid_11char_week_01(self, validator):
        """11-char SN with week 01 passes."""
        assert validator.validate_format("AB001FGHIJK") is True

    def test_valid_11char_week_52(self, validator):
        """11-char SN with week 52 passes."""
        assert validator.validate_format("AB052FGHIJK") is True

    def test_invalid_11char_bad_year(self, validator):
        """11-char SN with non-digit year char fails."""
        # Position 2 must be in YEAR_MAP_11 (digits 0-9)
        # 'X' is not a digit
        assert validator.validate_format("ABX12FGHIJK") is False

    def test_invalid_11char_week_00(self, validator):
        """11-char SN with week 00 fails."""
        assert validator.validate_format("AB000FGHIJK") is False

    def test_invalid_11char_week_53(self, validator):
        """11-char SN with week 53 fails."""
        assert validator.validate_format("AB053FGHIJK") is False

    def test_invalid_11char_non_numeric_week(self, validator):
        """11-char SN with non-numeric week fails."""
        assert validator.validate_format("AB0XXFGHIJK") is False

    # -- validate_format: 10-char (new randomized format) --

    def test_valid_10char(self, validator):
        """10-char alphanumeric SN passes."""
        assert validator.validate_format("ABCD123456") is True

    def test_valid_10char_lowercase(self, validator):
        """10-char lowercase SN passes (uppercased internally)."""
        assert validator.validate_format("abcd123456") is True

    # -- validate_format: edge cases --

    def test_too_short(self, validator):
        """Strings shorter than 10 chars are rejected."""
        assert validator.validate_format("ABCDEF789") is False

    def test_too_long(self, validator):
        """Strings longer than 12 chars are rejected."""
        assert validator.validate_format("A" * 13) is False

    def test_none_input(self, validator):
        """None returns False."""
        assert validator.validate_format(None) is False

    def test_empty_string(self, validator):
        """Empty string returns False."""
        assert validator.validate_format("") is False

    def test_non_alphanumeric(self, validator):
        """Strings with special characters are rejected."""
        assert validator.validate_format("ABC-DEF-GHI") is False

    # -- extract_metadata: 12-char --

    def test_extract_metadata_12char_half1(self, validator):
        """12-char metadata extraction for first half of year."""
        # Year=C(2010, H1), Week=3(week 3)
        sn = "ABCC3DEFGH12"
        meta = validator.extract_metadata(sn)
        assert meta["vendor"] == "Apple"
        assert meta["manufacture_year"] == 2010
        assert meta["manufacture_week"] == 3
        assert meta["factory_code"] == "ABC"
        assert meta["format"] == "Apple-12digit-middle"

    def test_extract_metadata_12char_half2(self, validator):
        """12-char metadata extraction for second half of year (week += 26)."""
        # Year=D(2010, H2), Week=1(week 1) -> actual week = 1 + 26 = 27
        sn = "ABCD1DEFGH12"
        meta = validator.extract_metadata(sn)
        assert meta["manufacture_year"] == 2010
        assert meta["manufacture_week"] == 27

    def test_extract_metadata_12char_max_week_half2(self, validator):
        """12-char with half=2 and week X(26) -> actual week = 52."""
        # Year=D(2010, H2), Week=X(26) -> 26 + 26 = 52
        sn = "ABCDXDEFGH12"
        meta = validator.extract_metadata(sn)
        assert meta["manufacture_week"] == 52

    # -- extract_metadata: 11-char --

    def test_extract_metadata_11char(self, validator):
        """11-char metadata extraction."""
        # Factory=AB, Year=0(2010), Week=12
        sn = "AB012FGHIJK"
        meta = validator.extract_metadata(sn)
        assert meta["vendor"] == "Apple"
        assert meta["manufacture_year"] == 2010
        assert meta["manufacture_week"] == 12
        assert meta["factory_code"] == "AB"
        assert meta["format"] == "Apple-11digit-old"

    def test_extract_metadata_11char_year_5(self, validator):
        """11-char with year char '5' maps to 2005."""
        sn = "XY530ABCDEF"
        meta = validator.extract_metadata(sn)
        assert meta["manufacture_year"] == 2005
        assert meta["manufacture_week"] == 30

    # -- extract_metadata: 10-char --

    def test_extract_metadata_10char(self, validator):
        """10-char metadata notes randomized format."""
        sn = "ABCD123456"
        meta = validator.extract_metadata(sn)
        assert meta["vendor"] == "Apple"
        assert meta["format"] == "Apple-10digit-randomized"
        assert "randomized" in meta["note"].lower()
        assert "manufacture_year" not in meta

    # -- extract_metadata: invalid --

    def test_extract_metadata_invalid(self, validator):
        """Invalid SN returns empty dict."""
        assert validator.extract_metadata("SHORT") == {}
        assert validator.extract_metadata(None) == {}


# =============================================================================
# CiscoSNValidator
# =============================================================================


class TestCiscoSNValidator:
    """Test Cisco serial number validation and metadata extraction."""

    @pytest.fixture
    def validator(self):
        return CiscoSNValidator()

    def test_vendor_name(self, validator):
        """Test vendor_name property returns 'Cisco'."""
        assert validator.vendor_name == "Cisco"

    # -- validate_format --

    def test_valid_with_known_prefix(self, validator):
        """SN with known Cisco prefix passes."""
        assert validator.validate_format("FOC1234ABCD") is True
        assert validator.validate_format("FXS5678WXYZ") is True
        assert validator.validate_format("JAE9012MNOP") is True
        assert validator.validate_format("SAL3456QRST") is True

    def test_valid_without_known_prefix(self, validator):
        """SN without known prefix but correct length passes."""
        assert validator.validate_format("XYZ12345AB") is True

    def test_valid_8char(self, validator):
        """Minimum length (8 chars) passes."""
        assert validator.validate_format("ABCD1234") is True

    def test_valid_15char(self, validator):
        """Maximum length (15 chars) passes."""
        assert validator.validate_format("A" * 14 + "B") is True

    def test_too_short(self, validator):
        """Strings shorter than 8 chars are rejected."""
        assert validator.validate_format("ABCD123") is False

    def test_too_long(self, validator):
        """Strings longer than 15 chars are rejected."""
        assert validator.validate_format("A" * 16) is False

    def test_non_alphanumeric(self, validator):
        """Strings with special characters are rejected."""
        assert validator.validate_format("FOC-1234-AB") is False

    def test_none_input(self, validator):
        """None returns False."""
        assert validator.validate_format(None) is False

    def test_empty_string(self, validator):
        """Empty string returns False."""
        assert validator.validate_format("") is False

    # -- extract_metadata --

    def test_extract_metadata_with_foc_prefix(self, validator):
        """FOC prefix yields Catalyst Switch product family hint."""
        meta = validator.extract_metadata("FOC1234ABCD")
        assert meta["vendor"] == "Cisco"
        assert meta["validated"] is True
        assert meta["prefix"] == "FOC"
        assert meta["product_family_hint"] == "Catalyst Switch"

    def test_extract_metadata_with_fxs_prefix(self, validator):
        """FXS prefix yields ASR Router product family hint."""
        meta = validator.extract_metadata("FXS5678WXYZ")
        assert meta["product_family_hint"] == "ASR Router"

    def test_extract_metadata_with_jae_prefix(self, validator):
        """JAE prefix yields Small Business Switch product family hint."""
        meta = validator.extract_metadata("JAE9012MNOP")
        assert meta["product_family_hint"] == "Small Business Switch"

    def test_extract_metadata_with_sal_prefix(self, validator):
        """SAL prefix yields Legacy Equipment product family hint."""
        meta = validator.extract_metadata("SAL3456QRST")
        assert meta["product_family_hint"] == "Legacy Equipment"

    def test_extract_metadata_known_prefix_no_hint(self, validator):
        """Known prefix without a product family hint has prefix but no hint."""
        meta = validator.extract_metadata("FCW12345678")
        assert meta["prefix"] == "FCW"
        assert "product_family_hint" not in meta

    def test_extract_metadata_no_prefix(self, validator):
        """SN without known prefix has no prefix or hint keys."""
        meta = validator.extract_metadata("XYZ12345AB")
        assert "prefix" not in meta
        assert "product_family_hint" not in meta

    def test_extract_metadata_invalid(self, validator):
        """Invalid SN returns empty dict."""
        assert validator.extract_metadata("SHORT") == {}

    def test_extract_metadata_format_includes_length(self, validator):
        """Format string includes the SN length."""
        meta = validator.extract_metadata("FOC1234ABCD")
        assert "11-char" in meta["format"]

    def test_common_prefixes_set(self, validator):
        """Verify all documented common prefixes exist."""
        expected = {"FOC", "FXS", "JAE", "SAL", "FCW", "FTX", "FHK", "CAT",
                    "JMX", "KWC", "DTI", "INM", "SCA", "FCZ", "FGL"}
        assert validator.COMMON_PREFIXES == expected


# =============================================================================
# LenovoSNValidator
# =============================================================================


class TestLenovoSNValidator:
    """Test Lenovo serial number validation and metadata extraction."""

    @pytest.fixture
    def validator(self):
        return LenovoSNValidator()

    def test_vendor_name(self, validator):
        """Test vendor_name property returns 'Lenovo'."""
        assert validator.vendor_name == "Lenovo"

    # -- validate_format --

    def test_valid_10char(self, validator):
        """Standard 10-char Lenovo SN passes."""
        assert validator.validate_format("PF1ABC2345") is True

    def test_valid_8char(self, validator):
        """Minimum length (8 chars) passes."""
        assert validator.validate_format("PF1ABC23") is True

    def test_valid_12char(self, validator):
        """Maximum length (12 chars) passes."""
        assert validator.validate_format("PF1ABC234567") is True

    def test_too_short(self, validator):
        """Strings shorter than 8 chars are rejected."""
        assert validator.validate_format("PF1ABC2") is False

    def test_too_long(self, validator):
        """Strings longer than 12 chars are rejected."""
        assert validator.validate_format("A" * 13) is False

    def test_non_alphanumeric(self, validator):
        """Strings with special characters are rejected."""
        assert validator.validate_format("PF1-ABC-23") is False

    def test_none_input(self, validator):
        """None returns False."""
        assert validator.validate_format(None) is False

    def test_empty_string(self, validator):
        """Empty string returns False."""
        assert validator.validate_format("") is False

    # -- extract_metadata --

    def test_extract_metadata_valid(self, validator):
        """Valid SN returns basic metadata."""
        meta = validator.extract_metadata("PF1ABC2345")
        assert meta["vendor"] == "Lenovo"
        assert meta["validated"] is True
        assert "Lenovo SN" in meta["format"]

    def test_extract_metadata_format_includes_length(self, validator):
        """Format string includes the SN length."""
        meta = validator.extract_metadata("PF1ABC2345")
        assert "10-char" in meta["format"]

    def test_extract_metadata_invalid(self, validator):
        """Invalid SN returns empty dict."""
        assert validator.extract_metadata("SHORT") == {}


# =============================================================================
# VendorDetector
# =============================================================================


class TestVendorDetector:
    """Test vendor detection from serial number format."""

    @pytest.fixture
    def detector(self):
        return VendorDetector()

    # -- detect_vendor --

    def test_detect_dell_7char(self, detector):
        """7-char alphanumeric SN is detected as Dell."""
        result = detector.detect_vendor("J8HGF32")
        assert result is not None
        vendor, validator = result
        assert vendor == "Dell"
        assert isinstance(validator, DellSNValidator)

    def test_detect_cisco_with_prefix(self, detector):
        """SN with known Cisco prefix is detected as Cisco."""
        result = detector.detect_vendor("FOC1234ABCD")
        assert result is not None
        vendor, _ = result
        assert vendor == "Cisco"

    def test_detect_apple_12char(self, detector):
        """12-char SN with valid Apple encoding is detected as Apple."""
        # Year=C(2010-H1), Week=1
        result = detector.detect_vendor("ABCC1DEFGH12")
        assert result is not None
        vendor, _ = result
        assert vendor == "Apple"

    def test_detect_apple_11char(self, detector):
        """11-char SN with valid Apple encoding is detected as Apple."""
        result = detector.detect_vendor("AB012FGHIJK")
        assert result is not None
        vendor, _ = result
        assert vendor == "Apple"

    def test_detect_apple_10char(self, detector):
        """10-char alphanumeric SN is detected as Apple (before HP check)."""
        result = detector.detect_vendor("ABCD123456")
        assert result is not None
        vendor, _ = result
        # Apple accepts all 10-char alphanumeric; it's checked before HP
        assert vendor == "Apple"

    def test_detect_lenovo_range(self, detector):
        """8-12 char SN not matching earlier vendors falls to Lenovo."""
        # 8-char that doesn't match Dell (not 7), Cisco prefix, or Apple (not 10-12)
        # Actually 8-char won't match Apple (requires 10+), so it goes to:
        # Dell(7)? No. Cisco prefix? No. Apple? No (8 chars). HP(10)? No (8 chars). Lenovo? Yes.
        result = detector.detect_vendor("XY12AB34")
        assert result is not None
        vendor, _ = result
        assert vendor == "Lenovo"

    def test_detect_cisco_generic(self, detector):
        """SN matching Cisco format generically (8-15 chars, no prefix)."""
        # 13-char without Cisco prefix, also doesn't match Dell (not 7),
        # Apple 12 (needs valid year/week), Apple 11 (needs valid year/week).
        # Let's use 13-char that fails Apple: position 3 = 'B' (not in YEAR_MAP_12)
        # Actually for 13-char it's not 10/11/12, so Apple rejects it.
        # Then HP (10)? No (13 chars). Lenovo (8-12)? No (13 chars). Cisco generic (8-15)? Yes.
        result = detector.detect_vendor("XYZW123456789")
        assert result is not None
        vendor, _ = result
        assert vendor == "Cisco"

    def test_detect_none_for_none_input(self, detector):
        """None input returns None."""
        assert detector.detect_vendor(None) is None

    def test_detect_none_for_empty_string(self, detector):
        """Empty string returns None."""
        assert detector.detect_vendor("") is None

    def test_detect_none_for_too_short(self, detector):
        """Very short string that matches no validator returns None."""
        # 3-char is too short for all validators (min is 7 for Dell, 8 for others)
        assert detector.detect_vendor("AB1") is None

    def test_detect_none_for_non_string(self, detector):
        """Non-string input returns None."""
        assert detector.detect_vendor(12345) is None

    # -- get_validator --

    def test_get_validator_dell(self, detector):
        """get_validator('dell') returns DellSNValidator."""
        v = detector.get_validator("dell")
        assert isinstance(v, DellSNValidator)

    def test_get_validator_case_insensitive(self, detector):
        """get_validator is case-insensitive."""
        assert detector.get_validator("Dell") is not None
        assert detector.get_validator("DELL") is not None
        assert detector.get_validator("dell") is not None

    def test_get_validator_hp(self, detector):
        """get_validator('hp') returns HPSNValidator."""
        assert isinstance(detector.get_validator("hp"), HPSNValidator)

    def test_get_validator_apple(self, detector):
        """get_validator('apple') returns AppleSNValidator."""
        assert isinstance(detector.get_validator("apple"), AppleSNValidator)

    def test_get_validator_cisco(self, detector):
        """get_validator('cisco') returns CiscoSNValidator."""
        assert isinstance(detector.get_validator("cisco"), CiscoSNValidator)

    def test_get_validator_lenovo(self, detector):
        """get_validator('lenovo') returns LenovoSNValidator."""
        assert isinstance(detector.get_validator("lenovo"), LenovoSNValidator)

    def test_get_validator_unknown(self, detector):
        """get_validator for unknown vendor returns None."""
        assert detector.get_validator("unknown") is None
        assert detector.get_validator("totallyunknown") is None

    # -- Detection priority order edge cases --

    def test_cisco_prefix_takes_priority_over_dell(self, detector):
        """Cisco prefix check comes before Dell for FOC-prefixed 7-char SNs (#16)."""
        # "FOC1234" is 7 chars starting with FOC (Cisco prefix)
        # Cisco prefix check comes first to avoid misclassifying Cisco SNs as Dell
        result = detector.detect_vendor("FOC1234")
        assert result is not None
        vendor, _ = result
        assert vendor == "Cisco"

    def test_cisco_prefix_before_apple(self, detector):
        """Cisco prefix match takes priority over Apple for 11-12 char SNs."""
        # "FOC12345678" is 11 chars with FOC prefix
        # Cisco prefix check comes before Apple
        result = detector.detect_vendor("FOC12345678")
        assert result is not None
        vendor, _ = result
        assert vendor == "Cisco"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
