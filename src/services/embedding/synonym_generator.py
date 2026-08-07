# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Synonym Generator for Explorer Search

Generates a pre-computed lookup table mapping common IT terms to field names.
This enables instant (<1ms) synonym resolution in the browser.
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone

import numpy as np

from .config import EmbeddingConfig, default_config
from .embedding_service import EmbeddingService
from .field_registry import FieldRegistry, FieldDefinition
from .synonym_rules import match_rules, get_all_core_synonyms, CORE_FIELD_SYNONYMS
from .synonym_llm import LLMSynonymGenerator

logger = logging.getLogger(__name__)


@dataclass
class SynonymMatch:
    """Result of matching a term to a field."""
    term: str
    field_name: str
    similarity_score: float
    field_display_name: str


def convert_field_name_to_api_format(field_name: str, data_source: Optional[str]) -> str:
    """
    Convert field_registry field name to API format.

    Extended fields need double underscore separator to match assets API:
    - field_registry: intune_full_effectiveEncryption_s (single underscore)
    - API format: intune_full__effectiveEncryption_s (double underscore)

    Args:
        field_name: The field name from field_registry
        data_source: The data source (e.g., "Intune Full") - indicates extended field

    Returns:
        Field name in API format
    """
    if not data_source:
        # Static field - no transformation needed
        return field_name

    # Extended field - convert source_fieldname to source__fieldname
    # Source name: "Intune Full" -> "intune_full"
    source_key = data_source.lower().replace(' ', '_')

    # Find where the source prefix ends in the field name
    prefix = f"{source_key}_"
    if field_name.startswith(prefix):
        # Extract the actual field name part
        actual_field = field_name[len(prefix):]
        # Rebuild with double underscore
        return f"{source_key}__{actual_field}"

    # Fallback - return as-is if pattern doesn't match
    return field_name


class SynonymGenerator:
    """
    Generate synonym lookup table using embedding similarity.

    For each common IT term, finds the most similar field based on
    embedding distance between the term and field descriptions.
    """

    # Extended list of common IT terms for synonym matching
    # Organized by category for maintainability
    COMMON_IT_TERMS = [
        # === HOSTNAME / DEVICE NAME ===
        "machine name", "computer name", "device name", "host", "server name",
        "node", "node name", "pc name", "workstation name", "system name",
        "hostname", "host name", "endpoint name", "asset name",

        # === IP ADDRESS ===
        "ip address", "network address", "ipv4", "ipv6", "ipv4 address",
        "ipv6 address", "internet protocol", "network ip", "device ip",
        "ip", "address", "network addr",

        # === MAC ADDRESS ===
        "mac address", "hardware address", "physical address", "ethernet address",
        "network interface", "nic address", "mac", "nic",

        # === SERIAL NUMBER ===
        "serial number", "serial", "service tag", "asset tag", "hardware id",
        "product serial", "device serial", "sn", "s/n", "chassis serial",

        # === OPERATING SYSTEM ===
        "operating system", "os", "platform", "system software", "os version",
        "windows", "linux", "macos", "ubuntu", "red hat", "os name",

        # === CATEGORY / TYPE ===
        "category", "type", "device type", "asset type", "classification",
        "server", "workstation", "laptop", "desktop", "virtual machine", "vm",
        "network device", "switch", "router", "firewall", "iot", "iot device",
        "subcategory", "sub category", "device class",

        # === MANUFACTURER / VENDOR ===
        "manufacturer", "vendor", "make", "brand", "oem", "hardware vendor",
        "dell", "hp", "cisco", "apple", "lenovo", "vmware", "maker",

        # === MODEL ===
        "model", "model number", "product", "product name", "hardware model",
        "device model", "model name", "sku", "part number",

        # === LOCATION ===
        "location", "site", "building", "floor", "rack", "data center",
        "datacenter", "dc", "physical location", "where", "region",
        "azure region", "cloud region", "geography",

        # === OWNER / USER ===
        "owner", "assigned to", "user", "responsible", "contact", "department",
        "team", "custodian", "manager", "assignee", "user email",

        # === DATA SOURCE ===
        "data source", "source", "integration", "provider", "system",
        "imported from", "discovered by", "origin",

        # === TIME / DATES ===
        "first seen", "discovered", "created", "added", "first discovered",
        "last seen", "last active", "last updated", "recent", "latest",
        "when discovered", "discovery date", "last activity",

        # === STATUS ===
        "status", "state", "active", "online", "offline", "enabled", "disabled",

        # === EOL / SUPPORT ===
        "eol", "end of life", "supported", "unsupported", "eol status",
        "end of support", "lifecycle", "support status",

        # === CONFIDENCE ===
        "confidence", "score", "quality", "accuracy", "certainty", "reliability",
        "data quality", "confidence score",

        # === FQDN ===
        "fqdn", "fully qualified", "domain name", "dns name", "full hostname",
        "fully qualified domain name", "dns",

        # === SECURITY / ANTIVIRUS ===
        "antivirus", "av", "endpoint protection", "security software",
        "defender", "sentinelone", "crowdstrike", "security agent",
        "edr", "endpoint detection", "threat protection",

        # === THREATS / INFECTIONS ===
        "infected", "infection", "malware", "threat", "threats",
        "active threats", "compromised", "clean", "threat status",
        "threat count", "malware detected",

        # === AGENT / VERSION ===
        "agent version", "version", "software version", "agent",
        "endpoint agent", "security version", "client version",

        # === VULNERABILITIES ===
        "vulnerabilities", "vulns", "vulnerability count", "total vulns",
        "cve", "security issues", "vulnerable", "vulnerability",

        # === RISK ===
        "risk", "risk level", "risk score", "threat level", "severity",
        "priority", "criticality", "risk rating",

        # === ENCRYPTION ===
        "encrypted", "encryption", "encryption status", "bitlocker",
        "disk encryption", "is encrypted", "encryption state",

        # === ENROLLMENT / REGISTRATION ===
        "enrollment", "enrolled", "registration", "registered",
        "device enrollment", "enrollment type", "registration state",
        "mdm enrollment", "intune enrollment",

        # === DETECTION / PROTECTION MODE ===
        "detection", "detection mode", "protection mode", "mitigation",
        "mitigation mode", "protect", "detect", "response mode",

        # === UPDATE STATUS ===
        "up to date", "updated", "outdated", "patch status", "update status",
        "needs update", "current", "latest version",

        # === DECOMMISSION ===
        "decommissioned", "retired", "end of life", "decom",
        "pending uninstall", "uninstall", "removal",

        # === CLOUD / AZURE ===
        "azure", "cloud", "aws", "gcp", "cloud provider", "resource group",
        "subscription", "cloud resource", "virtual machine",

        # === IPAM ===
        "ipam", "ip management", "subnet", "network location",
        "ipam location", "network segment",

        # === MAC VENDOR ===
        "mac vendor", "nic vendor", "hardware manufacturer", "oui",
        "network card vendor", "ethernet vendor",
    ]

    def __init__(self, config: Optional[EmbeddingConfig] = None, llm_client=None,
                 embedding_service: Optional[EmbeddingService] = None):
        """Initialize synonym generator with config.

        Args:
            config: Embedding configuration. Uses default if not provided.
            llm_client: Optional pipeline LLM client for Layer-2 synonyms.
            embedding_service: Optional pre-built EmbeddingService to share.
                When omitted the generator builds its own — loading the ONNX
                model — so a caller that already has one must pass it in or
                the model loads twice per process (#1396).
        """
        self.config = config or default_config
        self.embedding_service = (embedding_service
                                  or EmbeddingService(config=self.config))
        self.field_registry = FieldRegistry(config=self.config)
        self.llm_synonym_gen = LLMSynonymGenerator(llm_client) if llm_client else None

        # Cache for field embeddings
        self._field_embeddings: Dict[str, np.ndarray] = {}
        self._field_definitions: Dict[str, FieldDefinition] = {}

    def _load_fields(self) -> None:
        """Load all searchable fields and their embeddings."""
        logger.info("Loading fields from registry...")

        fields = self.field_registry.get_searchable_fields()
        logger.info(f"Found {len(fields)} searchable fields")

        # Store field definitions
        self._field_definitions = {f.field_name: f for f in fields}

        # Generate embeddings for field descriptions (batch for speed)
        logger.info("Generating embeddings for field descriptions...")

        docs = [f"{f.field_name} - {f.description}" for f in fields]
        embeddings = self.embedding_service.embed_texts(docs)
        for field_def, embedding in zip(fields, embeddings):
            self._field_embeddings[field_def.field_name] = embedding

        logger.info(f"Generated embeddings for {len(self._field_embeddings)} fields")

    def reload_fields(self) -> None:
        """Drop cached field definitions and embeddings.

        The next generation call re-reads fields from the registry, so a
        regeneration that follows a description update sees the fresh
        description (Layer-1 rules and Layer-2 LLM both read it) rather than a
        stale cached copy from an earlier call in the same process.
        """
        self._field_embeddings = {}
        self._field_definitions = {}

    def _find_best_match(self, term: str) -> Optional[SynonymMatch]:
        """Find the best matching field for a term."""
        if not self._field_embeddings:
            self._load_fields()

        # Embed the search term
        term_embedding = self.embedding_service.embed_text(term)

        # Find most similar field
        best_match = None
        best_score = -1

        for field_name, field_embedding in self._field_embeddings.items():
            # Cosine similarity (embeddings are normalized)
            similarity = float(np.dot(term_embedding, field_embedding))

            if similarity > best_score:
                best_score = similarity
                best_match = field_name

        if best_match and best_score >= self.config.synonym_similarity_threshold:
            field_def = self._field_definitions[best_match]
            return SynonymMatch(
                term=term,
                field_name=best_match,
                similarity_score=best_score,
                field_display_name=field_def.display_name
            )

        return None

    def generate_lookup_table(
        self,
        additional_terms: Optional[List[str]] = None,
        min_similarity: Optional[float] = None
    ) -> Dict:
        """Generate the complete synonym lookup table using 3-layer hybrid matching."""
        logger.info("Generating hybrid synonym lookup table...")

        if not self._field_embeddings:
            self._load_fields()

        # Build description map for Layer 1
        field_descriptions = {
            name: f"{defn.field_name} - {defn.description}"
            for name, defn in self._field_definitions.items()
        }

        # === SEED: Core field synonyms (hardcoded, authoritative) ===
        core_synonyms = get_all_core_synonyms()
        core_fields = set(CORE_FIELD_SYNONYMS.keys())
        logger.info(f"Core synonyms: {len(core_synonyms)} terms for {len(core_fields)} static fields")

        # === LAYER 2: LLM-generated search terms (extended fields only) ===
        llm_synonyms: Dict[str, str] = {}  # term → field_name
        if self.llm_synonym_gen and self.llm_synonym_gen.llm_client.is_available():
            extended_fields = {
                name: defn for name, defn in self._field_definitions.items()
                if defn.field_type == 'extended'
            }
            logger.info(f"Layer 2: Generating LLM search terms for {len(extended_fields)} extended fields...")
            for field_name, field_def in extended_fields.items():
                terms = self.llm_synonym_gen.generate_search_terms(
                    field_name=field_name,
                    data_source=field_def.data_source or "unknown",
                    description=field_def.description,
                )
                for term in terms:
                    if term not in llm_synonyms:
                        llm_synonyms[term] = field_name
            logger.info(f"Layer 2: {len(llm_synonyms)} LLM-generated synonyms")
        else:
            logger.info("Layer 2: LLM not available, skipping")

        # Combine all candidate terms
        all_terms = list(self.COMMON_IT_TERMS)
        if additional_terms:
            all_terms.extend(additional_terms)
        # Add LLM-generated terms to candidate list
        all_terms.extend(llm_synonyms.keys())

        # Deduplicate
        seen = set()
        unique_terms = []
        for term in all_terms:
            normalized = term.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_terms.append(normalized)

        threshold = min_similarity or self.config.synonym_similarity_threshold
        synonyms: Dict[str, str] = {}
        layer_stats = {"core": 0, "rules": 0, "llm": 0, "embedding": 0}

        # Seed core synonyms first (highest priority, no LLM needed)
        for term, field_name in core_synonyms.items():
            if field_name in self._field_definitions:
                synonyms[term] = field_name  # Static fields use bare names (no API conversion)
                layer_stats["core"] += 1

        for term in unique_terms:
            # Skip terms already resolved by core synonyms
            if term in synonyms:
                continue

            # Layer 1: Deterministic rules (overrides, abbreviations, substring)
            rule_matches = match_rules(term, field_descriptions)
            if rule_matches:
                best = max(rule_matches, key=lambda m: m.confidence)
                field_def = self._field_definitions.get(best.field_name)
                if field_def:
                    data_source = field_def.data_source
                    api_name = convert_field_name_to_api_format(best.field_name, data_source)
                    synonyms[term] = api_name
                    layer_stats["rules"] += 1
                    continue

            # Layer 2: LLM-generated terms
            if term in llm_synonyms:
                field_name = llm_synonyms[term]
                field_def = self._field_definitions.get(field_name)
                if field_def:
                    data_source = field_def.data_source
                    api_name = convert_field_name_to_api_format(field_name, data_source)
                    synonyms[term] = api_name
                    layer_stats["llm"] += 1
                    continue

            # Layer 3: Embedding similarity (fallback)
            match = self._find_best_match(term)
            if match and match.similarity_score >= threshold:
                field_def = self._field_definitions.get(match.field_name)
                data_source = field_def.data_source if field_def else None
                api_name = convert_field_name_to_api_format(match.field_name, data_source)
                synonyms[term] = api_name
                layer_stats["embedding"] += 1

        logger.info(
            f"Hybrid results: {len(synonyms)} synonyms "
            f"(rules={layer_stats['rules']}, llm={layer_stats['llm']}, "
            f"embedding={layer_stats['embedding']})"
        )

        # Build field metadata
        fields_meta = {}
        for field_name, field_def in self._field_definitions.items():
            api_field_name = convert_field_name_to_api_format(field_name, field_def.data_source)
            fields_meta[api_field_name] = {
                "display_name": field_def.display_name,
                "description": field_def.description,
                "field_type": field_def.field_type,
                "data_source": field_def.data_source
            }

        return {
            "_metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model": self.config.model_name,
                "similarity_threshold": threshold,
                "total_terms": len(unique_terms),
                "matched_terms": len(synonyms),
                "total_fields": len(self._field_definitions),
                "layer_stats": layer_stats,
            },
            "synonyms": synonyms,
            "fields": fields_meta
        }

    def save_lookup_table(
        self,
        output_path: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Generate and save the synonym lookup table to a JSON file.

        Args:
            output_path: Path to save JSON file (uses config default if not provided)
            **kwargs: Additional arguments passed to generate_lookup_table()

        Returns:
            Path to the saved file
        """
        output_path = output_path or self.config.synonym_output_path

        # Ensure directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Generate lookup table
        lookup_table = self.generate_lookup_table(**kwargs)

        # Save to JSON
        with open(output_path, 'w') as f:
            json.dump(lookup_table, f, indent=2)

        logger.info(f"Saved synonym lookup table to {output_path}")
        logger.info(f"  - {lookup_table['_metadata']['matched_terms']} synonyms")
        logger.info(f"  - {lookup_table['_metadata']['total_fields']} fields")

        return output_path

    def update_lookup_table_for_fields(
        self,
        field_names: List[str],
        output_path: Optional[str] = None,
        use_llm: bool = True,
        replace: bool = False,
        seed_core: bool = False,
    ) -> str:
        """
        Incrementally update the synonym lookup table for specific fields.

        Loads the existing synonym file, generates synonyms only for the
        given fields (all 3 layers), merges them in, and saves. Avoids
        re-processing the entire field population.

        Args:
            field_names: List of field names to generate synonyms for
            output_path: Path to save JSON file (uses config default)
            use_llm: Whether to run the Layer-2 LLM search-term step
                (default True). When False, only rules + embedding matching
                run for the new fields.
            replace: When True, drop these fields' existing synonym entries
                before regenerating, so a re-run fully replaces a field's
                synonyms (no stale terms) instead of only appending. Only
                EXTENDED fields' entries are ever dropped — a Core/static field
                is never stripped even if its name is passed. Use for
                regeneration; leave False for first-time (new-field) additions.
            seed_core: When True, ensure every authoritative Core (static)
                synonym is present in the result (deterministic, no LLM/
                embedding; fills only missing terms). Lets a full "Regenerate
                All" produce a complete table even from a missing/empty file
                without disturbing existing Core or extended entries.

        Returns:
            Path to the saved file
        """
        output_path = output_path or self.config.synonym_output_path

        if not self._field_embeddings:
            self._load_fields()

        # Load existing lookup table
        existing: Dict = {}
        if Path(output_path).exists():
            with open(output_path) as f:
                existing = json.load(f)

        existing_synonyms: Dict[str, str] = existing.get("synonyms", {})
        existing_fields: Dict[str, dict] = existing.get("fields", {})

        # Replace mode: strip this run's fields' existing terms first so they
        # become eligible for regeneration and stale terms don't linger. Only
        # EXTENDED fields are stripped — a Core/static field's curated synonyms
        # are never removed even if its name is passed (defense in depth; the
        # callers already scope to extended fields). Other extended fields keep
        # their synonyms verbatim.
        if replace:
            target_api_names = {
                convert_field_name_to_api_format(
                    name, self._field_definitions[name].data_source)
                for name in field_names
                if name in self._field_definitions
                and self._field_definitions[name].field_type == 'extended'
            }
            existing_synonyms = {
                term: api_field
                for term, api_field in existing_synonyms.items()
                if api_field not in target_api_names
            }

        # Build description map for Layer 1 (only new fields)
        new_field_defs = {
            name: defn for name, defn in self._field_definitions.items()
            if name in field_names
        }
        field_descriptions = {
            name: f"{defn.field_name} - {defn.description}"
            for name, defn in new_field_defs.items()
        }

        # Layer 2: LLM-generated search terms (only for new fields)
        llm_synonyms: Dict[str, str] = {}
        if use_llm and self.llm_synonym_gen and self.llm_synonym_gen.llm_client.is_available():
            logger.info(f"Layer 2: Generating LLM search terms for {len(new_field_defs)} new fields...")
            for field_name, field_def in new_field_defs.items():
                if field_def.field_type != 'extended':
                    continue
                terms = self.llm_synonym_gen.generate_search_terms(
                    field_name=field_name,
                    data_source=field_def.data_source or "unknown",
                    description=field_def.description,
                )
                for term in terms:
                    if term not in llm_synonyms:
                        llm_synonyms[term] = field_name
            logger.info(f"Layer 2: {len(llm_synonyms)} LLM-generated synonyms for new fields")

        # Candidate terms: common IT terms + LLM terms for new fields
        all_terms = list(self.COMMON_IT_TERMS) + list(llm_synonyms.keys())
        seen = set()
        unique_terms = []
        for term in all_terms:
            normalized = term.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_terms.append(normalized)

        threshold = self.config.synonym_similarity_threshold
        new_synonyms: Dict[str, str] = {}
        layer_stats = {"rules": 0, "llm": 0, "embedding": 0}

        for term in unique_terms:
            # Skip terms already in the existing file
            if term in existing_synonyms:
                continue

            # Layer 1: Deterministic rules (only match against new fields)
            rule_matches = match_rules(term, field_descriptions)
            if rule_matches:
                best = max(rule_matches, key=lambda m: m.confidence)
                field_def = self._field_definitions.get(best.field_name)
                if field_def:
                    api_name = convert_field_name_to_api_format(best.field_name, field_def.data_source)
                    new_synonyms[term] = api_name
                    layer_stats["rules"] += 1
                    continue

            # Layer 2: LLM-generated terms
            if term in llm_synonyms:
                field_name = llm_synonyms[term]
                field_def = self._field_definitions.get(field_name)
                if field_def:
                    api_name = convert_field_name_to_api_format(field_name, field_def.data_source)
                    new_synonyms[term] = api_name
                    layer_stats["llm"] += 1
                    continue

            # Layer 3: Embedding similarity — only if it matches a new field
            match = self._find_best_match(term)
            if match and match.similarity_score >= threshold and match.field_name in field_names:
                field_def = self._field_definitions.get(match.field_name)
                data_source = field_def.data_source if field_def else None
                api_name = convert_field_name_to_api_format(match.field_name, data_source)
                new_synonyms[term] = api_name
                layer_stats["embedding"] += 1

        logger.info(
            f"Incremental synonyms: {len(new_synonyms)} new "
            f"(rules={layer_stats['rules']}, llm={layer_stats['llm']}, "
            f"embedding={layer_stats['embedding']})"
        )

        # Merge into existing
        merged_synonyms = {**existing_synonyms, **new_synonyms}

        # Seed authoritative Core synonyms if requested (fills only missing
        # terms — present Core/extended entries are untouched). Keeps a full
        # rebuild complete even when the on-disk file was missing or empty.
        if seed_core:
            for core_term, core_field in get_all_core_synonyms().items():
                if (core_field in self._field_definitions
                        and core_term not in merged_synonyms):
                    merged_synonyms[core_term] = core_field

        # Update field metadata for new fields
        for field_name in field_names:
            field_def = self._field_definitions.get(field_name)
            if field_def:
                api_name = convert_field_name_to_api_format(field_name, field_def.data_source)
                existing_fields[api_name] = {
                    "display_name": field_def.display_name,
                    "description": field_def.description,
                    "field_type": field_def.field_type,
                    "data_source": field_def.data_source,
                }

        lookup_table = {
            "_metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model": self.config.model_name,
                "similarity_threshold": threshold,
                "matched_terms": len(merged_synonyms),
                "total_fields": len(existing_fields),
                "incremental_fields": field_names,
                "incremental_new_synonyms": len(new_synonyms),
            },
            "synonyms": merged_synonyms,
            "fields": existing_fields,
        }

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(lookup_table, f, indent=2)

        logger.info(f"Updated synonym lookup table: +{len(new_synonyms)} synonyms for {len(field_names)} new fields")
        return output_path

    def get_detailed_matches(
        self,
        terms: Optional[List[str]] = None
    ) -> List[SynonymMatch]:
        """
        Get detailed match information for analysis/debugging.

        Args:
            terms: List of terms to analyze (uses default if not provided)

        Returns:
            List of SynonymMatch objects with scores
        """
        if not self._field_embeddings:
            self._load_fields()

        terms = terms or self.COMMON_IT_TERMS
        matches = []

        for term in terms:
            match = self._find_best_match(term)
            if match:
                matches.append(match)

        # Sort by score descending
        matches.sort(key=lambda m: m.similarity_score, reverse=True)

        return matches

    def analyze_coverage(self) -> Dict:
        """
        Analyze which fields have good synonym coverage.

        Returns:
            Dictionary with coverage statistics per field
        """
        if not self._field_embeddings:
            self._load_fields()

        # Count synonyms per field
        field_synonym_count = {f: 0 for f in self._field_definitions}
        field_best_score = {f: 0.0 for f in self._field_definitions}

        for term in self.COMMON_IT_TERMS:
            match = self._find_best_match(term)
            if match and match.similarity_score >= self.config.synonym_similarity_threshold:
                field_synonym_count[match.field_name] += 1
                if match.similarity_score > field_best_score[match.field_name]:
                    field_best_score[match.field_name] = match.similarity_score

        # Build coverage report
        coverage = {}
        for field_name in self._field_definitions:
            coverage[field_name] = {
                "synonym_count": field_synonym_count[field_name],
                "best_match_score": round(field_best_score[field_name], 3),
                "has_synonyms": field_synonym_count[field_name] > 0
            }

        # Summary statistics
        fields_with_synonyms = sum(1 for c in coverage.values() if c["has_synonyms"])

        return {
            "fields_total": len(self._field_definitions),
            "fields_with_synonyms": fields_with_synonyms,
            "fields_without_synonyms": len(self._field_definitions) - fields_with_synonyms,
            "coverage_percentage": round(100 * fields_with_synonyms / len(self._field_definitions), 1),
            "per_field": coverage
        }

