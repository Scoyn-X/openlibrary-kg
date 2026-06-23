"""Polysemy detection: finding words with multiple meanings across contexts.

For each concept name that:
  - has at least `min_occurrences` occurrences, AND
  - is distributed across at least `min_files` distinct files,
we cluster the LLM-generated definitions for its occurrences. Each cluster
represents one distinct meaning of the concept.

The file-spread gate is important: a concept that appears 10 times but all
inside one file is unlikely to be polysemous — it's just one local meaning
repeated. We want concepts whose usage is broad enough that polysemy is
plausible.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import numpy as np

from openlibrary_kg.embeddings.base import EmbeddingProvider
from openlibrary_kg.models import DefinitionCluster

logger = logging.getLogger("openlibrary_kg.relationships")


def _dbscan_cluster(
    vectors: np.ndarray,
    eps: float = 0.35,
    min_samples: int = 1,
) -> np.ndarray:
    """Simple DBSCAN for definition embeddings. Returns cluster labels."""
    n = vectors.shape[0]
    labels = np.full(n, -1, dtype=int)
    cluster_id = 0

    for i in range(n):
        if labels[i] != -1:
            continue

        distances = np.linalg.norm(vectors - vectors[i], axis=1)
        neighbors = np.where(distances <= eps)[0]

        if len(neighbors) < min_samples:
            continue

        labels[neighbors] = cluster_id

        to_check = list(neighbors)
        while to_check:
            pt = to_check.pop()
            distances_pt = np.linalg.norm(vectors - vectors[pt], axis=1)
            neighbors_pt = np.where(distances_pt <= eps)[0]
            if len(neighbors_pt) >= min_samples:
                for nb in neighbors_pt:
                    if labels[nb] == -1:
                        labels[nb] = cluster_id
                        to_check.append(nb)

        cluster_id += 1

    return labels


def analyze_polysemy(
    occurrences: list[dict[str, Any]],
    embedding_provider: EmbeddingProvider,
    min_occurrences: int = 5,
    min_files: int = 3,
    distance_threshold: float = 0.35,
) -> dict[str, list[dict[str, Any]]]:
    """Analyze polysemy: cluster definitions per concept to find distinct meanings.

    Args:
        occurrences: List of occurrence dicts with non-empty `definition`.
        embedding_provider: Embedding provider for clustering.
        min_occurrences: Minimum # occurrences to be considered for polysemy.
            Default 5 (up from 3) — 3 is too low to distinguish noise from signal.
        min_files: Minimum # distinct files the concept must span. Default 3.
            Without this gate, single-file repeated usage is wrongly tagged poly.
        distance_threshold: DBSCAN eps. Larger = more lenient grouping = fewer
            clusters per concept. Default 0.35.

    Returns:
        Dict mapping concept_name -> list of DefinitionCluster dicts.
        Only entries with >= 1 cluster are returned; a concept with no
        polysemy (one meaning) is still included with a single-element list.
    """
    # Group by name; also track file spread
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    name_files: dict[str, set[str]] = defaultdict(set)
    for occ in occurrences:
        name = occ.get("split_name", "")
        if not (name and occ.get("definition")):
            continue
        by_name[name].append(occ)
        fp = occ.get("context", {}).get("file_path", "")
        if fp:
            name_files[name].add(fp)

    polysemy_results: dict[str, list[dict[str, Any]]] = {}
    skipped_low_occ = 0
    skipped_low_files = 0

    for name, occs in by_name.items():
        if len(occs) < min_occurrences:
            skipped_low_occ += 1
            continue
        if len(name_files[name]) < min_files:
            skipped_low_files += 1
            continue

        definitions = [o["definition"] for o in occs]
        if len(set(definitions)) <= 1:
            polysemy_results[name] = []
            continue

        embeddings = embedding_provider.embed(definitions)
        labels = _dbscan_cluster(embeddings, eps=distance_threshold, min_samples=1)

        n_clusters = int(labels.max()) + 1 if len(labels) > 0 else 0
        if n_clusters <= 1:
            polysemy_results[name] = []
            continue

        clusters: list[dict[str, Any]] = []
        for cid in range(n_clusters):
            indices = np.where(labels == cid)[0]
            cluster_emb = embeddings[indices]
            centroid = cluster_emb.mean(axis=0)
            distances = np.linalg.norm(cluster_emb - centroid, axis=1)
            best_idx = indices[int(np.argmin(distances))]

            cluster = DefinitionCluster(
                canonical_definition=occs[best_idx]["definition"],
                occurrence_ids=[occs[i].get("occurrence_id", "") for i in indices],
                distinctiveness=float(1.0 - float(np.mean(distances))),
            )
            clusters.append(cluster.model_dump())

        polysemy_results[name] = clusters
        logger.info(
            "Polysemy: '%s' has %d meanings (%d occurrences across %d files)",
            name, len(clusters), len(occs), len(name_files[name]),
        )

    total_polysemous = sum(1 for v in polysemy_results.values() if len(v) > 1)
    logger.info(
        "Polysemy analysis: %d concepts checked, %d polysemous "
        "(skipped %d low-frequency, %d low-file-spread)",
        len(polysemy_results), total_polysemous,
        skipped_low_occ, skipped_low_files,
    )
    return polysemy_results
