"""Statistics and summary reporting for the knowledge graph."""

from __future__ import annotations

import logging
from typing import Any

from openlibrary_kg.models import KnowledgeGraph

logger = logging.getLogger("openlibrary_kg.graph")


def compute_statistics(kg: KnowledgeGraph) -> dict[str, Any]:
    """Compute summary statistics for the knowledge graph."""
    concepts = kg.concepts
    relationships = kg.relationships

    stats: dict[str, Any] = {
        "total_concepts": len(concepts),
        "total_relationships": len(relationships),
        "total_occurrences": sum(c.frequency for c in concepts),
    }

    # Relationship type counts
    rel_type_counts: dict[str, int] = {}
    for rel in relationships:
        rel_type_counts[rel.relationship_type] = (
            rel_type_counts.get(rel.relationship_type, 0) + 1
        )
    stats["relationships_by_type"] = rel_type_counts

    # Top concepts by frequency
    sorted_by_freq = sorted(concepts, key=lambda c: c.frequency, reverse=True)
    stats["top_concepts_by_frequency"] = [
        {
            "name": c.canonical_name,
            "frequency": c.frequency,
            "num_files": len(set(o.context.file_path for o in c.occurrences)),
            "raw_identifiers": c.all_raw_identifiers[:5],
        }
        for c in sorted_by_freq[:30]
    ]

    # Polysemy stats
    polysemous = [c for c in concepts if len(c.definition_clusters) > 1]
    stats["polysemous_concepts"] = len(polysemous)
    stats["top_polysemous"] = [
        {
            "name": c.canonical_name,
            "num_meanings": len(c.definition_clusters),
            "meanings": [cl.canonical_definition[:100] for cl in c.definition_clusters],
        }
        for c in sorted(polysemous, key=lambda c: len(c.definition_clusters), reverse=True)[:15]
    ]

    # Top synonym pairs
    synonym_rels = [r for r in relationships if r.relationship_type == "synonym"]
    if synonym_rels:
        top_syns = sorted(synonym_rels, key=lambda r: r.weight, reverse=True)[:20]
        stats["top_synonym_pairs"] = [
            {
                "source": r.source_concept_id,
                "target": r.target_concept_id,
                "similarity": round(r.weight, 3),
            }
            for r in top_syns
        ]

    # Top co-occurrence pairs
    cooc_rels = [r for r in relationships if r.relationship_type == "co-occurrence"]
    if cooc_rels:
        top_cooc = sorted(cooc_rels, key=lambda r: r.weight, reverse=True)[:20]
        stats["top_cooccurrence_pairs"] = [
            {
                "source": r.source_concept_id,
                "target": r.target_concept_id,
                "score": round(r.weight, 3),
                "count": r.metadata.get("cooccurrence_count", 0),
            }
            for r in top_cooc
        ]

    # Concept degree distribution (if we have relationships)
    if relationships:
        degrees: dict[str, int] = {}
        for rel in relationships:
            degrees[rel.source_concept_id] = degrees.get(rel.source_concept_id, 0) + 1
            degrees[rel.target_concept_id] = degrees.get(rel.target_concept_id, 0) + 1
        if degrees:
            deg_values = sorted(degrees.values(), reverse=True)
            stats["degree"] = {
                "max": deg_values[0],
                "avg": round(sum(deg_values) / len(deg_values), 1),
                "median": deg_values[len(deg_values) // 2],
                "num_isolated": len(concepts) - len(degrees),
            }
            # Top concepts by degree
            sorted_deg = sorted(degrees.items(), key=lambda x: x[1], reverse=True)
            stats["top_central_concepts"] = [
                {"name": name, "degree": d}
                for name, d in sorted_deg[:20]
            ]

    return stats


def print_statistics(stats: dict[str, Any]) -> None:
    """Print a human-readable summary of KG statistics."""
    print("=" * 60)
    print("Knowledge Graph Statistics")
    print("=" * 60)
    print(f"  Total concepts:      {stats['total_concepts']}")
    print(f"  Total relationships:  {stats['total_relationships']}")
    print(f"  Total occurrences:    {stats['total_occurrences']}")
    print()

    rel_types = stats.get("relationships_by_type", {})
    if rel_types:
        print("  Relationships by type:")
        for rtype, count in rel_types.items():
            print(f"    {rtype}: {count}")
        print()

    print("  Top concepts by frequency:")
    for c in stats.get("top_concepts_by_frequency", [])[:10]:
        print(f"    {c['name']}: freq={c['frequency']}, "
              f"files={c['num_files']}, ids={c['raw_identifiers']}")
    print()

    polysemous = stats.get("top_polysemous", [])
    if polysemous:
        print(f"  Polysemous concepts: {stats.get('polysemous_concepts', 0)}")
        for p in polysemous[:5]:
            print(f"    '{p['name']}': {p['num_meanings']} meanings")
        print()

    top_syns = stats.get("top_synonym_pairs", [])
    if top_syns:
        print("  Top synonym pairs:")
        for s in top_syns[:5]:
            print(f"    {s['source']} <-> {s['target']}: {s['similarity']}")
        print()

    top_cooc = stats.get("top_cooccurrence_pairs", [])
    if top_cooc:
        print("  Top co-occurrence pairs:")
        for c in top_cooc[:5]:
            print(f"    {c['source']} <-> {c['target']}: {c['score']} "
                  f"(count={c['count']})")
        print()

    degree = stats.get("degree")
    if degree:
        print(f"  Degree: max={degree['max']}, avg={degree['avg']}, "
              f"median={degree['median']}, isolated={degree['num_isolated']}")
        top_central = stats.get("top_central_concepts", [])[:5]
        if top_central:
            print("  Most central concepts:")
            for c in top_central:
                print(f"    {c['name']}: degree={c['degree']}")
