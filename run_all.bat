@echo off
REM ============================================================================
REM  Openlibrary-KG full pipeline
REM
REM  Modes:
REM    run_all.bat                Default: full pipeline (resumes from existing)
REM    run_all.bat demo           Fast (~30 min) preview using stratified sampling
REM    run_all.bat clean          Wipe output\ and .llm_cache\ first (interactive)
REM    run_all.bat eval           Run old compare_methods.py + new Phase 8 eval
REM    run_all.bat navigate       Phase 8 only: semantic navigation eval
REM    run_all.bat neo4j          Neo4j import + canned queries only
REM
REM  Logs: kg_construction.log   Outputs: output\
REM ============================================================================

setlocal ENABLEDELAYEDEXPANSION

REM Prevent sentence-transformers from phoning home to HuggingFace
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

set MODE=%1
if "%MODE%"=="" set MODE=run

REM ----------------------------------------------------------------------------
REM Demo defaults
REM ----------------------------------------------------------------------------
set DEMO_MIN_FREQ=8
set DEMO_PER_CONCEPT=5

REM ----------------------------------------------------------------------------
REM Phase 8 defaults
REM ----------------------------------------------------------------------------
set NAV_TOP_K=10
set NAV_MAX_HOPS=3

REM ----------------------------------------------------------------------------
REM Mode: clean -- interactive confirm then wipe output + cache
REM ----------------------------------------------------------------------------
if /I "%MODE%"=="clean" (
    rem  
    echo This will DELETE:
    echo   - output\*.json
    echo   - .llm_cache\
    echo   - output\.emb_cache
    rem  
    set /p CONFIRM="Type YES to confirm: "
    if /I not "!CONFIRM!"=="YES" (
        echo Aborted.
        exit /b 1
    )
    if exist output\*.json del /Q output\*.json
    if exist .llm_cache rmdir /S /Q .llm_cache
    if exist output\.emb_cache rmdir /S /Q output\.emb_cache
    echo Cleaned. Continuing with full run...
    set MODE=run
)

if not exist output mkdir output

REM ----------------------------------------------------------------------------
REM Helper: run a phase only if its output is missing.
REM   :run_phase  <output_file>  <description>  <command>
REM ----------------------------------------------------------------------------
goto :main

:run_phase
    set OUT=%~1
    set DESC=%~2
    set CMD=%~3
    rem  
    echo =================================================================
    echo   !DESC!
    echo =================================================================
    if exist "!OUT!" (
        echo [skip] !OUT! already exists. Delete it to force rerun.
        exit /b 0
    )
    echo Running: !CMD!
    call !CMD!
    set RC=!errorlevel!
    if !RC! NEQ 0 (
        rem  
        echo [FAIL] !DESC! returned errorlevel !RC!.
        echo        Fix the error and rerun run_all.bat to resume.
        exit /b !RC!
    )
    exit /b 0

:main

REM ----------------------------------------------------------------------------
REM Shortcut modes -- skip directly to the target section
REM ----------------------------------------------------------------------------
if /I "%MODE%"=="neo4j"      goto :neo4j
if /I "%MODE%"=="navigate"   goto :navigate_eval
if /I "%MODE%"=="eval"       goto :evaluation

REM ============================================================================
REM  BUILD PIPELINE (Phases 1-6)
REM ============================================================================

REM --- Phase 1: AST extraction (no API) ---
call :run_phase "output\phase_1_concepts.json" ^
                "Phase 1: Concept extraction" ^
                "python scripts\extract_concepts.py"
if errorlevel 1 exit /b 1

REM --- Phase 2: LLM definitions ---
if /I "%MODE%"=="demo" (
    call :run_phase "output\phase_2_definitions.json" ^
                    "Phase 2: LLM definitions (DEMO: stratified sample)" ^
                    "python scripts\generate_definitions.py --strategy stratified --min-freq %DEMO_MIN_FREQ% --per-concept %DEMO_PER_CONCEPT%"
) else (
    call :run_phase "output\phase_2_definitions.json" ^
                    "Phase 2: LLM definitions (full)" ^
                    "python scripts\generate_definitions.py"
)
if errorlevel 1 exit /b 1

REM --- Phase 3: Synonyms ---
call :run_phase "output\phase_3_synonyms.json" ^
                "Phase 3: Synonym detection (cosine + LLM judge)" ^
                "python scripts\detect_synonyms.py"
if errorlevel 1 exit /b 1

REM --- Phase 4: Polysemy ---
call :run_phase "output\phase_4_polysemy_groups.json" ^
                "Phase 4: Polysemy clustering" ^
                "python scripts\analyze_polysemy.py"
if errorlevel 1 exit /b 1

REM --- Phase 5: Co-occurrence ---
call :run_phase "output\phase_5_cooccurrence.json" ^
                "Phase 5: Co-occurrence (subdomain-aware Jaccard)" ^
                "python scripts\analyze_cooccurrence.py"
if errorlevel 1 exit /b 1

REM --- Phase 6: KG assembly ---
call :run_phase "output\phase_6_knowledge_graph.json" ^
                "Phase 6: KG assembly" ^
                "python scripts\build_kg.py"
if errorlevel 1 exit /b 1

REM --- Ground truth: SWE-bench Pro ---
call :run_phase "output\swebench_ground_truth.json" ^
                "Downstream prep: SWE-bench Pro ground truth" ^
                "python scripts\build_swebench_ground_truth.py"
if errorlevel 1 exit /b 1

REM ============================================================================
REM  EVALUATION
REM ============================================================================
:evaluation

REM --- (A) Original comparison: BM25 vs KG-walk ---
rem
echo =================================================================
echo   Evaluation (A): BM25 baseline vs KG-walk (compare_methods.py)
echo =================================================================
python scripts\compare_methods.py --top-k 10
if errorlevel 1 (
    rem  
    echo [WARN] compare_methods.py failed (non-fatal). Continuing...
)

:navigate_eval
REM --- (B) Phase 8: Semantic navigation evaluation ---
rem
echo =================================================================
echo   Evaluation (B): Phase 8 Semantic Navigation (navigate_issue.py)
echo =================================================================
set NAV_CMD=python scripts\navigate_issue.py --top-k %NAV_TOP_K% --max-hops %NAV_MAX_HOPS%
if exist "output\swebench_ground_truth.json" (
    set NAV_CMD=!NAV_CMD! --also-bm25
)
echo Running: !NAV_CMD!
call !NAV_CMD!
if errorlevel 1 (
    rem  
    echo [WARN] navigate_issue.py failed (non-fatal). Continuing...
)

REM ============================================================================
REM  NEO4J (skip for navigate/eval-only modes)
REM ============================================================================
if /I "%MODE%"=="navigate" goto :done
if /I "%MODE%"=="eval"     goto :done

:neo4j
REM --- Neo4j import (graceful fallback if not running) ---
rem
echo =================================================================
echo   Neo4j import
echo =================================================================
echo Probing Neo4j connection...
python -c "from openlibrary_kg.config import load_config; from neo4j import GraphDatabase; cfg = load_config('config.yaml').neo4j; d = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password)); s = d.session(database=cfg.database); s.run('RETURN 1').consume(); s.close(); d.close(); print('OK')"
if errorlevel 1 (
    rem  
    echo [WARN] Neo4j is not reachable. Skipping import + queries.
    echo        Start Neo4j, verify config.yaml password,
    echo        then run:  run_all.bat neo4j
    goto :done
)

rem
echo Importing KG into Neo4j (clearing existing data first)...
python scripts\export_to_neo4j.py --clear
if errorlevel 1 (
    rem  
    echo [WARN] Neo4j import failed. Continuing anyway.
    goto :done
)

rem
echo =================================================================
echo   Running canned Cypher queries
echo =================================================================
python scripts\run_kg_queries.py --out output\kg_queries_report.md

:done
rem
echo =================================================================
echo   Pipeline complete.
echo =================================================================
rem
echo   Output files in output\:
dir /B output\phase_*.json 2>nul
rem
echo   Evaluation results:
dir /B output\compare_*.json 2>nul
dir /B output\phase_8_*.json 2>nul
dir /B output\phase_8_*.md   2>nul
rem
echo   Other:
dir /B output\demo_*.json 2>nul
dir /B output\kg_queries_report.md 2>nul
rem
echo   Log:  kg_construction.log
echo =================================================================

endlocal
exit /b 0
