"""
LLM-native baseline for issue → file localization.

Sends the issue text + a list of all source files to an LLM (GPT-4o, Claude,
DeepSeek, etc.) and asks it to rank the top-10 most likely modified files.

This is a *modern* baseline — BM25 dates from 1994; LLM-native code
understanding represents the current frontier.  Comparing KG against both
tells a richer story:
  - BM25:   can basic keyword matching do this?
  - LLM:    can a frontier model with no codebase-specific training do this?
  - KG:     can a structured, explainable, zero-cost semantic graph compete?

Usage:
    1. Edit config.example.yaml → fill in api_key and model
    2. python scripts/eval_llm_baseline.py

Output:
    output/llm_baseline_eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")
OUTPUT = PROJECT / "output"


# ── Prompt template ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert software engineer familiar with the OpenLibrary (openlibrary.org) Python codebase.

I will give you:
1. A bug report / feature request (the "issue").
2. A list of ALL Python source files in the repository.

Your task: rank the TOP 10 files that are most likely to need modification
to fix or implement this issue.

Rules:
- Output ONLY a JSON array of file paths, in order of relevance (most likely first).
- Do NOT include files that are obviously unrelated.
- Do NOT explain your reasoning — just output the JSON array.
- Example output: ["core/lending.py", "plugins/upstream/account.py", "core/models.py"]

Consider:
- Which module/package handles the feature mentioned?
- Which files define the API endpoints, data models, or business logic involved?
- Are there utility files or import scripts that would also need changes?
"""

USER_PROMPT_TEMPLATE = """## Issue

{issue_text}

## All source files in the repository

{file_list}

## Top 10 most likely files to modify (JSON array)
"""


# ── File discovery (same logic as BM25 baseline) ────────────────────────

def discover_files(codebase_root: str) -> list[str]:
    """Return relative paths of all .py files, excluding tests/vendor."""
    root = Path(codebase_root)
    files = []
    for py_file in sorted(root.rglob("*.py")):
        fp = str(py_file)
        # Skip tests, vendor, mocks, conftest
        if any(x in fp.replace("\\", "/") for x in ["/tests/", "/vendor/", "/mocks/", "conftest.py"]):
            continue
        rel = py_file.relative_to(root).as_posix()
        files.append(rel)
    return files


# ── LLM client ──────────────────────────────────────────────────────────

class LLMBaselineClient:
    """Minimal OpenAI-compatible client for baseline evaluation."""

    def __init__(self, config: dict):
        import httpx

        self.model = config["model"]
        api_key = config["api_key"]
        api_base = config.get("api_base", "https://api.openai.com/v1")

        if not api_key:
            raise ValueError(
                "No API key found.  Edit the LLM baseline config with your key."
            )

        self._client = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0),
        )
        self.temperature = config.get("temperature", 0.0)
        self.max_tokens = config.get("max_tokens", 500)
        self.max_retries = config.get("max_retries", 2)

    async def rank_files(self, issue_text: str, file_list: list[str]) -> list[str]:
        """Ask the LLM to rank files for an issue."""
        prompt = USER_PROMPT_TEMPLATE.format(
            issue_text=issue_text[:6000],  # truncate very long issues
            file_list="\n".join(file_list),
        )

        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.post(
                    "/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    },
                )
                if resp.status_code != 200:
                    print(f"  API error {resp.status_code}: {resp.text[:200]}")
                    if attempt < self.max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return []

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return self._parse_response(content)

            except Exception as exc:
                print(f"  Request failed: {exc}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return []

    @staticmethod
    def _parse_response(content: str) -> list[str]:
        """Extract a JSON array of file paths from the LLM response."""
        # Try direct JSON parse
        try:
            result = json.loads(content)
            if isinstance(result, list):
                return [f for f in result if isinstance(f, str)]
        except json.JSONDecodeError:
            pass

        # Try extracting a JSON array with regex
        m = re.search(r"\[.*?\]", content, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
                if isinstance(result, list):
                    return [f for f in result if isinstance(f, str)]
            except json.JSONDecodeError:
                pass

        # Last resort: extract anything that looks like a file path
        paths = re.findall(r'"(core/[\w/]+\.py|plugins/[\w/]+\.py|scripts/[\w/]+\.py|solr/[\w/]+\.py|catalog/[\w/]+\.py|utils/[\w/]+\.py|fastapi/[\w/]+\.py|openlibrary/[\w/]+\.py)"', content)
        if paths:
            return paths

        print(f"  Could not parse LLM response: {content[:200]}")
        return []


# ── Main evaluation ─────────────────────────────────────────────────────

async def run_evaluation(config: dict, top_k: int = 10) -> dict[str, Any]:
    """Run the LLM-native baseline on all 91 SWE-bench issues."""
    # Load ground truth
    gt_path = OUTPUT / "swebench_ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))

    # Discover files
    codebase_root = config.get("codebase_root",
        "D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary")
    all_files = discover_files(codebase_root)
    print(f"Discovered {len(all_files)} Python files")

    # Create LLM client
    client = LLMBaselineClient(config["llm"])

    # Evaluate
    file_hits = 0
    mrr_sum = 0.0
    n = 0
    failures = 0
    per_issue = []

    for idx, rec in enumerate(gt):
        ps = rec.get("problem_statement", "")
        if not ps or not ps.strip():
            continue

        gt_files = rec.get("changed_files", [])
        if not gt_files:
            continue

        n += 1
        line0 = ps.split(chr(10))[0][:80].encode('ascii', errors='replace').decode('ascii')
        print(f"[{idx+1}/{len(gt)}] {line0}")

        try:
            ranked = await client.rank_files(ps, all_files)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            ranked = []
            failures += 1

        # Normalize paths for comparison
        def _norm(p: str) -> str:
            p = p.replace("\\", "/").split("openlibrary/", 1)[-1]
            return p

        gt_set = {_norm(f) for f in gt_files}

        file_rank = None
        for i, pred in enumerate(ranked[:top_k], 1):
            pred_norm = _norm(pred)
            for g in gt_set:
                if pred_norm.endswith(g) or g.endswith(pred_norm):
                    file_rank = i
                    file_hits += 1
                    mrr_sum += 1.0 / i
                    break
            if file_rank is not None:
                break

        per_issue.append({
            "instance_id": rec.get("instance_id", ""),
            "title": ps[:120],
            "gt_files": sorted(gt_set),
            "predicted_files": ranked[:top_k],
            "file_rank": file_rank,
        })

        pred_str = str(ranked[:3]).encode('ascii', errors='replace').decode('ascii')
        print(f"  rank={file_rank}, predicted={pred_str}")

    result = {
        "method": f"LLM-native ({config['llm']['model']})",
        "n": float(n),
        "top_k": float(top_k),
        "failures": float(failures),
        "file_recall_at_k": file_hits / n if n else 0.0,
        "file_mrr": mrr_sum / n if n else 0.0,
    }

    # Save
    out_path = OUTPUT / "llm_baseline_eval.json"
    out_path.write_text(
        json.dumps({"summary": result, "per_issue": per_issue}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved: {out_path}")
    return result


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM-native baseline for issue localization")
    parser.add_argument("--config", default="llm_baseline_config.json")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        print()
        print("Create llm_baseline_config.json with your API credentials:")
        print(json.dumps({
            "llm": {
                "model": "gpt-4o",
                "api_key": "sk-...",
                "api_base": "https://api.openai.com/v1",
                "temperature": 0.0,
                "max_tokens": 500,
                "max_retries": 2,
            },
            "codebase_root": "D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary",
        }, indent=2))
        sys.exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))

    result = asyncio.run(run_evaluation(config, top_k=args.top_k))

    print(f"\n{'='*60}")
    print(f"LLM-NATIVE BASELINE RESULT")
    print(f"{'='*60}")
    print(f"Model:   {config['llm']['model']}")
    print(f"N:       {int(result['n'])}")
    print(f"Recall@{args.top_k}: {100*result['file_recall_at_k']:.1f}%")
    print(f"MRR:     {result['file_mrr']:.3f}")

    # Also load existing results for comparison
    cmp = OUTPUT / "compare_summary.json"
    if cmp.exists():
        existing = json.loads(cmp.read_text(encoding="utf-8"))
        print(f"\n{'='*60}")
        print(f"COMPARISON")
        print(f"{'='*60}")
        for entry in existing:
            print(f"{entry['method']:>12}: Recall@10={entry['file_recall_at_k']}, MRR={entry['file_mrr']}")
        print(f"{result['method']:>12}: Recall@10={100*result['file_recall_at_k']:.1f}%, MRR={result['file_mrr']:.3f}")


if __name__ == "__main__":
    main()
