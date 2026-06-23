"""General-purpose KG query platform.

Decouples the knowledge graph from any single downstream task.
Every consumer — issue localizer, README generator, impact analyser —
queries the KG through this uniform interface.

Design principles:
  - Read-only: never mutates the graph (task-specific state lives in callers).
  - Index-once: all lookups pre-built at init, O(1) at query time.
  - No task bias: the API describes *what the graph contains*, not *how to
    use it*.  Interpretation belongs to the caller.
"""

from __future__ import annotations

import json
import math
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("openlibrary_kg.kg_query")


# ── Public types ────────────────────────────────────────────────────────

@dataclass
class ConceptInfo:
    """Read-only snapshot of one KG concept."""
    name: str
    frequency: int
    files: frozenset[str]
    definition: str                            # best definition text
    all_raw_identifiers: list[str] = field(default_factory=list)
    cluster_count: int = 1                     # number of polysemy clusters


@dataclass
class EdgeInfo:
    """One relationship edge."""
    source: str
    target: str
    edge_type: str                             # synonym | co-occurrence
    weight: float


# ── Main class ───────────────────────────────────────────────────────────


class KGQuery:
    """Read-only, indexed view of the full knowledge graph."""

    def __init__(self, kg_path: str | Path):
        with open(kg_path, encoding="utf-8") as f:
            self._raw = json.load(f)

        self.concepts: dict[str, ConceptInfo] = {}
        self._concept_files: dict[str, frozenset[str]] = {}
        self._file_concepts: dict[str, frozenset[str]] = {}
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._edges: list[EdgeInfo] = []
        self._idf: dict[str, float] = {}

        self._build()
        logger.info(
            "KGQuery ready: %d concepts, %d edges, %d files",
            len(self.concepts), len(self._edges), len(self._file_concepts),
        )

    # ── Index building ───────────────────────────────────────────────────

    def _build(self) -> None:
        # --- Concepts ---
        for c in self._raw.get("concepts", []):
            name = c.get("canonical_name", "")
            if not name:
                continue
            files: set[str] = set()
            best_def = ""
            for occ in c.get("occurrences", []):
                fp = occ.get("context", {}).get("file_path", "")
                if fp:
                    files.add(self._norm_path(fp))
                d = occ.get("definition", "")
                if len(d) > len(best_def):
                    best_def = d
            # Also check definition_clusters for canonical def
            for cl in c.get("definition_clusters", []):
                d = cl.get("canonical_definition", "")
                if len(d) > len(best_def):
                    best_def = d

            n_clusters = len(c.get("definition_clusters", []) or [])
            if n_clusters < 1:
                n_clusters = 1

            frozen = frozenset(files)
            self.concepts[name] = ConceptInfo(
                name=name,
                frequency=c.get("frequency", 0),
                files=frozen,
                definition=best_def,
                all_raw_identifiers=c.get("all_raw_identifiers", []),
                cluster_count=max(n_clusters, 1),
            )
            self._concept_files[name] = frozen
            for fp in frozen:
                self._file_concepts.setdefault(fp, frozenset()).union({name})

        # Rebuild file_concepts properly
        file_to_cons: dict[str, set[str]] = defaultdict(set)
        for cname, files in self._concept_files.items():
            for fp in files:
                file_to_cons[fp].add(cname)
        self._file_concepts = {fp: frozenset(cs) for fp, cs in file_to_cons.items()}

        # --- Edges ---
        for rel in self._raw.get("relationships", []):
            src = rel.get("source_concept_id", "")
            tgt = rel.get("target_concept_id", "")
            rtype = rel.get("relationship_type", "")
            w = float(rel.get("weight", 0.0))
            if not src or not tgt or w <= 0:
                continue
            self._edges.append(EdgeInfo(source=src, target=tgt, edge_type=rtype, weight=w))
            self._adjacency[src].add(tgt)
            self._adjacency[tgt].add(src)

        # --- IDF ---
        total_files = max(1, len(self._file_concepts))
        for name, files in self._concept_files.items():
            self._idf[name] = math.log(1.0 + total_files / max(1, len(files)))

    # ── Query API ─────────────────────────────────────────────────────────

    def get_concept(self, name: str) -> ConceptInfo | None:
        """Look up a single concept by canonical name."""
        return self.concepts.get(name)

    def concept_exists(self, name: str) -> bool:
        """Quick membership check."""
        return name in self.concepts

    def get_files(self, concept_name: str) -> frozenset[str]:
        """Which files contain occurrences of this concept?"""
        return self._concept_files.get(concept_name, frozenset())

    def get_concepts_in_file(self, file_path: str) -> frozenset[str]:
        """Which concepts appear in this file?"""
        # Normalise the incoming path
        fp = self._norm_path(file_path)
        for f, cs in self._file_concepts.items():
            if fp in f or f.endswith(fp) or fp.endswith(f):
                return cs
        return self._concept_files.get(fp, frozenset())

    def get_neighbors(self, concept_name: str) -> frozenset[str]:
        """All concepts directly connected to this one."""
        return frozenset(self._adjacency.get(concept_name, set()))

    def get_idf(self, concept_name: str) -> float:
        """Inverse document frequency for this concept."""
        return self._idf.get(concept_name, 1.0)

    def bfs(
        self,
        seeds: dict[str, float],
        max_hops: int = 3,
        min_weight: float = 0.01,
    ) -> dict[str, float]:
        """Multi-hop breadth-first search from seed concepts.

        Args:
            seeds: {concept_name: initial_weight}
            max_hops: maximum traversal depth.
            min_weight: stop expanding when cumulative weight drops below this.

        Returns:
            {concept_name: aggregated_weight} for all reached concepts.
        """
        reached: dict[str, float] = dict(seeds)
        frontier = list(seeds.items())

        for _hop in range(max_hops):
            if not frontier:
                break
            next_frontier: list[tuple[str, float]] = []
            for cur_name, cur_w in frontier:
                for nb in self._adjacency.get(cur_name, set()):
                    gain = cur_w * 0.5  # decay
                    if gain < min_weight:
                        continue
                    prev = reached.get(nb, 0.0)
                    if gain > prev:
                        reached[nb] = gain
                        next_frontier.append((nb, gain))
            frontier = next_frontier

        return reached

    def subgraph(
        self,
        concept_names: set[str],
        radius: int = 1,
    ) -> tuple[set[str], set[tuple[str, str, str]]]:
        """Extract the induced subgraph around a set of concepts.

        Returns:
            (nodes, edges) where edges are (source, target, type).
        """
        nodes = set(concept_names)
        if radius > 0:
            for _ in range(radius):
                new: set[str] = set()
                for n in nodes:
                    new |= self._adjacency.get(n, set())
                nodes |= new

        edge_set: set[tuple[str, str, str]] = set()
        for e in self._edges:
            if e.source in nodes and e.target in nodes:
                edge_set.add((e.source, e.target, e.edge_type))

        return nodes, edge_set

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _norm_path(fp: str) -> str:
        fp = fp.replace("\\", "/")
        for prefix in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
            if prefix in fp:
                return fp.split(prefix, 1)[1]
        return fp

    def __repr__(self) -> str:
        return (
            f"KGQuery(concepts={len(self.concepts)}, "
            f"edges={len(self._edges)}, files={len(self._file_concepts)})"
        )
