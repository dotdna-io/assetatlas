# SPDX-License-Identifier: AGPL-3.0-or-later
"""
ChromaDB Store - Vector storage and retrieval for Explorer Search

Manages two collections:
1. fields - Field name embeddings for suggestion API
2. values - Sample values per field for semantic matching

Uses persistent storage for durability across restarts.
"""

import fcntl
import logging
from contextlib import contextmanager
from typing import List, Optional, Dict, Any
from pathlib import Path

import chromadb
from chromadb.config import Settings
import numpy as np

from .config import EmbeddingConfig, default_config

logger = logging.getLogger(__name__)


@contextmanager
def _init_lock(persist_path: Path):
    """
    Cross-process exclusive lock around ChromaDB client init.

    The API container runs gunicorn with 4 workers; on a fresh install all
    four invoke ChromaDBStore() near-simultaneously. ChromaDB 1.x bootstraps
    its own SQLite metadata schema (table `collections` etc.) without
    IF NOT EXISTS, so concurrent first-time inits race and one or more
    workers crash with `(code: 1) table collections already exists`,
    leaving SuggestionService unavailable in those workers.

    fcntl.flock is process-level, so it serialises across workers without
    needing infrastructure (no Redis lock, no leader election). The cost
    is negligible: workers wait at most a few hundred ms while the first
    one creates the schema; subsequent inits are no-ops.
    """
    lock_path = persist_path / ".init.lock"
    persist_path.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class ChromaDBStore:
    """
    Manages ChromaDB collections for Explorer Search embeddings.

    This store is designed for:
    - Batch updates during asset generation (add/update embeddings)
    - Fast queries during API requests (search similar)

    Collections:
    - fields: Field definitions with descriptions (~50 documents)
    - values: Sample values per field (~500 documents)

    Usage:
        # For batch operations (during generation)
        with ChromaDBStore() as store:
            store.add_field_embeddings(fields_data)
            store.add_value_embeddings(values_data)

        # For queries (during API requests)
        store = ChromaDBStore()
        results = store.search_fields("machine name", n_results=5)
    """

    def __init__(self, config: Optional[EmbeddingConfig] = None):
        """
        Initialize ChromaDB store.

        Args:
            config: Embedding configuration. Uses default if not provided.
        """
        self.config = config or default_config
        self._client = None
        self._fields_collection = None
        self._values_collection = None

        self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize ChromaDB client with persistent storage."""
        persist_path = Path(self.config.chromadb_path)
        persist_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Initializing ChromaDB at: {persist_path}")

        with _init_lock(persist_path):
            self._client = chromadb.PersistentClient(
                path=str(persist_path),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )

            self._fields_collection = self._client.get_or_create_collection(
                name=self.config.fields_collection_name,
                metadata={"description": "Field definitions for Explorer Search suggestions"}
            )

            self._values_collection = self._client.get_or_create_collection(
                name=self.config.values_collection_name,
                metadata={"description": "Sample values per field for semantic matching"}
            )

        logger.info(
            f"Collections initialized - fields: {self._fields_collection.count()}, "
            f"values: {self._values_collection.count()}"
        )

    @property
    def fields_collection(self):
        """Get fields collection."""
        return self._fields_collection

    @property
    def values_collection(self):
        """Get values collection."""
        return self._values_collection

    # =========================================================================
    # Field Operations
    # =========================================================================

    def add_field_embeddings(
        self,
        field_names: List[str],
        embeddings: np.ndarray,
        metadatas: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Add or update field embeddings in the fields collection.

        Args:
            field_names: List of field names (used as IDs)
            embeddings: Embedding matrix (shape: [n_fields, dimensions])
            metadatas: Optional metadata for each field (display_name, description, etc.)
        """
        if len(field_names) == 0:
            return

        # Prepare IDs (use field names as IDs)
        ids = field_names

        # Convert numpy array to list of lists
        embeddings_list = embeddings.tolist()

        # Prepare metadatas if not provided
        if metadatas is None:
            metadatas = [{"field_name": name} for name in field_names]

        # Upsert to handle both add and update
        self._fields_collection.upsert(
            ids=ids,
            embeddings=embeddings_list,
            metadatas=metadatas
        )

        logger.info(f"Upserted {len(field_names)} field embeddings")

    def search_fields(
        self,
        query_embedding: np.ndarray,
        n_results: int = 5,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar fields by embedding.

        Args:
            query_embedding: Query vector (shape: [dimensions])
            n_results: Number of results to return
            where: Optional filter conditions

        Returns:
            List of dicts with field_name, score, and metadata
        """
        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        results = self._fields_collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=n_results,
            where=where,
            include=["metadatas", "distances"]
        )

        # Format results
        formatted = []
        if results['ids'] and results['ids'][0]:
            for i, field_id in enumerate(results['ids'][0]):
                # ChromaDB returns L2 distance, convert to similarity score
                distance = results['distances'][0][i] if results['distances'] else 0
                # For normalized embeddings, L2 distance can be converted to cosine similarity
                # similarity = 1 - (distance^2 / 2)
                similarity = 1 - (distance ** 2 / 2)

                formatted.append({
                    'field_name': field_id,
                    'score': float(similarity),
                    'metadata': results['metadatas'][0][i] if results['metadatas'] else {}
                })

        return formatted

    def get_field(self, field_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific field by name.

        Args:
            field_name: Field name to retrieve

        Returns:
            Dict with embedding and metadata, or None if not found
        """
        result = self._fields_collection.get(
            ids=[field_name],
            include=["embeddings", "metadatas"]
        )

        if result['ids']:
            embedding = None
            if result['embeddings'] is not None and len(result['embeddings']) > 0:
                embedding = np.array(result['embeddings'][0])

            metadata = {}
            if result['metadatas'] is not None and len(result['metadatas']) > 0:
                metadata = result['metadatas'][0]

            return {
                'field_name': result['ids'][0],
                'embedding': embedding,
                'metadata': metadata
            }
        return None

    def delete_field(self, field_name: str) -> bool:
        """
        Delete a field from the collection.

        Args:
            field_name: Field name to delete

        Returns:
            True if deleted, False if not found
        """
        try:
            self._fields_collection.delete(ids=[field_name])
            logger.info(f"Deleted field: {field_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete field {field_name}: {e}")
            return False

    def get_all_fields(self) -> List[Dict[str, Any]]:
        """Get all fields from the collection."""
        result = self._fields_collection.get(
            include=["metadatas"]
        )

        return [
            {'field_name': id_, 'metadata': meta}
            for id_, meta in zip(result['ids'], result['metadatas'] or [{}] * len(result['ids']))
        ]

    # =========================================================================
    # Value Operations
    # =========================================================================

    def add_value_embeddings(
        self,
        value_ids: List[str],
        embeddings: np.ndarray,
        metadatas: List[Dict[str, Any]]
    ) -> None:
        """
        Add or update value embeddings in the values collection.

        Args:
            value_ids: Unique IDs for each value (e.g., "hostname:server001")
            embeddings: Embedding matrix (shape: [n_values, dimensions])
            metadatas: Metadata for each value (must include field_name, value)
        """
        if len(value_ids) == 0:
            return

        embeddings_list = embeddings.tolist()

        self._values_collection.upsert(
            ids=value_ids,
            embeddings=embeddings_list,
            metadatas=metadatas
        )

        logger.info(f"Upserted {len(value_ids)} value embeddings")

    def search_values(
        self,
        query_embedding: np.ndarray,
        n_results: int = 10,
        field_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar values by embedding.

        Args:
            query_embedding: Query vector (shape: [dimensions])
            n_results: Number of results to return
            field_name: Optional filter by field name

        Returns:
            List of dicts with value, field_name, score
        """
        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        where = {"field_name": field_name} if field_name else None

        results = self._values_collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=n_results,
            where=where,
            include=["metadatas", "distances"]
        )

        formatted = []
        if results['ids'] and results['ids'][0]:
            for i, value_id in enumerate(results['ids'][0]):
                distance = results['distances'][0][i] if results['distances'] else 0
                similarity = 1 - (distance ** 2 / 2)

                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                formatted.append({
                    'value_id': value_id,
                    'value': metadata.get('value', ''),
                    'field_name': metadata.get('field_name', ''),
                    'score': float(similarity),
                    'metadata': metadata
                })

        return formatted

    def get_values_for_field(self, field_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all values for a specific field.

        Args:
            field_name: Field to get values for
            limit: Maximum number of values to return

        Returns:
            List of value dicts
        """
        result = self._values_collection.get(
            where={"field_name": field_name},
            limit=limit,
            include=["metadatas"]
        )

        return [
            {
                'value_id': id_,
                'value': meta.get('value', ''),
                'metadata': meta
            }
            for id_, meta in zip(result['ids'], result['metadatas'] or [{}] * len(result['ids']))
        ]

    def delete_values_for_field(self, field_name: str) -> int:
        """
        Delete all values for a specific field.

        Args:
            field_name: Field whose values should be deleted

        Returns:
            Number of values deleted
        """
        # First get all IDs for this field
        result = self._values_collection.get(
            where={"field_name": field_name}
        )

        if result['ids']:
            self._values_collection.delete(ids=result['ids'])
            logger.info(f"Deleted {len(result['ids'])} values for field: {field_name}")
            return len(result['ids'])

        return 0

    # =========================================================================
    # Collection Management
    # =========================================================================

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collections."""
        return {
            'fields_count': self._fields_collection.count(),
            'values_count': self._values_collection.count(),
            'persist_path': self.config.chromadb_path
        }

    def clear_fields(self) -> None:
        """Clear all documents from fields collection."""
        # Delete and recreate collection
        self._client.delete_collection(self.config.fields_collection_name)
        self._fields_collection = self._client.create_collection(
            name=self.config.fields_collection_name,
            metadata={"description": "Field definitions for Explorer Search suggestions"}
        )
        logger.info("Cleared fields collection")

    def clear_values(self) -> None:
        """Clear all documents from values collection."""
        self._client.delete_collection(self.config.values_collection_name)
        self._values_collection = self._client.create_collection(
            name=self.config.values_collection_name,
            metadata={"description": "Sample values per field for semantic matching"}
        )
        logger.info("Cleared values collection")

    def reset(self) -> None:
        """Reset both collections (delete all data)."""
        self.clear_fields()
        self.clear_values()
        logger.info("Reset all ChromaDB collections")

    def close(self) -> None:
        """Close the client (ChromaDB handles cleanup automatically)."""
        # ChromaDB PersistentClient doesn't require explicit close
        # but we set references to None for cleanup
        self._fields_collection = None
        self._values_collection = None
        self._client = None
        logger.debug("ChromaDB store closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        stats = self.get_collection_stats()
        return (
            f"ChromaDBStore(fields={stats['fields_count']}, "
            f"values={stats['values_count']})"
        )
