"""LLM-based definition generator for concept occurrences.

Orchestrates batching, caching, and rate-limited API calls.

Important behavior changes vs. the original version:

1. **Empty results are NOT cached.** Previously, when the LLM API failed
   (e.g. wrong model name), the generator stored `""` in the disk cache.
   That meant a second run hit cache and returned `""` again — definitions
   could never recover without `rm -rf .llm_cache`.
2. **Failure counts are reported and visible.** The previous code silently
   continued past every batch failure; now we count failures and the script
   exits with a non-zero `failure_rate` in the metadata so callers can act.
3. **Strict mode**: if `>50%` of definitions fail, raise an exception so the
   user sees the problem immediately instead of producing a 0-definition
   JSON file that looks superficially OK.
"""

from __future__ import annotations

import logging
from typing import Any

from openlibrary_kg.config import Config
from openlibrary_kg.llm.anthropic_client import AnthropicClient
from openlibrary_kg.llm.base import LLMClient
from openlibrary_kg.llm.openai_client import OpenAIClient
from openlibrary_kg.llm.prompt_templates import build_prompts
from openlibrary_kg.llm.rate_limiter import RateLimiter
from openlibrary_kg.utils.caching import DiskCache

logger = logging.getLogger("openlibrary_kg.llm")


class DefinitionGenerationError(RuntimeError):
    """Raised when LLM definition generation fails for too many occurrences."""


def _make_client(config: Config) -> LLMClient:
    """Create an LLM client based on configuration."""
    rate_limiter = RateLimiter(
        rate=config.llm.rate_limit.requests_per_second,
        capacity=config.llm.rate_limit.max_concurrent,
    )
    if config.llm.provider == "anthropic":
        return AnthropicClient(
            model=config.llm.model,
            api_key=config.llm.api_key,
            api_base=config.llm.api_base,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            max_retries=config.llm.max_retries,
            rate_limiter=rate_limiter,
        )
    return OpenAIClient(
        model=config.llm.model,
        api_key=config.llm.api_key,
        api_key_env=config.llm.api_key_env,
        api_base=config.llm.api_base,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        max_retries=config.llm.max_retries,
        rate_limiter=rate_limiter,
    )


async def generate_definitions(
    occurrences: list[dict[str, Any]],
    config: Config,
    sample: int | None = None,
    strict: bool = True,
    failure_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Generate definitions for concept occurrences using LLM.

    Args:
        occurrences: List of occurrence dicts from Phase 1.
        config: Full configuration.
        sample: If set, only process this many randomly-selected occurrences.
        strict: If True, raise when failure rate exceeds `failure_threshold`.
        failure_threshold: Maximum tolerated fraction of empty definitions.

    Returns:
        The same list with 'definition' fields filled in. Failed entries
        keep `definition = None` so downstream code can detect them.
    """
    client = _make_client(config)
    cache = DiskCache(config.llm.cache_dir)

    if sample and sample < len(occurrences):
        import random
        random.seed(42)
        occurrences = random.sample(occurrences, sample)

    prompts_data: list[tuple[int, tuple[str, str]]] = []
    for idx, occ in enumerate(occurrences):
        ctx = occ["context"]
        sys_p, usr_p = build_prompts(
            concept_name=occ["split_name"],
            raw_identifier=occ["raw_identifier"],
            file_path=ctx["file_path"],
            line_number=ctx["line_number"],
            class_name=ctx.get("class_name"),
            function_name=ctx.get("function_name"),
            block_type=ctx.get("block_type", "module"),
            code_snippet=ctx.get("code_snippet", ""),
        )
        prompts_data.append((idx, (sys_p, usr_p)))

    # Cache check — but only treat *non-empty* cached values as hits.
    uncached: list[tuple[int, tuple[str, str]]] = []
    cache_hits = 0
    for idx, (sys_p, usr_p) in prompts_data:
        cache_key = f"{sys_p}|||{usr_p}"
        cached = cache.get(cache_key)
        if cached and isinstance(cached, str) and cached.strip():
            occurrences[idx]["definition"] = cached
            cache_hits += 1
        else:
            occurrences[idx]["definition"] = None
            uncached.append((idx, (sys_p, usr_p)))

    logger.info(
        "Definitions: %d non-empty cache hits, %d to generate",
        cache_hits, len(uncached),
    )

    if not uncached:
        await client.close()
        return occurrences

    # Generate in batches
    failures = 0
    batch_size = config.llm.rate_limit.max_concurrent
    total_batches = (len(uncached) + batch_size - 1) // batch_size
    for batch_start in range(0, len(uncached), batch_size):
        batch = uncached[batch_start:batch_start + batch_size]
        batch_indices: list[int] = []
        batch_prompts: list[tuple[str, str]] = []
        for idx, prompt_pair in batch:
            batch_indices.append(idx)
            batch_prompts.append(prompt_pair)

        batch_no = batch_start // batch_size + 1
        logger.info(
            "Generating definitions batch %d/%d (%d prompts)",
            batch_no, total_batches, len(batch_prompts),
        )

        try:
            results = await client.generate_batch(batch_prompts)
        except Exception as exc:
            logger.error("Batch %d generation failed: %s", batch_no, exc)
            results = [""] * len(batch_prompts)

        for idx, (sys_p, usr_p), result in zip(batch_indices, batch_prompts, results):
            text = (result or "").strip()
            if not text:
                # Do NOT cache empty results — they would poison future runs.
                failures += 1
                occurrences[idx]["definition"] = None
                continue
            occurrences[idx]["definition"] = text
            cache.set(f"{sys_p}|||{usr_p}", text)

    await client.close()

    total = len(uncached)
    if total > 0:
        failure_rate = failures / total
        logger.info(
            "Definition generation: %d/%d failed (rate=%.2f)",
            failures, total, failure_rate,
        )
        if strict and failure_rate >= failure_threshold:
            # Don't silently produce a 0-definition file. Surface the problem.
            raise DefinitionGenerationError(
                f"LLM definition generation failed for {failures}/{total} "
                f"occurrences ({failure_rate:.0%}). Common causes:\n"
                f"  - Wrong model name in config.yaml "
                f"(check `llm.model` — deepseek only supports "
                f"'deepseek-chat' or 'deepseek-reasoner')\n"
                f"  - Invalid api_key / api_base\n"
                f"  - Rate-limit exceeded by upstream\n"
                f"Run with `--no-strict` to ignore and produce a partial "
                f"output, or fix the config and rerun."
            )

    return occurrences
