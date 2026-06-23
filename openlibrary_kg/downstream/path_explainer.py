"""Generate human-readable explanations for graph navigation results.

Converts a WalkResult + file ranking into natural-language explanations
that can be fed directly into an LLM prompt to guide bug localization.

Design: template-based (no LLM call required) by default. An optional
LLM-backed mode can be enabled for richer explanations, but the base
version works offline and is guaranteed not to fail.
"""

from __future__ import annotations

from typing import Any

from openlibrary_kg.downstream.graph_walker import GraphPath, WalkResult


def explain_file_ranking(
    ranked_files: list[dict[str, Any]],
    walk_result: WalkResult,
    concepts_by_name: dict[str, dict],
    max_files: int = 10,
) -> str:
    """Produce a multi-line explanation for the top-ranked files.

    Returns a string suitable for pasting into an LLM prompt as context.
    """
    lines: list[str] = [
        "=" * 60,
        "KG NAVIGATION REPORT",
        "=" * 60,
        "",
        f"Seed concepts matched from issue: {', '.join(walk_result.seed_concepts[:15])}",
        f"Total concepts reached via graph walk: {len(walk_result.concept_weights)}",
        f"Total paths explored: {len(walk_result.paths)}",
        "",
        "-" * 60,
        "TOP RANKED FILES",
        "-" * 60,
    ]

    for i, entry in enumerate(ranked_files[:max_files], 1):
        fp = entry.get("file_path", "")
        score = entry.get("score", 0.0)
        matched = entry.get("matched_concepts", [])[:10]
        top_func = entry.get("top_function", "")

        lines.append(f"\n  #{i}  {fp}")
        lines.append(f"      Score: {score:.4f}  |  Top function: {top_func or '(none)'}")

        # Explain which concepts contributed
        lines.append(f"      Matched concepts ({len(matched)}):")
        for name in matched[:8]:
            w = walk_result.concept_weights.get(name, 0.0)
            paths = walk_result.concept_paths.get(name, [])
            if paths:
                best_path = max(paths, key=lambda p: p.cumulative_weight)
                path_str = _format_path(best_path)
                lines.append(f"        - {name} (weight={w:.4f}) via {path_str}")
            else:
                lines.append(f"        - {name} (weight={w:.4f}, direct seed match)")

    lines.append("")
    lines.append("-" * 60)
    lines.append("CONCEPT DEFINITIONS (for context)")
    lines.append("-" * 60)

    # Collect all unique concepts mentioned
    mentioned: set[str] = set(walk_result.seed_concepts[:10])
    for entry in ranked_files[:max_files]:
        mentioned.update(entry.get("matched_concepts", [])[:5])

    for name in sorted(mentioned):
        c = concepts_by_name.get(name, {})
        clusters = c.get("definition_clusters", []) or []
        if clusters:
            for ci, cl in enumerate(clusters[:2]):
                d = cl.get("canonical_definition", "")[:120]
                if d:
                    label = f"  [{ci}]" if len(clusters) > 1 else ""
                    lines.append(f"  {name}{label}: {d}")
        else:
            occs = c.get("occurrences", [])
            for occ in occs[:1]:
                d = occ.get("definition", "")
                if d:
                    lines.append(f"  {name}: {d[:120]}")
                    break

    return "\n".join(lines)


def _format_path(path: GraphPath) -> str:
    """Format a single path as a readable string."""
    if not path.hops:
        return "direct seed match"
    parts: list[str] = [path.seed]
    for hop in path.hops:
        arrow = "~[syn]~>" if hop.edge_type == "synonym" else "~[cooc]~>"
        parts.append(f"{arrow} {hop.concept_name}")
    parts.append(f"(score={path.cumulative_weight:.4f})")
    return " ".join(parts)


def explain_with_llm(
    ranked_files: list[dict[str, Any]],
    walk_result: WalkResult,
    concepts_by_name: dict[str, dict],
    llm_client: Any,
    max_files: int = 5,
) -> str:
    """Use an LLM to produce a richer explanation.

    Args:
        llm_client: An instance with an async `generate(sys_prompt, usr_prompt)` method.
    """
    import asyncio

    base = explain_file_ranking(ranked_files, walk_result, concepts_by_name, max_files)

    system_prompt = (
        "You are a code navigation analyst. Given a knowledge graph navigation "
        "report, produce a concise summary (under 300 words) that explains: "
        "1) which concepts from the issue were matched, "
        "2) which files are most likely to need changes and why, "
        "3) any notable indirect paths (concept A led to concept B via "
        "co-occurrence, which led to a file). "
        "Be specific — mention file paths, concept names, and path types."
    )

    try:
        result = asyncio.run(llm_client.generate(system_prompt, base))
        return result if result else base
    except Exception:
        return base
