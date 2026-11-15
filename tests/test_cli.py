import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from clipper import cli
from clipper.collectors import SourceStatus
from clipper.models import Item


class Fake:
    name = "fake"

    def __init__(self, ok=True):
        self.ok = ok

    def collect(self, queries, window):
        when = window.until - timedelta(hours=2)
        items = [Item(source="fake", outlet=f"Outlet{n}", outlet_domain=f"o{n}.it",
                      url=f"https://o{n}.it/a{n}", title=f"Giornata Alfa numero {n} zebra{n}",
                      published_at=when, collected_at=when, snippet="Alfa snippet",
                      matched_queries=["subject"]) for n in range(3)]
        return items, [SourceStatus("fake", "t1", self.ok, len(items), None if self.ok else "boom",
                                    None)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "Sam's home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("clipper.pipeline._default_collectors", lambda p, progress=None: [Fake()])
    monkeypatch.setattr("clipper.estimate._default_collectors", lambda p, progress=None: [Fake()])
    monkeypatch.setattr("clipper.cli.fetch_full_text", lambda item, outlet: item)
    return tmp_path


def call(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    return code, json.loads(out)


def test_init_and_projects_with_apostrophe_path(env, capsys):
    folder = env / "Sam's Skills" / "Acme"
    code, out = call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", folder)
    assert code == 0 and out["ok"] is True and (folder / "project.yaml").is_file()
    code, out = call(capsys, "projects")
    assert code == 0 and out["projects"] == [{"name": "Acme", "folder": str(folder)}]
    code, out = call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", folder)
    assert code == 1 and "already exists" in out["error"] and "--replace" in out["error"]


def test_init_replace_backs_up_old_project(env, capsys):
    folder = env / "Acme"
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", folder)
    (folder / "sources.yaml").write_text("outlets: []\n", encoding="utf-8")
    (folder / "data.sqlite").write_bytes(b"old")
    (folder / "reports").mkdir()
    (folder / "reports" / "old.docx").write_bytes(b"r")
    code, out = call(capsys, "init", "--country", "FR", "--languages", "fr", "--name", "Acme",
                     "--folder", folder, "--replace")
    assert code == 0 and out["country"] == "France"
    backup = Path(out["backup"])
    assert backup.parent == folder.resolve() and backup.name.startswith("backup-")
    for name in ("project.yaml", "sources.yaml", "data.sqlite"):
        assert (backup / name).is_file()
    assert "country: IT" in (backup / "project.yaml").read_text(encoding="utf-8")
    assert "country: FR" in (folder / "project.yaml").read_text(encoding="utf-8")
    assert not (folder / "sources.yaml").exists() and not (folder / "data.sqlite").exists()
    assert (folder / "reports" / "old.docx").is_file()


def test_full_cycle_json_outputs(env, capsys):
    folder = env / "Sam's Skills" / "Acme"
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", folder)

    code, out = call(capsys, "status", "--project", "Acme")
    assert out == {"ok": True, "project": "Acme", "last_run": None, "failed_sources": [],
                   "next_due": "now"}

    code, out = call(capsys, "test-queries", "--project", "Acme")
    assert code == 0 and out["ok"] and out["caps"]["max_candidates"] >= 30 and out["queries"]
    assert "caps" not in out["estimate"]
    saved = (folder / "project.yaml").read_text(encoding="utf-8")
    assert "estimate:" in saved and "caps:" in saved

    code, out = call(capsys, "collect", "--project", str(folder))
    assert code == 0 and out["ok"] and out["items"] == 3 and out["status"] == "ok"
    assert out["sources_failed"] == 0
    run = out["run_id"]

    code, out = call(capsys, "status", "--project", "Acme")
    assert out["last_run"]["stats"]["reported"] is False  # collected, not yet rendered
    assert out["next_due"] == "now"

    cands = env / "out" / "cands.json"
    code, out = call(capsys, "candidates", "--project", "Acme", "--run", run, "--out", cands)
    assert code == 0 and out["ok"] and out["exported"] == 3
    items = json.loads(cands.read_text(encoding="utf-8"))["items"]

    tags = env / "tags.json"
    tags.write_text(json.dumps({"items": [
        {"id": it["id"], "relevant": True, "type": "news", "tone": "positive", "tone_reason": "x",
         "naming": "explicit", "related_to": "other", "summary": "Sintesi."} for it in items]}),
        encoding="utf-8")
    code, out = call(capsys, "apply-tags", "--project", "Acme", "--run", run, "--tags", tags)
    assert code == 0 and out["applied"] == 3

    draft_in = env / "draft_in.json"
    code, out = call(capsys, "draft-input", "--project", "Acme", "--run", run, "--out", draft_in)
    assert code == 0 and out["exported"] == 3

    draft = env / "draft.md"
    draft.write_text(f"## Summary\n\nCoverage was positive [#{items[0]['id']}].\n",
                     encoding="utf-8")
    code, out = call(capsys, "render", "--project", "Acme", "--run", run, "--draft", draft)
    assert code == 0 and out["ok"]
    for key in ("report_md", "report_docx", "run_note"):
        assert folder in Path(out[key]).parents
        assert Path(out[key]).is_file()
    note = Path(out["run_note"]).read_text(encoding="utf-8")
    assert "Tags applied: 3" in note and "Candidates: 3" in note

    code, out = call(capsys, "status", "--project", "Acme")
    assert out["last_run"]["id"] == run and out["last_run"]["status"] == "ok"
    assert out["last_run"]["stats"]["reported"] is True
    assert out["failed_sources"] == []
    assert out["next_due"] == (datetime.fromisoformat(out["last_run"]["until"])
                               + timedelta(days=7)).isoformat()


def test_failed_sources_in_status(env, capsys, monkeypatch):
    monkeypatch.setattr("clipper.pipeline._default_collectors",
                        lambda p, progress=None: [Fake(), Fake(ok=False)])
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", env / "Acme")
    code, out = call(capsys, "collect", "--project", "Acme")
    assert out["status"] == "partial" and out["sources_failed"] == 1
    code, out = call(capsys, "status", "--project", "Acme")
    assert out["failed_sources"] == ["t1"]


def test_failed_run_after_reported_run_keeps_next_due(env, capsys, monkeypatch):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", env / "Acme")
    code, out = call(capsys, "collect", "--project", "Acme")
    run = out["run_id"]
    draft = env / "draft.md"
    draft.write_text("Nothing.\n", encoding="utf-8")
    call(capsys, "render", "--project", "Acme", "--run", run, "--draft", draft)
    code, out = call(capsys, "status", "--project", "Acme")
    due = out["next_due"]
    monkeypatch.setattr("clipper.pipeline._default_collectors",
                        lambda p, progress=None: [Fake(ok=False)])
    code, out = call(capsys, "collect", "--project", "Acme")
    assert out["status"] == "failed"
    code, out = call(capsys, "status", "--project", "Acme")
    assert out["last_run"]["status"] == "failed" and out["next_due"] == due


def test_error_is_json(env, capsys):
    code, out = call(capsys, "collect", "--project", "nope")
    assert code == 1 and out["ok"] is False and "unknown project" in out["error"]


def test_naive_since_is_error_json(env, capsys):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", env / "Acme")
    code, out = call(capsys, "collect", "--project", "Acme", "--since", "2026-11-01T00:00:00")
    assert code == 1 and out["ok"] is False and "timezone" in out["error"]


def test_unknown_run_is_error_json(env, capsys):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", env / "Acme")
    code, out = call(capsys, "candidates", "--project", "Acme", "--run", 99, "--out",
                     env / "c.json")
    assert code == 1 and out["ok"] is False


def test_bad_arguments_still_json(env, capsys):
    code, out = call(capsys, "collect")
    assert code == 1 and out["ok"] is False


def test_non_json_values_do_not_break_output(env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "cmd_projects", lambda a: {"p": Path("x"), "d": datetime(2026, 1, 1)})
    code, out = call(capsys, "projects")
    assert code == 0 and out["ok"] is True and out["d"].startswith("2026-01-01")


def test_unexpected_exception_becomes_json(env, capsys, monkeypatch):
    def boom(a):
        raise RuntimeError("kaput")
    monkeypatch.setattr(cli, "cmd_projects", boom)
    code, out = call(capsys, "projects")
    assert code == 1 and out == {"ok": False, "error": "RuntimeError: kaput"}


def test_other_keyerror_is_not_reported_as_run_not_found(env, capsys, monkeypatch):
    def bad(a):
        raise KeyError("id")
    monkeypatch.setattr(cli, "cmd_projects", bad)
    code, out = call(capsys, "projects")
    assert code == 1 and out["error"] == "KeyError: 'id'"


def test_unknown_run_message(env, capsys):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", env / "Acme")
    code, out = call(capsys, "draft-input", "--project", "Acme", "--run", 99, "--out", env / "d.json")
    assert code == 1 and out["error"] == "run not found: 99"


def test_output_paths_are_absolute(env, capsys, monkeypatch):
    monkeypatch.chdir(env)
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", "Acme")
    code, out = call(capsys, "collect", "--project", "Acme")
    code, out = call(capsys, "candidates", "--project", "Acme", "--run", out["run_id"],
                     "--out", "rel.json")
    assert Path(out["out"]).is_absolute() and Path(out["out"]).is_file()


def test_progress_goes_to_stderr_and_stdout_stays_one_json_line(env, capsys, monkeypatch):
    class Chatty(Fake):
        def __init__(self, progress):
            super().__init__()
            self.progress = progress

        def collect(self, queries, window):
            self.progress("Fake: 1/1 queries")
            return super().collect(queries, window)

    monkeypatch.setattr("clipper.pipeline._default_collectors",
                        lambda p, progress=None: [Chatty(progress)])
    monkeypatch.setattr("clipper.estimate._default_collectors",
                        lambda p, progress=None: [Chatty(progress)])
    folder = env / "Acme"
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme", "--folder", folder)
    capsys.readouterr()
    for argv in (("collect", "--project", folder), ("test-queries", "--project", folder)):
        assert cli.main([str(a) for a in argv]) == 0
        cap = capsys.readouterr()
        assert cap.out.count("\n") == 1 and json.loads(cap.out)["ok"] is True
        assert "Fake: 1/1 queries" in cap.err


def test_init_country_without_outlet_list(env, capsys):
    code, out = call(capsys, "init", "--country", "fr", "--languages", "fr, en", "--name", "Acme",
                     "--folder", env / "Acme")
    assert code == 0 and out["country"] == "France" and out["outlet_list"] == 0
    text = (env / "Acme" / "project.yaml").read_text(encoding="utf-8")
    assert "country: FR" in text and "outlets_file: ''" in text and "- en" in text
    code, out = call(capsys, "collect", "--project", "Acme")
    assert code == 0


def test_init_rejects_unknown_country(env, capsys):
    code, out = call(capsys, "init", "--country", "Italy", "--languages", "it", "--name", "Acme",
                     "--folder", env / "Acme")
    assert code == 1 and "two-letter ISO code" in out["error"]


def test_stopped_collect_shows_as_failed_and_next_collect_works(env, capsys):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme",
         "--folder", env / "Acme")
    from clipper.models import RunWindow
    from clipper.store import Store
    with Store(env / "Acme" / "data.sqlite") as s:  # what a killed collect leaves behind
        s.start_run(RunWindow(datetime(2026, 11, 1).astimezone(), datetime(2026, 11, 8).astimezone()))
    code, out = call(capsys, "status", "--project", "Acme")
    assert out["last_run"]["status"] == "failed"
    code, out = call(capsys, "collect", "--project", "Acme")
    assert code == 0 and out["status"] == "ok"


def test_test_queries_keeps_estimate_when_every_source_failed(env, capsys, monkeypatch):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme",
         "--folder", env / "Acme")
    code, out = call(capsys, "test-queries", "--project", "Acme")
    assert out["estimate_saved"] is True
    saved = (env / "Acme" / "project.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr("clipper.estimate._default_collectors",
                        lambda p, progress=None: [Fake(ok=False)])
    monkeypatch.setattr(Fake, "collect", lambda self, q, w: ([], [SourceStatus("fake", "t", False, 0, "boom", None)]))
    code, out = call(capsys, "test-queries", "--project", "Acme")
    assert out["all_sources_failed"] is True and out["estimate"] is None
    assert (env / "Acme" / "project.yaml").read_text(encoding="utf-8") == saved


def test_sources_command_builds_project_list(env, capsys, monkeypatch):
    monkeypatch.setattr("clipper.sources.check_site", lambda d: (True, None))
    import clipper.sources as s
    monkeypatch.setattr(s, "build_sources",
                        lambda p, add=None, remove=None, tiers=None, _b=s.build_sources:
                        _b(p, add=add, remove=remove, tiers=tiers, check=lambda d: (True, None)))
    call(capsys, "init", "--country", "FR", "--languages", "fr", "--name", "Acme",
         "--folder", env / "Acme")
    code, out = call(capsys, "sources", "--project", "Acme")
    assert code == 0 and out["sources"] == 0
    add = env / "add.json"
    add.write_text('[{"name": "Le Monde", "domain": "lemonde.fr", "tier": "national"}]',
                   encoding="utf-8")
    code, out = call(capsys, "sources", "--project", "Acme", "--add", add)
    assert code == 0 and out["added"] == ["lemonde.fr"] and out["sources"] == 1


def test_empty_tags_file_cannot_render_a_quiet_report(env, capsys):
    call(capsys, "init", "--country", "IT", "--languages", "it", "--name", "Acme",
         "--folder", env / "Acme")
    code, out = call(capsys, "collect", "--project", "Acme")
    run = out["run_id"]
    call(capsys, "candidates", "--project", "Acme", "--run", run, "--out", env / "c.json")
    empty = env / "tags.json"
    empty.write_text('{"items": []}', encoding="utf-8")
    code, out = call(capsys, "apply-tags", "--project", "Acme", "--run", run, "--tags", empty)
    assert code == 0 and out["complete"] is False and len(out["missing_ids"]) == 3
    draft = env / "draft.md"
    draft.write_text("", encoding="utf-8")
    code, out = call(capsys, "render", "--project", "Acme", "--run", run, "--draft", draft)
    assert code == 1 and out["ok"] is False and "tagging is not complete" in out["error"]
    assert not list((env / "Acme").glob("*.md"))
    code, out = call(capsys, "status", "--project", "Acme")
    assert out["last_run"]["stats"]["reported"] is False
