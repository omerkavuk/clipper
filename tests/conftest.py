from pathlib import Path

import pytest

from clipper.project import load_project

EXAMPLE = Path(__file__).parent.parent / "examples" / "aurora-climate-lab"


@pytest.fixture
def example_project():
    return load_project(EXAMPLE)


@pytest.fixture(autouse=True)
def no_real_brave_key(monkeypatch):
    """Tests never use the user's real Brave key (or make live Brave calls)."""
    monkeypatch.setattr("clipper.pipeline.brave_key", lambda: None)
    monkeypatch.setattr("clipper.estimate.brave_key", lambda: None)
