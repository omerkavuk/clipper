from datetime import datetime, timedelta, timezone

import pytest

from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.store import Store

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def item(title="Titolo", url="https://a.it/x", domain="a.it", **kw):
    return Item(source="rss", outlet="A", outlet_domain=domain, url=url, title=title,
                published_at=T0, collected_at=T0, matched_queries=["q1"], **kw)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "m.db") as s:
        yield s


def window(days=0):
    return RunWindow(since=T0 + timedelta(days=days), until=T0 + timedelta(days=days + 1))


def test_add_items_skips_duplicates(store):
    run = store.start_run(window())
    ids = store.add_items(run, [item(), item()])
    assert len(ids) == 1
    assert len(store.items(run)) == 1


def reported(store, run_id, status="ok"):
    store.finish_run(run_id, status, {})
    store.mark_reported(run_id)


def test_duplicates_skipped_across_reported_overlapping_runs(store):
    r1 = store.start_run(window())
    store.add_items(r1, [item()])
    reported(store, r1)
    r2 = store.start_run(window())  # same window again
    assert store.add_items(r2, [item(url="https://www.a.it/x/")]) == []
    assert store.items(r2) == []


def test_rerunning_unreported_window_still_stores_items(store):
    r1 = store.start_run(window())
    store.add_items(r1, [item()])
    store.finish_run(r1, "ok", {})  # collected, never rendered
    r2 = store.start_run(window())
    assert len(store.add_items(r2, [item()])) == 1
    assert len(store.items(r2)) == 1


def test_rerunning_reported_overlapping_window_dedupes(store):
    r1 = store.start_run(RunWindow(since=T0 - timedelta(days=1), until=T0 + timedelta(hours=12)))
    store.add_items(r1, [item()])
    reported(store, r1, "partial")
    r2 = store.start_run(window())  # overlaps r1 by 12 hours
    assert store.add_items(r2, [item()]) == []


def test_reported_disjoint_window_does_not_dedupe(store):
    r1 = store.start_run(window(-1))
    store.add_items(r1, [item()])
    reported(store, r1)
    r2 = store.start_run(window())
    assert len(store.add_items(r2, [item()])) == 1


def test_unrendered_run_does_not_advance_last_successful_until(store):
    r1 = store.start_run(window(0))
    reported(store, r1)
    r2 = store.start_run(window(1))
    store.finish_run(r2, "ok", {})  # not rendered
    assert store.last_successful_until() == window(0).until
    assert store.previous_run(r2 + 1).id == r1
    store.mark_reported(r2)
    assert store.last_successful_until() == window(1).until
    assert store.previous_run(r2 + 1).id == r2


def test_item_roundtrip(store):
    run = store.start_run(window())
    it = item(snippet="s", full_text="ft", language="it", canonical_url="https://a.it/c", subscribers_only=True)
    (i,) = store.add_items(run, [it])
    got = store.get_item(i)
    assert got.id == i and got.published_at == T0 and got.matched_queries == ["q1"]
    assert got.full_text == "ft" and got.subscribers_only is True and got.canonical_url == "https://a.it/c"
    assert store.get_item(999) is None


def test_last_successful_until(store):
    assert store.last_successful_until() is None
    r1 = store.start_run(window(0))
    reported(store, r1)
    r2 = store.start_run(window(1))
    store.finish_run(r2, "failed", {"err": 1})
    store.mark_reported(r2)  # a failed run never counts, even if rendered
    assert store.last_successful_until() == window(0).until


def test_run_record(store):
    r = store.start_run(window())
    assert store.run(r).status == "running"
    store.finish_run(r, "partial", {"n": 2})
    rec = store.run(r)
    assert (rec.status, rec.stats, rec.since, rec.until) == ("partial", {"n": 2}, window().since, window().until)


def test_tags_and_stories(store):
    run = store.start_run(window())
    a, b, c = store.add_items(run, [item("A", "https://a.it/1"), item("B", "https://a.it/2"), item("C", "https://a.it/3")])
    store.set_tags(a, {"sentiment": "pos", "themes": ["x"]})
    assert store.tags(run) == {a: {"sentiment": "pos", "themes": ["x"]}}
    store.set_story(a, a)
    store.set_story(b, a)
    store.set_story(c, c)
    assert store.stories(run) == {a: [a, b], c: [c]}


def test_source_status(store):
    run = store.start_run(window())
    st = SourceStatus("rss", "a.it", False, 0, "boom", T0)
    store.add_source_status(run, st)
    assert store.source_statuses(run) == [st]


def test_last_successful_until_counts_partial_ignores_failed_and_running(store):
    r1 = store.start_run(window(0))
    reported(store, r1)
    r2 = store.start_run(window(1))
    reported(store, r2, "partial")
    assert store.last_successful_until() == window(1).until
    r3 = store.start_run(window(2))
    store.finish_run(r3, "failed", {})
    store.start_run(window(3))  # still running
    assert store.last_successful_until() == window(1).until


def test_google_copy_skipped_in_batch_and_in_later_run(store):
    r1 = store.start_run(window())
    orig = item("Clima, premiati 40 studenti", "https://a.it/clima")
    google = item("Clima, premiati 40 studenti", "https://news.google.com/rss/articles/abc")
    assert len(store.add_items(r1, [orig, google])) == 1
    reported(store, r1)
    r2 = store.start_run(window())
    assert store.add_items(r2, [item("Clima, premiati 40 studenti", "https://news.google.com/rss/articles/zzz")]) == []
    assert len(store.items(r1)) == 1 and store.items(r2) == []


def test_reprints_from_n_outlets_plus_google_copy_make_one_story(store):
    from clipper.dedupe import group_stories
    titles = ["Green Schools, premiati 40 studenti ricercatori da oltre 12 regioni",
              "Clima, premiati a Roma 40 studenti ricercatori di oltre 12 regioni con Green Schools",
              "Green Schools: premiati 40 studenti ricercatori da oltre 12 regioni"]
    items = [item(t, f"https://o{i}.it/x", f"o{i}.it") for i, t in enumerate(titles)]
    google = item(titles[0], "https://news.google.com/rss/articles/abc", "o0.it")
    run = store.start_run(window())
    ids = store.add_items(run, items + [google])
    assert len(ids) == 3 and len(store.items(run)) == 3
    stored = store.items(run)
    groups = group_stories(stored)
    assert len(groups) == 1
    story_id = stored[groups[0][0]].id
    for idx in groups[0]:
        store.set_story(stored[idx].id, story_id)
    assert store.stories(run) == {story_id: ids}


def test_naive_datetime_rejected(store):
    run = store.start_run(window())
    bad = item()
    bad.published_at = datetime(2026, 11, 1)
    with pytest.raises(ValueError):
        store.add_items(run, [bad])


def test_creates_parent_directory(tmp_path):
    with Store(tmp_path / "a" / "b" / "m.db") as s:
        assert s.last_successful_until() is None


def test_update_item_persists_fulltext_fields(store):
    run = store.start_run(window())
    (i,) = store.add_items(run, [item()])
    got = store.get_item(i)
    got.full_text, got.canonical_url, got.subscribers_only = "body", "https://a.it/c", True
    store.update_item(got)
    again = store.get_item(i)
    assert (again.full_text, again.canonical_url, again.subscribers_only) == ("body", "https://a.it/c", True)


def test_merge_run_stats_keeps_existing_keys(store):
    run = store.start_run(window())
    store.finish_run(run, "ok", {"new": 3})
    store.merge_run_stats(run, {"candidates": 2, "new": 4})
    assert store.run(run).stats == {"new": 4, "candidates": 2}
    assert store.run(run).status == "ok"


def test_merge_run_stats_unknown_run(store):
    with pytest.raises(KeyError):
        store.merge_run_stats(99, {"a": 1})


def test_last_run(store):
    assert store.last_run() is None
    store.start_run(window())
    second = store.start_run(window(1))
    assert store.last_run().id == second


def test_interrupted_runs_are_closed_as_failed(store):
    stuck = store.start_run(window())
    done = store.start_run(window(1))
    store.finish_run(done, "ok", {"new": 1})
    assert store.close_interrupted_runs() == 1
    assert store.run(stuck).status == "failed"
    assert store.run(stuck).stats["error"] == "interrupted before it finished"
    assert store.run(done).status == "ok"
    assert store.close_interrupted_runs() == 0
