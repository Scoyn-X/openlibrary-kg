"""Demonstrate KG as an Agent-capable code knowledge infrastructure.

Shows three modes:
  1. agent-query    — structured concept cards, impact reports, path explanation
  2. llm-oracle     — LLM proposes concepts, KG validates/rejects/guides
  3. full-pipeline  — LLM oracle -> agent query -> impact report

Usage:
    python scripts/demo_kg_agent.py --mode agent-query \\
        --file core/lending.py

    python scripts/demo_kg_agent.py --mode llm-oracle \\
        --title "POST /lists/add returns 500 error when POST data conflicts with query parameters"

    python scripts/demo_kg_agent.py --mode full-pipeline \\
        --title "Borrowing limit incorrectly blocks patron with 0 active loans"

Config: uses the same llm_baseline_config.json for LLM mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")


def mode_agent_query(args):
    """Demonstrate agent-native KG queries."""
    from openlibrary_kg.kg_query import KGQuery
    kg = KGQuery(str(PROJECT / "output" / "phase_6_knowledge_graph.json"))
    print(f"KG loaded: {kg}")

    if args.concept:
        card = kg.concept_card(args.concept)
        if card:
            print(json.dumps(card, indent=2, ensure_ascii=False))
        else:
            print(f"Concept '{args.concept}' not found in KG.")

    if args.source and args.target:
        path = kg.explain_path(args.source, args.target, max_hops=args.hops)
        if path:
            print(f"\nPath: {path['explanation']}")
            for s in path["steps"]:
                print(f"  {s['from']} --[{s['edge_type']}]--> {s['to']}")
        else:
            print(f"No path found between '{args.source}' and '{args.target}'")

    if args.file:
        report = kg.impact_report([args.file])
        print(f"\n{report['recommendation']}")
        print(f"\nDirectly affected:")
        for item in report["per_file_detail"][args.file]["directly_affected_files"][:5]:
            print(f"  {item['path']}: {item['shared_concepts'][:3]}")
        print(f"\nIndirectly affected:")
        for item in report["per_file_detail"][args.file]["indirectly_affected_files"][:5]:
            print(f"  {item['path']}: via {item['via_concepts'][:3]}")


def mode_llm_oracle(args):
    """Demonstrate LLM-KG oracle dialogue."""
    from openlibrary_kg.kg_query import KGQuery
    from openlibrary_kg.downstream.llm_oracle import LLMOracle

    kg = KGQuery(str(PROJECT / "output" / "phase_6_knowledge_graph.json"))
    config_path = PROJECT / "llm_baseline_config.json"
    if not config_path.exists():
        print("Need llm_baseline_config.json with LLM credentials.")
        sys.exit(1)
    llm_config = json.loads(config_path.read_text(encoding="utf-8"))["llm"]

    oracle = LLMOracle(kg, llm_config)
    title = args.title or "POST /lists/add returns 500 error"
    body = args.body or ""

    print(f"Issue: {title[:120]}")
    print("Running LLM-KG oracle...")
    result = oracle.diagnose(title, body)

    print(f"\nConfidence: {result.confidence}")
    print(f"Round 1: {len(result.rounds[0].kg_validated)} validated, "
          f"{len(result.rounds[0].kg_rejected)} rejected")
    if result.rounds[0].kg_rejected:
        print(f"Rejected terms: {result.rounds[0].kg_rejected}")
    if result.rounds[0].kg_suggestions:
        print(f"KG suggested: {result.rounds[0].kg_suggestions[:10]}")

    print(f"\nTop files:")
    for f in result.files[:5]:
        chain = f.get("reasoning_chain", {})
        print(f"  [{f['score']:.2f}] {f['file_path']}")
        print(f"    concepts: {f['matched_concepts'][:5]}")
        if chain:
            print(f"    path: {chain.get('explanation', 'N/A')}")

    if hasattr(result, "impact") and result.impact:
        print(f"\n{result.impact.get('recommendation', '')}")


def mode_full_pipeline(args):
    """Full pipeline: oracle -> agent query -> impact report."""
    mode_llm_oracle(args)


def main():
    parser = argparse.ArgumentParser(description="Demonstrate KG as Agent infrastructure")
    parser.add_argument("--mode", choices=["agent-query", "llm-oracle", "full-pipeline"],
                        default="agent-query")
    # agent-query mode args
    parser.add_argument("--concept", help="Concept name for concept_card")
    parser.add_argument("--source", help="Source concept for explain_path")
    parser.add_argument("--target", help="Target concept for explain_path")
    parser.add_argument("--hops", type=int, default=4, help="Max hops for explain_path")
    parser.add_argument("--file", help="File path for reverse_impact")
    # llm-oracle mode args
    parser.add_argument("--title", help="Issue title")
    parser.add_argument("--body", help="Issue body")

    args = parser.parse_args()

    if args.mode == "agent-query":
        mode_agent_query(args)
    elif args.mode == "llm-oracle":
        mode_llm_oracle(args)
    elif args.mode == "full-pipeline":
        mode_full_pipeline(args)


if __name__ == "__main__":
    main()
