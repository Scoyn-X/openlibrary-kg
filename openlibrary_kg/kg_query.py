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

    # ── Agent-native query API ──────────────────────────────────────────
    # These return structured, explainable answers designed for
    # consumption by AI agents (SWE-agent, Codex CLI, Claude Code, etc.)
    # as well as human developers.

    def concept_card(self, name: str) -> dict[str, Any] | None:
        """Structured summary of a concept for Agent consumption.

        Returns a dict with: name, definition, frequency, files, neighbors,
        polysemy clusters — everything an Agent needs to understand what
        this concept means in the codebase.
        """
        ci = self.concepts.get(name)
        if ci is None:
            return None

        neighbors = []
        for nb in sorted(self._adjacency.get(name, set())):
            nb_ci = self.concepts.get(nb)
            nb_def = nb_ci.definition[:80] if nb_ci else ""
            neighbors.append({"name": nb, "definition_preview": nb_def})

        files_list = sorted(ci.files)[:15]

        return {
            "name": ci.name,
            "definition": ci.definition,
            "frequency": ci.frequency,
            "file_count": len(ci.files),
            "files": files_list,
            "neighbors": neighbors,
            "polysemy_clusters": ci.cluster_count,
            "idf": self._idf.get(name, 0.0),
        }

    def explain_path(
        self,
        seed: str,
        target: str,
        max_hops: int = 4,
    ) -> dict[str, Any] | None:
        """Find a reasoning path from one concept to another, with full
        edge annotations.  Returns None if unreachable.

        Agent use: "Why did the KG suggest this file? Walk me through it."
        """
        if seed not in self.concepts or target not in self.concepts:
            return None
        if seed == target:
            return {"path": [seed], "hops": 0, "explanation": "Same concept."}

        # BFS with parent tracking
        from collections import deque
        parent: dict[str, tuple[str, str, float]] = {}  # node -> (from, edge_type, weight)
        queue = deque([seed])
        visited = {seed}

        while queue and len(visited) < 5000:
            cur = queue.popleft()
            cur_hop = 0
            # Count hops from seed
            p = cur
            while p != seed:
                p = parent[p][0]
                cur_hop += 1
            if cur_hop >= max_hops:
                continue

            for nb in self._adjacency.get(cur, set()):
                if nb in visited:
                    continue
                visited.add(nb)
                # Find the edge(s) between cur and nb
                for e in self._edges:
                    if (e.source == cur and e.target == nb) or (e.source == nb and e.target == cur):
                        parent[nb] = (cur, e.edge_type, e.weight)
                        break
                else:
                    parent[nb] = (cur, "unknown", 0.0)

                if nb == target:
                    # Reconstruct path
                    path = [target]
                    n = target
                    while n != seed:
                        n = parent[n][0]
                        path.append(n)
                    path.reverse()

                    # Build explanation
                    steps = []
                    for i in range(len(path) - 1):
                        a, b = path[i], path[i + 1]
                        _, etype, ew = parent[b]
                        ci_a = self.concepts.get(a)
                        ci_b = self.concepts.get(b)
                        steps.append({
                            "from": a,
                            "to": b,
                            "edge_type": etype,
                            "edge_weight": round(ew, 3),
                            "from_def": ci_a.definition[:100] if ci_a else "",
                            "to_def": ci_b.definition[:100] if ci_b else "",
                        })

                    return {
                        "path": path,
                        "hops": len(steps),
                        "steps": steps,
                        "explanation": " → ".join(
                            f"{s['from']}-[{s['edge_type']}]->{s['to']}"
                            for s in steps
                        ),
                    }

                queue.append(nb)

        return None

    def reverse_impact(
        self,
        file_path: str,
        radius: int = 1,
    ) -> dict[str, Any]:
        """What would be affected if a file were modified?

        Agent use: "Before I edit core/lending.py, what else should I check?"

        Returns concepts in this file, their 1-hop neighbors, and all
        files containing those neighbor concepts.
        """
        fp_norm = self._norm_path(file_path)
        # Find matching file
        matched_fp = fp_norm
        for f in self._file_concepts:
            if fp_norm in f or f.endswith(fp_norm):
                matched_fp = f
                break

        local_concepts = set(self._file_concepts.get(matched_fp, set()))

        # Expand to neighbors
        affected_concepts = set(local_concepts)
        for _ in range(radius):
            new = set()
            for c in affected_concepts:
                new |= self._adjacency.get(c, set())
            affected_concepts |= new

        # Map affected concepts to files
        affected_files: dict[str, set[str]] = defaultdict(set)
        for c in affected_concepts:
            for fp in self._concept_files.get(c, set()):
                affected_files[self._norm_path(fp)].add(c)

        # Classify: direct (local) vs indirect (neighbor reachable)
        direct_files = {}
        indirect_files = {}
        for fp, concepts in affected_files.items():
            overlap = concepts & local_concepts
            if overlap:
                direct_files[fp] = sorted(overlap)[:10]
            elif fp_norm not in fp and fp not in fp_norm:
                indirect_files[fp] = sorted(concepts)[:10]

        return {
            "file": fp_norm,
            "local_concepts": sorted(local_concepts)[:20],
            "local_count": len(local_concepts),
            "affected_concepts_total": len(affected_concepts),
            "directly_affected_files": [
                {"path": fp, "shared_concepts": cs}
                for fp, cs in sorted(direct_files.items())[:15]
            ],
            "indirectly_affected_files": [
                {"path": fp, "via_concepts": cs}
                for fp, cs in sorted(indirect_files.items())[:15]
            ],
        }

    def impact_report(
        self,
        file_paths: list[str],
        radius: int = 1,
    ) -> dict[str, Any]:
        """Agent-facing structured impact report for multiple files.

        Combines reverse_impact for each file, deduplicates, and produces
        a single report suitable for Agent consumption before making edits.
        """
        per_file = {}
        all_affected: set[str] = set()

        for fp in file_paths:
            report = self.reverse_impact(fp, radius=radius)
            per_file[fp] = report
            for item in report["directly_affected_files"]:
                all_affected.add(item["path"])
            for item in report["indirectly_affected_files"]:
                all_affected.add(item["path"])

        # Remove the input files themselves from 'affected'
        normed_inputs = {self._norm_path(f) for f in file_paths}
        truly_affected = []
        for af in sorted(all_affected):
            afn = self._norm_path(af)
            if afn not in normed_inputs and not any(
                afn.endswith(inf) or inf.endswith(afn)
                for inf in file_paths
            ):
                truly_affected.append(af)

        return {
            "files_changed": file_paths,
            "files_potentially_affected": truly_affected[:20],
            "total_concepts_involved": sum(
                r["affected_concepts_total"] for r in per_file.values()
            ),
            "per_file_detail": per_file,
            "recommendation": (
                f"修改 {len(file_paths)} 个文件，"
                f"可能影响 {len(truly_affected)} 个相关文件，"
                f"涉及 {sum(r['affected_concepts_total'] for r in per_file.values())} 个概念。"
                f"建议运行相关测试并审查受影响文件的调用链。"
            ),
        }

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
