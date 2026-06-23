"""Deep analysis of 6 'both miss' issues: can KG's structural advantage help?"""
import json, re, math
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

p6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
p3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
p5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))
kg_eval = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
bm25_eval = json.loads((OUTPUT / "compare_per_issue_bm25.json").read_text(encoding="utf-8"))

kg_issues = {i["instance_id"]: i for i in kg_eval["per_issue"]}
bm25_issues = {i["instance_id"]: i for i in bm25_eval["per_issue"]}

# --- Build data structures ---
def norm_path(fp):
    fp = fp.replace("\\", "/")
    for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
        if pre in fp:
            fp = fp.split(pre, 1)[1]
    return fp

concept_files = defaultdict(set)
file_concepts = defaultdict(set)
for c in p6["concepts"]:
    name = c["canonical_name"]
    for occ in c.get("occurrences", []):
        fp = norm_path(occ["context"]["file_path"])
        concept_files[name].add(fp)
        file_concepts[fp].add(name)

# Adjacency
adj = defaultdict(set)
for r in p3["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])
for r in p5["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])

# IDF
total_f = len(file_concepts)
concept_idf = {n: math.log(1 + total_f / max(1, len(fs))) for n, fs in concept_files.items()}

STOP_WORDS = {'the','and','for','with','that','this','from','are','not','its','can','has',
              'had','was','were','but','when','then','also','into','over','after','before',
              'between','under','does','should','would','could','being','been','have','which','using',
              'title','description','problem','currently','feature','request','report'}

def tokenize(text):
    return {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text) if len(t) >= 3 and t.lower() not in STOP_WORDS}

# --- Find both-miss cases ---
both_miss = []
for iid, kg in kg_issues.items():
    bm = bm25_issues.get(iid)
    kg_hit = kg.get("file_rank") is not None and kg["file_rank"] <= 10
    bm_hit = bm is not None and bm.get("file_rank") is not None and bm["file_rank"] <= 10
    if not kg_hit and not bm_hit:
        both_miss.append((iid, kg, bm))

print("=" * 80)
print(f"6 BOTH-MISS ISSUES: DEEP ANALYSIS")
print("=" * 80)

for idx, (iid, kg, bm) in enumerate(both_miss, 1):
    title = kg["title"]
    gt_files = kg["gt_files"]
    tokens = tokenize(title)
    kg_pred = kg.get("predicted_files", [])[:5]
    bm_pred = bm.get("predicted_files", [])[:5]

    print(f"\n{'='*80}")
    print(f"CASE #{idx}")
    print(f"{'='*80}")
    print(f"Title: {title[:150]}")
    print(f"GT files: {gt_files}")
    print(f"Tokens ({len(tokens)}): {sorted(list(tokens))[:15]}")

    # --- 1. Are GT files in KG? ---
    print(f"\n--- GT FILE COVERAGE ---")
    for gt in gt_files:
        matches = [f for f in file_concepts if f.endswith(gt) or gt.endswith(f.split("/")[-1])]
        if matches:
            for m in matches:
                print(f"  [OK] {gt} -> {m} ({len(file_concepts[m])} concepts)")
        else:
            print(f"  [NO] {gt} -> NOT IN KG (file not indexed)")

    # --- 2. Which issue tokens hit KG? ---
    kg_hit_tokens = tokens & set(concept_files.keys())
    print(f"\n--- TOKEN HITS ---")
    print(f"  Tokens in KG: {kg_hit_tokens}")

    # For each hit token, check if it appears in GT concepts
    for gt in gt_files:
        matches = [f for f in file_concepts if f.endswith(gt) or gt.endswith(f.split("/")[-1])]
        gt_concepts = set()
        for m in matches:
            gt_concepts |= file_concepts.get(m, set())
        overlap = kg_hit_tokens & gt_concepts
        if overlap:
            print(f"  → Direct hits on {gt}: {overlap}")

    # --- 3. Can BFS reach GT concepts from tokens? ---
    print(f"\n--- BFS REACHABILITY ---")
    seeds = kg_hit_tokens
    reachable = set(seeds)
    frontier = set(seeds)
    reached_by_hop = {}
    for hop in range(4):
        next_f = set()
        for node in frontier:
            next_f |= adj.get(node, set())
        frontier = next_f - reachable
        reachable |= frontier
        reached_by_hop[hop+1] = len(frontier)

    print(f"  Seeds: {len(seeds)}")
    for hop in range(1, 4):
        print(f"  Hop {hop}: +{reached_by_hop.get(hop, 0)} concepts, total={len(reachable) if hop==3 else '...'}")

    for gt in gt_files:
        matches = [f for f in file_concepts if f.endswith(gt) or gt.endswith(f.split("/")[-1])]
        gt_concepts_all = set()
        for m in matches:
            gt_concepts_all |= file_concepts.get(m, set())
        gt_reachable = gt_concepts_all & reachable
        gt_nonreachable = gt_concepts_all - reachable
        if gt_concepts_all:
            print(f"  {gt}: {len(gt_reachable)}/{len(gt_concepts_all)} concepts reachable")
            if gt_reachable:
                top_reachable = sorted(gt_reachable, key=lambda c: concept_idf.get(c,0), reverse=True)[:5]
                print(f"    Top IDF reachable: {top_reachable}")
        else:
            print(f"  {gt}: 0 concepts in KG → UNREACHABLE")

    # --- 4. What's the KG vs BM25 vs GT semantic gap? ---
    print(f"\n--- FAILURE DIAGNOSIS ---")
    if not tokens:
        print(f"  FAILURE MODE: Empty issue text (tokens=0)")
    elif not seeds:
        print(f"  FAILURE MODE: Zero tokens hit KG concepts")
    else:
        all_gt_reachable = False
        for gt in gt_files:
            matches = [f for f in file_concepts if f.endswith(gt) or gt.endswith(f.split("/")[-1])]
            gt_concepts_all = set()
            for m in matches:
                gt_concepts_all |= file_concepts.get(m, set())
            if gt_concepts_all and (gt_concepts_all & reachable):
                all_gt_reachable = True
                break

        if not all_gt_reachable:
            print(f"  FAILURE MODE: GT concepts not reachable from seeds")
            print(f"  → Need bridge: GT concepts are in different graph component")
        else:
            print(f"  FAILURE MODE: Concepts reachable but GT file not in top-10")
            print(f"  → RANKING ISSUE: correct concepts present, wrong files won")
            print(f"  → Could potentially be fixed by computing BFS-files-only IDF or")
            print(f"    weighting smaller, more specific files higher")

    # --- 5. Compare KG vs BM25 predictions ---
    print(f"\n  KG  top-3: {kg_pred[:3]}")
    print(f"  BM25 top-3: {bm_pred[:3]}")
    if kg_pred[:3] == bm_pred[:3]:
        print(f"  → Both methods make SAME mistakes — same blind spot")
    else:
        print(f"  → Different mistakes — potential for complementary fusion")

    # --- 6. Is this a 'semantic mismatch' case? ---
    # Check if GT is a utility file but issue sounds domain-specific
    print(f"\n--- STRUCTURAL ANALYSIS ---")
    for gt in gt_files:
        matches = [f for f in file_concepts if f.endswith(gt) or gt.endswith(f.split("/")[-1])]
        if not matches:
            continue
        gt_concepts = set()
        for m in matches:
            gt_concepts |= file_concepts.get(m, set())
        gt_degree = sum(1 for c in gt_concepts if c in adj)
        gt_connected_ratio = gt_degree / max(1, len(gt_concepts))
        print(f"  {gt}: {len(gt_concepts)} concepts, {gt_connected_ratio:.0%} connected")

        # Is this a "utility" file or "domain logic" file?
        if gt_connected_ratio < 0.3:
            print(f"    → LOW CONNECTIVITY: mostly isolated concepts — utility/logic file")
            print(f"    → KG structurally BLIND to this file (no edges to walk)")
        else:
            print(f"    → WELL CONNECTED: KG should be able to reach this")

print(f"\n{'='*80}")
print(f"SUMMARY: WHAT CAN BE SAVED?")
print(f"{'='*80}")
