from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

import feedparser
import requests

from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import Query, to_google_news

BASE_URL = "https://news.google.com/rss/search"
TIMEOUT = 15
PAUSE_SECONDS = 1
SEARCH_BUDGET_SECONDS = 90  # the broad search; site searches get their own budget
BLOCKED_STATUSES = (429, 503)  # Google's "slow down": stop asking for the rest of the run


class Blocked(Exception):
    pass


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


class GoogleNewsCollector:
    name = "google_news"

    def __init__(self, country: str, language: str, exclude_domains: list[str],
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 progress: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.progress = progress
        self.clock = clock
        self.blocked = False  # set on the first 429/503; shared by every later call
        self.off_reason: str | None = None
        self.country = country.upper()
        self.language = language
        self.exclude_domains = exclude_domains
        self.session = session or requests.Session()
        self.sleep = sleep

    def site_query(self, q: Query, site: str | list[str]) -> str:
        sites = [site] if isinstance(site, str) else list(site)
        restrict = (f"site:{sites[0]}" if len(sites) == 1
                    else "(" + " OR ".join(f"site:{d}" for d in sites) + ")")
        return f"{to_google_news(q, self.exclude_domains)} {restrict}"

    def collect(self, queries: list[Query], window: RunWindow,
                site: str | list[str] | None = None,
                budget: float | None = None) -> tuple[list[Item], list[SourceStatus]]:
        """Search each query; `site` restricts to one or more domains; `budget` caps seconds."""
        items: list[Item] = []
        statuses: list[SourceStatus] = []
        if budget is None and site is None:
            budget = SEARCH_BUDGET_SECONDS
        start = self.clock()
        target_site = site if isinstance(site, str) or site is None else ",".join(site)
        for n, q in enumerate(queries):
            target = target_site or q.id
            if self.off_reason:
                statuses.append(SourceStatus(self.name, target, False, 0, self.off_reason, None))
                continue
            if self.blocked:
                statuses.append(SourceStatus(self.name, target, False, 0,
                                             "blocked by Google, skipped", None))
                continue
            if budget is not None and self.clock() - start + (PAUSE_SECONDS if n else 0) > budget:
                statuses.append(SourceStatus(self.name, target, False, 0,
                                             "time budget exceeded, skipped", None))
                continue
            if n:
                self.sleep(PAUSE_SECONDS)
            if self.progress and not site:  # per-outlet fallback searches would be noise
                self.progress(f"Google News: {n + 1}/{len(queries)} queries")
            try:
                got = self._fetch(q, window, site)
            except Blocked:
                self.blocked = True
                if self.progress:
                    self.progress("Google News: asked to slow down, skipping remaining searches")
                statuses.append(SourceStatus(self.name, target, False, 0, "blocked by Google", None))
                continue
            except Exception as e:  # one failing query must not stop the run
                statuses.append(SourceStatus(self.name, target, False, 0, str(e), None))
                continue
            items.extend(got)
            statuses.append(SourceStatus(self.name, target, True, len(got), None, window.since))
        return items, statuses

    def _fetch(self, q: Query, window: RunWindow, site: str | list[str] | None) -> list[Item]:
        text = self.site_query(q, site) if site else to_google_news(q, self.exclude_domains)
        after = window.since.date().isoformat()
        before = (window.until + timedelta(days=1)).date().isoformat()
        params = {
            "q": f"{text} after:{after} before:{before}",
            "hl": self.language,
            "gl": self.country,
            "ceid": f"{self.country}:{self.language}",
        }
        resp = self.session.get(BASE_URL, params=params, timeout=TIMEOUT)
        if resp.status_code in BLOCKED_STATUSES:
            raise Blocked()
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise ValueError("unparseable response (not a valid feed)")
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        for e in feed.entries:
            parsed = e.get("published_parsed")
            if not parsed:
                continue
            published = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            if not (window.since <= published < window.until):
                continue
            src = e.get("source") or {}
            link = e.get("link", "")
            outlet_text = (src.get("title") or "").strip()
            domain = _domain(src.get("href", "")) or _domain(link)
            title = e.get("title", "")
            suffix = f" - {outlet_text}"
            if outlet_text and title.endswith(suffix):
                title = title[: -len(suffix)]
            out.append(Item(
                source=self.name,
                outlet=outlet_text or domain,
                outlet_domain=domain,
                url=link,
                title=title,
                published_at=published,
                collected_at=now,
                snippet="",
                language=self.language,
                matched_queries=[q.id],
            ))
        return out
