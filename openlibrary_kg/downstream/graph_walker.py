"""Multi-hop path exploration on the Knowledge Graph.

Given seed concepts (from QueryRewriter), performs BFS over synonym and
co-occurrence edges to discover concepts that are indirectly related to
the issue. Records full paths — not just endpoints — so downstream code
can explain *why* a file was recommended.

Key design decisions:
  - BFS with a priority queue weighted by cumulative path score.
  - Pruning: concepts that appear in >high_freq_ratio of files are dead ends
    (they are infrastructure / stdlib, not domain-specific).
  - Pruning: if a path score drops below min_path_weight, stop expanding.
  - Max 3 hops (configurable). Beyond that, the signal decays to noise.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("openlibrary_kg.downstream.graph_walker")


@dataclass
class Hop:
    """One step in a graph path."""

    concept_name: str
    edge_type: str       # "synonym" | "co-occurrence"
    edge_weight: float   # the raw edge weight from the KG
    via_concept: str     # the concept we came *from*
    track: str = ""      # for synonyms: "naming_variant" | "domain_equivalence"


@dataclass
class GraphPath:
    """A multi-hop path from a seed concept to a destination concept."""

    seed: str                              # starting concept
    seed_weight: float                     # initial relevance weight
    hops: list[Hop] = field(default_factory=list)
    cumulative_weight: float = 0.0

    @property
    def destination(self) -> str:
        if self.hops:
            return self.hops[-1].concept_name
        return self.seed

    @property
    def depth(self) -> int:
        return len(self.hops)

    def edge_types(self) -> list[str]:
        return [h.edge_type for h in self.hops]


@dataclass
class WalkResult:
    """The output of a graph walk."""

    seed_concepts: list[str]
    paths: list[GraphPath] = field(default_factory=list)
    # concept → aggregated weight (across all paths leading to it)
    concept_weights: dict[str, float] = field(default_factory=dict)
    # concept → list of paths that reach it (for explanation)
    concept_paths: dict[str, list[GraphPath]] = field(default_factory=dict)


class GraphWalker:
    """Multi-hop BFS walker on the KG.

    Starts from seed concepts, traverses synonym + co-occurrence edges,
    and returns all reached concepts with their paths and aggregated weights.
    """

    def __init__(
        self,
        kg: dict[str, Any],
        max_hops: int = 3,
        cooccurrence_decay: float = 0.5,
        synonym_track_b_factor: float = 0.2,
        callgraph_decay: float = 0.2,
        min_path_weight: float = 0.01,
        high_freq_file_ratio: float = 0.30,
    ):
        self.kg = kg
        self.max_hops = max_hops
        self.cooccurrence_decay = cooccurrence_decay
        self.synonym_track_b_factor = synonym_track_b_factor
        self.callgraph_decay = callgraph_decay
        self.min_path_weight = min_path_weight
        self.high_freq_file_ratio = high_freq_file_ratio

        # Built on first use (lazy, because walk() is called per issue)
        self._edges_built = False
        self._synonyms: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        self._cooccurrence: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self._callgraph: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self._concept_files: dict[str, set[str]] = defaultdict(set)
        self._total_files: int = 0
        self._high_freq_names: set[str] = set()

    # ------------------------------------------------------------------
    # Lazy index building (reuses data that IssueLocalizer already has,
    # but we keep it self-contained so GraphWalker works standalone.)
    # ------------------------------------------------------------------

    def _ensure_edges(self) -> None:
        if self._edges_built:
            return
        self._edges_built = True

        for rel in self.kg.get("relationships", []):
            rtype = rel.get("relationship_type")
            src = rel.get("source_concept_id", "")
            tgt = rel.get("target_concept_id", "")
            w = float(rel.get("weight", 0.0))
            if not src or not tgt or w <= 0:
                continue

            if rtype == "synonym":
                track = rel.get("metadata", {}).get("track", "naming_variant")
                self._synonyms[src].append((tgt, w, track))
                self._synonyms[tgt].append((src, w, track))
            elif rtype == "co-occurrence":
                self._cooccurrence[src].append((tgt, w))
                self._cooccurrence[tgt].append((src, w))
            elif rtype == "callgraph":
                self._callgraph[src].append((tgt, w))
                self._callgraph[tgt].append((src, w))

        # Build file index for pruning
        for c in self.kg.get("concepts", []):
            name = c.get("canonical_name", "")
            if not name:
                continue
            for occ in c.get("occurrences", []):
                fp = occ.get("context", {}).get("file_path", "")
                if fp:
                    self._concept_files[name].add(fp)

        all_files = {f for fs in self._concept_files.values() for f in fs}
        self._total_files = max(1, len(all_files))

        # Build file → concept count for normalization in ranking.
        # Without this, large files dominate ranking simply because they
        # contain more concepts, regardless of whether those concepts are
        # relevant to the issue.
        self._file_concept_count: dict[str, int] = defaultdict(int)
        for cname, files in self._concept_files.items():
            for fp in files:
                self._file_concept_count[fp] += 1

        threshold = self._total_files * self.high_freq_file_ratio
        for name, files in self._concept_files.items():
            if len(files) > threshold:
                self._high_freq_names.add(name)

        logger.info(
            "GraphWalker: %d synonym pairs, %d co-occurrence pairs, "
            "%d callgraph pairs, "
            "%d high-frequency concepts pruned (threshold=%.0f files)",
            sum(len(v) for v in self._synonyms.values()) // 2,
            sum(len(v) for v in self._cooccurrence.values()) // 2,
            sum(len(v) for v in self._callgraph.values()) // 2,
            len(self._high_freq_names),
            threshold,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def walk(
        self,
        seed_weights: dict[str, float],
        seed_cluster_filters: dict[str, set[str] | None] | None = None,
    ) -> WalkResult:
        """Explore the graph starting from seed concepts.

        Args:
            seed_weights: {concept_name: initial_weight} from QueryRewriter.
            seed_cluster_filters: {concept_name: set_of_occurrence_ids | None}
                from polysemy disambiguation. If a concept has a filter set,
                only those occurrences are counted when mapping to files.

        Returns:
            WalkResult with all paths and aggregated concept weights.
        """
        self._ensure_edges()

        seed_names = list(seed_weights.keys())
        result = WalkResult(seed_concepts=seed_names)

        # Seed concepts get their initial weights
        for name, w in seed_weights.items():
            result.concept_weights[name] = max(
                result.concept_weights.get(name, 0.0), w
            )
            # Zero-hop "path" (just the seed itself)
            path = GraphPath(seed=name, seed_weight=w, cumulative_weight=w)
            result.paths.append(path)
            result.concept_paths.setdefault(name, []).append(path)

        # BFS: frontier is (concept_name, cumulative_weight, path_hops)
        # Each entry: (name, cumulative_weight, hops_list)
        frontier: list[tuple[str, float, list[Hop]]] = [
            (name, w, []) for name, w in seed_weights.items()
        ]

        for hop_num in range(1, self.max_hops + 1):
            if not frontier:
                break

            next_frontier: list[tuple[str, float, list[Hop]]] = []

            for cur_name, cur_weight, cur_hops in frontier:
                # Skip pruned concepts
                if cur_name in self._high_freq_names:
                    continue

                # --- Synonym edges ---
                for tgt, edge_w, track in self._synonyms.get(cur_name, []):
                    if tgt in self._high_freq_names:
                        continue
                    factor = (
                        1.0 if track == "naming_variant"
                        else self.synonym_track_b_factor
                    )
                    gain = cur_weight * edge_w * factor
                    if gain < self.min_path_weight:
                        continue

                    hop = Hop(
                        concept_name=tgt,
                        edge_type="synonym",
                        edge_weight=edge_w,
                        via_concept=cur_name,
                        track=track,
                    )
                    new_hops = cur_hops + [hop]
                    next_frontier.append((tgt, gain, new_hops))

                    # Record
                    result.concept_weights[tgt] = max(
                        result.concept_weights.get(tgt, 0.0), gain,
                    )
                    path = GraphPath(
                        seed=self._seed_of(tgt, new_hops, seed_weights),
                        seed_weight=seed_weights.get(
                            self._seed_of(tgt, new_hops, seed_weights), 0.0,
                        ),
                        hops=new_hops,
                        cumulative_weight=gain,
                    )
                    result.paths.append(path)
                    result.concept_paths.setdefault(tgt, []).append(path)

                # --- Co-occurrence edges ---
                for tgt, edge_w in self._cooccurrence.get(cur_name, []):
                    if tgt in self._high_freq_names:
                        continue
                    gain = cur_weight * edge_w * self.cooccurrence_decay
                    if gain < self.min_path_weight:
                        continue

                    hop = Hop(
                        concept_name=tgt,
                        edge_type="co-occurrence",
                        edge_weight=edge_w,
                        via_concept=cur_name,
                    )
                    new_hops = cur_hops + [hop]
                    next_frontier.append((tgt, gain, new_hops))

                    result.concept_weights[tgt] = max(
                        result.concept_weights.get(tgt, 0.0), gain,
                    )
                    path = GraphPath(
                        seed=self._seed_of(tgt, new_hops, seed_weights),
                        seed_weight=seed_weights.get(
                            self._seed_of(tgt, new_hops, seed_weights), 0.0,
                        ),
                        hops=new_hops,
                        cumulative_weight=gain,
                    )
                    result.paths.append(path)
                    result.concept_paths.setdefault(tgt, []).append(path)

                # --- Call-graph edges (P1: bridge isolated concepts) ---
                for tgt, edge_w in self._callgraph.get(cur_name, []):
                    if tgt in self._high_freq_names:
                        continue
                    gain = cur_weight * edge_w * self.callgraph_decay
                    if gain < self.min_path_weight:
                        continue

                    hop = Hop(
                        concept_name=tgt,
                        edge_type="callgraph",
                        edge_weight=edge_w,
                        via_concept=cur_name,
                    )
                    new_hops = cur_hops + [hop]
                    next_frontier.append((tgt, gain, new_hops))

                    result.concept_weights[tgt] = max(
                        result.concept_weights.get(tgt, 0.0), gain,
                    )
                    path = GraphPath(
                        seed=self._seed_of(tgt, new_hops, seed_weights),
                        seed_weight=seed_weights.get(
                            self._seed_of(tgt, new_hops, seed_weights), 0.0,
                        ),
                        hops=new_hops,
                        cumulative_weight=gain,
                    )
                    result.paths.append(path)
                    result.concept_paths.setdefault(tgt, []).append(path)

            frontier = next_frontier
            logger.debug(
                "Hop %d: %d frontier entries", hop_num, len(frontier),
            )

        logger.info(
            "GraphWalker: %d seeds → %d reached concepts via %d paths "
            "(%d hops max)",
            len(seed_names),
            len(result.concept_weights),
            len(result.paths),
            self.max_hops,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seed_of(
        self, name: str, hops: list[Hop], seeds: dict[str, float]
    ) -> str:
        """The seed concept for a reached concept."""
        if not hops:
            return name
        return hops[0].via_concept if len(hops) == 1 else hops[0].via_concept

    def file_ranking(
        self,
        walk_result: WalkResult,
        occurrences_by_concept: dict[str, list[dict]],
        concept_idf: dict[str, float],
        cluster_filters: dict[str, set[str] | None] | None = None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Convert a WalkResult into a ranked file list.

        For each reached concept, credits its occurrences' files by
        concept_weight × IDF. Occurrences are filtered by cluster if
        a polysemy lock is active.
        """
        file_scores: dict[str, float] = defaultdict(float)
        file_funcs: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        file_matched: dict[str, set[str]] = defaultdict(set)
        file_best_paths: dict[str, list[GraphPath]] = defaultdict(list)

        for name, weight in walk_result.concept_weights.items():
            occs = occurrences_by_concept.get(name, [])
            if not occs:
                continue

            allowed = None
            if cluster_filters:
                allowed = cluster_filters.get(name)

            idf = concept_idf.get(name, 1.0)
            for occ in occs:
                if allowed is not None:
                    oid = occ.get("occurrence_id", "")
                    if oid not in allowed:
                        continue

                fp = occ.get("context", {}).get("file_path", "")
                if not fp:
                    continue
                contrib = weight * idf
                file_scores[fp] += contrib
                file_matched[fp].add(name)

                func = occ.get("context", {}).get("function_name") or ""
                if func:
                    file_funcs[fp][func] += contrib

            # Attach best path for this concept
            paths = walk_result.concept_paths.get(name, [])
            if paths:
                best = max(paths, key=lambda p: p.cumulative_weight)
                file_best_paths[name] = [best]

        # ── Concept-density boost ──────────────────────────────────────
        # Files whose matched concepts are a HIGH fraction of their total
        # concepts receive a multiplier.  Small utility files where 30 %
        # of concepts match should not be drowned by large files where
        # only 15 % match, even when the large file's absolute SUM is
        # higher.
        # ─────────────────────────────────────────────────────────────────
        final_scores: dict[str, float] = {}
        for fp, raw in file_scores.items():
            total = self._file_concept_count.get(fp, 1)
            density = len(file_matched[fp]) / max(1.0, total)
            final_scores[fp] = raw * (1.0 + 0.4 * density)

        ranked = sorted(final_scores.items(), key=lambda kv: kv[1], reverse=True)

        results: list[dict[str, Any]] = []
        for fp, score in ranked[:top_k]:
            funcs = sorted(
                file_funcs[fp].items(), key=lambda kv: kv[1], reverse=True,
            )[:5]
            top_funcs = [{"name": fn, "score": s} for fn, s in funcs]
            top_func, top_func_score = (funcs[0] if funcs else ("", 0.0))
            results.append({
                "file_path": fp,
                "score": round(score, 4),
                "top_function": top_func,
                "top_function_score": round(top_func_score, 4),
                "top_functions": top_funcs,
                "matched_concepts": sorted(file_matched[fp])[:20],
                "num_matched_concepts": len(file_matched[fp]),
            })
        return results
