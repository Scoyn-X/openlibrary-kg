"""Check scripts/ coverage: are new concepts connected or isolated?"""
import json
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

p3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
p5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))
p6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))

# Find scripts/ concepts
scripts_concepts = set()
for c in p6["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ["context"]["file_path"]
        fp = fp.replace("\\", "/")
        if "/scripts/" in fp:
            scripts_concepts.add(c["canonical_name"])
            break

print(f"Concepts from scripts/: {len(scripts_concepts)}")

# Connectivity
syn_connected = set()
for r in p3["relationships"]:
    syn_connected.add(r["source_concept_id"])
    syn_connected.add(r["target_concept_id"])
cooc_connected = set()
for r in p5["relationships"]:
    cooc_connected.add(r["source_concept_id"])
    cooc_connected.add(r["target_concept_id"])
all_edged = syn_connected | cooc_connected

scripts_edged = scripts_concepts & all_edged
scripts_isolated = scripts_concepts - all_edged
print(f"Scripts with edges: {len(scripts_edged)}")
print(f"Scripts ISOLATED:   {len(scripts_isolated)} ({100*len(scripts_isolated)/max(1,len(scripts_concepts)):.1f}%)")

# Compare to overall
all_concepts = {c["canonical_name"] for c in p6["concepts"]}
total_isolated = len(all_concepts - all_edged)
print(f"\nOverall isolated: {total_isolated}/{len(all_concepts)} ({100*total_isolated/len(all_concepts):.1f}%)")

# Build adjacency to check degrees
concept_freq = {c["canonical_name"]: c.get("frequency", 0) for c in p6["concepts"]}
adj = defaultdict(set)
for r in p3["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])
for r in p5["relationships"]:
    adj[r["source_concept_id"]].add(r["target_concept_id"])
    adj[r["target_concept_id"]].add(r["source_concept_id"])

# Show top isolated scripts concepts
if scripts_isolated:
    top_iso = sorted(scripts_isolated, key=lambda x: concept_freq.get(x, 0), reverse=True)[:20]
    print(f"\nTop isolated scripts/ concepts:")
    for name in top_iso:
        print(f"  {name}: freq={concept_freq.get(name, 0)}")

# Show connected scripts concepts and their neighbors
if scripts_edged:
    top = sorted(scripts_edged, key=lambda x: concept_freq.get(x, 0), reverse=True)[:15]
    print(f"\nTop connected scripts/ concepts:")
    for name in top:
        nbs = adj.get(name, set())
        print(f"  {name}: freq={concept_freq.get(name,0)}, degree={len(nbs)}, neighbors={sorted(list(nbs))[:5]}")

# ---- NOW: check the specific GT files that KG misses ----
print(f"\n{'='*60}")
print(f"SPECIFIC GT FILES CHECK")
print(f"{'='*60}")

# List the 5 "coverage missing" GT files from our analysis
gt_files_of_interest = [
    "scripts/providers/isbndb.py",
    "scripts/monitoring/monitor.py",
    "scripts/monitoring/utils.py",
    "scripts/import_standard_ebooks.py",
    "scripts/partner_batch_imports.py",
    "scripts/new-solr-updater.py",
    "scripts/import_open_textbook_library.py",
]

# Build file -> concepts mapping
file_concepts = defaultdict(set)
for c in p6["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ["context"]["file_path"]
        fp = fp.replace("\\", "/")
        for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
            if pre in fp:
                fp = fp.split(pre, 1)[1]
        file_concepts[fp].add(c["canonical_name"])

for gt in gt_files_of_interest:
    matches = [f for f in file_concepts if gt in f or f.endswith(gt.split("/")[-1])]
    if not matches:
        print(f"\n{gt}: NOT IN KG AT ALL")
        continue
    print(f"\n{gt}:")
    for m in matches[:2]:
        concepts = file_concepts[m]
        edged = concepts & all_edged
        isolated_cs = concepts - all_edged
        print(f"  -> {m}: {len(concepts)} concepts ({len(edged)} connected, {len(isolated_cs)} isolated)")
        if edged:
            # Show the best-connected concepts
            top = sorted(edged, key=lambda c: len(adj.get(c, set())), reverse=True)[:5]
            for c in top:
                print(f"     '{c}': degree={len(adj.get(c,set()))}, IDF-relevant")
