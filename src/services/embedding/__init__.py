# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Embedding Service Module for Explorer Search

Provides semantic search capabilities for the AssetAtlas Explorer:
1. Field embeddings for suggestion API
2. Value embeddings for semantic matching
3. Synonym lookup table generation for real-time regex enhancement
4. LLM-powered field description generation

Components:
- EmbeddingService: Core embedding generation using BGE-base-en-v1.5
- FieldRegistry: CRUD operations for field definitions
- ChromaDBStore: Vector storage and retrieval
- SynonymGenerator: Generate synonym lookup table for browser-side resolution
- DescriptionGenerator: Generate semantic field descriptions using LLM
"""

from .config import EmbeddingConfig
from .field_registry import FieldRegistry
from .embedding_service import EmbeddingService
from .synonym_generator import SynonymGenerator, SynonymMatch
from .pipeline_llm import LLMClientError  # noqa: F401  (re-export)
from .description_generator import DescriptionGenerator, GeneratedDescription
from .suggestion_service import SuggestionService, FieldSuggestion, ValueSuggestion

# ChromaDB is optional - only import if installed
try:
    from .chromadb_store import ChromaDBStore
except ImportError:
    ChromaDBStore = None

__all__ = [
    'EmbeddingConfig',
    'FieldRegistry',
    'EmbeddingService',
    'ChromaDBStore',
    'SynonymGenerator',
    'SynonymMatch',
    'LLMClientError',
    'DescriptionGenerator',
    'GeneratedDescription',
    'SuggestionService',
    'FieldSuggestion',
    'ValueSuggestion',
]
