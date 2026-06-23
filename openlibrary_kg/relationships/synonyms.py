"""Two-track synonym detection.

Track A — "Naming variant" (auto-accept):
    Cosine similarity ≥ naming_variant_threshold (default 0.85). These pairs
    are typically lexical variants of the same concept (e.g.
    `validate_email ↔ valid_email`, `account ↔ accounts`).

Track B — "Domain equivalence" (LLM-gated):
    Cosine similarity in [llm_judge_low, llm_judge_high) (default
    [0.55, 0.85)). The LLM is asked whether the two concepts refer to the
    same kind of domain entity in this codebase. Only YES answers become
    synonym edges. This catches pairs like `user ↔ account` whose surface
    forms differ but mean the same thing in this domain — pairs that pure
    cosine cannot find.

Pairs below `similarity_threshold` (default 0.55) are discarded entirely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from openlibrary_kg.embeddings.base import EmbeddingProvider
from openlibrary_kg.embeddings.similarity import top_k_similar
from openlibrary_kg.llm.base import LLMClient
from openlibrary_kg.llm.prompt_templates import (
    build_synonym_judge_prompts,
    parse_synonym_judgment,
)
from openlibrary_kg.models import Relationship
from openlibrary_kg.utils.caching import DiskCache

logger = logging.getLogger("openlibrary_kg.relationships")


_TRACK_B_CACHE_PREFIX = "SYNJUDGE|"


def _representative_definition(occurrences: list[dict[str, Any]]) -> str:
    """Pick the longest non-empty definition across occurrences as the rep."""
    defs = [
        o.get("definition", "") for o in occurrences
        if o.get("definition")
    ]
    if not defs:
        return ""
    return max(defs, key=len)


def _build_concept_texts(
    concepts: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """Build embedding texts.

    Returns (texts_to_embed, names, definitions) where each is parallel.
    """
    texts: list[str] = []
    names: list[str] = []
    defs: list[str] = []

    for c in concepts:
        name = c.get("canonical_name", c.get("split_name", ""))
        rep_def = _representative_definition(c.get("occurrences", []))
        raw_ids = c.get("all_raw_identifiers", [c.get("split_name", name)])
        parts = [name]
        if rep_def:
            parts.append(rep_def)
        if raw_ids:
            parts.append(", ".join(raw_ids[:3]))
        texts.append(" | ".join(parts))
        names.append(name)
        defs.append(rep_def)

    return texts, names, defs


def detect_synonyms(
    concepts: list[dict[str, Any]],
    embedding_provider: EmbeddingProvider,
    similarity_threshold: float = 0.55,
    naming_variant_threshold: float = 0.85,
    llm_judge_low: float = 0.55,
    llm_judge_high: float = 0.85,
    top_k: int = 20,
    llm_validation: bool = True,
    llm_client: LLMClient | None = None,
    llm_batch_size: int = 20,
    cache_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Detect synonym relationships among concepts using two tracks.

    Args:
        concepts: List of concept dicts (must include canonical_name; ideally
            with occurrences containing `definition` for Track B quality).
        embedding_provider: Embedding provider.
        similarity_threshold: Floor — pairs below this are dropped entirely.
        naming_variant_threshold: Track A cutoff — auto-accept above this.
        llm_judge_low: Track B lower bound (inclusive).
        llm_judge_high: Track B upper bound (exclusive, equals Track A floor).
        top_k: Max candidate pairs to keep per concept after scoring.
        llm_validation: If True, run Track B; if False, only Track A is applied.
        llm_client: Required if llm_validation is True. Async client.
        llm_batch_size: Number of concurrent LLM judgments per batch.
        cache_dir: Where to persist Track B judgments so an interrupted run
            can resume without paying again for already-judged pairs. If
            None, no caching (re-runs pay full cost).

    Returns:
        List of Relationship dicts with `relationship_type="synonym"`.
        Metadata includes "track" ("naming_variant" | "domain_equivalence")
        and the similarity score.
    """
    if len(concepts) < 2:
        return []

    logger.info("Building embedding text for %d concepts", len(concepts))
    texts, names, defs = _build_concept_texts(concepts)
    raw_ids_list = [c.get("all_raw_identifiers", []) for c in concepts]

    logger.info("Computing embeddings")
    embeddings = embedding_provider.embed_batch(texts)

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10
    sim_matrix = (embeddings @ embeddings.T) / (norms @ norms.T)

    top_pairs = top_k_similar(sim_matrix, k=top_k, threshold=similarity_threshold)

    # Bucket candidate pairs into the two tracks.
    track_a_pairs: list[tuple[int, int, float]] = []
    track_b_pairs: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    for i, candidates in enumerate(top_pairs):
        for j, score in candidates:
            if i == j:
                continue
            a, b = (i, j) if i < j else (j, i)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            if score >= naming_variant_threshold:
                track_a_pairs.append((a, b, float(score)))
            elif llm_validation and llm_judge_low <= score < llm_judge_high:
                track_b_pairs.append((a, b, float(score)))

    logger.info(
        "Synonym candidates: Track A (>=%.2f) %d, Track B [%.2f, %.2f) %d",
        naming_variant_threshold, len(track_a_pairs),
        llm_judge_low, llm_judge_high, len(track_b_pairs),
    )

    # Emit Track A relationships immediately.
    relationships: list[dict[str, Any]] = []
    for i, j, score in track_a_pairs:
        rel = Relationship(
            source_concept_id=concepts[i].get("concept_id", names[i]),
            target_concept_id=concepts[j].get("concept_id", names[j]),
            relationship_type="synonym",
            weight=score,
            metadata={
                "source_name": names[i],
                "target_name": names[j],
                "method": "embedding_cosine",
                "track": "naming_variant",
            },
        )
        relationships.append(rel.model_dump())

    # Track B: LLM gate.
    if llm_validation and track_b_pairs:
        if llm_client is None:
            logger.warning(
                "llm_validation=True but no llm_client provided; skipping Track B "
                "(%d candidates not evaluated)",
                len(track_b_pairs),
            )
        else:
            track_b_results = asyncio.run(_run_llm_judgments(
                track_b_pairs, names, raw_ids_list, defs,
                llm_client, llm_batch_size,
                cache_dir=cache_dir,
            ))
            accepted = 0
            for (i, j, score), (is_syn, reason) in zip(track_b_pairs, track_b_results):
                if not is_syn:
                    continue
                accepted += 1
                rel = Relationship(
                    source_concept_id=concepts[i].get("concept_id", names[i]),
                    target_concept_id=concepts[j].get("concept_id", names[j]),
                    relationship_type="synonym",
                    weight=score,
                    metadata={
                        "source_name": names[i],
                        "target_name": names[j],
                        "method": "llm_judged",
                        "track": "domain_equivalence",
                        "llm_reason": reason,
                    },
                )
                relationships.append(rel.model_dump())
            logger.info(
                "Track B: %d / %d candidates accepted by LLM",
                accepted, len(track_b_pairs),
            )

    logger.info("Found %d synonym pairs total", len(relationships))
    return relationships


async def _run_llm_judgments(
    pairs: list[tuple[int, int, float]],
    names: list[str],
    raw_ids_list: list[list[str]],
    defs: list[str],
    llm_client: LLMClient,
    batch_size: int,
    cache_dir: str | None = None,
) -> list[tuple[bool, str]]:
    """Ask the LLM whether each pair is a domain synonym.

    Uses a disk cache (when cache_dir is provided) so that an interrupted
    run can be resumed without re-paying for already-judged pairs. The
    cache key is derived from the prompt content, so changing the prompt
    template automatically invalidates old entries.

    Cache rules:
      - Only **decided** judgments are cached: ("YES"|"NO", reason).
      - LLM failures (empty response) are NOT cached, so they get retried
        on the next run.

    Returns a list of (is_synonym, reason) parallel to `pairs`.
    """
    cache = DiskCache(cache_dir) if cache_dir else None
    total = len(pairs)
    total_batches = (total + batch_size - 1) // batch_size

    # Build all prompts up front so we can do a single cache pass.
    all_prompts: list[tuple[str, str]] = []
    for i, j, _ in pairs:
        all_prompts.append(build_synonym_judge_prompts(
            names[i], raw_ids_list[i], defs[i],
            names[j], raw_ids_list[j], defs[j],
        ))

    # First pass: fill in cached judgments, collect the rest as "to do".
    results: list[tuple[bool, str] | None] = [None] * total
    cache_hits = 0
    todo_indices: list[int] = []

    if cache is not None:
        for idx, (sys_p, usr_p) in enumerate(all_prompts):
            key = f"{_TRACK_B_CACHE_PREFIX}{sys_p}|||{usr_p}"
            cached = cache.get(key)
            if (
                isinstance(cached, dict)
                and "is_synonym" in cached
                and "reason" in cached
            ):
                results[idx] = (bool(cached["is_synonym"]), str(cached["reason"]))
                cache_hits += 1
            else:
                todo_indices.append(idx)
    else:
        todo_indices = list(range(total))

    logger.info(
        "Track B: %d cache hits, %d to judge via LLM (in %d batches)",
        cache_hits, len(todo_indices), (len(todo_indices) + batch_size - 1) // batch_size,
    )

    if not todo_indices:
        return [r if r is not None else (False, "") for r in results]

    # Second pass: send only uncached prompts to the LLM, in batches.
    accepted_so_far = sum(1 for r in results if r and r[0])
    new_batches_total = (len(todo_indices) + batch_size - 1) // batch_size

    for batch_no, start in enumerate(range(0, len(todo_indices), batch_size), 1):
        slice_idx = todo_indices[start:start + batch_size]
        batch_prompts = [all_prompts[i] for i in slice_idx]
        try:
            raw = await llm_client.generate_batch(batch_prompts)
        except Exception as exc:
            logger.error(
                "Synonym-judgment batch %d/%d failed: %s",
                batch_no, new_batches_total, exc,
            )
            raw = [""] * len(batch_prompts)

        batch_accepts = 0
        for idx_in_results, (sys_p, usr_p), resp in zip(
            slice_idx, batch_prompts, raw,
        ):
            judged = parse_synonym_judgment(resp)
            results[idx_in_results] = judged
            if not (resp or "").strip():
                # LLM failure — do NOT cache (lets next run retry).
                continue
            if judged[0]:
                batch_accepts += 1
            if cache is not None:
                key = f"{_TRACK_B_CACHE_PREFIX}{sys_p}|||{usr_p}"
                cache.set(key, {"is_synonym": judged[0], "reason": judged[1]})

        accepted_so_far += batch_accepts
        logger.info(
            "Track B batch %d/%d done: +%d accepted "
            "(running total %d / %d evaluated, %d cached hits at start)",
            batch_no, new_batches_total, batch_accepts,
            accepted_so_far, cache_hits + min(start + batch_size, len(todo_indices)),
            cache_hits,
        )

    return [r if r is not None else (False, "") for r in results]
