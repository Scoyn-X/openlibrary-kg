"""Analyze 91 SWE-bench issues: classify by type, compare BM25 vs KG per type."""
import json, re
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

gt = json.loads((OUTPUT / "swebench_ground_truth.json").read_text(encoding="utf-8"))
kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))

kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}

# ── Classification rules ──
CATEGORIES = {
    "API/路由": [
        r"\b(POST|GET|PUT|DELETE)\s+/\w", r"/lists?/", r"/search", r"/books?/",
        r"endpoint", r"API", r"returns? \d{3}", r"route", r"request"
    ],
    "Solr/搜索": [
        r"\bsolr\b", r"\bindex\b", r"\breindex", r"\bquery\b", r"\bsearch\b",
        r"\bSolr\b", r"boolean clause", r"facet", r"document\b"
    ],
    "MARC/编目": [
        r"\bMARC\b", r"\bcatalog\b", r"\bimport\b.*\brecord", r"\bISBN\b",
        r"\bOCLC\b", r"\bbibliograph", r"\bmetadata\b", r"\bedition\b",
        r"\bauthor\b.*\bmatch", r"\bpubli[sc]h", r"\badd_book\b"
    ],
    "脚本/工具": [
        r"\bscript\b", r"\bCLI\b", r"\bbatch\b", r"\bscheduler\b",
        r"\bimport\b.*\bscript\b", r"\bmonitor", r"\bupdater\b",
        r"\bmigration\b", r"\butility\b"
    ],
    "修复/重构": [
        r"\brefactor\b", r"\binstead of\b", r"\breplace\b.*\bwith\b",
        r"\buse\b.*\binstead\b", r"\bmigrate\b.*\bto\b", r"\bexceeds?\b.*\bcomplex"
    ],
    "UI/前端": [
        r"\bbanner\b", r"\bdisplay\b", r"\bUI\b", r"\bpages?\b.*\bshow",
        r"\bmarkdown\b", r"\btemplate\b", r"\bpartials?\b"
    ],
}


def classify_issue(text: str) -> list[str]:
    """Return all matching categories for an issue."""
    text_lower = text.lower()
    matches = []
    for cat, patterns in CATEGORIES.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                matches.append(cat)
                break
    if not matches:
        matches.append("通用/其他")
    return matches


# ── Classify all 91 issues ──
per_cat = defaultdict(lambda: {"total": 0, "kg_hit": 0, "bm25_hit": 0, "kg_better": 0, "bm25_better": 0, "kg_top1": 0, "bm25_top1": 0})

for rec in gt:
    text = rec.get("problem_statement", "")
    cats = classify_issue(text)
    iid = rec["instance_id"]
    kg = kg_issues.get(iid, {})
    bm = bm25_issues.get(iid, {})

    kg_hit = kg.get("file_rank") is not None and kg["file_rank"] <= 10
    bm_hit = bm.get("file_rank") is not None and bm["file_rank"] <= 10
    kg_top1 = kg.get("file_rank") == 1
    bm_top1 = bm.get("file_rank") == 1

    for cat in cats:
        per_cat[cat]["total"] += 1
        if kg_hit: per_cat[cat]["kg_hit"] += 1
        if bm_hit: per_cat[cat]["bm25_hit"] += 1
        if kg_hit and not bm_hit: per_cat[cat]["kg_better"] += 1
        if bm_hit and not kg_hit: per_cat[cat]["bm25_better"] += 1
        if kg_top1: per_cat[cat]["kg_top1"] += 1
        if bm_top1: per_cat[cat]["bm25_top1"] += 1

# ── Print results ──
print(f"{'Category':<20} {'Count':>5} {'KG-Hit':>7} {'BM25-Hit':>9} {'KG>BM25':>8} {'BM25>KG':>8} {'KG-Top1':>8} {'BM25-Top1':>9}")
print("-" * 85)
for cat in CATEGORIES:
    stats = per_cat[cat]
    if stats["total"] == 0:
        continue
    kg_pct = 100 * stats["kg_hit"] / stats["total"]
    bm25_pct = 100 * stats["bm25_hit"] / stats["total"]
    print(f"{cat:<20} {stats['total']:>5} {kg_pct:>6.0f}% {bm25_pct:>8.0f}% {stats['kg_better']:>8} {stats['bm25_better']:>8} {stats['kg_top1']:>8} {stats['bm25_top1']:>9}")

stats = per_cat["通用/其他"]
if stats["total"] > 0:
    kg_pct = 100 * stats["kg_hit"] / stats["total"]
    bm25_pct = 100 * stats["bm25_hit"] / stats["total"]
    print(f"{'通用/其他':<20} {stats['total']:>5} {kg_pct:>6.0f}% {bm25_pct:>8.0f}% {stats['kg_better']:>8} {stats['bm25_better']:>8} {stats['kg_top1']:>8} {stats['bm25_top1']:>9}")

# ── Show which issues fall into each category ──
print(f"\n{'='*60}")
print("ISSUE EXAMPLES PER CATEGORY")
print(f"{'='*60}")

for cat in ["API/路由", "Solr/搜索", "MARC/编目", "脚本/工具", "修复/重构", "UI/前端"]:
    print(f"\n--- {cat} ---")
    for rec in gt:
        cats = classify_issue(rec.get("problem_statement", ""))
        if cat in cats:
            text = rec.get("problem_statement", "")
            head = text.split(chr(10))[0]
            # Find which actual meaningful title exists
            for line in text.split(chr(10)):
                line = line.strip()
                if line and not line.startswith("#"):
                    head = line[:100]
                    break
            iid = rec["instance_id"]
            kg = kg_issues.get(iid, {})
            bm = bm25_issues.get(iid, {})
            kg_r = kg.get("file_rank", "-")
            bm_r = bm.get("file_rank", "-")
            print(f"  KG={kg_r} BM25={bm_r} | {head[:90]}")
            break  # only show 1 example per category
