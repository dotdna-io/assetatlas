# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from src.modules.asset_generation.domain.sn_validation_enhanced import are_sns_similar, _levenshtein_distance


class TestLevenshteinDistance:
    def test_identical(self):
        assert _levenshtein_distance("ABC", "ABC") == 0

    def test_one_substitution(self):
        assert _levenshtein_distance("ABC", "ABD") == 1

    def test_one_insertion(self):
        assert _levenshtein_distance("ABC", "ABCD") == 1

    def test_one_deletion(self):
        assert _levenshtein_distance("ABCD", "ABC") == 1

    def test_completely_different(self):
        assert _levenshtein_distance("ABC", "XYZ") == 3


class TestAreSnsSimilar:
    # Identical
    def test_identical(self):
        assert are_sns_similar("PF4FXC5E", "PF4FXC5E") is True

    def test_case_insensitive(self):
        assert are_sns_similar("pf4fxc5e", "PF4FXC5E") is True

    # Confusable substitutions, up to max_edit_distance of them
    def test_one_substitution(self):
        assert are_sns_similar("MZ00E348", "MZ00E34B") is True  # 1 sub, 8<->B

    def test_two_substitutions(self):
        assert are_sns_similar("MZ005348", "MZ00S34B") is True  # 2 subs, 5<->S and 8<->B

    # Non-confusable substitutions are batch-sequential serials, not typos (#995)
    def test_non_confusable_substitution(self):
        assert are_sns_similar("MZ00E32Z", "MZ00E32B") is False  # Z<->B is not a misread

    def test_sequential_service_tags(self):
        assert are_sns_similar("5CG8123ABC", "5CG8123ABD") is False

    def test_more_confusable_subs_than_max_edit_distance(self):
        # 3 confusable subs (5<->S, 1<->I, 8<->B) — past max_edit_distance
        assert are_sns_similar("MZ005148", "MZ00SI4B") is False

    # Insertions/deletions are not substitutions (#995); length changes are the
    # substring rule's job, and these two are not substrings of each other.
    def test_one_deletion(self):
        assert are_sns_similar("PF4FCXC5E", "PF4FXC5E") is False  # 1 deletion

    # Substring
    def test_substring_match(self):
        assert are_sns_similar("ABC12345", "XABC12345Y") is True

    def test_short_substring(self):
        assert are_sns_similar("ABCDE", "XABCDE") is True

    # Completely different — should NOT match
    def test_different_sns(self):
        assert are_sns_similar("MJ0KN7ZB", "PF4F14MR") is False

    def test_very_different(self):
        assert are_sns_similar("5CG735775H", "L1HF3650190") is False

    def test_different_cisco(self):
        assert are_sns_similar("FD3423AN0139", "FCW2309G06R") is False

    # Too short
    def test_too_short(self):
        assert are_sns_similar("AB", "AC") is False

    def test_one_short(self):
        assert are_sns_similar("AB", "ABCDEF") is False

    # Edge cases
    def test_empty(self):
        assert are_sns_similar("", "PF4FXC5E") is False

    def test_none(self):
        assert are_sns_similar(None, "PF4FXC5E") is False

    def test_both_none(self):
        assert are_sns_similar(None, None) is False

    def test_whitespace_stripped(self):
        assert are_sns_similar(" PF4FXC5E ", "PF4FXC5E") is True
