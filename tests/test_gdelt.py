import json
from datetime import datetime, timezone
from pathlib import Path

import responses

from clipper.collectors import Collector
from clipper.collectors.gdelt import GDELT_BUDGET_SECONDS, GdeltCollector
from clipper.models import RunWindow
from clipper.queries import Query

BODY = (Path(__file__).parent / "fixtures" / "gdelt_artlist.json").read_text(encoding="utf-8")
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
Q = Query("subject", "Subject", [["Aurora"]], [])
Q2 = Query("other", "Other", [["Aurora"], ["Italia"]], [])
WINDOW = RunWindow(datetime(2026, 10, 14, tzinfo=timezone.utc),
                   datetime(2026, 11, 14, tzinfo=timezone.utc))


def make(sleeps=None, **kw):
    log = sleeps if sleeps is not None else []
    return GdeltCollector("IT", ["it", "en"], [], sleep=log.append, **kw)


@responses.activate
def test_parses_items_in_window():
    responses.add(responses.GET, URL, body=BODY)
    items, statuses = make().collect([Q], WINDOW)
    assert len(items) == 3  # the September article is outside the window
    first = items[0]
    assert first.source == "gdelt"
    assert first.outlet_domain == "brand-news.it" and first.outlet == "brand-news.it"
    assert first.url == "https://www.brand-news.it/a1"
    assert first.published_at == datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    assert first.language == "it"
    assert items[1].language == "en"
    assert items[2].language == "tagalog"
    assert first.matched_queries == ["subject"]
    assert statuses[0].ok and statuses[0].items == 3 and statuses[0].covered_since == WINDOW.since
    assert statuses[0].source == "gdelt" and statuses[0].target == "subject"


@responses.activate
def test_request_params():
    responses.add(responses.GET, URL, body=BODY)
    make().collect([Q], WINDOW)
    qs = responses.calls[0].request.params
    assert qs["mode"] == "artlist" and qs["format"] == "json"
    assert qs["maxrecords"] == "250" and qs["sort"] == "datedesc"
    assert qs["startdatetime"] == "20261014000000"
    assert qs["enddatetime"] == "20261114000000"
    assert "sourcecountry:IT" in qs["query"]


@responses.activate
def test_empty_object_is_successful_empty_fetch():
    responses.add(responses.GET, URL, body="{}")
    items, statuses = make().collect([Q], WINDOW)
    assert items == [] and statuses[0].ok and statuses[0].items == 0
    assert statuses[0].covered_since == WINDOW.since


@responses.activate
def test_pacing_between_requests():
    responses.add(responses.GET, URL, body=BODY)
    sleeps = []
    make(sleeps).collect([Q, Q2], WINDOW)
    assert len(sleeps) == 1 and sleeps[0] >= 5.5


@responses.activate
def test_429_backoff_then_success():
    responses.add(responses.GET, URL, status=429)
    responses.add(responses.GET, URL, status=429)
    responses.add(responses.GET, URL, body=BODY)
    sleeps = []
    items, statuses = make(sleeps).collect([Q], WINDOW)
    assert sleeps == [15, 30, 5.5]  # quick retry, wait, then the late retry (paced as usual)
    assert len(items) == 3 and statuses[0].ok


@responses.activate
def test_429_three_times_gives_failed_status():
    for _ in range(4):
        responses.add(responses.GET, URL, status=429)
    sleeps = []
    items, statuses = make(sleeps).collect([Q], WINDOW)
    assert sleeps == [15, 30, 5.5, 15]  # quick retry, wait, late retry with its own quick retry
    assert items == []
    assert not statuses[0].ok and statuses[0].error == "rate limited"
    assert statuses[0].covered_since is None


@responses.activate
def test_non_json_body_is_failure():
    responses.add(responses.GET, URL, body="Your query was too short or too long.")
    items, statuses = make().collect([Q], WINDOW)
    assert items == [] and not statuses[0].ok and statuses[0].error
    assert statuses[0].covered_since is None


@responses.activate
def test_failed_query_does_not_stop_next():
    responses.add(responses.GET, URL, body="oops")
    responses.add(responses.GET, URL, body=BODY)
    items, statuses = make().collect([Q, Q2], WINDOW)
    assert [s.ok for s in statuses] == [False, True]
    assert len(items) == 3


def test_satisfies_collector_protocol():
    c: Collector = make()
    assert c.name == "gdelt"
    json.loads(BODY)


@responses.activate
def test_429_exhaustion_skips_remaining_queries_without_requests():
    for _ in range(4):
        responses.add(responses.GET, URL, status=429)
    Q3 = Query("third", "Third", [["Aurora"], ["Roma"]], [])
    lines = []
    items, statuses = make(progress=lines.append).collect([Q, Q2, Q3], WINDOW)
    assert len(responses.calls) == 4  # query 1 twice, then its late retry twice; the others wait
    assert [s.error for s in statuses] == ["rate limited"] * 3
    assert [s.target for s in statuses] == ["subject", "other", "third"]
    assert not any(s.ok for s in statuses) and items == []
    assert "GDELT: asked to slow down; will retry later in the run" in lines


@responses.activate
def test_time_budget_skips_rest():
    responses.add(responses.GET, URL, body=BODY)
    ticks = iter([0, 10, 999, 999, 999, 999])
    items, statuses = make(clock=lambda: next(ticks)).collect([Q, Q2, Q2], WINDOW)
    assert len(responses.calls) == 1
    assert statuses[0].ok
    assert [s.error for s in statuses[1:]] == ["time budget exceeded, skipped"] * 2
    assert not statuses[1].ok


@responses.activate
def test_unusable_query_is_skipped_without_request():
    responses.add(responses.GET, URL, body=BODY)
    long = Query("subject", "S", [["one two three four five six"]], [])
    items, statuses = make().collect([long, Q], WINDOW)
    assert len(responses.calls) == 1
    assert statuses[0].error == "query not usable for GDELT" and not statuses[0].ok
    assert statuses[1].ok


@responses.activate
def test_progress_lines_only_with_callback():
    responses.add(responses.GET, URL, body=BODY)
    lines = []
    make(progress=lines.append).collect([Q], WINDOW)
    assert lines == ["GDELT: 1/1 requests"]


@responses.activate
def test_topic_and_people_searches_are_folded_into_one_request():
    from urllib.parse import parse_qs, urlparse
    responses.add(responses.GET, URL, body=BODY)
    progs = [Query(f"programme:{t}", t, [[t]], []) for t in ("Green Schools", "Solar Villages")]
    person = Query("person:Giulia Marchetti", "P", [["Giulia Marchetti"]], [])
    items, statuses = make().collect([Q, *progs, person], WINDOW)
    assert len(responses.calls) == 2  # the subject, then one OR'd request for the other three
    folded = parse_qs(urlparse(responses.calls[1].request.url).query)["query"][0]
    assert '"Green Schools" OR "Solar Villages" OR "Giulia Marchetti"' in folded
    assert [s.target for s in statuses] == ["subject", "topics and people 1"]
