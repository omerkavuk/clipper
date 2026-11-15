from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import responses

from clipper.collectors.bing_news import BASE_URL, BingNewsCollector, unwrap
from clipper.models import RunWindow
from clipper.queries import Query

W = RunWindow(datetime(2026, 11, 7, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))
Q = Query("programme:Green Schools", "P", [["Green Schools"]], ["tirocinio"])
Q2 = Query("subject", "S", [["Aurora Climate Lab"]], [])
LINK = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=x&amp;"
        "url=https%3a%2f%2fmediakey.it%2fnews%2fbus&amp;c=1")
FEED = f"""<?xml version="1.0"?><rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss" version="2.0">
<channel><title>x</title>
<item><title>Un bus per il clima: a Lumio &lt;b&gt;Green Schools&lt;/b&gt;</title><link>{LINK}</link>
<description>I studenti ricercatori a Lumio.</description><pubDate>Thu, 12 Nov 2026 00:46:00 GMT</pubDate>
<News:Source>mediakey.it</News:Source></item>
<item><title>Old</title><link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fold.it%2fa</link>
<pubDate>Thu, 01 Jan 2026 00:46:00 GMT</pubDate></item>
</channel></rss>"""


def make():
    return BingNewsCollector("IT", "it", sleep=lambda s: None)


def test_unwrap_gives_publisher_url():
    assert unwrap("http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fa.it%2fx&c=1") == "https://a.it/x"
    assert unwrap("https://a.it/y") == "https://a.it/y"


@responses.activate
def test_items_have_publisher_link_outlet_and_window_filter():
    responses.get(BASE_URL, body=FEED)
    items, statuses = make().collect([Q], W)
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://mediakey.it/news/bus" and it.outlet_domain == "mediakey.it"
    assert it.outlet == "mediakey.it" and it.title == "Un bus per il clima: a Lumio Green Schools"
    assert it.source == "bing_news" and statuses[0].ok
    p = {k: v[0] for k, v in parse_qs(urlparse(responses.calls[0].request.url).query).items()}
    assert p["q"] == '"Green Schools" -tirocinio' and p["cc"] == "IT" and p["setlang"] == "it-IT"


@responses.activate
def test_slow_down_stops_later_queries():
    responses.get(BASE_URL, status=429)
    _, statuses = make().collect([Q, Q2], W)
    assert len(responses.calls) == 1 and statuses[1].error == "asked to slow down, skipped"


def test_bing_is_a_default_source_and_can_be_skipped(example_project):
    from clipper import pipeline
    names = [type(c).__name__ for c in pipeline._default_collectors(example_project)]
    assert names[:3] == ["OutletFeedsCollector", "GoogleNewsCollector", "BingNewsCollector"]
    names = [type(c).__name__ for c in pipeline._default_collectors(example_project, skip=("bing_news",))]
    assert "BingNewsCollector" not in names


@responses.activate
def test_results_that_do_not_name_the_search_terms_are_dropped():
    noisy = FEED.replace("Un bus per il clima: a Lumio &lt;b&gt;Green Schools&lt;/b&gt;", "Pacco sospetto a Roma").replace(
        "I studenti ricercatori a Lumio.", "Evacuata la scuola.")
    responses.get(BASE_URL, body=noisy)
    items, statuses = make().collect([Q], W)
    assert items == [] and statuses[0].ok
