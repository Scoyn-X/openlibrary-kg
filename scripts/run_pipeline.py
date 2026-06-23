#!/usr/bin/env python
"""Full pipeline orchestrator: runs all phases sequentially.

Usage:
    python scripts/run_pipeline.py [--config config.yaml] [--sample N]

Phases:
    1. extract_concepts.py     — AST-based identifier extraction
    2. generate_definitions.py — LLM-based definition generation
    3. detect_synonyms.py     — Embedding-based synonym detection
    4. analyze_polysemy.py    — Definition clustering for polysemy
    5. analyze_cooccurrence.py— Function-level co-occurrence counting
    6. build_kg.py            — Final KG assembly and export
    7. export_to_neo4j.py     — Neo4j graph database import (--export-neo4j)
    8. navigate_issue.py      — Semantic navigation evaluation (--navigate)

Each phase writes intermediate JSON to output/ and can be rerun independently.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent


def run_phase(script_name: str, extra_args: list[str] | None = None) -> int:
    """Run a phase script and return its exit code."""
    script = SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script), "--config", "config.yaml"]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full KG construction pipeline")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--sample", type=int, default=None,
                        help="Number of concepts to sample for LLM phase (testing)")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip Phase 2 (LLM definition generation)")
    parser.add_argument("--export-neo4j", action="store_true",
                        help="Run Phase 7: Export to Neo4j after KG build")
    parser.add_argument("--navigate", action="store_true",
                        help="Run Phase 8: Semantic navigation evaluation after KG build")
    parser.add_argument("--navigate-top-k", type=int, default=10,
                        help="Top-K for Phase 8 evaluation (default 10)")
    parser.add_argument("--navigate-max-hops", type=int, default=3,
                        help="Max graph walk hops for Phase 8 (default 3)")
    parser.add_argument("--start-from", type=int, choices=range(1, 9),
                        help="Start from a specific phase (1-8)")
    args = parser.parse_args()

    phases = {
        1: ("extract_concepts.py", None, "Phase 1: Concept Extraction"),
        2: ("generate_definitions.py",
            ["--sample", str(args.sample)] if args.sample else None,
            "Phase 2: LLM Definition Generation"),
        3: ("detect_synonyms.py", None, "Phase 3: Synonym Detection"),
        4: ("analyze_polysemy.py", None, "Phase 4: Polysemy Analysis"),
        5: ("analyze_cooccurrence.py", None, "Phase 5: Co-occurrence Analysis"),
        6: ("build_kg.py", None, "Phase 6: KG Builder"),
        7: ("export_to_neo4j.py", None, "Phase 7: Neo4j Export"),
        8: ("navigate_issue.py",
            ["--top-k", str(args.navigate_top_k),
             "--max-hops", str(args.navigate_max_hops)],
            "Phase 8: Semantic Navigation"),
    }

    start = args.start_from or 1
    print(f"Pipeline starting from Phase {start}")

    for phase_num in range(start, 9):
        script, extra, description = phases[phase_num]

        if phase_num == 2 and args.skip_llm:
            print(f"\nSkipping {description} (--skip-llm)")
            continue

        if phase_num == 7 and not args.export_neo4j:
            print(f"\nSkipping {description} (use --export-neo4j to enable)")
            continue

        if phase_num == 8 and not args.navigate:
            print(f"\nSkipping {description} (use --navigate to enable)")
            continue

        print(f"\n{'='*60}")
        print(f"  {description}")
        print(f"{'='*60}")

        rc = run_phase(script, extra)
        if rc != 0:
            print(f"\n{description} failed with exit code {rc}")
            print("Fix the error and rerun with --start-from", phase_num)
            sys.exit(rc)

    print(f"\n{'='*60}")
    print("  Pipeline complete!")
    print(f"  Output files in output/ directory:")
    for f in sorted(Path("output").glob("*.json")):
        print(f"    {f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
