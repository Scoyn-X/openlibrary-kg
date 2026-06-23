#!/usr/bin/env python
"""Phase 4: Analyze polysemy (words with multiple meanings).

Usage:
    python scripts/analyze_polysemy.py [--config config.yaml]
"""

from __future__ import annotations

import argparse

from openlibrary_kg.config import load_config
from openlibrary_kg.embeddings.openai_embedding import OpenAIEmbeddingProvider
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.relationships.polysemy import analyze_polysemy
from openlibrary_kg.utils.io import read_json, write_json
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4: Polysemy Analysis")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default="output/phase_2_definitions.json")
    parser.add_argument("--output", default="output/phase_4_polysemy_groups.json")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)

    data = read_json(args.input)
    occurrences = data.get("occurrences", [])
    logger.info("Loaded %d occurrences", len(occurrences))

    # Filter to only those with definitions
    occs_with_defs = [o for o in occurrences if o.get("definition")]
    logger.info("Occurrences with definitions: %d", len(occs_with_defs))

    # Create embedding provider
    if config.embedding.provider == "openai":
        provider = OpenAIEmbeddingProvider(model=config.embedding.model)
    else:
        provider = SentenceTransformerProvider(model=config.embedding.model)

    polysemy_groups = analyze_polysemy(
        occs_with_defs,
        provider,
        min_occurrences=config.relationships.polysemy.min_occurrences_for_polysemy,
        min_files=config.relationships.polysemy.min_files_for_polysemy,
        distance_threshold=config.relationships.polysemy.embedding_distance_threshold,
    )

    # Count polysemous concepts
    polysemous = {k: v for k, v in polysemy_groups.items() if len(v) > 1}

    output_data = {
        "phase": "phase_4_polysemy_groups",
        "metadata": {
            "concepts_with_multiple_meanings": len(polysemous),
            "total_concepts_checked": len(polysemy_groups),
            "total_clusters": sum(len(v) for v in polysemy_groups.values()),
        },
        "polysemy_groups": polysemy_groups,
        "polysemous_concepts": polysemous,
    }

    write_json(args.output, output_data, pretty=config.output.pretty_print)
    logger.info(
        "Written polysemy groups: %d polysemous out of %d concepts",
        len(polysemous), len(polysemy_groups),
    )

    if polysemous:
        print("\nTop polysemous concepts:")
        sorted_poly = sorted(polysemous.items(), key=lambda x: len(x[1]), reverse=True)
        for name, clusters in sorted_poly[:10]:
            print(f"  '{name}': {len(clusters)} meanings")
            for c in clusters:
                print(f"    - {c['canonical_definition'][:100]}")


if __name__ == "__main__":
    main()
