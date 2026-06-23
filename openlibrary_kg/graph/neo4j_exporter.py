"""Neo4j Knowledge Graph exporter.

Imports the Openlibrary KG into a Neo4j graph database.
Creates (:Concept) nodes and [:SYNONYM | :CO_OCCURRENCE | :POLYSEMY] edges.
"""

from __future__ import annotations

import logging
from typing import Any

from openlibrary_kg.config import Neo4jConfig
from openlibrary_kg.models import KnowledgeGraph

logger = logging.getLogger("openlibrary_kg.graph.neo4j")


def _create_driver(config: Neo4jConfig):
    """Create a Neo4j driver from config."""
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        config.uri,
        auth=(config.user, config.password),
    )


def _clear_graph(session, database: str) -> None:
    """Delete all nodes and relationships in the database."""
    logger.info("Clearing existing graph data...")
    session.run("MATCH (n) DETACH DELETE n")
    logger.info("Graph cleared.")


def _create_constraints(session, database: str) -> None:
    """Create uniqueness constraints and indexes for concept nodes."""
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Concept) REQUIRE c.concept_id IS UNIQUE",
        "CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.canonical_name)",
        "CREATE INDEX IF NOT EXISTS FOR ()-[r:SYNONYM]-() ON (r.weight)",
        "CREATE INDEX IF NOT EXISTS FOR ()-[r:CO_OCCURRENCE]-() ON (r.weight)",
    ]
    for stmt in constraints:
        try:
            session.run(stmt)
        except Exception as exc:
            logger.warning("Constraint/index skipped: %s", exc)


def _import_concepts(session, concepts: list[Any], batch_size: int) -> dict[str, str]:
    """Import all concept nodes. Returns mapping of concept_id -> Neo4j elementId.

    Uses UNWIND batch for performance.
    """
    logger.info("Importing %d concept nodes...", len(concepts))
    concept_index: dict[str, str] = {}

    for batch_start in range(0, len(concepts), batch_size):
        batch = concepts[batch_start:batch_start + batch_size]
        rows = []
        for c in batch:
            if hasattr(c, "model_dump"):
                d = c.model_dump()
            else:
                d = c

            rows.append({
                "concept_id": d.get("concept_id", d.get("canonical_name", "")),
                "canonical_name": d.get("canonical_name", ""),
                "split_terms": d.get("split_terms", []),
                "all_raw_identifiers": d.get("all_raw_identifiers", [])[:20],
                "frequency": d.get("frequency", 0),
                "num_occurrences": len(d.get("occurrences", [])),
                "has_polysemy": len(d.get("definition_clusters", [])) > 1,
                "num_definition_clusters": len(d.get("definition_clusters", [])),
            })

        result = session.run(
            """
            UNWIND $rows AS row
            CREATE (c:Concept {
                concept_id: row.concept_id,
                canonical_name: row.canonical_name,
                split_terms: row.split_terms,
                all_raw_identifiers: row.all_raw_identifiers,
                frequency: row.frequency,
                num_occurrences: row.num_occurrences,
                has_polysemy: row.has_polysemy,
                num_definition_clusters: row.num_definition_clusters
            })
            RETURN c.concept_id AS concept_id, elementId(c) AS neo4j_id
            """,
            rows=rows,
        )

        for record in result:
            concept_index[record["concept_id"]] = record["neo4j_id"]

        logger.debug(
            "Imported concepts %d-%d/%d",
            batch_start + 1, min(batch_start + batch_size, len(concepts)), len(concepts),
        )

    logger.info("Imported %d concepts (%d with Neo4j IDs)", len(concepts), len(concept_index))
    return concept_index


def _import_relationships(
    session,
    relationships: list[Any],
    batch_size: int,
) -> None:
    """Import all relationships as edges between concept nodes.

    Preserves selected metadata on each edge so downstream queries can
    distinguish (e.g.) Track A vs Track B synonyms, or same-subdomain vs
    cross-subdomain co-occurrence pairs.
    """
    logger.info("Importing %d relationships...", len(relationships))

    by_type: dict[str, list[dict]] = {}
    for r in relationships:
        if hasattr(r, "model_dump"):
            d = r.model_dump()
        else:
            d = r
        rel_type = d.get("relationship_type", "RELATED_TO").upper().replace("-", "_")
        by_type.setdefault(rel_type, []).append(d)

    for rel_type, rels in by_type.items():
        logger.info("  Importing %d %s relationships...", len(rels), rel_type)
        for batch_start in range(0, len(rels), batch_size):
            batch = rels[batch_start:batch_start + batch_size]
            rows = []
            for d in batch:
                md = d.get("metadata", {}) or {}
                rows.append({
                    "source_id": d.get("source_concept_id", ""),
                    "target_id": d.get("target_concept_id", ""),
                    "weight": float(d.get("weight", 0.0)),
                    # Synonym-only fields (will be null for other types)
                    "track": md.get("track", ""),
                    "method": md.get("method", ""),
                    "llm_reason": (md.get("llm_reason", "") or "")[:240],
                    # Co-occurrence-only fields
                    "cooccurrence_count": int(md.get("cooccurrence_count", 0)),
                    "dominant_subdomain": md.get("dominant_subdomain", ""),
                    "same_subdomain_ratio": float(md.get("same_subdomain_ratio", 0.0)),
                    "cross_subdomain_penalized": bool(md.get("cross_subdomain_penalized", False)),
                })

            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (source:Concept {{concept_id: row.source_id}})
                MATCH (target:Concept {{concept_id: row.target_id}})
                MERGE (source)-[r:{rel_type}]->(target)
                SET r.weight = row.weight,
                    r.track = row.track,
                    r.method = row.method,
                    r.llm_reason = row.llm_reason,
                    r.cooccurrence_count = row.cooccurrence_count,
                    r.dominant_subdomain = row.dominant_subdomain,
                    r.same_subdomain_ratio = row.same_subdomain_ratio,
                    r.cross_subdomain_penalized = row.cross_subdomain_penalized
                RETURN count(r) AS created
                """,
                rows=rows,
            )

    logger.info("Imported %d relationships", len(relationships))


def export_to_neo4j(
    kg: KnowledgeGraph | dict | str,
    config: Neo4jConfig,
) -> None:
    """Export the knowledge graph to Neo4j.

    Args:
        kg: KnowledgeGraph instance, or dict (JSON-loaded), or path to KG JSON file.
        config: Neo4j connection configuration.
    """
    # Resolve KG input
    if isinstance(kg, str):
        import json
        with open(kg, encoding="utf-8") as f:
            kg_dict = json.load(f)
        kg = KnowledgeGraph(**kg_dict)
    elif isinstance(kg, dict):
        kg = KnowledgeGraph(**kg)

    concepts = kg.concepts
    relationships = kg.relationships

    logger.info("Connecting to Neo4j at %s", config.uri)
    driver = _create_driver(config)

    try:
        with driver.session(database=config.database) as session:
            # Optionally clear existing data
            if config.clear_existing:
                _clear_graph(session, config.database)

            # Create constraints
            _create_constraints(session, config.database)

            # Import concepts
            concept_index = _import_concepts(session, concepts, config.batch_size)

            # Import relationships
            if relationships:
                _import_relationships(session, relationships, config.batch_size)
            else:
                logger.info("No relationships to import.")

            # Summary
            result = session.run("MATCH (n:Concept) RETURN count(n) AS node_count")
            node_count = result.single()["node_count"]

            result = session.run("MATCH ()-[r]->() RETURN count(r) AS edge_count")
            edge_count = result.single()["edge_count"]

            logger.info("Neo4j import complete: %d nodes, %d edges", node_count, edge_count)
            print(f"\nNeo4j import complete!")
            print(f"  Nodes:          {node_count}")
            print(f"  Relationships:  {edge_count}")
            print(f"  Database:       {config.database}")
            print(f"  URI:            {config.uri}")

    finally:
        driver.close()
