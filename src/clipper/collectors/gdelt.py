from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import requests

from clipper import countries
from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import GDELT_LANGS, Query, to_gdelt

BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT = 60
PACE_SECONDS = 5.5
BACKOFFS = (10, 20, 40)  # one retry per entry after a 429
GDELT_BUDGET_SECONDS = 60  # total time for one collect call

_CODE_BY_NAME = {name: code for code, name in GDELT_LANGS.items()}


class RateLimited(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class GdeltCollector:
    name = "gdelt"

    def __init__(self, country: str, languages: list[str], exclude_domains: list[str],
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 progress: Callable[[str], None] | None = None):
        self.country = countries.gdelt_code(country)  # None: GDELT has no code for it
        self.languages = languages
        self.exclude_domains = exclude_domains
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.progress = progress
        self._requested = False
        self._start = 0.0

    def _say(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _skip(self, q: Query, reason: str) -> SourceStatus:
        return SourceStatus(self.name, q.id, False, 0, reason, None)

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: list[Item] = []
        statuses: list[SourceStatus] = []
        self._start = self.clock()
        skip_reason: str | None = None  # set once the rest of the run is given up
        for n, q in enumerate(queries, 1):
            if skip_reason:
                statuses.append(self._skip(q, skip_reason))
                continue
            if self.country is None:
                statuses.append(self._skip(q, "country not covered by GDELT"))
                continue
            if not to_gdelt(q, self.country, self.languages, self.exclude_domains):
                statuses.append(self._skip(q, "query not usable for GDELT"))
                continue
            try:
                got = self._fetch(q, window)
            except RateLimited:
                skip_reason = "rate limited, skipped"
                self._say("GDELT: rate limited, skipping remaining queries")
                statuses.append(self._skip(q, "rate limited"))
                continue
            except BudgetExceeded:
                skip_reason = "time budget exceeded, skipped"
                self._say("GDELT: time budget used up, skipping remaining queries")
                statuses.append(self._skip(q, skip_reason))
                continue
            except Exception as e:  # one failing query must not stop the run
                statuses.append(self._skip(q, str(e)))
                self._say(f"GDELT: {n}/{len(queries)} queries")
                continue
            items.extend(got)
            statuses.append(SourceStatus(self.name, q.id, True, len(got), None, window.since))
            self._say(f"GDELT: {n}/{len(queries)} queries")
        return items, statuses

    def _get(self, params: dict) -> requests.Response:
        """GET with pacing between all requests and backoff on 429."""
        delays = [None, *BACKOFFS]  # None: normal pacing; numbers: backoff before a retry
        for delay in delays:
            wait = max(delay, PACE_SECONDS) if delay is not None else (
                PACE_SECONDS if self._requested else 0)
            if self.clock() - self._start + wait > GDELT_BUDGET_SECONDS:
                raise BudgetExceeded()
            if wait:
                self.sleep(wait)  # a backoff replaces the pacing sleep
            self._requested = True
            resp = self.session.get(BASE_URL, params=params, timeout=TIMEOUT)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
        raise RateLimited()

    def _fetch(self, q: Query, window: RunWindow) -> list[Item]:
        params = {
            "query": to_gdelt(q, self.country, self.languages, self.exclude_domains),
            "mode": "artlist",
            "format": "json",
            "maxrecords": 250,
            "sort": "datedesc",
            "startdatetime": _fmt(window.since),
            "enddatetime": _fmt(window.until),
        }
        resp = self._get(params)
        try:
            data = resp.json()
        except ValueError:
            raise ValueError("response is not JSON: " + resp.text[:100].strip()) from None
        if not isinstance(data, dict):
            raise ValueError("unexpected JSON shape (not an object)")
        now = datetime.now(timezone.utc)
        out: list[Item] = []
        for a in data.get("articles") or []:
            try:
                published = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if not (window.since <= published < window.until):
                continue
            url = a.get("url", "")
            domain = _domain("//" + a["domain"]) if a.get("domain") else _domain(url)
            lang = (a.get("language") or "").strip().lower()
            out.append(Item(
                source=self.name,
                outlet=domain,
                outlet_domain=domain,
                url=url,
                title=a.get("title", ""),
                published_at=published,
                collected_at=now,
                snippet="",
                language=_CODE_BY_NAME.get(lang, lang),
                matched_queries=[q.id],
            ))
        return out
