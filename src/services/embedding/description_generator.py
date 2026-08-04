# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Description Generator - Generate semantic field descriptions using an LLM

Generates rich, synonym-aware descriptions for IT asset fields using the
operator's configured chat-provider LLM. These descriptions are used by the
embedding system for semantic search in the Explorer UI.

The generator uses very low temperature (0.05) for consistent,
deterministic field descriptions.
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from .pipeline_llm import LLMClientError, NullPipelineLLM, env_max_tokens

logger = logging.getLogger(__name__)

# LLM output ceiling for description generation. Sized for reasoning models
# (e.g. Qwen3) that spend several hundred tokens "thinking" before emitting the
# (still short, prompt-bounded) description — too small a cap truncates
# mid-reasoning and returns empty content. max_tokens is a CEILING, not a
# target: the system prompt keeps output to 1-2 sentences, so a generous cap
# doesn't lengthen it. Tunable per-deployment via EMBEDDING_LLM_MAX_TOKENS.
_DESC_MAX_TOKENS = env_max_tokens(4096)


# System prompt for field description generation
SYSTEM_PROMPT = """You are an IT asset management expert. Your task is to generate rich semantic descriptions for database fields that will be used for semantic search.

For each field, generate a description that:
1. Starts with the field name followed by common synonyms and alternative names
2. Explains what the field represents in IT asset management context
3. Includes relevant technical terms and variations that users might search for
4. Lists example values if applicable

Format: Start with the field name, then synonyms (comma-separated), then a period. Follow with 1-2 sentences of explanation.

Example for 'hostname':
hostname - computer name, machine name, device name, server name, workstation name, PC name, node name, system name, NetBIOS name, DNS short name. The unique name identifying a computer on a network, used for network communication and asset identification.

Be concise but comprehensive with synonyms. Focus on terms IT professionals would actually use."""


@dataclass
class GeneratedDescription:
    """Result of description generation."""
    field_name: str
    description: str
    success: bool
    error: Optional[str] = None


class DescriptionGenerator:
    """
    Generate semantic field descriptions using the operator's configured LLM.

    This service uses the operator's configured chat-provider LLM to generate
    rich descriptions that include synonyms and alternative names, optimized
    for semantic embedding search.

    Usage:
        generator = DescriptionGenerator()
        if generator.is_available():
            desc = generator.generate_description("hostname")
            print(desc.description)
    """

    def __init__(self, llm_client: Optional[Any] = None):
        """Initialize the description generator.

        Args:
            llm_client: A pipeline LLM client (``is_available``, ``generate``,
                ``close``). When ``None``, uses ``NullPipelineLLM`` so the
                generator reports unavailable rather than reaching a server.
        """
        self.llm_client = llm_client if llm_client is not None else NullPipelineLLM()

    def is_available(self) -> bool:
        """Check if the underlying LLM is available."""
        return self.llm_client.is_available()

    def wait_for_server(self, timeout: int = 300) -> bool:
        """Wait for the LLM server to become available, if the client supports it."""
        wait = getattr(self.llm_client, "wait_for_server", None)
        if wait is None:
            return self.llm_client.is_available()
        return wait(timeout)

    def generate_description(
        self,
        field_name: str,
        sample_values: Optional[List[str]] = None,
        context: Optional[str] = None
    ) -> GeneratedDescription:
        """
        Generate a semantic description for a field.

        Args:
            field_name: Name of the field (e.g., 'hostname', 'ip_address')
            sample_values: Optional list of sample values to help with context
            context: Optional additional context about the field

        Returns:
            GeneratedDescription with the result
        """
        prompt = self._build_prompt(field_name, sample_values, context)

        try:
            description = self.llm_client.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                max_tokens=_DESC_MAX_TOKENS
                # Uses config temperature (0.05) for consistent output
            )

            # Clean up the response
            description = self._clean_description(description, field_name)

            logger.info(f"Generated description for '{field_name}': {len(description)} chars")

            return GeneratedDescription(
                field_name=field_name,
                description=description,
                success=True
            )

        except LLMClientError as e:
            logger.error(f"Failed to generate description for '{field_name}': {e}")
            return GeneratedDescription(
                field_name=field_name,
                description="",
                success=False,
                error=str(e)
            )

    def generate_batch(
        self,
        fields: List[Dict[str, Any]]
    ) -> List[GeneratedDescription]:
        """
        Generate descriptions for multiple fields.

        Args:
            fields: List of dicts with 'field_name' and optionally
                   'sample_values' and 'context'

        Returns:
            List of GeneratedDescription results
        """
        results = []

        for i, field in enumerate(fields):
            field_name = field.get('field_name')
            if not field_name:
                continue

            logger.info(f"Generating description {i + 1}/{len(fields)}: {field_name}")

            result = self.generate_description(
                field_name=field_name,
                sample_values=field.get('sample_values'),
                context=field.get('context')
            )
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        logger.info(f"Batch complete: {success_count}/{len(results)} successful")

        return results

    def _build_prompt(
        self,
        field_name: str,
        sample_values: Optional[List[str]] = None,
        context: Optional[str] = None
    ) -> str:
        """Build the prompt for description generation."""
        prompt_parts = [
            f"Generate a semantic description for the IT asset field: '{field_name}'"
        ]

        if sample_values:
            # Limit to 5 samples to keep prompt concise
            samples = sample_values[:5]
            prompt_parts.append(f"\nSample values: {', '.join(samples)}")

        if context:
            prompt_parts.append(f"\nContext: {context}")

        prompt_parts.append("\nGenerate the description following the format shown in the examples.")

        return "\n".join(prompt_parts)

    def _clean_description(self, description: str, field_name: str) -> str:
        """
        Clean and validate the generated description.

        Ensures the description follows the expected format and
        starts with the field name.
        """
        # Remove any markdown formatting
        description = description.strip()
        description = description.replace("```", "")
        description = description.strip()

        # Remove quotes if the LLM wrapped it
        if description.startswith('"') and description.endswith('"'):
            description = description[1:-1]
        if description.startswith("'") and description.endswith("'"):
            description = description[1:-1]

        # Ensure it starts with the field name
        if not description.lower().startswith(field_name.lower()):
            description = f"{field_name} - {description}"

        return description

    def suggest_synonyms(self, field_name: str) -> List[str]:
        """
        Get synonym suggestions for a field name.

        Uses the LLM to generate a list of synonyms without
        the full description format.

        Args:
            field_name: Name of the field

        Returns:
            List of suggested synonyms
        """
        prompt = f"""List common synonyms and alternative names for the IT asset field '{field_name}'.
Only output the synonyms as a comma-separated list, nothing else.
Example: if field is 'hostname', output: computer name, machine name, device name, server name"""

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                max_tokens=_DESC_MAX_TOKENS
                # Uses config temperature (0.05) for consistent output
            )

            # Parse comma-separated list
            synonyms = [s.strip() for s in response.split(",")]
            synonyms = [s for s in synonyms if s and len(s) > 1]

            return synonyms

        except LLMClientError as e:
            logger.error(f"Failed to get synonyms for '{field_name}': {e}")
            return []

    def enhance_existing_description(
        self,
        field_name: str,
        current_description: str
    ) -> GeneratedDescription:
        """
        Enhance an existing description with more synonyms.

        Args:
            field_name: Name of the field
            current_description: Current description to enhance

        Returns:
            GeneratedDescription with enhanced version
        """
        prompt = f"""Enhance this IT asset field description by adding more relevant synonyms and alternative terms.

Field: {field_name}
Current description: {current_description}

Keep the same format (field name - synonyms. explanation) but add more relevant synonyms that IT professionals might use to search for this field. Keep it concise."""

        try:
            enhanced = self.llm_client.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                max_tokens=_DESC_MAX_TOKENS
                # Uses config temperature (0.05) for consistent output
            )

            enhanced = self._clean_description(enhanced, field_name)

            return GeneratedDescription(
                field_name=field_name,
                description=enhanced,
                success=True
            )

        except LLMClientError as e:
            logger.error(f"Failed to enhance description for '{field_name}': {e}")
            return GeneratedDescription(
                field_name=field_name,
                description=current_description,
                success=False,
                error=str(e)
            )

    def generate_batch_single_call(
        self,
        fields: list[dict],
    ) -> dict[str, str]:
        """
        Generate descriptions for multiple fields in a single LLM call.

        Args:
            fields: List of dicts with 'field_name' and optionally
                     'data_source', 'sample_values'.

        Returns:
            Dict mapping field_name -> description. Empty dict on failure.
        """
        if not fields:
            return {}

        field_lines = []
        for f in fields:
            line = f"- {f['field_name']}"
            if f.get('data_source'):
                line += f" (from {f['data_source']})"
            if f.get('sample_values'):
                samples = ', '.join(str(v) for v in f['sample_values'][:3])
                line += f" [examples: {samples}]"
            field_lines.append(line)

        prompt = (
            f"Generate semantic descriptions for these {len(fields)} IT asset fields. "
            "For EACH field, write one line starting with the exact field name, "
            "followed by ' - ', then synonyms and a 1-2 sentence description.\n\n"
            + "\n".join(field_lines)
        )

        from .pipeline_llm import describe_client
        provider, model = describe_client(self.llm_client)
        logger.info(
            "Generating %d field descriptions via %s (%s)",
            len(fields), provider, model,
        )
        try:
            raw = self.llm_client.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                # Reasoning floor for small batches + per-field headroom, but
                # capped so a large first-run batch can't request more output
                # than the provider allows (which would fail the whole call).
                # Over-cap batches get partial LLM coverage + fallback for the rest.
                max_tokens=min(4 * _DESC_MAX_TOKENS,
                               max(_DESC_MAX_TOKENS, 512 * len(fields))),
            )
            return self._parse_batch_response(raw, [f['field_name'] for f in fields])
        except LLMClientError as e:
            logger.error(f"Batch description generation failed: {e}")
            return {}

    def _parse_batch_response(
        self, raw: str, expected_fields: list[str]
    ) -> dict[str, str]:
        """Parse multi-field LLM response into field->description dict."""
        result = {}
        lines = [ln.strip() for ln in raw.strip().split('\n') if ln.strip()]

        for field_name in expected_fields:
            for line in lines:
                cleaned = line.lstrip('- ').lstrip('* ')
                if cleaned.lower().startswith(field_name.lower()):
                    desc = self._clean_description(cleaned, field_name)
                    result[field_name] = desc
                    break

        return result

    def close(self):
        """Close the LLM client."""
        self.llm_client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        available = "available" if self.is_available() else "unavailable"
        return f"DescriptionGenerator(llm={available})"
