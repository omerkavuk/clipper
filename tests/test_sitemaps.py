from datetime import datetime, timezone

import responses

from clipper.collectors.google_news import GoogleNewsCollector
from clipper.collectors.outlet_feeds import OutletFeedsCollector
from clipper.collectors.sitemaps import SitemapReader, slug_text
from clipper.models import Outlet, RunWindow
from clipper.queries import Query

WINDOW = RunWindow(datetime(2026, 10, 10, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))
Q = [Query("subject", "Subject", [["Aurora Climate Lab"]], []),
     Query("programme:Green Schools", "P", [["Green Schools"]], [])]
SITE = "https://notizie.example"
OUTLET = Outlet("Notizie", "notizie.example", None, "national", None, False)

ROBOTS = "User-agent: *\nDisallow: /admin\nSitemap: https://notizie.example/sitemap_index.xml\n"
INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://notizie.example/sitemap-2026-11.xml</loc></sitemap>
<sitemap><loc>https://notizie.example/sitemap-2026-10.xml</loc></sitemap>
<sitemap><loc>https://notizie.example/sitemap-2025-01.xml</loc></sitemap>
<sitemap><loc>https://notizie.example/sitemap-tags.xml</loc><lastmod>2024-01-01</lastmod></sitemap>
</sitemapindex>"""
NOV = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
 xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://notizie.example/cronaca/green-schools-premiati-a-roma-123</loc>
 <news:news><news:publication_date>2026-11-03T10:00:00+01:00</news:publication_date>
 <news:title>Green Schools, premiati a Roma i progetti</news:title></news:news></url>
<url><loc>https://notizie.example/sport/derby-456</loc><lastmod>2026-11-04</lastmod></url>
</urlset>"""
OCT = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://notizie.example/2026/10/12/aurora-climate-lab-apre-a-milano/</loc></url>
<url><loc>https://notizie.example/2026/09/30/aurora-climate-lab-vecchio/</loc></url>
</urlset>"""
PAGE = '<html><head><meta property="og:title" content="Aurora Climate Lab apre una sede a Milano"></head></html>'


def register():
    responses.get(SITE + "/robots.txt", body=ROBOTS)
    responses.get(SITE + "/sitemap_index.xml", body=INDEX)
    responses.get(SITE + "/sitemap-2026-11.xml", body=NOV)
    responses.get(SITE + "/sitemap-2026-10.xml", body=OCT)
    responses.get(SITE + "/2026/10/12/aurora-climate-lab-apre-a-milano/", body=PAGE)


def test_slug_text():
    assert slug_text("https://a.it/cronaca/green-schools-premiati-a-roma.html") == "green schools premiati a roma"
    assert slug_text("https://a.it/green-schools/123456/") == "green schools"


@responses.activate
def test_reads_only_the_months_in_the_window_and_matches_titles_and_addresses():
    register()
    res = SitemapReader().read(OUTLET, Q, WINDOW, deadline=float("inf"))
    items, oldest, error = res.items, res.oldest, res.error
    assert error is None and res.complete and oldest == datetime(2026, 9, 30, tzinfo=timezone.utc)
    by_url = {i.url: i for i in items}
    assert set(by_url) == {"https://notizie.example/cronaca/green-schools-premiati-a-roma-123",
                           "https://notizie.example/2026/10/12/aurora-climate-lab-apre-a-milano/"}
    news = by_url["https://notizie.example/cronaca/green-schools-premiati-a-roma-123"]
    assert news.title == "Green Schools, premiati a Roma i progetti"
    assert news.published_at == datetime(2026, 11, 3, 9, tzinfo=timezone.utc)
    assert news.matched_queries == ["programme:Green Schools"] and news.source == "sitemap"
    slug = by_url["https://notizie.example/2026/10/12/aurora-climate-lab-apre-a-milano/"]
    assert slug.title == "Aurora Climate Lab apre una sede a Milano"  # read from the page
    fetched = {c.request.url for c in responses.calls}
    assert SITE + "/sitemap-2025-01.xml" not in fetched and SITE + "/sitemap-tags.xml" not in fetched


@responses.activate
def test_site_without_sitemap_says_so():
    responses.get(SITE + "/robots.txt", status=404)
    for guess in ("/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml", "/sitemap-news.xml"):
        responses.get(SITE + guess, status=404)
    res = SitemapReader().read(OUTLET, Q, WINDOW, deadline=float("inf"))
    assert res.items == [] and res.error == "no sitemap found" and not res.complete


@responses.activate
def test_a_sitemap_that_covers_the_outlet_means_no_google_search():
    register()
    gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
    c = OutletFeedsCollector([OUTLET], gn, sleep=lambda s: None, sitemaps=SitemapReader())
    items, statuses = c.collect(Q, WINDOW)
    assert len(items) == 2 and statuses[0].ok and statuses[0].covered_since == WINDOW.since
    assert not any("news.google.com" in c.request.url for c in responses.calls)


@responses.activate
def test_no_sitemap_falls_back_to_one_google_site_search():
    responses.get(SITE + "/robots.txt", status=404)
    for guess in ("/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml", "/sitemap-news.xml"):
        responses.get(SITE + guess, status=404)
    responses.get("https://news.google.com/rss/search", body="<rss><channel></channel></rss>")
    gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
    c = OutletFeedsCollector([OUTLET], gn, sleep=lambda s: None, sitemaps=SitemapReader())
    items, statuses = c.collect(Q, WINDOW)
    google = [c for c in responses.calls if "news.google.com" in c.request.url]
    assert len(google) == len(Q) and "site%3Anotizie.example" in google[0].request.url
    assert statuses[0].ok


def test_list_pages_are_not_articles():
    from clipper.collectors.sitemaps import _is_article
    assert not _is_article("https://a.it/tag/green-schools/", None, None)
    assert not _is_article("https://a.it/argomenti/aurora-climate-lab", "Aurora Climate Lab", None)
    assert not _is_article("https://a.it/nazioni-unite/", None, None)  # a section, not a story
    assert _is_article("https://a.it/cronaca/green-schools-premiati-a-roma", None, None)


@responses.activate
def test_tag_sitemaps_and_files_older_than_the_period_are_skipped():
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://notizie.example/post-sitemap.xml</loc><lastmod>2026-10-01</lastmod></sitemap>
<sitemap><loc>https://notizie.example/post-sitemap9.xml</loc><lastmod>2026-11-13</lastmod></sitemap>
<sitemap><loc>https://notizie.example/post_tag-sitemap.xml</loc><lastmod>2026-11-13</lastmod></sitemap>
</sitemapindex>"""
    newest = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://notizie.example/2026/11/01/green-schools-a-roma/</loc></url>
<url><loc>https://notizie.example/2026/10/01/altro-articolo-vecchio/</loc></url></urlset>"""
    responses.get(SITE + "/robots.txt", body="Sitemap: https://notizie.example/sitemap_index.xml\n")
    responses.get(SITE + "/sitemap_index.xml", body=index)
    responses.get(SITE + "/post-sitemap9.xml", body=newest)
    responses.get(SITE + "/2026/11/01/green-schools-a-roma/", body="<title>Green Schools a Roma</title>")
    res = SitemapReader().read(OUTLET, Q, WINDOW, deadline=float("inf"))
    fetched = [c.request.url for c in responses.calls]
    assert SITE + "/post_tag-sitemap.xml" not in fetched
    assert SITE + "/post-sitemap.xml" not in fetched  # last changed before the period
    assert [i.title for i in res.items] == ["Green Schools a Roma"] and res.error is None


@responses.activate
def test_a_second_shard_for_the_same_period_is_read_too():
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://notizie.example/news-a.xml</loc><lastmod>2026-11-13</lastmod></sitemap>
<sitemap><loc>https://notizie.example/news-b.xml</loc><lastmod>2026-11-13</lastmod></sitemap>
</sitemapindex>"""
    shard_a = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://notizie.example/2026/11/13/meteo-di-oggi-a-roma/</loc></url>
<url><loc>https://notizie.example/2026/10/01/calcio-risultati-della-giornata/</loc></url></urlset>"""
    shard_b = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://notizie.example/2026/10/20/green-schools-premiati-a-roma/</loc></url></urlset>"""
    responses.get(SITE + "/robots.txt", body=ROBOTS)
    responses.get(SITE + "/sitemap_index.xml", body=index)
    responses.get(SITE + "/news-a.xml", body=shard_a)
    responses.get(SITE + "/news-b.xml", body=shard_b)
    responses.get(SITE + "/2026/10/20/green-schools-premiati-a-roma/", body="<title>Green Schools premiati</title>")
    res = SitemapReader().read(OUTLET, Q, WINDOW, deadline=float("inf"))
    assert [i.title for i in res.items] == ["Green Schools premiati"] and res.complete


@responses.activate
def test_a_missing_shard_means_not_covered_so_google_still_searches():
    responses.get(SITE + "/robots.txt", body=ROBOTS)
    responses.get(SITE + "/sitemap_index.xml", body=INDEX)
    responses.get(SITE + "/sitemap-2026-11.xml", body=NOV)
    responses.get(SITE + "/sitemap-2026-10.xml", status=500)  # one month's file fails
    responses.get("https://news.google.com/rss/search", body="<rss><channel></channel></rss>")
    res = SitemapReader().read(OUTLET, Q, WINDOW, deadline=float("inf"))
    assert not res.complete and len(res.items) == 1  # November's article is kept
    gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
    OutletFeedsCollector([OUTLET], gn, sleep=lambda s: None, sitemaps=SitemapReader()).collect(Q, WINDOW)
    assert any("news.google.com" in c.request.url for c in responses.calls)


def test_a_sitemap_crash_keeps_the_feed_results():
    from clipper.collectors.sitemaps import SitemapReader as R

    class Exploding(R):
        def read(self, *a, **k):
            raise TimeoutError("time budget exceeded")

    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Aurora Climate Lab oggi</title><link>https://feed.example/a</link>
<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://feed.example/rss", body=feed)
        rsps.get("https://news.google.com/rss/search", body="<rss><channel></channel></rss>")
        shallow = Outlet("Feed", "feed.example", "https://feed.example/rss", "national", None, False)
        gn = GoogleNewsCollector("IT", "it", [], sleep=lambda s: None)
        items, statuses = OutletFeedsCollector([shallow, OUTLET], gn, sleep=lambda s: None,
                                               sitemaps=Exploding()).collect(Q, WINDOW)
    assert [i.url for i in items if i.source == "outlet_feed"] == ["https://feed.example/a"]
    assert len(statuses) == 2
