import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import responses

from clipper import fulltext, gnews_links
from clipper.fulltext import fetch_full_text
from clipper.models import Item

FIX = Path(__file__).parent / "fixtures"
GID = "CBMiekFVX3lxTE84"
GURL = f"https://news.google.com/rss/articles/{GID}?oc=5"
PAGE = gnews_links.ARTICLE_PAGE.format(id=GID)
PUBLISHER = "https://www.ecodallecitta.it/green-schools-milano/"
REDIRECT_PAGE = ('<c-wiz><div jscontroller="aLI87" data-n-a-sg="SIG123" '
                 'data-n-a-ts="1763000000"></div></c-wiz>')


def batch_body(url):
    row = ["wrb.fr", "Fbv4je", json.dumps(["garturlres", url, 1]), None, None, None, "generic"]
    return ")]}'\n\n" + json.dumps([row, ["di", 42], ["af.httprm", 41, "x", 1]])


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    fulltext.reset_google_state()
    monkeypatch.setattr(fulltext, "GOOGLE_PAUSE_SECONDS", 0)


def gitem():
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return Item(source="google_news", outlet="Eco dalle Città", outlet_domain="ecodallecitta.it",
                url=GURL, title="Green Schools, da Lumio", published_at=now, collected_at=now)


def test_article_id():
    assert gnews_links.article_id(GURL) == GID
    assert gnews_links.article_id("https://news.google.com/articles/ABC") == "ABC"
    assert gnews_links.article_id("https://www.ansa.it/articles/ABC") is None


@responses.activate
def test_resolve_returns_publisher_url():
    responses.get(PAGE, body=REDIRECT_PAGE)
    responses.post(gnews_links.BATCH_URL, body=batch_body(PUBLISHER))
    assert gnews_links.resolve_many([GURL], requests.Session(), sleep=lambda s: None) == {GURL: PUBLISHER}
    sent = responses.calls[1].request.body
    assert "garturlreq" in requests.utils.unquote(sent) and "SIG123" in requests.utils.unquote(sent)


@responses.activate
def test_resolve_without_signature_says_why():
    responses.get(PAGE, body="<html>nothing</html>")
    out = gnews_links.resolve_many([GURL], requests.Session(), sleep=lambda s: None)[GURL]
    assert isinstance(out, gnews_links.Unresolved) and "no signature" in str(out)


@responses.activate
def test_consent_page_is_recognised_and_cookie_sent():
    responses.get(PAGE, status=302, headers={"Location": "https://consent.google.com/ml?continue=x"})
    responses.get("https://consent.google.com/ml", body="<html>Before you continue</html>")
    out = gnews_links.resolve_many([GURL], requests.Session(), sleep=lambda s: None)[GURL]
    assert isinstance(out, gnews_links.Unresolved) and "consent page" in str(out)
    assert "SOCS=" in responses.calls[0].request.headers.get("Cookie", "")


@responses.activate
def test_failed_lookups_are_counted_with_reasons():
    responses.get(PAGE, body="<html>nothing</html>")
    fetch_full_text(gitem(), None)
    stats = fulltext.google_link_stats()
    assert stats["failed"] == 1 and stats["resolved"] == 0
    assert stats["reasons"] == {"redirect page had no signature": 1}


@responses.activate
def test_resolve_raises_when_blocked():
    responses.get(PAGE, status=429)
    out = gnews_links.resolve_many([GURL], requests.Session(), sleep=lambda s: None)[GURL]
    assert isinstance(out, gnews_links.GoogleBlocked)


@responses.activate
def test_full_text_follows_google_link_to_publisher():
    responses.get(PAGE, body=REDIRECT_PAGE)
    responses.post(gnews_links.BATCH_URL, body=batch_body(PUBLISHER))
    responses.get(PUBLISHER, body=(FIX / "article.html").read_text(encoding="utf-8"))
    out = fetch_full_text(gitem(), None)
    assert out.canonical_url == PUBLISHER and out.full_text


@responses.activate
def test_publisher_link_kept_even_if_page_fails():
    responses.get(PAGE, body=REDIRECT_PAGE)
    responses.post(gnews_links.BATCH_URL, body=batch_body(PUBLISHER))
    responses.get(PUBLISHER, status=500)
    out = fetch_full_text(gitem(), None)
    assert out.canonical_url == PUBLISHER and out.full_text is None


@responses.activate
def test_block_stops_all_later_google_lookups():
    responses.get(PAGE, status=503)
    assert fetch_full_text(gitem(), None).canonical_url is None
    assert fetch_full_text(gitem(), None).canonical_url is None
    assert len(responses.calls) == 1  # the second item never asked Google


@responses.activate
def test_unresolvable_google_link_is_left_alone():
    responses.get(PAGE, body="<html></html>")
    out = fetch_full_text(gitem(), None)
    assert out.url == GURL and out.canonical_url is None and out.full_text is None


def batch_answer(urls):
    rows = [["wrb.fr", "Fbv4je", json.dumps(["garturlres", u, 1]), None, None, None, str(n)]
            for n, u in enumerate(urls)]
    return ")]}'\n\n" + json.dumps(rows + [["di", 42]])


def test_article_page_is_asked_for_with_a_locale():
    assert gnews_links.page_url(GID, GURL).endswith("?hl=en-US&gl=US&ceid=US%3Aen")
    it = gnews_links.page_url(GID, f"https://news.google.com/rss/articles/{GID}?hl=it&gl=IT&ceid=IT:it")
    assert it.endswith("?hl=it&gl=IT&ceid=IT%3Ait")


@responses.activate
def test_many_links_share_one_lookup():
    ids = ["AAA1", "BBB2", "CCC3"]
    for gid in ids:
        responses.get(gnews_links.ARTICLE_PAGE.format(id=gid), body=REDIRECT_PAGE)
    responses.post(gnews_links.BATCH_URL, body=batch_answer(["https://a.it/1", "https://b.it/2", "https://c.it/3"]))
    links = [f"https://news.google.com/rss/articles/{g}?oc=5" for g in ids]
    out = gnews_links.resolve_many(links, requests.Session(), sleep=lambda s: None)
    assert out == dict(zip(links, ["https://a.it/1", "https://b.it/2", "https://c.it/3"]))
    assert sum(1 for c in responses.calls if c.request.method == "POST") == 1
    assert "hl=en-US" in responses.calls[0].request.url


@responses.activate
def test_sorry_page_counts_as_blocked_and_stops_the_rest():
    responses.get(gnews_links.ARTICLE_PAGE.format(id="AAA1"), status=302,
                  headers={"Location": "https://www.google.com/sorry/index"})
    responses.get("https://www.google.com/sorry/index", body="unusual traffic")
    links = [f"https://news.google.com/rss/articles/{g}" for g in ("AAA1", "BBB2")]
    out = gnews_links.resolve_many(links, requests.Session(), sleep=lambda s: None)
    assert all(isinstance(v, gnews_links.GoogleBlocked) for v in out.values())
    assert len(responses.calls) == 2  # the redirect and the sorry page; BBB2 was never asked


@responses.activate
def test_preresolve_fills_the_cache_and_the_stats():
    responses.get(PAGE, body=REDIRECT_PAGE)
    responses.post(gnews_links.BATCH_URL, body=batch_answer([PUBLISHER]))
    assert fulltext.preresolve([GURL, "https://www.ansa.it/x"]) == {GURL: PUBLISHER}
    assert fulltext.google_link_stats()["resolved"] == 1
    responses.get(PUBLISHER, body=(FIX / "article.html").read_text(encoding="utf-8"))
    out = fetch_full_text(gitem(), None)  # uses the cached link, no second look-up
    assert out.canonical_url == PUBLISHER and sum(1 for c in responses.calls if c.request.method == "POST") == 1
