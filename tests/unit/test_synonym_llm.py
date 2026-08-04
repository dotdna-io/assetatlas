# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/test_synonym_llm.py
import pytest
from unittest.mock import MagicMock, patch
from src.services.embedding.synonym_llm import LLMSynonymGenerator


class TestLLMSynonymGenerator:
    """LLM synonym generator asks the model for search terms per field."""

    def setup_method(self):
        self.mock_llm = MagicMock()
        self.generator = LLMSynonymGenerator(self.mock_llm)

    def test_generates_terms_for_field(self):
        self.mock_llm.is_available.return_value = True
        self.mock_llm.generate.return_value = (
            "antivirus, endpoint protection, security software, av status, "
            "virus protection, malware protection"
        )
        terms = self.generator.generate_search_terms(
            field_name="sentinelone_av",
            data_source="SentinelOne",
            description="antivirus software name",
            sample_values=["SentinelOne", "Windows Defender"],
        )
        assert len(terms) >= 3
        assert "antivirus" in terms

    def test_returns_empty_when_llm_unavailable(self):
        self.mock_llm.is_available.return_value = False
        terms = self.generator.generate_search_terms(
            field_name="sentinelone_av",
            data_source="SentinelOne",
        )
        assert terms == []

    def test_deduplicates_and_lowercases(self):
        self.mock_llm.is_available.return_value = True
        self.mock_llm.generate.return_value = "Antivirus, antivirus, AV, av"
        terms = self.generator.generate_search_terms(
            field_name="test_field",
            data_source="Test",
        )
        assert len(terms) == len(set(terms)), "Should not contain duplicates"
        assert all(t == t.lower() for t in terms)

    def test_filters_empty_and_short_terms(self):
        self.mock_llm.is_available.return_value = True
        self.mock_llm.generate.return_value = "a, , ab, valid term, ok"
        terms = self.generator.generate_search_terms(
            field_name="test_field",
            data_source="Test",
        )
        assert "a" not in terms
        assert "" not in terms
        assert "valid term" in terms

    def test_batch_generates_for_multiple_fields(self):
        self.mock_llm.is_available.return_value = True
        self.mock_llm.generate.side_effect = [
            "antivirus, endpoint protection",
            "encryption, disk encryption, bitlocker",
        ]
        fields = [
            {"field_name": "av", "data_source": "SentinelOne"},
            {"field_name": "isEncrypted", "data_source": "Intune"},
        ]
        result = self.generator.generate_batch(fields)
        assert "av" in result
        assert "isEncrypted" in result
        assert "antivirus" in result["av"]
        assert "encryption" in result["isEncrypted"]

    def test_llm_error_returns_empty_gracefully(self):
        self.mock_llm.is_available.return_value = True
        self.mock_llm.generate.side_effect = Exception("LLM timeout")
        terms = self.generator.generate_search_terms(
            field_name="test_field",
            data_source="Test",
        )
        assert terms == []
