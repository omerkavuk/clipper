"""Bing News search (free RSS, no key): a second news route, so a Google block is not fatal.

Bing's feed links to the publisher's own article (inside a click-tracking link, which is
unwrapped here), with the outlet name, a summary and the publication time.
"""
from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qs, urlparse

import feedparser
import requests

from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import Query, matching_ids, to_google_news

BASE_URL = "https://www.bing.com/news/search"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
PAUSE_SECONDS = 1.5
BUDGET_SECONDS = 60
BLOCKED_STATUSES = (429, 503)
SNIPPET_MAX = 500
_TAGS = re.compile(r"<[^>]+>")


class _Blocked(Exception):
    pass


def unwrap(link: str) -> str:
    """The publisher URL inside Bing's apiclick link (or the link itself)."""
    parsed = urlparse(link)
    if parsed.hostname and parsed.hostname.endswith("bing.com"):
        inner = parse_qs(parsed.query).get("url", [""])[0]
        if inner.startswith("http"):
            return inner
    return link


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class BingNewsCollector:
    name = "bing_news"

    def __init__(self, country: str, language: str,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 progress: Callable[[str], None] | None = None):
        self.country = country.upper()
        self.language = language
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.progress = progress

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: dict[str, Item] = {}
        statuses: list[SourceStatus] = []
        start = self.clock()
        blocked = False
        for n, q in enumerate(queries):
            if blocked:
                statuses.append(SourceStatus(self.name, q.id, False, 0, "asked to slow down, skipped", None))
                continue
            if self.clock() - start + (PAUSE_SECONDS if n else 0) > BUDGET_SECONDS:
                statuses.append(SourceStatus(self.name, q.id, False, 0, "time budget exceeded, skipped", None))
                continue
            if n:
                self.sleep(PAUSE_SECONDS)
            if self.progress:
                self.progress(f"Bing News: {n + 1}/{len(queries)} queries")
            try:
                got = self._fetch(q, window, queries)
            except _Blocked:
                blocked = True
                statuses.append(SourceStatus(self.name, q.id, False, 0, "asked to slow down", None))
                continue
            except Exception as e:  # one failing query must not stop the run
                statuses.append(SourceStatus(self.name, q.id, False, 0, str(e), None))
                continue
            for it in got:
                kept = items.setdefault(it.url, it)
                if kept is not it and q.id not in kept.matched_queries:
                    kept.matched_queries.append(q.id)
            statuses.append(SourceStatus(self.name, q.id, True, len(got), None, window.since))
        return list(items.values()), statuses

    def _fetch(self, q: Query, window: RunWindow, queries: list[Query]) -> list[Item]:
        params = {"q": to_google_news(q, []), "format": "rss", "cc": self.country,
                  "setlang": f"{self.language}-{self.country}", "qft": 'sortbydate="1"'}
        resp = self.session.get(BASE_URL, params=params, timeout=TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
        if resp.status_code in BLOCKED_STATUSES:
            raise _Blocked()
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise ValueError("unparseable response (not a valid feed)")
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        for e in feed.entries:
            parsed = e.get("published_parsed")
            link = unwrap(e.get("link", ""))
            if not parsed or not link:
                continue
            published = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            if not (window.since <= published < window.until):
                continue
            domain = _domain(link)
            title = " ".join(_TAGS.sub("", e.get("title", "")).split())
            snippet = " ".join(_TAGS.sub("", e.get("summary", "")).split())[:SNIPPET_MAX]
            # Bing loosens quoted phrases; keep only results that really name what we search for.
            matched = matching_ids(queries, f"{title} {snippet}")
            if not matched:
                continue
            out.append(Item(
                source=self.name, outlet=(e.get("news_source") or "").strip() or domain,
                outlet_domain=domain, url=link,
                title=title, published_at=published, collected_at=now, snippet=snippet,
                language=self.language, matched_queries=matched,
            ))
        return out
