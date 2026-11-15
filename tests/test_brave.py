from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from clipper.collectors import brave
from clipper.collectors.brave import BASE_URL, BraveNewsCollector
from clipper.models import RunWindow
from clipper.queries import Query

WINDOW = RunWindow(datetime(2026, 10, 14, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))
SUBJECT = Query("subject", "S", [["Aurora Climate Lab", "Laboratorio Clima Aurora"]], ["tirocinio"])
PROG = Query("programme:Green Schools", "P", [["Green Schools"]], [])


def result(url, age, title="Green Schools a Roma", host=None, name="La Testata"):
    return {"type": "news_result", "url": url, "title": title, "description": "<strong>Aurora</strong> e Comune di Lumio",
            "page_age": age, "profile": {"name": name}, "meta_url": {"hostname": host or urlparse(url).hostname}}


def make(**kw):
    return BraveNewsCollector("IT", ["it"], "secret", sleep=lambda s: None, **kw)


def params(call):
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


@responses.activate
def test_request_has_country_language_dates_and_key_header():
    responses.get(BASE_URL, json={"results": []})
    make().collect([SUBJECT], WINDOW)
    call = responses.calls[0]
    p = params(call)
    assert p["country"] == "IT" and p["search_lang"] == "it" and p["count"] == "50"
    assert p["freshness"] == "2026-10-14to2026-11-13"
    assert p["q"] == '("Aurora Climate Lab" OR "Laboratorio Clima Aurora") -tirocinio'
    assert call.request.headers["X-Subscription-Token"] == "secret"
    assert "secret" not in call.request.url


@responses.activate
def test_results_parsed_and_kept_only_when_published_in_window():
    responses.get(BASE_URL, json={"results": [
        result("https://www.lastampa.it/a", "2026-10-22T09:00:00"),
        result("https://www.ansa.it/old", "2025-01-01T09:00:00"),      # modified in window, old
        result("https://www.ansa.it/nodate", None)]})
    items, statuses = make().collect([PROG], WINDOW)
    assert [i.url for i in items] == ["https://www.lastampa.it/a"]
    it = items[0]
    assert it.source == "brave" and it.outlet == "La Testata" and it.outlet_domain == "lastampa.it"
    assert it.snippet == "Aurora e Comune di Lumio" and it.matched_queries == ["programme:Green Schools"]
    assert statuses[0].ok and statuses[0].items == 1


@responses.activate
def test_same_article_from_two_queries_is_one_item_with_both_ids():
    responses.get(BASE_URL, json={"results": [result("https://www.lastampa.it/a", "2026-10-22T09:00:00")]})
    items, _ = make().collect([SUBJECT, PROG], WINDOW)
    assert len(items) == 1 and items[0].matched_queries == ["subject", "programme:Green Schools"]


@responses.activate
def test_unsupported_country_falls_back_to_worldwide_once():
    responses.get(BASE_URL, status=422, body='{"error": {"detail": "country: invalid value"}}')
    responses.get(BASE_URL, json={"results": []})
    responses.get(BASE_URL, json={"results": []})
    c = BraveNewsCollector("KE", ["en"], "k", sleep=lambda s: None)
    c.collect([SUBJECT, PROG], WINDOW)
    sent = [params(call)["country"] for call in responses.calls]
    assert sent == ["KE", "ALL", "ALL"]


@responses.activate
@pytest.mark.parametrize("status, reason", [(401, "Brave key not accepted"), (429, "Brave asked to slow down")])
def test_bad_key_or_rate_limit_stops_later_searches(status, reason):
    responses.get(BASE_URL, status=status)
    _, statuses = make().collect([SUBJECT, PROG], WINDOW)
    assert len(responses.calls) == 1
    assert statuses[0].error == reason and statuses[1].error == f"{reason}, skipped"


@responses.activate
def test_one_search_per_language():
    responses.get(BASE_URL, json={"results": []})
    BraveNewsCollector("IT", ["it", "en"], "k", sleep=lambda s: None).collect([PROG], WINDOW)
    assert [params(c)["search_lang"] for c in responses.calls] == ["it", "en"]


def test_key_from_env_or_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert brave.brave_key() is None
    (tmp_path / ".clipper").mkdir(exist_ok=True)
    (tmp_path / ".clipper" / "brave-key.txt").write_text("﻿  abc123 \n", encoding="utf-8")
    assert brave.brave_key() == "abc123"
    monkeypatch.setenv("BRAVE_API_KEY", "fromenv")
    assert brave.brave_key() == "fromenv"


def test_brave_only_joins_when_a_key_exists(monkeypatch, example_project):
    from clipper import pipeline
    kinds = lambda: [type(c).__name__ for c in pipeline._default_collectors(example_project)]
    assert "BraveNewsCollector" not in kinds()
    monkeypatch.setattr("clipper.pipeline.brave_key", lambda: "k")
    assert kinds()[3] == "BraveNewsCollector"


def test_skip_switches_sources_off(monkeypatch, example_project):
    from clipper import pipeline
    monkeypatch.setattr("clipper.pipeline.brave_key", lambda: "k")
    cs = pipeline._default_collectors(example_project, skip=("google_news", "gdelt"))
    names = [type(c).__name__ for c in cs]
    assert names == ["OutletFeedsCollector", "BingNewsCollector", "BraveNewsCollector"]
    assert cs[0].gnews.off_reason == "switched off for this run"
    with pytest.raises(ValueError, match="unknown source"):
        pipeline._default_collectors(example_project, skip=("bing",))
