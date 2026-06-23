"""OpenAI embedding provider.

Uses text-embedding-3-small or similar OpenAI-compatible endpoint.
"""

from __future__ import annotations

import logging
import os

import numpy as np
from openai import OpenAI

from openlibrary_kg.embeddings.base import EmbeddingProvider

logger = logging.getLogger("openlibrary_kg.embeddings")


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embedding via OpenAI / compatible API."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.model = model
        api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        api_base = api_base or os.environ.get("OPENAI_API_BASE", None)
        client_kwargs: dict = {"api_key": api_key}
        if api_base:
            client_kwargs["base_url"] = api_base
        self._client = OpenAI(**client_kwargs)

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.embed_batch(texts, batch_size=100)

    def embed_batch(self, texts: list[str], batch_size: int = 100) -> np.ndarray:
        if not texts:
            return np.array([])

        all_embeddings: list[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self._client.embeddings.create(
                model=self.model,
                input=batch,
            )
            batch_embs = np.array(
                [r.embedding for r in resp.data], dtype=np.float32
            )
            all_embeddings.append(batch_embs)

        return np.vstack(all_embeddings)
