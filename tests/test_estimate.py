from datetime import datetime, timedelta, timezone

from clipper.collectors import SourceStatus
from clipper.estimate import estimate
from clipper.estimate import test_queries as run_query_test
from clipper.models import Item, Project

NOW = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)


def project(frequency="weekly", exclude_domains=()):
    return Project(
        name="p", organisation="o", subject=["Acme"], contexts={}, competitors={},
        exclude_terms=[], exclude_domains=list(exclude_domains), programmes=[],
        spokespeople=[], country="IT", languages=["it"], frequency=frequency,
        report_language="en", readers="x", outlets_file="it", folder=None)


def item(n, title, domain="news.it", queries=("subject",), snippet=""):
    return Item(source="fake", outlet=domain, outlet_domain=domain,
                url=f"https://{domain}/a{n}", title=title,
                published_at=NOW - timedelta(days=1), collected_at=NOW,
                snippet=snippet, matched_queries=list(queries))


class Fake:
    name = "fake"

    def __init__(self, items, ok=True):
        self.items, self.ok = items, ok

    def collect(self, queries, window):
        return self.items, [SourceStatus("fake", "t", self.ok, len(self.items),
                                         None if self.ok else "boom", None)]


def run(p, items, **kw):
    return run_query_test(p, collectors=[Fake(items, **kw)], now=NOW)


def test_zero_hit_warning():
    r = run(project(), [])
    assert r["queries"][0]["hits"] == 0
    assert "finds nothing — misspelled, too specific, or another language?" in r["queries"][0]["warnings"]


def test_dominant_term_is_not_called_generic():
    items = [item(i, f"Acme story {i}") for i in range(4)] + [item(9, "Other")]
    r = run(project(), items)
    assert r["total_unique_hits"] == 5
    assert {"term": "Acme", "hits": 4} in r["terms"]
    assert not any("generic" in w for w in r["queries"][0]["warnings"])  # a flagship can dominate


def test_own_website_warning_and_exclusion():
    items = [item(1, "Acme a", domain="www.acme.org"), item(2, "Acme b", domain="acme.org"),
             item(3, "Acme c", domain="news.it")]
    r = run(project(exclude_domains=["acme.org"]), items)
    q = r["queries"][0]
    assert "mostly your own website" in q["warnings"]
    assert q["hits"] == 1 and q["samples"] == ["Acme c"]
    assert r["total_unique_hits"] == 1
    assert abs(r["own_site_share"] - 2 / 3) < 1e-9


def test_social_domains_dropped_and_not_counted_as_own_site():
    items = [item(1, "Acme a", domain="www.instagram.com"), item(2, "Acme b", domain="acme.org"),
             item(3, "Acme c", domain="news.it")]
    r = run(project(exclude_domains=["acme.org"]), items)
    q = r["queries"][0]
    assert q["hits"] == 1 and q["samples"] == ["Acme c"]
    assert r["total_unique_hits"] == 1
    assert abs(r["own_site_share"] - 1 / 2) < 1e-9  # instagram is neither a hit nor own site


def test_unique_failed_sources_and_window():
    r = run(project(), [item(1, "Acme a"), item(1, "Acme a")], ok=False)
    assert r["total_unique_hits"] == 1
    assert r["sources_failed"] == ["fake"]
    assert r["window"]["until"] == NOW.isoformat()


def test_estimate_weekly_numbers():
    e = estimate(project("weekly"), {"total_unique_hits": 70})
    assert e["items_per_run"] == 70
    assert e["per_run"] == 70 * 600 + 8000 + 40 * 250 + 15000 == 75000
    assert e["per_month"] == round(75000 * 4.3)


def test_estimate_periods():
    assert estimate(project("daily"), {"total_unique_hits": 70})["items_per_run"] == 10
    assert estimate(project("monthly"), {"total_unique_hits": 70})["items_per_run"] == 300


def test_caps_from_estimate():
    e = estimate(project("weekly"), {"total_unique_hits": 70})
    assert e["caps"] == {"max_candidates": 140, "max_fulltext": 30, "max_report_items": 40}


def test_max_candidates_floor_on_quiet_week():
    r = run(project(), [])
    assert estimate(project(), r)["caps"]["max_candidates"] == 30


def test_many_hits_warn_even_when_titles_lack_the_term():
    p = project()
    p.programmes = ["VELA"]
    items = [item(i, f"Corsa {i}", queries=("programme:VELA",)) for i in range(120)]
    r = run(p, items)
    q = next(q for q in r["queries"] if q["id"] == "programme:VELA")
    assert any(w.startswith("too generic — 120 results") for w in q["warnings"])


def test_all_sources_failed_is_flagged():
    assert run(project(), [], ok=False)["all_sources_failed"] is True
    assert run(project(), [item(1, "Acme")])["all_sources_failed"] is False
