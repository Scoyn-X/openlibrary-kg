"""
Quick fix: Extract concepts from scripts/ directory and merge into KG.
Uses code snippets as placeholder definitions (no LLM calls needed).
"""
import ast
import json
import re
import math
import uuid
from pathlib import Path
from collections import defaultdict

CODEEBASE = Path("D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary")
SCRIPTS_DIR = CODEEBASE / "scripts"
OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# Load existing data
phase1 = json.loads((OUTPUT / "phase_1_concepts.json").read_text(encoding="utf-8"))
phase6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))

# Get existing concept set and occurrences
existing_names = {o["split_name"] for o in phase1["occurrences"]}
existing_occs = {(o["context"]["file_path"], o["context"]["line_number"]) for o in phase1["occurrences"]}
print(f"Existing concepts: {len(existing_names)}")
print(f"Existing occurrences: {len(phase1['occurrences'])}")

# Noun filter from the extractor
from openlibrary_kg.extraction.name_splitter import split_identifier
from openlibrary_kg.extraction.noun_filter import filter_tokens

# Extract from scripts/
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
new_occurrences = []
new_concept_names = set()

for py_file in sorted(SCRIPTS_DIR.rglob("*.py")):
    if "conftest" in py_file.name or "test" in py_file.name.lower():
        continue
    try:
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        continue

    fp = str(py_file).replace("\\", "/")
    lines = source.split("\n")

    # Also parse subdirectories like scripts/solr_builder/ etc
    for node in ast.walk(tree):
        # Class definitions
        if isinstance(node, ast.ClassDef):
            name = node.name
            parts = split_identifier(name)
            valid_parts = filter_tokens(parts)
            for part in valid_parts:

                # Get code snippet
                start = max(0, node.lineno - 2)
                end = min(len(lines), node.end_lineno + 1) if node.end_lineno else start + 5
                snippet = "\n".join(lines[start:end])[:300]

                key = (fp, node.lineno)
                if key in existing_occs:
                    continue

                new_occurrences.append({
                    "occurrence_id": str(uuid.uuid4()),
                    "raw_identifier": name,
                    "split_name": part,
                    "identifier_type": "class_name",
                    "context": {
                        "file_path": fp,
                        "function_name": "",
                        "class_name": name,
                        "line_number": node.lineno,
                        "code_snippet": snippet,
                        "block_type": "class",
                    },
                    "definition": f"A {part} entity in the openlibrary scripts.",
                })
                new_concept_names.add(part)

        # Function definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip dunder methods
            if node.name.startswith("__") and node.name.endswith("__"):
                continue

            name = node.name
            parts = split_identifier(name)
            valid_parts = filter_tokens(parts)
            for part in valid_parts:

                start = max(0, node.lineno - 2)
                end = min(len(lines), node.end_lineno + 1) if node.end_lineno else start + 5
                snippet = "\n".join(lines[start:end])[:300]

                key = (fp, node.lineno)
                if key in existing_occs:
                    continue

                new_occurrences.append({
                    "occurrence_id": str(uuid.uuid4()),
                    "raw_identifier": name,
                    "split_name": part,
                    "identifier_type": "function_name",
                    "context": {
                        "file_path": fp,
                        "function_name": name,
                        "class_name": "",
                        "line_number": node.lineno,
                        "code_snippet": snippet,
                        "block_type": "function",
                    },
                    "definition": f"A {part} utility in the openlibrary scripts.",
                })
                new_concept_names.add(part)

# Remove concepts already in KG
new_concept_names -= existing_names
print(f"\nNew concepts from scripts/: {len(new_concept_names)}")
print(f"New occurrences: {len(new_occurrences)}")

# Merge into phase_1
phase1["occurrences"].extend(new_occurrences)
phase1["metadata"]["total_occurrences"] = len(phase1["occurrences"])
phase1["metadata"]["scripts_added"] = len(new_occurrences)

with open(OUTPUT / "phase_1_concepts.json", "w", encoding="utf-8") as f:
    json.dump(phase1, f, ensure_ascii=False, indent=2)
print(f"Updated phase_1_concepts.json: {len(phase1['occurrences'])} occurrences")

# Add new concepts to phase_6 KG
existing_kg_names = {c["canonical_name"] for c in phase6["concepts"]}
concept_index = phase6.get("concept_index", {})

for name in sorted(new_concept_names):
    if name in existing_kg_names:
        continue

    # Find occurrences for this concept
    concept_occs = [o for o in new_occurrences if o["split_name"] == name]

    # Build concept entry
    raw_ids = list(set(o["raw_identifier"] for o in concept_occs))
    concept_entry = {
        "concept_id": name,
        "canonical_name": name,
        "split_terms": [name],
        "all_raw_identifiers": raw_ids,
        "occurrences": concept_occs,
        "frequency": len(concept_occs),
        "definition_clusters": [{
            "cluster_id": 0,
            "definition": concept_occs[0]["definition"] if concept_occs else f"A {name} entity.",
            "occurrence_ids": [o["occurrence_id"] for o in concept_occs],
            "size": len(concept_occs),
        }],
    }
    phase6["concepts"].append(concept_entry)
    concept_index[name] = len(phase6["concepts"]) - 1

# Save augmented KG for scripts/
scripts_kg_path = OUTPUT / "phase_6_knowledge_graph_scripts.json"
with open(scripts_kg_path, "w", encoding="utf-8") as f:
    json.dump(phase6, f, ensure_ascii=False, indent=2)

total_concepts = len(phase6["concepts"])
total_edges = len(phase6["relationships"])
print(f"\nSaved augmented KG: {total_concepts} concepts, {total_edges} edges → {scripts_kg_path}")
print(f"New concepts added: {len(new_concept_names)}")
if new_concept_names:
    print(f"Sample new concepts: {sorted(list(new_concept_names))[:30]}")
