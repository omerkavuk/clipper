from datetime import datetime, timezone
from pathlib import Path

import responses

from clipper.collectors import Collector
from clipper.collectors.google_news import GoogleNewsCollector
from clipper.models import RunWindow
from clipper.queries import Query

FEED = (Path(__file__).parent / "fixtures" / "gnews_it.xml").read_bytes()
URL = "https://news.google.com/rss/search"
Q = Query("subject", "Subject", [["Aurora"]], [])
WINDOW = RunWindow(datetime(2026, 10, 14, tzinfo=timezone.utc),
                   datetime(2026, 11, 14, tzinfo=timezone.utc))


def make(**kw):
    return GoogleNewsCollector("IT", "it", [], sleep=lambda s: None, **kw)


@responses.activate
def test_parses_items_in_window():
    responses.add(responses.GET, URL, body=FEED)
    items, statuses = make().collect([Q], WINDOW)
    assert len(items) == 2
    assert {i.outlet_domain for i in items} == {"brand-news.it", "quotidiano.example.it"}
    first = items[0]
    assert first.source == "google_news"
    assert first.outlet == "Brand News"
    assert first.url == "https://news.google.com/rss/articles/CBMiAAA1"
    assert first.language == "it"
    assert first.published_at.tzinfo is not None
    assert statuses[0].ok and statuses[0].items == 2
    assert statuses[0].source == "google_news" and statuses[0].target == "subject"


@responses.activate
def test_request_has_dates_and_edition():
    responses.add(responses.GET, URL, body=FEED)
    make().collect([Q], WINDOW)
    url = responses.calls[0].request.url
    assert "after%3A2026-10-14" in url or "after:2026-10-14" in url
    assert "before%3A2026-11-15" in url or "before:2026-11-15" in url
    assert "gl=IT" in url
    assert "ceid=IT%3Ait" in url or "ceid=IT:it" in url


@responses.activate
def test_http_error_gives_failed_status():
    responses.add(responses.GET, URL, status=500)
    items, statuses = make().collect([Q], WINDOW)
    assert items == []
    assert statuses[0].ok is False
    assert "500" in statuses[0].error


@responses.activate
def test_blocked_by_google_stops_all_later_searches():
    responses.add(responses.GET, URL, status=503)
    c = make()
    q2 = Query("programme:X", "X", [["X"]], [])
    _, statuses = c.collect([Q, q2], WINDOW)
    assert [s.error for s in statuses] == ["blocked by Google", "blocked by Google, skipped"]
    _, later = c.collect([Q], WINDOW, site=["a.it", "b.it"])
    assert later[0].error == "blocked by Google, skipped"
    assert len(responses.calls) == 1  # one request, then nothing more for the run


@responses.activate
def test_budget_skips_searches_that_would_run_late():
    responses.add(responses.GET, URL, body=FEED)
    ticks = iter([0, 0, 100, 100])
    c = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None, clock=lambda: next(ticks))
    q2 = Query("programme:X", "X", [["X"]], [])
    _, statuses = c.collect([Q, q2], WINDOW, budget=50)
    assert statuses[0].ok and statuses[1].error == "time budget exceeded, skipped"
    assert len(responses.calls) == 1


def test_site_query_for_several_domains():
    assert make().site_query(Q, ["a.it", "b.it"]) == "Aurora (site:a.it OR site:b.it)"


@responses.activate
def test_matched_queries_recorded():
    responses.add(responses.GET, URL, body=FEED)
    items, _ = make().collect([Q], WINDOW)
    assert all(i.matched_queries == [Q.id] for i in items)


@responses.activate
def test_site_restriction_and_target():
    responses.add(responses.GET, URL, body=FEED)
    c = make()
    assert c.site_query(Q, "ansa.it") == "Aurora site:ansa.it"
    _, statuses = c.collect([Q], WINDOW, site="ansa.it")
    assert "site%3Aansa.it" in responses.calls[0].request.url or "site:ansa.it" in responses.calls[0].request.url
    assert statuses[0].target == "ansa.it"


@responses.activate
def test_missing_source_url_falls_back_to_link_host():
    body = FEED.replace(b'<source url="https://www.brand-news.it">', b"<source>")
    body = body.replace(b"https://news.google.com/rss/articles/CBMiAAA1", b"https://www.brand-news.it/a/1")
    responses.add(responses.GET, URL, body=body)
    items, _ = make().collect([Q], WINDOW)
    assert "brand-news.it" in {i.outlet_domain for i in items}


def test_pauses_between_queries_only():
    pauses = []
    c = GoogleNewsCollector("IT", "it", [], sleep=pauses.append)
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, URL, body=FEED)
        rsps.add(responses.GET, URL, body=FEED)
        c.collect([Q, Query("b", "B", [["x"]], [])], WINDOW)
    assert pauses == [1]


def test_satisfies_protocol():
    c: Collector = make()
    assert c.name == "google_news"


@responses.activate
def test_title_suffix_stripped():
    responses.add(responses.GET, URL, body=FEED)
    items, _ = make().collect([Q], WINDOW)
    assert items[0].title == "Aurora apre un nuovo centro a Roma"


@responses.activate
def test_inner_dash_preserved_and_missing_source_untouched():
    body = FEED.replace(b"Aurora apre un nuovo centro a Roma - Brand News",
                        b"Aurora - il piano - Brand News")
    body = body.replace(b'<source url="https://quotidiano.example.it">Il Quotidiano</source>',
                        b'<source url="https://quotidiano.example.it"></source>')
    responses.add(responses.GET, URL, body=body)
    items, _ = make().collect([Q], WINDOW)
    assert items[0].title == "Aurora - il piano"
    assert items[1].title == "Sviluppo sostenibile, il piano dell'Aurora - Il Quotidiano"
    assert items[1].outlet == "quotidiano.example.it"


@responses.activate
def test_unparseable_response_is_failed_status():
    responses.add(responses.GET, URL, body=b"<html><body>Before you continue<br></body></html>")
    items, statuses = make().collect([Q], WINDOW)
    assert items == []
    assert statuses[0].ok is False and statuses[0].covered_since is None
    assert "unparseable response" in statuses[0].error
