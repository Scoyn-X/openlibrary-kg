#!/usr/bin/env python
"""Phase 5: Analyze co-occurrence relationships.

Finds concept pairs that frequently appear in the same function/class.

Usage:
    python scripts/analyze_cooccurrence.py [--config config.yaml]
"""

from __future__ import annotations

import argparse

from openlibrary_kg.config import load_config
from openlibrary_kg.relationships.cooccurrence import analyze_cooccurrence
from openlibrary_kg.utils.io import read_json, write_json
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5: Co-occurrence Analysis")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default="output/phase_1_concepts.json",
                        help="Input: Phase 1 occurrences (no definitions needed)")
    parser.add_argument("--output", default="output/phase_5_cooccurrence.json")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)

    data = read_json(args.input)
    occurrences = data.get("occurrences", [])
    logger.info("Loaded %d occurrences", len(occurrences))

    co_cfg = config.relationships.cooccurrence
    relationships = analyze_cooccurrence(
        occurrences,
        min_count=co_cfg.min_count,
        normalization=co_cfg.normalization,
        threshold=co_cfg.threshold,
        use_subdomain_partition=co_cfg.use_subdomain_partition,
        cross_subdomain_factor=co_cfg.cross_subdomain_factor,
        drop_module_level_context=co_cfg.drop_module_level_context,
    )

    output_data = {
        "phase": "phase_5_cooccurrence",
        "metadata": {
            "total_cooccurrence_pairs": len(relationships),
            "normalization": config.relationships.cooccurrence.normalization,
            "min_count": config.relationships.cooccurrence.min_count,
        },
        "relationships": relationships,
    }

    write_json(args.output, output_data, pretty=config.output.pretty_print)
    logger.info("Written %d co-occurrence pairs to %s",
                len(relationships), args.output)

    # Show top pairs
    if relationships:
        sorted_rels = sorted(relationships, key=lambda r: r["weight"], reverse=True)
        print("\nTop co-occurring concept pairs:")
        for r in sorted_rels[:15]:
            src = r["source_concept_id"]
            tgt = r["target_concept_id"]
            w = r["weight"]
            cnt = r["metadata"]["cooccurrence_count"]
            print(f"  {src} <-> {tgt}: {w:.3f} (count={cnt})")


if __name__ == "__main__":
    main()
