from datetime import datetime, timezone

import responses

from clipper.collectors.google_news import GoogleNewsCollector
from clipper.collectors.outlet_feeds import OutletFeedsCollector
from clipper.models import Outlet, RunWindow
from clipper.queries import Query

GNEWS = "https://news.google.com/rss/search"
FEED_URL = "https://www.example.it/feed/"
Q = Query("subject", "Subject", [["Aurora"]], [])
WINDOW = RunWindow(datetime(2026, 10, 14, tzinfo=timezone.utc),
                   datetime(2026, 11, 14, tzinfo=timezone.utc))

FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Clima: Aurora a Roma</title><link>https://www.example.it/a</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Il &lt;b&gt;premio&lt;/b&gt; dell'Aurora&lt;/p&gt;</description></item>
<item><title>Aurora, notizia vecchia</title><link>https://www.example.it/b</link>
<pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate><description>old</description></item>
<item><title>Calcio: derby</title><link>https://www.example.it/c</link>
<pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate><description>sport</description></item>
</channel></rss>"""

GNEWS_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>g</title>
<item><title>Aurora news - Some Name</title><link>https://news.google.com/rss/articles/X1</link>
<pubDate>Thu, 22 Oct 2026 10:00:00 GMT</pubDate>
<source url="https://www.example.it">Some Name</source></item></channel></rss>"""


def outlet(feed=FEED_URL):
    return Outlet("Example", "example.it", feed, "trade", None, False)


def make(outlets):
    gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
    return OutletFeedsCollector(outlets, gn, sleep=lambda s: None)


@responses.activate
def test_feed_items_filtered_by_query_and_window():
    responses.add(responses.GET, FEED_URL, body=FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert len(items) == 1
    it = items[0]
    assert it.source == "outlet_feed" and it.outlet == "Example" and it.outlet_domain == "example.it"
    assert it.url == "https://www.example.it/a"
    assert it.snippet == "Il premio dell'Aurora"
    assert it.matched_queries == ["subject"]
    assert it.subscribers_only is False
    assert len(statuses) == 1
    assert statuses[0].source == "outlet" and statuses[0].target == "example.it"
    assert statuses[0].ok and statuses[0].items == 1


@responses.activate
def test_covered_since_recorded():
    responses.add(responses.GET, FEED_URL, body=FEED)
    _, statuses = make([outlet()]).collect([Q], WINDOW)
    assert statuses[0].covered_since == datetime(2026, 7, 1, 10, tzinfo=timezone.utc)


@responses.activate
def test_feed_404_falls_back_to_site_query():
    responses.add(responses.GET, FEED_URL, status=404)
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    gnews_call = responses.calls[1].request
    assert "site%3Aexample.it" in gnews_call.url or "site:example.it" in gnews_call.url
    assert len(items) == 1
    assert items[0].source == "google_news" and items[0].outlet == "Example"
    assert len(statuses) == 1
    assert statuses[0].source == "outlet" and statuses[0].ok and statuses[0].items == 1
    assert statuses[0].covered_since == WINDOW.since and statuses[0].error is None


@responses.activate
def test_no_feed_goes_straight_to_site_query():
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    items, statuses = make([outlet(feed=None)]).collect([Q], WINDOW)
    assert len(responses.calls) == 1 and len(items) == 1 and statuses[0].ok


@responses.activate
def test_unparseable_feed_falls_back():
    responses.add(responses.GET, FEED_URL, body="<html><body>not a feed</body></html>")
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert len(items) == 1 and statuses[0].ok


@responses.activate
def test_failed_feed_and_failed_fallback_is_one_failed_status():
    responses.add(responses.GET, FEED_URL, status=404)
    responses.add(responses.GET, GNEWS, status=503)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert items == [] and len(statuses) == 1
    assert not statuses[0].ok and "feed failed" in statuses[0].error
    assert statuses[0].covered_since is None


@responses.activate
def test_each_outlet_gets_one_status_in_list_order():
    other = Outlet("Other", "other.it", "https://other.it/feed", "trade", None, False)
    responses.add(responses.GET, FEED_URL, body=FEED)
    responses.add(responses.GET, "https://other.it/feed", body=FEED)
    pauses = []
    gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
    coll = OutletFeedsCollector([outlet(), other], gn, sleep=pauses.append)
    items, statuses = coll.collect([Q], WINDOW)
    assert [s.target for s in statuses] == ["example.it", "other.it"]
    assert len(items) == 2 and pauses == []  # feeds are read in parallel, no pauses


UNDATED_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Aurora a Roma</title><link>https://www.example.it/u1</link></item>
<item><title>Aurora, altro</title><link>https://www.example.it/u2</link></item>
</channel></rss>"""

NO_LINK_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Aurora senza link</title>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Aurora con link</title><link>https://www.example.it/l</link>
<pubDate>Tue, 20 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


@responses.activate
def test_undated_feed_falls_back_to_site_query():
    responses.add(responses.GET, FEED_URL, body=UNDATED_FEED)
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert len(responses.calls) == 2
    assert len(items) == 1 and items[0].source == "google_news"
    assert statuses[0].ok and statuses[0].covered_since == WINDOW.since


@responses.activate
def test_feed_entries_without_link_are_skipped():
    responses.add(responses.GET, FEED_URL, body=NO_LINK_FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert [i.url for i in items] == ["https://www.example.it/l"]
    assert statuses[0].ok and statuses[0].items == 1


@responses.activate
def test_outlets_without_feed_share_one_site_search_per_query():
    batch = [Outlet(f"O{n}", f"o{n}.it", None, "trade", None, False) for n in range(3)]
    body = GNEWS_FEED.replace("https://www.example.it", "https://www.o1.it")
    responses.add(responses.GET, GNEWS, body=body)
    items, statuses = make(batch).collect([Q], WINDOW)
    assert len(responses.calls) == 1
    url = responses.calls[0].request.url
    assert "site%3Ao0.it+OR+site%3Ao1.it+OR+site%3Ao2.it" in url
    assert [s.target for s in statuses] == ["o0.it", "o1.it", "o2.it"]
    assert all(s.ok for s in statuses) and [s.items for s in statuses] == [0, 1, 0]
    assert len(items) == 1 and items[0].outlet == "O1"


@responses.activate
def test_site_search_result_from_another_domain_is_dropped():
    batch = [Outlet("A", "a.it", None, "trade", None, False),
             Outlet("B", "b.it", None, "trade", None, False)]
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)  # source is example.it, not in the batch
    items, statuses = make(batch).collect([Q], WINDOW)
    assert items == [] and all(s.ok for s in statuses)


@responses.activate
def test_eleven_outlets_make_two_site_searches():
    batch = [Outlet(f"O{n}", f"o{n}.it", None, "trade", None, False) for n in range(11)]
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    make(batch).collect([Q], WINDOW)
    assert len(responses.calls) == 2


SHALLOW_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Aurora oggi</title><link>https://www.example.it/new</link>
<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate><description>Aurora</description></item>
</channel></rss>"""


@responses.activate
def test_shallow_feed_gets_a_site_search_for_older_weeks():
    from urllib.parse import parse_qs, urlparse
    responses.add(responses.GET, FEED_URL, body=SHALLOW_FEED)
    responses.add(responses.GET, GNEWS, body=GNEWS_FEED)
    items, statuses = make([outlet()]).collect([Q], WINDOW)
    assert sorted(i.url for i in items) == ["https://news.google.com/rss/articles/X1",
                                            "https://www.example.it/new"]
    q = parse_qs(urlparse(responses.calls[1].request.url).query)["q"][0]
    assert "site:example.it" in q and "before:2026-11-13" in q  # only the part the feed misses
    assert statuses[0].covered_since == WINDOW.since and statuses[0].items == 2


@responses.activate
def test_deep_feed_needs_no_site_search():
    responses.add(responses.GET, FEED_URL, body=FEED)
    make([outlet()]).collect([Q], WINDOW)
    assert len(responses.calls) == 1


BODY_FEED = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>x</title>
<item><title>Nuovi fondi per il clima</title><link>https://www.example.it/d</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate><description>Breve</description>
<content:encoded><![CDATA[<p>Il progetto di <b>Aurora</b> a Torino.</p>]]></content:encoded></item>
<item><title>Calcio: derby</title><link>https://www.example.it/e</link>
<pubDate>Tue, 20 Oct 2026 11:00:00 GMT</pubDate><description>sport</description>
<content:encoded><![CDATA[<p>Aurora calcio</p>]]></content:encoded></item>
<item><title>Aurora fuori periodo</title><link>https://www.example.it/f</link>
<pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate><description>old</description></item>
</channel></rss>"""


@responses.activate
def test_mention_only_in_the_body_is_found():
    responses.add(responses.GET, FEED_URL, body=BODY_FEED)
    q = Query("subject", "Subject", [["Aurora"]], ["calcio"])
    items, _ = make([outlet()]).collect([q], WINDOW)
    assert [i.url for i in items] == ["https://www.example.it/d"]
    assert items[0].snippet == "Breve"
