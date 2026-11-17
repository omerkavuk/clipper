import json
import re
from pathlib import Path

import yaml

from clipper.cli import build_parser
from clipper.tags import NAMING, TONES, TYPES

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ["setup", "run", "status", "edit"]


def test_plugin_json():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "clipper"
    assert data["version"] == "1.3.0"
    assert data["description"]
    assert data["author"]["name"] == "Omer"
    assert data["license"] == "MIT"


def test_skill_frontmatter():
    for name in SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        assert m, name
        meta = yaml.safe_load(m.group(1))
        assert meta["name"] == name
        assert meta["description"].strip()
        assert "Agent Reach" in meta["description"]
        assert "--directory" in text


def test_run_skill_lists_every_tag_label():
    text = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    for label in TYPES | TONES | NAMING:
        assert f'"{label}"' in text, label


def test_every_mm_command_in_skills_exists():
    sub = next(a for a in build_parser()._actions if hasattr(a, "choices") and a.choices)
    commands = set(sub.choices)
    used = set()
    for name in SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        used |= set(re.findall(r"`clipper ([a-z][a-z-]*)", text))
        used |= set(re.findall(r"mm (?!<command>)([a-z][a-z-]+) --", text))
    assert used
    assert used <= commands, used - commands


def test_skills_use_native_plugin_path_as_shown():
    for name in SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "exactly as shown" in text, name
        assert "/c/Users" in text and "Never convert" in text, name


def _setup_text():
    folder = ROOT / "skills" / "setup"
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted(folder.glob("*.md")))


def test_setup_describes_both_schedule_paths():
    text = _setup_text()
    assert "schtasks /Create" in text
    assert "launchctl bootstrap" in text
    assert "Task 14" not in text


def test_scheduling_is_gated_on_explicit_yes():
    for name in ["setup", "edit", "status"]:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "explicit yes" in text, name


def test_run_skill_resumes_unreported_run():
    text = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    first_step = text.index("0. First run `clipper status")
    assert first_step < text.index("1. `clipper collect")
    assert "last_run.stats.reported" in text and "continue from step 2" in text


def test_setup_reports_english_only_and_windows_quoting():
    text = _setup_text()
    assert "English for now" in text and "`report_language` to `en`" in text
    assert "CommandLineToArgvW" in text and "UNVERIFIED" in text
    spike = (ROOT / "docs" / "spikes" / "scheduled-run.md").read_text(encoding="utf-8")
    assert "falls back automatically" not in spike and "explicit yes" in spike
    assert 'Bash(python -m uv run --directory "<plugin root>"' not in spike


def test_setup_is_a_wizard_with_clickable_questions():
    text = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    assert "question tool" in text and "Never comment between screens" in text
    for n in range(16):
        assert f"**Screen {n}:" in text, n
    assert "Social media" in text and "clipper sources" in text
    assert (ROOT / "examples" / "sample-report.md").is_file()
