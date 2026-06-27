"""Debug: check which no-KG-hit issues get benefit from soft-index fallback."""
import json, re, math
from pathlib import Path

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")
si = json.loads((OUTPUT / "soft_index.json").read_text(encoding="utf-8"))
p6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
kg_names = {c["canonical_name"] for c in p6["concepts"]}
gt = json.loads((OUTPUT / "swebench_ground_truth.json").read_text(encoding="utf-8"))

all_files = set()
for c in p6["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ["context"]["file_path"]
        if fp:
            all_files.add(fp)
total_f = len(all_files)


def norm_path(fp):
    fp = fp.replace("\\", "/")
    for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
        if pre in fp:
            return fp.split(pre, 1)[1]
    return fp


print("=" * 60)
print("NO-KG-HIT ISSUES: SOFT-INDEX FALLBACK CHECK")
print("=" * 60)

for rec in gt:
    ps = rec.get("problem_statement", "")
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", ps.lower()))
    kg_hits = tokens & kg_names
    si_hits = {t for t in tokens - kg_names if t in si}

    if kg_hits:
        continue

    files = rec.get("changed_files", [])
    print(f"\nIssue: {ps.split(chr(10))[0][:100]}")
    print(f"  GT: {files[:2]}")
    print(f"  SI tokens: {si_hits}")

    if not si_hits:
        print(f"  -> NO soft-index hits. Dead end.")
        continue

    # Check GT file boost
    gt_matched = False
    for t in si_hits:
        si_files = si[t]
        idf = math.log(1.0 + total_f / max(1, len(si_files)))
        boost = 0.3 * idf
        for sf in si_files:
            sf_norm = norm_path(sf)
            for gt_f in files:
                if sf_norm.endswith(gt_f) or gt_f.endswith(sf_norm.split("/")[-1]):
                    if not gt_matched:
                        print(f"  GT FILE BOOSTS:")
                        gt_matched = True
                    print(f"    {t}: +{boost:.3f} -> {sf_norm}")
                    break

    if not gt_matched:
        print(f"  -> SI tokens exist but none match GT files.")

    # Show top SI-ranked files
    file_scores = {}
    for t in si_hits:
        si_files = si[t]
        idf = math.log(1.0 + total_f / max(1, len(si_files)))
        boost = 0.3 * idf
        for sf in si_files:
            sf_norm = norm_path(sf)
            file_scores[sf_norm] = file_scores.get(sf_norm, 0) + boost

    ranked = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)[:5]
    print(f"  Top SI-ranked files: {[(f, round(s,3)) for f, s in ranked]}")
