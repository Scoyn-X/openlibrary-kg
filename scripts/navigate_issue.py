#!/usr/bin/env python
"""Phase 8: Semantic Alignment Navigator — KG-based issue localization.

Runs the full navigation pipeline on SWE-bench ground truth and produces
ranked file/function recommendations with code skeletons and explanations.

Pipeline stages:
  1. Semantic entry   — embedding maps issue text → KG concept space
  2. Polysemy locking  — each matched concept locked to its best meaning cluster
  3. Multi-hop walk    — BFS over synonym + co-occurrence edges (up to 3 hops)
  4. File ranking      — weighted by path score × IDF
  5. Skeleton generation — concept-focused code view for top files
  6. Path explanation  — human-readable rationale for each recommendation

Output files (written to output/):
  phase_8_evaluation.json       — Recall@K, MRR, per-issue predictions
  phase_8_file_rankings.json    — Full ranked output per issue
  phase_8_navigation_report.md  — Human-readable report

Usage:
    # Full evaluation against SWE-bench ground truth
    python scripts/navigate_issue.py

    # Custom config / ground truth / top-K
    python scripts/navigate_issue.py --config config.yaml --top-k 10 \
        --ground-truth output/swebench_ground_truth.json

    # Single-issue lookup (no evaluation)
    python scripts/navigate_issue.py --title "User can't log in with email"

    # Run full eval with fallback comparison
    python scripts/navigate_issue.py --also-bm25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openlibrary_kg.config import load_config
from openlibrary_kg.downstream.issue_localization import IssueLocalizer, evaluate
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.utils.io import write_json
from openlibrary_kg.utils.logging import setup_logging


def _write_markdown_report(
    output_dir: Path,
    eval_result: dict[str, float],
    per_issue: list[dict] | None,
) -> Path:
    """Write a human-readable markdown report."""
    lines: list[str] = [
        "# Phase 8: Navigation Report",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Issues evaluated | {int(eval_result.get('n', 0))} |",
        f"| Failures | {int(eval_result.get('failures', 0))} |",
        f"| Top-K | {int(eval_result.get('top_k', 10))} |",
        f"| File Recall@K | {eval_result.get('file_recall_at_k', 0.0):.1%} |",
        f"| File MRR | {eval_result.get('file_mrr', 0.0):.4f} |",
    ]
    if "function_recall_at_k" in eval_result:
        lines.extend([
            f"| Function Recall@K | {eval_result.get('function_recall_at_k', 0.0):.1%} |",
            f"| Function MRR | {eval_result.get('function_mrr', 0.0):.4f} |",
            f"| Issues with function GT | {int(eval_result.get('n_with_function_gt', 0))} |",
        ])

    if per_issue:
        lines.extend([
            "",
            "## Per-Issue Results",
            "",
            "| # | Instance ID | Title | GT Files | Predicted Files | File Rank | Func Rank |",
            "|---|---|---|---|---|---|---|",
        ])
        for i, entry in enumerate(per_issue[:30], 1):
            gt = ", ".join(entry.get("gt_files", [])[:3]) or "(none)"
            pred = ", ".join(entry.get("predicted_files", [])[:3]) or "(none)"
            fr = entry.get("file_rank") or "-"
            funcr = entry.get("func_rank") or "-"
            lines.append(
                f"| {i} | {entry.get('instance_id', '?')[:30]} | "
                f"{entry.get('title', '')[:40]} | "
                f"{gt[:60]} | {pred[:60]} | {fr} | {funcr} |"
            )
        if len(per_issue) > 30:
            lines.append(f"| ... | ... | ... | ... | ... | ... | ... |")
            lines.append(f"| | | | | *({len(per_issue)} total)* | | |")

    report_path = output_dir / "phase_8_navigation_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 8: KG-based issue localization"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--kg", default="output/phase_6_knowledge_graph.json",
        help="Path to the KG JSON",
    )
    parser.add_argument(
        "--ground-truth",
        default="output/swebench_ground_truth.json",
        help="Path to ground-truth JSON for evaluation",
    )
    parser.add_argument("--title", help="Issue title (single-issue mode)")
    parser.add_argument("--body", default="", help="Issue body")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--level", choices=["file", "function", "both"], default="both",
    )
    parser.add_argument(
        "--max-hops", type=int, default=3,
        help="Max graph walk hops (1-5)",
    )
    parser.add_argument(
        "--no-skeleton", action="store_true",
        help="Skip skeleton generation (faster)",
    )
    parser.add_argument(
        "--also-bm25", action="store_true",
        help="Also run BM25 baseline for comparison",
    )
    parser.add_argument(
        "--out-dir", default="output",
        help="Output directory for results",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.logging.level, config.logging.file)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Setup embedding provider ──────────────────────────────────
    embed = None
    try:
        embed = SentenceTransformerProvider(model=config.embedding.model)
        logger.info("Embedding provider loaded: %s", config.embedding.model)
    except Exception as exc:
        logger.warning(
            "Failed to load embedding provider: %s. "
            "Semantic entry disabled; falling back to token match.",
            exc,
        )

    # ── Single-issue mode ─────────────────────────────────────────
    if args.title:
        loc = IssueLocalizer(
            kg_path=args.kg,
            embedding_provider=embed,
            max_hops=args.max_hops,
        )
        ranked = loc.localize(args.title, args.body, top_k=args.top_k)

        print(f"\nIssue: {args.title[:120]}")
        print(f"Top-{args.top_k} file recommendations:\n")
        for i, r in enumerate(ranked, 1):
            print(f"  #{i}  {r['file_path']}")
            print(f"       Score: {r['score']:.4f}  |  "
                  f"Top function: {r.get('top_function', '(none)')}")
            matched = r.get("matched_concepts", [])[:8]
            print(f"       Concepts: {', '.join(matched)}")
            skeleton = r.get("skeleton", "")
            if skeleton:
                print(f"       Skeleton: {len(skeleton)} chars")
            print()

        # Also write to file
        write_json(out_dir / "phase_8_single_issue.json", {
            "issue_title": args.title,
            "issue_body": args.body,
            "top_k": args.top_k,
            "results": ranked,
        })
        return

    # ── Evaluation mode ───────────────────────────────────────────
    gt_path = Path(args.ground_truth)
    if not gt_path.exists():
        logger.error(
            "Ground truth file not found: %s\n"
            "Run: python scripts/build_swebench_ground_truth.py",
            gt_path,
        )
        sys.exit(1)

    logger.info("Loading KG from %s", args.kg)
    loc = IssueLocalizer(
        kg_path=args.kg,
        embedding_provider=embed,
        max_hops=args.max_hops,
    )

    per_issue_out = out_dir / "phase_8_evaluation.json"
    logger.info(
        "Running evaluation on %s (top_k=%d, level=%s, max_hops=%d)",
        gt_path, args.top_k, args.level, args.max_hops,
    )

    result = evaluate(
        loc,
        str(gt_path),
        top_k=args.top_k,
        level=args.level,
        per_issue_out=str(per_issue_out),
    )

    # ── Print summary ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Phase 8 Navigation Results")
    print("=" * 60)
    print(f"  Issues evaluated:        {int(result['n'])}")
    print(f"  Failures:                {int(result.get('failures', 0))}")
    print(f"  Top-K:                   {int(result['top_k'])}")
    print(f"  Method:                  KG-walk-v2 (semantic + multi-hop)")
    print(f"  Max hops:                {args.max_hops}")
    if "file_recall_at_k" in result:
        print(f"  File Recall@{args.top_k}:         "
              f"{result['file_recall_at_k']:.2%}")
        print(f"  File MRR:                {result['file_mrr']:.4f}")
    if "function_recall_at_k" in result:
        print(f"  Function Recall@{args.top_k}:     "
              f"{result['function_recall_at_k']:.2%}")
        print(f"  Function MRR:            {result['function_mrr']:.4f}")
    print(f"\n  Full results: {per_issue_out}")

    # ── Markdown report ───────────────────────────────────────────
    with open(per_issue_out, encoding="utf-8") as f:
        eval_data = json.load(f)
    report_path = _write_markdown_report(
        out_dir, result, eval_data.get("per_issue"),
    )
    print(f"  Report:       {report_path}")

    # ── BM25 comparison (optional) ────────────────────────────────
    if args.also_bm25:
        print("\n--- BM25 Baseline ---")
        try:
            from openlibrary_kg.downstream.baselines import BM25Baseline

            bm25 = BM25Baseline(codebase_root=config.codebase.root)
            bm25_result = evaluate(
                bm25,
                str(gt_path),
                top_k=args.top_k,
                level=args.level,
                per_issue_out=str(out_dir / "phase_8_bm25_evaluation.json"),
            )
            print(f"  BM25 File Recall@{args.top_k}:     "
                  f"{bm25_result.get('file_recall_at_k', 0.0):.2%}")
            print(f"  BM25 File MRR:            "
                  f"{bm25_result.get('file_mrr', 0.0):.4f}")
            if "function_recall_at_k" in bm25_result:
                print(f"  BM25 Function Recall@{args.top_k}: "
                      f"{bm25_result.get('function_recall_at_k', 0.0):.2%}")
                print(f"  BM25 Function MRR:        "
                      f"{bm25_result.get('function_mrr', 0.0):.4f}")
        except Exception as exc:
            logger.error("BM25 baseline failed: %s", exc)

    print(f"\n{'=' * 60}")
    print("  Phase 8 complete.")


if __name__ == "__main__":
    main()
