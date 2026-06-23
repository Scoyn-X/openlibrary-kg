"""
Experiment A: LLM translates issue text into KG-friendly terminology.
Augments the ground truth JSON with translated terms, then re-evaluates.
"""
import json
import asyncio
import sys
from pathlib import Path

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")
OUTPUT = PROJECT / "output"

from openlibrary_kg.config import load_config
from openlibrary_kg.llm.openai_client import OpenAIClient
from openlibrary_kg.llm.rate_limiter import RateLimiter

SYSTEM_PROMPT = """You are a codebase terminology expert for OpenLibrary (openlibrary.org).

Your task: Given a bug report / issue description, extract or infer the MOST LIKELY 5-15 source-code-level terms (identifiers, function names, variable names) that would appear in the Python files related to this issue.

Rules:
- Output ONLY a comma-separated list of lowercase terms, no explanation.
- Think about what the developer would name classes/functions/variables in the code that fixes this issue.
- Use snake_case for multi-word terms (e.g., "loan_limit", "patron_account", "waitinglist_position").
- Prefer domain-specific terms over generic words (e.g., "isbn" over "identifier", "patron" over "user").
- If the issue mentions an API endpoint like "/lists/add", include "lists" and "add".
- If it mentions a technology like "solr", "marc", "isbn", include those.

Examples:
Issue: "Borrowing limit incorrectly blocks patron with 0 active loans"
Terms: borrow, loan, loan_limit, patron, active_loans, patron_account, check_borrowing_limit, get_active_loans, block, lending

Issue: "Edition.from_isbn() does not recognize ASIN"
Terms: edition, isbn, asin, from_isbn, identifier, validate, validation, isbn_lookup, amazon_id, edition_retrieval

Issue: "POST /lists/add returns 500 error when POST data conflicts with query parameters"
Terms: lists, add, from_input, normalize_input_seed, makelist, setvalue, post, query_params, seed, list_seeds"""


async def translate_issue(client: OpenAIClient, text: str, idx: int, total: int) -> str:
    """Call LLM to translate issue text → KG-friendly terms."""
    # Truncate very long issues
    truncated = text[:2000] if len(text) > 2000 else text

    try:
        result = await client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"Issue: {truncated}\n\nTerms:",
        )
        terms = result.strip().strip(",").strip()
        print(f"  [{idx+1}/{total}] → {terms[:120]}")
        return terms
    except Exception as e:
        print(f"  [{idx+1}/{total}] FAILED: {e}")
        return ""


async def main():
    config = load_config(str(PROJECT / "config.yaml"))
    rate_limiter = RateLimiter(rate=5.0, capacity=3)
    client = OpenAIClient(
        model="deepseek-chat",
        api_key=config.llm.api_key,
        api_key_env=config.llm.api_key_env,
        api_base=config.llm.api_base or "https://api.deepseek.com/v1",
        temperature=0.3,
        max_tokens=100,
        max_retries=2,
        rate_limiter=rate_limiter,
    )

    # Load ground truth
    gt_path = OUTPUT / "swebench_ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    total = len(gt)
    print(f"Translating {total} issues via LLM...\n")

    for i, rec in enumerate(gt):
        text = rec.get("problem_statement", "")
        # Also add title if separate
        terms = await translate_issue(client, text, i, total)
        rec["llm_terms"] = terms

    # Save augmented ground truth
    aug_path = OUTPUT / "swebench_ground_truth_llm.json"
    with open(aug_path, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {aug_path}")

    # Print a few examples
    print("\n--- Sample translations ---")
    for rec in gt[:5]:
        title = rec.get("problem_statement", "")[:100]
        terms = rec.get("llm_terms", "")
        print(f"  Issue: {title}...")
        print(f"  Terms: {terms}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
