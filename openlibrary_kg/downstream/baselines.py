"""Baseline retrievers for issue localization, used to validate that the
KG-based localizer provides value over standard IR.

Currently implements:
  - BM25Baseline: classic BM25 over the full text of every .py file in the
    openlibrary subtree. No KG, no LLM, no embeddings.

These baselines expose the same interface as IssueLocalizer.localize() so
that the same evaluate() function works on them.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("openlibrary_kg.downstream.baselines")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _tokenize_code(text: str) -> list[str]:
    """Tokenize a Python source file for BM25.

    We split identifiers like `get_user_email` into `get`, `user`, `email`
    too (in addition to keeping `get_user_email` as a whole token), because
    that mirrors how the KG concept tokens are split — making the BM25
    baseline a more faithful "same input signal" comparison.
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text):
        lo = tok.lower()
        out.append(lo)
        # Also append snake_case parts
        if "_" in lo:
            for part in lo.split("_"):
                if len(part) >= 2:
                    out.append(part)
    return out


def _tokenize_issue(text: str) -> list[str]:
    """Issue-side tokenizer — share with IssueLocalizer for a fair comparison."""
    from openlibrary_kg.downstream.issue_localization import _tokenize
    return _tokenize(text)


class BM25Baseline:
    """BM25 retriever over the openlibrary Python files.

    Indexes every .py file (excluding tests/vendor/mocks) under the
    codebase root. Issue tokens are scored against each file via BM25.
    """

    def __init__(
        self,
        codebase_root: str | Path,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        k1: float = 1.5,
        b: float = 0.75,
        top_functions_per_file: int = 5,
    ):
        self.codebase_root = Path(codebase_root)
        self.k1 = k1
        self.b = b
        self.top_functions_per_file = top_functions_per_file

        # Discover files
        from openlibrary_kg.extraction.file_discovery import discover_python_files
        self.files: list[Path] = discover_python_files(
            self.codebase_root,
            include_patterns=include_patterns or ["**/*.py"],
            exclude_patterns=exclude_patterns or [
                "**/tests/**", "**/vendor/**", "**/mocks/**", "**/conftest.py",
            ],
        )

        self.file_paths: list[str] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_freqs: list[Counter] = []
        self.doc_lens: list[int] = []
        # For function-level prediction, we need the function symbols per file.
        # We use a quick regex grep (NOT a full AST parse) to keep this baseline
        # truly "baseline" — no shared infrastructure with the KG pipeline.
        self.file_functions: list[list[str]] = []
        df: Counter = Counter()

        func_re = re.compile(
            r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        )

        for fp in self.files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            tokens = _tokenize_code(text)
            if not tokens:
                continue
            freqs = Counter(tokens)
            self.file_paths.append(fp.as_posix())
            self.doc_tokens.append(tokens)
            self.doc_freqs.append(freqs)
            self.doc_lens.append(len(tokens))
            self.file_functions.append(func_re.findall(text))
            for term in freqs:
                df[term] += 1

        self.N = len(self.file_paths)
        self.avgdl = (sum(self.doc_lens) / self.N) if self.N else 0.0

        # BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1)
        self.idf: dict[str, float] = {}
        for term, d in df.items():
            self.idf[term] = math.log(1.0 + (self.N - d + 0.5) / (d + 0.5))

        logger.info(
            "BM25Baseline indexed %d files, vocab=%d, avgdl=%.1f",
            self.N, len(self.idf), self.avgdl,
        )

    def _score_file(self, query_tokens: list[str], idx: int) -> float:
        freqs = self.doc_freqs[idx]
        dl = self.doc_lens[idx]
        if dl == 0:
            return 0.0
        score = 0.0
        denom_norm = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
        for term in query_tokens:
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            idf = self.idf.get(term, 0.0)
            score += idf * (tf * (self.k1 + 1)) / (tf + denom_norm)
        return score

    def localize(
        self,
        title: str,
        body: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Same return schema as IssueLocalizer.localize()."""
        tokens = _tokenize_issue((title or "") + "\n" + (body or ""))
        if not tokens or self.N == 0:
            return []

        scores = [(i, self._score_file(tokens, i)) for i in range(self.N)]
        scores.sort(key=lambda kv: kv[1], reverse=True)

        results: list[dict[str, Any]] = []
        # Pre-build a regex from query tokens once for function ranking.
        query_set = set(tokens)
        for idx, score in scores[:top_k]:
            if score <= 0:
                continue
            fp = self.file_paths[idx]
            funcs = self.file_functions[idx]
            # Rank functions by how many query tokens occur in their name.
            ranked_funcs: list[tuple[str, float]] = []
            seen: set[str] = set()
            for fn in funcs:
                if fn in seen:
                    continue
                seen.add(fn)
                fn_tokens = set(_tokenize_code(fn))
                overlap = len(fn_tokens & query_set)
                if overlap > 0:
                    ranked_funcs.append((fn, float(overlap)))
            ranked_funcs.sort(key=lambda kv: kv[1], reverse=True)
            top_funcs = [
                {"name": n, "score": s}
                for n, s in ranked_funcs[: self.top_functions_per_file]
            ]
            # If no function had query-token overlap, fall back to listing first
            # few defs so function-level eval still has something to compare.
            if not top_funcs and funcs:
                top_funcs = [
                    {"name": fn, "score": 0.0}
                    for fn in funcs[: self.top_functions_per_file]
                ]
            top_func, top_func_score = (
                (top_funcs[0]["name"], top_funcs[0]["score"])
                if top_funcs else ("", 0.0)
            )
            results.append({
                "file_path": fp,
                "score": score,
                "top_function": top_func,
                "top_function_score": top_func_score,
                "top_functions": top_funcs,
                "matched_concepts": [],
            })
        return results
