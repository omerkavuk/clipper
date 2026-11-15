"""Brave News Search: an independent news index, used next to Google News when a key is set.

The key is the user's own (https://api-dashboard.search.brave.com). Clipper reads it from
the BRAVE_API_KEY environment variable or from ~/.clipper/brave-key.txt and never prints it.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

import requests

from clipper import paths
from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import Query, to_google_news

BASE_URL = "https://api.search.brave.com/res/v1/news/search"
KEY_FILE = "brave-key.txt"
TIMEOUT = 15
PAUSE_SECONDS = 1.1          # stay under one request a second
BUDGET_SECONDS = 90
COUNT = 50
MAX_QUERY_CHARS = 400        # Brave's limits for q
MAX_QUERY_WORDS = 50
SNIPPET_MAX = 500
_TAGS = re.compile(r"<[^>]+>")


class _Stop(Exception):
    """Every later Brave request would fail the same way (bad key, rate limit)."""


def brave_key() -> str | None:
    """The user's Brave key, or None when Brave is not set up."""
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if key:
        return key
    f = paths.home() / KEY_FILE
    if f.is_file():
        key = f.read_text(encoding="utf-8-sig").strip()
        return key or None
    return None


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _texts(q: Query) -> list[str]:
    """The query as Brave text; split into one text per term when it is over Brave's limits."""
    text = to_google_news(Query(q.id, q.label, q.all_of, q.exclude), [])
    if len(text) <= MAX_QUERY_CHARS and len(text.split()) <= MAX_QUERY_WORDS:
        return [text]
    if len(q.all_of) == 1:
        return [to_google_news(Query(q.id, q.label, [[t]], q.exclude), []) for t in q.all_of[0]]
    return [text[:MAX_QUERY_CHARS]]


class BraveNewsCollector:
    name = "brave"

    def __init__(self, country: str, languages: list[str], key: str,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 progress: Callable[[str], None] | None = None):
        self.country = country.upper()
        self.languages = languages or ["en"]
        self.key = key
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.progress = progress
        self._country_ok = True   # Brave serves a fixed list of countries; others fall back to ALL
        self._requested = False

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: dict[str, Item] = {}
        statuses: list[SourceStatus] = []
        start = self.clock()
        stop: str | None = None
        jobs = [(q, lang, text) for q in queries for lang in self.languages for text in _texts(q)]
        for n, (q, lang, text) in enumerate(jobs, 1):
            target = f"{q.id}:{lang}"
            if stop:
                statuses.append(SourceStatus(self.name, target, False, 0, f"{stop}, skipped", None))
                continue
            if self.clock() - start > BUDGET_SECONDS:
                statuses.append(SourceStatus(self.name, target, False, 0, "time budget exceeded, skipped", None))
                continue
            if self.progress:
                self.progress(f"Brave: {n}/{len(jobs)} searches")
            try:
                got = self._fetch(q, lang, text, window)
            except _Stop as e:
                stop = str(e)
                statuses.append(SourceStatus(self.name, target, False, 0, stop, None))
                continue
            except Exception as e:  # one failing search must not stop the run
                statuses.append(SourceStatus(self.name, target, False, 0, str(e), None))
                continue
            for it in got:
                kept = items.setdefault(it.url, it)
                if kept is not it and q.id not in kept.matched_queries:
                    kept.matched_queries.append(q.id)
            statuses.append(SourceStatus(self.name, target, True, len(got), None, window.since))
        return list(items.values()), statuses

    def _get(self, params: dict) -> requests.Response:
        if self._requested:
            self.sleep(PAUSE_SECONDS)
        self._requested = True
        return self.session.get(BASE_URL, params=params, timeout=TIMEOUT,
                                headers={"Accept": "application/json",
                                         "X-Subscription-Token": self.key})

    def _fetch(self, q: Query, lang: str, text: str, window: RunWindow) -> list[Item]:
        last_day = (window.until - timedelta(seconds=1)).date()
        params = {"q": text, "search_lang": lang, "count": COUNT,
                  "freshness": f"{window.since.date().isoformat()}to{last_day.isoformat()}",
                  "country": self.country if self._country_ok else "ALL"}
        resp = self._get(params)
        if resp.status_code == 422 and self._country_ok and "country" in resp.text:
            self._country_ok = False  # not a Brave market: search worldwide in the language
            params["country"] = "ALL"
            resp = self._get(params)
        if resp.status_code in (401, 403):
            raise _Stop("Brave key not accepted")
        if resp.status_code == 429:
            raise _Stop("Brave asked to slow down")
        resp.raise_for_status()
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        for r in (resp.json().get("results") or []):
            url = (r.get("url") or "").strip()
            published = _date(r.get("page_age"))
            # Freshness also matches pages modified in the period; keep only those published in it.
            if not url or not published or not (window.since <= published < window.until):
                continue
            domain = (((r.get("meta_url") or {}).get("hostname")) or _domain(url)).lower().removeprefix("www.")
            outlet = ((r.get("profile") or {}).get("name") or "").strip() or domain
            out.append(Item(
                source=self.name, outlet=outlet, outlet_domain=domain, url=url,
                title=" ".join(_TAGS.sub("", str(r.get("title") or "")).split()),
                published_at=published, collected_at=now,
                snippet=" ".join(_TAGS.sub("", str(r.get("description") or "")).split())[:SNIPPET_MAX],
                language=lang, matched_queries=[q.id],
            ))
        return out
