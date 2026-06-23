"""JSON and file I/O utilities with Windows-safe path handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_path(path: str) -> str:
    """Convert a path to forward-slash format, resolving Windows backslashes."""
    return Path(path).as_posix()


def read_json(filepath: str | Path) -> Any:
    """Read a JSON file with UTF-8 encoding."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def write_json(filepath: str | Path, data: Any, pretty: bool = True) -> None:
    """Write data as JSON with UTF-8 encoding."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        else:
            json.dump(data, f, ensure_ascii=False, default=str)


def ensure_dir(dirpath: str | Path) -> Path:
    """Create directory if it doesn't exist, return Path."""
    path = Path(dirpath)
    path.mkdir(parents=True, exist_ok=True)
    return path
