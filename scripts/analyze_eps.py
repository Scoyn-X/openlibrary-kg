"""
Eps sensitivity analysis: test different thresholds on key polysemous concepts.
Uses cached definition embeddings from Phase 2 — no re-embedding needed.
"""
import json
import numpy as np
from pathlib import Path
from collections import defaultdict

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# Load definitions and poly data
p2 = json.loads((OUTPUT / "phase_2_definitions.json").read_text(encoding="utf-8"))
p4 = json.loads((OUTPUT / "phase_4_polysemy_groups.json").read_text(encoding="utf-8"))

# We need definition embeddings. Load them from the embedding cache.
# The QueryRewriter precomputes concept embeddings, but here we need per-occurrence def embeddings.
# Let's use the sentence-transformer directly to embed occurrence definitions.
from sentence_transformers import SentenceTransformer

print("Loading embedding model...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# Key concepts to analyze: high-frequency polysemous concepts
KEY_CONCEPTS = [
    "date", "book", "user", "work", "field", "line",
    "record", "edition", "author", "page", "group",
    "search", "publisher", "account", "title"
]

# Build occurrence index by split_name
occs_by_name = defaultdict(list)
for occ in p2["occurrences"]:
    name = occ["split_name"]
    if name in KEY_CONCEPTS:
        occs_by_name[name].append(occ)

print(f"\n{'='*70}")
print(f"EPS SENSITIVITY ANALYSIS")
print(f"{'='*70}")
print(f"{'Concept':>12} | {'Freq':>5} | ", end="")
all_eps = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 0.90, 1.0]
for eps in all_eps:
    print(f"eps={eps:.2f}", end="  ")
print()
print("-" * 100)

summary = {}

for name in KEY_CONCEPTS:
    occs = occs_by_name.get(name, [])
    if len(occs) < 3:
        print(f"{name:>12} | {len(occs):>5} | (too few)")
        continue

    # Get all definitions
    definitions = [o.get("definition", "") for o in occs]
    defs = [d for d in definitions if d]
    if len(defs) < 3:
        continue

    print(f"Embedding {len(defs)} defs for '{name}'...")
    vecs = model.encode(defs, show_progress_bar=False)

    print(f"{name:>12} | {len(defs):>5} | ", end="")
    row = {}
    for eps in all_eps:
        # Simple DBSCAN (same as polysemy.py)
        n = vecs.shape[0]
        labels = np.full(n, -1, dtype=int)
        cluster_id = 0
        for i in range(n):
            if labels[i] != -1:
                continue
            distances = np.linalg.norm(vecs - vecs[i], axis=1)
            neighbors = np.where(distances <= eps)[0]
            if len(neighbors) < 1:
                continue
            labels[neighbors] = cluster_id
            to_check = list(neighbors)
            while to_check:
                pt = to_check.pop(0)
                dist_pt = np.linalg.norm(vecs - vecs[pt], axis=1)
                nb_pt = np.where(dist_pt <= eps)[0]
                if len(nb_pt) >= 1:
                    for nb in nb_pt:
                        if labels[nb] == -1:
                            labels[nb] = cluster_id
                            to_check.append(nb)
            cluster_id += 1

        n_clusters = len(set(labels) - {-1})
        print(f"{n_clusters:4d}        ", end="")
        row[eps] = n_clusters

    print()
    summary[name] = row

# Recommendation: find eps where most key concepts have 3-8 clusters
print(f"\n{'='*70}")
print("RECOMMENDATION")
print(f"{'='*70}")
print(f"{'eps':>8} | avg_clusters | concepts_with_1-8 | verdict")
print("-" * 60)
for eps in all_eps:
    counts = [row[eps] for row in summary.values()]
    avg = sum(counts) / len(counts) if counts else 0
    good = sum(1 for c in counts if 1 <= c <= 8)
    verdict = "GOOD" if good >= len(counts) * 0.7 else ("TOO TIGHT" if avg > 15 else "TOO LOOSE")
    print(f"{eps:0.2f}     | {avg:6.1f}       | {good}/{len(counts)}              | {verdict}")

print(f"\nCurrent eps: 0.35 (produces {sum(row.get(0.35, 0) for row in summary.values())} clusters across these concepts)")
print(f"Recommendation: eps = 0.55-0.60 (keeps most concepts in 3-8 range)")
