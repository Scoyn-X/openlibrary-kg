"""SWE-bench Pro loader for openlibrary instances.

This module loads SWE-bench-Pro from HuggingFace, filters to openlibrary
instances, parses each gold patch to extract changed files + functions,
and writes a ground-truth JSON file consumed by issue_localization.evaluate.

Output schema (one record per instance):

    {
      "instance_id": "internetarchive__openlibrary-12345",
      "repo": "internetarchive/openlibrary",
      "base_commit": "abc123...",
      "problem_statement": "Issue title + body as one string",
      "changed_files":     ["openlibrary/accounts/model.py", ...],
      "changed_functions": [
          {"file": "openlibrary/accounts/model.py", "function": "get_user"},
          ...
      ],
      "patch": "<full diff>"      # kept for inspection / future use
    }

Patch parsing is intentionally dependency-free (a small mini-parser of
unified diff format) so we don't add `unidiff` as a runtime requirement.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("openlibrary_kg.downstream.swebench")

# Dataset name candidates. The official location is ScaleAI/SWE-bench_Pro
# (underscore before Pro). Other variants are kept as fallback in case Scale
# republishes or mirrors it elsewhere. Override with --dataset.
DATASET_CANDIDATES = (
    "ScaleAI/SWE-bench_Pro",
    "ScaleAI/SWE-bench-Pro",
    "princeton-nlp/SWE-bench_Pro",
    "swe-bench/SWE-bench_Pro",
)

DEFAULT_REPO = "internetarchive/openlibrary"


# --- Unified-diff mini-parser ----------------------------------------------

# +++ b/openlibrary/accounts/model.py
_FILE_HEADER_RE = re.compile(r"^\+\+\+\s+b/(.+?)\s*$")

# @@ -10,7 +10,9 @@ def get_user(uid):
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@(.*)$"
)

# Matches `def name(` or `class Name(` or `async def name(`
_DEF_RE = re.compile(
    r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\(:]"
)


def parse_patch(patch_text: str) -> dict[str, list]:
    """Parse a unified-diff patch.

    Returns {"changed_files": [...], "changed_functions": [{file, function}]}.

    Functions are extracted from two signals:
      1. Hunk header context (`@@ ... @@ def get_user(...)`): git shows the
         function the hunk is inside. Very reliable when present.
      2. `def`/`class` lines in added/context lines of the hunk body: catches
         newly-added definitions and refactors that move definitions.

    Both signals are unioned. We do NOT cross-check against the actual source
    at base_commit (would require checking the repo out); this gives slight
    over-recall but is cheap and sufficient for ranking-eval ground truth.
    """
    files: list[str] = []
    func_set: set[tuple[str, str]] = set()
    current_file: str | None = None
    in_python = False

    for line in patch_text.splitlines():
        m = _FILE_HEADER_RE.match(line)
        if m:
            path = m.group(1).strip()
            current_file = path
            in_python = path.endswith(".py")
            if in_python and path not in files:
                files.append(path)
            continue

        if not current_file or not in_python:
            continue

        m = _HUNK_HEADER_RE.match(line)
        if m:
            context = m.group(1)
            fm = _DEF_RE.search(context)
            if fm:
                func_set.add((current_file, fm.group(1)))
            continue

        # Inside a hunk: look at added or context lines (skip removed lines).
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith(" "):
            body = line[1:] if line[:1] in "+ " else line
            fm = _DEF_RE.search(body)
            if fm:
                func_set.add((current_file, fm.group(1)))

    funcs = [{"file": f, "function": n} for f, n in sorted(func_set)]
    return {"changed_files": files, "changed_functions": funcs}


# --- Dataset loading -------------------------------------------------------

def _try_load(dataset_name: str, split: str):
    """Attempt to load a single dataset name. Returns the dataset or None."""
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The `datasets` package is required. Install with: pip install datasets"
        ) from exc

    try:
        return load_dataset(dataset_name, split=split)
    except Exception as exc:
        logger.info("Could not load %s [%s]: %s", dataset_name, split, exc)
        return None


def load_openlibrary_instances(
    dataset_name: str | None = None,
    split: str = "test",
    repo_filter: str = DEFAULT_REPO,
) -> list[dict[str, Any]]:
    """Load SWE-bench Pro openlibrary instances.

    Args:
        dataset_name: HF dataset name. If None, try DATASET_CANDIDATES in order.
        split: Dataset split. SWE-bench convention: "test".
        repo_filter: Only keep instances whose `repo` matches this.

    Returns:
        List of instance dicts (raw fields preserved).
    """
    candidates: Iterable[str]
    if dataset_name:
        candidates = (dataset_name,)
    else:
        candidates = DATASET_CANDIDATES

    ds = None
    used_name = None
    for name in candidates:
        ds = _try_load(name, split)
        if ds is not None:
            used_name = name
            break

    if ds is None:
        raise RuntimeError(
            "Could not load SWE-bench-Pro from any known location. "
            f"Tried: {list(candidates)}. "
            "If the dataset lives elsewhere, pass --dataset <hf-name>."
        )

    logger.info("Loaded %s [%s] with %d rows", used_name, split, len(ds))
    rows = [dict(r) for r in ds if r.get("repo") == repo_filter]
    logger.info(
        "Filtered to %d instances for repo=%s", len(rows), repo_filter,
    )
    return rows


# --- Ground-truth assembly -------------------------------------------------

def build_swebench_ground_truth(
    out_path: str | Path = "output/swebench_ground_truth.json",
    dataset_name: str | None = None,
    split: str = "test",
    repo_filter: str = DEFAULT_REPO,
    max_instances: int | None = None,
) -> Path:
    """Load + parse + save the ground-truth file."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = load_openlibrary_instances(
        dataset_name=dataset_name, split=split, repo_filter=repo_filter,
    )
    if max_instances:
        rows = rows[:max_instances]

    records: list[dict[str, Any]] = []
    skipped_no_patch = 0
    skipped_no_py = 0

    for row in rows:
        patch = row.get("patch") or row.get("gold_patch") or ""
        if not patch:
            skipped_no_patch += 1
            continue

        parsed = parse_patch(patch)
        if not parsed["changed_files"]:
            # Patch only touches templates / JS / CSS — not useful for a Python KG.
            skipped_no_py += 1
            continue

        problem = (
            row.get("problem_statement")
            or row.get("issue_text")
            or (row.get("title", "") + "\n\n" + row.get("body", "")).strip()
        )

        records.append({
            "instance_id": row.get("instance_id", ""),
            "repo": row.get("repo", repo_filter),
            "base_commit": row.get("base_commit", ""),
            "problem_statement": problem,
            "changed_files": parsed["changed_files"],
            "changed_functions": parsed["changed_functions"],
            "patch": patch,
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    logger.info(
        "Wrote %d ground-truth records to %s "
        "(skipped: %d no-patch, %d no-python-file)",
        len(records), out_path, skipped_no_patch, skipped_no_py,
    )
    return out_path
