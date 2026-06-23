"""Fetch (issue → files-changed-by-fixing-PR) pairs as evaluation ground truth.

Strategy:
  1. Query openlibrary/openlibrary's GitHub for closed issues.
  2. For each issue, find a merged PR that says "fixes #N" or "closes #N"
     (GitHub's `timeline_url` exposes "cross-referenced" + "closed-by" events
     when a PR closed the issue). We also fall back to scanning PR bodies.
  3. Record the issue's title+body and the set of file paths the PR touched.

The output is a JSON file:
    [
        {
            "issue_number": 12345,
            "title": "...",
            "body": "...",
            "pr_number": 12350,
            "changed_files": ["openlibrary/accounts/model.py", ...],
            "url": "https://github.com/internetarchive/openlibrary/issues/12345"
        },
        ...
    ]

Only files matching `openlibrary/**.py` are recorded — issues fixed entirely
in templates/JS/CSS aren't useful for evaluating a Python-code KG.

GitHub auth: set GITHUB_TOKEN to avoid the 60-request/hour anonymous limit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("openlibrary_kg.downstream")

API_ROOT = "https://api.github.com"
DEFAULT_REPO = "internetarchive/openlibrary"

FIX_KEYWORDS = re.compile(
    r"\b(fix|fixes|fixed|close|closes|closed|resolve|resolves|resolved)\s+"
    r"(?:#|https?://github\.com/[^/]+/[^/]+/issues/)(\d+)",
    re.IGNORECASE,
)


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(client: httpx.Client, url: str, params: dict | None = None) -> Any:
    """GET with simple rate-limit handling."""
    for attempt in range(5):
        resp = client.get(url, params=params, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (403, 429):
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = max(5, int(reset) - int(time.time())) if reset else 60
            logger.warning("Rate limited; sleeping %ds", min(wait, 120))
            time.sleep(min(wait, 120))
            continue
        if resp.status_code == 404:
            return None
        logger.warning("GET %s -> %d: %s", url, resp.status_code, resp.text[:200])
        time.sleep(2 ** attempt)
    return None


def fetch_closed_issues(
    repo: str = DEFAULT_REPO,
    per_page: int = 100,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Page through closed issues (excluding PRs)."""
    issues: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        for page in range(1, max_pages + 1):
            data = _get(
                client, f"{API_ROOT}/repos/{repo}/issues",
                params={"state": "closed", "per_page": per_page, "page": page},
            )
            if not data:
                break
            # Filter out pull requests (the issues endpoint returns both)
            real_issues = [i for i in data if "pull_request" not in i]
            issues.extend(real_issues)
            if len(data) < per_page:
                break
    return issues


def find_fixing_pr(
    client: httpx.Client,
    repo: str,
    issue_number: int,
) -> int | None:
    """Try to find the PR that closed this issue.

    Uses GitHub's `events` API (look for closed events with commit refs)
    and `timeline` (look for cross-referenced PRs).
    """
    timeline = _get(
        client,
        f"{API_ROOT}/repos/{repo}/issues/{issue_number}/timeline",
        params={"per_page": 100},
    )
    if timeline is None:
        return None
    for event in timeline:
        # Newer GitHub events surface the closing PR directly
        if event.get("event") == "closed":
            src = event.get("commit_id") or ""
            if src:
                # commit-based close — not a PR; skip
                continue
        if event.get("event") == "cross-referenced":
            src = event.get("source", {})
            issue_obj = src.get("issue", {})
            if issue_obj.get("pull_request"):
                # state == merged is what we want
                pr_data = _get(
                    client,
                    f"{API_ROOT}/repos/{repo}/pulls/{issue_obj['number']}",
                )
                if pr_data and pr_data.get("merged"):
                    return issue_obj["number"]
    return None


def list_pr_files(
    client: httpx.Client,
    repo: str,
    pr_number: int,
    keep_pattern: str = "openlibrary/",
    suffix: str = ".py",
) -> list[str]:
    """Return PR-modified file paths matching the keep_pattern + suffix."""
    files: list[str] = []
    page = 1
    while True:
        data = _get(
            client,
            f"{API_ROOT}/repos/{repo}/pulls/{pr_number}/files",
            params={"per_page": 100, "page": page},
        )
        if not data:
            break
        for f in data:
            path = f.get("filename", "")
            if keep_pattern in path and path.endswith(suffix):
                files.append(path)
        if len(data) < 100:
            break
        page += 1
    return files


def build_ground_truth(
    repo: str = DEFAULT_REPO,
    out_path: str | Path = "output/issue_ground_truth.json",
    max_pages: int = 5,
    max_issues: int | None = 200,
) -> Path:
    """Build and save the issue-localization ground truth dataset."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    issues = fetch_closed_issues(repo=repo, max_pages=max_pages)
    if max_issues:
        issues = issues[:max_issues]
    logger.info("Fetched %d closed issues from %s", len(issues), repo)

    records: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        for i, issue in enumerate(issues, 1):
            num = issue.get("number")
            if num is None:
                continue
            pr_number = find_fixing_pr(client, repo, num)
            if not pr_number:
                continue
            changed = list_pr_files(client, repo, pr_number)
            if not changed:
                continue
            records.append({
                "issue_number": num,
                "title": issue.get("title", ""),
                "body": issue.get("body", "") or "",
                "pr_number": pr_number,
                "changed_files": changed,
                "url": issue.get("html_url", ""),
            })
            if i % 20 == 0:
                logger.info("Processed %d/%d issues; %d kept", i, len(issues), len(records))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %d ground-truth records to %s", len(records), out_path)
    return out_path
