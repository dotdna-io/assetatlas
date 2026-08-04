# SPDX-License-Identifier: AGPL-3.0-or-later
# src/services/embedding/synonym_llm.py
"""
Layer 2: LLM-generated search terms per field.

Asks the LLM: "What would an IT professional type to search for this field?"
Returns a list of search terms that get stored as direct term->field mappings.

Called once per field at registration time (pipeline Stage 22), not at query time.
"""

import logging
from typing import Optional

from .pipeline_llm import env_max_tokens

logger = logging.getLogger(__name__)

# Reasoning-safe output ceiling (see description_generator._DESC_MAX_TOKENS):
# Qwen3-style models spend hundreds of tokens "thinking" before emitting the
# short comma-separated term list, so a tiny cap returns empty content.
_SYN_MAX_TOKENS = env_max_tokens(4096)

_SEARCH_TERMS_PROMPT = """List 8-12 search terms that an IT professional would type to find this field in an asset explorer.

Field: {field_name}
Data source: {data_source}
{description_line}
{samples_line}

Output ONLY a comma-separated list of search terms. Include:
- Common synonyms and abbreviations
- How a non-technical person might describe it
- Related IT concepts

Example for a field 'isEncrypted' from Intune with values true/false:
encrypted, encryption, disk encryption, bitlocker, encryption status, is encrypted, drive encryption, full disk encryption, fde, encryption state"""


class LLMSynonymGenerator:
    """Generate search terms for fields using a local LLM."""

    def __init__(self, llm_client):
        """
        Args:
            llm_client: A pipeline LLM client (see pipeline_llm.py).
        """
        self.llm_client = llm_client

    def generate_search_terms(
        self,
        field_name: str,
        data_source: str,
        description: str = "",
        sample_values: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Generate search terms for a single field.

        Returns:
            List of lowercase, deduplicated search terms. Empty list on failure.
        """
        if not self.llm_client.is_available():
            return []

        description_line = f"Description: {description}" if description else ""
        samples_line = ""
        if sample_values:
            samples_line = f"Sample values: {', '.join(str(v) for v in sample_values[:5])}"

        prompt = _SEARCH_TERMS_PROMPT.format(
            field_name=field_name,
            data_source=data_source,
            description_line=description_line,
            samples_line=samples_line,
        )

        try:
            raw = self.llm_client.generate(prompt=prompt, max_tokens=_SYN_MAX_TOKENS)
            return self._parse_terms(raw)
        except Exception as e:
            logger.warning(f"LLM synonym generation failed for '{field_name}': {e}")
            return []

    def generate_batch(
        self,
        fields: list[dict],
    ) -> dict[str, list[str]]:
        """
        Generate search terms for multiple fields.

        Args:
            fields: List of dicts with keys: field_name, data_source,
                    and optionally description, sample_values.

        Returns:
            Dict mapping field_name -> list of search terms.
        """
        from .pipeline_llm import describe_client
        provider, model = describe_client(self.llm_client)
        logger.info(
            "Generating search terms for %d fields via %s (%s)",
            len(fields), provider, model,
        )
        result = {}
        for f in fields:
            terms = self.generate_search_terms(
                field_name=f["field_name"],
                data_source=f.get("data_source", "unknown"),
                description=f.get("description", ""),
                sample_values=f.get("sample_values"),
            )
            if terms:
                result[f["field_name"]] = terms
        return result

    @staticmethod
    def _parse_terms(raw: str) -> list[str]:
        """Parse comma-separated LLM output into clean, validated term list."""
        terms = []
        seen = set()
        for part in raw.split(","):
            term = part.strip().lower()
            # Remove bullet points, numbering, quotes
            term = term.lstrip("0123456789.-) ").strip("\"'")
            if not _is_valid_term(term) or term in seen:
                continue
            seen.add(term)
            terms.append(term)
        return terms


def _is_valid_term(term: str) -> bool:
    """Reject terms that are too short, too long, or look like sample values."""
    if len(term) < 2 or len(term) > 40:
        return False
    # Reject terms with dots (look like hostnames/domains: server01.corp.com)
    if "." in term and not term.startswith("e.g"):
        return False
    # Reject terms with colons (look like MAC addresses: aa:bb:cc)
    if ":" in term:
        return False
    # Reject terms that are mostly digits (look like IDs or sample values)
    alpha = sum(1 for c in term if c.isalpha())
    if alpha < len(term) * 0.4:
        return False
    return True
