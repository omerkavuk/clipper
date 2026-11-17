from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import requests

from clipper import countries
from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import GDELT_LANGS, SPECIFIC_PREFIXES, Query, combine, matching_ids, to_gdelt

BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT = 20               # well under the budget, so one slow answer can't use it all up
PACE_SECONDS = 5.5         # GDELT allows about one request every 5 seconds
BACKOFFS = (15,)           # one quick retry after a 429; the search is then retried later in the run
RETRY_PAUSE_SECONDS = 30   # wait before the late retries
GDELT_BUDGET_SECONDS = 100  # the minimum: room for a request, a quick retry, the wait and a late retry
SECONDS_PER_REQUEST = 35
MAX_BUDGET_SECONDS = 300
TERMS_PER_REQUEST = 5      # topic and people searches are folded into OR'd requests of this size

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
        self._budget = GDELT_BUDGET_SECONDS

    def _say(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _skip(self, q: Query, reason: str) -> SourceStatus:
        return SourceStatus(self.name, q.id, False, 0, reason, None)

    def plan(self, queries: list[Query]) -> list[tuple[Query, list[Query]]]:
        """The requests to send: subject searches one by one, topic and people searches
        folded into OR'd requests. Each comes with the searches it stands for."""
        singles = [q for q in queries if len(q.all_of) != 1 or not q.id.startswith(("programme:", "person:"))]
        foldable = [q for q in queries if q not in singles]
        out = [(q, [q]) for q in singles]
        by_id = {q.id: q for q in foldable}
        for combined, ids in combine(foldable, TERMS_PER_REQUEST, "topics and people") if foldable else []:
            out.append((combined, [by_id[i] for i in ids]))
        return out

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        items: list[Item] = []
        statuses: list[SourceStatus] = []
        self._start = self.clock()
        requests_plan = self.plan(queries)
        self._budget = min(MAX_BUDGET_SECONDS, max(GDELT_BUDGET_SECONDS, SECONDS_PER_REQUEST * len(requests_plan)))
        retry: list[tuple[Query, list[Query]]] = []
        limited = False  # GDELT asked us to slow down: later requests wait for the late retry
        skip_reason: str | None = None  # set once the rest of the run is given up

        def run(q: Query, members: list[Query]) -> str | None:
            """Send one request; returns "rate limited" or "budget" when it couldn't run."""
            try:
                got = self._fetch(q, window)
            except RateLimited:
                return "rate limited"
            except BudgetExceeded:
                return "budget"
            except Exception as e:  # one failing request must not stop the run
                statuses.append(self._skip(q, str(e)))
                return None
            for it in got:  # a folded request: credit the searches the headline really names
                it.matched_queries = matching_ids(members, it.title) or [m.id for m in members]
            items.extend(got)
            statuses.append(SourceStatus(self.name, q.id, True, len(got), None, window.since))
            return None

        for n, (q, members) in enumerate(requests_plan, 1):
            if skip_reason:
                statuses.append(self._skip(q, skip_reason))
                continue
            if self.country is None:
                statuses.append(self._skip(q, "country not covered by GDELT"))
                continue
            if not to_gdelt(q, self.country, self.languages, self.exclude_domains):
                statuses.append(self._skip(q, "query not usable for GDELT"))
                continue
            if limited:
                retry.append((q, members))
                continue
            outcome = run(q, members)
            if outcome == "rate limited":
                limited = True
                retry.append((q, members))
                self._say("GDELT: asked to slow down; will retry later in the run")
            elif outcome == "budget":
                skip_reason = "time budget exceeded, skipped"
                self._say("GDELT: time budget used up, skipping remaining queries")
                statuses.append(self._skip(q, skip_reason))
            self._say(f"GDELT: {n}/{len(requests_plan)} requests")

        if retry and not skip_reason:
            if self.clock() - self._start + RETRY_PAUSE_SECONDS < self._budget:
                self.sleep(RETRY_PAUSE_SECONDS)
            for k, (q, members) in enumerate(retry):
                outcome = run(q, members)
                if outcome:
                    reason = "rate limited" if outcome == "rate limited" else "time budget exceeded"
                    statuses.extend(self._skip(rq, reason) for rq, _ in retry[k:])
                    break
        elif retry:
            statuses.extend(self._skip(q, skip_reason) for q, _ in retry)
        return items, statuses

    def _get(self, params: dict) -> requests.Response:
        """GET with pacing between all requests and backoff on 429."""
        delays = [None, *BACKOFFS]  # None: normal pacing; numbers: backoff before a retry
        for delay in delays:
            wait = max(delay, PACE_SECONDS) if delay is not None else (
                PACE_SECONDS if self._requested else 0)
            if self.clock() - self._start + wait > self._budget:
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
