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

from clipper.collectors import SourceStatus, summarize_jobs
from clipper.models import Item, RunWindow
from clipper.queries import Query, matching_ids, to_google_news

BASE_URL = "https://www.bing.com/news/search"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
PAUSE_SECONDS = 1.5
BUDGET_SECONDS = 60          # for one week in one language; more languages and periods get more
MAX_BUDGET_SECONDS = 240
LONG_WINDOW_DAYS = 8         # beyond this, also ask for the most relevant results, not only the newest
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

    def __init__(self, country: str, language: str | list[str],
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 progress: Callable[[str], None] | None = None):
        self.country = country.upper()
        self.languages = [language] if isinstance(language, str) else list(language) or ["en"]
        self.language = self.languages[0]
        self.incomplete_from = None  # set when a search failed (retried from here next run)
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.progress = progress

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: dict[str, Item] = {}
        long_window = (window.until - window.since).days >= LONG_WINDOW_DAYS
        orders = [True, False] if long_window else [True]  # newest first, then most relevant
        jobs = [(q, lang, newest) for q in queries for lang in self.languages for newest in orders]
        budget = min(MAX_BUDGET_SECONDS, BUDGET_SECONDS * len(self.languages) * len(orders))
        tally = {q.id: {"done": [], "failed": [], "items": 0} for q in queries}
        start = self.clock()
        blocked = False

        def fail(t, lang, newest, why):
            t["failed"].append((f"{lang}, {'newest' if newest else 'most relevant'}", why, window.since))
            self.incomplete_from = window.since

        for n, (q, lang, newest) in enumerate(jobs):
            t = tally[q.id]
            if blocked:
                fail(t, lang, newest, "asked to slow down, skipped")
                continue
            if self.clock() - start + (PAUSE_SECONDS if n else 0) > budget:
                fail(t, lang, newest, "time budget exceeded, skipped")
                continue
            if n:
                self.sleep(PAUSE_SECONDS)
            if self.progress and (n % 10 == 0 or n == len(jobs) - 1):
                self.progress(f"Bing News: {n + 1}/{len(jobs)} searches")
            try:
                got = self._fetch(q, window, queries, lang, newest)
            except _Blocked:
                blocked = True
                fail(t, lang, newest, "asked to slow down")
                continue
            except Exception as e:  # one failing search must not stop the run
                fail(t, lang, newest, str(e))
                continue
            t["done"].append(lang)
            for it in got:
                kept = items.setdefault(it.url, it)
                if kept is not it:
                    kept.matched_queries = list(dict.fromkeys(kept.matched_queries + it.matched_queries))
                else:
                    t["items"] += 1
        statuses = [summarize_jobs(self.name, q.id, tally[q.id]["done"], tally[q.id]["failed"],
                                   tally[q.id]["items"], window) for q in queries]
        return list(items.values()), statuses

    def _fetch(self, q: Query, window: RunWindow, queries: list[Query],
               language: str | None = None, newest: bool = True) -> list[Item]:
        language = language or self.language
        params = {"q": to_google_news(q, []), "format": "rss", "cc": self.country,
                  "setlang": f"{language}-{self.country}"}
        if newest:
            params["qft"] = 'sortbydate="1"'
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
                language=language, matched_queries=matched,
            ))
        return out
