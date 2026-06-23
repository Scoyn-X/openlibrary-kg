"""Abstract embedding provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import numpy.typing as npt


class EmbeddingProvider(ABC):
    """Abstract interface for text embedding."""

    @abstractmethod
    def embed(self, texts: list[str]) -> npt.NDArray[np.floating]:
        """Embed a list of texts into vectors.

        Args:
            texts: List of text strings to embed.

        Returns:
            numpy array of shape (len(texts), embedding_dim).
        """
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str], batch_size: int = 64) -> npt.NDArray[np.floating]:
        """Embed with batching, useful for large lists."""
        ...
