#!/usr/bin/env python
"""Build the SWE-bench Pro ground truth for openlibrary.

Loads openlibrary instances from SWE-bench Pro (HuggingFace), parses each
instance's gold patch to extract changed files + functions, and writes
output/swebench_ground_truth.json.

Usage:
    # Use default dataset names (tries a few common locations)
    python scripts/build_swebench_ground_truth.py

    # Specify dataset explicitly
    python scripts/build_swebench_ground_truth.py --dataset ScaleAI/SWE-bench-Pro

    # Limit while debugging
    python scripts/build_swebench_ground_truth.py --max-instances 20

Pre-req:
    pip install datasets
"""

from __future__ import annotations

import argparse

from openlibrary_kg.downstream.swebench_loader import build_swebench_ground_truth
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SWE-bench Pro ground truth")
    parser.add_argument(
        "--dataset",
        default=None,
        help="HuggingFace dataset name. If omitted, try common candidates.",
    )
    parser.add_argument("--split", default="test", help="Dataset split")
    parser.add_argument(
        "--repo", default="internetarchive/openlibrary",
        help="Repo to filter to",
    )
    parser.add_argument(
        "--output", default="output/swebench_ground_truth.json",
    )
    parser.add_argument("--max-instances", type=int, default=None)
    args = parser.parse_args()

    setup_logging("INFO", "kg_construction.log")
    build_swebench_ground_truth(
        out_path=args.output,
        dataset_name=args.dataset,
        split=args.split,
        repo_filter=args.repo,
        max_instances=args.max_instances,
    )


if __name__ == "__main__":
    main()
