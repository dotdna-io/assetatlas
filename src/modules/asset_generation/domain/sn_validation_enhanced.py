# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Enhanced Serial Number Validation

This module provides improved serial number validation with:
- Vendor-specific format validation
- Manufacturing date extraction
- Virtual environment detection
- Entropy-based pattern detection
- All original validation features

This is a research/comparison module and is NOT integrated into the
production pipeline yet. Use for benchmarking and A/B testing.

Author: AssetAtlas Development Team
Date: 2025-10-31
Status: Experimental
"""

import logging
import re
import math
from collections import Counter
from typing import Any, Dict, Set, Tuple, Optional
from functools import lru_cache

import pandas as pd

from src.modules.asset_generation.domain.vendor_validators import VendorDetector

logger = logging.getLogger(__name__)


def are_sns_similar(sn1: Optional[str], sn2: Optional[str], max_edit_distance: int = 2) -> bool:
    """Check if two serial numbers likely refer to the same device.

    Returns True if:
    - They are identical (case-insensitive)
    - One is a substring of the other (handles format differences)
    - They differ in at most max_edit_distance positions and every one of those
      is a visually-confusable substitution (handles OCR/manual-entry typos)
    Both SNs must be >= 4 chars to be considered.

    Only a *confusable* substitution reads as a typo. Any other character-for-
    character delta is the signature of hardware bought in a batch — sequential
    serials off one production line — and treating those as one device makes a
    wrong merge impossible to split, which is the opposite of what the caller
    (_detect_and_fix_sn_conflicts) wants (#995).
    """
    if not sn1 or not sn2:
        return False

    s1 = sn1.strip().upper()
    s2 = sn2.strip().upper()

    if s1 == s2:
        return True

    # Both must be meaningful length
    if len(s1) < 4 or len(s2) < 4:
        return False

    # Substring check
    if s1 in s2 or s2 in s1:
        return True

    # Typo check — substitutions only, so an inserted or deleted character rules
    # it out. A serial one source spells longer or shorter than another is the
    # substring check's job, above; requiring substitutions alone means the two
    # must be the same length and align position-by-position.
    if len(s1) != len(s2):
        return False

    diffs = [(c1, c2) for c1, c2 in zip(s1, s2) if c1 != c2]
    if len(diffs) > max_edit_distance:
        return False

    return all(_are_chars_confusable(c1, c2) for c1, c2 in diffs)


# Characters a person or an OCR pass can genuinely misread for one another off a
# printed serial label. Every pair *within* a group is confusable — the grouping
# is not transitive shorthand, each intra-group pair holds on its own:
#   0/O/Q  round glyphs telling each other apart on one small feature each: Q is
#          an O plus a tail, and 0 an O plus a slash or a narrower bowl — and a
#          worn label or a font without the slashed zero loses exactly those
#   1/I/L  bare vertical strokes. are_sns_similar() upper-cases, so a source's
#          lowercase "l" arrives as "L" and is then indistinguishable from "I" —
#          the canonical confusion, and the reason Base58/Crockford drop these
#   2/Z, 5/S, 6/G, 8/B  the classic digit/letter lookalikes; many vendors leave
#          these characters out of serial alphabets for this very reason
# Deliberately absent: substitutions that are merely *adjacent* rather than
# confusable. C/D, 2/5, B/N, P/W, D/G, 6/8 are what a batch of sequentially
# serialled hardware looks like, not what a misread label looks like (#995).
_CONFUSABLE_GROUPS = (
    frozenset("0OQ"),
    frozenset("1IL"),
    frozenset("2Z"),
    frozenset("5S"),
    frozenset("6G"),
    frozenset("8B"),
)


def _are_chars_confusable(c1: str, c2: str) -> bool:
    """Check whether two characters are visually confusable (symmetric)."""
    return any(c1 in group and c2 in group for group in _CONFUSABLE_GROUPS)


try:
    from Levenshtein import distance as _levenshtein_distance
except ImportError:
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """Compute Levenshtein edit distance between two strings (pure-Python fallback)."""
        if len(s1) < len(s2):
            return _levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)

        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                cost = 0 if c1 == c2 else 1
                curr_row.append(min(
                    curr_row[j] + 1,       # insert
                    prev_row[j + 1] + 1,   # delete
                    prev_row[j] + cost     # substitute
                ))
            prev_row = curr_row
        return prev_row[-1]


class EnhancedSNValidator:
    """
    Enhanced serial number validator with vendor-specific validation,
    entropy analysis, and virtual environment detection.

    Improvements over base AssetValidator:
    1. Vendor-specific format validation (Dell, HP, Apple, Cisco, Lenovo)
    2. Manufacturing date extraction (Apple)
    3. Virtual environment SN detection (VMware, AWS, etc.)
    4. Entropy-based pattern detection (low randomness = suspicious)
    5. SMBIOS placeholder detection
    6. All original validation features preserved
    """

    def __init__(
        self,
        sn_overuse_threshold: int = 4,
        sn_migration_threshold: int = 2,
        consecutive_char_length_threshold: int = 4,
        entropy_threshold: float = 2.0,
        enable_vendor_validation: bool = True,
    ):
        """
        Initialize enhanced validator.

        Args:
            sn_overuse_threshold: Number of hostnames before SN considered overused
            sn_migration_threshold: Threshold for potential device migration
            consecutive_char_length_threshold: Length for consecutive char detection
            entropy_threshold: Minimum Shannon entropy for valid SN (0-8 scale)
            enable_vendor_validation: Whether to perform vendor-specific validation
        """
        # Common placeholder serial numbers (from original validator)
        self.common_sn_placeholders = frozenset({
            "unknown", "none", "n/a", "", "na", "null",
            "to be filled by o.e.m.", "system serial number", "default string",
            "00000000", "12345678", "123456789", "0", "not available",
            "oem", "o.e.m", "not specified", "chassis s/n", "no serial number",
            "serialnumber", "systemname", "virtual chassi", "vmware",
            "серійний номер відсутній",  # Ukrainian
            "no disponible",  # Spanish
            "nicht verfügbar",  # German
            # RESEARCH FINDING: Common vendor placeholders
            "0123456789",  # Supermicro common default
            "1111111",  # Repeating digits
            "11111111",
            "111111111",
            "2222222",
            "22222222",
            "222222222",
            "3333333",
            "33333333",
            "333333333",
            "4444444",
            "44444444",
            "444444444",
            "5555555",
            "55555555",
            "555555555",
            "6666666",
            "66666666",
            "666666666",
            "7777777",
            "77777777",
            "777777777",
            "8888888",
            "88888888",
            "888888888",
            "9999999",
            "99999999",
            "999999999",
        })

        # ENHANCEMENT: OEM/BIOS placeholder patterns (regex-based)
        # Catches manufacturer-default SNs regardless of spacing/casing.
        # e.g., "Defaultstring", "ToBeFilledByO.E.M.", "NotSpecified"
        self._oem_placeholder_patterns = [
            re.compile(r'^default\s*string$', re.IGNORECASE),
            re.compile(r'^to\s*be\s*filled\s*by\s*o\.?e\.?m\.?$', re.IGNORECASE),
            re.compile(r'^not\s*specified$', re.IGNORECASE),
            re.compile(r'^system\s*serial\s*number$', re.IGNORECASE),
            re.compile(r'^chassis\s*serial\s*number$', re.IGNORECASE),
            re.compile(r'^base\s*board\s*serial\s*number$', re.IGNORECASE),
            re.compile(r'^no\s*serial\s*number$', re.IGNORECASE),
            re.compile(r'^system\s*product\s*name$', re.IGNORECASE),
            re.compile(r'^system\s*name$', re.IGNORECASE),
            re.compile(r'^asset\s*tag$', re.IGNORECASE),
        ]

        # ENHANCEMENT: SMBIOS/BIOS placeholders
        self.smbios_placeholders = frozenset({
            "asset tag",
            "base board serial number",
            "system sku number",
            "mb-1234567890",  # Generic motherboard SNs
            "chassis serial number",
            "system product name",
            "baseboard serial number",
        })

        # ENHANCEMENT: Virtual environment serial number patterns
        self.virtual_sn_patterns = {
            r'^vmware-.*': 'VMware VM',
            r'^vmware\s+virtual\s+platform': 'VMware VM',
            r'^ec2-.*': 'AWS EC2',
            r'^i-[0-9a-f]{8,17}$': 'AWS Instance ID',
            # UUID v4 pattern (commonly used in VMs)
            r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$': 'UUID v4 (likely VM)',
            # RESEARCH FINDING: Docker container IDs (12-char hex)
            r'^[0-9a-f]{12}$': 'Docker Container ID',
            # RESEARCH FINDING: Hyper-V and Xen patterns
            r'^microsoft\s*:\s*hyper-v\s*:\s*[0-9a-f-]+$': 'Hyper-V VM',
            r'^xen-[0-9a-f-]+$': 'Xen VM',
            # RESEARCH FINDING: KVM and VirtualBox
            r'^kvm-[0-9a-f-]+$': 'KVM VM',
            r'^vbox-[0-9a-f-]+$': 'VirtualBox VM',
            # RESEARCH FINDING: Hyper-V BIOS UUID (numeric groups: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XX)
            r'^\d{4}(-\d{4}){5}-\d{2}$': 'Hyper-V BIOS UUID',
            # Generic VM markers
            r'^virtual\s+machine$': 'Generic VM',
            r'^none$': 'Placeholder (common in Hyper-V)',
        }

        # Configuration
        self.sn_overuse_threshold = sn_overuse_threshold
        self.sn_migration_threshold = sn_migration_threshold
        self.consecutive_char_length_threshold = consecutive_char_length_threshold
        self.entropy_threshold = entropy_threshold
        self.enable_vendor_validation = enable_vendor_validation

        # Pre-compile regex patterns for performance
        self._consecutive_char_pattern = re.compile(
            r'(.)\1{' + str(consecutive_char_length_threshold - 1) + r'}'
        )
        self._simple_sequences = frozenset({"1234", "12345", "123456", "1234567", "12345678"})

        # ENHANCEMENT: Vendor detector for format validation
        if enable_vendor_validation:
            self.vendor_detector = VendorDetector()
        else:
            self.vendor_detector = None

        # Statistics tracking
        self.stats = {
            'total_validated': 0,
            'rejected_placeholder': 0,
            'rejected_too_short': 0,
            'rejected_repeating': 0,
            'rejected_sequential': 0,
            'rejected_overuse': 0,
            'rejected_virtual_env': 0,
            'rejected_low_entropy': 0,
            'rejected_vendor_format': 0,
            'vendor_detections': {},
            'accepted': 0,
        }

        logger.info(
            f"EnhancedSNValidator initialized with "
            f"vendor_validation={enable_vendor_validation}, "
            f"entropy_threshold={entropy_threshold}"
        )

    # =========================================================================
    # ENHANCEMENT: Entropy Analysis
    # =========================================================================

    @staticmethod
    def calculate_entropy(s: str) -> float:
        """
        Calculate Shannon entropy of a string.

        Shannon entropy measures randomness/information content.
        Low entropy indicates repeating patterns or low character variety.

        Args:
            s: String to analyze

        Returns:
            Entropy score (0-8 typical for serial numbers, higher = more random)
            - 0.0: All same character (e.g., "AAAA")
            - 1.0-2.0: Low variety (e.g., "ABC-123-ABC-123")
            - 2.5-4.0: Medium variety (typical valid SNs)
            - 4.0+: High variety

        Examples:
            "AAAAAA" → 0.0
            "ABC-123-ABC-123" → ~2.5
            "J8HGF32" → ~2.8
            "C02ABCDEFGH1" → ~3.1
        """
        if not s or len(s) < 2:
            return 0.0

        # Normalize to lowercase for consistency
        s = s.lower()

        # Count character frequencies
        counter = Counter(s)
        length = len(s)

        # Calculate Shannon entropy: -Σ(p(x) * log2(p(x)))
        entropy = 0.0
        for count in counter.values():
            p = count / length
            if p > 0:
                entropy -= p * math.log2(p)

        return entropy

    def is_low_entropy(self, sn: str, threshold: Optional[float] = None) -> bool:
        """
        Check if serial number has suspiciously low entropy.

        Args:
            sn: Serial number to check
            threshold: Entropy threshold (uses instance default if None)

        Returns:
            True if entropy is suspiciously low
        """
        if threshold is None:
            threshold = self.entropy_threshold

        if not sn or len(sn) < 6:
            # Short SNs lack sufficient characters for meaningful entropy analysis.
            # A 5-char SN with all unique chars has max entropy of ~2.3 bits,
            # which is below most useful thresholds anyway.
            return False

        entropy = self.calculate_entropy(sn)

        return entropy < threshold

    # =========================================================================
    # ENHANCEMENT: Virtual Environment Detection
    # =========================================================================

    def is_virtual_environment_sn(self, sn: str) -> Tuple[bool, Optional[str]]:
        """
        Check if serial number indicates a virtual environment.

        Virtual machines, cloud instances, and containers often have:
        - Predictable UUID patterns
        - Vendor-specific prefixes (VMware-, ec2-, etc.)
        - Generic placeholders

        Args:
            sn: Serial number to check

        Returns:
            Tuple of (is_virtual, environment_type)
            - is_virtual: True if SN indicates virtual environment
            - environment_type: Description of environment type
        """
        if not sn or not isinstance(sn, str):
            return (False, None)

        sn_lower = sn.lower().strip()

        # Check against known virtual environment patterns
        for pattern, env_type in self.virtual_sn_patterns.items():
            if re.match(pattern, sn_lower):
                return (True, env_type)

        return (False, None)

    # =========================================================================
    # ENHANCEMENT: Vendor-Specific Validation
    # =========================================================================

    def validate_vendor_format(self, sn: str) -> Dict[str, Any]:
        """
        Perform vendor-specific format validation and metadata extraction.

        Args:
            sn: Serial number to validate

        Returns:
            Dictionary with validation results:
            {
                'is_valid': bool,
                'vendor': str or None,
                'format': str,
                'metadata': dict (may include manufacture_year, etc.)
                'confidence': float (0.0-1.0)
            }
        """
        if not self.enable_vendor_validation or not self.vendor_detector:
            return {
                'is_valid': None,  # Not checked
                'vendor': None,
                'format': 'unknown',
                'metadata': {},
                'confidence': 0.0
            }

        # Try to detect vendor
        detection = self.vendor_detector.detect_vendor(sn)

        if not detection:
            # No vendor detected - doesn't mean invalid, just unknown
            return {
                'is_valid': None,  # Unknown vendor
                'vendor': None,
                'format': 'unknown',
                'metadata': {},
                'confidence': 0.0
            }

        vendor_name, validator = detection

        # Validate format
        is_valid = validator.validate_format(sn)

        # Extract metadata if valid
        metadata = {}
        if is_valid:
            metadata = validator.extract_metadata(sn)

        # Calculate confidence based on validation results
        confidence = 0.0
        if is_valid:
            # Higher confidence if we extracted metadata
            confidence = 0.9 if metadata else 0.7

        return {
            'is_valid': is_valid,
            'vendor': vendor_name,
            'format': metadata.get('format', 'detected'),
            'metadata': metadata,
            'confidence': confidence
        }

    # =========================================================================
    # Original Validation Methods (preserved from AssetValidator)
    # =========================================================================

    def has_sequential_numbers(self, s: str, length: int = None) -> bool:
        """Check for sequential numbers (e.g., "123456")."""
        if length is None:
            length = self.consecutive_char_length_threshold

        if len(s) < length:
            return False

        for i in range(len(s) - length + 1):
            substring = s[i:i + length]
            if substring.isdigit():
                # Check if digits are sequential
                is_sequential = all(
                    int(substring[k]) == int(substring[0]) + k
                    for k in range(1, length)
                )
                if is_sequential:
                    return True

        return False

    def has_sequential_letters(self, s: str, length: int = None) -> bool:
        """Check for sequential letters (e.g., "abcdef")."""
        if length is None:
            length = self.consecutive_char_length_threshold

        s_lower = s.lower()
        if len(s_lower) < length:
            return False

        for i in range(len(s_lower) - length + 1):
            substring = s_lower[i:i + length]
            if substring.isalpha():
                # Check if letters are sequential
                is_sequential = all(
                    ord(substring[k]) == ord(substring[0]) + k
                    for k in range(1, length)
                )
                if is_sequential:
                    return True

        return False

    # =========================================================================
    # ENHANCED: Main Validation Method
    # =========================================================================

    def is_sn_suspicious(
        self,
        sn_val: Any,
        sns_to_exclude_due_to_overuse: Set[str] = None,
        return_reason: bool = False
    ) -> bool | Tuple[bool, Optional[str]]:
        """
        Check if a serial number is suspicious based on enhanced heuristics.

        ENHANCEMENTS over original:
        1. Virtual environment detection
        2. Entropy analysis
        3. Vendor-specific format validation
        4. SMBIOS placeholder detection
        5. Detailed rejection reason tracking

        Args:
            sn_val: Serial number value to check
            sns_to_exclude_due_to_overuse: Set of known overused SNs
            return_reason: If True, return (is_suspicious, reason) tuple

        Returns:
            If return_reason=False: True if suspicious, False if likely legitimate
            If return_reason=True: (is_suspicious, reason_string)
        """
        self.stats['total_validated'] += 1

        if sns_to_exclude_due_to_overuse is None:
            sns_to_exclude_due_to_overuse = set()

        # Check for null/empty
        if pd.isna(sn_val):
            self.stats['rejected_placeholder'] += 1
            return (True, 'null_or_empty') if return_reason else True

        sn_str = str(sn_val).strip()
        if not sn_str:
            self.stats['rejected_placeholder'] += 1
            return (True, 'empty_string') if return_reason else True

        sn_lower = sn_str.lower()

        # ORIGINAL: Common placeholders
        if sn_lower in self.common_sn_placeholders:
            self.stats['rejected_placeholder'] += 1
            return (True, 'common_placeholder') if return_reason else True

        # ENHANCEMENT: SMBIOS placeholders
        if sn_lower in self.smbios_placeholders:
            self.stats['rejected_placeholder'] += 1
            return (True, 'smbios_placeholder') if return_reason else True

        # ENHANCEMENT: OEM/BIOS placeholder patterns (regex-based)
        for pattern in self._oem_placeholder_patterns:
            if pattern.match(sn_str):
                self.stats['rejected_placeholder'] += 1
                return (True, 'oem_placeholder') if return_reason else True

        # ENHANCEMENT: Virtual environment detection
        is_virtual, env_type = self.is_virtual_environment_sn(sn_str)
        if is_virtual:
            self.stats['rejected_virtual_env'] += 1
            logger.debug(f"Virtual environment SN detected: {sn_str} ({env_type})")
            return (True, f'virtual_env_{env_type}') if return_reason else True

        # ORIGINAL: Too short
        if len(sn_lower) < 4:
            self.stats['rejected_too_short'] += 1
            return (True, 'too_short') if return_reason else True

        # ORIGINAL: All same character
        if len(sn_lower) > 1 and len(set(sn_lower)) == 1:
            self.stats['rejected_repeating'] += 1
            return (True, 'all_same_char') if return_reason else True

        # ORIGINAL: Simple sequences
        if sn_lower.isdigit() and sn_lower in self._simple_sequences:
            self.stats['rejected_sequential'] += 1
            return (True, 'simple_sequence') if return_reason else True

        # ORIGINAL: Consecutive characters pattern
        if self._consecutive_char_pattern.search(sn_str):
            self.stats['rejected_repeating'] += 1
            return (True, 'consecutive_chars') if return_reason else True

        # ORIGINAL: Sequential numbers
        if self.has_sequential_numbers(sn_str):
            self.stats['rejected_sequential'] += 1
            return (True, 'sequential_numbers') if return_reason else True

        # ORIGINAL: Sequential letters
        if self.has_sequential_letters(sn_str):
            self.stats['rejected_sequential'] += 1
            return (True, 'sequential_letters') if return_reason else True

        # ENHANCEMENT: Low entropy check (repeated patterns)
        if self.is_low_entropy(sn_str):
            entropy = self.calculate_entropy(sn_str)
            self.stats['rejected_low_entropy'] += 1
            logger.debug(f"Low entropy SN detected: {sn_str} (entropy={entropy:.2f})")
            return (True, f'low_entropy_{entropy:.2f}') if return_reason else True

        # ENHANCEMENT: Vendor-specific format validation
        if self.enable_vendor_validation:
            vendor_result = self.validate_vendor_format(sn_str)

            if vendor_result['vendor']:
                # Track vendor detection
                vendor = vendor_result['vendor']
                self.stats['vendor_detections'][vendor] = \
                    self.stats['vendor_detections'].get(vendor, 0) + 1

                # If we detected a vendor but format is invalid, reject
                if vendor_result['is_valid'] is False:
                    self.stats['rejected_vendor_format'] += 1
                    logger.debug(
                        f"Invalid {vendor} format detected: {sn_str}"
                    )
                    return (True, f'invalid_{vendor}_format') if return_reason else True

        # ORIGINAL: Overused SN
        if sn_str in sns_to_exclude_due_to_overuse:
            self.stats['rejected_overuse'] += 1
            return (True, 'overused') if return_reason else True

        # Passed all checks - likely legitimate
        self.stats['accepted'] += 1
        return (False, None) if return_reason else False

    def evaluate_sn_quality(
        self,
        sn_val: Any,
        hostname_count: int = 0
    ) -> Tuple[float, bool, Dict[str, Any]]:
        """
        Evaluate the quality/legitimacy of a serial number with enhanced scoring.

        ENHANCEMENTS over original:
        1. Vendor detection adds confidence boost
        2. Manufacturing date extraction provides additional metadata
        3. Entropy score influences quality
        4. Returns detailed metadata dictionary

        Args:
            sn_val: Serial number value
            hostname_count: Number of hostnames associated with this SN

        Returns:
            Tuple of (quality_score, is_migration_candidate, metadata)
            - quality_score: 0-1, where 1 is highest quality
            - is_migration_candidate: True if might be legitimate migration
            - metadata: Additional information (vendor, manufacture date, etc.)
        """
        if pd.isna(sn_val) or not str(sn_val).strip():
            return (0.0, False, {})

        sn_str = str(sn_val).strip()
        sn_lower = sn_str.lower()

        metadata = {}

        # Start with perfect score
        score = 1.0

        # Check against placeholders
        if sn_lower in self.common_sn_placeholders or sn_lower in self.smbios_placeholders:
            return (0.0, False, {'reason': 'placeholder'})

        # Check OEM placeholder patterns
        for pattern in self._oem_placeholder_patterns:
            if pattern.match(sn_str):
                return (0.0, False, {'reason': 'oem_placeholder'})

        # Check virtual environment
        is_virtual, env_type = self.is_virtual_environment_sn(sn_str)
        if is_virtual:
            return (0.0, False, {'reason': 'virtual_environment', 'env_type': env_type})

        # Length check
        if len(sn_lower) < 4:
            return (0.1, False, {'reason': 'too_short'})
        elif len(sn_lower) < 6:
            score *= 0.7

        # Repeating character check
        if len(sn_lower) > 1 and len(set(sn_lower)) == 1:
            return (0.1, False, {'reason': 'repeating'})

        # Simple sequences
        if sn_lower.isdigit() and sn_lower in self._simple_sequences:
            return (0.1, False, {'reason': 'simple_sequence'})

        # Pattern checks (reduce score if found)
        if self._consecutive_char_pattern.search(sn_str):
            score *= 0.3

        if self.has_sequential_numbers(sn_str):
            score *= 0.4

        if self.has_sequential_letters(sn_str):
            score *= 0.4

        # ENHANCEMENT: Entropy check
        entropy = self.calculate_entropy(sn_str)
        metadata['entropy'] = entropy

        if entropy < 1.5:  # Very low
            score *= 0.2
        elif entropy < 2.0:  # Low
            score *= 0.5
        elif entropy > 3.0:  # High (good!)
            score *= 1.1  # Slight boost
        score = min(1.0, score)  # Cap after entropy adjustment

        # ENHANCEMENT: Vendor validation boost
        if self.enable_vendor_validation:
            vendor_result = self.validate_vendor_format(sn_str)

            if vendor_result['vendor']:
                metadata['vendor'] = vendor_result['vendor']
                metadata['vendor_format'] = vendor_result['format']

                if vendor_result['is_valid']:
                    # Valid vendor format = confidence boost
                    score *= 1.2
                    score = min(1.0, score)  # Cap after vendor boost

                    # Add extracted metadata
                    if vendor_result['metadata']:
                        metadata['vendor_metadata'] = vendor_result['metadata']

                        # Manufacturing date is especially valuable
                        if 'manufacture_year' in vendor_result['metadata']:
                            metadata['manufacture_year'] = \
                                vendor_result['metadata']['manufacture_year']
                            metadata['manufacture_week'] = \
                                vendor_result['metadata'].get('manufacture_week')
                            score *= 1.1  # Additional boost for date extraction
                            score = min(1.0, score)  # Cap after date boost

                elif vendor_result['is_valid'] is False:
                    # Invalid vendor format = penalty
                    score *= 0.3
                    metadata['vendor_invalid'] = True

        # Cap score at 1.0
        score = min(score, 1.0)

        # Evaluate based on hostname associations
        if hostname_count > self.sn_overuse_threshold:
            # Definitely overused
            return (score * 0.1, False, metadata)
        elif hostname_count > self.sn_migration_threshold:
            # Might be migration if SN quality is good
            is_migration = score > 0.6
            return (score * 0.7, is_migration, metadata)
        else:
            # Good SN with appropriate hostname count
            return (score, True, metadata)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get validation statistics.

        Returns:
            Dictionary with detailed statistics on validation results
        """
        total = self.stats['total_validated']

        if total == 0:
            return self.stats

        # Calculate percentages
        return {
            **self.stats,
            'rejection_rate': (total - self.stats['accepted']) / total * 100,
            'acceptance_rate': self.stats['accepted'] / total * 100,
            'breakdown': {
                'placeholder': self.stats['rejected_placeholder'] / total * 100,
                'too_short': self.stats['rejected_too_short'] / total * 100,
                'repeating': self.stats['rejected_repeating'] / total * 100,
                'sequential': self.stats['rejected_sequential'] / total * 100,
                'overuse': self.stats['rejected_overuse'] / total * 100,
                'virtual_env': self.stats['rejected_virtual_env'] / total * 100,
                'low_entropy': self.stats['rejected_low_entropy'] / total * 100,
                'vendor_format': self.stats['rejected_vendor_format'] / total * 100,
            }
        }

    def reset_stats(self):
        """Reset validation statistics."""
        for key in self.stats:
            if isinstance(self.stats[key], dict):
                self.stats[key] = {}
            else:
                self.stats[key] = 0
