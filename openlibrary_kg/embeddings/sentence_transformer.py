"""Local sentence-transformers embedding provider.

Uses the sentence-transformers library for free, local embedding computation.
Recommended model: all-MiniLM-L6-v2 (384-dim, fast, good quality).
"""

from __future__ import annotations

import logging

import numpy as np

from openlibrary_kg.embeddings.base import EmbeddingProvider

logger = logging.getLogger("openlibrary_kg.embeddings")


class SentenceTransformerProvider(EmbeddingProvider):
    """Embedding via local sentence-transformers model."""

    def __init__(self, model: str = "all-MiniLM-L6-v2", model_name: str = ""):
        self.model_name = model or model_name or "all-MiniLM-L6-v2"
        self._model = None
        logger.info("Loading sentence-transformers model: %s", self.model_name)

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.embed_batch(texts, batch_size=64)

    def embed_batch(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        if not texts:
            return np.array([])
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=len(texts) > 1000,
            convert_to_numpy=True,
        )
        return embeddings  # type: ignore[return-value]
