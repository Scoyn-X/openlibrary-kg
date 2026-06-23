#!/usr/bin/env python
"""Run a curated set of Cypher queries against the Neo4j KG and print results.

These are designed to be both (a) a smoke test that the import worked, and
(b) a tour of what the KG can tell you. Each query prints its name + Cypher
text + a tabular result.

Usage:
    python scripts/run_kg_queries.py
    python scripts/run_kg_queries.py --concept user      # ego subgraph for "user"
    python scripts/run_kg_queries.py --out output/kg_queries_report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from openlibrary_kg.config import load_config


QUERIES: list[dict[str, str]] = [
    {
        "name": "1. Total nodes & edges (by type)",
        "purpose": "Sanity check that import filled the graph.",
        "cypher": """
            MATCH (c:Concept)
            WITH count(c) AS concept_count
            MATCH ()-[r]->()
            RETURN concept_count,
                   count(r) AS total_edges,
                   sum(CASE type(r) WHEN 'SYNONYM' THEN 1 ELSE 0 END) AS synonyms,
                   sum(CASE type(r) WHEN 'CO_OCCURRENCE' THEN 1 ELSE 0 END) AS cooccur,
                   sum(CASE type(r) WHEN 'POLYSEMY' THEN 1 ELSE 0 END) AS polysemy
        """,
    },
    {
        "name": "2. Top 15 concepts by frequency (most-used domain words)",
        "purpose": "Sanity check that domain concepts (not stdlib) dominate.",
        "cypher": """
            MATCH (c:Concept)
            RETURN c.canonical_name AS concept,
                   c.frequency AS frequency,
                   c.num_definition_clusters AS meanings
            ORDER BY frequency DESC
            LIMIT 15
        """,
    },
    {
        "name": "3. Top 15 most-connected concepts (hubs)",
        "purpose": "Which concepts sit at the centre of the graph?",
        "cypher": """
            MATCH (c:Concept)-[r]-()
            WITH c, count(r) AS degree
            RETURN c.canonical_name AS concept, degree
            ORDER BY degree DESC
            LIMIT 15
        """,
    },
    {
        "name": "4. Synonyms — Track A (naming variants, auto-accepted)",
        "purpose": "These are concepts the embedding said are surface variants.",
        "cypher": """
            MATCH (a:Concept)-[r:SYNONYM]->(b:Concept)
            WHERE r.track = 'naming_variant'
            RETURN a.canonical_name AS a, b.canonical_name AS b,
                   round(r.weight, 3) AS similarity
            ORDER BY r.weight DESC
            LIMIT 15
        """,
    },
    {
        "name": "5. Synonyms — Track B (LLM-judged domain equivalences)",
        "purpose": "These are pairs that look different but the LLM said are the same concept here.",
        "cypher": """
            MATCH (a:Concept)-[r:SYNONYM]->(b:Concept)
            WHERE r.track = 'domain_equivalence'
            RETURN a.canonical_name AS a, b.canonical_name AS b,
                   round(r.weight, 3) AS cosine,
                   r.llm_reason AS llm_reason
            ORDER BY r.weight DESC
            LIMIT 15
        """,
    },
    {
        "name": "6. Polysemous concepts (≥2 meanings)",
        "purpose": "Words that mean different things in different parts of the codebase.",
        "cypher": """
            MATCH (c:Concept)
            WHERE c.has_polysemy = true
            RETURN c.canonical_name AS concept,
                   c.num_definition_clusters AS meanings,
                   c.frequency AS frequency
            ORDER BY c.num_definition_clusters DESC, c.frequency DESC
            LIMIT 15
        """,
    },
    {
        "name": "7. Top co-occurrence pairs (same-subdomain only)",
        "purpose": "Strong concept pairs within one subpackage — typically meaningful.",
        "cypher": """
            MATCH (a:Concept)-[r:CO_OCCURRENCE]->(b:Concept)
            WHERE r.cross_subdomain_penalized = false
            RETURN a.canonical_name AS a, b.canonical_name AS b,
                   r.dominant_subdomain AS subdomain,
                   r.cooccurrence_count AS together,
                   round(r.weight, 3) AS jaccard
            ORDER BY r.weight DESC, together DESC
            LIMIT 15
        """,
    },
    {
        "name": "8. Cross-subdomain co-occurrence (down-weighted but still surviving)",
        "purpose": "Pairs spanning different subpackages — interesting integration points.",
        "cypher": """
            MATCH (a:Concept)-[r:CO_OCCURRENCE]->(b:Concept)
            WHERE r.cross_subdomain_penalized = true
            RETURN a.canonical_name AS a, b.canonical_name AS b,
                   r.cooccurrence_count AS together,
                   round(r.weight, 3) AS jaccard_after_penalty
            ORDER BY r.weight DESC
            LIMIT 10
        """,
    },
]


# Ego-network query that takes a concept name. Run separately if --concept passed.
EGO_QUERY = """
    MATCH (c:Concept {canonical_name: $name})-[r]-(neighbor:Concept)
    RETURN type(r) AS edge_type,
           neighbor.canonical_name AS neighbor,
           round(r.weight, 3) AS weight,
           coalesce(r.track, r.dominant_subdomain, '') AS extra
    ORDER BY r.weight DESC
    LIMIT 20
"""


def _fmt_table(rows: list[dict]) -> str:
    if not rows:
        return "  (no results)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    widths = {c: min(w, 60) for c, w in widths.items()}
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [header, sep]
    for r in rows:
        vals = []
        for c in cols:
            s = str(r.get(c, ""))
            if len(s) > widths[c]:
                s = s[: widths[c] - 1] + "…"
            vals.append(s.ljust(widths[c]))
        lines.append(" | ".join(vals))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cypher queries against the imported KG")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--concept", default=None,
                        help="If given, additionally print the ego subgraph of this concept.")
    parser.add_argument("--out", default=None,
                        help="Optional: write the full report to a markdown file.")
    args = parser.parse_args()

    config = load_config(args.config)

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("ERROR: neo4j driver not installed. Run: pip install neo4j")
        raise SystemExit(2)

    try:
        driver = GraphDatabase.driver(
            config.neo4j.uri,
            auth=(config.neo4j.user, config.neo4j.password),
        )
        with driver.session(database=config.neo4j.database) as session:
            session.run("RETURN 1").consume()
    except Exception as exc:
        print(f"ERROR: cannot connect to Neo4j at {config.neo4j.uri}: {exc}")
        print("Hint: make sure Neo4j is running and config.yaml.neo4j credentials are correct.")
        raise SystemExit(2)

    out_lines: list[str] = []
    def _emit(s: str = "") -> None:
        print(s)
        out_lines.append(s)

    _emit(f"# Openlibrary KG — Cypher report")
    _emit(f"_Connected to {config.neo4j.uri}, database `{config.neo4j.database}`_\n")

    with driver.session(database=config.neo4j.database) as session:
        for q in QUERIES:
            _emit(f"\n## {q['name']}")
            _emit(f"_{q['purpose']}_\n")
            _emit("```cypher")
            _emit(q["cypher"].strip())
            _emit("```")
            try:
                result = session.run(q["cypher"])
                rows = [dict(r) for r in result]
            except Exception as exc:
                _emit(f"  ERROR running query: {exc}")
                continue
            _emit("```")
            _emit(_fmt_table(rows))
            _emit("```")

        if args.concept:
            _emit(f"\n## Ego subgraph for concept `{args.concept}`")
            _emit("\n```cypher")
            _emit(EGO_QUERY.strip())
            _emit("```")
            try:
                result = session.run(EGO_QUERY, name=args.concept)
                rows = [dict(r) for r in result]
                _emit("```")
                _emit(_fmt_table(rows))
                _emit("```")
            except Exception as exc:
                _emit(f"  ERROR: {exc}")

    driver.close()

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(out_lines), encoding="utf-8")
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
