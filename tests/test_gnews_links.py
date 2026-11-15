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
    assert gnews_links.resolve(GURL, requests.Session()) == PUBLISHER
    sent = responses.calls[1].request.body
    assert "garturlreq" in requests.utils.unquote(sent) and "SIG123" in requests.utils.unquote(sent)


@responses.activate
def test_resolve_without_signature_returns_none():
    responses.get(PAGE, body="<html>nothing</html>")
    assert gnews_links.resolve(GURL, requests.Session()) is None


@responses.activate
def test_resolve_raises_when_blocked():
    responses.get(PAGE, status=429)
    with pytest.raises(gnews_links.GoogleBlocked):
        gnews_links.resolve(GURL, requests.Session())


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
