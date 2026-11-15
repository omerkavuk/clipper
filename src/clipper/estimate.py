from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta, timezone
from typing import Callable

from clipper.collectors import Collector
from clipper.collectors.gdelt import GdeltCollector
from clipper.collectors.google_news import GoogleNewsCollector
from clipper.collectors.outlet_feeds import OutletFeedsCollector
from clipper.collectors.bing_news import BingNewsCollector
from clipper.collectors.brave import BraveNewsCollector, brave_key
from clipper.collectors.social import SocialCollector
from clipper.dedupe import normalize_url
from clipper.models import Item, Project, RunWindow
from clipper.pipeline import DEFAULT_EXCLUDED_DOMAINS, Caps, is_social
from clipper.queries import Query, build_queries, matches
from clipper.sources import project_outlets

# Calibrated later from real runs.
TOKENS_PER_TAGGED_ITEM = 600
REPORT_BASE_TOKENS = 8000
TOKENS_PER_REPORT_ITEM = 250
RUN_OVERHEAD_TOKENS = 15000
MIN_CANDIDATES = 30  # a quiet test week must not give a cap of zero

NO_HITS = "finds nothing — misspelled, too specific, or another language?"
OWN_SITE = "mostly your own website"
GENERIC_HITS_PER_WEEK = 100  # more than this for one search is almost always an everyday word
OWN_SITE_SHARE = 0.5
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
RUNS_PER_MONTH = {"daily": 30, "weekly": 4.3, "monthly": 1}


def _own_site(domain: str, exclude_domains: list[str]) -> bool:
    domain = (domain or "").lower()
    return any(domain == d.lower() or domain.endswith("." + d.lower()) for d in exclude_domains)


def _default_collectors(project: Project,
                        progress: Callable[[str], None] | None = None) -> list[Collector]:
    # No exclude_domains here, so the share of own-site results can be measured.
    gnews = GoogleNewsCollector(project.country, project.languages[0], [], progress=progress)
    return [
        OutletFeedsCollector(project_outlets(project), gnews, progress=progress),
        gnews,
        BingNewsCollector(project.country, project.languages[0], progress=progress),
        GdeltCollector(project.country, project.languages, [], progress=progress),
        *([BraveNewsCollector(project.country, project.languages, brave_key(), progress=progress)]
          if brave_key() else []),
        *([SocialCollector(project.social, progress=progress, own_accounts=project.own_accounts)]
          if project.social else []),
    ]


def _unique(items: list[Item]) -> list[Item]:
    seen: dict[str, Item] = {}
    for it in items:
        seen.setdefault(normalize_url(it.url), it)
    return list(seen.values())


def _terms(queries: list[Query]) -> dict[str, list[str]]:
    """Each distinct term, with the ids of the queries that contain it."""
    out: dict[str, list[str]] = {}
    for q in queries:
        for group in q.all_of:
            for term in group:
                ids = out.setdefault(term, [])
                if q.id not in ids:
                    ids.append(q.id)
    return out


def test_queries(project: Project, collectors: list[Collector] | None = None, days: int = 7,
                 now: datetime | None = None,
                 progress: Callable[[str], None] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    window = RunWindow(since=now - timedelta(days=days), until=now)
    queries = build_queries(project)
    collectors = _default_collectors(project, progress) if collectors is None else collectors

    raw: list[Item] = []
    failed: list[str] = []
    answered: set[str] = set()  # sources where at least one request worked
    for c in collectors:
        items, statuses = c.collect(queries, window)
        raw.extend(items)
        for s in statuses:
            if s.ok:
                answered.add(s.source)
            elif s.source not in failed:
                failed.append(s.source)

    # Social and video platforms are dropped outright; they are not "own site" hits.
    raw = [i for i in raw if is_social(i)
           or not _own_site(i.outlet_domain, list(DEFAULT_EXCLUDED_DOMAINS))]
    ex = project.exclude_domains
    unique = _unique([i for i in raw if not _own_site(i.outlet_domain, ex)])
    raw_unique = _unique(raw)
    own_share = (sum(1 for i in raw_unique if _own_site(i.outlet_domain, ex)) / len(raw_unique)
                 if raw_unique else 0.0)

    total = len(unique)
    terms = []
    # A search is only "too generic" when it finds a lot in absolute terms. Its share of this
    # project's results says nothing: a flagship programme rightly dominates the coverage.
    for term in _terms(queries):
        probe = Query("term", term, [[term]], [])
        hits = sum(1 for i in unique if matches(probe, i.title + " " + i.snippet))
        terms.append({"term": term, "hits": hits})

    out_queries = []
    for q in queries:
        pre = _unique([i for i in raw if q.id in i.matched_queries])
        post = [i for i in pre if not _own_site(i.outlet_domain, ex)]
        warnings = []
        if not post:
            warnings.append(NO_HITS)
        weekly = len(post) * 7 / max(days, 1)
        if weekly > GENERIC_HITS_PER_WEEK:
            warnings.append(f"too generic — {len(post)} results; probably an everyday word")
        if pre and sum(1 for i in pre if _own_site(i.outlet_domain, ex)) / len(pre) >= OWN_SITE_SHARE:
            warnings.append(OWN_SITE)
        out_queries.append({"id": q.id, "label": q.label, "hits": len(post),
                            "samples": [i.title for i in post[:3]], "warnings": warnings})

    return {
        "window": {"since": window.since.isoformat(), "until": window.until.isoformat()},
        "queries": out_queries,
        "terms": terms,
        "total_unique_hits": total,
        "own_site_share": own_share,
        "sources_failed": failed,
        "all_sources_failed": not answered,
    }


test_queries.__test__ = False  # not a pytest test


def estimate(project: Project, test_result: dict) -> dict:
    period = PERIOD_DAYS[project.frequency]
    items_per_run = math.ceil(test_result["total_unique_hits"] * period / 7)
    tag_tokens = items_per_run * TOKENS_PER_TAGGED_ITEM
    report_tokens = (REPORT_BASE_TOKENS
                     + min(items_per_run, Caps.max_report_items) * TOKENS_PER_REPORT_ITEM)
    per_run = tag_tokens + report_tokens + RUN_OVERHEAD_TOKENS
    caps = Caps(max_candidates=max(MIN_CANDIDATES, math.ceil(2 * items_per_run)))
    return {
        "items_per_run": items_per_run,
        "tag_tokens": tag_tokens,
        "report_tokens": report_tokens,
        "overhead": RUN_OVERHEAD_TOKENS,
        "per_run": per_run,
        "per_month": round(per_run * RUNS_PER_MONTH[project.frequency]),
        "caps": dataclasses.asdict(caps),
    }
