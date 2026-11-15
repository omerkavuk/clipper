import dataclasses
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from clipper.collectors import SourceStatus
from clipper.estimate import (REPORT_BASE_TOKENS, RUN_OVERHEAD_TOKENS,
                                    TOKENS_PER_REPORT_ITEM, TOKENS_PER_TAGGED_ITEM)
from clipper.models import Item, RunWindow
from clipper.pipeline import Caps
from clipper.render import export_draft_input, render_report, render_run_note
from clipper.store import Store

UNTIL = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
SINCE = datetime(2026, 11, 7, 9, tzinfo=timezone.utc)


def mk(n, title=None, outlet=None, url=None, when=None, **kw):
    return Item(source="fake", outlet=outlet or f"Outlet{n}", outlet_domain=f"o{n}.it",
                url=url or f"https://o{n}.it/a{n}", title=title or f"Headline number {n} zebra{n}",
                published_at=when or datetime(2026, 10, 23, 8, n, tzinfo=timezone.utc),
                collected_at=UNTIL, matched_queries=["subject"], **kw)


def tag(relevant=True, naming="explicit", tone="positive", related="other"):
    return {"relevant": relevant, "type": "news", "tone": tone, "tone_reason": "because",
            "naming": naming, "related_to": related, "summary": "A summary."}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "m.db") as s:
        yield s


@pytest.fixture
def p(example_project):
    return example_project


def make_run(store, items, tags=None, since=SINCE, until=UNTIL, status="ok", stats=None):
    run = store.start_run(RunWindow(since, until))
    ids = store.add_items(run, items)
    for i, t in zip(ids, tags if tags is not None else [tag()] * len(ids)):
        if t is not None:
            store.set_tags(i, t)
    store.finish_run(run, status, stats or {"collected": 5, "new": len(ids), "malformed": 0})
    store.merge_run_stats(run, {"candidate_ids": ids, "tagged": True})  # as after a complete batch
    return run, ids


def draft_with(tmp_path, text):
    f = tmp_path / "draft.md"
    f.write_text(text, encoding="utf-8")
    return f


def report_md(tmp_path, p, date="2026-11-14"):
    return (tmp_path / "reports" / f"{p.name}-{date}.md").read_text("utf-8")


def test_run_note_lists_failed_outlets(tmp_path, store, p):
    p = dataclasses.replace(p, estimate=None)
    run, ids = make_run(store, [mk(1), mk(2), mk(3)], [tag(), tag(), tag(relevant=False)])
    for st in [SourceStatus("outlet", "ansa.it", True, 3, None, SINCE),
               SourceStatus("outlet", "agi.it", False, 0, "HTTP 404", None),
               SourceStatus("outlet", "late.it", True, 1, None, SINCE + timedelta(days=2)),
               SourceStatus("google_news", "subject", True, 5, None, SINCE),
               SourceStatus("google_news", "person:x", False, 0, "timeout", None),
               SourceStatus("gdelt", "subject", False, 0, "rate limited", None)]:
        store.add_source_status(run, st)
    stats = {"candidates": 3, "truncated": 0, "applied": 3, "rejected": 1, "unknown_ids": 2,
             "removed_refs": 4, "removed_sentences": []}
    path = render_run_note(store, run, p, stats, tmp_path)
    assert path == tmp_path / "run-notes" / f"{p.name}-2026-11-14.md"
    note = path.read_text("utf-8")
    assert "Checked 2 of 3 outlets" in note
    assert "agi.it: HTTP 404" in note
    assert "Google News: 1 of 2 requests ok" in note and "timeout" in note
    assert "GDELT: 0 of 1 requests ok" in note and "rate limited" in note
    assert "late.it: covered since 2026-11-09" in note
    assert "Items collected: 5 · new: 3 · malformed: 0" in note
    assert "Candidates: 3 · truncated: 0" in note
    assert "Tags applied: 3 · rejected: 1 · unknown: 2" in note
    assert "Citations removed: 4" in note
    assert "no estimate yet" in note
    expected = 3 * TOKENS_PER_TAGGED_ITEM + REPORT_BASE_TOKENS + 2 * TOKENS_PER_REPORT_ITEM + RUN_OVERHEAD_TOKENS
    assert f"This run (count-based): {expected:,}" in note


def test_run_note_shows_estimate(tmp_path, store, p):
    p = dataclasses.replace(p, estimate={"per_run": 12345})
    run, _ = make_run(store, [mk(1)])
    assert "Estimated tokens per run: 12,345" in render_run_note(store, run, p, {}, tmp_path).read_text("utf-8")


def test_draft_input_has_previous_period(tmp_path, store, p):
    run1, _ = make_run(store, [mk(1), mk(2), mk(3)],
                       [tag(tone="negative"), tag(tone="positive"), tag(relevant=False)])
    store.mark_reported(run1)
    make_run(store, [mk(4)], [tag()], status="failed", since=UNTIL, until=UNTIL + timedelta(days=7))
    make_run(store, [mk(6)], [tag()], since=UNTIL, until=UNTIL + timedelta(days=7))  # never rendered
    run3, _ = make_run(store, [mk(5)], [tag()], since=UNTIL, until=UNTIL + timedelta(days=7))
    out = tmp_path / "in.json"
    export_draft_input(store, run3, p, Caps(), out)
    prev = json.loads(out.read_text("utf-8"))["previous"]
    assert prev["relevant"] == 2
    assert prev["by_tone"] == {"negative": 1, "positive": 1}
    assert prev["by_related_to"] == {"other": 2}


def test_draft_input_first_run_has_no_previous(tmp_path, store, p):
    run, _ = make_run(store, [mk(1)])
    out = tmp_path / "in.json"
    export_draft_input(store, run, p, Caps(), out)
    assert json.loads(out.read_text("utf-8"))["previous"] is None


def test_draft_input_has_no_source_fields(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, title="Città")], [tag()])
    out = tmp_path / "in.json"
    export_draft_input(store, run, p, Caps(), out)
    raw = out.read_text("utf-8")
    assert "Città" in raw  # not escaped
    item = json.loads(raw)["items"][0]
    assert set(item) == {"id", "outlet", "title", "published_at", "type", "tone", "tone_reason",
                         "naming", "related_to", "summary", "subscribers_only", "story_id"}
    for key in ("source", "matched_queries", "url"):
        assert key not in item


def test_draft_input_relevant_only_capped_and_ordered(tmp_path, store, p):
    items = [mk(n, when=datetime(2026, 11, 1 + n, tzinfo=timezone.utc)) for n in range(1, 6)]
    tags = [tag(naming="none"), tag(naming="explicit"), tag(relevant=False), tag(naming="none"),
            tag(naming="explicit")]
    run, ids = make_run(store, items, tags)
    store.set_story(ids[0], ids[0])
    store.set_story(ids[3], ids[0])
    out = tmp_path / "in.json"
    result = export_draft_input(store, run, p, Caps(max_report_items=3), out)
    data = json.loads(out.read_text("utf-8"))
    # explicit first (newest first), then newest of the rest
    assert [i["id"] for i in data["items"]] == [ids[4], ids[1], ids[3]]
    assert result == {"exported": 3, "relevant": 4, "truncated": 1}
    assert data["stories"] == {str(ids[0]): [ids[0], ids[3]]}
    assert next(i for i in data["items"] if i["id"] == ids[3])["story_id"] == ids[0]


# ---------------------------------------------------------------- leadership report

def test_header_has_no_outlet_count_and_shows_inclusive_end(tmp_path, store, p):
    midnight = datetime(2026, 11, 14, tzinfo=timezone.utc)
    run, ids = make_run(store, [mk(1)], since=SINCE, until=midnight)
    store.add_source_status(run, SourceStatus("outlet", "a.it", True, 0, None, None))
    render_report(store, run, draft_with(tmp_path, "A good month."), p, tmp_path)
    md = report_md(tmp_path, p, date="2026-11-13")
    assert md.startswith("# Aurora Climate Lab media coverage\n")
    assert "Aurora Climate Lab · 07 Nov 2026 – 13 Nov 2026 · Scope: online media in Italy\n" in md
    assert "checked" not in md


def test_sections_in_order_and_old_sections_gone(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)], [tag(related="Green Schools")])
    render_report(store, run, draft_with(tmp_path, "## Summary\n\nA good month. Mostly awards."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert md.index("## At a glance") < md.index("## Summary") < md.index("## By programme")
    for gone in ("Key items", "Things to notice", "By theme"):
        assert gone not in md
    assert "\nA good month. Mostly awards.\n" in md


def test_at_a_glance_figures(tmp_path, store, p):
    items = [mk(1, outlet="La Repubblica", url="https://www.repubblica.it/a"),
             mk(2, outlet="Comune di Lumio"), mk(3, outlet="Comune di Lumio"),
             mk(4, outlet="Kypros News", url="https://kyprosnews.com.cy/x"), mk(5)]
    items[3].outlet_domain = "kyprosnews.com.cy"
    items[0].outlet_domain = "repubblica.it"
    tags = [tag(related="Green Schools"),
            dict(tag(related="Green Schools", naming="none"), type="institutional"),
            dict(tag(related="Green Schools", naming="parent_only", tone="neutral"), type="institutional"),
            tag(related="Green Schools", naming="none"),
            tag(related="Clean Air Week", tone="negative")]
    run, ids = make_run(store, items, tags)
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "- **Coverage:** 5 articles in 4 outlets between 7 November 2026 and 14 November 2026." in md
    assert "- **Tone:** 3 positive, 1 neutral, 1 negative." in md
    assert "- **Green Schools:** 4 of the 5 articles." in md
    assert "- **Aurora Climate Lab named directly:** 2 of the 5 articles; 1 more name only Aurora Foundation." in md
    assert ("- **Highest-profile coverage:** La Repubblica, [Headline number 1 zebra1]"
            "(https://www.repubblica.it/a) (23 Oct 2026).") in md
    assert "- **Who published:** 3 news articles; 2 from institutions, 2 of them from Comune di Lumio." in md
    assert "- **Reach abroad:** 1 article (Cyprus)." in md
    assert "Compared with last period" not in md


def test_compared_with_last_period(tmp_path, store, p):
    run1, _ = make_run(store, [mk(1), mk(2)])
    store.mark_reported(run1)
    run2, _ = make_run(store, [mk(3)], since=UNTIL, until=UNTIL + timedelta(days=7))
    render_report(store, run2, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "- **Compared with last period:** 1 article, against 2 in the previous report." in report_md(
        tmp_path, p, "2026-11-21")


def test_programme_groups_outlet_first_and_gaps_kept(tmp_path, store, p):
    p = dataclasses.replace(p, report_groups={})
    items = [mk(1, outlet="Comune di Lumio", when=datetime(2026, 11, 8, tzinfo=timezone.utc)),
             mk(2, outlet="Comune di Lumio", when=datetime(2026, 11, 9, tzinfo=timezone.utc)),
             mk(3, outlet="Ipsoa")]
    run, ids = make_run(store, items, [tag(related="Green Schools"), tag(related="Green Schools"),
                                        tag(related="Clean Air Week")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert ("### Green Schools (2 articles)\n\n**Comune di Lumio**\n\n"
            "- A summary. [Headline number 2 zebra2](https://o2.it/a2) · 09 Nov 2026\n"
            "- A summary. [Headline number 1") in md
    assert "### Clean Air Week (1 article)\n\n**Ipsoa**" in md
    assert "### Solar Villages\n\nNo coverage this period." in md
    assert "### Aurora Climate Lab: other coverage\n\nNo coverage this period." in md
    assert md.index("Green Schools (2") < md.index("Clean Air Week (1") < md.index("### Solar Villages")


def test_custom_report_groups_merge_programmes(tmp_path, store, p):
    p = dataclasses.replace(p, report_groups={"Solar Villages and VELA": ["Solar Villages"],
                                              "other": "Aurora and the Aurora Climate Lab"})
    run, ids = make_run(store, [mk(1), mk(2)], [tag(related="Solar Villages"), tag(related="other")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "### Solar Villages and VELA (1 article)" in md
    assert "### Aurora and the Aurora Climate Lab (1 article)" in md
    assert "### Solar Villages\n" not in md


def test_same_story_in_two_outlets_shares_one_heading(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, outlet="Radio Valle"), mk(2, outlet="Notizie Verdi"), mk(3, outlet="Gamma")],
                        [tag(naming="explicit"), tag(naming="none"), tag(relevant=False)])
    for i in ids:
        store.set_story(i, ids[0])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "**Radio Valle and Notizie Verdi**" in md
    assert ("- A summary. [Headline number 1 zebra1](https://o1.it/a1) · Radio Valle · 23 Oct 2026; "
            "[Headline number 2 zebra2](https://o2.it/a2) · Notizie Verdi · 23 Oct 2026") in md
    assert "Gamma" not in md


def test_outlet_name_cannot_inject_a_link(tmp_path, store, p):
    evil = "Evil](https://evil.example/x) [Click"
    run, ids = make_run(store, [mk(1, outlet=evil, title="T [x](https://evil.example/y)")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "](https://evil.example" not in report_md(tmp_path, p)


def test_canonical_url_preferred(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, canonical_url="https://real.it/story")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "(https://real.it/story)" in report_md(tmp_path, p)


def test_untagged_item_is_not_relevant(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)], [None])
    stats = render_report(store, run, draft_with(tmp_path, "x"), p, tmp_path)
    assert stats["relevant"] == 0
    assert "No coverage of Aurora Climate Lab was found in this period." in report_md(tmp_path, p)


def test_quiet_period_message(tmp_path, store, p):
    run, _ = make_run(store, [mk(1)], [tag(relevant=False)])
    store.add_source_status(run, SourceStatus("outlet", "ansa.it", True, 0, None, SINCE))
    stats = render_report(store, run, draft_with(tmp_path, "Lots of invented news [[item:1]]"), p, tmp_path)
    md = report_md(tmp_path, p)
    assert md.rstrip().endswith("\n\nNo coverage of Aurora Climate Lab was found in this period.")
    assert "invented" not in md and stats["relevant"] == 0
    note = render_run_note(store, run, p, stats, tmp_path).read_text("utf-8")
    assert "Checked 1 of 1 outlets" in note and "Relevant items: 0" in note


def test_summary_is_cleaned_and_short(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)])
    draft = (f"## Summary\n\nA good month [[item:{ids[0]}]]. It was found via Google\nNews by us. "
             "See [this](https://invented.example/x) piece. Third sentence. Fourth sentence.")
    stats = render_report(store, run, draft_with(tmp_path, draft), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "\nA good month. See this piece. Third sentence.\n" in md
    assert "Google" not in md and "invented" not in md and "Fourth" not in md
    assert stats["removed_sentences"] == ["It was found via Google News by us."]
    assert stats["removed_refs"] == 1 and stats["removed_links"] == 1 and stats["fallback"] is False


def test_empty_summary_is_left_out(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)])
    stats = render_report(store, run, draft_with(tmp_path, "The agent searched the feeds."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "## Summary" not in md and "## By programme" in md and stats["fallback"] is True
    note = render_run_note(store, run, p, stats, tmp_path).read_text("utf-8")
    assert "Draft unusable" in note


def test_description_never_mentions_method(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)], [dict(tag(), summary="Found through Google News.")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "google" not in report_md(tmp_path, p).lower()


def test_headline_with_agenti_not_removed(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, title="Gli agenti del clima a Roma")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "[Gli agenti del clima a Roma](" in report_md(tmp_path, p)


def test_subscribers_only_marker(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, subscribers_only=True), mk(2)])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    lines = report_md(tmp_path, p).splitlines()
    assert next(l for l in lines if "zebra1" in l).endswith("23 Oct 2026 · subscribers only")
    assert "subscribers only" not in next(l for l in lines if "zebra2" in l)


def test_long_groups_are_capped(tmp_path, store, p):
    p = dataclasses.replace(p, caps={"max_report_items": 2})
    run, ids = make_run(store, [mk(n) for n in range(1, 5)])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert md.count("- A summary.") == 2 and "Plus 2 more articles." in md
    assert "4 articles in 4 outlets" in md


def test_word_file_has_outlet_style_and_links(tmp_path, store, p):
    from docx import Document
    from docx.shared import RGBColor
    run, ids = make_run(store, [mk(1, outlet="Comune di Lumio")], [tag(related="Green Schools")])
    stats = render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert stats["report_docx"].endswith(f"{p.name}-2026-11-14.docx")
    doc = Document(stats["report_docx"])
    styles = [(para.style.name, para.text) for para in doc.paragraphs]
    assert ("Heading 1", "Aurora Climate Lab media coverage") in styles
    assert ("Heading 3", "Green Schools (1 article)") in styles
    outlet = next(para for para in doc.paragraphs if para.text == "Comune di Lumio")
    assert outlet.runs[0].bold and outlet.runs[0].font.color.rgb == RGBColor(0x00, 0x20, 0x60)
    assert outlet.paragraph_format.keep_with_next
    item = next(para for para in doc.paragraphs if "zebra1" in para.text)
    assert item.style.name == "List Bullet"
    assert "https://o1.it/a1" in [r.target_ref for r in doc.part.rels.values() if r.is_external]
    assert not (tmp_path / "reports" / f"{p.name}-2026-11-14.html").exists()


def test_filename_with_apostrophe_and_spaces(tmp_path, store, p):
    p = dataclasses.replace(p, name="Sam's Climate Lab")
    run, ids = make_run(store, [mk(1)])
    stats = render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path / "out dir")
    assert (tmp_path / "out dir" / "reports" / "Sam's Climate Lab-2026-11-14.docx").exists()
    assert stats["report_md"].endswith("Sam's Climate Lab-2026-11-14.md")


def test_file_date_is_last_second_of_window(tmp_path, store, p):
    midnight = datetime(2026, 11, 15, tzinfo=timezone.utc)
    run, ids = make_run(store, [mk(1)], since=SINCE, until=midnight)
    stats = render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert stats["report_md"].endswith("-2026-11-14.md")


def test_reach_abroad_uses_tagged_country(tmp_path, store, p):
    run, ids = make_run(store, [mk(1, outlet="Kypros News", url="https://www.kyprosnews.com/x"), mk(2)],
                        [dict(tag(), outlet_country="CY"), dict(tag(), outlet_country="IT")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    assert "- **Reach abroad:** 1 article (Cyprus)." in report_md(tmp_path, p)


def test_description_is_one_sentence(tmp_path, store, p):
    run, ids = make_run(store, [mk(1)], [dict(tag(), summary="Comune di Lumio awards 50 winners. Based on headline only.")])
    render_report(store, run, draft_with(tmp_path, "Fine."), p, tmp_path)
    md = report_md(tmp_path, p)
    assert "- Comune di Lumio awards 50 winners. [Headline" in md and "Based on" not in md
