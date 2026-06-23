"""Knowledge Graph builder: assembles concepts, relationships, and metadata.

Reads all phase outputs and constructs the complete KnowledgeGraph.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from openlibrary_kg.models import (
    CodeContext,
    Concept,
    ConceptOccurrence,
    KnowledgeGraph,
    Relationship,
)

logger = logging.getLogger("openlibrary_kg.graph")


def build_knowledge_graph(
    phase_1_data: dict[str, Any],
    phase_2_data: dict[str, Any] | None = None,
    phase_3_data: dict[str, Any] | None = None,
    phase_4_data: dict[str, Any] | None = None,
    phase_5_data: dict[str, Any] | None = None,
) -> KnowledgeGraph:
    """Assemble the complete knowledge graph from all phase outputs.

    Only phase_1_data is required. Other phases are optional and their
    data will be incorporated if provided.
    """
    # Step 1: Aggregate occurrences into concepts by split_name
    occurrences = phase_1_data.get("occurrences", [])
    definitions_map: dict[str, str] = {}
    if phase_2_data:
        for occ in phase_2_data.get("occurrences", []):
            oid = occ.get("occurrence_id", "")
            if occ.get("definition"):
                definitions_map[oid] = occ["definition"]

    concept_map: dict[str, Concept] = {}
    for occ_dict in occurrences:
        name = occ_dict.get("split_name", "")
        if not name:
            continue

        # Build occurrence object
        ctx = occ_dict.get("context", {})
        context = CodeContext(
            file_path=ctx.get("file_path", ""),
            function_name=ctx.get("function_name"),
            class_name=ctx.get("class_name"),
            line_number=ctx.get("line_number", 0),
            code_snippet=ctx.get("code_snippet", ""),
            block_type=ctx.get("block_type", "module"),
        )

        occ_obj = ConceptOccurrence(
            occurrence_id=occ_dict.get("occurrence_id", ""),
            raw_identifier=occ_dict.get("raw_identifier", ""),
            split_name=name,
            identifier_type=occ_dict.get("identifier_type", ""),
            context=context,
            definition=definitions_map.get(occ_dict.get("occurrence_id", "")),
        )

        if name not in concept_map:
            concept_map[name] = Concept(
                concept_id=name,   # 用名字做 ID，与关系数据一致
                canonical_name=name,
                split_terms=name.split("_"),
                all_raw_identifiers=[],
                occurrences=[],
            )
        c = concept_map[name]
        c.occurrences.append(occ_obj)
        c.all_raw_identifiers.append(occ_obj.raw_identifier)
        c.frequency = len(c.occurrences)

    # Deduplicate raw identifiers
    for c in concept_map.values():
        c.all_raw_identifiers = list(dict.fromkeys(c.all_raw_identifiers))

    logger.info("Aggregated %d unique concepts", len(concept_map))

    # Step 2: Add polysemy clusters (Phase 4)
    if phase_4_data:
        polysemy_groups = phase_4_data.get("polysemy_groups", {})
        for name, clusters in polysemy_groups.items():
            if name in concept_map and clusters:
                # Import DefinitionCluster properly
                from openlibrary_kg.models import DefinitionCluster
                concept_map[name].definition_clusters = [
                    DefinitionCluster(**c) if isinstance(c, dict) else c
                    for c in clusters
                ]

    # Step 3: Collect all relationships
    all_relationships: list[Relationship] = []
    for phase_data, phase_label in [
        (phase_3_data, "synonym"),
        (phase_5_data, "co-occurrence"),
    ]:
        if phase_data:
            for rel_dict in phase_data.get("relationships", []):
                rel = Relationship(**rel_dict) if isinstance(rel_dict, dict) else rel_dict
                all_relationships.append(rel)

    logger.info("Collected %d relationships", len(all_relationships))

    # Step 4: Build index
    concept_index: dict[str, int] = {}
    concepts_list = list(concept_map.values())
    for i, c in enumerate(concepts_list):
        concept_index[c.concept_id] = i
        concept_index[c.canonical_name] = i

    # Step 5: Assemble KG
    kg = KnowledgeGraph(
        metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "codebase": "openlibrary",
            "source_path": phase_1_data.get("metadata", {}).get("codebase_root", ""),
            "total_concepts": len(concepts_list),
            "total_relationships": len(all_relationships),
            "total_occurrences": len(occurrences),
            "phases_included": [
                p for p, d in [
                    ("phase_1", phase_1_data),
                    ("phase_2_definitions", phase_2_data),
                    ("phase_3_synonyms", phase_3_data),
                    ("phase_4_polysemy", phase_4_data),
                    ("phase_5_cooccurrence", phase_5_data),
                ] if d is not None
            ],
        },
        concepts=concepts_list,
        relationships=all_relationships,
        concept_index=concept_index,
    )

    return kg
