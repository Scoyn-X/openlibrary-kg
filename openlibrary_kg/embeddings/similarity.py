"""Cosine similarity and top-K matching utilities."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def cosine_similarity(a: NDArray[np.floating], b: NDArray[np.floating]) -> NDArray[np.floating]:
    """Compute cosine similarity between two sets of vectors.

    Args:
        a: (m, d) array of m vectors.
        b: (n, d) array of n vectors.

    Returns:
        (m, n) similarity matrix. sim[i][j] = cosine(a[i], b[j]).
    """
    # Normalize
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return a_norm @ b_norm.T


def top_k_similar(
    sim_matrix: NDArray[np.floating],
    k: int = 20,
    threshold: float = 0.0,
) -> list[list[tuple[int, float]]]:
    """Extract top-K similar pairs above threshold for each row.

    Args:
        sim_matrix: (n, n) symmetric similarity matrix.
        k: Number of top matches to keep per item.
        threshold: Minimum similarity to include.

    Returns:
        List of lists: for item i, list of (j, score) for top K matches j > i.
    """
    n = sim_matrix.shape[0]
    results: list[list[tuple[int, float]]] = [[] for _ in range(n)]

    for i in range(n):
        # Get similarities to all j > i (to avoid duplicates in symmetric matrix)
        row = sim_matrix[i]
        candidates = []
        for j in range(n):
            if j <= i:
                continue
            score = float(row[j])
            if score >= threshold:
                candidates.append((j, score))

        # Sort by score descending, keep top K
        candidates.sort(key=lambda x: x[1], reverse=True)
        results[i] = candidates[:k]

    return results
