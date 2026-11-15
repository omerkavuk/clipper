from __future__ import annotations

import os
from pathlib import Path


def _user_home() -> Path:
    env = os.environ.get("HOME")
    return Path(env) if env else Path.home()


def home() -> Path:
    """Return ~/.clipper, creating it on demand."""
    p = _user_home() / ".clipper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_projects_dir() -> Path:
    return _user_home() / "Documents" / "Clipper"
