#!/usr/bin/env python
"""Phase 1: Extract concepts from the Openlibrary Python codebase.

Two-pass filtering:
  Pass 1 — per-identifier, via name_splitter (hard blocklist of stdlib,
           builtins, framework symbols, stop words).
  Pass 2 — corpus-level, drop concepts that appear in >50% of files
           (those are framework plumbing, not domain concepts).

Usage:
    python scripts/extract_concepts.py [--config config.yaml]

Output:
    output/phase_1_concepts.json
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from openlibrary_kg.config import Config, load_config
from openlibrary_kg.extraction.ast_parser import parse_file
from openlibrary_kg.extraction.file_discovery import discover_python_files
from openlibrary_kg.extraction.name_splitter import split_name_filter_nouns, split_identifier
from openlibrary_kg.extraction.noun_filter import filter_by_coverage
from openlibrary_kg.utils.io import write_json
from openlibrary_kg.utils.logging import setup_logging


def run_phase_1(config: Config) -> dict[str, Any]:
    """Run concept extraction and return the output data."""
    logger = setup_logging(config.logging.level, config.logging.file)

    root = Path(config.codebase.root)
    logger.info("Discovering Python files in %s", root)
    files = discover_python_files(
        root,
        include_patterns=config.codebase.include_patterns,
        exclude_patterns=config.codebase.exclude_patterns,
    )
    logger.info("Found %d Python files", len(files))

    # Use None so split_name_filter_nouns falls back to HARD_BLOCKLIST.
    # YAML-configured stop_words are merged in on top.
    extra_stop = {w.lower() for w in config.extraction.stop_words}
    keep_abbr = set(config.extraction.keep_abbreviations)
    min_len = config.extraction.min_identifier_length
    context_lines = config.extraction.context_lines

    from openlibrary_kg.extraction.noun_filter import HARD_BLOCKLIST
    effective_stop_words = HARD_BLOCKLIST | extra_stop

    all_occurrences: list[dict[str, Any]] = []
    file_count = 0
    occ_count = 0

    # Pass 1: AST + per-token hard blocklist
    # Also build a soft-index: tokens blocked from the KG but still useful
    # as lightweight file-level signals (e.g. "requests", "urllib").
    soft_index: dict[str, set[str]] = defaultdict(set)

    for fpath in files:
        occurrences = parse_file(fpath, context_lines=context_lines)
        if not occurrences:
            continue
        file_count += 1

        for occ in occurrences:
            # `import` identifiers don't enter the concept graph, but their
            # tokens ARE valuable as soft-index signals.  E.g. an issue
            # "refactor to use requests" should match files with `import requests`.
            if occ.identifier_type == "import":
                raw_tokens = split_identifier(occ.raw_identifier)
                for t in raw_tokens:
                    if len(t) >= min_len:
                        soft_index[t.lower()].add(fpath.as_posix())
                # Also record full raw identifier
                raw_lower = occ.raw_identifier.lower()
                if len(raw_lower) >= min_len:
                    soft_index[raw_lower].add(fpath.as_posix())
                continue

            split_name = split_name_filter_nouns(
                occ.raw_identifier,
                stop_words=effective_stop_words,
                keep_abbreviations=keep_abbr,
                min_length=min_len,
            )

            # ---- soft-index: capture blocked tokens as lightweight signals ----
            # When ALL tokens in an identifier are blocked, the identifier
            # carries zero domain-concept value, but its tokens may still be
            # useful for issue localization.  For example "requests" (HTTP
            # library) is blocked because it's a stdlib module, but an issue
            # saying "refactor to use requests" should still match the files
            # where `import requests` appears.
            # ---- soft-index: capture partial matches ──────────────────
            # Even when split_name is non-empty, some tokens may have been
            # blocked.  Record the FULL raw identifier so compound names
            # like `read_subjects` (→ "subjects" only) remain searchable.
            # Also record individual blocked tokens.
            # ───────────────────────────────────────────────────────────
            raw_tokens = split_identifier(occ.raw_identifier)
            raw_lower = occ.raw_identifier.lower()
            has_blocked_token = False
            for t in raw_tokens:
                if t.lower() in effective_stop_words:
                    has_blocked_token = True
                    if len(t) >= min_len:
                        soft_index[t.lower()].add(fpath.as_posix())
            # Record raw identifier if any token was blocked AND the full
            # name is not already a KG concept name
            if has_blocked_token and len(raw_lower) >= min_len:
                soft_index[raw_lower].add(fpath.as_posix())
            # ───────────────────────────────────────────────────────────

            if not split_name:
                continue

            occ.split_name = split_name
            occ_dict = occ.model_dump()
            all_occurrences.append(occ_dict)
            occ_count += 1

            # ---- compound-name preservation ─────────────────────────
            # When a multi-token identifier loses tokens to filtering
            # (e.g. "format_languages" → "languages", "read_subjects" →
            # "subjects"), the surviving single token is spread across
            # dozens of files and lacks the specificity to anchor an
            # issue.  We create a SECOND occurrence whose split_name is
            # the full raw identifier — giving the compound its own KG
            # concept with high IDF and natural co-occurrence edges.
            # ───────────────────────────────────────────────────────────
            kept_tokens = [t for t in split_name.split("_") if len(t) >= min_len]
            if has_blocked_token and len(kept_tokens) <= 1 and len(raw_tokens) >= 2:
                full_name = raw_lower
                if full_name not in effective_stop_words:
                    extra = occ.model_copy()
                    extra.split_name = full_name
                    extra_dict = extra.model_dump()
                    all_occurrences.append(extra_dict)
                    occ_count += 1

    logger.info(
        "Pass 1: extracted %d occurrences from %d files (before coverage filter)",
        occ_count, file_count,
    )

    # Pass 2: corpus-level coverage filter
    concept_to_files: dict[str, set[str]] = defaultdict(set)
    for occ in all_occurrences:
        name = occ.get("split_name", "")
        fpath = occ.get("context", {}).get("file_path", "")
        if name and fpath:
            concept_to_files[name].add(fpath)

    over_covered = filter_by_coverage(
        concept_to_files,
        total_files=file_count,
        max_file_ratio=0.5,
    )
    logger.info(
        "Pass 2: dropping %d concepts that appear in >50%% of files: %s",
        len(over_covered),
        sorted(over_covered)[:30],
    )

    filtered_occurrences = [
        o for o in all_occurrences if o.get("split_name", "") not in over_covered
    ]
    final_count = len(filtered_occurrences)
    logger.info(
        "Final: %d occurrences kept (dropped %d via coverage filter)",
        final_count, occ_count - final_count,
    )

    # Convert soft_index sets to sorted lists for JSON serialization
    soft_index_serializable: dict[str, list[str]] = {}
    for token, file_set in sorted(soft_index.items()):
        soft_index_serializable[token] = sorted(file_set)

    return {
        "phase": "phase_1_concepts",
        "metadata": {
            "codebase_root": str(root),
            "total_files_found": len(files),
            "total_files_parsed": file_count,
            "total_occurrences_pre_coverage": occ_count,
            "total_occurrences": final_count,
            "concepts_dropped_by_coverage": sorted(over_covered),
            "soft_index_tokens": len(soft_index_serializable),
        },
        "occurrences": filtered_occurrences,
        "soft_index": soft_index_serializable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: Concept Extraction")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--output", default=None, help="Override output directory")
    args = parser.parse_args()

    config = load_config(args.config)
    output_dir = Path(args.output or config.output.directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = run_phase_1(config)

    out_path = output_dir / "phase_1_concepts.json"
    write_json(out_path, data, pretty=config.output.pretty_print)
    print(f"Written {len(data['occurrences'])} occurrences to {out_path}")

    # Also save soft_index as a standalone file for downstream consumers
    si_path = output_dir / "soft_index.json"
    import json
    with open(si_path, "w", encoding="utf-8") as f:
        json.dump(data["soft_index"], f, ensure_ascii=False)
    print(f"Written soft_index ({len(data['soft_index'])} tokens) to {si_path}")


if __name__ == "__main__":
    main()
