"""Analyze KG vs BM25 performance by GT file subdomain."""
import json
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")
kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))
gt_data = json.loads((OUTPUT / "swebench_ground_truth.json").read_text(encoding="utf-8"))

kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}

sub_stats = defaultdict(lambda: {"total": 0, "kg_hit": 0, "bm25_hit": 0, "kg_top1": 0, "bm25_top1": 0})

for rec in gt_data:
    iid = rec["instance_id"]
    kg = kg_issues.get(iid)
    bm = bm25_issues.get(iid)
    if not kg or not bm:
        continue
    for gt_f in rec.get("changed_files", []):
        sub = gt_f.split("/")[0] if "/" in gt_f else "other"
        sub_stats[sub]["total"] += 1
        kg_hit = kg.get("file_rank") is not None and kg["file_rank"] <= 10
        bm_hit = bm.get("file_rank") is not None and bm["file_rank"] <= 10
        if kg_hit: sub_stats[sub]["kg_hit"] += 1
        if bm_hit: sub_stats[sub]["bm25_hit"] += 1
        if kg.get("file_rank") == 1: sub_stats[sub]["kg_top1"] += 1
        if bm.get("file_rank") == 1: sub_stats[sub]["bm25_top1"] += 1

print(f"{'Subdomain':<25} {'#GT':>5} {'KG%':>7} {'BM25%':>8} {'KG-T1':>6} {'BM-T1':>6}")
print("-" * 62)
for sub in sorted(sub_stats.keys()):
    s = sub_stats[sub]
    if s["total"] < 2:
        continue
    print(f"{sub:<25} {s['total']:>5} {100*s['kg_hit']/s['total']:>6.0f}% {100*s['bm25_hit']/s['total']:>7.0f}% {s['kg_top1']:>6} {s['bm25_top1']:>6}")

# Also: which subdomains does KG outperform BM25?
print(f"\n{'='*60}")
print("WHERE KG > BM25 (by subdomain)")
print(f"{'='*60}")
for sub in sorted(sub_stats.keys()):
    s = sub_stats[sub]
    if s["total"] < 2:
        continue
    diff = s["kg_hit"] - s["bm25_hit"]
    if diff > 0:
        print(f"  {sub}: KG +{diff} over BM25 ({s['total']} GT files)")
