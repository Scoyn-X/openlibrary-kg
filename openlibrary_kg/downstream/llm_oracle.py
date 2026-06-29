"""LLM + KG Oracle: the KG validates LLM guesses, LLM reasons over KG facts.

Architecture:
  ┌──────────┐   "what concepts relate to X?"   ┌──────────┐
  │   LLM    │ ──────────────────────────────→ │    KG    │
  │ (GPT-4o) │ ←────────────────────────────── │ (Oracle) │
  └──────────┘   concept_card + neighbors       └──────────┘
       │                                                │
       │  LLM proposes concepts,                        │
       │  KG validates or rejects each,                  │
       │  LLM adapts based on feedback,                  │
       │  KG returns final subgraph + file ranking.      │
       ▼                                                ▼
  ┌──────────────────────────────────────────────────────┐
  │  Structured Agent output:                            │
  │    - Ranked files with reasoning chains              │
  │    - Impact analysis for the suggested edits         │
  │    - Confidence annotation (verified vs. guessed)    │
  └──────────────────────────────────────────────────────┘

This turns the KG from a "search tool" into an "oracle" — the LLM can
hypothesize, but the KG decides whether each hypothesis has evidence
in the codebase.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("openlibrary_kg.downstream.llm_oracle")


# ── Oracle prompts ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an AI agent working with a Knowledge Graph (KG) of the OpenLibrary Python codebase. The KG contains 4,338 concepts extracted from the source code, connected by synonym and co-occurrence relationships.

Your workflow:
1. I give you an issue description.
2. You propose 5-10 search terms that might identify relevant concepts in the KG.
3. I validate each term against the KG and tell you which ones actually exist, along with their semantic definitions and neighbors.
4. Based on KG feedback, you refine your understanding and propose a final set of files to examine.

Rules:
- Output ONLY valid JSON in the specified format. No markdown, no explanation outside the JSON.
- When I show you KG concept cards, USE them — they are ground truth about what exists in the codebase.
- If the KG says a concept doesn't exist, drop it and try related terms that the KG suggested.
- Be creative with term variations: "borrowing" might be "borrow", "loan", "lending" in the KG.
"""

ROUND1_PROMPT = """## Issue

{issue_text}

## Task

Propose 5-10 search terms (concept names) that are most likely to exist in the KG and help locate the files that need modification.

Output JSON:
{{"terms": ["term1", "term2", ...], "reasoning": "one sentence explaining your choice"}}"""

ROUND2_PROMPT = """## Issue

{issue_text}

## KG Validation Results

{validation}

## Task

Based on the KG feedback above, which concepts are most relevant? Propose a final set of concept names, then rank the files that should be examined.

Output JSON:
{{"confirmed_concepts": ["concept1", ...], "suggested_files": ["path/to/file1.py", ...], "confidence": "high|medium|low", "reasoning": "one sentence"}}"""


# ── Types ───────────────────────────────────────────────────────────────

@dataclass
class OracleRound:
    """One round of LLM-KG dialogue."""
    llm_terms: list[str] = field(default_factory=list)
    kg_validated: dict[str, dict] = field(default_factory=dict)
    kg_rejected: list[str] = field(default_factory=list)
    kg_suggestions: list[str] = field(default_factory=list)


@dataclass
class OracleResult:
    """Final output from the LLM-KG oracle."""
    issue_summary: str = ""
    confirmed_concepts: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    reasoning_chain: list[dict] = field(default_factory=list)
    confidence: str = "medium"
    rounds: list[OracleRound] = field(default_factory=list)


# ── Client ──────────────────────────────────────────────────────────────

class LLMOracleClient:
    """Minimal LLM client that speaks to the oracle."""

    def __init__(self, config: dict):
        import httpx
        self.model = config.get("model", "openai/gpt-4o")
        api_key = config.get("api_key", "")
        api_base = config.get("api_base", "https://openrouter.ai/api/v1")

        if not api_key:
            raise ValueError("No API key configured for LLM oracle")

        self._client = httpx.Client(
            base_url=api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0),
        )
        self.temperature = config.get("temperature", 0.0)
        self.max_tokens = config.get("max_tokens", 1000)

    def chat(self, user_prompt: str) -> str:
        resp = self._client.post(
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
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()["choices"][0]["message"]["content"]


# ── Oracle ──────────────────────────────────────────────────────────────

class LLMOracle:
    """The LLM-KG dialogue manager.

    Usage:
        from openlibrary_kg.kg_query import KGQuery
        kg = KGQuery("output/phase_6_knowledge_graph.json")

        config = json.load(open("llm_baseline_config.json"))
        oracle = LLMOracle(kg, config["llm"])

        result = oracle.diagnose(issue_title, issue_body)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    """

    def __init__(self, kg_query: Any, llm_config: dict):
        self.kg = kg_query
        self.client = LLMOracleClient(llm_config)

    def diagnose(
        self,
        title: str,
        body: str = "",
        max_rounds: int = 1,
    ) -> OracleResult:
        """Run the full LLM-KG dialogue for an issue.

        Returns an OracleResult with confirmed concepts, ranked files,
        and an impact report for the Agent to consume.
        """
        issue_text = ((title or "") + "\n" + (body or "")).strip()
        if not issue_text:
            return OracleResult(issue_summary="Empty issue")

        rounds: list[OracleRound] = []

        # ── Round 1: LLM proposes terms, KG validates ──────────────
        r1_text = ROUND1_PROMPT.format(issue_text=issue_text[:4000])
        try:
            r1_raw = self.client.chat(r1_text)
            r1_data = self._parse_json(r1_raw)
        except Exception as exc:
            logger.error("Round 1 failed: %s", exc)
            return OracleResult(issue_summary=f"LLM error: {exc}")

        r1 = OracleRound()
        r1.llm_terms = r1_data.get("terms", [])[:10]

        # Validate each term against KG
        for term in r1.llm_terms:
            term_clean = term.strip().lower()
            card = self.kg.concept_card(term_clean)
            if card:
                r1.kg_validated[term_clean] = card
            else:
                r1.kg_rejected.append(term_clean)

        # Suggest neighbors of validated concepts
        for name in list(r1.kg_validated.keys()):
            for nb_info in r1.kg_validated[name].get("neighbors", [])[:3]:
                nb_name = nb_info["name"]
                if nb_name not in r1.kg_validated:
                    r1.kg_suggestions.append(nb_name)

        r1.kg_suggestions = list(set(r1.kg_suggestions))[:10]
        rounds.append(r1)

        # ── Build result from validated concepts ───────────────────
        result = OracleResult(
            issue_summary=issue_text[:200],
            rounds=rounds,
        )

        # Collect all validated concepts + BFS reachable
        all_concepts: dict[str, float] = {}
        for name, card in r1.kg_validated.items():
            all_concepts[name] = 1.0
            for nb_info in card.get("neighbors", []):
                all_concepts.setdefault(nb_info["name"], 0.5)

        result.confirmed_concepts = sorted(all_concepts.keys())[:30]

        # Compute file ranking from concept weights
        file_scores: dict[str, float] = {}
        for cname, weight in all_concepts.items():
            files = self.kg.get_files(cname)
            idf = self.kg.get_idf(cname)
            for fp in files:
                file_scores[fp] = file_scores.get(fp, 0.0) + weight * idf

        ranked = sorted(file_scores.items(), key=lambda kv: kv[1], reverse=True)

        # Build reasoning chains for top files
        for fp, score in ranked[:10]:
            fp_concepts = self.kg.get_concepts_in_file(fp)
            matched = [c for c in all_concepts if c in fp_concepts]
            # Find a path from any seed concept to a matched concept
            sample_path = None
            for seed in list(r1.kg_validated.keys())[:3]:
                for mc in matched[:3]:
                    sample_path = self.kg.explain_path(seed, mc, max_hops=3)
                    if sample_path:
                        break
                if sample_path:
                    break

            result.files.append({
                "file_path": fp,
                "score": round(score, 4),
                "matched_concepts": matched[:10],
                "reasoning_chain": sample_path,
            })

        # Impact analysis for top files
        top_files = [f["file_path"] for f in result.files[:3]]
        try:
            result.impact = self.kg.impact_report(top_files)
        except Exception:
            pass

        # Confidence estimation
        validation_rate = (
            len(r1.kg_validated) / max(1, len(r1.llm_terms))
            if r1.llm_terms else 0
        )
        if validation_rate >= 0.6:
            result.confidence = "high"
        elif validation_rate >= 0.3:
            result.confidence = "medium"
        else:
            result.confidence = "low"

        return result

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Robust JSON extraction from LLM output."""
        raw = raw.strip()
        # Remove markdown code fences
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            return {}
