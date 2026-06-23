#!/usr/bin/env python
"""Phase 8 (downstream): Build the issue-localization ground truth.

Fetches recent closed issues from the openlibrary GitHub repo and records
each issue together with the file paths of the merged PR that fixed it.

Usage:
    # Anonymous (limited to 60 requests/hour)
    python scripts/build_issue_ground_truth.py --max-pages 3 --max-issues 100

    # Authenticated (5000 req/hour) — recommended
    set GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx     # Windows
    export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx  # Linux/macOS
    python scripts/build_issue_ground_truth.py

Output:
    output/issue_ground_truth.json
"""

from __future__ import annotations

import argparse

from openlibrary_kg.downstream.ground_truth import build_ground_truth
from openlibrary_kg.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Build issue-localization ground truth")
    parser.add_argument("--repo", default="internetarchive/openlibrary")
    parser.add_argument("--output", default="output/issue_ground_truth.json")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="GitHub pages to fetch (per_page=100 each)")
    parser.add_argument("--max-issues", type=int, default=200,
                        help="Cap on issues processed after fetching")
    args = parser.parse_args()

    setup_logging("INFO", "kg_construction.log")
    build_ground_truth(
        repo=args.repo,
        out_path=args.output,
        max_pages=args.max_pages,
        max_issues=args.max_issues,
    )


if __name__ == "__main__":
    main()
