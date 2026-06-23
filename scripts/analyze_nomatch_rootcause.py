"""
P0 fixed: Correct file path lookup for root-cause tracing.
"""
import json
import math
import re
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

kg_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_data = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))
phase1 = json.loads((OUTPUT / "phase_1_concepts.json").read_text(encoding="utf-8"))
phase3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
phase5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))

kg_issues = {i["instance_id"]: i for i in kg_data["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_data["per_issue"]}
kg_concepts = {c["canonical_name"] for c in phase3["concepts"]}

# Build concept → set of normalized file paths
def norm_path(fpath: str) -> str:
    """Normalize to relative path matching GT/eval format."""
    p = fpath.replace("\\", "/")
    for prefix in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/", "openlibrary/"):
        if prefix in p:
            return p.split(prefix, 1)[1]
    return p

concept_files = defaultdict(set)
for occ in phase1["occurrences"]:
    name = occ["split_name"]
    fp = occ["context"]["file_path"]
    concept_files[name].add(norm_path(fp))

# Build file → concepts
file_concepts = defaultdict(set)
for cname, files in concept_files.items():
    for f in files:
        file_concepts[f].add(cname)

# Connectivity
connected = set()
syn_edges = defaultdict(set)
for rel in phase3["relationships"]:
    s, t = rel["source_concept_id"], rel["target_concept_id"]
    connected.add(s); connected.add(t)
    syn_edges[s].add(t); syn_edges[t].add(s)

cooc_edges = defaultdict(set)
for rel in phase5["relationships"]:
    s, t = rel["source_concept_id"], rel["target_concept_id"]
    connected.add(s); connected.add(t)
    cooc_edges[s].add(t); cooc_edges[t].add(s)

isolated = kg_concepts - connected

# IDF
all_files = {f for fs in concept_files.values() for f in fs}
total_f = max(1, len(all_files))
concept_idf = {n: math.log(1 + total_f / max(1, len(fs))) for n, fs in concept_files.items()}

STOPWORDS = {'the','and','for','with','that','this','from','are','not','its','can','has','had','was','were','but','when','then','also','into','over','after','before','between','under','does','should','would','could','being','been','have','which','using','doesnt','dont','cant','wont'}
def tokenize(text):
    text = re.sub(r'[^a-zA-Z\s]', ' ', text.lower())
    return {t for t in text.split() if len(t) >= 3 and t not in STOPWORDS}

print("=" * 80)
print("P0: CORRECTED ROOT-CAUSE ANALYSIS (file→concept lookup fixed)")
print("=" * 80)

for inst_id in sorted(kg_issues.keys()):
    kg = kg_issues[inst_id]
    bm = bm25_issues.get(inst_id)
    kg_hit = kg.get("file_rank") is not None and kg["file_rank"] <= 10
    bm_hit = bm is not None and bm.get("file_rank") is not None and bm["file_rank"] <= 10

    if kg_hit or not bm_hit:
        continue  # only KG-miss, BM25-hit

    title = kg.get("title", "")
    gt_files = kg.get("gt_files", [])
    kg_pred = kg.get("predicted_files", [])
    bm_pred = bm.get("predicted_files", [])
    kg_rank = kg.get("file_rank")
    tokens = tokenize(title)
    tokens_in_kg = tokens & kg_concepts

    print(f"\n{'='*80}")
    print(f"ISSUE: {title[:130]}")
    print(f"GT: {gt_files}")
    print(f"Issue tokens in KG ({len(tokens_in_kg)}): {sorted(tokens_in_kg)}")
    print(f"KG rank: {kg_rank}, BM25 rank: {bm.get('file_rank')}")

    # CORRECTED: find files that match GT file paths
    for gt_file in gt_files:
        # Find actual file paths in concept_files that match this GT file
        matched_paths = []
        for fpath in all_files:
            # fpath is e.g. "catalog/add_book/load_book.py"
            # gt_file is e.g. "catalog/add_book/load_book.py"
            if fpath.endswith(gt_file) or gt_file.endswith(fpath) or fpath == gt_file:
                matched_paths.append(fpath)
            elif gt_file in fpath or fpath in gt_file:
                matched_paths.append(fpath)

        # Get concepts from those files
        gt_file_concepts = set()
        for mp in set(matched_paths):
            gt_file_concepts |= file_concepts.get(mp, set())

        gt_in_kg = gt_file_concepts & kg_concepts
        gt_connected = gt_in_kg & connected

        print(f"\n  GT: {gt_file}")
        print(f"    Matched file paths in KG: {sorted(set(matched_paths))[:5]}")
        print(f"    KG concepts in this file: {len(gt_in_kg)} (connected: {len(gt_connected)})")

        # Show top concepts
        if gt_connected:
            top = sorted(gt_connected, key=lambda c: concept_idf.get(c, 0), reverse=True)[:10]
            print(f"    Top connected concepts: {[(c, len(concept_files[c]), f'{concept_idf.get(c,0):.1f}') for c in top]}")
            # How many of these overlap with issue tokens?
            overlap = gt_connected & tokens_in_kg
            print(f"    Overlap with issue tokens: {overlap} ({len(overlap)})")
            # How many are reachable via BFS 1-hop from issue tokens?
            bfs1 = set()
            for t in tokens_in_kg:
                bfs1 |= syn_edges.get(t, set()) | cooc_edges.get(t, set())
            bfs1_overlap = gt_connected & bfs1
            print(f"    Reachable via BFS-1 from issue tokens: {bfs1_overlap - overlap} more")

    # Check why predicted files rank higher than GT
    print(f"\n  KG predicted top-5: {kg_pred[:5]}")
    for pf in kg_pred[:3]:
        pf_concepts = set()
        for mp in all_files:
            if pf in mp or mp in pf or pf.endswith(mp) or mp.endswith(pf):
                pf_concepts |= file_concepts.get(mp, set())
        pf_in_kg = pf_concepts & kg_concepts
        pf_connected = pf_in_kg & connected
        # How many are reachable from issue tokens?
        pf_reachable = set()
        for t in tokens_in_kg:
            pf_reachable |= syn_edges.get(t, set()) | cooc_edges.get(t, set())
        pf_reachable &= pf_connected
        print(f"    {pf}: {len(pf_connected)} connected KG concepts, {len(pf_reachable)} reachable from issue tokens")
