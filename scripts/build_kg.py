#!/usr/bin/env python
"""Phase 6: Build the final Knowledge Graph.

Assembles data from all prior phases into a unified KG.

Usage:
    python scripts/build_kg.py [--config config.yaml]

The script automatically detects which phase outputs exist and
incorporates all available data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openlibrary_kg.config import load_config
from openlibrary_kg.graph.builder import build_knowledge_graph
from openlibrary_kg.graph.export import export_gexf, export_json
from openlibrary_kg.graph.stats import compute_statistics, print_statistics
from openlibrary_kg.models import KnowledgeGraph
from openlibrary_kg.utils.logging import setup_logging


def _load_if_exists(path: str) -> dict[str, Any] | None:
    """Load JSON if file exists, return None otherwise."""
    p = Path(path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6: KG Builder")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    parser.add_argument("--output", default="output/phase_6_knowledge_graph.json")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)

    output_dir = Path(args.output_dir or config.output.directory)

    # Load all phase outputs
    logger.info("Loading phase outputs...")
    phase_1 = _load_if_exists(output_dir / "phase_1_concepts.json")
    phase_2 = _load_if_exists(output_dir / "phase_2_definitions.json")
    phase_3 = _load_if_exists(output_dir / "phase_3_synonyms.json")
    phase_4 = _load_if_exists(output_dir / "phase_4_polysemy_groups.json")
    phase_5 = _load_if_exists(output_dir / "phase_5_cooccurrence.json")

    if not phase_1:
        logger.error("Phase 1 output not found. Run extract_concepts.py first.")
        return

    # Build KG
    logger.info("Building knowledge graph...")
    kg: KnowledgeGraph = build_knowledge_graph(
        phase_1_data=phase_1,
        phase_2_data=phase_2,
        phase_3_data=phase_3,
        phase_4_data=phase_4,
        phase_5_data=phase_5,
    )

    # Export JSON (primary format)
    json_path = Path(args.output)
    export_json(kg, json_path, pretty=config.output.pretty_print)

    # Export GEXF if requested
    if "gexf" in config.output.formats:
        gexf_path = json_path.with_suffix(".gexf")
        export_gexf(kg, gexf_path)

    # Compute and print statistics
    stats = compute_statistics(kg)
    print_statistics(stats)

    # Save stats
    stats_path = json_path.with_name(json_path.stem + "_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)

    logger.info("KG built: %d concepts, %d relationships",
                len(kg.concepts), len(kg.relationships))
    logger.info("Output: %s", json_path)


if __name__ == "__main__":
    main()
