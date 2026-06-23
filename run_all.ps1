# Openlibrary-KG full pipeline (PowerShell version)
# Usage:
#   .\run_all.ps1                    Full pipeline (resume from existing)
#   .\run_all.ps1 -Mode demo         Fast preview with stratified sampling
#   .\run_all.ps1 -Mode clean        Wipe output + cache, then full run
#   .\run_all.ps1 -Mode navigate     Phase 8 only
#   .\run_all.ps1 -Mode eval         Evaluation only (skip build + Neo4j)
#   .\run_all.ps1 -Mode neo4j        Neo4j import only

param([string]$Mode = "run")

$ErrorActionPreference = "Stop"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# Ensure scripts can import openlibrary_kg without pip install -e .
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$PSScriptRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $PSScriptRoot
}

$Python = "C:\Python314\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$DEMO_MIN_FREQ = 8
$DEMO_PER_CONCEPT = 5
$NAV_TOP_K = 10
$NAV_MAX_HOPS = 3

if (-not (Test-Path output)) { New-Item -ItemType Directory output | Out-Null }

Set-Location $PSScriptRoot

function Run-Phase($OutputFile, $Description, $ScriptArgs) {
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host "  $Description"
    Write-Host ("=" * 60)
    if (Test-Path $OutputFile) {
        Write-Host "[skip] $OutputFile already exists. Delete it to force rerun."
        return
    }
    $argList = $ScriptArgs -split " "
    Write-Host "Running: python $ScriptArgs"
    & $Python $argList
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[FAIL] $Description returned exit code $LASTEXITCODE."
        Write-Host "       Fix the error and rerun to resume."
        exit $LASTEXITCODE
    }
}

function Run-Python($ScriptArgs, $WarnMsg = "") {
    $argList = $ScriptArgs -split " "
    & $Python $argList
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] $WarnMsg (exit code $LASTEXITCODE)"
    }
}

# --- Clean mode ---
if ($Mode -eq "clean") {
    Write-Host ""
    Write-Host "This will DELETE output\*.json, output\.emb_cache, and .llm_cache\"
    $confirm = Read-Host "Type YES to confirm"
    if ($confirm -ne "YES") {
        Write-Host "Aborted."
        exit 1
    }
    Remove-Item -Force output\*.json -ErrorAction SilentlyContinue
    if (Test-Path output\.emb_cache) { Remove-Item -Recurse -Force output\.emb_cache }
    if (Test-Path .llm_cache) { Remove-Item -Recurse -Force .llm_cache }
    Write-Host "Cleaned. Continuing with full run..."
    $Mode = "run"
}

# --- Mode dispatch ---
$doBuild    = ($Mode -eq "run") -or ($Mode -eq "demo") -or ($Mode -eq "clean")
$doEval     = $doBuild -or ($Mode -eq "eval")
$doNavigate = $doBuild -or ($Mode -eq "navigate")
$doNeo4j    = $doBuild -or ($Mode -eq "neo4j")

# ============================================================================
# BUILD PIPELINE (Phases 1-6)
# ============================================================================
if ($doBuild) {
    Run-Phase "output\phase_1_concepts.json" "Phase 1: Concept extraction" "scripts\extract_concepts.py"

    if ($Mode -eq "demo") {
        Run-Phase "output\phase_2_definitions.json" "Phase 2: LLM definitions (DEMO)" "scripts\generate_definitions.py --strategy stratified --min-freq $DEMO_MIN_FREQ --per-concept $DEMO_PER_CONCEPT"
    } else {
        Run-Phase "output\phase_2_definitions.json" "Phase 2: LLM definitions (full)" "scripts\generate_definitions.py"
    }

    Run-Phase "output\phase_3_synonyms.json" "Phase 3: Synonym detection" "scripts\detect_synonyms.py"
    Run-Phase "output\phase_4_polysemy_groups.json" "Phase 4: Polysemy clustering" "scripts\analyze_polysemy.py"
    Run-Phase "output\phase_5_cooccurrence.json" "Phase 5: Co-occurrence analysis" "scripts\analyze_cooccurrence.py"
    Run-Phase "output\phase_6_knowledge_graph.json" "Phase 6: KG assembly" "scripts\build_kg.py"
    Run-Phase "output\swebench_ground_truth.json" "Ground truth: SWE-bench Pro" "scripts\build_swebench_ground_truth.py"
}

# ============================================================================
# EVALUATION (A): compare_methods.py
# ============================================================================
if ($doEval) {
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host "  Evaluation (A): BM25 baseline vs KG-walk"
    Write-Host ("=" * 60)
    Run-Python "scripts\compare_methods.py --top-k 10" "compare_methods.py failed (non-fatal)"
}

# ============================================================================
# EVALUATION (B): Phase 8 navigate_issue.py
# ============================================================================
if ($doNavigate) {
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host "  Evaluation (B): Phase 8 Semantic Navigation"
    Write-Host ("=" * 60)
    $navArgs = "scripts\navigate_issue.py --top-k $NAV_TOP_K --max-hops $NAV_MAX_HOPS"
    if (Test-Path "output\swebench_ground_truth.json") {
        $navArgs += " --also-bm25"
    }
    Run-Python $navArgs "navigate_issue.py failed (non-fatal)"
}

# ============================================================================
# NEO4J
# ============================================================================
if ($doNeo4j) {
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host "  Neo4j import"
    Write-Host ("=" * 60)

    # Probe Neo4j connection using a temp script file
    $probeFile = Join-Path $PSScriptRoot "output\_neo4j_probe.py"
    @"
from openlibrary_kg.config import load_config
from neo4j import GraphDatabase
cfg = load_config('config.yaml').neo4j
d = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
s = d.session(database=cfg.database)
s.run('RETURN 1').consume()
s.close()
d.close()
print('OK')
"@ | Out-File -FilePath $probeFile -Encoding ASCII

    Write-Host "Probing Neo4j connection..."
    $neo4jOk = $false
    try {
        & $Python $probeFile
        if ($LASTEXITCODE -eq 0) { $neo4jOk = $true }
    } catch { }
    Remove-Item $probeFile -ErrorAction SilentlyContinue

    if (-not $neo4jOk) {
        Write-Host ""
        Write-Host "[WARN] Neo4j is not reachable. Skipping import + queries."
        Write-Host "       Start Neo4j, verify config.yaml password,"
        Write-Host "       then run: .\run_all.ps1 -Mode neo4j"
    } else {
        Write-Host ""
        Write-Host "Importing KG into Neo4j (clearing existing data first)..."
        Run-Python "scripts\export_to_neo4j.py --clear" "Neo4j import failed"

        Write-Host ""
        Write-Host ("=" * 60)
        Write-Host "  Running canned Cypher queries"
        Write-Host ("=" * 60)
        Run-Python "scripts\run_kg_queries.py --out output\kg_queries_report.md" "Cypher queries failed"
    }
}

# ============================================================================
# DONE
# ============================================================================
Write-Host ""
Write-Host ("=" * 60)
Write-Host "  Pipeline complete."
Write-Host ("=" * 60)
Write-Host ""
Write-Host "  Output files in output\:"
Get-ChildItem output\phase_*.json -Name -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Get-ChildItem output\compare_*.json -Name -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Get-ChildItem output\phase_8_*.json -Name -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Get-ChildItem output\phase_8_*.md -Name -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Get-ChildItem output\demo_*.json -Name -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Write-Host ""
Write-Host "  Log:  kg_construction.log"
Write-Host ("=" * 60)
