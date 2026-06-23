#!/usr/bin/env python
"""Export the knowledge graph to a Neo4j graph database.

Usage:
    python scripts/export_to_neo4j.py [--config config.yaml] [--kg output/phase_6_knowledge_graph.json]

Requirements:
    pip install openlibrary-kg[neo4j]
    # or: pip install neo4j

Neo4j connection is configured in config.yaml under `neo4j:` section,
or via environment variables:
    NEO4J_URI=bolt://localhost:7687
    NEO4J_USER=neo4j
    NEO4J_PASSWORD=yourpassword
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openlibrary_kg.config import load_config
from openlibrary_kg.graph.neo4j_exporter import export_to_neo4j
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export KG to Neo4j graph database"
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument(
        "--kg", default="output/phase_6_knowledge_graph.json",
        help="Path to the KG JSON file from Phase 6",
    )
    parser.add_argument("--clear", action="store_true",
                        help="Clear existing graph data before import")
    parser.add_argument("--uri", default=None, help="Override Neo4j URI")
    parser.add_argument("--user", default=None, help="Override Neo4j username")
    parser.add_argument("--password", default=None, help="Override Neo4j password")
    parser.add_argument("--database", default=None, help="Override Neo4j database name")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)

    # CLI overrides
    if args.clear:
        config.neo4j.clear_existing = True
    if args.uri:
        config.neo4j.uri = args.uri
    if args.user:
        config.neo4j.user = args.user
    if args.password:
        config.neo4j.password = args.password
    if args.database:
        config.neo4j.database = args.database

    # Check KG file exists
    kg_path = Path(args.kg)
    if not kg_path.exists():
        logger.error("KG file not found: %s", kg_path)
        print(f"Error: KG file not found: {kg_path}")
        print("Run 'python scripts/build_kg.py' first to generate the KG.")
        return

    # Load and export
    logger.info("Loading KG from %s", kg_path)
    with open(kg_path, encoding="utf-8") as f:
        kg_data = json.load(f)

    export_to_neo4j(kg_data, config.neo4j)


if __name__ == "__main__":
    main()
