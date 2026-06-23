"""
Modify ground truth to prepend LLM terms to problem_statement, then evaluate.
"""
import json
from pathlib import Path

OUTPUT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg/output")

# Load augmented ground truth
gt = json.loads((OUTPUT / "swebench_ground_truth_llm.json").read_text(encoding="utf-8"))

# Prepend LLM terms to problem_statement
for rec in gt:
    terms = rec.get("llm_terms", "")
    original = rec.get("problem_statement", "")
    if terms:
        rec["problem_statement"] = f"[KG terms: {terms}]\n\n{original}"

# Save modified ground truth
gt_path = OUTPUT / "swebench_ground_truth_llm_augmented.json"
with open(gt_path, "w", encoding="utf-8") as f:
    json.dump(gt, f, ensure_ascii=False, indent=2)
print(f"Saved: {gt_path}")

# Now run evaluation with the augmented issue text
import subprocess
import sys

print("\n=== Evaluating with LLM-augmented issue text ===")
cmd = [
    sys.executable, "scripts/compare_methods.py",
    "--no-bm25",
    "--kg", "output/phase_6_knowledge_graph.json",
    "--ground-truth", str(gt_path),
    "--out-dir", "output",
    "--top-k", "10",
]
result = subprocess.run(cmd, cwd="D:/Secret/Sem4/SE/frontier/openlibrary-kg", capture_output=True, text=True)
print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-500:])
