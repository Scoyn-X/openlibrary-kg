"""
P1 v3: Call-graph edges with strict filtering.
- Only bridges concepts where AT LEAST one side is isolated.
- Max 5 edges per isolated concept (prevents hub explosion).
- Very low weight (0.08).
- Only uses the first hop's callgraph edges (avoids BFS cascade).
"""
import ast
import json
from pathlib import Path
from collections import defaultdict

CODEEBASE = Path("D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary/openlibrary")
OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

phase1 = json.loads((OUTPUT / "phase_1_concepts.json").read_text(encoding="utf-8"))
phase6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
phase3 = json.loads((OUTPUT / "phase_3_synonyms.json").read_text(encoding="utf-8"))
phase5 = json.loads((OUTPUT / "phase_5_cooccurrence.json").read_text(encoding="utf-8"))

# Find isolated concepts
connected = set()
for rel in phase3["relationships"]:
    connected.add(rel["source_concept_id"])
    connected.add(rel["target_concept_id"])
for rel in phase5["relationships"]:
    connected.add(rel["source_concept_id"])
    connected.add(rel["target_concept_id"])

all_kg_concepts = {c["canonical_name"] for c in phase6["concepts"]}
isolated = all_kg_concepts - connected
print(f"Isolated: {len(isolated)} / {len(all_kg_concepts)} ({100*len(isolated)/len(all_kg_concepts):.1f}%)")

# Build: file_path → {function_name: set of split_names}
file_func_concepts = defaultdict(lambda: defaultdict(set))
for occ in phase1["occurrences"]:
    fp = occ["context"]["file_path"]
    fn = occ["context"]["function_name"]
    sn = occ["split_name"]
    if fp and fn and sn in isolated:  # only track isolated concepts
        file_func_concepts[fp][fn].add(sn)

# Only track files that have isolated concepts
files_with_isolated = set(file_func_concepts.keys())
print(f"Files with isolated concepts: {len(files_with_isolated)}")

# Step 1: Extract call graph from AST (only relevant files)
call_graph = defaultdict(lambda: defaultdict(set))

class CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.calls = set()
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        self.generic_visit(node)

py_files = list(CODEEBASE.rglob("*.py"))
processed = 0
for py_file in py_files:
    fp = str(py_file).replace("\\", "/")
    if fp not in files_with_isolated:
        # Only parse files that have isolated concepts as callers
        # (but we still need to resolve callees from any file)
        pass
    try:
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        continue
    processed += 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visitor = CallVisitor()
            visitor.visit(node)
            if visitor.calls:
                call_graph[fp][node.name] = visitor.calls

print(f"Parsed {processed} files")

# Step 2: function → files lookup (for all files, not just those with isolated concepts)
func_to_files = defaultdict(set)
for py_file in CODEEBASE.rglob("*.py"):
    try:
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        continue
    fp = str(py_file).replace("\\", "/")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_to_files[node.name].add(fp)

# Also add concept functions
for fp, funcs in file_func_concepts.items():
    for fn in funcs:
        func_to_files[fn].add(fp)

print(f"Function name lookup: {len(func_to_files)} entries")

# Step 3: Create call-graph edges — isolated ↔ anything
WEIGHT = 0.08  # very low — call graph is a weak semantic signal
MAX_EDGES_PER_ISOLATED = 10  # prevent hub explosion

edges = []
edge_set = set()
isolated_edge_count = defaultdict(int)

for caller_fp, funcs in call_graph.items():
    caller_concepts_all = file_func_concepts.get(caller_fp, {})
    for caller_fn, callee_names in funcs.items():
        caller_concepts = caller_concepts_all.get(caller_fn, set()) & isolated
        if not caller_concepts:
            continue

        for callee_name in callee_names:
            callee_files = func_to_files.get(callee_name, set())
            for callee_fp in callee_files:
                # Get ALL concepts in the callee function (in any file)
                callee_concepts_all = defaultdict(set)
                for occ in phase1["occurrences"]:
                    if occ["context"]["function_name"] == callee_name:
                        callee_concepts_all[occ["split_name"]].add(occ["context"]["file_path"])

                callee_concepts = set(callee_concepts_all.keys()) & all_kg_concepts
                if not callee_concepts:
                    continue

                for c1 in caller_concepts:
                    if isolated_edge_count[c1] >= MAX_EDGES_PER_ISOLATED:
                        continue
                    for c2 in callee_concepts:
                        if c2 == c1:
                            continue
                        if isolated_edge_count.get(c1, 0) >= MAX_EDGES_PER_ISOLATED:
                            break
                        key = tuple(sorted([c1, c2]))
                        if key in edge_set:
                            continue
                        edge_set.add(key)
                        edges.append({
                            "source_concept_id": c1,
                            "target_concept_id": c2,
                            "relationship_type": "callgraph",
                            "weight": WEIGHT,
                            "metadata": {"source": "callgraph"},
                        })
                        isolated_edge_count[c1] += 1
                        if c2 in isolated:
                            isolated_edge_count[c2] += 1

print(f"Call-graph edges: {len(edges)}")
bridged = {c for e in edges for c in (e["source_concept_id"], e["target_concept_id"])} & isolated
print(f"Isolated concepts bridged: {len(bridged)} / {len(isolated)}")

# Merge
rel_by_type = defaultdict(list)
for r in phase6["relationships"]:
    rel_by_type[r["relationship_type"]].append(r)
print(f"Existing: synonym={len(rel_by_type['synonym'])}, co-occurrence={len(rel_by_type['co-occurrence'])}")

phase6["relationships"] = rel_by_type["synonym"] + rel_by_type["co-occurrence"] + edges
out_path = OUTPUT / "phase_6_knowledge_graph_callgraph.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(phase6, f, ensure_ascii=False, indent=2)
print(f"Saved: {out_path}")
print(f"Total edges: {len(phase6['relationships'])}")
