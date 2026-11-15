from datetime import datetime, timezone
from pathlib import Path

import requests
import responses

from clipper.fulltext import fetch_full_text
from clipper.models import Item, Outlet

FIX = Path(__file__).parent / "fixtures"
URL = "https://www.example.it/a"


def make_item(url=URL):
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    return Item("feed", "Example", "example.it", url, "Aurora a Roma", now, now)


def make_outlet(paywall=False):
    return Outlet("Example", "example.it", None, "trade", None, paywall)


@responses.activate
def test_extracts_text():
    responses.get(URL, body=(FIX / "article.html").read_text(encoding="utf-8"), status=200)
    item = make_item()
    out = fetch_full_text(item, make_outlet())
    assert "frase principale del corpo" in out.full_text
    assert out.subscribers_only is False
    assert out.canonical_url == URL
    assert item.full_text is None  # input not mutated
    assert out is not item


@responses.activate
def test_paywall_phrase_marks_subscribers_only():
    responses.get(URL, body=(FIX / "paywall.html").read_text(encoding="utf-8"), status=200)
    out = fetch_full_text(make_item(), make_outlet())
    assert out.subscribers_only is True


@responses.activate
def test_login_redirect_marks_subscribers_only():
    responses.get(URL, status=302, headers={"Location": "https://www.example.it/noaccess.html?to=/a"})
    responses.get("https://www.example.it/noaccess.html",
                  body="<html><body><p>Accedi</p></body></html>", status=200)
    out = fetch_full_text(make_item(), make_outlet())
    assert out.subscribers_only is True
    assert out.canonical_url.startswith("https://www.example.it/noaccess.html")


@responses.activate
def test_network_error_returns_item_unchanged():
    responses.get(URL, body=requests.ConnectionError("boom"))
    item = make_item()
    out = fetch_full_text(item, make_outlet(paywall=True))
    assert out.full_text is None
    assert out.subscribers_only is False
    assert out.canonical_url is None


@responses.activate
def test_paywall_outlet_short_text_is_subscribers_only():
    responses.get(URL, body="<html><body><p>Breve.</p></body></html>", status=200)
    assert fetch_full_text(make_item(), make_outlet(paywall=True)).subscribers_only is True


@responses.activate
def test_forbidden_on_paywall_outlet_marks_subscribers_only():
    responses.get(URL, status=403)
    out = fetch_full_text(make_item(), make_outlet(paywall=True))
    assert out.subscribers_only is True
    assert out.full_text is None
    assert fetch_full_text(make_item(), make_outlet(paywall=False)).subscribers_only is False


@responses.activate
def test_unresolved_google_news_url_keeps_paywall_flag():
    from clipper import fulltext
    fulltext.reset_google_state()
    gurl = "https://news.google.com/rss/articles/X1"
    responses.get("https://news.google.com/rss/articles/X1", body="<html></html>")
    item = make_item(gurl)
    out = fetch_full_text(item, make_outlet(paywall=True))
    assert len(responses.calls) == 1  # only the redirect page; no article fetched
    assert out.subscribers_only is False
    assert out.full_text is None and out.canonical_url is None
    assert out is not item


@responses.activate
def test_redirect_to_another_host_is_not_canonical():
    responses.get(URL, status=302, headers={"Location": "https://consent.google.com/ml?continue=x"})
    responses.get("https://consent.google.com/ml",
                  body=(FIX / "article.html").read_text(encoding="utf-8"), status=200)
    out = fetch_full_text(make_item(), make_outlet())
    assert out.canonical_url is None


@responses.activate
def test_subdomain_of_outlet_is_canonical():
    final = "https://roma.example.it/a"
    responses.get(URL, status=301, headers={"Location": final})
    responses.get(final, body=(FIX / "article.html").read_text(encoding="utf-8"), status=200)
    assert fetch_full_text(make_item(), make_outlet()).canonical_url == final


@responses.activate
def test_undeclared_utf8_page_keeps_accents():
    body = (FIX / "article.html").read_text(encoding="utf-8")
    body = body.replace("frase principale del corpo", "frase principale della città")
    body = body.replace('<meta charset="utf-8">', "").replace("charset=utf-8", "")
    assert "città" in body and "charset" not in body.lower()
    responses.get(URL, body=body.encode("utf-8"), status=200, content_type="text/html")
    out = fetch_full_text(make_item(), make_outlet())
    assert "città" in out.full_text and "cittÃ" not in out.full_text
