"""File discovery for the Openlibrary codebase.

Walks the codebase directory, applying include/exclude glob patterns
to find all Python source files to analyze.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


def discover_python_files(
    root: str | Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[Path]:
    """Find all Python files in root matching include/exclude glob patterns.

    Args:
        root: Root directory of the codebase.
        include_patterns: Glob patterns to include. Default: ["**/*.py"].
        exclude_patterns: Glob patterns to exclude. Any path matching
                          one of these will be skipped.

    Returns:
        Sorted list of Path objects for all matching Python files.
    """
    root = Path(root)
    if include_patterns is None:
        include_patterns = ["**/*.py"]
    if exclude_patterns is None:
        exclude_patterns = [
            "**/tests/**",
            "**/vendor/**",
            "**/mocks/**",
            "**/conftest.py",
            "**/__pycache__/**",
            "**/.git/**",
            "**/node_modules/**",
        ]

    # Collect candidate files from include patterns
    candidates: set[Path] = set()
    for pattern in include_patterns:
        for p in root.glob(pattern):
            if p.is_file():
                candidates.add(p)

    # Apply exclusion filters
    results: list[Path] = []
    for fpath in candidates:
        rel = fpath.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude_patterns):
            continue
        results.append(fpath)

    return sorted(results)
