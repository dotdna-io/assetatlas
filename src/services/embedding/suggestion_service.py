# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Suggestion Service - Provides semantic search suggestions for Explorer

Uses embeddings to find similar fields and values based on user query.
Preloaded at API startup via background thread for fast first-request response.
"""

import logging
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .config import EmbeddingConfig, default_config
from .embedding_service import EmbeddingService
from .synonym_generator import convert_field_name_to_api_format

try:
    from .chromadb_store import ChromaDBStore
except ImportError:
    ChromaDBStore = None

logger = logging.getLogger(__name__)


@dataclass
class FieldSuggestion:
    """A field suggestion result."""
    field_name: str
    display_name: str
    description: str
    score: float
    field_type: str
    data_source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response dict."""
        return {
            'type': 'field',
            'field_name': self.field_name,
            'display_name': self.display_name,
            'description': self.description,
            'score': round(self.score, 3),
            'field_type': self.field_type,
            'data_source': self.data_source,
            'templates': [
                f"{self.field_name} contains [value]",
                f"{self.field_name} eq [value]",
                f"{self.field_name} starts with [value]"
            ]
        }


@dataclass
class ValueSuggestion:
    """A value suggestion result."""
    value: str
    field_name: str
    display_name: str
    score: float
    count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response dict."""
        return {
            'type': 'value',
            'value': self.value,
            'field_name': self.field_name,
            'display_name': self.display_name,
            'score': round(self.score, 3),
            'count': self.count,
            'template': f"{self.field_name} eq {self.value}"
        }


class SuggestionService:
    """
    Provides semantic search suggestions using embeddings.

    Uses lazy loading for the embedding model to avoid startup delay.
    The first query will be slower (~2-3s for model load), subsequent
    queries will be fast (~50-100ms).

    Usage:
        service = SuggestionService()
        suggestions = service.get_suggestions("machine name", limit=8)
    """

    # Singleton instance for API usage
    _instance: Optional['SuggestionService'] = None

    @classmethod
    def get_instance(cls, config: Optional[EmbeddingConfig] = None) -> 'SuggestionService':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def __init__(self, config: Optional[EmbeddingConfig] = None):
        """
        Initialize suggestion service.

        Args:
            config: Embedding configuration. Uses default if not provided.
        """
        self.config = config or default_config
        self._embedding_service: Optional[EmbeddingService] = None
        self._chromadb_store = None
        self._is_initialized = False
        self._unavailable_reason: Optional[str] = None

    @property
    def is_available(self) -> bool:
        """Whether the service has all dependencies and can serve requests."""
        return self._is_initialized and self._unavailable_reason is None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of embedding service and ChromaDB."""
        if self._is_initialized or self._unavailable_reason is not None:
            return

        if ChromaDBStore is None:
            self._unavailable_reason = (
                "chromadb package is not installed. "
                "Install it with: pip install chromadb>=0.4.0"
            )
            logger.warning(f"SuggestionService unavailable: {self._unavailable_reason}")
            return

        logger.info("Initializing SuggestionService...")
        start_time = time.time()

        try:
            self._embedding_service = EmbeddingService(config=self.config, lazy_load=True)
            self._chromadb_store = ChromaDBStore(config=self.config)
            self._is_initialized = True
            logger.info(f"SuggestionService initialized in {time.time() - start_time:.2f}s")
        except Exception as e:
            self._unavailable_reason = f"Failed to initialize: {e}"
            logger.warning(f"SuggestionService unavailable: {self._unavailable_reason}")

    def get_suggestions(
        self,
        query: str,
        limit: int = 8,
        include_values: bool = False,
        min_score: float = 0.5
    ) -> Dict[str, Any]:
        """
        Get field and optionally value suggestions for a query.

        Args:
            query: User's search query (min 2 chars recommended)
            limit: Maximum number of suggestions to return
            include_values: Whether to include value suggestions
            min_score: Minimum similarity score threshold

        Returns:
            Dict with suggestions list and timing info
        """
        start_time = time.time()

        if not query or len(query.strip()) < 2:
            return {
                'suggestions': [],
                'query': query,
                'query_time_ms': 0,
                'message': 'Query too short (min 2 characters)'
            }

        self._ensure_initialized()

        if not self.is_available:
            return {
                'suggestions': [],
                'query': query,
                'query_time_ms': 0,
                'total_found': 0,
                'service_unavailable': True,
                'message': self._unavailable_reason or 'Suggestion service is not available'
            }

        query = query.strip().lower()
        suggestions = []

        # Get field suggestions
        field_suggestions = self._get_field_suggestions(query, limit, min_score)
        suggestions.extend([s.to_dict() for s in field_suggestions])

        # Optionally get value suggestions
        if include_values and len(suggestions) < limit:
            remaining = limit - len(suggestions)
            value_suggestions = self._get_value_suggestions(query, remaining, min_score)
            suggestions.extend([s.to_dict() for s in value_suggestions])

        query_time_ms = (time.time() - start_time) * 1000

        return {
            'suggestions': suggestions[:limit],
            'query': query,
            'query_time_ms': round(query_time_ms, 1),
            'total_found': len(suggestions)
        }

    def _get_field_suggestions(
        self,
        query: str,
        limit: int,
        min_score: float
    ) -> List[FieldSuggestion]:
        """Get field suggestions using embedding similarity."""
        # Check if ChromaDB has fields
        stats = self._chromadb_store.get_collection_stats()
        if stats['fields_count'] == 0:
            logger.warning("No fields in ChromaDB - returning empty suggestions")
            return []

        # Generate query embedding
        query_embedding = self._embedding_service.embed_text(query)

        # Search ChromaDB
        results = self._chromadb_store.search_fields(
            query_embedding=query_embedding,
            n_results=limit * 2  # Get more than needed to filter by score
        )

        suggestions = []
        for result in results:
            score = result.get('score', 0)
            if score < min_score:
                continue

            metadata = result.get('metadata', {})
            data_source = metadata.get('data_source')
            raw_name = result['field_name']
            # Convert field_registry name to API format (source__field for extended fields)
            api_name = convert_field_name_to_api_format(raw_name, data_source)
            suggestions.append(FieldSuggestion(
                field_name=api_name,
                display_name=metadata.get('display_name', raw_name),
                description=metadata.get('description', ''),
                score=score,
                field_type=metadata.get('field_type', 'unknown'),
                data_source=data_source
            ))

        return suggestions[:limit]

    def _get_value_suggestions(
        self,
        query: str,
        limit: int,
        min_score: float
    ) -> List[ValueSuggestion]:
        """Get value suggestions using embedding similarity."""
        stats = self._chromadb_store.get_collection_stats()
        if stats['values_count'] == 0:
            return []

        # Generate query embedding
        query_embedding = self._embedding_service.embed_text(query)

        # Search ChromaDB values
        results = self._chromadb_store.search_values(
            query_embedding=query_embedding,
            n_results=limit * 2
        )

        suggestions = []
        for result in results:
            score = result.get('score', 0)
            if score < min_score:
                continue

            metadata = result.get('metadata', {})
            raw_field = result.get('field_name', '')
            data_source = metadata.get('data_source')
            api_field = convert_field_name_to_api_format(raw_field, data_source)
            suggestions.append(ValueSuggestion(
                value=result.get('value', ''),
                field_name=api_field,
                display_name=metadata.get('display_name', raw_field),
                score=score,
                count=metadata.get('count', 0)
            ))

        return suggestions[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        self._ensure_initialized()

        if not self.is_available:
            return {
                'embedding_model': self.config.model_name,
                'embedding_loaded': False,
                'chromadb_fields_count': 0,
                'chromadb_values_count': 0,
                'service_unavailable': True,
                'message': self._unavailable_reason
            }

        chromadb_stats = self._chromadb_store.get_collection_stats()

        return {
            'embedding_model': self.config.model_name,
            'embedding_loaded': self._embedding_service.is_loaded if self._embedding_service else False,
            'chromadb_fields_count': chromadb_stats['fields_count'],
            'chromadb_values_count': chromadb_stats['values_count'],
            'chromadb_path': chromadb_stats['persist_path']
        }

    def health_check(self) -> Dict[str, Any]:
        """Check service health without loading the model."""
        if ChromaDBStore is None:
            return {
                'status': 'unavailable',
                'chromadb_available': False,
                'error': 'chromadb package is not installed'
            }

        try:
            store = ChromaDBStore(config=self.config)
            stats = store.get_collection_stats()
            store.close()

            return {
                'status': 'healthy',
                'chromadb_available': True,
                'fields_count': stats['fields_count'],
                'values_count': stats['values_count'],
                'model_loaded': self._embedding_service.is_loaded if self._embedding_service else False
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'chromadb_available': False,
                'error': str(e)
            }
