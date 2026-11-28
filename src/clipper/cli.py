"""The `clipper` command line. Each command prints exactly one JSON object."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from clipper import estimate as est
from clipper import countries, paths, pipeline
from clipper.collectors.outlet_feeds import load_outlets
from clipper import sources as src
from clipper import project as proj
from clipper.fulltext import fetch_full_text, google_link_stats, preresolve
from clipper.models import Project, RunWindow
from clipper.render import export_draft_input, render_report, render_run_note
from clipper.store import Store, _iso
from clipper.tags import apply_tags

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


class RunNotFound(LookupError):
    pass


def _check_run(store: Store, run_id: int):
    try:
        return store.run(run_id)
    except KeyError:
        raise RunNotFound(f"run not found: {run_id}") from None


def _abs(path) -> str:
    return str(Path(path).resolve())


def _load(value: str) -> Project:
    folder = Path(value)
    if folder.is_dir() and (folder / "project.yaml").is_file():
        return proj.load_project(folder)
    return proj.load_project(proj.find_project(value))


def _store(p: Project) -> Store:
    return Store(p.folder / "data.sqlite")


def _iso_arg(value: str | None, flag: str) -> datetime | None:
    if value is None:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{flag} must be an ISO date-time, got {value!r}") from None
    if d.tzinfo is None:
        raise ValueError(f"{flag} must include a timezone, e.g. 2026-11-14T09:00:00+00:00")
    return d


def _progress(message: str) -> None:
    """Progress goes to stderr; stdout carries only the final JSON object."""
    print(message, file=sys.stderr, flush=True)


def cmd_init(a) -> dict:
    folder = Path(a.folder) if a.folder else paths.default_projects_dir() / a.name
    backup = None
    if (folder / "project.yaml").exists():
        if not a.replace:
            raise proj.ProjectError(
                f"a project already exists in {folder}: edit its project.yaml to update it, "
                "or run init again with --replace to start fresh (the old files are kept in a backup folder)")
        backup = _back_up_project(folder)
    country = a.country.strip().upper()
    if not countries.is_known(country):
        raise proj.ProjectError(
            f"--country must be a two-letter ISO code such as FR or KE, got {a.country!r}")
    languages = [s.strip().lower() for s in a.languages.split(",") if s.strip()]
    if not languages:
        raise proj.ProjectError("--languages must list at least one language code, such as fr")
    outlets_file = countries.outlet_list(country)
    p = Project(
        name=a.name, organisation=a.name, subject=[a.name], contexts={}, competitors={},
        exclude_terms=[], exclude_domains=[], programmes=[], spokespeople=[], country=country,
        languages=languages, frequency="weekly", report_language="en", readers="",
        outlets_file=outlets_file, folder=folder)
    proj.save_project(p)
    proj.register_project(p)
    out = {"project": p.name, "folder": _abs(folder), "project_file": _abs(folder / "project.yaml"),
           "country": countries.name(country),
           "outlet_list": len(load_outlets(outlets_file))}
    if backup:
        out["backup"] = _abs(backup)
    return out


BACKED_UP = ("project.yaml", "sources.yaml", "data.sqlite")


def _back_up_project(folder: Path) -> Path:
    """Move the old plan, source list and history aside; reports and run notes stay where they are."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = folder / f"backup-{stamp}"
    backup.mkdir()
    for name in BACKED_UP:
        if (folder / name).exists():
            (folder / name).replace(backup / name)
    return backup


def cmd_sources(a) -> dict:
    p = _load(a.project)
    add = None
    if a.add:
        try:
            raw = yaml.safe_load(Path(a.add).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise src.SourcesError(f"cannot read --add file: {e}") from None
        add = raw.get("outlets", raw) if isinstance(raw, dict) else raw
        if not isinstance(add, list):
            raise src.SourcesError("--add file must hold a list of {name, domain, tier}")
    remove = [d for d in (a.remove or "").split(",") if d.strip()]
    tiers = [t.strip() for t in (a.tiers or "").split(",") if t.strip()]
    if not (add or remove or tiers):
        return {"project": p.name, **src.summarize(src.project_outlets(p))}
    return {"project": p.name, **src.build_sources(p, add=add, remove=remove, tiers=tiers or None)}


def cmd_social_setup(a) -> dict:
    from clipper.collectors import social
    p = _load(a.project)
    return {"project": p.name, **social.setup_platforms(p.social),
            "check": social.check_platforms(p.social)}


def cmd_social_check(a) -> dict:
    from clipper.collectors import social
    p = _load(a.project)
    return {"project": p.name, "platforms": social.check_platforms(p.social)}


def cmd_social_login(a) -> dict:
    from clipper.collectors import social
    if a.platform not in social.ROUTES:
        raise ValueError(f"unknown platform {a.platform!r}")
    return social.login(a.platform)


def cmd_compare(a) -> dict:
    from clipper import compare as cmp
    from clipper.collectors.brave import BraveNewsCollector, brave_key
    from clipper.collectors.google_news import GoogleNewsCollector
    p = _load(a.project)
    since, until = _iso_arg(a.since, "--since"), _iso_arg(a.until, "--until")
    if not (since and until):
        raise ValueError("--since and --until are required")
    names = [r.strip() for r in (a.routes or "google_news,brave").split(",") if r.strip()]
    routes = {}
    for name in names:
        if name == "google_news":
            # Same country and languages as Brave; no abroad edition, which Brave has no match for.
            routes[name] = GoogleNewsCollector(p.country, p.languages, p.exclude_domains,
                                               progress=_progress)
        elif name == "brave":
            key = brave_key()
            if not key:
                raise ValueError("no Brave key: save it in ~/.clipper/brave-key.txt")
            routes[name] = BraveNewsCollector(p.country, p.languages, key, progress=_progress)
        else:
            raise ValueError(f"unknown route {name!r}; use google_news, brave")
    return cmp.compare(p, RunWindow(since, until), routes, Path(a.out))


def cmd_compare_score(a) -> dict:
    from clipper import compare as cmp
    return cmp.score(_load(a.project), Path(a.articles), Path(a.tags))


def cmd_projects(a) -> dict:
    return {"projects": [{"name": n, "folder": str(f)}
                         for n, f in sorted(proj.list_projects().items())]}


def cmd_test_queries(a) -> dict:
    p = _load(a.project)
    result = est.test_queries(p, days=a.days, progress=_progress)
    if result["all_sources_failed"]:
        # Nothing answered: any figure would be invented. Keep the last good estimate.
        return {"all_sources_failed": True, "sources_failed": result["sources_failed"],
                "queries": result["queries"], "estimate": None, "estimate_saved": False}
    estimate = est.estimate(p, result)
    caps = estimate.pop("caps")
    p.estimate, p.caps = estimate, caps
    proj.save_project(p)
    return {"all_sources_failed": False, "estimate_saved": True,"queries": result["queries"], "terms": result["terms"],
            "total_unique_hits": result["total_unique_hits"],
            "own_site_share": result["own_site_share"],
            "sources_failed": result["sources_failed"],
            "warnings": {q["id"]: q["warnings"] for q in result["queries"] if q["warnings"]},
            "estimate": estimate, "caps": caps}


def cmd_collect(a) -> dict:
    p = _load(a.project)
    since, until = _iso_arg(a.since, "--since"), _iso_arg(a.until, "--until")
    with _store(p) as store:
        window = pipeline.resolve_window(store, p.frequency, datetime.now(timezone.utc),
                                         since, until)
        skip = tuple(s.strip() for s in (a.skip or "").split(",") if s.strip())
        run_id = pipeline.collect(p, store, window, progress=_progress, skip=skip)
        run = store.run(run_id)
    return {"run_id": run_id, "status": run.status, "items": run.stats.get("new", 0),
            "sources_failed": run.stats.get("sources_failed", 0)}


def cmd_candidates(a) -> dict:
    p = _load(a.project)
    with _store(p) as store:
        _check_run(store, a.run)
        res = pipeline.export_candidates(store, a.run, pipeline.load_caps(p), Path(a.out), p,
                                         fetch=fetch_full_text, resolve_links=preresolve)
        res["google_links"] = google_link_stats()
        store.merge_run_stats(a.run, {"candidates": res["exported"],
                                      "truncated": res["truncated"],
                                      "fulltext_attempted": res["fulltext_attempted"],
                                      "fulltext_fetched": res["fulltext_fetched"],
                                      "google_links": res["google_links"]})
    res.pop("candidate_ids")
    return {"run_id": a.run, "out": _abs(a.out), **res}


def cmd_apply_tags(a) -> dict:
    p = _load(a.project)
    with _store(p) as store:
        _check_run(store, a.run)
        res = apply_tags(store, a.run, Path(a.tags), p)
        store.merge_run_stats(a.run, {k: res[k] for k in ("applied", "rejected", "unknown_ids")})
    return {"run_id": a.run, **res}


def cmd_draft_input(a) -> dict:
    p = _load(a.project)
    with _store(p) as store:
        _check_run(store, a.run)
        res = export_draft_input(store, a.run, p, pipeline.load_caps(p), Path(a.out))
    return {"run_id": a.run, "out": _abs(a.out), **res}


def cmd_render(a) -> dict:
    p = _load(a.project)
    with _store(p) as store:
        run = _check_run(store, a.run)
        res = render_report(store, a.run, Path(a.draft), p, p.folder)
        note = render_run_note(store, a.run, p, {**run.stats, **res}, p.folder)
        store.mark_reported(a.run)  # only a rendered run counts as covered
    return {"report_md": _abs(res["report_md"]), "report_docx": _abs(res["report_docx"]),
            "run_note": _abs(note), "removed_refs": res["removed_refs"]}


def cmd_status(a) -> dict:
    p = _load(a.project)
    with _store(p) as store:
        store.close_interrupted_runs()  # only runs whose collect process is gone
        last = store.last_run()
        if last is None:
            return {"project": p.name, "last_run": None, "failed_sources": [],
                    "next_due": "now"}
        failed = [s.target for s in store.source_statuses(last.id) if not s.ok]
        reported = store.last_reported_run()
    stats = {**last.stats, "reported": bool(last.stats.get("reported"))}
    next_due = ("now" if reported is None
                else _iso(reported.until + timedelta(days=PERIOD_DAYS[p.frequency])))
    return {"project": p.name,
            "last_run": {"id": last.id, "status": last.status, "since": _iso(last.since),
                         "until": _iso(last.until), "stats": stats},
            "failed_sources": failed,
            "next_due": next_due}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clipper", description="Clipper")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name, fn, project=True, run=False):
        sp = sub.add_parser(name)
        sp.set_defaults(fn=fn)
        if project:
            sp.add_argument("--project", required=True)
        if run:
            sp.add_argument("--run", type=int, required=True)
        return sp

    sp = add("init", cmd_init, project=False)
    sp.add_argument("--name", required=True)
    sp.add_argument("--country", required=True, help="ISO 3166-1 alpha-2 code, e.g. FR")
    sp.add_argument("--languages", required=True, help="comma-separated ISO 639-1 codes, e.g. fr,en")
    sp.add_argument("--folder")
    sp.add_argument("--replace", action="store_true",
                    help="start fresh: move the existing project's files to a backup folder")
    add("projects", cmd_projects, project=False)
    sp = add("compare", cmd_compare)
    sp.add_argument("--since", required=True)
    sp.add_argument("--until", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--routes", help="comma-separated: google_news,brave (default both)")
    sp = add("compare-score", cmd_compare_score)
    sp.add_argument("--articles", required=True)
    sp.add_argument("--tags", required=True)
    add("social-setup", cmd_social_setup)
    add("social-check", cmd_social_check)
    sp = add("social-login", cmd_social_login, project=False)
    sp.add_argument("--platform", required=True)
    sp = add("sources", cmd_sources)
    sp.add_argument("--add", help="YAML or JSON file: a list of {name, domain, tier}")
    sp.add_argument("--remove", help="comma-separated domains")
    sp.add_argument("--tiers", help="comma-separated source types to keep on the first build")
    sp = add("test-queries", cmd_test_queries)
    sp.add_argument("--days", type=int, default=7)
    sp = add("collect", cmd_collect)
    sp.add_argument("--skip", help="sources to switch off for this run, e.g. google_news")
    sp.add_argument("--since")
    sp.add_argument("--until")
    sp = add("candidates", cmd_candidates, run=True)
    sp.add_argument("--out", required=True)
    sp = add("apply-tags", cmd_apply_tags, run=True)
    sp.add_argument("--tags", required=True)
    sp = add("draft-input", cmd_draft_input, run=True)
    sp.add_argument("--out", required=True)
    sp = add("render", cmd_render, run=True)
    sp.add_argument("--draft", required=True)
    add("status", cmd_status)
    return ap


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:  # argparse already printed usage to stderr
        if e.code == 0:
            return 0
        _emit({"ok": False, "error": "invalid command line arguments (usage is on stderr)"})
        return 1
    try:
        line = json.dumps({"ok": True, **args.fn(args)}, ensure_ascii=False, default=str)
    except RunNotFound as e:
        return _fail(str(e))
    except (proj.ProjectError, ValueError, FileNotFoundError, sqlite3.Error) as e:
        return _fail(str(e))
    except Exception as e:  # never a traceback, KeyError included
        return _fail(f"{type(e).__name__}: {e}")
    sys.stdout.write(line + "\n")
    return 0


def _fail(message: str) -> int:
    _emit({"ok": False, "error": message})
    return 1


if __name__ == "__main__":
    sys.exit(main())
