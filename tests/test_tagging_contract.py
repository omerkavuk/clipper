"""A report may only be rendered when every exported candidate has exactly one valid tag.

Regression for the developer review: an empty tagging response used to count as
0 applied / 0 rejected / 0 unknown, pass the run skill's guard, and publish
"No coverage ... was found" although candidates existed.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper.models import Item, RunWindow
from clipper.pipeline import Caps, export_candidates
from clipper.project import load_project
from clipper.render import export_draft_input, render_report
from clipper.store import Store
from clipper.tags import TaggingIncomplete, apply_tags

EXAMPLE = Path(__file__).parent.parent / "examples" / "aurora-climate-lab"
SINCE = datetime(2026, 11, 7, tzinfo=timezone.utc)
UNTIL = datetime(2026, 11, 14, tzinfo=timezone.utc)


def item(n):
    return Item(source="fake", outlet=f"Outlet{n}", outlet_domain=f"o{n}.it",
                url=f"https://o{n}.it/a{n}", title=f"Green Schools news number {n} zebra{n}",
                published_at=datetime(2026, 11, 10, 8, n, tzinfo=timezone.utc),
                collected_at=UNTIL, matched_queries=["subject"])


def tag(id_, relevant=True, **kw):
    e = {"id": id_, "relevant": relevant, "type": "news", "tone": "neutral", "tone_reason": "",
         "naming": "explicit", "related_to": "other", "summary": "Summary."}
    e.update(kw)
    return e


@pytest.fixture
def env(tmp_path):
    p = load_project(EXAMPLE)
    p.folder = tmp_path / "proj"
    with Store(tmp_path / "s.db") as store:
        yield store, p, tmp_path


def exported(store, p, tmp_path, n_items):
    run = store.start_run(RunWindow(SINCE, UNTIL))
    ids = store.add_items(run, [item(n) for n in range(n_items)])
    store.finish_run(run, "ok", {"new": len(ids)})
    res = export_candidates(store, run, Caps(), tmp_path / "c.json", p, fetch=lambda it, o: it)
    store.merge_run_stats(run, {"candidate_ids": res["candidate_ids"]})
    return run, ids


def tags_file(tmp_path, entries, name="tags.json"):
    f = tmp_path / name
    f.write_text(json.dumps({"items": entries}), encoding="utf-8")
    return f


def draft(tmp_path, text="- News [[item:1]]"):
    f = tmp_path / "draft.md"
    f.write_text(text, encoding="utf-8")
    return f


def test_export_lists_the_candidate_ids_and_the_organisation_context(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 2)
    payload = json.loads((tmp / "c.json").read_text(encoding="utf-8"))
    assert sorted(i["id"] for i in payload["items"]) == sorted(ids)
    org = payload["organisation"]
    assert org["name"] == p.organisation and "Aurora Climate Lab" in org["names"]
    assert "Green Schools" in org["programmes"] and org["parent_names"] == p.parent_names


def test_empty_tagging_batch_cannot_publish_a_quiet_report(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 2)
    res = apply_tags(store, run, tags_file(tmp, []), p)
    assert res["complete"] is False and res["missing_ids"] == sorted(ids)
    assert store.tags(run) == {}  # nothing committed
    with pytest.raises(TaggingIncomplete):
        render_report(store, run, draft(tmp), p, tmp)
    with pytest.raises(TaggingIncomplete):
        export_draft_input(store, run, p, Caps(), tmp / "d.json")
    assert not list(tmp.glob("**/*.md")) or all("No coverage" not in f.read_text(encoding="utf-8")
                                                for f in tmp.glob("**/*.md"))


def test_incomplete_batch_commits_nothing_and_a_retry_completes_it(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 3)
    first = apply_tags(store, run, tags_file(tmp, [tag(ids[0]), tag(ids[1], tone="bad")]), p)
    assert first["complete"] is False and first["rejected"] == 1
    assert first["missing_ids"] == sorted(ids[1:]) and store.tags(run) == {}
    retry = apply_tags(store, run, tags_file(tmp, [tag(ids[1]), tag(ids[2])], "retry.json"), p)
    assert retry["complete"] is True and retry["missing_ids"] == []
    assert sorted(store.tags(run)) == sorted(ids)
    out = render_report(store, run, draft(tmp, f"- News [[item:{ids[0]}]]"), p, tmp)
    assert out["relevant"] == 3


def test_tags_for_items_that_were_not_exported_are_unknown(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 1)
    res = apply_tags(store, run, tags_file(tmp, [tag(ids[0]), tag(9999)]), p)
    assert res["complete"] is True and res["unknown_ids"] == 1


def test_tagging_before_export_is_refused(env):
    store, p, tmp = env
    run = store.start_run(RunWindow(SINCE, UNTIL))
    store.add_items(run, [item(1)])
    with pytest.raises(ValueError, match="export the candidates first"):
        apply_tags(store, run, tags_file(tmp, []), p)


def test_genuinely_empty_period_gives_a_quiet_report(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 0)
    assert ids == []
    res = apply_tags(store, run, tags_file(tmp, []), p)
    assert res["complete"] is True
    out = render_report(store, run, draft(tmp, ""), p, tmp)
    assert "No coverage of Aurora Climate Lab was found" in Path(out["report_md"]).read_text(encoding="utf-8")


def test_all_candidates_checked_and_none_relevant_gives_a_quiet_report(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 2)
    res = apply_tags(store, run, tags_file(tmp, [tag(i, relevant=False) for i in ids]), p)
    assert res["complete"] is True
    out = render_report(store, run, draft(tmp, ""), p, tmp)
    assert out["relevant"] == 0
    assert "No coverage" in Path(out["report_md"]).read_text(encoding="utf-8")


def test_failed_collection_cannot_be_rendered(env):
    store, p, tmp = env
    run = store.start_run(RunWindow(SINCE, UNTIL))
    store.finish_run(run, "failed", {"error": "every source failed"})
    with pytest.raises(TaggingIncomplete, match="collection failed"):
        render_report(store, run, draft(tmp, ""), p, tmp)


def test_new_export_resets_the_batch(env):
    store, p, tmp = env
    run, ids = exported(store, p, tmp, 2)
    apply_tags(store, run, tags_file(tmp, [tag(ids[0])]), p)
    res = export_candidates(store, run, Caps(), tmp / "c2.json", p, fetch=lambda it, o: it)
    store.merge_run_stats(run, {"candidate_ids": res["candidate_ids"], "tagged": False})
    again = apply_tags(store, run, tags_file(tmp, [tag(ids[1])], "t2.json"), p)
    assert again["complete"] is False and again["missing_ids"] == [ids[0]]
