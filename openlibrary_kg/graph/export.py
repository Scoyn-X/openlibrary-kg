"""KG export utilities: JSON (primary) and NetworkX/GEXF formats."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openlibrary_kg.models import KnowledgeGraph

logger = logging.getLogger("openlibrary_kg.graph")


def export_json(kg: KnowledgeGraph, filepath: str | Path, pretty: bool = True) -> None:
    """Export the knowledge graph as JSON in node-link format."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = kg.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        else:
            json.dump(data, f, ensure_ascii=False, default=str)
    logger.info("Exported KG JSON to %s", path)


def export_gexf(kg: KnowledgeGraph, filepath: str | Path) -> None:
    """Export the knowledge graph as GEXF (for Gephi visualization)."""
    try:
        import networkx as nx
    except ImportError:
        logger.warning("networkx not installed; skipping GEXF export")
        return

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    G = nx.Graph()

    # Add concept nodes
    for c in kg.concepts:
        G.add_node(
            c.concept_id,
            label=c.canonical_name,
            frequency=c.frequency,
            num_occurrences=len(c.occurrences),
            num_raw_identifiers=len(c.all_raw_identifiers),
            has_polysemy=len(c.definition_clusters) > 1,
        )

    # Add relationship edges
    for rel in kg.relationships:
        G.add_edge(
            rel.source_concept_id,
            rel.target_concept_id,
            type=rel.relationship_type,
            weight=rel.weight,
        )

    nx.write_gexf(G, str(path))
    logger.info("Exported KG GEXF to %s (%d nodes, %d edges)",
                 path, G.number_of_nodes(), G.number_of_edges())


def to_networkx(kg: KnowledgeGraph) -> Any:
    """Build a NetworkX graph from the knowledge graph.

    Returns:
        networkx.Graph with all nodes and edges.
    """
    import networkx as nx

    G = nx.Graph()
    for c in kg.concepts:
        G.add_node(c.concept_id, **c.model_dump())
    for rel in kg.relationships:
        G.add_edge(
            rel.source_concept_id,
            rel.target_concept_id,
            **rel.model_dump(),
        )
    return G


def export_networkx_pickle(kg: KnowledgeGraph, filepath: str | Path) -> None:
    """Export the KG as a NetworkX pickle for Python analysis."""
    import pickle

    G = to_networkx(kg)
    path = Path(filepath)
    with open(path, "wb") as f:
        pickle.dump(G, f)
    logger.info("Exported NetworkX pickle to %s", path)
