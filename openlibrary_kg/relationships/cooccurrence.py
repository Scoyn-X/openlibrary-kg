"""Co-occurrence analysis with subdomain partition.

A pair of concepts that appears together in the same function within the
same openlibrary subpackage (e.g. both in `accounts/...`) is far more
meaningful than a pair that appears together "in the same module" only
because both happen to be `import` statements at the top of two unrelated
files. To reflect this, we:

  1. Treat module-level co-occurrence (no enclosing class or function)
     as noisy and optionally drop it (`drop_module_level_context`).
  2. Tag each context with its openlibrary subdomain (top-level subpackage
     under `openlibrary/openlibrary/`), and apply `cross_subdomain_factor`
     (default 0.3) to pairs that span subdomains. Same-subdomain pairs
     keep their full Jaccard weight.

Result: stdlib-import noise drops out, domain-coherent pairs rise.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from typing import Any

from openlibrary_kg.models import Relationship

logger = logging.getLogger("openlibrary_kg.relationships")


# Match: ".../openlibrary/openlibrary/<subdomain>/..."
_SUBDOMAIN_RE = re.compile(r"openlibrary/openlibrary/([^/]+)")


def _subdomain_of(file_path: str) -> str:
    """Extract the openlibrary subdomain (top-level subpackage) for a file.

    Returns the subpackage name (e.g. "accounts", "core", "coverstore",
    "plugins", "catalog", "solr", "admin") or "_other" when path is unknown.
    """
    norm = file_path.replace("\\", "/")
    m = _SUBDOMAIN_RE.search(norm)
    if not m:
        return "_other"
    sub = m.group(1)
    # Strip .py extension if the match landed on a leaf module (api.py, app.py)
    if sub.endswith(".py"):
        return sub[:-3]
    return sub


def _build_context_sets(
    occurrences: list[dict[str, Any]],
    drop_module_level: bool,
) -> tuple[
    dict[tuple[str, str, str], set[str]],
    dict[tuple[str, str, str], str],
]:
    """Group concepts by enclosing context. Returns (context→concepts, context→subdomain)."""
    context_concepts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    context_subdomain: dict[tuple[str, str, str], str] = {}

    for occ in occurrences:
        ctx = occ.get("context", {})
        name = occ.get("split_name", "")
        if not name or not ctx:
            continue

        class_name = ctx.get("class_name") or ""
        function_name = ctx.get("function_name") or ""
        if drop_module_level and not class_name and not function_name:
            continue

        file_path = ctx.get("file_path", "")
        key = (file_path, class_name, function_name)
        context_concepts[key].add(name)
        if key not in context_subdomain:
            context_subdomain[key] = _subdomain_of(file_path)

    return context_concepts, context_subdomain


def analyze_cooccurrence(
    occurrences: list[dict[str, Any]],
    min_count: int = 3,
    normalization: str = "jaccard",
    threshold: float = 0.05,
    use_subdomain_partition: bool = True,
    cross_subdomain_factor: float = 0.3,
    drop_module_level_context: bool = True,
) -> list[dict[str, Any]]:
    """Find concept pairs that frequently co-occur within meaningful contexts.

    Args:
        occurrences: List of occurrence dicts from Phase 1.
        min_count: Minimum raw co-occurrence count to consider.
        normalization: "jaccard" | "pmi" | "npmi".
        threshold: Minimum normalized score to keep.
        use_subdomain_partition: If True, down-weight cross-subdomain pairs.
        cross_subdomain_factor: Multiplier applied to the weight of pairs
            whose dominant co-occurrence contexts span multiple subdomains.
        drop_module_level_context: If True, ignore contexts with no enclosing
            class or function (typically just `import` clusters at file top).
    """
    context_sets, context_subdomain = _build_context_sets(
        occurrences, drop_module_level=drop_module_level_context,
    )
    logger.info(
        "Co-occurrence: %d non-trivial contexts (drop_module_level=%s)",
        len(context_sets), drop_module_level_context,
    )

    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    # Track which subdomains each pair appears in (for cross-subdomain detection)
    pair_subdomains: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    concept_total_contexts: dict[str, int] = defaultdict(int)

    for key, concepts in context_sets.items():
        if len(concepts) < 2:
            continue
        sd = context_subdomain.get(key, "_other")
        sorted_concepts = sorted(concepts)
        for i in range(len(sorted_concepts)):
            concept_total_contexts[sorted_concepts[i]] += 1
            for j in range(i + 1, len(sorted_concepts)):
                pair = (sorted_concepts[i], sorted_concepts[j])
                pair_counts[pair] += 1
                pair_subdomains[pair][sd] += 1

    total_contexts = len(context_sets)

    relationships: list[dict[str, Any]] = []
    for (a, b), count in pair_counts.items():
        if count < min_count:
            continue

        pa = concept_total_contexts[a] / total_contexts
        pb = concept_total_contexts[b] / total_contexts
        pab = count / total_contexts

        if normalization == "pmi":
            base = math.log2(pab / (pa * pb)) if pa > 0 and pb > 0 else 0.0
        elif normalization == "npmi":
            pmi = math.log2(pab / (pa * pb)) if pa > 0 and pb > 0 else 0.0
            base = pmi / (-math.log2(pab)) if pab > 0 else 0.0
        else:
            union = concept_total_contexts[a] + concept_total_contexts[b] - count
            base = count / union if union > 0 else 0.0

        # Subdomain coherence factor
        sds = pair_subdomains[(a, b)]
        dominant_sd, dominant_count = max(sds.items(), key=lambda kv: kv[1])
        same_sd_ratio = dominant_count / count if count > 0 else 0.0
        if use_subdomain_partition and same_sd_ratio < 0.5:
            # Pair is mostly cross-subdomain — down-weight.
            score = base * cross_subdomain_factor
            cross = True
        else:
            score = base
            cross = False

        if score < threshold:
            continue

        rel = Relationship(
            source_concept_id=a,
            target_concept_id=b,
            relationship_type="co-occurrence",
            weight=score,
            metadata={
                "cooccurrence_count": count,
                "normalization": normalization,
                "source_total": concept_total_contexts[a],
                "target_total": concept_total_contexts[b],
                "dominant_subdomain": dominant_sd,
                "same_subdomain_ratio": same_sd_ratio,
                "cross_subdomain_penalized": cross,
                "raw_score": base,
            },
        )
        relationships.append(rel.model_dump())

    logger.info("Co-occurrence: found %d pairs (above threshold)", len(relationships))
    return relationships
