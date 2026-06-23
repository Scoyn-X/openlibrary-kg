#!/usr/bin/env python
"""Phase 3: Detect synonyms using two-track method.

  Track A — cosine >= naming_variant_threshold → auto-accept (naming variants).
  Track B — cosine in [llm_judge_low, llm_judge_high) → ask LLM if they're
            domain-equivalent.

The LLM track catches synonyms whose surface forms differ (e.g. user ↔
account) which pure embedding similarity cannot find.

Usage:
    python scripts/detect_synonyms.py [--config config.yaml]
"""

from __future__ import annotations

import argparse

from openlibrary_kg.config import load_config
from openlibrary_kg.embeddings.openai_embedding import OpenAIEmbeddingProvider
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.llm.definition_generator import _make_client
from openlibrary_kg.relationships.synonyms import detect_synonyms
from openlibrary_kg.utils.io import read_json, write_json
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3: Synonym Detection")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default="output/phase_2_definitions.json",
                        help="Input: Phase 2 definitions (falls back to Phase 1)")
    parser.add_argument("--output", default="output/phase_3_synonyms.json")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable Track B (LLM judgment) — Track A only.")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)

    data = read_json(args.input)
    occurrences = data.get("occurrences", [])

    # Aggregate occurrences by split_name
    concepts_map: dict[str, dict] = {}
    for occ in occurrences:
        name = occ.get("split_name", "")
        if not name:
            continue
        if name not in concepts_map:
            concepts_map[name] = {
                "concept_id": name,
                "canonical_name": name,
                "split_name": name,
                "all_raw_identifiers": [occ.get("raw_identifier", "")],
                "occurrences": [occ],
            }
        else:
            concepts_map[name]["all_raw_identifiers"].append(
                occ.get("raw_identifier", "")
            )
            concepts_map[name]["occurrences"].append(occ)

    # Deduplicate raw identifiers for stability
    for c in concepts_map.values():
        c["all_raw_identifiers"] = list(dict.fromkeys(c["all_raw_identifiers"]))

    concepts = list(concepts_map.values())
    logger.info("Aggregated %d unique concepts from %d occurrences",
                len(concepts), len(occurrences))

    if config.embedding.provider == "openai":
        provider = OpenAIEmbeddingProvider(model=config.embedding.model)
    else:
        provider = SentenceTransformerProvider(model=config.embedding.model)

    syn_cfg = config.relationships.synonyms
    use_llm = syn_cfg.llm_validation and not args.no_llm
    llm_client = _make_client(config) if use_llm else None

    relationships = detect_synonyms(
        concepts,
        provider,
        similarity_threshold=syn_cfg.similarity_threshold,
        naming_variant_threshold=syn_cfg.naming_variant_threshold,
        llm_judge_low=syn_cfg.llm_judge_low,
        llm_judge_high=syn_cfg.llm_judge_high,
        top_k=syn_cfg.top_k,
        llm_validation=use_llm,
        llm_client=llm_client,
        llm_batch_size=syn_cfg.llm_batch_size,
        cache_dir=config.llm.cache_dir if use_llm else None,
    )

    output_data = {
        "phase": "phase_3_synonyms",
        "metadata": {
            "num_concepts": len(concepts),
            "num_synonym_pairs": len(relationships),
            "naming_variant_threshold": syn_cfg.naming_variant_threshold,
            "llm_judge_range": [syn_cfg.llm_judge_low, syn_cfg.llm_judge_high],
            "llm_validation": use_llm,
            "embedding_model": config.embedding.model,
        },
        "concepts": concepts,
        "relationships": relationships,
    }

    write_json(args.output, output_data, pretty=config.output.pretty_print)
    logger.info("Written %d synonym pairs to %s", len(relationships), args.output)


if __name__ == "__main__":
    main()
