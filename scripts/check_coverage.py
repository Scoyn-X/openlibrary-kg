"""Quick: how many files does KG cover vs BM25?"""
import json
from pathlib import Path

CODEEBASE = Path("D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary")
OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# KG files
kg_full = json.loads((OUTPUT / "phase_6_knowledge_graph.json").read_text(encoding="utf-8"))
kg_files = set()
for c in kg_full["concepts"]:
    for occ in c.get("occurrences", []):
        fp = occ.get("context", {}).get("file_path", "")
        if fp:
            fp = fp.replace("\\", "/")
            for pre in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
                if pre in fp:
                    fp = fp.split(pre, 1)[1]
            kg_files.add(fp)
print(f"KG covers: {len(kg_files)} files")

# BM25 files
bm25_files = set()
codebase_str = str(CODEEBASE).replace("\\", "/") + "/"
for py_file in CODEEBASE.rglob("*.py"):
    fp = str(py_file).replace("\\", "/")
    if "tests" in fp or "vendor" in fp or "conftest" in fp:
        continue
    fp = fp.replace(codebase_str, "")
    bm25_files.add(fp)
print(f"BM25 covers: {len(bm25_files)} files")

missing = bm25_files - kg_files
print(f"\nMissing from KG: {len(missing)} files")
for f in sorted(missing):
    print(f"  {f}")

# Also show KG-only files
kg_only = kg_files - bm25_files
print(f"\nKG-only (not in BM25): {len(kg_only)} files")
for f in sorted(kg_only)[:10]:
    print(f"  {f}")
if len(kg_only) > 10:
    print(f"  ... and {len(kg_only)-10} more")
