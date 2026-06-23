#!/usr/bin/env python
"""Phase 2: Generate concept definitions using LLM.

Sampling strategies:
  --strategy random         (default) Random N occurrences (if --sample is set).
  --strategy stratified     For each concept with frequency >= --min-freq,
                            take up to --per-concept occurrences. Use this
                            when you want Phase 4 (polysemy) to have enough
                            material per concept while keeping total LLM cost
                            bounded.
  --strategy full           Ignore --sample, do everything.

Examples:
    # Test prompt quality on 50 random occurrences
    python scripts/generate_definitions.py --strategy random --sample 50

    # Demo-friendly: cover every freq>=8 concept with up to 5 occurrences each
    python scripts/generate_definitions.py --strategy stratified \\
                                           --min-freq 8 --per-concept 5

    # Full production run
    python scripts/generate_definitions.py
"""

from __future__ import annotations

import argparse
import asyncio
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from openlibrary_kg.config import load_config
from openlibrary_kg.llm.definition_generator import (
    DefinitionGenerationError,
    generate_definitions,
)
from openlibrary_kg.utils.io import read_json, write_json
from openlibrary_kg.utils.logging import setup_logging


def _stratified_sample(
    occurrences: list[dict[str, Any]],
    min_freq: int,
    per_concept: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Group by split_name; keep concepts with freq>=min_freq; take per_concept each.

    The point: with random sampling, low-frequency concepts get sampled lots
    of times but high-frequency ones don't get enough coverage either. For
    polysemy detection, we need *several definitions per concept* — stratified
    sampling guarantees this for any concept worth analysing.
    """
    rng = random.Random(seed)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occ in occurrences:
        name = occ.get("split_name", "")
        if name:
            by_name[name].append(occ)

    sampled: list[dict[str, Any]] = []
    eligible_concepts = 0
    for name, occs in by_name.items():
        if len(occs) < min_freq:
            continue
        eligible_concepts += 1
        if len(occs) <= per_concept:
            sampled.extend(occs)
        else:
            sampled.extend(rng.sample(occs, per_concept))
    return sampled


async def run_phase_2(
    input_file: str,
    output_file: str,
    config_path: str,
    sample: int | None,
    strategy: str,
    min_freq: int,
    per_concept: int,
    strict: bool,
) -> None:
    config = load_config(config_path)
    logger = setup_logging(config.logging.level, config.logging.file)

    logger.info("Loading Phase 1 concepts from %s", input_file)
    data = read_json(input_file)
    occurrences: list[dict[str, Any]] = data["occurrences"]
    original_count = len(occurrences)

    if strategy == "stratified":
        occurrences = _stratified_sample(
            occurrences, min_freq=min_freq, per_concept=per_concept,
        )
        unique_concepts = len({o.get("split_name", "") for o in occurrences})
        logger.info(
            "Stratified sample: %d occurrences across %d concepts "
            "(from %d total; min_freq=%d, per_concept=%d)",
            len(occurrences), unique_concepts, original_count,
            min_freq, per_concept,
        )
    elif strategy == "random" and sample:
        rng = random.Random(42)
        if sample < len(occurrences):
            occurrences = rng.sample(occurrences, sample)
        logger.info(
            "Random sample: %d / %d occurrences", len(occurrences), original_count,
        )
    else:
        logger.info("Full mode: %d occurrences", len(occurrences))

    try:
        occurrences = await generate_definitions(
            occurrences, config, sample=None, strict=strict,
        )
    except DefinitionGenerationError as exc:
        logger.error("Phase 2 aborted: %s", exc)
        raise SystemExit(2)

    non_empty = sum(1 for o in occurrences if o.get("definition"))
    output_data = {
        "phase": "phase_2_definitions",
        "metadata": {
            **data.get("metadata", {}),
            "definitions_generated": non_empty,
            "definitions_failed": len(occurrences) - non_empty,
            "llm_model": config.llm.model,
            "llm_provider": config.llm.provider,
            "sampling_strategy": strategy,
            "sampling_size": len(occurrences),
        },
        "occurrences": occurrences,
    }

    write_json(output_file, output_data, pretty=config.output.pretty_print)
    logger.info(
        "Written to %s (%d definitions / %d occurrences)",
        output_file, non_empty, len(occurrences),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: LLM Definition Generation")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default="output/phase_1_concepts.json")
    parser.add_argument("--output", default="output/phase_2_definitions.json")
    parser.add_argument(
        "--strategy",
        choices=["random", "stratified", "full"],
        default="full",
    )
    parser.add_argument("--sample", type=int, default=None,
                        help="(random) Number of occurrences to sample")
    parser.add_argument("--min-freq", type=int, default=8,
                        help="(stratified) Min concept frequency to include")
    parser.add_argument("--per-concept", type=int, default=5,
                        help="(stratified) Max occurrences per concept")
    parser.add_argument("--no-strict", action="store_true")
    args = parser.parse_args()

    asyncio.run(run_phase_2(
        args.input, args.output, args.config,
        sample=args.sample,
        strategy=args.strategy,
        min_freq=args.min_freq,
        per_concept=args.per_concept,
        strict=not args.no_strict,
    ))


if __name__ == "__main__":
    main()
