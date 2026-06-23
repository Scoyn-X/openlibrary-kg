#!/usr/bin/env python
"""Phase 9 (downstream): Run / evaluate issue localization against the KG.

Usage:
    # One-shot lookup (no ground truth needed)
    python scripts/locate_issue.py --title "User can't log in with email"

    # Full evaluation against ground truth
    python scripts/locate_issue.py --eval --ground-truth output/issue_ground_truth.json
"""

from __future__ import annotations

import argparse
import json

from openlibrary_kg.config import load_config
from openlibrary_kg.downstream.issue_localization import IssueLocalizer, evaluate
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue localization on the KG")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--kg", default="output/phase_6_knowledge_graph.json")
    parser.add_argument("--title", help="Issue title (single-issue mode)")
    parser.add_argument("--body", default="", help="Issue body")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--eval", action="store_true",
                        help="Run evaluation against --ground-truth")
    parser.add_argument(
        "--ground-truth",
        default="output/swebench_ground_truth.json",
        help="Path to ground-truth JSON (SWE-bench or GitHub-issue format)",
    )
    parser.add_argument(
        "--level", choices=["file", "function", "both"], default="both",
        help="Evaluation granularity",
    )
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Skip embedding-based polysemy disambiguation")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)

    embed = None
    if not args.no_embeddings:
        embed = SentenceTransformerProvider(model=config.embedding.model)

    loc = IssueLocalizer(
        kg_path=args.kg,
        embedding_provider=embed,
    )

    if args.eval:
        result = evaluate(loc, args.ground_truth, top_k=args.top_k, level=args.level)
        print(json.dumps(result, indent=2))
        return

    if not args.title:
        parser.error("Either --title or --eval is required.")

    ranked = loc.localize(args.title, args.body, top_k=args.top_k)
    print(json.dumps(ranked, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
