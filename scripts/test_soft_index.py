"""Quick offline test of soft_index boost logic."""
import json, re, math
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")
si = json.loads((OUTPUT / "soft_index.json").read_text(encoding="utf-8"))
p6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
kg_names = {c["canonical_name"] for c in p6["concepts"]}

# IDF for soft_index
all_files = set()
for c in p6["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ["context"]["file_path"]
        if fp:
            all_files.add(fp)
total_files = len(all_files)

soft_idf = {}
for token, files in si.items():
    soft_idf[token] = math.log(1.0 + total_files / max(1, len(files)))


def norm_path(fp: str) -> str:
    fp = fp.replace("\\", "/")
    for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
        if pre in fp:
            return fp.split(pre, 1)[1]
    return fp


# --- Test issue 1: get_ia.py / requests / urllib ---
print("=" * 60)
print("TEST 1: get_ia.py — 'refactor to use requests instead of urllib'")
print("=" * 60)

issue = "Refactor openlibrary/catalog/get_ia.py to use requests instead of urllib"
tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", issue.lower()))
orphans = {t for t in tokens if t not in kg_names and t in si}

print(f"Tokens: {tokens}")
print(f"KG hits: {tokens & kg_names}")
print(f"Soft-index hits (orphans): {orphans}")

boost = defaultdict(float)
for t in orphans:
    files = si[t]
    idf = soft_idf[t]
    b = 0.25 * idf
    for f in files:
        boost[norm_path(f)] += b
    print(f"  '{t}': idf={idf:.2f}, {len(files)} files, per-file-boost={b:.3f}")
    for f in files:
        print(f"    -> {norm_path(f)}")

gt = "openlibrary/catalog/get_ia.py"
for norm_fp, b in sorted(boost.items(), key=lambda x: x[1], reverse=True):
    if gt in norm_fp or norm_fp in gt:
        print(f"  GT FILE BOOST: {norm_fp} = +{b:.3f}")

# --- Test issue 2: isbndb ---
print()
print("=" * 60)
print("TEST 2: isbndb importer")
print("=" * 60)

issue2 = "add support for importing metadata from isbndb batch records"
tokens2 = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", issue2.lower()))
orphans2 = {t for t in tokens2 if t not in kg_names and t in si}
print(f"Orphans: {orphans2}")

for t in orphans2:
    files = si[t]
    print(f"  '{t}': {len(files)} files")
    for f in files[:3]:
        print(f"    -> {norm_path(f)}")

# --- Test issue 3: read_subjects ---
print()
print("=" * 60)
print("TEST 3: read_subjects complexity")
print("=" * 60)

issue3 = "read_subjects in get_subjects.py exceeds complexity thresholds"
tokens3 = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", issue3.lower()))
orphans3 = {t for t in tokens3 if t not in kg_names and t in si}
print(f"Orphans: {orphans3}")
print(f"KG hits: {tokens3 & kg_names}")

for t in tokens3 - kg_names:
    if t in si:
        files = si[t]
        print(f"  '{t}': IN soft_index, {len(files)} files")
        for f in files[:3]:
            print(f"    -> {norm_path(f)}")
    else:
        print(f"  '{t}': NOT in soft_index (and not in KG)")

# --- Summary: which both-miss cases benefit ---
print()
print("=" * 60)
print("SUMMARY: BOTH-MISS CASES WITH SOFT-INDEX BENEFIT")
print("=" * 60)

both_miss_issues = [
    ("Refactor to use requests instead of urllib", "get_ia.py"),  # Case #1
    ("read_subjects exceeds complexity", "get_subjects.py"),      # Case from both-miss
    ("Add support for importing metadata from ISBNdb", "isbndb.py"),  # Case 3/8
    ("Reading goal banner Dec to Feb", "dateutil.py"),            # Case #5
]

for desc, gt_hint in both_miss_issues:
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", desc.lower()))
    kg_hits = tokens & kg_names
    si_hits = {t for t in tokens - kg_names if t in si}
    total_signal = len(kg_hits) + len(si_hits)
    print(f"  [{gt_hint}] KG={len(kg_hits)}, SI={len(si_hits)}, total={total_signal}")
    if si_hits:
        print(f"    SI tokens: {si_hits}")
