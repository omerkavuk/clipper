"""A project's own source list: built in setup, checked, and stored next to the project file."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml

from clipper.collectors.outlet_feeds import USER_AGENT, load_outlets
from clipper.models import Outlet, Project

SOURCES_FILE = "sources.yaml"
TIERS = ("national", "regional", "agency", "broadcaster", "specialist", "trade",
         "institutional", "think_tank")
TIMEOUT = 10
WORKERS = 8
FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/feeds/all.rss", "/index.xml")
_ALT = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'(\w+)\s*=\s*["\']([^"\']*)["\']')


class SourcesError(ValueError):
    pass


def normalize_domain(value: str) -> str:
    v = (value or "").strip().lower()
    if "//" not in v:
        v = "//" + v
    host = urlparse(v).hostname or ""
    return host.removeprefix("www.")


def _sources_path(project: Project) -> Path:
    return Path(project.folder) / SOURCES_FILE


def _to_outlet(o: dict) -> Outlet:
    return Outlet(name=o["name"], domain=o["domain"], feed=o.get("feed"), tier=o["tier"],
                  region=o.get("region"), paywall=bool(o.get("paywall", False)))


def _to_dict(o: Outlet) -> dict:
    d = {"name": o.name, "domain": o.domain, "feed": o.feed, "tier": o.tier}
    if o.region:
        d["region"] = o.region
    if o.paywall:
        d["paywall"] = True
    return d


def project_outlets(project: Project) -> list[Outlet]:
    """The project's own list when it has one, otherwise the bundled list for its country."""
    path = _sources_path(project) if project.folder else None
    if path and path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [_to_outlet(o) for o in raw.get("outlets") or []]
    return load_outlets(project.outlets_file)


def _feed_ok(session: requests.Session, url: str) -> bool:
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        return r.status_code < 400 and bool(feedparser.parse(r.content).entries)
    except requests.RequestException:
        return False


def check_site(domain: str, session: requests.Session | None = None) -> tuple[bool, str | None]:
    """(reachable, feed url or None): the home page answers, and a working feed if one is found."""
    session = session or requests.Session()
    home = f"https://{domain}/"
    try:
        r = session.get(home, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return False, None
    if r.status_code >= 400 and r.status_code not in (401, 403):  # 403: up, but refuses robots
        return False, None
    candidates: list[str] = []
    for tag in _ALT.findall(r.text[:200_000] if r.status_code < 400 else ""):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        if attrs.get("rel", "").lower() == "alternate" and (
                "rss" in attrs.get("type", "") or "atom" in attrs.get("type", "")) and attrs.get("href"):
            candidates.append(urljoin(r.url, attrs["href"]))
    candidates += [urljoin(home, p) for p in FEED_PATHS]
    for url in dict.fromkeys(candidates):
        if _feed_ok(session, url):
            return True, url
    return True, None


def build_sources(project: Project, add: list[dict] | None = None, remove: list[str] | None = None,
                  tiers: list[str] | None = None, check=check_site) -> dict:
    """Merge candidates into the project's source list, check new sites, and save it."""
    for t in tiers or []:
        if t not in TIERS:
            raise SourcesError(f"unknown source type {t!r}; use one of {', '.join(TIERS)}")
    current = project_outlets(project)
    if tiers and not _sources_path(project).is_file():  # first build: keep only the chosen kinds
        current = [o for o in current if o.tier in tiers]
    by_domain = {o.domain: o for o in current}

    added, unreachable, skipped = [], [], []
    fresh: list[dict] = []
    for c in add or []:
        domain = normalize_domain(c.get("domain") or c.get("url") or "")
        tier = c.get("tier", "")
        if not domain or not c.get("name") or tier not in TIERS:
            skipped.append(c.get("name") or c.get("domain") or "?")
            continue
        if domain in by_domain or any(f["domain"] == domain for f in fresh):
            continue
        fresh.append({**c, "domain": domain})
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda c: check(c["domain"]), fresh))
    for c, (reachable, feed) in zip(fresh, results):
        if not reachable:
            unreachable.append(c["domain"])
            continue
        by_domain[c["domain"]] = Outlet(c["name"], c["domain"], feed, c["tier"],
                                        c.get("region"), bool(c.get("paywall", False)))
        added.append(c["domain"])

    removed = []
    for d in remove or []:
        d = normalize_domain(d)
        if by_domain.pop(d, None):
            removed.append(d)

    outlets = sorted(by_domain.values(), key=lambda o: (TIERS.index(o.tier) if o.tier in TIERS
                                                        else len(TIERS), o.name.lower()))
    path = _sources_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"outlets": [_to_dict(o) for o in outlets]},
                                   allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {**summarize(outlets), "added": added, "unreachable": unreachable,
            "skipped": skipped, "removed": removed, "file": str(path.resolve())}


def summarize(outlets: list[Outlet]) -> dict:
    by_tier: dict[str, int] = {}
    for o in outlets:
        by_tier[o.tier] = by_tier.get(o.tier, 0) + 1
    return {"sources": len(outlets), "by_tier": by_tier,
            "with_feed": sum(1 for o in outlets if o.feed),
            "examples": [o.name for o in outlets[:8]]}
