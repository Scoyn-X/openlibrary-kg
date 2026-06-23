"""Disk-based cache for LLM and embedding results.

Allows resumability: if a phase crashes, cached responses are reused on re-run.
Cache keys are MD5 hashes of the input.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class DiskCache:
    """Simple JSON-file-based persistent cache."""

    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        """Convert a string key to a file path."""
        safe = hashlib.md5(key.encode()).hexdigest()  # noqa: S324
        return self.cache_dir / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        """Return cached value or None."""
        path = self._key_path(key)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    def set(self, key: str, value: Any) -> None:
        """Store a value in the cache."""
        with open(self._key_path(key), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)

    def has(self, key: str) -> bool:
        return self._key_path(key).exists()

    def clear(self) -> None:
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
