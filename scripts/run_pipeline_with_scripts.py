"""
One-shot: Run full pipeline with scripts/ included in concept extraction.
Creates a temporary config, runs phases 1-6, then evaluates.
"""
import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")
SCRIPTS = PROJECT / "scripts"
OUTPUT = PROJECT / "output"

# Step 1: Create a temp config with expanded include_patterns
# Read existing config (manually, since yaml has encoding issues)
config_lines = []
with open(PROJECT / "config.yaml", "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        config_lines.append(line)

# Replace the include_patterns line
new_lines = []
for line in config_lines:
    if 'include_patterns:' in line and 'openlibrary' in line:
        new_lines.append('  include_patterns: ["openlibrary/**/*.py", "scripts/**/*.py"]\n')
    else:
        new_lines.append(line)

temp_config = PROJECT / "config_temp.yaml"
with open(temp_config, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print(f"Created temp config: {temp_config}")

# Step 2: Run phases 1-6
phases = [
    ("extract_concepts.py", "Phase 1: Concept Extraction"),
    ("generate_definitions.py", "Phase 2: LLM Definitions"),
    ("detect_synonyms.py", "Phase 3: Synonyms"),
    ("analyze_polysemy.py", "Phase 4: Polysemy"),
    ("analyze_cooccurrence.py", "Phase 5: Co-occurrence"),
    ("build_kg.py", "Phase 6: KG Assembly"),
]

for script, desc in phases:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    cmd = [sys.executable, str(SCRIPTS / script), "--config", str(temp_config)]
    result = subprocess.run(cmd, cwd=str(PROJECT))
    if result.returncode != 0:
        print(f"FAILED: {desc} (exit {result.returncode})")
        sys.exit(1)
    print(f"DONE: {desc}")

print(f"\nPipeline complete!")
print(f"Output files:")
for f in sorted(OUTPUT.glob("*.json")):
    print(f"  {f.name}")
