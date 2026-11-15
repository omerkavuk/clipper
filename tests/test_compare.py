import json
from datetime import datetime, timezone

import pytest

from clipper.collectors import SourceStatus
from clipper.compare import compare, score
from clipper.models import Item, RunWindow

W = RunWindow(datetime(2026, 10, 14, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))
WHEN = datetime(2026, 10, 22, tzinfo=timezone.utc)


def it(domain, title, url):
    return Item("x", domain, domain, url, title, WHEN, WHEN, matched_queries=["subject"])


class Route:
    def __init__(self, items, ok=True):
        self.items, self.ok = items, ok

    def collect(self, queries, window):
        return self.items, [SourceStatus("x", "q", self.ok, len(self.items), None if self.ok else "boom", None)]


def tag(i, relevant=True):
    return {"id": i, "relevant": relevant, "type": "news", "tone": "neutral", "tone_reason": "",
            "naming": "explicit", "related_to": "other", "summary": "s"}


def test_same_article_matched_by_domain_and_headline_across_routes(tmp_path, example_project):
    google = Route([it("ansa.it", "Green Schools, premiati 40 studenti ricercatori", "https://news.google.com/rss/articles/A"),
                    it("agi.it", "Clima: Comune di Lumio e Aurora premiano i studenti", "https://news.google.com/rss/articles/B")])
    brave = Route([it("ansa.it", "Green Schools, premiati 40 studenti ricercatori", "https://www.ansa.it/a.html"),
                   it("blog.example.com", "Aurora Climate Lab at COP31", "https://blog.example.com/p"),
                   it("instagram.com", "Aurora post", "https://instagram.com/p/1")])
    res = compare(example_project, W, {"google_news": google, "brave": brave}, tmp_path)
    assert res["articles"] == 3 and res["routes"]["brave"]["found"] == 2  # social profile excluded
    arts = json.loads((tmp_path / "articles.json").read_text(encoding="utf-8"))["items"]
    ansa = next(a for a in arts if a["domain"] == "ansa.it")
    assert ansa["routes"] == ["google_news", "brave"] and ansa["url"] == "https://www.ansa.it/a.html"

    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"items": [tag(a["id"], relevant=a["domain"] != "agi.it") for a in arts]}),
                    encoding="utf-8")
    s = score(example_project, tmp_path / "articles.json", tags)
    assert s["relevant_found_by"] == {"brave": 2, "google_news": 1}
    assert s["breakdown"]["both"]["listed_outlet"] == 1
    assert s["breakdown"]["only_brave"]["outside_or_unknown"] == 1
    assert s["irrelevant"] == 1 and "only_google_news" not in s["breakdown"]


def test_score_refuses_untagged_articles(tmp_path, example_project):
    compare(example_project, W, {"brave": Route([it("ansa.it", "T", "https://www.ansa.it/a")])}, tmp_path)
    tags = tmp_path / "tags.json"
    tags.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="no valid tag"):
        score(example_project, tmp_path / "articles.json", tags)
