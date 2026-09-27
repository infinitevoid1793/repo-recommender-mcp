"""Locations of the files the server reads and writes.

config.yaml, .env and state.duckdb live at the project root rather than inside
the package, so a clone keeps them beside the README. Set
REPO_RECOMMENDER_HOME to point them somewhere else.
"""

import os
from pathlib import Path


def project_root() -> Path:
    override = os.environ.get("REPO_RECOMMENDER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]
