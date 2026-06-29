"""Architecture-aware module index for issue localization.

Loads module_cards.json and provides:
  - file → layer mapping (e.g. "core/models.py" → "domain_model")
  - file → key_concepts mapping
  - key_concept → files inverted index
  - layer-aware ranking boost for files
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("openlibrary_kg.downstream.module_index")


class ModuleIndex:
    """Architecture-aware view of the codebase.

    Maps every Python file to its architectural role (layer), domain
    responsibility, and key concepts.  Used by the issue localizer to
    give ranking boosts to files whose role matches the issue type.
    """

    def __init__(self, cards_path: str | Path = "output/module_cards.json"):
        cards_path = Path(cards_path)
        if not cards_path.exists():
            logger.warning("module_cards.json not found — module index disabled")
            self._empty = True
            return

        self._empty = False
        data = json.loads(cards_path.read_text(encoding="utf-8"))

        # file_path → {layer, responsibility, key_concepts}
        self._file_info: dict[str, dict] = {}
        # key_concept → set of file_paths (inverted index)
        self._concept_to_files: dict[str, set[str]] = defaultdict(set)
        # layer → set of file_paths
        self._layer_to_files: dict[str, set[str]] = defaultdict(set)

        for card in data.get("cards", []):
            fp = card["file_path"]
            info = {
                "layer": card.get("layer", "utility"),
                "responsibility": card.get("responsibility", ""),
                "key_concepts": card.get("key_concepts", []),
            }
            self._file_info[fp] = info
            self._layer_to_files[info["layer"]].add(fp)
            for kc in info["key_concepts"]:
                self._concept_to_files[kc.lower()].add(fp)

        logger.info(
            "ModuleIndex: %d files across %d layers",
            len(self._file_info),
            len(self._layer_to_files),
        )

    def get_layer(self, file_path: str) -> str:
        """Return the architectural layer for a file."""
        if self._empty:
            return "unknown"
        # Try exact match first, then suffix match
        if file_path in self._file_info:
            return self._file_info[file_path]["layer"]
        for fp in self._file_info:
            if file_path.endswith(fp) or fp.endswith(file_path):
                return self._file_info[fp]["layer"]
        return "unknown"

    def get_key_concepts(self, file_path: str) -> list[str]:
        """Return the key domain concepts for a file."""
        if self._empty:
            return []
        if file_path in self._file_info:
            return self._file_info[file_path].get("key_concepts", [])
        for fp in self._file_info:
            if file_path.endswith(fp) or fp.endswith(file_path):
                return self._file_info[fp].get("key_concepts", [])
        return []

    def files_matching_concept(self, concept: str) -> set[str]:
        """Find files whose key_concepts match a given term."""
        if self._empty:
            return set()
        return self._concept_to_files.get(concept.lower(), set())

    def layer_files(self, layer: str) -> set[str]:
        """Return all files in a given architectural layer."""
        if self._empty:
            return set()
        return self._layer_to_files.get(layer, set())

    def compute_architecture_boost(
        self,
        file_path: str,
        issue_concepts: set[str],
        issue_layers: set[str] | None = None,
    ) -> float:
        """Compute a ranking boost for a file based on its architectural fit.

        Boost factors:
          - File's key_concepts overlap with issue concepts → +0.15 per match
          - File is in a layer relevant to this issue type → +0.10
        """
        if self._empty:
            return 0.0

        boost = 0.0
        kcs = self.get_key_concepts(file_path)

        for kc in kcs:
            for ic in issue_concepts:
                if ic.lower() in kc.lower() or kc.lower() in ic.lower():
                    boost += 0.15
                    break

        # Layer bonus (cap at 0.10)
        if issue_layers:
            file_layer = self.get_layer(file_path)
            if file_layer in issue_layers:
                boost += 0.10

        return min(boost, 0.5)  # cap at 0.5

    @property
    def enabled(self) -> bool:
        return not self._empty

    def __repr__(self) -> str:
        if self._empty:
            return "ModuleIndex(empty)"
        return (
            f"ModuleIndex(files={len(self._file_info)}, "
            f"layers={len(self._layer_to_files)})"
        )
