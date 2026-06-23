"""Semantic entry point: map issue text into the KG concept space.

Replaces naive token matching. Uses embedding similarity between the
issue text and each concept's representative definition to retrieve
top-K candidates, then optionally locks each candidate to a specific
polysemy cluster (from Phase 4).

Why this works when token matching doesn't:
    Issue says "crashes when borrowing limit exceeded"
    → embedding matches concept definitions for "error_handler",
      "loan_limit", "patron_account"
    → none of these concept names appear literally in the issue text,
      but their definitions ("A loan_limit is the maximum number of
      books a patron can borrow simultaneously") are semantically close.

This is a *retrieve* step — it bridges the natural-language / code-identifier
gap before any graph walking happens.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("openlibrary_kg.downstream.query_rewriter")


@dataclass
class ConceptMatch:
    """A single concept matched from an issue, with disambiguation state."""

    concept_name: str
    weight: float                     # confidence / relevance score
    cluster_id: int | None = None     # locked polysemy cluster index, or None
    match_reason: str = ""            # human-readable rationale
    occurrence_filter: set[str] | None = None  # occurrence_ids in the locked cluster


@dataclass
class IssueQuery:
    """The result of rewriting an issue into KG concept space."""

    original_text: str
    matches: list[ConceptMatch] = field(default_factory=list)
    # Quick lookup
    _by_name: dict[str, ConceptMatch] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for m in self.matches:
            self._by_name[m.concept_name] = m

    def get_weight(self, name: str) -> float:
        m = self._by_name.get(name)
        return m.weight if m else 0.0

    def top_names(self, n: int = 20) -> list[str]:
        return [m.concept_name for m in self.matches[:n]]


class QueryRewriter:
    """Translate an issue text into a ranked set of KG concepts.

    Uses a retrieve-then-disambiguate pipeline:
      1. Embed the issue text.
      2. Cosine-rank all concept definitions → top-K candidates.
      3. For each candidate with multiple polysemy clusters, lock to the
         cluster whose canonical_definition is closest to the issue.
      4. Merge with a lightweight token-match signal for cases where the
         issue happens to use the exact concept name.
    """

    def __init__(
        self,
        kg: dict[str, Any],
        embedding_provider: Any | None = None,
        semantic_top_k: int = 50,
        semantic_weight: float = 0.7,
        token_weight: float = 0.3,
        cache_dir: str = "output/.emb_cache",
    ):
        self.kg = kg
        self.embedding_provider = embedding_provider
        self.semantic_top_k = semantic_top_k
        self.semantic_weight = semantic_weight
        self.token_weight = token_weight
        self.cache_dir = Path(cache_dir)

        self._semantic_enabled = embedding_provider is not None
        self._concept_texts: list[str] = []
        self._concept_names: list[str] = []
        self._concept_embeddings: np.ndarray | None = None
        self._token_index: dict[str, set[str]] = {}
        self._stem_index: dict[str, set[str]] = {}

        self._build_representation_texts()
        self._build_token_index()
        if self._semantic_enabled:
            if not self._load_embeddings_cache():
                self._precompute_embeddings()
                self._save_embeddings_cache()
            if not self._load_cluster_embeddings_cache():
                self._precompute_cluster_embeddings()
                self._save_cluster_embeddings_cache()

    # ------------------------------------------------------------------
    # Build concept representation texts
    # ------------------------------------------------------------------

    def _build_representation_texts(self) -> None:
        """For each KG concept, build a single string that describes it.

        Priority: best definition > any definition > split_terms join > name.
        """
        for c in self.kg.get("concepts", []):
            name = c.get("canonical_name", "")
            if not name:
                continue
            text = self._representative_text(c)
            self._concept_texts.append(text)
            self._concept_names.append(name)

        logger.info(
            "QueryRewriter: built representation texts for %d concepts",
            len(self._concept_names),
        )

    @staticmethod
    def _representative_text(concept: dict[str, Any]) -> str:
        name = concept.get("canonical_name", "")
        parts: list[str] = [name]

        # Best definition from definition_clusters
        clusters = concept.get("definition_clusters", []) or []
        best_def = ""
        for cl in clusters:
            d = cl.get("canonical_definition", "")
            if len(d) > len(best_def):
                best_def = d
        if best_def:
            parts.append(best_def)
        else:
            # Fall back to any occurrence definition
            for occ in concept.get("occurrences", []):
                d = occ.get("definition", "")
                if d and len(d) > len(best_def):
                    best_def = d
            if best_def:
                parts.append(best_def)

        # Add split terms for lexical signal
        terms = concept.get("split_terms", []) or []
        if terms:
            parts.append(", ".join(terms))

        # Add a few raw identifiers
        raw = concept.get("all_raw_identifiers", []) or []
        if raw:
            parts.append(", ".join(raw[:5]))

        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Token index (fallback / complementary signal)
    # ------------------------------------------------------------------

    def _build_token_index(self) -> None:
        import re as _re
        _token_re = _re.compile(r"[A-Za-z_][A-Za-z0-9_]+")

        for i, name in enumerate(self._concept_names):
            concept = self.kg["concepts"][i]
            for t in _token_re.findall(name.lower()):
                self._token_index.setdefault(t, set()).add(name)
            for raw in concept.get("all_raw_identifiers", []):
                for t in _token_re.findall(raw.lower()):
                    self._token_index.setdefault(t, set()).add(name)

    # ------------------------------------------------------------------
    # Embedding precomputation
    # ------------------------------------------------------------------

    def _cache_key(self) -> str:
        """Build a short hash from concept names so cache auto-invalidates
        when the KG is rebuilt with different concepts."""
        joined = "|".join(sorted(self._concept_names))
        return hashlib.md5(joined.encode()).hexdigest()[:12]  # noqa: S324

    def _precompute_embeddings(self) -> None:
        if not self._concept_texts:
            self._semantic_enabled = False
            return
        logger.info(
            "QueryRewriter: embedding %d concept texts (this may take 1-2 min on CPU)...",
            len(self._concept_texts),
        )
        try:
            self._concept_embeddings = self.embedding_provider.embed_batch(
                self._concept_texts
            )
            logger.info(
                "QueryRewriter: done — %d concepts embedded, dim=%d",
                len(self._concept_texts),
                self._concept_embeddings.shape[1],
            )
        except Exception as exc:
            logger.error("Failed to precompute concept embeddings: %s", exc)
            self._semantic_enabled = False

    def _save_embeddings_cache(self) -> None:
        """Persist concept embeddings to disk so subsequent runs skip 30 s."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._cache_key()
            np.save(self.cache_dir / f"concept_emb_{key}.npy", self._concept_embeddings)
            # Also save names for cache validation
            with open(self.cache_dir / f"concept_names_{key}.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(self._concept_names))
            # Clean up old cache files from previous KG builds
            for old in self.cache_dir.glob("concept_emb_*.npy"):
                if old.stem != f"concept_emb_{key}":
                    old.unlink(missing_ok=True)
            for old in self.cache_dir.glob("concept_names_*.txt"):
                if old.stem != f"concept_names_{key}":
                    old.unlink(missing_ok=True)
            logger.info("QueryRewriter: saved concept embedding cache (%s)", key)
        except Exception as exc:
            logger.warning("Failed to save concept embedding cache: %s", exc)

    def _load_embeddings_cache(self) -> bool:
        """Try to load cached concept embeddings. Returns True on success."""
        try:
            key = self._cache_key()
            emb_path = self.cache_dir / f"concept_emb_{key}.npy"
            names_path = self.cache_dir / f"concept_names_{key}.txt"
            if not emb_path.exists() or not names_path.exists():
                return False
            cached_names = names_path.read_text(encoding="utf-8").splitlines()
            if cached_names != self._concept_names:
                logger.info("QueryRewriter: concept cache stale (names changed), recomputing")
                return False
            self._concept_embeddings = np.load(emb_path)
            logger.info(
                "QueryRewriter: loaded %d concept embeddings from cache",
                len(self._concept_names),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to load concept embedding cache: %s", exc)
            return False

    def _precompute_cluster_embeddings(self) -> None:
        """Precompute cluster-definition embeddings for ALL polysemous concepts.

        Without this, each issue's _disambiguate_clusters() would call
        embed_batch() separately for each matched polysemous concept
        (91 issues × ~10 concepts = ~900 tiny embedding calls).
        With this, we embed everything once at init time and reuse cached
        vectors at query time (only the issue text is embedded fresh each call).
        """
        concepts_by_name = {c["canonical_name"]: c for c in self.kg.get("concepts", [])}
        # Map: concept_name → (cluster_vecs, occ_id_sets)
        self._cluster_emb_cache: dict[str, tuple[np.ndarray, list[set[str]]]] = {}

        # Collect all cluster definition texts and their metadata
        all_texts: list[str] = []
        plan: list[tuple[str, list[set[str]], int]] = []  # (name, occ_id_sets, start_idx)

        for name in self._concept_names:
            concept = concepts_by_name.get(name)
            if not concept:
                continue
            clusters = concept.get("definition_clusters", []) or []
            if len(clusters) < 2:
                continue
            defs = [c.get("canonical_definition", "") or name for c in clusters]
            occ_sets = [set(c.get("occurrence_ids", [])) for c in clusters]
            base = len(all_texts)
            all_texts.extend(defs)
            plan.append((name, occ_sets, base))

        if not all_texts:
            logger.info("QueryRewriter: no polysemous concepts to cache")
            return

        logger.info(
            "QueryRewriter: embedding %d cluster definitions across %d polysemous concepts...",
            len(all_texts), len(plan),
        )
        try:
            all_vecs = self.embedding_provider.embed_batch(all_texts)
        except Exception as exc:
            logger.error("Failed to precompute cluster embeddings: %s", exc)
            return

        for name, occ_sets, base in plan:
            k = len(occ_sets)
            self._cluster_emb_cache[name] = (all_vecs[base:base + k], occ_sets)

        logger.info(
            "QueryRewriter: cached cluster embeddings for %d polysemous concepts",
            len(self._cluster_emb_cache),
        )

    def _save_cluster_embeddings_cache(self) -> None:
        """Persist cluster embeddings so subsequent runs skip this step."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._cache_key()
            # Save as npz: each concept gets its vectors as a named array
            save_dict: dict[str, np.ndarray] = {}
            metadata: dict[str, list[list[str]]] = {}
            for name, (vecs, occ_sets) in self._cluster_emb_cache.items():
                save_dict[name] = vecs
                metadata[name] = [sorted(s) for s in occ_sets]
            np.savez_compressed(
                self.cache_dir / f"cluster_emb_{key}.npz", **save_dict,
            )
            import json
            with open(self.cache_dir / f"cluster_meta_{key}.json", "w", encoding="utf-8") as f:
                json.dump(metadata, f)
            # Clean old
            for old in self.cache_dir.glob("cluster_emb_*.npz"):
                if old.stem != f"cluster_emb_{key}":
                    old.unlink(missing_ok=True)
            for old in self.cache_dir.glob("cluster_meta_*.json"):
                if old.stem != f"cluster_meta_{key}":
                    old.unlink(missing_ok=True)
            logger.info("QueryRewriter: saved cluster embedding cache (%s)", key)
        except Exception as exc:
            logger.warning("Failed to save cluster embedding cache: %s", exc)

    def _load_cluster_embeddings_cache(self) -> bool:
        """Try to load cached cluster embeddings. Returns True on success."""
        try:
            import json
            key = self._cache_key()
            emb_path = self.cache_dir / f"cluster_emb_{key}.npz"
            meta_path = self.cache_dir / f"cluster_meta_{key}.json"
            if not emb_path.exists() or not meta_path.exists():
                return False
            data = np.load(emb_path, allow_pickle=False)
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            self._cluster_emb_cache = {}
            for name in data.files:
                occ_sets = [set(s) for s in metadata.get(name, [])]
                self._cluster_emb_cache[name] = (data[name], occ_sets)
            logger.info(
                "QueryRewriter: loaded cluster embeddings for %d concepts from cache",
                len(self._cluster_emb_cache),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to load cluster embedding cache: %s", exc)
            self._cluster_emb_cache = {}
            return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def rewrite(self, issue_text: str) -> IssueQuery:
        """Translate issue text into a ranked, disambiguated set of KG concepts."""
        if not issue_text.strip():
            return IssueQuery(original_text=issue_text)

        matches: dict[str, ConceptMatch] = {}

        # Track A: semantic (embedding) retrieval
        if self._semantic_enabled and self._concept_embeddings is not None:
            self._semantic_match(issue_text, matches)

        # Track B: token match (complementary)
        self._token_match(issue_text, matches)

        # Sort by weight descending
        sorted_matches = sorted(
            matches.values(), key=lambda m: m.weight, reverse=True
        )
        top = sorted_matches[: self.semantic_top_k]

        # For each top match, lock polysemy cluster
        if self._semantic_enabled and self._concept_embeddings is not None:
            self._disambiguate_clusters(issue_text, top)

        return IssueQuery(original_text=issue_text, matches=top)

    # ------------------------------------------------------------------
    # Track A: semantic match
    # ------------------------------------------------------------------

    def _semantic_match(self, issue_text: str, out: dict[str, ConceptMatch]) -> None:
        try:
            issue_vec = self.embedding_provider.embed_batch([issue_text])[0]
        except Exception as exc:
            logger.warning("Failed to embed issue text: %s", exc)
            return

        denom = (
            np.linalg.norm(self._concept_embeddings, axis=1)
            * np.linalg.norm(issue_vec)
            + 1e-10
        )
        sims = (self._concept_embeddings @ issue_vec) / denom

        top_indices = np.argsort(sims)[::-1][: self.semantic_top_k]
        for idx in top_indices:
            score = float(sims[idx])
            if score < 0.15:  # very low — skip
                continue
            name = self._concept_names[idx]
            w = score * self.semantic_weight
            if name in out:
                out[name].weight = max(out[name].weight, w)
            else:
                out[name] = ConceptMatch(
                    concept_name=name,
                    weight=w,
                    match_reason=f"embedding similarity {score:.3f}",
                )

    # ------------------------------------------------------------------
    # Track B: token match
    # ------------------------------------------------------------------

    def _token_match(self, issue_text: str, out: dict[str, ConceptMatch]) -> None:
        import re as _re
        from openlibrary_kg.downstream.issue_localization import (
            _light_stem,
            _tokenize,
        )

        tokens = _tokenize(issue_text)
        for t in tokens:
            exact = self._token_index.get(t, set())
            for name in exact:
                gain = (1.0 if t == name else 0.6) * self.token_weight
                if name in out:
                    out[name].weight = max(out[name].weight, gain)
                else:
                    out[name] = ConceptMatch(
                        concept_name=name,
                        weight=gain,
                        match_reason=f"exact token match: '{t}'",
                    )

            stem = _light_stem(t)
            if stem != t:
                stem_hits = self._token_index.get(stem, set())
                for name in stem_hits:
                    if name in exact:
                        continue
                    gain = 0.3 * self.token_weight
                    if name in out:
                        out[name].weight = max(out[name].weight, gain)
                    else:
                        out[name] = ConceptMatch(
                            concept_name=name,
                            weight=gain,
                            match_reason=f"stemmed token match: '{t}'→'{stem}'",
                        )

    # ------------------------------------------------------------------
    # Polysemy disambiguation
    # ------------------------------------------------------------------

    def _disambiguate_clusters(
        self, issue_text: str, matches: list[ConceptMatch]
    ) -> None:
        """Lock each matched concept to the best polysemy cluster.

        Uses precomputed cluster embeddings from init — only the issue text
        is embedded fresh. This avoids ~900 tiny embed_batch() calls across
        91 issues and keeps evaluation interactive.
        """
        if not self._cluster_emb_cache:
            return  # nothing to disambiguate

        try:
            issue_vec = self.embedding_provider.embed_batch([issue_text])[0]
        except Exception:
            return

        for match in matches:
            cached = self._cluster_emb_cache.get(match.concept_name)
            if cached is None:
                continue
            cluster_vecs, occ_sets = cached

            denom = (
                np.linalg.norm(cluster_vecs, axis=1)
                * np.linalg.norm(issue_vec)
                + 1e-10
            )
            sims = (cluster_vecs @ issue_vec) / denom
            best = int(np.argmax(sims))

            match.cluster_id = best
            match.occurrence_filter = occ_sets[best]
            match.match_reason += (
                f" | polysemy cluster {best}/{len(occ_sets)}"
            )
