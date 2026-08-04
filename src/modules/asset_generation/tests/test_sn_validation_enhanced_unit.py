# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for sn_validation_enhanced.py

Tests the EnhancedSNValidator including entropy analysis, virtual environment
detection, vendor-specific validation, sequential pattern detection,
suspicious SN detection, quality evaluation, and statistics tracking, plus
are_sns_similar — the veto that decides whether an over-merged asset is split.
"""

import math
import pytest
import numpy as np
import pandas as pd

from src.modules.asset_generation.domain.sn_validation_enhanced import (
    EnhancedSNValidator,
    are_sns_similar,
)


# =============================================================================
# Initialization
# =============================================================================


class TestEnhancedSNValidatorInit:
    """Test EnhancedSNValidator initialization."""

    def test_init_defaults(self):
        """Default init uses expected threshold values."""
        v = EnhancedSNValidator()
        assert v.sn_overuse_threshold == 4
        assert v.sn_migration_threshold == 2
        assert v.consecutive_char_length_threshold == 4
        assert v.entropy_threshold == 2.0
        assert v.enable_vendor_validation is True
        assert v.vendor_detector is not None

    def test_init_custom_thresholds(self):
        """Custom thresholds are stored correctly."""
        v = EnhancedSNValidator(
            sn_overuse_threshold=10,
            sn_migration_threshold=5,
            consecutive_char_length_threshold=3,
            entropy_threshold=3.0,
        )
        assert v.sn_overuse_threshold == 10
        assert v.sn_migration_threshold == 5
        assert v.consecutive_char_length_threshold == 3
        assert v.entropy_threshold == 3.0

    def test_init_vendor_validation_disabled(self):
        """Disabling vendor validation sets detector to None."""
        v = EnhancedSNValidator(enable_vendor_validation=False)
        assert v.vendor_detector is None
        assert v.enable_vendor_validation is False


# =============================================================================
# Entropy Analysis
# =============================================================================


class TestCalculateEntropy:
    """Test Shannon entropy calculation."""

    def test_all_same_character(self):
        """All-same-char string has zero entropy."""
        assert EnhancedSNValidator.calculate_entropy("AAAAAA") == 0.0

    def test_empty_string(self):
        """Empty string returns 0.0."""
        assert EnhancedSNValidator.calculate_entropy("") == 0.0

    def test_single_char(self):
        """Single character (length < 2) returns 0.0."""
        assert EnhancedSNValidator.calculate_entropy("A") == 0.0

    def test_none_input(self):
        """None input returns 0.0."""
        assert EnhancedSNValidator.calculate_entropy(None) == 0.0

    def test_two_distinct_equal_chars(self):
        """Two equally distributed chars have entropy of 1.0."""
        # "AB" has 2 chars each appearing once: -2*(0.5*log2(0.5)) = 1.0
        assert EnhancedSNValidator.calculate_entropy("AB") == pytest.approx(1.0)

    def test_mixed_chars_positive_entropy(self):
        """Mixed-character string has positive entropy."""
        entropy = EnhancedSNValidator.calculate_entropy("J8HGF32")
        assert entropy > 0.0

    def test_higher_variety_higher_entropy(self):
        """More character variety yields higher entropy."""
        low = EnhancedSNValidator.calculate_entropy("AABB")
        high = EnhancedSNValidator.calculate_entropy("ABCD")
        assert high > low

    def test_case_insensitive(self):
        """Entropy calculation normalizes to lowercase."""
        upper = EnhancedSNValidator.calculate_entropy("ABCDEF")
        lower = EnhancedSNValidator.calculate_entropy("abcdef")
        assert upper == pytest.approx(lower)

    def test_known_entropy_value(self):
        """Verify entropy for a string with known distribution."""
        # "AABB" has 2 chars each appearing twice in length 4:
        # -2*(2/4 * log2(2/4)) = -2*(0.5*(-1)) = 1.0
        assert EnhancedSNValidator.calculate_entropy("AABB") == pytest.approx(1.0)


class TestIsLowEntropy:
    """Test low-entropy detection."""

    def test_low_entropy_repeating_pattern(self):
        """Repeating pattern with low entropy is detected."""
        v = EnhancedSNValidator(entropy_threshold=2.0)
        # "ABABABAB" has entropy ~1.0, below 2.0 threshold
        assert v.is_low_entropy("ABABABAB") is True

    def test_high_entropy_passes(self):
        """High-entropy string is not flagged."""
        v = EnhancedSNValidator(entropy_threshold=2.0)
        # Diverse characters produce higher entropy
        assert v.is_low_entropy("J8HGF32XYZ") is False

    def test_short_string_skipped(self):
        """Strings shorter than 6 chars return False (skip analysis)."""
        v = EnhancedSNValidator(entropy_threshold=2.0)
        assert v.is_low_entropy("AAAA") is False
        assert v.is_low_entropy("AB") is False

    def test_empty_string_skipped(self):
        """Empty string returns False."""
        v = EnhancedSNValidator()
        assert v.is_low_entropy("") is False

    def test_custom_threshold(self):
        """Custom threshold parameter overrides instance default."""
        v = EnhancedSNValidator(entropy_threshold=1.0)
        # With instance threshold 1.0, "ABABABAB" (entropy ~1.0) might pass
        # But with explicit threshold=3.0 it should be flagged
        assert v.is_low_entropy("ABABABAB", threshold=3.0) is True

    def test_none_input_skipped(self):
        """None input returns False."""
        v = EnhancedSNValidator()
        assert v.is_low_entropy(None) is False


# =============================================================================
# Virtual Environment Detection
# =============================================================================


class TestIsVirtualEnvironmentSN:
    """Test virtual environment serial number detection."""

    @pytest.fixture
    def validator(self):
        return EnhancedSNValidator()

    def test_vmware_prefix(self, validator):
        """VMware prefixed SN is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("vmware-abc123")
        assert is_virtual is True
        assert env_type == "VMware VM"

    def test_vmware_virtual_platform(self, validator):
        """VMware Virtual Platform string is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn(
            "VMware Virtual Platform"
        )
        assert is_virtual is True
        assert env_type == "VMware VM"

    def test_ec2_prefix(self, validator):
        """AWS EC2 prefixed SN is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("ec2-instance-abc")
        assert is_virtual is True
        assert env_type == "AWS EC2"

    def test_aws_instance_id(self, validator):
        """AWS instance ID format is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn(
            "i-0123456789abcdef0"
        )
        assert is_virtual is True
        assert env_type == "AWS Instance ID"

    def test_uuid_v4(self, validator):
        """UUID v4 format is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn(
            "550e8400-e29b-41d4-a716-446655440000"
        )
        assert is_virtual is True
        assert env_type == "UUID v4 (likely VM)"

    def test_docker_container_id(self, validator):
        """Docker container ID (12-char hex) is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("a1b2c3d4e5f6")
        assert is_virtual is True
        assert env_type == "Docker Container ID"

    def test_xen_prefix(self, validator):
        """Xen prefixed SN is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("xen-abcdef-1234")
        assert is_virtual is True
        assert env_type == "Xen VM"

    def test_kvm_prefix(self, validator):
        """KVM prefixed SN is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("kvm-abcdef-1234")
        assert is_virtual is True
        assert env_type == "KVM VM"

    def test_vbox_prefix(self, validator):
        """VirtualBox prefixed SN is detected."""
        is_virtual, env_type = validator.is_virtual_environment_sn("vbox-abcdef-1234")
        assert is_virtual is True
        assert env_type == "VirtualBox VM"

    def test_normal_sn_not_detected(self, validator):
        """Normal physical SN is not flagged as virtual."""
        is_virtual, env_type = validator.is_virtual_environment_sn("CND9876XYZ")
        assert is_virtual is False
        assert env_type is None

    def test_none_input(self, validator):
        """None input returns (False, None)."""
        is_virtual, env_type = validator.is_virtual_environment_sn(None)
        assert is_virtual is False
        assert env_type is None

    def test_empty_string(self, validator):
        """Empty string returns (False, None)."""
        is_virtual, env_type = validator.is_virtual_environment_sn("")
        assert is_virtual is False
        assert env_type is None

    def test_non_string_input(self, validator):
        """Non-string input returns (False, None)."""
        is_virtual, env_type = validator.is_virtual_environment_sn(12345)
        assert is_virtual is False
        assert env_type is None

    def test_case_insensitive(self, validator):
        """Detection is case-insensitive (lowercased internally)."""
        is_virtual, _ = validator.is_virtual_environment_sn("VMWARE-ABC123")
        assert is_virtual is True


# =============================================================================
# Vendor Format Validation
# =============================================================================


class TestValidateVendorFormat:
    """Test vendor-specific format validation."""

    def test_vendor_validation_enabled_known_vendor(self):
        """Known vendor SN returns valid result with metadata."""
        v = EnhancedSNValidator(enable_vendor_validation=True)
        result = v.validate_vendor_format("J8HGF32")  # Dell 7-char
        assert result["is_valid"] is True
        assert result["vendor"] == "Dell"
        assert result["confidence"] == 0.9  # Has metadata
        assert result["metadata"]["vendor"] == "Dell"

    def test_vendor_validation_enabled_unknown_vendor(self):
        """Unknown vendor SN returns is_valid=None."""
        v = EnhancedSNValidator(enable_vendor_validation=True)
        result = v.validate_vendor_format("XY")  # Too short for any vendor
        assert result["is_valid"] is None
        assert result["vendor"] is None

    def test_vendor_validation_disabled(self):
        """With vendor validation disabled, returns is_valid=None."""
        v = EnhancedSNValidator(enable_vendor_validation=False)
        result = v.validate_vendor_format("J8HGF32")
        assert result["is_valid"] is None
        assert result["vendor"] is None

    def test_confidence_with_metadata(self):
        """Valid SN with metadata gets 0.9 confidence."""
        v = EnhancedSNValidator(enable_vendor_validation=True)
        result = v.validate_vendor_format("J8HGF32")
        assert result["confidence"] == 0.9


# =============================================================================
# Sequential Detection
# =============================================================================


class TestHasSequentialNumbers:
    """Test sequential number detection."""

    @pytest.fixture
    def validator(self):
        return EnhancedSNValidator()

    def test_ascending_digits(self, validator):
        """Ascending digit sequence is detected."""
        assert validator.has_sequential_numbers("12345") is True

    def test_ascending_within_string(self, validator):
        """Sequential digits embedded in string are detected."""
        assert validator.has_sequential_numbers("abc1234def") is True

    def test_non_ascending(self, validator):
        """Non-ascending digits are not detected."""
        assert validator.has_sequential_numbers("54321") is False

    def test_letters_only(self, validator):
        """Pure letter strings are not detected."""
        assert validator.has_sequential_numbers("abcde") is False

    def test_short_string(self, validator):
        """String shorter than threshold returns False."""
        assert validator.has_sequential_numbers("12") is False

    def test_custom_length(self, validator):
        """Custom length parameter changes detection threshold."""
        assert validator.has_sequential_numbers("123", length=3) is True
        assert validator.has_sequential_numbers("12", length=3) is False

    def test_high_digit_sequence(self, validator):
        """Sequence starting at high digits is detected."""
        assert validator.has_sequential_numbers("56789") is True


class TestHasSequentialLetters:
    """Test sequential letter detection."""

    @pytest.fixture
    def validator(self):
        return EnhancedSNValidator()

    def test_ascending_lowercase(self, validator):
        """Ascending lowercase letters are detected."""
        assert validator.has_sequential_letters("abcde") is True

    def test_ascending_uppercase(self, validator):
        """Ascending uppercase letters are detected (case insensitive)."""
        assert validator.has_sequential_letters("ABCDE") is True

    def test_mixed_case(self, validator):
        """Mixed case sequential letters are detected."""
        assert validator.has_sequential_letters("AbCdE") is True

    def test_non_sequential_letters(self, validator):
        """Non-sequential letters are not detected."""
        assert validator.has_sequential_letters("acegi") is False

    def test_digits_not_detected(self, validator):
        """Digit-only strings are not detected as sequential letters."""
        assert validator.has_sequential_letters("12345") is False

    def test_short_string(self, validator):
        """String shorter than threshold returns False."""
        assert validator.has_sequential_letters("ab") is False

    def test_custom_length(self, validator):
        """Custom length parameter changes detection threshold."""
        assert validator.has_sequential_letters("abc", length=3) is True
        assert validator.has_sequential_letters("ab", length=3) is False

    def test_embedded_in_string(self, validator):
        """Sequential letters embedded in a larger string are detected."""
        assert validator.has_sequential_letters("123abcd789") is True


# =============================================================================
# Main Suspicious Detection
# =============================================================================


class TestIsSNSuspicious:
    """Test the main is_sn_suspicious method."""

    @pytest.fixture
    def validator(self):
        return EnhancedSNValidator()

    # -- Null/empty --

    def test_nan_is_suspicious(self, validator):
        """NaN value is suspicious."""
        assert validator.is_sn_suspicious(np.nan) is True

    def test_pd_na_is_suspicious(self, validator):
        """pd.NA is suspicious."""
        assert validator.is_sn_suspicious(pd.NA) is True

    def test_none_is_suspicious(self, validator):
        """None is suspicious."""
        assert validator.is_sn_suspicious(None) is True

    def test_empty_string_is_suspicious(self, validator):
        """Empty string is suspicious."""
        assert validator.is_sn_suspicious("") is True

    def test_whitespace_is_suspicious(self, validator):
        """Whitespace-only string is suspicious."""
        assert validator.is_sn_suspicious("   ") is True

    # -- Common placeholders --

    def test_unknown_placeholder(self, validator):
        """'unknown' is flagged as common placeholder."""
        assert validator.is_sn_suspicious("unknown") is True

    def test_na_placeholder(self, validator):
        """'n/a' is flagged as common placeholder."""
        assert validator.is_sn_suspicious("n/a") is True

    def test_oem_placeholder(self, validator):
        """'to be filled by o.e.m.' is flagged."""
        assert validator.is_sn_suspicious("To Be Filled By O.E.M.") is True

    def test_default_string_placeholder(self, validator):
        """'default string' is flagged."""
        assert validator.is_sn_suspicious("Default String") is True

    # -- SMBIOS placeholders --

    def test_smbios_placeholder(self, validator):
        """SMBIOS placeholder strings are flagged."""
        assert validator.is_sn_suspicious("Asset Tag") is True
        assert validator.is_sn_suspicious("Base Board Serial Number") is True
        assert validator.is_sn_suspicious("Chassis Serial Number") is True

    # -- Virtual environment --

    def test_vmware_sn_suspicious(self, validator):
        """VMware SN is flagged as suspicious."""
        assert validator.is_sn_suspicious("vmware-56 4d e8 94") is True

    def test_aws_instance_id_suspicious(self, validator):
        """AWS instance ID is flagged as suspicious."""
        assert validator.is_sn_suspicious("i-0123456789abcdef0") is True

    # -- Too short --

    def test_too_short_suspicious(self, validator):
        """Strings shorter than 4 chars (after stripping) are suspicious."""
        assert validator.is_sn_suspicious("AB") is True
        assert validator.is_sn_suspicious("XY1") is True

    # -- All same character --

    def test_all_same_char_suspicious(self, validator):
        """All-same-character strings are suspicious."""
        assert validator.is_sn_suspicious("AAAA") is True
        assert validator.is_sn_suspicious("0000") is True

    # -- Simple sequences --

    def test_simple_sequence_suspicious(self, validator):
        """Simple numeric sequences are suspicious."""
        assert validator.is_sn_suspicious("12345") is True
        assert validator.is_sn_suspicious("1234567") is True

    # -- Consecutive chars --

    def test_consecutive_chars_suspicious(self, validator):
        """Strings with 4+ consecutive identical characters are suspicious."""
        assert validator.is_sn_suspicious("ABaaaa123") is True
        assert validator.is_sn_suspicious("11110000AB") is True

    # -- Sequential numbers/letters --

    def test_sequential_numbers_suspicious(self, validator):
        """Sequential ascending digits are suspicious."""
        assert validator.is_sn_suspicious("ABC123456") is True

    def test_sequential_letters_suspicious(self, validator):
        """Sequential ascending letters are suspicious."""
        assert validator.is_sn_suspicious("XYZ-ABCDEF") is True

    # -- Low entropy --

    def test_low_entropy_suspicious(self, validator):
        """Low-entropy strings with 6+ chars are suspicious."""
        # "ABABABABAB" (10 chars) has entropy ~1.0, below default 2.0
        assert validator.is_sn_suspicious("ABABABABAB") is True

    # -- Overused SN --

    def test_overused_sn_suspicious(self, validator):
        """SN in exclusion set is flagged as overused."""
        exclude = {"OVERUSED123"}
        assert validator.is_sn_suspicious("OVERUSED123", sns_to_exclude_due_to_overuse=exclude) is True

    def test_not_in_overuse_set_passes(self, validator):
        """SN not in exclusion set is not flagged for overuse."""
        exclude = {"OVERUSED123"}
        assert validator.is_sn_suspicious("CND9876XYZ", sns_to_exclude_due_to_overuse=exclude) is False

    # -- Valid SNs --

    def test_valid_sn_not_suspicious(self, validator):
        """Legitimate serial numbers pass all checks."""
        assert validator.is_sn_suspicious("CND9876XYZ") is False

    def test_valid_dell_sn(self, validator):
        """Valid Dell service tag passes."""
        assert validator.is_sn_suspicious("J8HGF32") is False

    # -- return_reason --

    def test_return_reason_nan(self, validator):
        """return_reason=True for NaN returns tuple with reason."""
        result = validator.is_sn_suspicious(np.nan, return_reason=True)
        assert isinstance(result, tuple)
        is_suspicious, reason = result
        assert is_suspicious is True
        assert reason == "null_or_empty"

    def test_return_reason_empty(self, validator):
        """return_reason=True for empty string returns tuple."""
        is_suspicious, reason = validator.is_sn_suspicious("", return_reason=True)
        assert is_suspicious is True
        assert reason == "empty_string"

    def test_return_reason_placeholder(self, validator):
        """return_reason=True for placeholder returns 'common_placeholder'."""
        is_suspicious, reason = validator.is_sn_suspicious("unknown", return_reason=True)
        assert is_suspicious is True
        assert reason == "common_placeholder"

    def test_return_reason_smbios(self, validator):
        """return_reason=True for SMBIOS placeholder returns 'smbios_placeholder'."""
        is_suspicious, reason = validator.is_sn_suspicious("asset tag", return_reason=True)
        assert is_suspicious is True
        assert reason == "smbios_placeholder"

    def test_return_reason_virtual_env(self, validator):
        """return_reason=True for virtual env includes env type."""
        is_suspicious, reason = validator.is_sn_suspicious("vmware-abc123", return_reason=True)
        assert is_suspicious is True
        assert "virtual_env" in reason

    def test_return_reason_too_short(self, validator):
        """return_reason=True for too-short SN returns 'too_short'."""
        is_suspicious, reason = validator.is_sn_suspicious("AB", return_reason=True)
        assert is_suspicious is True
        assert reason == "too_short"

    def test_return_reason_all_same_char(self, validator):
        """return_reason=True for same char returns 'all_same_char'."""
        is_suspicious, reason = validator.is_sn_suspicious("AAAA", return_reason=True)
        assert is_suspicious is True
        assert reason == "all_same_char"

    def test_return_reason_valid(self, validator):
        """return_reason=True for valid SN returns (False, None)."""
        is_suspicious, reason = validator.is_sn_suspicious("CND9876XYZ", return_reason=True)
        assert is_suspicious is False
        assert reason is None

    def test_return_reason_overused(self, validator):
        """return_reason=True for overused SN returns 'overused'."""
        exclude = {"OVERUSED123"}
        is_suspicious, reason = validator.is_sn_suspicious(
            "OVERUSED123", sns_to_exclude_due_to_overuse=exclude, return_reason=True
        )
        assert is_suspicious is True
        assert reason == "overused"


# =============================================================================
# Quality Evaluation
# =============================================================================


class TestEvaluateSNQuality:
    """Test serial number quality scoring."""

    @pytest.fixture
    def validator(self):
        return EnhancedSNValidator()

    # -- Immediate failures --

    def test_nan_returns_zero(self, validator):
        """NaN value returns score 0.0."""
        score, is_migration, meta = validator.evaluate_sn_quality(np.nan)
        assert score == 0.0
        assert is_migration is False
        assert meta == {}

    def test_empty_string_returns_zero(self, validator):
        """Empty string returns score 0.0."""
        score, is_migration, meta = validator.evaluate_sn_quality("")
        assert score == 0.0
        assert is_migration is False

    def test_none_returns_zero(self, validator):
        """None returns score 0.0."""
        score, is_migration, meta = validator.evaluate_sn_quality(None)
        assert score == 0.0
        assert is_migration is False

    def test_placeholder_returns_zero(self, validator):
        """Placeholder SN returns score 0.0 with reason."""
        score, is_migration, meta = validator.evaluate_sn_quality("unknown")
        assert score == 0.0
        assert is_migration is False
        assert meta.get("reason") == "placeholder"

    def test_smbios_placeholder_returns_zero(self, validator):
        """SMBIOS placeholder returns score 0.0."""
        score, is_migration, meta = validator.evaluate_sn_quality("asset tag")
        assert score == 0.0
        assert meta.get("reason") == "placeholder"

    def test_virtual_env_returns_zero(self, validator):
        """Virtual environment SN returns score 0.0."""
        score, is_migration, meta = validator.evaluate_sn_quality("vmware-abc123")
        assert score == 0.0
        assert is_migration is False
        assert meta.get("reason") == "virtual_environment"
        assert meta.get("env_type") == "VMware VM"

    def test_too_short_returns_low_score(self, validator):
        """SN shorter than 4 chars returns 0.1."""
        score, is_migration, meta = validator.evaluate_sn_quality("AB1")
        assert score == pytest.approx(0.1)
        assert is_migration is False
        assert meta.get("reason") == "too_short"

    def test_repeating_returns_low_score(self, validator):
        """All-same-char SN returns 0.1."""
        score, is_migration, meta = validator.evaluate_sn_quality("AAAA")
        assert score == pytest.approx(0.1)
        assert meta.get("reason") == "repeating"

    def test_simple_sequence_returns_low_score(self, validator):
        """Simple numeric sequence returns 0.1."""
        score, is_migration, meta = validator.evaluate_sn_quality("12345")
        assert score == pytest.approx(0.1)
        assert meta.get("reason") == "simple_sequence"

    # -- Score reductions --

    def test_short_sn_4_5_chars_penalized(self, validator):
        """SN with 4-5 chars gets score * 0.7 penalty."""
        score, _, _ = validator.evaluate_sn_quality("AB12")
        # Base 1.0 * 0.7 (short) * entropy/vendor adjustments
        assert score < 1.0

    def test_consecutive_chars_penalized(self, validator):
        """Consecutive identical chars reduce score."""
        score, _, _ = validator.evaluate_sn_quality("XYZaaaa789012")
        assert score < 1.0

    # -- Good SN scoring --

    def test_good_sn_high_score(self, validator):
        """Good quality SN with no issues gets high score."""
        score, is_migration, meta = validator.evaluate_sn_quality("CND9876XYZ")
        assert score > 0.7
        assert is_migration is True

    def test_good_sn_with_vendor_detection(self, validator):
        """Valid Dell SN gets vendor boost."""
        score, is_migration, meta = validator.evaluate_sn_quality("J8HGF32")
        assert score > 0.7
        assert is_migration is True
        assert "vendor" in meta

    # -- Hostname count impact --

    def test_high_hostname_count_overused(self, validator):
        """SN with hostname_count > overuse_threshold gets severe penalty."""
        score, is_migration, _ = validator.evaluate_sn_quality(
            "CND9876XYZ", hostname_count=10
        )
        assert score < 0.5
        assert is_migration is False

    def test_migration_candidate(self, validator):
        """SN with moderate hostname count may be migration candidate."""
        # hostname_count=3 is > migration_threshold(2) but <= overuse_threshold(4)
        score, is_migration, _ = validator.evaluate_sn_quality(
            "CND9876XYZ", hostname_count=3
        )
        # Score reduced by 0.7, is_migration depends on quality
        assert score < 1.0
        # Good quality SN with moderate count should be migration candidate
        assert is_migration is True

    def test_low_hostname_count_passes(self, validator):
        """SN with low hostname count gets full score."""
        score, is_migration, _ = validator.evaluate_sn_quality(
            "CND9876XYZ", hostname_count=1
        )
        assert is_migration is True

    # -- Metadata contents --

    def test_metadata_contains_entropy(self, validator):
        """Metadata includes entropy value for non-trivial SNs."""
        _, _, meta = validator.evaluate_sn_quality("CND9876XYZ")
        assert "entropy" in meta

    def test_metadata_contains_vendor_info(self, validator):
        """Metadata includes vendor info when detected."""
        _, _, meta = validator.evaluate_sn_quality("J8HGF32")
        assert meta.get("vendor") == "Dell"

    # -- Score capped at 1.0 --

    def test_score_capped_at_one(self, validator):
        """Score with all boosts is capped at 1.0."""
        score, _, _ = validator.evaluate_sn_quality("J8HGF32", hostname_count=0)
        assert score <= 1.0


# =============================================================================
# Statistics
# =============================================================================


class TestStatistics:
    """Test validation statistics tracking."""

    def test_initial_stats(self):
        """Initial stats have zero counters."""
        v = EnhancedSNValidator()
        stats = v.get_stats()
        assert stats["total_validated"] == 0
        assert stats["accepted"] == 0

    def test_stats_after_validations(self):
        """Stats are updated after running validations."""
        v = EnhancedSNValidator()
        v.is_sn_suspicious("CND9876XYZ")  # accepted
        v.is_sn_suspicious("unknown")  # rejected placeholder
        v.is_sn_suspicious(np.nan)  # rejected null

        stats = v.get_stats()
        assert stats["total_validated"] == 3
        assert stats["accepted"] == 1
        assert stats["rejected_placeholder"] == 2  # unknown + nan
        assert "rejection_rate" in stats
        assert "acceptance_rate" in stats
        assert stats["acceptance_rate"] == pytest.approx(100 / 3)

    def test_stats_breakdown(self):
        """Stats breakdown contains percentage fields."""
        v = EnhancedSNValidator()
        v.is_sn_suspicious("CND9876XYZ")
        v.is_sn_suspicious("AB")  # too_short

        stats = v.get_stats()
        assert "breakdown" in stats
        assert "too_short" in stats["breakdown"]
        assert stats["breakdown"]["too_short"] == pytest.approx(50.0)

    def test_stats_no_validations_returns_raw(self):
        """Stats with no validations returns raw dict without percentages."""
        v = EnhancedSNValidator()
        stats = v.get_stats()
        assert "rejection_rate" not in stats
        assert "breakdown" not in stats

    def test_reset_stats(self):
        """reset_stats clears all counters."""
        v = EnhancedSNValidator()
        v.is_sn_suspicious("CND9876XYZ")
        v.is_sn_suspicious("unknown")

        v.reset_stats()
        stats = v.get_stats()
        assert stats["total_validated"] == 0
        assert stats["accepted"] == 0
        assert stats["rejected_placeholder"] == 0
        assert stats["vendor_detections"] == {}

    def test_vendor_detection_tracking(self):
        """Vendor detections are tracked in stats."""
        v = EnhancedSNValidator()
        v.is_sn_suspicious("J8HGF32")  # Dell

        stats = v.get_stats()
        assert stats["vendor_detections"].get("Dell", 0) >= 1

    def test_virtual_env_stat_tracking(self):
        """Virtual environment rejections are tracked."""
        v = EnhancedSNValidator()
        v.is_sn_suspicious("vmware-abc123")

        stats = v.get_stats()
        assert stats["rejected_virtual_env"] >= 1


# =============================================================================
# Vendor validation in is_sn_suspicious
# =============================================================================


class TestVendorValidationInSuspicious:
    """Test vendor format validation within is_sn_suspicious."""

    def test_vendor_validation_disabled_does_not_reject(self):
        """With vendor validation disabled, no vendor format rejection occurs."""
        v = EnhancedSNValidator(enable_vendor_validation=False)
        # A valid-looking SN that would pass other checks
        assert v.is_sn_suspicious("CND9876XYZ") is False

    def test_valid_vendor_format_accepted(self):
        """SN with valid vendor format passes."""
        v = EnhancedSNValidator(enable_vendor_validation=True)
        assert v.is_sn_suspicious("J8HGF32") is False


# =============================================================================
# are_sns_similar — the SN-conflict split's veto
# =============================================================================

# Every character substitution are_sns_similar() accepts as a misread rather
# than as two different devices. Changing this table changes which wrong merges
# the pipeline is able to split, so it is spelled out here in full (#995).
CONFUSABLE_PAIRS = [
    ("0", "O"), ("0", "Q"), ("O", "Q"),
    ("1", "I"), ("1", "L"), ("I", "L"),
    ("2", "Z"),
    ("5", "S"),
    ("6", "G"),
    ("8", "B"),
]

# The real over-merges the veto held together in the dev fleet — 7 of 7 firings,
# 0 typos, measured 2026-07-17 (#995). Each is two distinct machines whose
# serials came off one production line. Lower-cased exactly as
# _detect_and_fix_sn_conflicts hands them to are_sns_similar().
BATCH_SEQUENTIAL_PAIRS = [
    ("pf60ezlb", "pf60ezln", "CCHZRHNB0080 / CCHZRHNB0081 — two Lenovos"),
    ("6d0kf94", "8g0kf94", "USFML223899GM / USFML241509GM — two Dell Pro 13 Plus"),
    ("g2v29k4", "g5v29k4", "AUNB9000 / aunb9002 — MACs from different OUIs"),
    ("pw0jyyap", "pw0jyyaw", "USFESL213720GM / USFESL241587GM — two Lenovos"),
    ("2skjzj3", "crkjzj3", "nbblu206632 / nbblu206645 — two Dells"),
    ("23jqm94", "b5jqm94", "four USFDL* hostnames — several Dells"),
    ("mz014ma9", "mz014qa1", "ThinkCentre M70q Gen 5 vs M75q Gen 5 — different models"),
]


class TestAreSnsSimilarKeepsRealTyposMerged:
    """The veto must still fire for the thing it exists to catch.

    _detect_and_fix_sn_conflicts splits an asset that carries two serials. If
    two sources merely spell ONE device's serial differently — someone read the
    label and typed an O for a 0 — splitting it would invent a device that does
    not exist. That is the case are_sns_similar() is there to protect.
    """

    @pytest.mark.parametrize("c1,c2", CONFUSABLE_PAIRS)
    def test_confusable_substitution_reads_as_one_device(self, c1, c2):
        """A character a human can misread off a printed label is not a split."""
        assert are_sns_similar(f"5CG8123AB{c1}", f"5CG8123AB{c2}") is True

    @pytest.mark.parametrize("c1,c2", CONFUSABLE_PAIRS)
    def test_confusable_substitution_is_symmetric(self, c1, c2):
        """Confusability has no direction — which source reported first is not a fact."""
        assert are_sns_similar(f"5CG8123AB{c2}", f"5CG8123AB{c1}") is True

    def test_two_confusable_substitutions_read_as_one_device(self):
        """max_edit_distance=2 confusable substitutions still read as one typo'd serial."""
        assert are_sns_similar("SCG8123ABS", "5CG8123AB5") is True

    def test_identical_serials(self):
        """Identical serials are trivially one device."""
        assert are_sns_similar("PF4FXC5E", "PF4FXC5E") is True

    def test_case_insensitive(self):
        """Sources disagree on case; that is a format difference, not a device."""
        assert are_sns_similar("pf4fxc5e", "PF4FXC5E") is True

    def test_substring_still_reads_as_one_device(self):
        """The substring rule is untouched by #995 — a decorated serial is one device."""
        assert are_sns_similar("ABC12345", "XABC12345Y") is True


class TestAreSnsSimilarSplitsBatchSequentialSerials:
    """Hardware bought in a batch has sequential serials, and is many devices.

    This is the #995 bug. A uniform edit distance read `5CG8123ABC` and
    `5CG8123ABD` as one device, so when two such machines were merged — via a
    shared hostname or a virtual MAC, both documented failure modes here — the
    split that should have caught it silently refused to run, permanently.
    """

    @pytest.mark.parametrize("sn1,sn2,reality", BATCH_SEQUENTIAL_PAIRS)
    def test_measured_over_merge_is_not_one_device(self, sn1, sn2, reality):
        """Each of the 7 measured false-positive vetoes must now allow the split."""
        assert are_sns_similar(sn1, sn2) is False, (
            f"{sn1} / {sn2} read as one device, but they are {reality}. "
            "_detect_and_fix_sn_conflicts will refuse to split this wrong merge."
        )

    def test_sequential_dell_service_tags_are_two_devices(self):
        """The next unit off the line differs by one character. That is not a typo."""
        assert are_sns_similar("5CG8123ABC", "5CG8123ABD") is False

    def test_non_confusable_substitution_does_not_veto(self):
        """C/D is adjacency, not confusability — no one misreads a C as a D."""
        assert are_sns_similar("CNU4201ABC", "CNU4201ABD") is False

    def test_one_confusable_and_one_not_does_not_veto(self):
        """Every differing character must qualify, not just one of them."""
        # S/5 is confusable; S/3 is not.
        assert are_sns_similar("5CG812SABS", "5CG8123AB5") is False

    def test_more_confusable_substitutions_than_max_edit_distance(self):
        """The distance cap still binds — 3 confusable substitutions is not a typo."""
        assert are_sns_similar("SCG8I23ABS", "5CG8123AB5") is False

    def test_insertion_is_not_a_substitution(self):
        """A length change is the substring rule's business, not the typo rule's."""
        assert are_sns_similar("PF4FCXC5E", "PF4FXC5E") is False

    def test_different_vendors_are_two_devices(self):
        """Unrelated serials were never similar and still are not."""
        assert are_sns_similar("5CG8123ABC", "CNU4201XYZ") is False


class TestAreSnsSimilarGuards:
    """The guards that predate #995 and are deliberately kept."""

    def test_too_short_to_judge(self):
        """Under 4 chars there is not enough serial to reason about."""
        assert are_sns_similar("AB", "AC") is False

    def test_one_too_short(self):
        """A short serial cannot vouch for a long one, substring or not."""
        assert are_sns_similar("AB", "ABCDEF") is False

    def test_empty(self):
        """An absent serial says nothing about identity."""
        assert are_sns_similar("", "PF4FXC5E") is False

    def test_none(self):
        """An absent serial says nothing about identity."""
        assert are_sns_similar(None, "PF4FXC5E") is False

    def test_whitespace_stripped(self):
        """Surrounding whitespace is a source artefact, not a difference."""
        assert are_sns_similar(" PF4FXC5E ", "PF4FXC5E") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
