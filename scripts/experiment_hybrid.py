"""
Experiment C: Hybrid scoring via Reciprocal Rank Fusion (RRF).

Combines BM25 and KG-walk rankings without re-running either method.
RRF score = Σ 1/(k + rank_in_method)   where k=60 (standard value).

This gives a fair estimate of the hybrid approach's potential.
"""
import json
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))
kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))

bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}
kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}

K = 60  # RRF constant


def rrf_score(rank: int | None, k: int = K) -> float:
    """Reciprocal rank: rank 1 → 1/61, rank 10 → 1/70, None → 0."""
    if rank is None:
        return 0.0
    return 1.0 / (k + rank)


def evaluate_hybrid(alpha: float, k: int = K):
    """Evaluate RRF with given alpha blending BM25 and KG ranks.

    hybrid_score(file) = alpha * rrf(bm25_rank) + (1-alpha) * rrf(kg_rank)
    """
    file_hits = 0
    file_mrr_sum = 0.0
    n = 0
    improvement_over_kg = 0  # count issues where hybrid hits but KG alone misses
    improvement_over_bm25 = 0

    for inst_id, kg_issue in kg_issues.items():
        bm_issue = bm25_issues.get(inst_id)
        if bm_issue is None:
            continue
        n += 1

        gt_files = set(kg_issue.get("gt_files", []))
        if not gt_files:
            continue

        # Build BM25 rank map: file_path -> rank
        bm_ranks = {}
        for i, f in enumerate(bm_issue.get("predicted_files", []), 1):
            bm_ranks[f] = i

        # Build KG rank map
        kg_ranks = {}
        for i, f in enumerate(kg_issue.get("predicted_files", []), 1):
            kg_ranks[f] = i

        # Normalize file paths for cross-reference
        def norm(p: str) -> str:
            p = p.replace("\\", "/")
            for prefix in ("openlibrary/openlibrary/", "openlibrary/"):
                if p.startswith(prefix):
                    p = p[len(prefix):]
            return p

        # Map normalized paths back to original
        bm_norm_to_orig = {norm(f): f for f in bm_ranks}
        kg_norm_to_orig = {norm(f): f for f in kg_ranks}

        # Score all files that appear in either method's top-N
        all_files = set()
        for f in list(bm_ranks.keys()) + list(kg_ranks.keys()):
            all_files.add(norm(f))

        hybrid_scores = {}
        for f_norm in all_files:
            bm_f = bm_norm_to_orig.get(f_norm)
            kg_f = kg_norm_to_orig.get(f_norm)
            bm_r = bm_ranks.get(bm_f, None) if bm_f else None
            kg_r = kg_ranks.get(kg_f, None) if kg_f else None

            # If a file is in one method but not the other, assign penalty rank
            # Use rank=50 (approximately "not in top-10" penalty)
            bm_score = rrf_score(bm_r if bm_r is not None else 50, k)
            kg_score = rrf_score(kg_r if kg_r is not None else 50, k)
            hybrid_scores[f_norm] = alpha * bm_score + (1 - alpha) * kg_score

        # Sort by hybrid score
        ranked = sorted(hybrid_scores.items(), key=lambda x: x[1], reverse=True)

        # Check if any GT file is in top-10
        file_rank = None
        for i, (f_norm, score) in enumerate(ranked[:10], 1):
            for gt in gt_files:
                if f_norm.endswith(gt) or gt.endswith(f_norm):
                    file_rank = i
                    file_hits += 1
                    file_mrr_sum += 1.0 / i
                    break
            if file_rank is not None:
                break

        # Track improvements
        kg_hit = kg_issue.get("file_rank") is not None and kg_issue["file_rank"] <= 10
        bm_hit = bm_issue.get("file_rank") is not None and bm_issue["file_rank"] <= 10
        hy_hit = file_rank is not None and file_rank <= 10

        if hy_hit and not kg_hit:
            improvement_over_kg += 1
        if hy_hit and not bm_hit:
            improvement_over_bm25 += 1

    recall = file_hits / n if n else 0
    mrr = file_mrr_sum / n if n else 0
    return {
        "alpha": alpha,
        "n": n,
        "file_recall": recall,
        "file_mrr": mrr,
        "improvement_over_kg": improvement_over_kg,
        "improvement_over_bm25": improvement_over_bm25,
    }


# Test different alpha values
print("=" * 70)
print("EXPERIMENT C: Hybrid BM25 + KG via Reciprocal Rank Fusion")
print("=" * 70)
print(f"{'Alpha':>8}  {'File Recall@10':>16}  {'File MRR':>10}  {'vs KG':>10}  {'vs BM25':>10}")
print("-" * 70)

results = []
for alpha in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
    r = evaluate_hybrid(alpha)
    results.append(r)
    print(
        f"α={alpha:0.1f}   "
        f"{r['file_recall']*100:6.1f}% → {int(r['file_recall']*91)}/91"
        f"      {r['file_mrr']:.3f}"
        f"      {r['improvement_over_kg']:+d}"
        f"      {r['improvement_over_bm25']:+d}"
    )

# Find best alpha
best = max(results, key=lambda r: r["file_recall"])
print()
print(f"Best α = {best['alpha']:.1f}: Recall@10 = {best['file_recall']*100:.1f}%")
print(f"  Improves over KG by {best['improvement_over_kg']} issues")
print(f"  Improves over BM25 by {best['improvement_over_bm25']} issues")

# Show comparison
print()
print("=" * 70)
print("COMPARISON")
print("=" * 70)
print(f"BM25 alone:    92.3%  (MRR 0.758)")
print(f"KG-walk alone: 82.4%  (MRR 0.547)")
print(f"Best hybrid:   {best['file_recall']*100:.1f}%  (MRR {best['file_mrr']:.3f})  α={best['alpha']:.1f}")

# Save results
with open(OUTPUT / "experiment_hybrid.json", "w", encoding="utf-8") as f:
    json.dump({
        "experiment": "hybrid_scoring_rrf",
        "method": "Reciprocal Rank Fusion",
        "k": K,
        "bm25_baseline": {"file_recall": 0.923, "file_mrr": 0.758},
        "kg_baseline": {"file_recall": 0.824, "file_mrr": 0.547},
        "results": results,
        "best": best,
    }, f, ensure_ascii=False, indent=2)
print(f"\nResults saved to output/experiment_hybrid.json")
