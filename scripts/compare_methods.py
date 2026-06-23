#!/usr/bin/env python
"""Side-by-side comparison: BM25 baseline vs KG-walk localizer.

Runs both methods on the same SWE-bench Pro openlibrary ground truth and
prints a comparison table. Also writes per-issue dumps so you can inspect
exactly which issues each method got right.

Usage:
    python scripts/compare_methods.py

    # Skip BM25 indexing (e.g. if you already have it cached, not supported yet)
    python scripts/compare_methods.py --no-bm25

    # Custom top-K
    python scripts/compare_methods.py --top-k 5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openlibrary_kg.config import load_config
from openlibrary_kg.downstream.baselines import BM25Baseline
from openlibrary_kg.downstream.issue_localization import IssueLocalizer, evaluate
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.utils.logging import setup_logging


def _fmt_pct(x: float) -> str:
    return f"{100*x:.1f}%"


def _print_table(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["method", "n", "top_k",
            "file_recall_at_k", "file_mrr",
            "function_recall_at_k", "function_mrr"]
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("-" * len(line))
    for r in rows:
        print(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare KG vs BM25 baseline")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--kg", default="output/phase_6_knowledge_graph.json")
    parser.add_argument(
        "--ground-truth", default="output/swebench_ground_truth.json",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--level", choices=["file", "function", "both"], default="both",
    )
    parser.add_argument("--no-bm25", action="store_true")
    parser.add_argument("--no-kg", action="store_true")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Disable polysemy disambiguation in KG method")
    parser.add_argument("--out-dir", default="output")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []

    if not args.no_bm25:
        print("\n=== Running BM25 baseline ===")
        bm25 = BM25Baseline(codebase_root=config.codebase.root)
        result_bm25 = evaluate(
            bm25, args.ground_truth,
            top_k=args.top_k, level=args.level,
            per_issue_out=out_dir / "compare_per_issue_bm25.json",
        )
        result_bm25_display = {
            "method": "BM25",
            "n": int(result_bm25["n"]),
            "top_k": int(result_bm25["top_k"]),
            "file_recall_at_k": _fmt_pct(result_bm25.get("file_recall_at_k", 0.0)),
            "file_mrr": f"{result_bm25.get('file_mrr', 0.0):.3f}",
            "function_recall_at_k": _fmt_pct(result_bm25.get("function_recall_at_k", 0.0)),
            "function_mrr": f"{result_bm25.get('function_mrr', 0.0):.3f}",
        }
        summary_rows.append(result_bm25_display)
        print(json.dumps(result_bm25, indent=2))

    if not args.no_kg:
        print("\n=== Running KG-walk localizer ===")
        embed = None
        if not args.no_embeddings:
            embed = SentenceTransformerProvider(model=config.embedding.model)
        kg = IssueLocalizer(kg_path=args.kg, embedding_provider=embed)
        result_kg = evaluate(
            kg, args.ground_truth,
            top_k=args.top_k, level=args.level,
            per_issue_out=out_dir / "compare_per_issue_kg.json",
        )
        result_kg_display = {
            "method": "KG-walk",
            "n": int(result_kg["n"]),
            "top_k": int(result_kg["top_k"]),
            "file_recall_at_k": _fmt_pct(result_kg.get("file_recall_at_k", 0.0)),
            "file_mrr": f"{result_kg.get('file_mrr', 0.0):.3f}",
            "function_recall_at_k": _fmt_pct(result_kg.get("function_recall_at_k", 0.0)),
            "function_mrr": f"{result_kg.get('function_mrr', 0.0):.3f}",
        }
        summary_rows.append(result_kg_display)
        print(json.dumps(result_kg, indent=2))

    if summary_rows:
        print("\n=== Summary ===")
        _print_table(summary_rows)

        with open(out_dir / "compare_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_rows, f, ensure_ascii=False, indent=2)
        print(f"\nWrote per-issue dumps + summary to {out_dir}/")


if __name__ == "__main__":
    main()
