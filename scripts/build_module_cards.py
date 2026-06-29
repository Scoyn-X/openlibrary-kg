"""Build module responsibility cards for all Python files in the codebase.

Each card contains:
  - file_path: relative path
  - responsibility: one-sentence description of what this file does
  - layer: one of {domain_model, business_logic, http_handler, utility, script, test_support, config}
  - subdomain: which top-level directory
  - key_symbols: top-level function/class names extracted from the file
  - imports_from: files it imports (dependency edges)
  - imported_by: files that import it

Output: output/module_cards.json

Uses the same llm_baseline_config.json for API access.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from collections import defaultdict

PROJECT = Path("D:/Secret/Sem4/SE/frontier/openlibrary-kg")
OUTPUT = PROJECT / "output"

SYSTEM_PROMPT = """You are an expert Python software architect analyzing the OpenLibrary codebase.

For each source file, I will give you:
  - The file path
  - The main classes and functions defined in it
  - A few import statements

You must output a SINGLE LINE of JSON with these fields:
{
  "responsibility": "one sentence describing what this file does in the codebase",
  "layer": "one of: domain_model, business_logic, http_handler, utility, script, data_access, config",
  "key_concepts": ["2-4 domain concepts this file is fundamentally about"]
}

Layer definitions:
- domain_model: defines core data types, entities, schemas
- business_logic: implements business rules and workflows
- http_handler: handles HTTP requests, routes, API endpoints
- utility: stateless helper functions, format conversion, validation
- script: standalone runnable script, batch job, migration
- data_access: database queries, Solr indexing, data providers
- config: configuration, constants, environment settings

Rules:
- Keep responsibility to ONE sentence.
- key_concepts should be abstract domain-level terms (e.g. "borrowing", "catalog metadata", "ISBN resolution"), NOT code identifiers.
- Output ONLY the JSON line. No markdown, no explanation.
"""

USER_PROMPT_TEMPLATE = """File: {file_path}

Key symbols defined here:
{key_symbols}

Sample imports:
{imports}

JSON:"""


# ── File analysis ───────────────────────────────────────────────────────

def analyze_file(file_path: Path, codebase_root: Path) -> dict | None:
    """Extract key symbols and imports from a Python file."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None

    rel_path = file_path.relative_to(codebase_root).as_posix()

    # Extract top-level function and class names
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if not node.name.startswith("_"):
                symbols.append(f"def {node.name}")
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")

    # Extract import statements
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names if a.name != "*")
            if names:
                imports.append(f"from {module} import {names}")

    if not symbols and not imports:
        return None

    return {
        "file_path": rel_path,
        "key_symbols": symbols[:15],  # top 15
        "imports": imports[:10],       # first 10 imports
        "symbol_count": len(symbols),
    }


# ── LLM client ──────────────────────────────────────────────────────────

class LLMClient:
    def __init__(self, config: dict):
        import httpx
        self.model = config.get("model", "openai/gpt-4o")
        api_key = config.get("api_key", "")
        api_base = config.get("api_base", "https://openrouter.ai/api/v1")
        if not api_key:
            raise ValueError("No API key configured")

        self._client = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )
        self.temperature = config.get("temperature", 0.2)
        self.max_tokens = config.get("max_tokens", 200)

    async def generate(self, user_prompt: str) -> str:
        for attempt in range(3):
            try:
                resp = await self._client.post(
                    "/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    },
                )
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code != 200:
                    print(f"  API {resp.status_code}: {resp.text[:100]}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                    continue
                return resp.json()["choices"][0]["message"]["content"]
            except Exception as exc:
                print(f"  Error: {exc}")
                if attempt < 2:
                    await asyncio.sleep(2)
        return "{}"


# ── Main ────────────────────────────────────────────────────────────────

async def main():
    # Load LLM config
    config_path = PROJECT / "llm_baseline_config.json"
    if not config_path.exists():
        print("Need llm_baseline_config.json")
        sys.exit(1)
    llm_config = json.loads(config_path.read_text(encoding="utf-8"))["llm"]

    codebase_root = Path(
        json.loads(config_path.read_text(encoding="utf-8")).get(
            "codebase_root", "D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary"
        )
    )

    # Discover files
    files = []
    for py_file in sorted(codebase_root.rglob("*.py")):
        rel = py_file.relative_to(codebase_root).as_posix()
        if any(x in rel for x in ["/tests/", "/vendor/", "/mocks/", "conftest.py"]):
            continue
        files.append(py_file)

    print(f"Found {len(files)} Python files")

    # Analyze each file (no API needed yet)
    analyzed = []
    for fp in files:
        info = analyze_file(fp, codebase_root)
        if info:
            analyzed.append(info)

    print(f"Analyzed {len(analyzed)} files with AST")

    # Build import graph (dependency edges)
    # Map: file_path -> set of modules it imports from
    import_graph: dict[str, set[str]] = defaultdict(set)
    reverse_graph: dict[str, set[str]] = defaultdict(set)

    import_graph_node: dict[str, set[str]] = defaultdict(set)
    for info in analyzed:
        fp = info["file_path"]
        for imp_line in info["imports"]:
            # Extract module name from import statement
            if imp_line.startswith("from "):
                # from openlibrary.core.models import Thing
                parts = imp_line.split(" import ")[0].replace("from ", "")
            elif imp_line.startswith("import "):
                parts = imp_line.replace("import ", "")
            else:
                continue
            # Normalize: first segment is the top-level module
            mod = parts.strip().split(".")[0]
            if mod in {"openlibrary", "core", "plugins", "catalog", "solr",
                        "scripts", "utils", "fastapi", "coverstore", "accounts",
                        "admin", "i18n", "olbase", "mocks", "data", "views",
                        "components", "macros", "schemata", "templates"}:
                continue  # skip known top-level aliases
            import_graph[fp].add(parts)

    # ── Batch LLM calls ──────────────────────────────────────────────────
    client = LLMClient(llm_config)
    sem = asyncio.Semaphore(3)  # lower concurrency for rate limits

    async def process_one(info: dict, idx: int, total: int) -> dict | None:
        async with sem:
            prompt = USER_PROMPT_TEMPLATE.format(
                file_path=info["file_path"],
                key_symbols="\n".join(f"  {s}" for s in info["key_symbols"][:12]),
                imports="\n".join(f"  {s}" for s in info["imports"][:8]),
            )
            try:
                raw = await client.generate(prompt)
                data = json.loads(raw.strip())
                info["responsibility"] = data.get("responsibility", "")
                info["layer"] = data.get("layer", "utility")
                info["key_concepts"] = data.get("key_concepts", [])
            except json.JSONDecodeError:
                # Try to extract JSON object
                import re
                m = re.search(r'\{.*?\}', raw, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(0))
                        info["responsibility"] = data.get("responsibility", "")
                        info["layer"] = data.get("layer", "utility")
                        info["key_concepts"] = data.get("key_concepts", [])
                    except:
                        info["responsibility"] = ""
                        info["layer"] = "utility"
                        info["key_concepts"] = []
                else:
                    info["responsibility"] = ""
                    info["layer"] = "utility"
                    info["key_concepts"] = []

            if (idx + 1) % 10 == 0:
                print(f"  [{idx+1}/{total}] {info['file_path'][:60]}")
            return info

    # Process all files in batches
    total = len(analyzed)
    tasks = [process_one(info, i, total) for i, info in enumerate(analyzed)]
    results = await asyncio.gather(*tasks)

    # Build reverse import graph
    file_to_rel_path = {info["file_path"]: info["file_path"] for info in results}
    for info in results:
        fp = info["file_path"]
        for imported_mod in import_graph.get(fp, set()):
            # Try to match to a known file
            for other_fp in file_to_rel_path:
                if other_fp.replace("/", ".").replace(".py", "").endswith(
                    imported_mod.replace("/", ".").replace(".py", "")
                ):
                    reverse_graph[other_fp].add(fp)
                    break

    # Final output
    cards = []
    for info in results:
        cards.append({
            "file_path": info["file_path"],
            "responsibility": info.get("responsibility", ""),
            "layer": info.get("layer", "utility"),
            "key_concepts": info.get("key_concepts", []),
            "symbol_count": info.get("symbol_count", 0),
            "imports_from_count": len(import_graph.get(info["file_path"], set())),
            "imported_by_count": len(reverse_graph.get(info["file_path"], set())),
        })

    # Save
    out_path = OUTPUT / "module_cards.json"
    out_path.write_text(
        json.dumps({
            "total": len(cards),
            "by_layer": {
                layer: len([c for c in cards if c["layer"] == layer])
                for layer in ["domain_model", "business_logic", "http_handler",
                              "utility", "script", "data_access", "config"]
            },
            "cards": cards,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {len(cards)} module cards to {out_path}")

    # Print stats
    from collections import Counter
    layers = Counter(c["layer"] for c in cards)
    print(f"\nBy layer:")
    for layer, count in layers.most_common():
        print(f"  {layer}: {count}")

    # Show a few examples
    print(f"\nExample cards:")
    for card in cards[:5]:
        if card["responsibility"]:
            print(f"  {card['file_path']}")
            print(f"    [{card['layer']}] {card['responsibility'][:120]}")
            print(f"    concepts: {card['key_concepts']}")


if __name__ == "__main__":
    asyncio.run(main())
