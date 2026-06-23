"""
Root cause analysis: categorize why KG-walk fails on certain issues.
Does NOT modify any pipeline code — pure analysis of existing eval data.
"""
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# Load evaluations
kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))
kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}

# Load KG concepts
phase1 = json.loads((OUTPUT / "phase_1_concepts.json").read_text(encoding="utf-8"))
phase3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
phase5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))

# Build concept set from phase_3 concepts list (concepts are dicts)
kg_concepts = {c["canonical_name"] for c in phase3["concepts"]}
# Also build occurrence-level split_name set from phase_1 for broader coverage
kg_split_names = set()
for occ in phase1["occurrences"]:
    kg_split_names.add(occ["split_name"])
# Use both for matching
kg_all_names = kg_concepts | kg_split_names
print(f"KG unique concepts (phase_3): {len(kg_concepts)}")
print(f"KG split names (phase_1): {len(kg_split_names)}")
print(f"KG all names (union): {len(kg_all_names)}")

# Build concept -> files mapping from phase_1 occurrences
concept_files = defaultdict(set)
for occ in phase1["occurrences"]:
    name = occ["split_name"]
    fpath = occ["context"]["file_path"]
    # Normalize to relative path
    if "openlibrary/openlibrary/" in fpath:
        rel = fpath.split("openlibrary/openlibrary/", 1)[1]
    elif "Openlibrary/openlibrary/" in fpath:
        rel = fpath.split("Openlibrary/openlibrary/", 1)[1]
    else:
        rel = fpath
    concept_files[name].add(rel)

# Build concept connectivity map
connected_concepts = set()
for rel in phase3["relationships"]:
    connected_concepts.add(rel["source_concept_id"])
    connected_concepts.add(rel["target_concept_id"])
for rel in phase5["relationships"]:
    connected_concepts.add(rel["source_concept_id"])
    connected_concepts.add(rel["target_concept_id"])
isolated_concepts = kg_concepts - connected_concepts
print(f"Connected concepts: {len(connected_concepts)}")
print(f"Isolated concepts: {len(isolated_concepts)} ({100*len(isolated_concepts)/len(kg_concepts):.1f}%)")
print(f"Synonym edges: {len(phase3['relationships'])}")
print(f"Co-occurrence edges: {len(phase5['relationships'])}")

# Tokenizer
STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'not',
    'its', 'can', 'has', 'had', 'was', 'were', 'but', 'when', 'then',
    'also', 'into', 'over', 'after', 'before', 'between', 'under',
    'does', 'should', 'would', 'could', 'being', 'been', 'have',
    'which', 'using', 'doesnt', 'dont', 'cant', 'wont'
}

def tokenize(text):
    text = re.sub(r'[^a-zA-Z\s]', ' ', text.lower())
    return {t for t in text.split() if len(t) >= 3 and t not in STOPWORDS}

# Categorize
cats = {
    "kg_hit_bm25_miss": [],
    "both_hit": [],
    "both_miss": [],
    "kg_miss_bm25_hit": [],
}

for inst_id, kg in kg_issues.items():
    bm = bm25_issues.get(inst_id)
    kg_hit = kg.get("file_rank") is not None and kg["file_rank"] <= 10
    bm_hit = bm is not None and bm.get("file_rank") is not None and bm["file_rank"] <= 10
    if kg_hit and not bm_hit:
        cats["kg_hit_bm25_miss"].append(inst_id)
    elif kg_hit and bm_hit:
        cats["both_hit"].append(inst_id)
    elif not kg_hit and not bm_hit:
        cats["both_miss"].append(inst_id)
    else:
        cats["kg_miss_bm25_hit"].append(inst_id)

print(f"\n{'='*60}")
print(f"ISSUE CATEGORIZATION (n=91)")
print(f"{'='*60}")
print(f"Both hit:         {len(cats['both_hit']):3d}  -- both methods succeed")
print(f"KG hit, BM25 miss:{len(cats['kg_hit_bm25_miss']):3d}  -- KG's unique strength")
print(f"BM25 hit, KG miss:{len(cats['kg_miss_bm25_hit']):3d}  -- WHERE KG CAN IMPROVE")
print(f"Both miss:        {len(cats['both_miss']):3d}  -- hard cases for both")
print(f"\n→ KG can potentially gain on {len(cats['kg_miss_bm25_hit'])} issues")
print(f"→ If KG fixes all of them: 82.4% → {100*(len(cats['both_hit'])+len(cats['kg_hit_bm25_miss'])+len(cats['kg_miss_bm25_hit']))/91:.1f}%")

# Deep analysis of "KG miss, BM25 hit"
print(f"\n{'='*60}")
print(f"DEEP ANALYSIS: {len(cats['kg_miss_bm25_hit'])} ISSUES WHERE BM25 HITS, KG MISSES")
print(f"{'='*60}")

failure_modes = Counter()
details = []

for inst_id in cats["kg_miss_bm25_hit"]:
    kg = kg_issues[inst_id]
    bm = bm25_issues[inst_id]
    title = kg.get("title", "")
    gt_files = kg.get("gt_files", [])
    kg_pred = kg.get("predicted_files", [])
    bm_pred = bm.get("predicted_files", [])
    kg_rank = kg.get("file_rank")
    bm_rank = bm.get("file_rank")

    tokens = tokenize(title)
    tokens_in_kg = tokens & kg_concepts
    match_rate = len(tokens_in_kg) / max(len(tokens), 1)

    # Count isolated among matched
    matched_isolated = [t for t in tokens_in_kg if t in isolated_concepts]
    matched_connected = [t for t in tokens_in_kg if t in connected_concepts]

    # Check if any GT file has ANY concept match (even weak)
    gt_concepts_in_kg = set()
    for gt_file in gt_files:
        for concept_name, files in concept_files.items():
            if any(gt_file in f for f in files):
                gt_concepts_in_kg.add(concept_name)

    # Classify failure mode
    if match_rate == 0:
        mode = "ENTRY_MISSING: Zero issue keywords in KG"
    elif len(tokens_in_kg) == 1 and len(matched_connected) == 0:
        mode = "ISOLATED: Single matched concept has no edges"
    elif len(matched_connected) == 0:
        mode = "ISOLATED: All matched concepts are isolated"
    elif len(matched_connected) <= 2 and kg_rank and kg_rank > 10:
        mode = "RANKING: Connected concepts exist but ranked outside top-10"
    elif kg_rank is None:
        mode = "NO_MATCH: No file scored (concept-to-file mapping failed)"
    else:
        mode = "RANKING: Sufficient match but ranked > 10"

    failure_modes[mode] += 1
    details.append({
        "title": title[:120],
        "gt_files": gt_files,
        "tokens": tokens,
        "tokens_in_kg": tokens_in_kg,
        "matched_connected": matched_connected,
        "matched_isolated": matched_isolated,
        "kg_rank": kg_rank,
        "bm_rank": bm_rank,
        "mode": mode,
    })

print(f"\nFailure mode distribution:")
for mode, count in failure_modes.most_common():
    print(f"  {count:2d}  {mode}")

# Print each case
print(f"\n{'='*60}")
print("PER-ISSUE DETAILS")
print(f"{'='*60}")
for i, d in enumerate(details):
    print(f"\n--- Issue {i+1}: {d['title'][:100]}")
    print(f"    GT files: {d['gt_files']}")
    print(f"    Issue tokens ({len(d['tokens'])}): {sorted(d['tokens'])}")
    print(f"    In KG ({len(d['tokens_in_kg'])}): {sorted(d['tokens_in_kg'])}")
    print(f"    Connected: {sorted(d['matched_connected'])}")
    print(f"    Isolated: {sorted(d['matched_isolated'])}")
    print(f"    KG rank: {d['kg_rank']}, BM25 rank: {d['bm_rank']}")
    print(f"    → {d['mode']}")

# Best KG cases (KG hit, BM25 miss)
print(f"\n{'='*60}")
print(f"KG UNIQUE WINS: {len(cats['kg_hit_bm25_miss'])} ISSUES WHERE KG BEATS BM25")
print(f"{'='*60}")
for inst_id in cats["kg_hit_bm25_miss"]:
    kg = kg_issues[inst_id]
    bm = bm25_issues[inst_id]
    print(f"  [{kg.get('file_rank')} vs {bm.get('file_rank')}] {kg.get('title','')[:100]}")
    print(f"    GT: {kg.get('gt_files')}")

print(f"\n{'='*60}")
print("SUMMARY STATS")
print(f"{'='*60}")
print(f"KG File Recall@10: 82.4%")
print(f"BM25 File Recall@10: 92.3%")
print(f"KG improvement potential (if all KG-miss/BM25-hit fixed): {(len(cats['both_hit'])+len(cats['kg_hit_bm25_miss'])+len(cats['kg_miss_bm25_hit']))/91*100:.1f}%")
print(f"KG unique advantage (beats BM25 on): {len(cats['kg_hit_bm25_miss'])} issues")
print(f"Two methods agree (both hit): {len(cats['both_hit'])} issues")
print(f"Two methods agree (both miss): {len(cats['both_miss'])} issues")
print(f"Method disagreement: {len(cats['kg_hit_bm25_miss']) + len(cats['kg_miss_bm25_hit'])} issues")
