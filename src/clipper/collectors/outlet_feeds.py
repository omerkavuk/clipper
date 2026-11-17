from __future__ import annotations

import calendar
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from importlib import resources
from typing import Callable

import feedparser
import requests
import yaml

from clipper.collectors import SourceStatus
from clipper.collectors.google_news import GoogleNewsCollector
from clipper.models import Item, Outlet, RunWindow
from clipper.queries import Query, combine, matching_ids

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
FEED_WORKERS = 8           # feeds are fetched in parallel: one slow site no longer holds the run
SITE_BATCH = 10            # outlets without a usable feed share one site-restricted search
FALLBACK_BUDGET_SECONDS = 60   # the minimum; more batches get more time
SECONDS_PER_BATCH = 20
MAX_FALLBACK_SECONDS = 300
SHALLOW_SLACK = timedelta(days=1)  # a feed reaching back to within a day of the start is deep enough
TERMS_PER_SEARCH = 8           # older weeks of shallow feeds: all names folded into a few searches
SNIPPET_MAX = 500
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def load_outlets(code: str) -> list[Outlet]:
    """The bundled outlet list `code`; an empty code means the project has no list yet."""
    if not code:
        return []
    if not re.fullmatch(r"[a-z]{2,8}", code or ""):
        raise ValueError(f"unknown outlet list: {code!r}")
    try:
        text = (resources.files("clipper") / "data" / "outlets" / f"{code}.yaml").read_text(
            encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"unknown outlet list: {code!r}") from None
    return [Outlet(name=o["name"], domain=o["domain"], feed=o.get("feed"), tier=o["tier"],
                   region=o.get("region"), paywall=bool(o.get("paywall", False)))
            for o in yaml.safe_load(text)["outlets"]]


def _plain(html: str) -> str:
    return _SPACES.sub(" ", unescape(_TAGS.sub(" ", html or ""))).strip()


class OutletFeedsCollector:
    name = "outlet"

    def __init__(self, outlets: list[Outlet], gnews: GoogleNewsCollector,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 progress: Callable[[str], None] | None = None):
        self.progress = progress
        self.outlets = outlets
        self.gnews = gnews
        self.session = session or requests.Session()
        self.sleep = sleep

    def _say(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _try_feed(self, outlet: Outlet, queries: list[Query], window: RunWindow):
        try:
            return self._from_feed(outlet, queries, window), None
        except Exception as e:  # a bad feed falls back, it never stops the run
            return None, f"feed failed: {e}"

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: list[Item] = []
        statuses: dict[str, SourceStatus] = {}
        with_feed = [o for o in self.outlets if o.feed]
        if with_feed:
            self._say(f"Outlets: reading {len(with_feed)} feeds")
        with ThreadPoolExecutor(max_workers=FEED_WORKERS) as pool:
            results = list(pool.map(lambda o: self._try_feed(o, queries, window), with_feed))
        feed_errors: dict[str, str] = {}
        for outlet, (ok, error) in zip(with_feed, results):
            if ok is None:
                feed_errors[outlet.domain] = error
                continue
            got, covered = ok
            items.extend(got)
            statuses[outlet.domain] = SourceStatus("outlet", outlet.domain, True, len(got), None, covered)

        rest = [o for o in self.outlets if o.domain not in statuses]
        batches = [rest[i:i + SITE_BATCH] for i in range(0, len(rest), SITE_BATCH)]
        # Feeds that only reach back a few days: search their older weeks by site.
        shallow = [o for o in self.outlets if o.domain in statuses and statuses[o.domain].covered_since
                   and statuses[o.domain].covered_since > window.since + SHALLOW_SLACK]
        deep_batches = [shallow[i:i + SITE_BATCH] for i in range(0, len(shallow), SITE_BATCH)]
        budget = min(MAX_FALLBACK_SECONDS,
                     max(FALLBACK_BUDGET_SECONDS, SECONDS_PER_BATCH * (len(batches) + len(deep_batches))))
        start = self.gnews.clock()
        for n, batch in enumerate(batches, 1):
            self._say(f"Outlets: site search {n}/{len(batches)}")
            remaining = budget - (self.gnews.clock() - start)
            got, batch_statuses = self._fallback(batch, queries, window, feed_errors, remaining)
            items.extend(got)
            statuses.update(batch_statuses)
        for n, batch in enumerate(deep_batches, 1):
            self._say(f"Outlets: older weeks {n}/{len(deep_batches)}")
            remaining = budget - (self.gnews.clock() - start)
            got, reached = self._older_weeks(batch, queries, window, statuses, remaining)
            items.extend(got)
            for d in reached:
                old = statuses[d]
                statuses[d] = SourceStatus(old.source, old.target, old.ok,
                                           old.items + sum(1 for i in got if i.outlet_domain == d),
                                           old.error, window.since)
        return items, [statuses[o.domain] for o in self.outlets]

    def _older_weeks(self, batch: list[Outlet], queries: list[Query], window: RunWindow,
                     statuses: dict[str, SourceStatus], budget: float) -> tuple[list[Item], list[str]]:
        """Site searches for the part of the window a shallow feed doesn't cover. Every name
        is folded into a few OR'd searches. Returns the items and the domains now covered."""
        if budget <= 0:
            return [], []
        until = max(statuses[o.domain].covered_since for o in batch)
        part = RunWindow(window.since, min(until, window.until))
        foldable = [q for q in queries if len(q.all_of) == 1]
        folded = [c for c, _ in combine(foldable, TERMS_PER_SEARCH, "older weeks")] if foldable else []
        domains = [o.domain for o in batch]
        site = domains[0] if len(domains) == 1 else domains
        got, results = self.gnews.collect(folded, part, site=site, budget=budget)
        by_domain = {o.domain: o for o in batch}
        kept = []
        for it in got:
            outlet = by_domain.get(it.outlet_domain) or next(
                (o for d, o in by_domain.items() if it.outlet_domain.endswith("." + d)), None)
            if outlet is None:
                continue
            it.outlet, it.outlet_domain = outlet.name, outlet.domain
            it.matched_queries = matching_ids(queries, it.title) or ["subject"]
            kept.append(it)
        searched = bool(results) and all(s.ok for s in results)
        return kept, (domains if searched else [])

    def _from_feed(self, outlet: Outlet, queries: list[Query],
                   window: RunWindow) -> tuple[list[Item], datetime]:
        resp = self.session.get(outlet.feed, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if not feed.entries and (feed.bozo or not feed.version):
            raise ValueError("unparseable feed")
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        oldest: datetime | None = None
        for e in feed.entries:
            parsed = e.get("published_parsed") or e.get("updated_parsed")
            if not parsed:
                continue
            published = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            if oldest is None or published < oldest:
                oldest = published
            if not (window.since <= published < window.until):
                continue
            link = (e.get("link") or "").strip()
            if not link:
                continue
            title = _plain(e.get("title", ""))
            summary = _plain(e.get("summary", ""))
            matched = matching_ids(queries, f"{title} {summary}")
            if not matched:
                continue
            out.append(Item(
                source="outlet_feed", outlet=outlet.name, outlet_domain=outlet.domain,
                url=link, title=title, published_at=published, collected_at=now,
                snippet=summary[:SNIPPET_MAX], matched_queries=matched, subscribers_only=False,
            ))
        if feed.entries and oldest is None:  # nothing to place in the window: use the site query
            raise ValueError("feed entries have no dates")
        return out, oldest or window.since

    def _fallback(self, batch: list[Outlet], queries: list[Query], window: RunWindow,
                  feed_errors: dict[str, str], budget: float) -> tuple[list[Item], dict[str, SourceStatus]]:
        """One site-restricted search per query for the whole batch of outlets."""
        domains = [o.domain for o in batch]
        if budget <= 0:
            got, results = [], [SourceStatus(self.gnews.name, "*", False, 0,
                                             "time budget exceeded, skipped", None)]
        else:
            site = domains[0] if len(domains) == 1 else domains
            got, results = self.gnews.collect(queries, window, site=site, budget=budget)
        by_domain = {o.domain: o for o in batch}
        counts = {d: 0 for d in domains}
        kept: list[Item] = []
        for it in got:
            outlet = by_domain.get(it.outlet_domain) or next(
                (o for d, o in by_domain.items() if it.outlet_domain.endswith("." + d)), None)
            if outlet is None:
                continue
            it.outlet = outlet.name
            counts[outlet.domain] += 1
            kept.append(it)
        searched = any(s.ok for s in results)
        errors = "; ".join(dict.fromkeys(s.error for s in results if s.error)) or "fallback search failed"
        out: dict[str, SourceStatus] = {}
        for d in domains:
            if searched:
                out[d] = SourceStatus("outlet", d, True, counts[d], None, window.since)
            else:
                error = f"{feed_errors[d]}; {errors}" if d in feed_errors else errors
                out[d] = SourceStatus("outlet", d, False, 0, error, None)
        return kept, out
