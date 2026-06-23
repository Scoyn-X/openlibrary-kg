"""Diagnose remaining KG quality issues after eps fix."""
import json
import re
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

eval_data = json.loads((OUTPUT / "compare_per_issue_kg.json").read_text(encoding="utf-8"))
p6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
p3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
p5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))

# Build adjacency
adj = defaultdict(set)
for r in p3["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])
for r in p5["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])

# Build component lookup
comp_of = {}
for node in adj:
    if node not in comp_of:
        stack = [node]
        comp_id = node
        while stack:
            n = stack.pop()
            if n in comp_of:
                continue
            comp_of[n] = comp_id
            for nb in adj.get(n, set()):
                if nb not in comp_of:
                    stack.append(nb)

# Assign isolated concepts to own singleton components
for c in p6["concepts"]:
    name = c["canonical_name"]
    if name not in comp_of:
        comp_of[name] = name

# Build concept->files
concept_files = defaultdict(set)
for c in p6["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ["context"]["file_path"]
        fp = fp.replace("\\", "/")
        for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
            if pre in fp:
                fp = fp.split(pre, 1)[1]
        concept_files[c["canonical_name"]].add(fp)

STOP_WORDS = {'the','and','for','with','that','this','from','are','not','its','can','has',
              'had','was','were','but','when','then','also','into','over','after','before',
              'between','under','does','should','would','could','being','been','have','which','using'}

def tokenize(text):
    text = re.sub(r'[^a-zA-Z\s]', ' ', text.lower())
    return {t for t in text.split() if len(t) >= 3 and t not in STOP_WORDS}

categories = defaultdict(int)
disconnected_examples = []
noentry_examples = []
nocover_examples = []

for issue in eval_data["per_issue"]:
    if issue.get("file_rank") is not None and issue["file_rank"] <= 10:
        continue

    title = issue.get("title", "")
    gt_files = issue.get("gt_files", [])
    tokens = tokenize(title)

    gt_concepts = set()
    for gt_file in gt_files:
        for cname, files in concept_files.items():
            for fp in files:
                if gt_file in fp or fp in gt_file:
                    gt_concepts.add(cname)

    token_hits = tokens & set(concept_files.keys())
    seed_comp_ids = set(comp_of.get(t) for t in token_hits if t in comp_of)
    gt_comp_ids = set(comp_of.get(c) for c in gt_concepts if c in comp_of)
    same_comp = seed_comp_ids & gt_comp_ids

    if not seed_comp_ids:
        categories["no_entry"] += 1
        noentry_examples.append((title[:80], gt_files, tokens))
    elif not gt_comp_ids:
        categories["no_gt_coverage"] += 1
        nocover_examples.append((title[:80], gt_files))
    elif not same_comp:
        categories["disconnected"] += 1
        disconnected_examples.append((title[:80], gt_files, token_hits, gt_concepts))
    else:
        categories["same_comp_but_missed"] += 1

print("=== FAILURE MODE BREAKDOWN (15 missed issues) ===")
print(f"No entry (tokens not in KG):     {categories['no_entry']}")
print(f"No GT coverage (file not indexed): {categories['no_gt_coverage']}")
print(f"Disconnected (diff components):  {categories['disconnected']}")
print(f"Same component but not top-10:   {categories['same_comp_but_missed']}")

print(f"\n--- NO ENTRY examples ---")
for t, gt, tokens in noentry_examples[:3]:
    print(f"  {t}")
    print(f"    GT: {gt}, tokens: {tokens}")

print(f"\n--- DISCONNECTED examples ---")
for t, gt, hits, gtcs in disconnected_examples[:5]:
    print(f"  {t}")
    print(f"    GT: {gt}")
    print(f"    Issue tokens in KG: {hits}")
    print(f"    GT concepts (sample): {sorted(list(gtcs))[:5]}")

# Also check: what % of concepts in the NON-giant component are actually used by successful issues?
print(f"\n=== CONCEPT FREQUENCY BY COMPONENT ===")
giant_comp_id = max(set(comp_of.values()), key=list(comp_of.values()).count)
concept_freq = defaultdict(int)
for c in p6["concepts"]:
    name = c["canonical_name"]
    concept_freq[name] = c.get("frequency", 0)

giant_concepts = {n for n, cid in comp_of.items() if cid == giant_comp_id}
print(f"Giant component concepts: {len(giant_concepts)}")
print(f"  Avg frequency: {sum(concept_freq.get(c, 0) for c in giant_concepts)/max(1,len(giant_concepts)):.1f}")

small_comp_concepts = {n for n, cid in comp_of.items() if cid != giant_comp_id and cid != n}
print(f"Small component concepts: {len(small_comp_concepts)}")
print(f"  Avg frequency: {sum(concept_freq.get(c, 0) for c in small_comp_concepts)/max(1,len(small_comp_concepts)):.1f}")
