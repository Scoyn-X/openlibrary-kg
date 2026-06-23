#!/usr/bin/env python
"""Demo data explorer for the Openlibrary KG.

Query saved phase outputs to find interesting examples for
presentation, debugging, or paper writing.

All queries are read-only — they load JSON files from output/
and print filtered/sorted results.

Usage:
    # Top co-occurrence pairs in a specific subdomain
    python scripts/demo_data.py --cooc --subdomain plugins

    # Most polysemous concepts with their meanings
    python scripts/demo_data.py --poly --min-meanings 5

    # Synonym pairs with LLM reasons (Track B)
    python scripts/demo_data.py --syn --track domain_equivalence

    # Synonym naming variants (Track A)
    python scripts/demo_data.py --syn --track naming_variant

    # Ego network: all relationships around a concept
    python scripts/demo_data.py --ego user

    # Concepts with most co-occurrence connections (hubs)
    python scripts/demo_data.py --hubs

    # Concepts with most synonym connections
    python scripts/demo_data.py --syn-hubs

    # Cross-subdomain co-occurrence (interesting integration points)
    python scripts/demo_data.py --cooc --cross

    # Search concept by name pattern
    python scripts/demo_data.py --search borrow

    # Export a focused subgraph for a list of concepts
    python scripts/demo_data.py --subgraph user,book,author,isbn --out demo_subset.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path("output")


def _load(phase: str) -> dict[str, Any]:
    mapping = {
        "p3": "phase_3_synonyms.json",
        "p4": "phase_4_polysemy_groups.json",
        "p5": "phase_5_cooccurrence.json",
        "p6": "phase_6_knowledge_graph.json",
    }
    path = OUTPUT_DIR / mapping[phase]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ====================================================================
# Query: top co-occurrence pairs
# ====================================================================

def show_cooc(args: argparse.Namespace) -> None:
    data = _load("p5")
    rels: list[dict] = data.get("relationships", [])
    concept_idx = _load_concept_index()

    # Filter by subdomain
    if args.subdomain:
        rels = [
            r for r in rels
            if r.get("metadata", {}).get("dominant_subdomain") == args.subdomain
        ]

    # Filter cross-subdomain only
    if args.cross:
        rels = [
            r for r in rels
            if r.get("metadata", {}).get("cross_subdomain_penalized")
        ]

    # Sort
    if args.sort_by_count:
        rels.sort(key=lambda r: r.get("metadata", {}).get("cooccurrence_count", 0), reverse=True)
    else:
        rels.sort(key=lambda r: r.get("weight", 0), reverse=True)

    n = args.n or 20
    print(f"\n{'='*70}")
    filt_desc = []
    if args.subdomain:
        filt_desc.append(f"subdomain={args.subdomain}")
    if args.cross:
        filt_desc.append("cross-subdomain")
    print(f"Top {n} co-occurrence pairs ({', '.join(filt_desc) if filt_desc else 'all'})")
    print(f"{'='*70}")
    print(f"{'Source':<25} {'Target':<25} {'Weight':>8} {'Count':>6}  Subdomain")
    print("-" * 70)

    for r in rels[:n]:
        src = r.get("source_concept_id", "?")
        tgt = r.get("target_concept_id", "?")
        w = r.get("weight", 0)
        cnt = r.get("metadata", {}).get("cooccurrence_count", 0)
        sd = r.get("metadata", {}).get("dominant_subdomain", "?")
        cross_flag = " ✗" if r.get("metadata", {}).get("cross_subdomain_penalized") else ""
        print(f"{src:<25} {tgt:<25} {w:>8.4f} {cnt:>6}{cross_flag}  {sd}")


# ====================================================================
# Query: synonyms
# ====================================================================

def show_syn(args: argparse.Namespace) -> None:
    data = _load("p3")
    rels: list[dict] = data.get("relationships", [])

    if args.track:
        rels = [
            r for r in rels
            if r.get("metadata", {}).get("track") == args.track
        ]

    rels.sort(key=lambda r: r.get("weight", 0), reverse=True)
    n = args.n or 20

    track_label = args.track or "all"
    print(f"\n{'='*70}")
    print(f"Top {n} synonym pairs (track={track_label})")
    print(f"{'='*70}")

    if args.track == "domain_equivalence":
        print(f"{'Source':<25} {'Target':<25} {'Cosine':>8}  LLM Reason")
        print("-" * 70)
        for r in rels[:n]:
            src = r.get("source_concept_id", "?")
            tgt = r.get("target_concept_id", "?")
            w = r.get("weight", 0)
            reason = r.get("metadata", {}).get("llm_reason", "")[:120]
            print(f"{src:<25} {tgt:<25} {w:>8.4f}  {reason}")
    else:
        print(f"{'Source':<25} {'Target':<25} {'Weight':>8}  Method")
        print("-" * 70)
        for r in rels[:n]:
            src = r.get("source_concept_id", "?")
            tgt = r.get("target_concept_id", "?")
            w = r.get("weight", 0)
            method = r.get("metadata", {}).get("method", "?")
            print(f"{src:<25} {tgt:<25} {w:>8.4f}  {method}")


# ====================================================================
# Query: polysemy
# ====================================================================

def show_poly(args: argparse.Namespace) -> None:
    data = _load("p4")
    poly = data.get("polysemous_concepts", {})

    if args.min_meanings:
        poly = {
            k: v for k, v in poly.items()
            if len(v) >= args.min_meanings
        }

    sorted_poly = sorted(poly.items(), key=lambda kv: len(kv[1]), reverse=True)
    n = args.n or 15

    print(f"\n{'='*70}")
    print(f"Top {n} polysemous concepts (min {args.min_meanings or 2} meanings)")
    print(f"{'='*70}")

    for name, clusters in sorted_poly[:n]:
        print(f"\n  [{len(clusters)} meanings] {name}")
        for i, c in enumerate(clusters[:5]):
            defn = c.get("canonical_definition", "")[:100]
            distinct = c.get("distinctiveness", 0.0)
            print(f"    [{i}] (distinctiveness={distinct:.3f}) {defn}")
        if len(clusters) > 5:
            print(f"    ... and {len(clusters) - 5} more")


# ====================================================================
# Query: concept hubs (most connected)
# ====================================================================

def show_hubs(args: argparse.Namespace) -> None:
    data = _load("p6")
    rels: list[dict] = data.get("relationships", [])
    concepts: list[dict] = data.get("concepts", [])
    concept_idx = {c["canonical_name"]: c for c in concepts}

    # Count edges per concept
    degree: dict[str, int] = defaultdict(int)
    for rel in rels:
        degree[rel.get("source_concept_id", "")] += 1
        degree[rel.get("target_concept_id", "")] += 1

    sorted_deg = sorted(degree.items(), key=lambda kv: kv[1], reverse=True)
    n = args.n or 20

    print(f"\n{'='*70}")
    print(f"Top {n} most-connected concepts (hubs)")
    print(f"{'='*70}")
    print(f"{'Concept':<25} {'Degree':>6}  {'Frequency':>8}  {'Files':>6}  Polysemy")
    print("-" * 70)

    for name, deg in sorted_deg[:n]:
        c = concept_idx.get(name, {})
        freq = c.get("frequency", 0)
        files = len({o.get("context", {}).get("file_path", "")
                      for o in c.get("occurrences", [])})
        n_clusters = len(c.get("definition_clusters", []))
        poly_flag = f"{n_clusters} meanings" if n_clusters > 1 else "-"
        print(f"{name:<25} {deg:>6}  {freq:>8}  {files:>6}  {poly_flag}")


# ====================================================================
# Query: synonym hubs
# ====================================================================

def show_syn_hubs(args: argparse.Namespace) -> None:
    data = _load("p6")
    rels: list[dict] = data.get("relationships", [])

    syn_degree: dict[str, int] = defaultdict(int)
    for rel in rels:
        if rel.get("relationship_type") == "synonym":
            syn_degree[rel.get("source_concept_id", "")] += 1
            syn_degree[rel.get("target_concept_id", "")] += 1

    sorted_deg = sorted(syn_degree.items(), key=lambda kv: kv[1], reverse=True)
    n = args.n or 15

    print(f"\n{'='*70}")
    print(f"Top {n} concepts with most synonym connections")
    print(f"{'='*70}")
    for name, deg in sorted_deg[:n]:
        print(f"  {name}: {deg} synonym edges")


# ====================================================================
# Query: ego network
# ====================================================================

def show_ego(args: argparse.Namespace) -> None:
    data = _load("p6")
    rels: list[dict] = data.get("relationships", [])
    concepts: list[dict] = data.get("concepts", [])

    concept_idx = {c["canonical_name"]: c for c in concepts}
    target = args.ego.lower()

    neighbors: dict[str, list[tuple[str, str, float, dict]]] = defaultdict(list)

    for rel in rels:
        src = rel.get("source_concept_id", "")
        tgt = rel.get("target_concept_id", "")
        rtype = rel.get("relationship_type", "?")
        w = rel.get("weight", 0.0)
        md = rel.get("metadata", {})

        if src.lower() == target:
            neighbors[tgt].append((rtype, "out", w, md))
        if tgt.lower() == target:
            neighbors[src].append((rtype, "in", w, md))

    # Find exact case match
    exact_name = target
    for name in concept_idx:
        if name.lower() == target:
            exact_name = name
            break

    c = concept_idx.get(exact_name, {})
    freq = c.get("frequency", 0)
    clusters = c.get("definition_clusters", [])

    print(f"\n{'='*70}")
    print(f"Ego network: {exact_name}")
    print(f"{'='*70}")
    print(f"  Frequency: {freq}")
    print(f"  Polysemy clusters: {len(clusters)}")
    if clusters:
        for i, cl in enumerate(clusters[:3]):
            d = cl.get("canonical_definition", "")[:100]
            print(f"    [{i}] {d}")

    print(f"\n  Neighbors ({len(neighbors)}):")
    print(f"  {'Neighbor':<25} {'Relation':<18} {'Weight':>8}  Detail")
    print(f"  {'-'*25} {'-'*18} {'-'*8}  {'-'*20}")

    sorted_nb = sorted(neighbors.items(),
                       key=lambda kv: max(w for _, _, w, _ in kv[1]),
                       reverse=True)

    for nb_name, edges in sorted_nb[:args.n or 25]:
        for rtype, direction, w, md in edges[:2]:
            detail = ""
            if rtype == "synonym":
                detail = md.get("track", "")
                if md.get("llm_reason"):
                    detail += f" | {md['llm_reason'][:60]}"
            elif rtype == "co-occurrence":
                detail = f"count={md.get('cooccurrence_count', '?')}"
                if md.get("cross_subdomain_penalized"):
                    detail += " (cross-subdomain)"
            print(f"  {nb_name:<25} {rtype:<18} {w:>8.4f}  {detail}")


# ====================================================================
# Query: search concepts by name
# ====================================================================

def show_search(args: argparse.Namespace) -> None:
    data = _load("p6")
    concepts: list[dict] = data.get("concepts", [])

    query = args.search.lower()
    matches = [
        c for c in concepts
        if query in c.get("canonical_name", "").lower()
        or any(query in ri.lower() for ri in c.get("all_raw_identifiers", []))
    ]

    print(f"\n{'='*70}")
    print(f"Search results for '{args.search}' ({len(matches)} matches)")
    print(f"{'='*70}")

    matches.sort(key=lambda c: c.get("frequency", 0), reverse=True)
    for c in matches[:args.n or 20]:
        name = c["canonical_name"]
        freq = c.get("frequency", 0)
        raw = c.get("all_raw_identifiers", [])[:5]
        clusters = c.get("definition_clusters", [])
        poly = f"{len(clusters)} meanings" if len(clusters) > 1 else "-"
        files = len({o.get("context", {}).get("file_path", "")
                      for o in c.get("occurrences", [])})
        print(f"  {name:<30} freq={freq:>5}  files={files:>4}  polysemy={poly}")
        if raw:
            print(f"    Raw identifiers: {', '.join(raw)}")
        # Show best definition
        best_def = ""
        for cl in clusters:
            d = cl.get("canonical_definition", "")
            if len(d) > len(best_def):
                best_def = d
        if not best_def:
            for occ in c.get("occurrences", []):
                d = occ.get("definition", "")
                if d and len(d) > len(best_def):
                    best_def = d
        if best_def:
            print(f"    Definition: {best_def[:120]}")


# ====================================================================
# Query: export subgraph
# ====================================================================

def show_subgraph(args: argparse.Namespace) -> None:
    data = _load("p6")
    concepts: list[dict] = data.get("concepts", [])
    rels: list[dict] = data.get("relationships", [])

    seeds = {s.strip().lower() for s in args.subgraph.split(",") if s.strip()}
    concept_idx = {c["canonical_name"].lower(): c for c in concepts}

    # Collect matching concepts
    matched_concepts = []
    for name, c in concept_idx.items():
        if name in seeds or any(s in name for s in seeds):
            matched_concepts.append(c)

    matched_names = {c["canonical_name"] for c in matched_concepts}

    # Collect relationships between matched concepts
    matched_rels = []
    for rel in rels:
        src = rel.get("source_concept_id", "")
        tgt = rel.get("target_concept_id", "")
        if src in matched_names and tgt in matched_names:
            matched_rels.append(rel)

    out_path = Path(args.out) if args.out else OUTPUT_DIR / "demo_subset.json"
    subset = {
        "metadata": {
            "description": f"Subgraph for: {', '.join(sorted(seeds))}",
            "source": "phase_6_knowledge_graph.json",
        },
        "concepts": matched_concepts,
        "relationships": matched_rels,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(subset, f, ensure_ascii=False, indent=2)

    print(f"\nExported subgraph to {out_path}")
    print(f"  Concepts: {len(matched_concepts)}")
    print(f"  Relationships: {len(matched_rels)}")


# ====================================================================
# Helpers
# ====================================================================

def _load_concept_index() -> dict[str, dict]:
    data = _load("p6")
    return {c["canonical_name"]: c for c in data.get("concepts", [])}


# ====================================================================
# Main
# ====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo data explorer for the Openlibrary KG"
    )
    parser.add_argument("--cooc", action="store_true",
                        help="Show top co-occurrence pairs")
    parser.add_argument("--syn", action="store_true",
                        help="Show top synonym pairs")
    parser.add_argument("--poly", action="store_true",
                        help="Show polysemous concepts")
    parser.add_argument("--hubs", action="store_true",
                        help="Show most-connected concepts")
    parser.add_argument("--syn-hubs", action="store_true",
                        help="Show concepts with most synonym edges")
    parser.add_argument("--ego", type=str, metavar="CONCEPT",
                        help="Show ego network around a concept")
    parser.add_argument("--search", type=str, metavar="PATTERN",
                        help="Search concepts by name pattern")
    parser.add_argument("--subgraph", type=str, metavar="A,B,C",
                        help="Export subgraph for comma-separated concept names")
    parser.add_argument("--out", type=str, default=None,
                        help="Output file for --subgraph")

    # Filters
    parser.add_argument("--subdomain", type=str, default=None,
                        help="Filter co-occurrence by subdomain")
    parser.add_argument("--cross", action="store_true",
                        help="Show only cross-subdomain co-occurrence")
    parser.add_argument("--track", type=str,
                        choices=["naming_variant", "domain_equivalence"],
                        help="Filter synonyms by track")
    parser.add_argument("--sort-by-count", action="store_true",
                        help="Sort co-occurrence by raw count instead of weight")
    parser.add_argument("--min-meanings", type=int, default=None,
                        help="Min polysemy clusters to show")
    parser.add_argument("-n", type=int, default=None,
                        help="Number of results to show")

    args = parser.parse_args()

    # Determine action
    if args.cooc:
        show_cooc(args)
    elif args.syn:
        show_syn(args)
    elif args.poly:
        show_poly(args)
    elif args.hubs:
        show_hubs(args)
    elif args.syn_hubs:
        show_syn_hubs(args)
    elif args.ego:
        show_ego(args)
    elif args.search:
        show_search(args)
    elif args.subgraph:
        show_subgraph(args)
    else:
        # Default: print a summary of all data available
        print_summary()


def print_summary() -> None:
    """Print a summary of all available demo data."""
    p3 = _load("p3")
    p4 = _load("p4")
    p5 = _load("p5")
    p6 = _load("p6")

    rels3 = p3.get("relationships", [])
    tracks = defaultdict(int)
    for r in rels3:
        tracks[r.get("metadata", {}).get("track", "unknown")] += 1

    poly = p4.get("polysemous_concepts", {})

    rels5 = p5.get("relationships", [])
    sds = defaultdict(int)
    cross_sd = 0
    for r in rels5:
        sds[r.get("metadata", {}).get("dominant_subdomain", "_other")] += 1
        if r.get("metadata", {}).get("cross_subdomain_penalized"):
            cross_sd += 1

    concepts = p6.get("concepts", [])
    all_rels = p6.get("relationships", [])

    print("=" * 60)
    print("  Openlibrary KG — Demo Data Summary")
    print("=" * 60)
    print(f"""
  Concepts:              {len(concepts)}
  Total relationships:   {len(all_rels)}
    Synonyms:            {len(rels3)}
      Naming variants:   {tracks.get('naming_variant', 0)}
      Domain equivalence:{tracks.get('domain_equivalence', 0)}
    Co-occurrence:       {len(rels5)}
      Same-subdomain:    {len(rels5) - cross_sd}
      Cross-subdomain:   {cross_sd}

  Polysemous concepts:   {len(poly)}
    With 2 meanings:     {sum(1 for v in poly.values() if len(v) == 2)}
    With 3-5 meanings:   {sum(1 for v in poly.values() if 3 <= len(v) <= 5)}
    With 6+ meanings:    {sum(1 for v in poly.values() if len(v) >= 6)}

  Top 5 subdomains by co-occurrence:
""")
    for sd, cnt in sorted(sds.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        print(f"    {sd:<25} {cnt:>4} pairs")

    print(f"""
  Quick examples:
    python scripts/demo_data.py --cooc --subdomain accounts
    python scripts/demo_data.py --syn --track domain_equivalence
    python scripts/demo_data.py --poly --min-meanings 10
    python scripts/demo_data.py --ego user
    python scripts/demo_data.py --search borrow
    python scripts/demo_data.py --hubs
    python scripts/demo_data.py --subgraph user,book,author --out demo_subset.json

  Pipe to file:
    python scripts/demo_data.py --syn --track naming_variant -n 50 > demo_synonyms.txt
""")


if __name__ == "__main__":
    main()
