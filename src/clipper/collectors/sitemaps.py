"""Read an outlet's sitemap: the site's own list of its articles, with dates and often titles.

Almost every news site publishes one (listed in robots.txt), and many reach back months.
That makes it the free way to cover the older weeks a feed has dropped, and sites that
Google News doesn't index. No search engine is involved.

An article counts when its date is in the period and a project search matches its title,
or failing a title, the words in its web address ("…/green-schools-premiati-a-roma").
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass
import io
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests
from lxml import etree

from clipper.models import Item, Outlet, RunWindow
from clipper.queries import Query, matching_ids

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 25                   # some news sites take 10+ seconds per sitemap file
MAX_BYTES = 8_000_000          # per sitemap file, after unzipping
MAX_FILES = 10                 # sitemap files read per outlet
TITLES_PER_OUTLET = 15         # pages opened to read a proper headline when the sitemap has none
GUESSES = ("/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml", "/sitemap-news.xml")
SNIPPET_MAX = 500
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)
_URL_DATE = re.compile(r"/(20\d\d)[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|-|$)")
_META = re.compile(r"<meta\b[^>]*>", re.I)
_CONTENT = re.compile(r'content\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
# Sitemaps of tag, category or author pages, images and the like: never articles.
_NOT_ARTICLES = re.compile(r"tag|categor|author|autor|attachment|image|video|media|product|"
                           r"page-sitemap|pages-sitemap|event|taxonom|topic|argoment", re.I)
# Path parts that mark a list page rather than an article.
_LIST_PARTS = {"tag", "tags", "category", "categories", "categoria", "categorie", "argomento",
               "argomenti", "author", "authors", "autore", "autori", "topic", "topics", "page",
               "pagina", "search", "cerca", "feed"}
_HTML_TITLE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)
_WORDS = re.compile(r"[-_+.]+")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    t = text.strip().replace("Z", "+00:00")
    for candidate in (t, t[:10]):
        try:
            d = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    return None


def _months(window: RunWindow) -> set[str]:
    out, d = set(), window.since.replace(day=1)
    while d <= window.until:
        out.add(f"{d.year}{d.month:02d}")
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def _file_month(url: str) -> str | None:
    """'2026-10', '2026/10', '202610' or 'yyyy=2026&mm=10' in a sitemap file name."""
    m = (re.search(r"(20\d\d)[-/_]?(0[1-9]|1[0-2])(?!\d)", url)
         or re.search(r"yyyy=(20\d\d)\D+mm=(0[1-9]|1[0-2])", url))
    return f"{m.group(1)}{m.group(2)}" if m else None


def month_hint(url: str) -> bool:
    return _file_month(url) is not None


def slug_text(url: str) -> str:
    """The words in an article's web address, for matching when there is no title."""
    path = urlsplit(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]
    last = re.sub(r"\.(s?html?|php|aspx?)$", "", last, flags=re.I)
    if re.fullmatch(r"[\d_-]*", last) and "/" in path:  # ".../green-schools/12345": use the part before
        last = path.rsplit("/", 2)[-2]
    return " ".join(_WORDS.sub(" ", last).split())


@dataclass
class SitemapResult:
    items: list[Item]
    oldest: datetime | None   # the earliest date among the entries read
    error: str | None         # set when nothing usable was read
    complete: bool            # every file that could hold the period was read
    note: str | None = None   # why it is incomplete, when it is


def _entries(root):
    """(address, date, title, date in the address) for each dated entry of a urlset."""
    for el in root:
        if _local(el.tag) != "url":
            continue
        loc = title = None
        published = lastmod = None
        for child in el.iter():
            name = _local(child.tag)
            if name == "loc" and loc is None:
                loc = (child.text or "").strip()
            elif name == "publication_date":
                published = _date(child.text)
            elif name == "lastmod":
                lastmod = _date(child.text)
            elif name == "title" and child.text:
                title = " ".join(child.text.split())
        if not loc:
            continue
        m = _URL_DATE.search(loc)
        url_date = (datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
                    if m else None)
        when = published or url_date or lastmod
        if when is not None:
            yield loc, when, title, url_date


def _is_article(loc: str, title: str | None, url_date) -> bool:
    """Leave out list pages (tags, categories, authors) and bare section pages."""
    parts = [p.lower() for p in urlsplit(loc).path.split("/") if p]
    if not parts or any(p in _LIST_PARTS for p in parts):
        return False
    if title or url_date:
        return True
    return len(slug_text(loc).split()) >= 4  # "nazioni-unite" is a section; articles have longer slugs


class SitemapReader:
    def __init__(self, session: requests.Session | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.session = session or requests.Session()
        self.clock = clock
        self.found_in_robots = False  # set by find(): robots.txt listed the sitemaps

    def _get(self, url: str, deadline: float) -> bytes | None:
        if self.clock() > deadline:
            raise TimeoutError("time budget exceeded")
        resp = self.session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True)
        if resp.status_code != 200:
            return None
        data = b""
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                break
        if data[:2] == b"\x1f\x8b":
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_BYTES + 1)
        return data[:MAX_BYTES]

    def find(self, domain: str, deadline: float) -> list[str]:
        """Sitemap addresses for a site: from robots.txt, else the usual names."""
        base = f"https://{domain}"
        found: list[str] = []
        try:
            robots = self._get(base + "/robots.txt", deadline)
        except TimeoutError:
            raise
        except Exception:
            robots = None
        if robots:
            for line in robots.decode("utf-8", "replace").splitlines():
                if line.lower().startswith("sitemap:"):
                    found.append(line.split(":", 1)[1].strip())
        self.found_in_robots = bool(found)
        return list(dict.fromkeys(found)) or [base + g for g in GUESSES]

    def read(self, outlet: Outlet, queries: list[Query], window: RunWindow,
             deadline: float) -> SitemapResult:
        """Read every sitemap file that can hold the period. Never raises: a failure is reported
        in the result, and whatever was found before it is kept."""
        months = _months(window)
        seen: set[str] = set()
        entries: dict[str, tuple[datetime, str]] = {}
        oldest: datetime | None = None
        files = 0
        complete = True       # every file that could hold the period was read
        problem: str | None = None
        try:
            listed = self.find(outlet.domain, deadline)
            guessed = not self.found_in_robots
            queue = [(u, not guessed) for u in listed]  # (address, must it be read?)
            while queue:
                url, required = queue.pop(0)
                if url in seen:
                    continue
                if files >= MAX_FILES:
                    complete, problem = False, "too many sitemap files; the rest were not read"
                    break
                seen.add(url)
                data = self._get(url, deadline)
                if not data:
                    if required:
                        complete, problem = False, "a sitemap file could not be read"
                    continue
                try:
                    root = etree.fromstring(data, _PARSER)
                except etree.XMLSyntaxError:
                    root = None
                if root is None:
                    if required:
                        complete, problem = False, "a sitemap file could not be read"
                    continue
                files += 1
                if _local(root.tag) == "sitemapindex":
                    queue = [(c, True) for c in self._pick_children(root, url, window, months)] + queue
                    continue
                for loc, when, title, url_date in _entries(root):
                    oldest = when if oldest is None or when < oldest else oldest
                    if window.since <= when < window.until and _is_article(loc, title, url_date):
                        entries[loc] = (when, title or "")
        except TimeoutError:
            complete, problem = False, "time budget exceeded before all sitemap files were read"
        except Exception as e:  # one outlet's sitemap must never stop the run
            complete, problem = False, f"sitemap failed: {type(e).__name__}"
        if files == 0:
            return SitemapResult([], None, problem or "no sitemap found", False)
        try:
            items = self._items(outlet, queries, entries, deadline)
        except Exception as e:
            return SitemapResult([], oldest, f"sitemap failed: {type(e).__name__}", False)
        return SitemapResult(items, oldest, None, complete, problem)

    def _pick_children(self, root, index_url: str, window: RunWindow, months: set[str]) -> list[str]:
        """The child sitemaps likely to hold the period, newest first: by month in the name,
        then news or post files, then other dated files. Tag, category and media files are skipped."""
        scored = []
        for el in root:
            if _local(el.tag) != "sitemap":
                continue
            loc = lastmod = None
            for child in el.iter():
                if _local(child.tag) == "loc":
                    loc = urljoin(index_url, (child.text or "").strip())
                elif _local(child.tag) == "lastmod":
                    lastmod = _date(child.text)
            if not loc or _NOT_ARTICLES.search(urlsplit(loc).path.rsplit("/", 1)[-1]):
                continue
            recent = -(lastmod.timestamp() if lastmod else 0)
            number = re.search(r"(\d+)\.xml(?:\.gz)?$", loc)
            later = -int(number.group(1)) if number and not month_hint(loc) else 0  # post-sitemap19 before post-sitemap2
            month = _file_month(loc)
            if month is not None:
                if month in months:
                    scored.append((0, -int(month), 0, loc))
                continue  # a file for another month
            if lastmod is not None and lastmod < window.since:
                continue  # last changed before the period: nothing new in it
            if re.search(r"news|post|article|articol|notizi", loc, re.I):
                scored.append((1, later, recent, loc))
            elif lastmod is not None:
                scored.append((2, later, recent, loc))
        scored.sort(key=lambda s: (s[0], s[1], s[2]))
        return [loc for *_, loc in scored]

    def _items(self, outlet: Outlet, queries: list[Query], entries: dict[str, tuple[datetime, str]],
               deadline: float) -> list[Item]:
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        titles_read = 0
        for loc, (when, title) in entries.items():
            text = title or slug_text(loc)
            matched = matching_ids(queries, text)
            if not matched:
                continue
            body = None
            if not title and titles_read < TITLES_PER_OUTLET and self.clock() < deadline:
                titles_read += 1
                title, body = self._page(loc, deadline)
                title = title or ""
                if title:
                    matched = matching_ids(queries, title) or matched
            out.append(Item(
                source="sitemap", outlet=outlet.name, outlet_domain=outlet.domain, url=loc,
                title=title or text.capitalize(), published_at=when, collected_at=now,
                snippet=(body or "")[:SNIPPET_MAX], matched_queries=matched,
                # The page was already downloaded for its headline: keep its text, so the
                # full-text step doesn't download it a second time.
                full_text=body, canonical_url=loc if body else None,
            ))
        return out

    def _page(self, url: str, deadline: float) -> tuple[str | None, str | None]:
        """The article's headline and text, from one download."""
        try:
            data = self._get(url, deadline)
        except Exception:
            return None, None
        if not data:
            return None, None
        try:
            import trafilatura
            body = (trafilatura.extract(data, url=url) or "")[:20_000] or None
        except Exception:
            body = None
        return self._title(data), body

    def _title(self, data: bytes) -> str | None:
        page = data[:200_000].decode("utf-8", "replace")
        from html import unescape
        for tag in _META.findall(page):
            if re.search(r"(property|name)\s*=\s*[\"']og:title[\"']", tag, re.I):
                m = _CONTENT.search(tag)
                if m:
                    return " ".join(unescape(m.group(2) or m.group(3) or "").split()) or None
        m = _HTML_TITLE.search(page)
        return " ".join(unescape(m.group(1)).split()) or None if m else None
