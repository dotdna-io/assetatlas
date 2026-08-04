# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Embedding Service - Core embedding generation via ONNX Runtime

Uses BAAI/bge-base-en-v1.5 for CPU-optimized semantic embeddings.
Achieves 93% accuracy on synonym detection for IT asset fields.
This service is used during asset generation pipeline, not in the Flask API.
"""

import logging
from typing import List, Optional, Union
import numpy as np

# ONNX Runtime is the shipped backend: it runs the SAME weights as the
# safetensors sentence-transformers used to load, so vectors are equivalent to
# float32 noise (worst-case cosine 0.99999976 over 2000 real strings, identical
# top-10 ordering on every query — tests/unit/services/embedding/
# test_onnx_parity.py, issue #1070). Dropping torch + sentence-transformers takes
# 282 MB compressed off the base image that api, scheduler, mcp and teams-bridge
# all share.
try:
    from .onnx_backend import OnnxTextEncoder
except ImportError:  # pragma: no cover
    OnnxTextEncoder = None

# sentence-transformers is no longer installed in the image. The import stays so
# a developer with it in their venv keeps a working fallback, and so the failure
# mode on a broken install is a clear error rather than an AttributeError.
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from .config import EmbeddingConfig, default_config

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Generate embeddings using BGE-base-en-v1.5.

    This service is designed to be used during batch operations
    (asset generation, synonym table generation) rather than
    real-time API calls. The model is loaded on initialization
    and should be unloaded when done.

    Model: BAAI/bge-base-en-v1.5
    - 109M parameters, 768 dimensions
    - 93% accuracy on IT synonym detection
    - ~8ms average query time on CPU

    Usage:
        # For batch operations
        with EmbeddingService() as service:
            embeddings = service.embed_texts(["text1", "text2"])

        # Or manual lifecycle
        service = EmbeddingService()
        embeddings = service.embed_texts(["text1", "text2"])
        service.unload()
    """

    def __init__(self, config: Optional[EmbeddingConfig] = None, lazy_load: bool = False):
        """
        Initialize embedding service.

        Args:
            config: Embedding configuration. Uses default if not provided.
            lazy_load: If True, don't load model until first use.
        """
        self.config = config or default_config
        self._model = None
        self._is_loaded = False

        if not lazy_load:
            self._load_model()

    def _load_model(self) -> None:
        """Load the embedding model into memory."""
        if self._is_loaded:
            return

        logger.info(f"Loading embedding model: {self.config.model_name}")
        logger.info(f"Cache directory: {self.config.model_cache_path}")
        logger.info(f"Device: {self.config.device}")

        try:
            self._model = self._build_backend()
            self._model.eval()  # Set to evaluation mode
            self._is_loaded = True

            logger.info(f"Model loaded successfully. Dimensions: {self.config.model_dimensions}")

        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise RuntimeError(f"Could not load model {self.config.model_name}: {e}")

    def _build_backend(self):
        """ONNX first, sentence-transformers only as a developer fallback.

        Order matters: a venv that happens to have BOTH must behave like the
        shipped image, or local results silently diverge from production.
        """
        if OnnxTextEncoder is not None:
            # The module imports cleanly even when onnxruntime/tokenizers are
            # absent (it degrades to ort=None), and it can also fail on a cache
            # that predates the ONNX export. So decide on CONSTRUCTION, not on
            # importability — testing `OnnxTextEncoder is not None` would make
            # the fallback below unreachable in exactly the cases it exists for.
            try:
                backend = OnnxTextEncoder(
                    self.config.model_name,
                    cache_folder=self.config.model_cache_path,
                )
                logger.info("Using ONNX Runtime embedding backend")
                return backend
            except Exception as e:
                if SentenceTransformer is None:
                    # The shipped image has no fallback; a broken ONNX backend
                    # must surface as itself, not as a vaguer error later.
                    raise
                logger.warning(
                    "ONNX backend unavailable (%s); falling back to "
                    "sentence-transformers. This is not what the image ships.", e
                )
        if SentenceTransformer is not None:
            return SentenceTransformer(
                self.config.model_name,
                device=self.config.device,
                cache_folder=self.config.model_cache_path,
            )
        raise ImportError(
            "No embedding backend available: install onnxruntime + tokenizers "
            "(shipped) or sentence-transformers (developer fallback)."
        )

    def ensure_loaded(self) -> None:
        """Ensure model is loaded before use."""
        if not self._is_loaded:
            self._load_model()

    def unload(self) -> None:
        """Unload model from memory to free resources."""
        if self._model is not None:
            del self._model
            self._model = None
            self._is_loaded = False
            logger.info("Embedding model unloaded")

    def embed_text(self, text: str) -> np.ndarray:
        """
        Generate embedding for a single text.

        Args:
            text: Input text to embed

        Returns:
            Embedding vector as numpy array (shape: [dimensions])
        """
        self.ensure_loaded()
        return self.embed_texts([text])[0]

    def embed_texts(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False
    ) -> np.ndarray:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            batch_size: Override default batch size
            show_progress: Show progress bar during encoding

        Returns:
            Embeddings matrix as numpy array (shape: [n_texts, dimensions])
        """
        self.ensure_loaded()

        if not texts:
            return np.array([])

        batch_size = batch_size or self.config.batch_size

        logger.debug(f"Generating embeddings for {len(texts)} texts")

        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=self.config.normalize_embeddings,
            convert_to_numpy=True
        )

        logger.debug(f"Generated embeddings shape: {embeddings.shape}")

        return embeddings

    def compute_similarity(
        self,
        embeddings_a: np.ndarray,
        embeddings_b: np.ndarray
    ) -> np.ndarray:
        """
        Compute cosine similarity between two sets of embeddings.

        Args:
            embeddings_a: First set (shape: [n, d])
            embeddings_b: Second set (shape: [m, d])

        Returns:
            Similarity matrix (shape: [n, m])
        """
        # Embeddings are already normalized if config.normalize_embeddings is True
        if not self.config.normalize_embeddings:
            # Normalize for cosine similarity
            norm_a = np.linalg.norm(embeddings_a, axis=1, keepdims=True)
            norm_b = np.linalg.norm(embeddings_b, axis=1, keepdims=True)
            embeddings_a = embeddings_a / np.clip(norm_a, 1e-8, None)
            embeddings_b = embeddings_b / np.clip(norm_b, 1e-8, None)

        return embeddings_a @ embeddings_b.T

    def find_most_similar(
        self,
        query_embedding: np.ndarray,
        candidate_embeddings: np.ndarray,
        top_k: int = 1
    ) -> List[tuple]:
        """
        Find most similar candidates for a query embedding.

        Args:
            query_embedding: Query vector (shape: [d])
            candidate_embeddings: Candidate vectors (shape: [n, d])
            top_k: Number of results to return

        Returns:
            List of (index, score) tuples, sorted by score descending
        """
        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        similarities = self.compute_similarity(query_embedding, candidate_embeddings)
        similarities = similarities.flatten()

        # Get top-k indices
        if top_k >= len(similarities):
            top_indices = np.argsort(similarities)[::-1]
        else:
            top_indices = np.argpartition(similarities, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        return [(int(idx), float(similarities[idx])) for idx in top_indices]

    def embed_field_document(self, field_name: str, description: str) -> np.ndarray:
        """
        Generate embedding for a field definition.

        Combines field name and description for richer semantic representation.

        Args:
            field_name: Field name (e.g., 'hostname')
            description: Field description

        Returns:
            Embedding vector
        """
        document = f"{field_name} - {description}"
        return self.embed_text(document)

    @property
    def is_loaded(self) -> bool:
        """Check if model is currently loaded."""
        return self._is_loaded

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions."""
        return self.config.model_dimensions

    @property
    def model_name(self) -> str:
        """Get model name."""
        return self.config.model_name

    def __enter__(self):
        """Context manager entry."""
        self.ensure_loaded()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - unload model."""
        self.unload()

    def __repr__(self) -> str:
        status = "loaded" if self._is_loaded else "not loaded"
        return f"EmbeddingService(model={self.config.model_name}, {status})"
