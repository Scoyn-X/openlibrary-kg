"""Concept-focused code skeleton generation.

Given a ranked set of files and the concepts matched to each, produce a
condensed code view that:
  - Preserves imports and class/function signatures (structure).
  - Includes full code snippets for functions that contain matched concepts.
  - Excludes everything else (noise reduction).

This skeleton is designed to be pasted into an LLM prompt for precise
bug localization — it gives the LLM enough structure to understand the
file layout while focusing its attention on the relevant functions.

Why not just give the full file?
  SWE-bench files can be 500-2000+ lines. LLMs suffer from "lost in the
  middle" — relevant code buried in a long context is often missed.
  The skeleton filters to ~100-300 lines of highly relevant code.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("openlibrary_kg.downstream.skeleton_generator")


class SkeletonGenerator:
    """Generate concept-focused code skeletons for matched files."""

    def __init__(self, kg: dict[str, Any]):
        # Build: file_path → {function_name → set of concept_names}
        self._file_func_concepts: dict[
            str, dict[str, set[str]]
        ] = defaultdict(lambda: defaultdict(set))
        # Build: file_path → {function_name → best_code_snippet}
        self._file_func_snippets: dict[str, dict[str, str]] = defaultdict(dict)

        logger.info(
            "SkeletonGenerator: indexing occurrences across %d concepts...",
            len(kg.get("concepts", [])),
        )
        occ_count = 0
        for c in kg.get("concepts", []):
            cname = c.get("canonical_name", "")
            if not cname:
                continue
            for occ in c.get("occurrences", []):
                occ_count += 1
                ctx = occ.get("context", {})
                fp = ctx.get("file_path", "")
                if not fp:
                    continue
                func = ctx.get("function_name") or "__module__"
                self._file_func_concepts[fp][func].add(cname)
                snippet = ctx.get("code_snippet", "")
                # Keep the longest snippet for each function
                if len(snippet) > len(
                    self._file_func_snippets[fp].get(func, "")
                ):
                    self._file_func_snippets[fp][func] = snippet
        logger.info(
            "SkeletonGenerator: indexed %d occurrences → %d files, %d unique functions",
            occ_count,
            len(self._file_func_concepts),
            sum(len(funcs) for funcs in self._file_func_concepts.values()),
        )

    def generate(
        self,
        file_path: str,
        matched_concepts: list[str],
        max_functions: int = 10,
    ) -> str:
        """Generate a code skeleton for one file, focused on matched concepts.

        Args:
            file_path: The file to skeletonize.
            matched_concepts: Concepts that were matched for this file.
            max_functions: Max number of relevant function snippets to include.

        Returns:
            A string containing the skeleton, or empty string if nothing found.
        """
        func_concepts = self._file_func_concepts.get(file_path, {})
        func_snippets = self._file_func_snippets.get(file_path, {})
        if not func_concepts:
            return ""

        matched_set = set(matched_concepts)

        # Score each function by how many matched concepts it contains
        scored: list[tuple[str, float, str]] = []
        for func, concepts in func_concepts.items():
            overlap = len(concepts & matched_set)
            if overlap == 0:
                continue
            snippet = func_snippets.get(func, "")
            scored.append((func, float(overlap), snippet))

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:max_functions]

        lines: list[str] = [
            f"# Skeleton for: {file_path}",
            f"# Matched concepts: {', '.join(matched_concepts[:15])}",
            f"# Relevant functions: {len(top)} / {len(func_concepts)} total",
            "",
        ]

        for func, score, snippet in top:
            label = func if func != "__module__" else "(module-level)"
            lines.append(f"# --- [{label}] (concept hits: {int(score)}) ---")
            lines.append(snippet.rstrip())
            lines.append("")

        return "\n".join(lines)

    def generate_batch(
        self,
        ranked_files: list[dict[str, Any]],
        max_files: int = 5,
        max_functions_per_file: int = 10,
    ) -> str:
        """Generate skeletons for the top-ranked files, concatenated.

        Returns a single string suitable for appending to an LLM prompt.
        """
        parts: list[str] = []
        for entry in ranked_files[:max_files]:
            fp = entry.get("file_path", "")
            matched = entry.get("matched_concepts", [])
            skeleton = self.generate(fp, matched, max_functions_per_file)
            if skeleton:
                parts.append(skeleton)
        return "\n\n".join(parts)
