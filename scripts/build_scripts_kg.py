"""
Extract concepts from scripts/ directory, generate LLM definitions,
then merge into the existing KG.

Output: scripts_phase1.json → scripts_phase2.json → merged KG.
"""
import ast
import asyncio
import json
import re
import uuid
from pathlib import Path
from collections import defaultdict

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")
CODEEBASE = Path("D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary")
SCRIPTS_DIR = CODEEBASE / "scripts"
OUTPUT = PROJECT / "output"

from openlibrary_kg.extraction.name_splitter import split_identifier
from openlibrary_kg.extraction.noun_filter import filter_tokens
from openlibrary_kg.config import load_config

async def main():
    # ── Step 1: Extract concepts from scripts/ ──
    phase1 = json.loads((OUTPUT / "phase_1_concepts.json").read_text(encoding="utf-8"))
    existing_names = {o["split_name"] for o in phase1["occurrences"]}
    existing_occs = {(o["context"]["file_path"], o["context"]["line_number"]) for o in phase1["occurrences"]}

    new_occurrences = []
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

        for node in ast.walk(tree):
            # ClassDef
            if isinstance(node, ast.ClassDef):
                parts = split_identifier(node.name)
                valid = filter_tokens(parts)
                for part in valid:
                    key = (fp, node.lineno)
                    if key in existing_occs:
                        continue
                    start = max(0, node.lineno - 2)
                    end = min(len(lines), (node.end_lineno or node.lineno) + 1)
                    snippet = "\n".join(lines[start:end])[:300]
                    new_occurrences.append({
                        "occurrence_id": str(uuid.uuid4()),
                        "raw_identifier": node.name,
                        "split_name": part,
                        "identifier_type": "class_name",
                        "context": {
                            "file_path": fp,
                            "function_name": "",
                            "class_name": node.name,
                            "line_number": node.lineno,
                            "code_snippet": snippet,
                            "block_type": "class",
                        },
                    })

            # FunctionDef
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                parts = split_identifier(node.name)
                valid = filter_tokens(parts)
                for part in valid:
                    key = (fp, node.lineno)
                    if key in existing_occs:
                        continue
                    start = max(0, node.lineno - 2)
                    end = min(len(lines), (node.end_lineno or node.lineno) + 1)
                    snippet = "\n".join(lines[start:end])[:300]
                    new_occurrences.append({
                        "occurrence_id": str(uuid.uuid4()),
                        "raw_identifier": node.name,
                        "split_name": part,
                        "identifier_type": "function_name",
                        "context": {
                            "file_path": fp,
                            "function_name": node.name,
                            "class_name": "",
                            "line_number": node.lineno,
                            "code_snippet": snippet,
                            "block_type": "function",
                        },
                    })

    new_names = {o["split_name"] for o in new_occurrences}
    truly_new = new_names - existing_names
    print(f"Scripts/ occurrences: {len(new_occurrences)}")
    print(f"New concepts: {len(truly_new)}")
    print(f"Sample new: {sorted(list(truly_new))[:20]}")

    # Save phase_1 for scripts only
    scripts_phase1 = {
        "phase": "phase_1_scripts",
        "metadata": {"total_occurrences": len(new_occurrences)},
        "occurrences": new_occurrences,
    }
    scripts_phase1_path = OUTPUT / "scripts_phase_1.json"
    with open(scripts_phase1_path, "w", encoding="utf-8") as f:
        json.dump(scripts_phase1, f, ensure_ascii=False, indent=2)
    print(f"Saved: {scripts_phase1_path}")

    # ── Step 2: Generate LLM definitions ──
    print("\nGenerating LLM definitions for scripts/ concepts...")
    config = load_config(str(PROJECT / "config.yaml"))

    from openlibrary_kg.llm.definition_generator import generate_definitions
    occurrences_with_defs = await generate_definitions(
        new_occurrences, config, sample=None, strict=False,
    )

    non_empty = sum(1 for o in occurrences_with_defs if o.get("definition"))
    print(f"Definitions generated: {non_empty} / {len(occurrences_with_defs)}")

    # Save scripts phase_2
    scripts_phase2 = {
        "phase": "phase_2_scripts",
        "metadata": {"definitions_generated": non_empty},
        "occurrences": occurrences_with_defs,
    }
    scripts_phase2_path = OUTPUT / "scripts_phase_2.json"
    with open(scripts_phase2_path, "w", encoding="utf-8") as f:
        json.dump(scripts_phase2, f, ensure_ascii=False, indent=2)
    print(f"Saved: {scripts_phase2_path}")

    # ── Step 3: Merge into KG ──
    phase6 = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
    existing_kg_names = {c["canonical_name"] for c in phase6["concepts"]}
    concept_index = phase6.get("concept_index", {})

    added = 0
    by_name = defaultdict(list)
    for occ in occurrences_with_defs:
        by_name[occ["split_name"]].append(occ)

    for name, occs in sorted(by_name.items()):
        if name in existing_kg_names:
            continue
        raw_ids = list(set(o["raw_identifier"] for o in occs))
        definition = occs[0].get("definition", f"A {name} entity.")
        concept_entry = {
            "concept_id": name,
            "canonical_name": name,
            "split_terms": [name],
            "all_raw_identifiers": raw_ids,
            "occurrences": occs,
            "frequency": len(occs),
            "definition_clusters": [{
                "cluster_id": 0,
                "definition": definition,
                "occurrence_ids": [o["occurrence_id"] for o in occs],
                "size": len(occs),
            }],
        }
        phase6["concepts"].append(concept_entry)
        concept_index[name] = len(phase6["concepts"]) - 1
        added += 1

    kg_path = OUTPUT / "phase_6_knowledge_graph_scripts.json"
    with open(kg_path, "w", encoding="utf-8") as f:
        json.dump(phase6, f, ensure_ascii=False, indent=2)

    print(f"\nMerged: {added} new concepts into KG")
    print(f"KG now: {len(phase6['concepts'])} concepts, {len(phase6['relationships'])} edges")
    print(f"Saved: {kg_path}")

if __name__ == "__main__":
    asyncio.run(main())
