# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Configuration for Embedding Service

All settings for the embedding system including model selection,
ChromaDB paths, and performance parameters.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EmbeddingConfig:
    """Configuration for the embedding service."""

    # Model settings - bge-base-en-v1.5 achieves 93% accuracy on synonym detection
    model_name: str = "BAAI/bge-base-en-v1.5"
    model_dimensions: int = 768
    device: str = "cpu"  # CPU-only deployment

    # Local model cache (inside project directory)
    model_cache_path: str = field(default_factory=lambda: str(
        Path(__file__).parent.parent.parent.parent / "models" / "cache"
    ))

    # ChromaDB settings
    chromadb_path: str = field(default_factory=lambda: str(
        Path(__file__).parent.parent.parent.parent / "data" / "chromadb"
    ))
    fields_collection_name: str = "fields"
    values_collection_name: str = "values"

    # Synonym lookup settings
    synonym_output_path: str = field(default_factory=lambda: str(
        Path(__file__).parent.parent.parent.parent / "static" / "synonym_lookup.json"
    ))
    synonym_similarity_threshold: float = 0.62  # Lowered for hybrid system (Layer 3 fallback)

    # Database settings (requires DATABASE_URL environment variable)
    database_url: Optional[str] = field(default_factory=lambda: os.environ.get(
        'DATABASE_URL'
    ))

    # Performance settings
    batch_size: int = 32  # Batch size for embedding generation
    normalize_embeddings: bool = True  # L2 normalize for cosine similarity

    # Common IT terms for synonym generation
    # These terms will be matched against field descriptions
    common_it_terms: list = field(default_factory=lambda: [
        # Hostname synonyms
        "machine name", "computer name", "device name", "host", "server name",
        "node", "node name", "pc name", "workstation name", "system name",

        # IP synonyms
        "ip address", "network address", "ipv4", "ipv6", "ipv4 address",
        "ipv6 address", "internet protocol", "network ip", "device ip",

        # MAC synonyms
        "mac address", "hardware address", "physical address", "ethernet address",
        "network interface", "nic address",

        # Serial number synonyms
        "serial number", "serial", "service tag", "asset tag", "hardware id",
        "product serial", "device serial", "sn", "s/n",

        # OS synonyms
        "operating system", "os", "platform", "system software", "os version",
        "windows", "linux", "macos", "ubuntu", "red hat",

        # Category synonyms
        "category", "type", "device type", "asset type", "classification",
        "server", "workstation", "laptop", "desktop", "virtual machine", "vm",
        "network device", "switch", "router", "firewall", "iot",

        # Manufacturer synonyms
        "manufacturer", "vendor", "make", "brand", "oem", "hardware vendor",
        "dell", "hp", "cisco", "apple", "lenovo", "vmware",

        # Model synonyms
        "model", "model number", "product", "product name", "hardware model",
        "device model", "model name",

        # Location synonyms
        "location", "site", "building", "floor", "rack", "data center",
        "datacenter", "dc", "physical location", "where",

        # Owner synonyms
        "owner", "assigned to", "user", "responsible", "contact", "department",
        "team", "custodian",

        # Data source synonyms
        "data source", "source", "integration", "provider", "system",
        "imported from", "discovered by",

        # Time synonyms
        "first seen", "discovered", "created", "added", "first discovered",
        "last seen", "last active", "last updated", "recent", "latest",

        # Status synonyms
        "status", "state", "eol", "end of life", "supported", "active",
        "online", "offline",

        # Confidence synonyms
        "confidence", "score", "quality", "accuracy", "certainty",

        # FQDN synonyms
        "fqdn", "fully qualified", "domain name", "dns name", "full hostname",
    ])

    def __post_init__(self):
        """Ensure paths exist after initialization."""
        Path(self.model_cache_path).mkdir(parents=True, exist_ok=True)
        Path(self.chromadb_path).mkdir(parents=True, exist_ok=True)
        Path(self.synonym_output_path).parent.mkdir(parents=True, exist_ok=True)


# Default configuration instance
default_config = EmbeddingConfig()
