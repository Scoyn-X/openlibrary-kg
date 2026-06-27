"""Issue classification and strategy routing.

Replaces the one-size-fits-all BFS + SUM ranking with an issue-aware
pipeline that asks "what kind of issue is this?" before deciding
*how* to find the relevant files.

Software engineering insight:
  Different issue types map to different parts of the codebase in
  structurally different ways.  An API endpoint issue (``POST /lists/add``)
  wants route matching + the plugin layer; a MARC catalog issue wants
  catalog/ subtree traversal; a refactoring issue wants import-graph
  analysis.  No single ranking formula covers them all well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IssueCategory(Enum):
    API_ROUTE = "api_route"          # POST /lists/add, endpoint, request handler
    SOLR_SEARCH = "solr_search"      # Solr, index, query, search feature
    MARC_CATALOG = "marc_catalog"    # MARC, ISBN, edition, author, metadata
    SCRIPT_TOOL = "script_tool"      # script, CLI, batch, scheduler, monitor
    REFACTOR = "refactor"            # "refactor X to use Y", "replace A with B"
    UI_FRONTEND = "ui_frontend"      # banner, display, template, partials, markdown
    DOMAIN_LOGIC = "domain_logic"    # core business rules, lending, waitinglist
    GENERAL = "general"              # catch-all


# ── Classification rules ──────────────────────────────────────────────
# Each rule is a (regex, weight) pair.  Weight contributes to scoring.
_RULES: dict[IssueCategory, list[tuple[str, float]]] = {
    IssueCategory.API_ROUTE: [
        (r"\b(POST|GET|PUT|DELETE|PATCH)\s+/\w", 1.0),
        (r"/\w+/\w+\b.*\bendpoint", 0.8),
        (r"\breturns?\s+\d{3}\b", 0.8),
        (r"\broute\b", 0.6),
        (r"\bhandler\b", 0.5),
        (r"\b(list|search|add|delete|edit|books?|authors?|subjects?)\s+endpoint", 0.7),
    ],
    IssueCategory.SOLR_SEARCH: [
        (r"\b[sS]olr\b", 1.0),
        (r"\breindex", 0.9),
        (r"\bindex\b.*\b(update|rebuild)", 0.8),
        (r"\bboolean\s+clause", 0.9),
        (r"\bsearch\s+(query|result|scheme|pipeline)", 0.8),
        (r"\bfacet\b", 0.7),
        (r"\bSolrQuery\b", 0.9),
        (r"\bwork.?search\b", 0.8),
    ],
    IssueCategory.MARC_CATALOG: [
        (r"\bMARC\b", 1.0),
        (r"\bISB[Nn]\b", 0.9),
        (r"\bOCLC\b", 0.9),
        (r"\bLCCN\b", 0.9),
        (r"\bDDC\b", 0.9),
        (r"\bcatalog\b", 0.7),
        (r"\bedition\b.*\b(match|merge|import)", 0.8),
        (r"\bauthor\b.*\b(match|record|disambig)", 0.8),
        (r"\bbibliograph", 0.8),
        (r"\badd.?book\b", 0.9),
        (r"\bimport.*\b(record|api|edition)", 0.7),
        (r"\bwikidata\b", 0.7),
        (r"\b(metadata|publisher|publish_date)\b.*\b(import|normalize)", 0.7),
    ],
    IssueCategory.SCRIPT_TOOL: [
        (r"\bscript\b", 0.8),
        (r"\bCLI\b", 0.9),
        (r"\b(command.?line|batch|scheduler?)\b", 0.8),
        (r"\bmonitor", 0.6),
        (r"\bupdater\b", 0.6),
        (r"\bmigration\b", 0.8),
        (r"\butility\b", 0.5),
        (r"\bdocker", 0.7),
    ],
    IssueCategory.REFACTOR: [
        (r"\brefactor", 1.0),
        (r"\b(instead of|rather than)\b", 0.9),
        (r"\breplace\b.*\bwith\b", 0.9),
        (r"\bmigrate\b.*\b(to|from)\b", 0.8),
        (r"\buse\b.*\binstead\b", 0.8),
        (r"\bexceeds?\b.*\b(complex|threshold)", 0.7),
        (r"\breorgani[sz]e\b", 0.6),
    ],
    IssueCategory.UI_FRONTEND: [
        (r"\bbanner\b", 0.9),
        (r"\bdisplay\b", 0.5),
        (r"\bUI\b", 0.8),
        (r"\bmarkdown\b", 0.8),
        (r"\btemplate\b", 0.7),
        (r"\bpartials?\b", 0.7),
        (r"\b(table.of.contents|toc)\b", 0.9),
        (r"\breading.?goal\b", 0.8),
        (r"\bmy.?books\b", 0.7),
    ],
    IssueCategory.DOMAIN_LOGIC: [
        (r"\blending\b", 0.9),
        (r"\bwaiting.?list\b", 0.9),
        (r"\bpatron\b", 0.8),
        (r"\b(borrow|loan)\b", 0.8),
        (r"\bbooknotes?\b", 0.9),
        (r"\breading.?log\b", 0.8),
        (r"\bobservations?\b", 0.7),
        (r"\b(ratings?|bookshelves?)\b", 0.7),
    ],
}


@dataclass
class IssueProfile:
    """What we know about an issue after classification."""
    category: IssueCategory
    confidence: float                     # 0-1
    all_scores: dict[IssueCategory, float] = field(default_factory=dict)

    @property
    def uses_bm25_primary(self) -> bool:
        """Categories where BM25 keyword matching is the stronger signal."""
        return self.category in {
            IssueCategory.MARC_CATALOG,
            IssueCategory.SOLR_SEARCH,
            IssueCategory.SCRIPT_TOOL,
            IssueCategory.REFACTOR,
        }

    @property
    def uses_kg_primary(self) -> bool:
        """Categories where KG semantic matching is the stronger signal."""
        return self.category in {
            IssueCategory.API_ROUTE,
            IssueCategory.UI_FRONTEND,
            IssueCategory.DOMAIN_LOGIC,
            IssueCategory.GENERAL,
        }

    @property
    def focus_subdomains(self) -> list[str]:
        """Which subdirectories are most relevant for this issue type?"""
        mapping = {
            IssueCategory.API_ROUTE: ["plugins", "fastapi"],
            IssueCategory.SOLR_SEARCH: ["solr", "plugins/worksearch"],
            IssueCategory.MARC_CATALOG: ["catalog", "core"],
            IssueCategory.SCRIPT_TOOL: ["scripts"],
            IssueCategory.REFACTOR: [],       # depends on what's being refactored
            IssueCategory.UI_FRONTEND: ["plugins/openlibrary", "fastapi"],
            IssueCategory.DOMAIN_LOGIC: ["core"],
            IssueCategory.GENERAL: [],
        }
        return mapping.get(self.category, [])


def classify_issue(title: str, body: str = "") -> IssueProfile:
    """Classify an issue into one or more categories based on its text.

    Returns an IssueProfile with the highest-scoring category and confidence.
    """
    text = ((title or "") + " " + (body or "")).lower()
    scores: dict[IssueCategory, float] = {}

    for cat, rules in _RULES.items():
        score = 0.0
        for pattern, weight in rules:
            if re.search(pattern, text):
                score += weight
        if score > 0:
            scores[cat] = min(score, 1.0)  # cap at 1.0

    if not scores:
        return IssueProfile(
            category=IssueCategory.GENERAL,
            confidence=0.3,
            all_scores={IssueCategory.GENERAL: 0.3},
        )

    # Pick the highest-scoring category
    best_cat = max(scores, key=scores.get)
    return IssueProfile(
        category=best_cat,
        confidence=scores[best_cat],
        all_scores=scores,
    )


# ── Subdomain extraction (architecture-aware) ──────────────────────────

_SUBDOMAIN_RE = re.compile(
    r"^(accounts|admin|catalog|components|core|coverstore|data|fastapi|"
    r"i18n|macros|mocks|olbase|plugins|schemata|solr|templates|"
    r"utils|views|scripts)(?:/|$)"
)


def file_subdomain(file_path: str) -> str:
    """Extract the top-level subdomain from a file path.

    >>> file_subdomain('core/lending.py')
    'core'
    >>> file_subdomain('plugins/upstream/account.py')
    'plugins'
    """
    fp = file_path.replace("\\", "/")
    for prefix in ("openlibrary/openlibrary/", "Openlibrary/openlibrary/"):
        if prefix in fp:
            fp = fp.split(prefix, 1)[1]
    m = _SUBDOMAIN_RE.match(fp)
    return m.group(1) if m else "other"


def compute_strategy_weights(
    issue_profile: IssueProfile,
    kg_files: dict[str, float],
    bm25_files: dict[str, float],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Combine KG and BM25 rankings using an issue-type-aware strategy.

    For BM25-primary issues (MARC, Solr, scripts, refactor):
        BM25 dominates, KG provides a light semantic boost.
    For KG-primary issues (API, UI, domain logic, general):
        KG dominates, BM25 provides keyword fallback.
    Both: architecture-aware subdomain weighting.
    """
    result_scores: dict[str, float] = {}
    focus_domains = set(issue_profile.focus_subdomains)

    if issue_profile.uses_bm25_primary:
        alpha = 0.85  # BM25 weight
    else:
        alpha = 0.40  # KG weight dominates

    # Normalize scores to 0-1 range for fair blending
    def _normalize(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return {}
        max_v = max(d.values())
        if max_v <= 0:
            return d
        return {k: v / max_v for k, v in d.items()}

    kg_norm = _normalize(kg_files)
    bm25_norm = _normalize(bm25_files)

    all_files = set(kg_norm) | set(bm25_norm)

    for fp in all_files:
        kg_s = kg_norm.get(fp, 0.0)
        bm_s = bm25_norm.get(fp, 0.0)

        # ── Architecture-aware subdomain bonus ─────────────────────
        sub_bonus = 1.0
        if focus_domains:
            sd = file_subdomain(fp)
            if sd in focus_domains:
                sub_bonus = 1.15  # +15% for files in focus subdomains

        # Blend
        score = (alpha * bm_s + (1 - alpha) * kg_s) * sub_bonus
        result_scores[fp] = score

    ranked = sorted(result_scores.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {
            "file_path": fp,
            "score": round(score, 4),
            "strategy": f"{issue_profile.category.value}_alpha={alpha:.2f}",
        }
        for fp, score in ranked[:top_k]
    ]
