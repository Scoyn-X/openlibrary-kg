#!/usr/bin/env python
"""Openlibrary Knowledge Graph — Main entry point.

This is a convenience wrapper around scripts/run_pipeline.py.

Usage:
    python main.py [--config config.yaml] [--sample N] [--skip-llm]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    from scripts.run_pipeline import main
    main()
