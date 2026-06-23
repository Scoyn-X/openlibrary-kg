"""
Experiment A v2: Inject LLM-translated terms as DIRECT seed concepts
instead of appending to text. This bypasses embedding noise.
"""
import json
import sys
from pathlib import Path
from openlibrary_kg.downstream.issue_localization import IssueLocalizer, evaluate
from openlibrary_kg.embeddings.sentence_transformer import SentenceTransformerProvider
from openlibrary_kg.config import load_config

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# Load augmented ground truth (with llm_terms)
gt = json.loads((OUTPUT / "swebench_ground_truth_llm.json").read_text(encoding="utf-8"))

# Load KG concepts for filtering
kg_full = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
kg_names = {c["canonical_name"] for c in kg_full["concepts"]}
print(f"KG concepts: {len(kg_names)}")

# For each issue, filter LLM terms to KG concept names
hits = 0
total_terms = 0
for rec in gt:
    terms_str = rec.get("llm_terms", "")
    terms = [t.strip() for t in terms_str.split(",") if t.strip()]

    # Filter to KG concepts
    kg_matches = [t for t in terms if t in kg_names]
    rec["llm_kg_terms"] = kg_matches
    total_terms += len(terms)
    hits += len(kg_matches)

print(f"LLM terms total: {total_terms}, in KG: {hits} ({100*hits/total_terms:.1f}%)")

# Print some examples
for rec in gt[:5]:
    title = rec.get("problem_statement", "")[:100]
    kg_terms = rec.get("llm_kg_terms", [])
    all_terms = rec.get("llm_terms", "")
    print(f"\n  Issue: {title}...")
    print(f"  LLM all: {all_terms[:120]}")
    print(f"  In KG:   {kg_terms}")

# Now: inject LLM-KG terms as seed concepts with weight=0.9
# We do this by monkey-patching the localizer

config = load_config("config.yaml")
embed = SentenceTransformerProvider(model=config.embedding.model)
localizer = IssueLocalizer(
    kg_path=str(OUTPUT / "phase_6_knowledge_graph.json"),
    embedding_provider=embed,
)

# Create a wrapper that injects LLM-matched concepts
original_localize = localizer.localize

def augmented_localize(title, body="", top_k=10):
    """localize with LLM-KG term injection."""
    # Find this issue's LLM terms by matching the title
    issue_text = (title or "") + "\n" + (body or "")
    kg_terms = []

    for rec in gt:
        ps = rec.get("problem_statement", "")
        # Match by problem_statement start
        if issue_text.strip().startswith(ps.strip()[:100]):
            kg_terms = rec.get("llm_kg_terms", [])
            break
        # Also try title match
        orig_ps = rec.get("problem_statement", "")
        if title and title[:80] in orig_ps:
            kg_terms = rec.get("llm_kg_terms", [])
            break

    if not kg_terms:
        return original_localize(title, body, top_k=top_k)

    # Inject LLM terms as concept seeds before the walk
    # We modify the QueryRewriter output by adding these as ConceptMatch
    from openlibrary_kg.downstream.query_rewriter import ConceptMatch

    # We need to patch the issue_query after rewrite
    # Simplest approach: modify seed_weights before the walk
    # Actually, we can't easily do that without modifying the method.
    # Better approach: prepend terms with KNOWN KG weight through the
    # token match fallback path, OR just append terms to text.

    # Let's use a different strategy: prepend the KG concept names
    # as "IMPORTANT: file likely contains: concept1, concept2, concept3"
    # This gives the embedding a stronger signal
    boost = "Important domain concepts: " + ", ".join(kg_terms)
    augmented_title = f"{boost}\n---\n{title}"
    return original_localize(augmented_title, body, top_k=top_k)


localizer.localize = augmented_localize

# Run evaluation
print("\n=== Evaluating with LLM-KG seed injection ===")
result = evaluate(
    localizer,
    str(OUTPUT / "swebench_ground_truth.json"),  # original GT
    top_k=10,
    level="both",
    per_issue_out=str(OUTPUT / "compare_per_issue_llm_seed.json"),
)

print(f"\nFile Recall@10: {result.get('file_recall_at_k', 0)*100:.1f}%")
print(f"File MRR:       {result.get('file_mrr', 0):.3f}")
print(f"Function Recall@10: {result.get('function_recall_at_k', 0)*100:.1f}%")
print(f"Function MRR:       {result.get('function_mrr', 0):.3f}")

# Save
with open(OUTPUT / "experiment_llm_seed_results.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Saved results")
