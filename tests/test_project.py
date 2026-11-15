from pathlib import Path

import pytest
import yaml

from clipper.project import (
    Project,
    ProjectError,
    find_project,
    list_projects,
    load_project,
    register_project,
    save_project,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "aurora-climate-lab"


def make_project(folder: Path, **overrides) -> Project:
    data = dict(
        name="Aurora Climate Lab",
        organisation="Aurora",
        subject=["Aurora Climate Lab", "Laboratorio Clima Aurora"],
        contexts={"climate": ["clima", "climate"]},
        competitors={"FAO": ["FAO"]},
        exclude_terms=["internship"],
        exclude_domains=["aurora.example"],
        programmes=["Green Schools"],
        spokespeople=["Giulia Marchetti"],
        country="IT",
        languages=["it", "en"],
        frequency="weekly",
        report_language="en",
        readers="Centre staff",
        outlets_file="it",
        folder=folder,
    )
    data.update(overrides)
    return Project(**data)


def test_roundtrip(tmp_path):
    p = make_project(tmp_path / "proj", estimate={"items": 40}, caps={"max_items": 100})
    save_project(p)
    assert load_project(p.folder) == p


def test_roundtrip_without_optional_fields(tmp_path):
    p = make_project(tmp_path / "proj")
    save_project(p)
    raw = yaml.safe_load((p.folder / "project.yaml").read_text(encoding="utf-8"))
    assert "estimate" not in raw and "caps" not in raw
    assert load_project(p.folder) == p


def test_missing_subject_raises(tmp_path):
    p = make_project(tmp_path / "proj", subject=[])
    save_project(p)
    with pytest.raises(ProjectError, match="subject"):
        load_project(p.folder)


def test_bad_frequency_raises(tmp_path):
    p = make_project(tmp_path / "proj", frequency="hourly")
    save_project(p)
    with pytest.raises(ProjectError, match="frequency"):
        load_project(p.folder)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ProjectError):
        load_project(tmp_path / "nothing")


def test_registry_with_apostrophe_and_spaces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    folder = tmp_path / "Sam's Skills" / "Media Monitor" / "Aurora Climate Lab"
    p = make_project(folder)
    save_project(p)
    register_project(p)
    assert find_project("Aurora Climate Lab") == folder
    assert list_projects() == {"Aurora Climate Lab": folder}
    assert load_project(folder).name == "Aurora Climate Lab"


def test_find_unknown_project_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(ProjectError):
        find_project("Nope")


def test_example_project_loads():
    p = load_project(EXAMPLE)
    assert p.name == "Aurora Climate Lab"
    assert p.organisation == "Aurora Climate Lab"
    assert "Laboratorio Clima Aurora" in p.subject
    assert p.programmes == ["Green Schools", "Solar Villages", "Clean Air Week"]
    assert p.contexts == {"initiatives": ["VELA", "SV4"]}
    assert sum(1 for t in p.subject if len(t.split()) <= 5) >= 2
    assert p.frequency == "weekly"
    assert p.outlets_file == "it"


def test_unknown_country_or_no_language_is_rejected(tmp_path):
    import yaml
    from clipper.project import ProjectError, load_project
    base = yaml.safe_load(open(EXAMPLE / "project.yaml", encoding="utf-8"))
    for change, msg in [({"country": "XX"}, "country"), ({"languages": []}, "languages")]:
        (tmp_path / "project.yaml").write_text(yaml.safe_dump({**base, **change}), encoding="utf-8")
        with pytest.raises(ProjectError, match=msg):
            load_project(tmp_path)
