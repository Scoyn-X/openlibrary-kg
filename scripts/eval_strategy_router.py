"""Evaluate strategy router v2: category-aware RRF + subdomain bonus."""
import json, re
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))
gt_data = json.loads((OUTPUT / "swebench_ground_truth.json").read_text(encoding="utf-8"))

from openlibrary_kg.downstream.strategy_router import (
    classify_issue, IssueCategory, file_subdomain,
)

kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}

K = 60  # RRF constant


def rrf(rank, k=K):
    if rank is None or rank == 0:
        return 0.0
    return 1.0 / (k + rank)


def norm_path(fp):
    fp = fp.replace("\\", "/")
    for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
        if pre in fp:
            return fp.split(pre, 1)[1]
    return fp


def _file_match(pred: str, gt: str) -> bool:
    return pred.endswith(gt) or gt.endswith(pred)


# ── Category-aware alpha (BM25 weight) ──
CATEGORY_ALPHA = {
    IssueCategory.MARC_CATALOG: 0.75,   # BM25 dominates keyword-dense catalog
    IssueCategory.SOLR_SEARCH: 0.70,    # BM25 for tech terms
    IssueCategory.SCRIPT_TOOL: 0.70,    # BM25 for filename match
    IssueCategory.REFACTOR: 0.60,       # mixed — need both signals
    IssueCategory.API_ROUTE: 0.45,      # KG has route→handler understanding
    IssueCategory.UI_FRONTEND: 0.35,    # KG better for semantic UI issues
    IssueCategory.DOMAIN_LOGIC: 0.30,   # KG best for business logic
    IssueCategory.GENERAL: 0.50,        # balanced
}

results_by_cat = defaultdict(lambda: {"total": 0, "file_hits": 0, "mrr_sum": 0.0, "top1_hits": 0})
overall_file_hits = 0
overall_mrr_sum = 0.0
overall_n = 0

changed = []  # issues where router differs from plain RRF

for rec in gt_data:
    iid = rec["instance_id"]
    ps = rec.get("problem_statement", "")
    head, _, tail = ps.partition("\n")
    title, body = head, tail

    kg = kg_issues.get(iid, {})
    bm = bm25_issues.get(iid, {})

    if not kg or not bm:
        continue

    gt_files = {norm_path(f) for f in rec.get("changed_files", [])}

    # Build rank maps
    kg_ranks = {}
    for i, f in enumerate(kg.get("predicted_files", []), 1):
        kg_ranks[norm_path(f)] = i
    bm_ranks = {}
    for i, f in enumerate(bm.get("predicted_files", []), 1):
        bm_ranks[norm_path(f)] = i

    # Classify
    profile = classify_issue(title, body)
    alpha = CATEGORY_ALPHA.get(profile.category, 0.50)
    focus_domains = set(profile.focus_subdomains)

    # All files from both methods
    all_files = set(kg_ranks) | set(bm_ranks)
    scores = {}
    for fp in all_files:
        kg_r = kg_ranks.get(fp, 50)  # penalty for not in top-10
        bm_r = bm_ranks.get(fp, 50)
        score = alpha * rrf(bm_r) + (1 - alpha) * rrf(kg_r)

        # Architectual subdomain bonus
        if focus_domains and file_subdomain(fp) in focus_domains:
            score *= 1.10

        scores[fp] = score

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:10]

    file_rank = None
    for i, (fp, _) in enumerate(ranked, 1):
        if any(_file_match(fp, g) for g in gt_files):
            file_rank = i
            overall_file_hits += 1
            overall_mrr_sum += 1.0 / i
            if i == 1:
                results_by_cat[profile.category]["top1_hits"] += 1
            break

    # Track if this differs from plain α=0.6 RRF
    plain_scores = {}
    for fp in all_files:
        kg_r = kg_ranks.get(fp, 50)
        bm_r = bm_ranks.get(fp, 50)
        plain_scores[fp] = 0.6 * rrf(bm_r) + 0.4 * rrf(kg_r)
    plain_ranked = sorted(plain_scores.items(), key=lambda kv: kv[1], reverse=True)[:10]
    plain_top1 = plain_ranked[0][0] if plain_ranked else ""
    routed_top1 = ranked[0][0] if ranked else ""
    if plain_top1 != routed_top1:
        changed.append((profile.category.value, title[:80], plain_top1[:50], routed_top1[:50]))

    results_by_cat[profile.category]["total"] += 1
    overall_n += 1
    if file_rank is not None:
        results_by_cat[profile.category]["file_hits"] += 1
        results_by_cat[profile.category]["mrr_sum"] += 1.0 / file_rank

# ── Print ──
print(f"{'='*80}")
print(f"STRATEGY ROUTER v2 (category-aware RRF + subdomain bonus)")
print(f"{'='*80}")

recall = 100 * overall_file_hits / overall_n
mrr = overall_mrr_sum / overall_n
print(f"\nOverall: File Recall@10 = {recall:.1f}% ({overall_file_hits}/{overall_n})")
print(f"Overall: File MRR = {mrr:.3f}")

print(f"\n{'Category':<20} {'Count':>5} {'Recall@10':>10} {'MRR':>8} {'Top-1':>7} {'Alpha':>7}")
print("-" * 65)
for cat in [IssueCategory.MARC_CATALOG, IssueCategory.SOLR_SEARCH, IssueCategory.API_ROUTE,
            IssueCategory.REFACTOR, IssueCategory.SCRIPT_TOOL, IssueCategory.UI_FRONTEND,
            IssueCategory.DOMAIN_LOGIC, IssueCategory.GENERAL]:
    stats = results_by_cat[cat]
    if stats["total"] == 0:
        continue
    c_recall = 100 * stats["file_hits"] / stats["total"] if stats["total"] else 0
    c_mrr = stats["mrr_sum"] / stats["total"] if stats["total"] else 0
    a = CATEGORY_ALPHA.get(cat, 0.5)
    print(f"{cat.value:<20} {stats['total']:>5} {c_recall:>9.1f}% {c_mrr:>8.3f} {stats['top1_hits']:>6} {a:>7.2f}")

print(f"\n--- Benchmark Comparison ---")
print(f"Original KG:        82.4% → 84.6%")
print(f"BM25 alone:         92.3%")
print(f"RRF (α=0.6):        92.3%")
print(f"Strategy Router:    {recall:.1f}%")

if changed:
    print(f"\n--- Top-1 changes from plain RRF ({len(changed)} issues) ---")
    for cat_val, t, old, new in changed[:10]:
        print(f"  [{cat_val}] {t}")
        print(f"    Plain RRF #1: {old}")
        print(f"    Router  #1:   {new}")
