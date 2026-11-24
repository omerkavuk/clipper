from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

import feedparser
import requests

from clipper.collectors import SourceStatus, summarize_jobs
from clipper.models import Item, RunWindow
from clipper.queries import SPECIFIC_PREFIXES, Query, to_google_news

BASE_URL = "https://news.google.com/rss/search"
TIMEOUT = 15
PAUSE_SECONDS = 1
SEARCH_BUDGET_SECONDS = 90  # the broad search for one week; longer periods get more
MAX_SEARCH_BUDGET_SECONDS = 480
SECONDS_PER_REQUEST = 2.5
SLICE_DAYS = 7  # Google News returns at most ~100 results per search, so long periods go week by week
ABROAD_EDITION = ("US", "en")  # worldwide English coverage of topics and people
BLOCKED_STATUSES = (429, 503)  # Google's "slow down": stop asking for the rest of the run
# Google's EU consent page otherwise stands in front of some news.google.com pages.
CONSENT_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}


def slices(window: RunWindow, days: int = SLICE_DAYS) -> list[RunWindow]:
    """The window cut into pieces of at most `days` days (one piece for short windows)."""
    out, start = [], window.since
    while start < window.until:
        end = min(start + timedelta(days=days), window.until)
        out.append(RunWindow(start, end))
        start = end
    return out or [window]


def is_specific(q: Query) -> bool:
    return q.id.startswith(SPECIFIC_PREFIXES)


class Blocked(Exception):
    pass


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


class GoogleNewsCollector:
    name = "google_news"

    def __init__(self, country: str, language: str | list[str], exclude_domains: list[str],
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 progress: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 abroad: bool = False):
        self.progress = progress
        self.clock = clock
        self.blocked = False  # set on the first 429/503; shared by every later call
        self.off_reason: str | None = None
        self.incomplete_from = None  # start of the earliest period a search failed for (retried next run)
        self.country = country.upper()
        self.languages = [language] if isinstance(language, str) else list(language) or ["en"]
        self.language = self.languages[0]  # site searches use the first language only
        self.abroad = abroad and (self.country, "en") != ABROAD_EDITION
        self.exclude_domains = exclude_domains
        self.session = session or requests.Session()
        self.session.cookies.update(CONSENT_COOKIES)
        self.sleep = sleep

    def _jobs(self, queries: list[Query], window: RunWindow) -> list[tuple[Query, tuple[str, str], RunWindow]]:
        """Every (query, edition, period) to search: each language, week by week, plus one
        worldwide English pass over the whole window for topic and people searches."""
        jobs = []
        home = [(self.country, lang) for lang in self.languages]
        parts = slices(window)
        for q in queries:
            for edition in home:
                jobs += [(q, edition, part) for part in parts]
            if self.abroad and is_specific(q):
                jobs.append((q, ABROAD_EDITION, window))
        return jobs

    def site_query(self, q: Query, site: str | list[str]) -> str:
        sites = [site] if isinstance(site, str) else list(site)
        restrict = (f"site:{sites[0]}" if len(sites) == 1
                    else "(" + " OR ".join(f"site:{d}" for d in sites) + ")")
        return f"{to_google_news(q, self.exclude_domains)} {restrict}"

    def collect(self, queries: list[Query], window: RunWindow,
                site: str | list[str] | None = None,
                budget: float | None = None) -> tuple[list[Item], list[SourceStatus]]:
        """Search each query; `site` restricts to one or more domains; `budget` caps seconds."""
        if site is None:
            return self._collect_broad(queries, window, budget)
        items: list[Item] = []
        statuses: list[SourceStatus] = []
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

    def _collect_broad(self, queries: list[Query], window: RunWindow,
                       budget: float | None) -> tuple[list[Item], list[SourceStatus]]:
        jobs = self._jobs(queries, window)
        if budget is None:
            budget = min(MAX_SEARCH_BUDGET_SECONDS,
                         max(SEARCH_BUDGET_SECONDS, len(jobs) * SECONDS_PER_REQUEST))
        start = self.clock()
        found: dict[str, Item] = {}
        per_query: dict[str, dict] = {q.id: {"done": [], "failed": [], "items": 0} for q in queries}

        def fail(tally, edition, part, why):
            label = f"{edition[1]}, {part.since:%d %b}–{part.until:%d %b}"
            tally["failed"].append((label, why, part.since))
            if self.incomplete_from is None or part.since < self.incomplete_from:
                self.incomplete_from = part.since

        for n, (q, edition, part) in enumerate(jobs):
            tally = per_query[q.id]
            if self.off_reason or self.blocked:
                fail(tally, edition, part, self.off_reason or "blocked by Google, skipped")
                continue
            if self.clock() - start + (PAUSE_SECONDS if n else 0) > budget:
                fail(tally, edition, part, "time budget exceeded, skipped")
                continue
            if n:
                self.sleep(PAUSE_SECONDS)
            if self.progress and (n % 10 == 0 or n == len(jobs) - 1):
                self.progress(f"Google News: {n + 1}/{len(jobs)} searches")
            try:
                got = self._fetch(q, part, None, edition)
            except Blocked:
                self.blocked = True
                if self.progress:
                    self.progress("Google News: asked to slow down, skipping remaining searches")
                fail(tally, edition, part, "blocked by Google")
                continue
            except Exception as e:  # one failing search must not stop the run
                fail(tally, edition, part, str(e))
                continue
            tally["done"].append(part)
            for it in got:
                kept = found.setdefault(it.url, it)
                if kept is not it:
                    kept.matched_queries = list(dict.fromkeys(kept.matched_queries + it.matched_queries))
                else:
                    tally["items"] += 1
        statuses = [summarize_jobs(self.name, q.id, per_query[q.id]["done"], per_query[q.id]["failed"],
                                   per_query[q.id]["items"], window) for q in queries]
        return list(found.values()), statuses

    def _fetch(self, q: Query, window: RunWindow, site: str | list[str] | None,
               edition: tuple[str, str] | None = None) -> list[Item]:
        text = self.site_query(q, site) if site else to_google_news(q, self.exclude_domains)
        after = window.since.date().isoformat()
        before = (window.until + timedelta(days=1)).date().isoformat()
        country, language = edition or (self.country, self.language)
        params = {
            "q": f"{text} after:{after} before:{before}",
            "hl": language,
            "gl": country,
            "ceid": f"{country}:{language}",
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
                language=language,
                matched_queries=[q.id],
            ))
        return out
