"""Issue localization on the openlibrary KG.

Given an issue's title+body, rank source files by how likely they contain
the change required to fix it.

Pipeline (rewritten, v2):
  1. **Semantic entry** — QueryRewriter maps the issue text into KG concept
     space via embedding similarity + polysemy disambiguation. This replaces
     the old naive token-match _seed_concepts.
  2. **Multi-hop graph walk** — GraphWalker explores synonym + co-occurrence
     edges up to 3 hops, recording full paths (not just endpoints).
  3. **File ranking** — Concepts reached by the walk are weighted by path
     score × IDF, and files are ranked by aggregated concept contribution.
  4. **Skeleton generation** — For top-ranked files, SkeletonGenerator
     produces a concept-focused code view (imports + relevant functions).
  5. **Path explanation** — Each file recommendation is accompanied by a
     human-readable explanation of *why* it was matched.

Backward compatibility:
  - localize() returns the same dict schema (file_path, score, top_function,
    top_functions, matched_concepts) plus new fields (skeleton, explanation).
  - evaluate() signature unchanged.
  - Falls back to token match when no embedding provider is available.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("openlibrary_kg.downstream.issue_localization")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")

# Common English stopwords. Excludes domain-relevant words that may overlap
# with code identifiers (e.g. we KEEP "user", "page", "list" because they
# are legitimate concept names; we drop pure function words).
ENGLISH_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of",
    "to", "in", "on", "at", "by", "for", "with", "without", "into",
    "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "doing", "done",
    "can", "cant", "cannot", "could", "couldnt", "should", "shouldnt",
    "would", "wouldnt", "will", "wont", "shall", "may", "might", "must",
    "this", "that", "these", "those", "there", "here", "where",
    "when", "why", "how", "what", "which", "who", "whom", "whose",
    "i", "me", "my", "we", "us", "our", "you", "your", "yours",
    "he", "him", "his", "she", "her", "it", "its", "they", "them",
    "their", "theirs",
    "not", "no", "yes", "so", "too", "very", "just", "only", "also",
    "any", "some", "all", "none", "each", "every", "more", "most",
    "less", "few", "many", "much", "such", "same", "other", "another",
    "than", "while", "during", "since", "until", "before", "after",
    "above", "below", "up", "down", "out", "off", "over", "under",
    "again", "still", "ever", "never", "now", "then", "always",
    "about", "around", "through", "between", "among", "against",
    "but", "however", "though", "although", "because", "due",
    "issue", "bug", "feature", "request", "problem", "error",
    "doesnt", "isnt", "wasnt", "arent", "wont", "didnt", "havent",
    "please", "trying", "tried", "want", "need", "see", "seen",
    "use", "using", "used",
    "t", "s", "re", "ve", "ll", "m", "d",
    "steps", "reproduce", "expected", "actual", "behavior",
    "version", "environment", "log", "logs", "traceback", "stack",
})


def _light_stem(token: str) -> str:
    """Light morphological stemming for code-token matching.

    Goal: bring `logging / logins / users / accounts` close to their KG
    counterparts `login / user / account` without over-conflating.
    """
    t = token.lower()
    if len(t) <= 3:
        return t
    for suf in ("ing", "ies", "ied", "es", "s", "ed"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            base = t[:-len(suf)]
            if suf == "ies":
                return base + "y"
            return base
    return t


def _tokenize(text: str) -> list[str]:
    """Tokenize issue text: lower, length>=2, drop English stopwords."""
    if not text:
        return []
    out: list[str] = []
    for t in _TOKEN_RE.findall(text):
        lo = t.lower()
        if len(lo) < 2:
            continue
        if lo in ENGLISH_STOPWORDS:
            continue
        out.append(lo)
    return out


class IssueLocalizer:
    """Maps an issue to ranked file candidates via the KG.

    Uses semantic entry (embedding) + multi-hop graph walk + skeleton
    generation for precise, explainable file recommendations.
    """

    def __init__(
        self,
        kg_path: str | Path = "output/phase_6_knowledge_graph.json",
        kg_query: Any | None = None,                  # pre-built KGQuery (optional)
        synonym_track_b_factor: float = 0.5,
        cooccurrence_decay: float = 0.5,
        top_functions_per_file: int = 5,
        max_clusters_for_disambiguation: int = 30,
        embedding_provider: Any | None = None,
        max_hops: int = 3,
        semantic_top_k: int = 50,
    ):
        # ── Load KG (via platform layer or raw JSON) ─────────────
        if kg_query is not None:
            self._kg_query = kg_query
            self.kg = kg_query._raw
        else:
            from openlibrary_kg.kg_query import KGQuery
            self._kg_query = KGQuery(str(kg_path))
            self.kg = self._kg_query._raw

        self.synonym_track_b_factor = synonym_track_b_factor
        self.cooccurrence_decay = cooccurrence_decay
        self.top_functions_per_file = top_functions_per_file
        self.max_clusters_for_disambiguation = max_clusters_for_disambiguation
        self.embedding_provider = embedding_provider
        self.max_hops = max_hops
        self.semantic_top_k = semantic_top_k

        # ── Build indexes ────────────────────────────────────────
        self.concepts_by_name: dict[str, dict] = {}
        self.occurrences_by_concept: dict[str, list[dict]] = defaultdict(list)
        self.concept_files: dict[str, set[str]] = defaultdict(set)

        for c in self.kg.get("concepts", []):
            name = c.get("canonical_name", "")
            if not name:
                continue
            self.concepts_by_name[name] = c
            for occ in c.get("occurrences", []):
                self.occurrences_by_concept[name].append(occ)
                fp = occ.get("context", {}).get("file_path", "")
                if fp:
                    self.concept_files[name].add(fp)

        # IDF
        all_files = {f for fs in self.concept_files.values() for f in fs}
        total_files = max(1, len(all_files))
        self.concept_idf: dict[str, float] = {}
        for name, files in self.concept_files.items():
            self.concept_idf[name] = math.log(
                1 + total_files / max(1, len(files))
            )

        # ── Semantic entry module ─────────────────────────────────
        from openlibrary_kg.downstream.query_rewriter import QueryRewriter
        self.query_rewriter = QueryRewriter(
            self.kg,
            embedding_provider=embedding_provider,
            semantic_top_k=semantic_top_k,
        )
        self._semantic_enabled = self.query_rewriter._semantic_enabled

        # ── Multi-hop graph walker ─────────────────────────────────
        from openlibrary_kg.downstream.graph_walker import GraphWalker
        self.graph_walker = GraphWalker(
            self.kg,
            max_hops=max_hops,
            cooccurrence_decay=cooccurrence_decay,
            synonym_track_b_factor=synonym_track_b_factor,
        )

        # ── Skeleton generator ─────────────────────────────────────
        from openlibrary_kg.downstream.skeleton_generator import (
            SkeletonGenerator,
        )
        self.skeleton_gen = SkeletonGenerator(self.kg)

        logger.info(
            "IssueLocalizer ready: %d concepts, %d files, semantic=%s, "
            "max_hops=%d",
            len(self.concepts_by_name),
            total_files,
            self._semantic_enabled,
            max_hops,
        )

    # ==================================================================
    # Main entry point
    # ==================================================================

    def localize(
        self,
        title: str,
        body: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Rank candidate files for the given issue.

        Returns list of dicts:
            {
              "file_path": str,
              "score": float,
              "top_function": str,
              "top_function_score": float,
              "top_functions": [{"name": ..., "score": ...}, ...],
              "matched_concepts": [...],
              "skeleton": str,           # NEW: concept-focused code skeleton
              "explanation": str,        # NEW: why this file was matched
            }
        """
        issue_text = (title or "") + "\n" + (body or "")
        if not issue_text.strip():
            return []

        # ── Short-title enrichment: extract key signals from body ──
        issue_text = self._enrich_short_title(issue_text)

        # ── Step 1: Semantic entry — issue → KG concepts ─────────
        try:
            issue_query = self.query_rewriter.rewrite(issue_text)
        except Exception as exc:
            logger.error("QueryRewriter failed: %s; falling back to token match", exc)
            issue_query = self._fallback_seed_concepts(issue_text)

        if not issue_query.matches:
            logger.debug("No concepts matched for issue: %s", title[:80])
            return []

        # ── Step 2: Multi-hop graph walk ─────────────────────────
        seed_weights = {m.concept_name: m.weight for m in issue_query.matches}
        cluster_filters: dict[str, set[str] | None] = {}
        for m in issue_query.matches:
            if m.occurrence_filter is not None:
                cluster_filters[m.concept_name] = m.occurrence_filter

        try:
            walk_result = self.graph_walker.walk(seed_weights)
        except Exception as exc:
            logger.error("GraphWalker failed: %s", exc)
            return []

        # ── Step 3: File ranking ─────────────────────────────────
        ranked = self.graph_walker.file_ranking(
            walk_result,
            self.occurrences_by_concept,
            self.concept_idf,
            cluster_filters=cluster_filters,
            top_k=top_k,
        )

        # ── Step 4: Skeleton + Explanation ────────────────────────
        from openlibrary_kg.downstream.path_explainer import (
            explain_file_ranking,
        )

        explanation = explain_file_ranking(
            ranked, walk_result, self.concepts_by_name, max_files=top_k,
        )

        for entry in ranked:
            entry["skeleton"] = self.skeleton_gen.generate(
                entry["file_path"],
                entry.get("matched_concepts", []),
            )
            entry["explanation"] = explanation

        return ranked

    # ==================================================================
    # Short-title enrichment
    # ==================================================================

    @staticmethod
    def _enrich_short_title(issue_text: str, min_title_tokens: int = 5) -> str:
        """When the issue title carries zero information, replace it with
        the first substantive sentence from the body.

        Issues like ``## Title:`` have bodies that start with a real
        summary line (e.g. "Import API rejects differentiable records
        when other metadata is missing").  Using that as the title gives
        embedding a concrete query instead of noise.
        """
        head, _, tail = issue_text.partition("\n")
        head_tokens = [t for t in _TOKEN_RE.findall(head.lower()) if len(t) >= 3]
        if len(head_tokens) >= min_title_tokens:
            return issue_text

        # Take the first non-empty, non-heading line from body as real title
        for line in tail.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # This is a real sentence — use it as the title
            return stripped + "\n\n" + issue_text

        return issue_text

    # ==================================================================
    # Fallback: token match (when embedding provider is unavailable)
    # ==================================================================

    def _fallback_seed_concepts(self, issue_text: str) -> Any:
        """Legacy token-match path, used only when QueryRewriter fails."""
        from openlibrary_kg.downstream.query_rewriter import (
            ConceptMatch,
            IssueQuery,
        )

        tokens = _tokenize(issue_text)
        matches: dict[str, ConceptMatch] = {}

        for t in tokens:
            # Match against concept names and raw identifiers
            for name, concept in self.concepts_by_name.items():
                if t == name.lower():
                    w = 1.0
                elif t in (name.lower() for _ in [name]):
                    w = 0.8
                else:
                    raw_ids = concept.get("all_raw_identifiers", [])
                    if any(t == ri.lower() for ri in raw_ids):
                        w = 0.6
                    else:
                        continue
                if name in matches:
                    matches[name].weight = max(matches[name].weight, w)
                else:
                    matches[name] = ConceptMatch(
                        concept_name=name,
                        weight=w,
                        match_reason=f"token match: '{t}'",
                    )

        sorted_matches = sorted(
            matches.values(), key=lambda m: m.weight, reverse=True,
        )
        return IssueQuery(
            original_text=issue_text,
            matches=sorted_matches[: self.semantic_top_k],
        )


# ======================================================================
# Evaluation (signature unchanged for backward compatibility)
# ======================================================================

def evaluate(
    localizer: IssueLocalizer,
    ground_truth_path: str | Path,
    top_k: int = 10,
    level: str = "both",
    per_issue_out: str | Path | None = None,
) -> dict[str, float]:
    """Compute Recall@K and MRR over a ground-truth set.

    Function-level matching considers ALL top-N functions per file,
    not just the highest-scoring one.
    """
    with open(ground_truth_path, encoding="utf-8") as f:
        records = json.load(f)

    def _normalize(p: str) -> str:
        p = p.replace("\\", "/")
        for prefix in ("openlibrary/openlibrary/", "openlibrary/"):
            if p.startswith(prefix):
                return p[len(prefix):]
        m = re.search(r"openlibrary/openlibrary/(.+)$", p)
        if m:
            return m.group(1)
        m = re.search(r"openlibrary/(.+)$", p)
        if m:
            return m.group(1)
        return p

    def _file_match(pred: str, gt: str) -> bool:
        return pred.endswith(gt) or gt.endswith(pred)

    file_hits = 0
    func_hits = 0
    file_mrr_sum = 0.0
    func_mrr_sum = 0.0
    n = 0
    n_with_funcs = 0
    per_issue: list[dict] = []
    failures = 0

    for rec in records:
        if "problem_statement" in rec and rec["problem_statement"]:
            text = rec["problem_statement"]
            head, _, tail = text.partition("\n")
            title, body = head, tail
        else:
            title = rec.get("title", "")
            body = rec.get("body", "")

        try:
            ranked = localizer.localize(title, body, top_k=top_k)
        except Exception as exc:
            logger.error(
                "Failed to localize issue %s: %s",
                rec.get("instance_id", rec.get("issue_number", "?")),
                exc,
            )
            ranked = []
            failures += 1

        n += 1

        gt_files = {_normalize(f) for f in rec.get("changed_files", [])}
        gt_funcs = {
            (_normalize(g["file"]), g["function"])
            for g in rec.get("changed_functions", [])
            if g.get("file") and g.get("function")
        }
        if gt_funcs:
            n_with_funcs += 1

        file_rank, func_rank = None, None

        if ranked and level in ("file", "both"):
            for i, r in enumerate(ranked, 1):
                pred = _normalize(r["file_path"])
                if any(_file_match(pred, g) for g in gt_files):
                    file_rank = i
                    file_hits += 1
                    file_mrr_sum += 1.0 / i
                    break

        if ranked and level in ("function", "both") and gt_funcs:
            for i, r in enumerate(ranked, 1):
                pred_f = _normalize(r["file_path"])
                pred_func_names = {
                    tf["name"] for tf in r.get("top_functions", [])
                }
                if not pred_func_names:
                    continue
                matched = False
                for gt_f, gt_func in gt_funcs:
                    if not _file_match(pred_f, gt_f):
                        continue
                    if gt_func in pred_func_names:
                        matched = True
                        break
                if matched:
                    func_rank = i
                    func_hits += 1
                    func_mrr_sum += 1.0 / i
                    break

        if per_issue_out is not None:
            per_issue.append({
                "instance_id": rec.get("instance_id", ""),
                "title": title[:120],
                "gt_files": sorted(gt_files),
                "gt_functions": sorted(
                    [f"{gf}::{gn}" for gf, gn in gt_funcs]
                ),
                "predicted_files": [
                    _normalize(r["file_path"]) for r in (ranked or [])
                ],
                "file_rank": file_rank,
                "func_rank": func_rank,
            })

    result: dict[str, float] = {
        "method": "KG-walk-v2",
        "n": float(n),
        "top_k": float(top_k),
        "failures": float(failures),
    }
    if level in ("file", "both"):
        result["file_recall_at_k"] = file_hits / n if n else 0.0
        result["file_mrr"] = file_mrr_sum / n if n else 0.0
    if level in ("function", "both"):
        denom = n_with_funcs or 1
        result["function_recall_at_k"] = func_hits / denom if n_with_funcs else 0.0
        result["function_mrr"] = (
            func_mrr_sum / denom if n_with_funcs else 0.0
        )
        result["n_with_function_gt"] = float(n_with_funcs)

    if per_issue_out is not None:
        out_path = Path(per_issue_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {"summary": result, "per_issue": per_issue},
                f, ensure_ascii=False, indent=2,
            )
        logger.info("Per-issue dump written to %s", out_path)

    return result
