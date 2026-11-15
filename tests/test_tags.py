import json
from datetime import datetime, timezone

import pytest

from clipper.models import Item, RunWindow
from clipper.store import Store
from clipper.tags import apply_tags

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _item(n):
    return Item(source="rss", outlet="A", outlet_domain=f"a{n}.it", url=f"https://a{n}.it/x",
                title=f"Titolo {n}", published_at=T0, collected_at=T0, matched_queries=["q1"])


@pytest.fixture
def setup(tmp_path, example_project):
    with Store(tmp_path / "m.db") as s:
        run = s.start_run(RunWindow(since=T0, until=T0))
        ids = s.add_items(run, [_item(1), _item(2)])
        s.merge_run_stats(run, {"candidate_ids": ids})
        yield s, run, ids, example_project, tmp_path / "tags.json"


def entry(id_, **kw):
    e = {"id": id_, "relevant": True, "type": "news", "tone": "positive", "tone_reason": "ok",
         "naming": "explicit", "related_to": "other", "summary": "Sintesi."}
    e.update(kw)
    return e


def _counts(res):
    """The validator's counts: valid entries accepted, invalid rejected, ids not exported."""
    return {"applied": res["accepted"], "rejected": res["rejected"], "unknown_ids": res["unknown_ids"]}


def _seen(s, run):
    """Valid tags, whether still pending (batch incomplete) or committed (batch complete)."""
    return {**s.pending_tags(run), **s.tags(run)}


def write(path, entries):
    path.write_text(json.dumps({"items": entries}), encoding="utf-8")


def test_valid_tags_applied(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(ids[0]), entry(ids[1], tone="negative")])
    assert _counts(apply_tags(s, run, f, proj)) == {"applied": 2, "rejected": 0, "unknown_ids": 0}
    assert _seen(s, run)[ids[1]]["tone"] == "negative"


def test_unknown_label_rejected(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(ids[0], tone="very positive")])
    assert _counts(apply_tags(s, run, f, proj)) == {"applied": 0, "rejected": 1, "unknown_ids": 0}
    assert _seen(s, run) == {}


def test_unknown_id_counted(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(9999)])
    assert _counts(apply_tags(s, run, f, proj)) == {"applied": 0, "rejected": 0, "unknown_ids": 1}


def test_overlong_summary_truncated_not_rejected(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(ids[0], summary="x" * 400, tone_reason="y" * 300)])
    assert apply_tags(s, run, f, proj)["accepted"] == 1
    t = _seen(s, run)[ids[0]]
    assert len(t["summary"]) == 240 and len(t["tone_reason"]) == 140


def test_related_to_outside_programmes_becomes_other(setup):
    s, run, ids, proj, f = setup
    prog = proj.programmes[0]
    write(f, [entry(ids[0], related_to=prog.upper()), entry(ids[1], related_to="Nonsense")])
    apply_tags(s, run, f, proj)
    assert _seen(s, run)[ids[0]]["related_to"] == prog
    assert _seen(s, run)[ids[1]]["related_to"] == "other"


def test_null_related_to_other_and_extra_keys_dropped(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(ids[0], related_to=None, evil="x")])
    apply_tags(s, run, f, proj)
    t = _seen(s, run)[ids[0]]
    assert t["related_to"] == "other" and "evil" not in t


def test_missing_key_wrong_type_and_non_dict_rejected(setup):
    s, run, ids, proj, f = setup
    bad = entry(ids[0])
    del bad["summary"]
    write(f, [bad, entry(ids[1], relevant="yes"), "junk", entry(ids[1], summary=5)])
    assert _counts(apply_tags(s, run, f, proj)) == {"applied": 0, "rejected": 4, "unknown_ids": 0}


def test_duplicate_id_last_valid_wins(setup):
    s, run, ids, proj, f = setup
    write(f, [entry(ids[0], tone="negative"), entry(ids[0], tone="neutral"), entry(ids[0], tone="bad")])
    assert _counts(apply_tags(s, run, f, proj)) == {"applied": 1, "rejected": 1, "unknown_ids": 0}
    assert _seen(s, run)[ids[0]]["tone"] == "neutral"


def test_id_from_other_run_unknown(setup):
    s, run, ids, proj, f = setup
    run2 = s.start_run(RunWindow(since=T0, until=T0))
    s.merge_run_stats(run2, {"candidate_ids": []})
    write(f, [entry(ids[0])])
    assert apply_tags(s, run2, f, proj)["unknown_ids"] == 1


@pytest.mark.parametrize("content", [None, "not json", '{"a": 1}', '{"items": 3}', "[]"])
def test_bad_file_raises(setup, content):
    s, run, ids, proj, f = setup
    if content is not None:
        f.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        apply_tags(s, run, f, proj)
