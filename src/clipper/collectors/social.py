"""Social platforms through Clipper's own private, pinned copy of Agent Reach.

The copy is installed in Clipper's environment and always runs with its own home folder
(~/.clipper/reach) and its own browser port, so the user's own Agent Reach install, logins
and browser are never touched.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from clipper import paths
from clipper.collectors import SourceStatus
from clipper.models import Item, RunWindow
from clipper.queries import Query, matching_ids

REACH_PORT = 9333          # not 9222: that is the user's own Agent Reach browser
CALL_TIMEOUT = 60
BUDGET_SECONDS = 150
MAX_TERMS = 6              # searches per platform per run
LIMIT = 10
SNIPPET_MAX = 500
YOUTUBE_READS = 10         # YouTube search has no dates; each result is read for its date

PLATFORM_NAMES = {"x": "X", "linkedin": "LinkedIn", "instagram": "Instagram",
                  "facebook": "Facebook", "tiktok": "TikTok", "youtube": "YouTube",
                  "reddit": "Reddit"}
PLATFORM_DOMAINS = {"x": "x.com", "linkedin": "linkedin.com", "instagram": "instagram.com",
                    "facebook": "facebook.com", "tiktok": "tiktok.com", "youtube": "youtube.com",
                    "reddit": "reddit.com"}
# How each platform is searched. "browser" routes need Clipper's private browser and a login.
ROUTES = {
    "youtube": ("youtube", "search", None),
    "reddit": ("reddit", "search", None),
    "linkedin": ("web", "search", "site:linkedin.com/posts"),
    "x": ("twitter", "search", None),
    # Instagram's search finds users, not posts: posts come from the accounts the project watches.
    "instagram": ("instagram", "posts", None),
    "facebook": ("facebook", "search", None),
    "tiktok": ("web", "search", "site:tiktok.com"),
}
BROWSER_CHANNELS = {"x": "twitter", "instagram": "instagram", "facebook": "facebook"}
PLAIN_ERRORS = {
    "NEED_SETUP": "needs the one-time social setup",
    "NEED_LOGIN": "needs you to log in once",
    "RATE_LIMITED": "asked to slow down",
    "PLATFORM_BLOCKED": "asked for a verification on its own site",
    "TIMEOUT": "did not answer in time",
    "BUSY": "busy, try later",
    "UPSTREAM_BROKEN": "not answering right now",
}


def reach_home() -> Path:
    p = paths.home() / "reach"
    p.mkdir(parents=True, exist_ok=True)
    return p


def reach_command() -> list[str]:
    exe = Path(sys.executable).parent / ("agent-reach.exe" if os.name == "nt" else "agent-reach")
    return [str(exe)] if exe.exists() else [sys.executable, "-c",
                                            "from agent_reach.cli import main; main()"]


def run_reach(args: list[str], timeout: float = CALL_TIMEOUT) -> dict:
    """Run the private Agent Reach copy; always returns its JSON (or an error in the same shape)."""
    home = str(reach_home())
    env = {**os.environ, "HOME": home, "USERPROFILE": home, "REACH_BROWSER_PORT": str(REACH_PORT)}
    try:
        done = subprocess.run([*reach_command(), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "items": [], "error": {"code": "TIMEOUT", "message": "timed out"}}
    except OSError as e:
        return {"ok": False, "items": [], "error": {"code": "NEED_SETUP", "message": str(e)}}
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "items": [],
                "error": {"code": "UPSTREAM_BROKEN", "message": (done.stderr or done.stdout)[-300:]}}


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _reason(err: dict) -> str:
    """A plain reason, keeping Agent Reach's own code and hint for whoever fixes it."""
    code = err.get("code")
    text = PLAIN_ERRORS.get(code, err.get("message") or "failed")
    hint = err.get("hint") or err.get("retry_after")
    return f"{text} [{code}{'; ' + str(hint) if hint else ''}]" if code else text


def plan_terms(queries: list[Query], limit: int = MAX_TERMS,
               first: list[str] | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(term, query id) to search, and the ones left out by the per-platform budget. The budget
    is shared fairly: the main name of every search (organisation, each topic, each person)
    comes before any second spelling, so many aliases can't crowd out the topics."""
    groups = [q for q in queries if len(q.all_of) == 1]  # combined queries are matched locally
    seen: set[str] = set()
    order: list[tuple[str, str]] = []
    depth = max((len(q.all_of[0]) for q in groups), default=0)
    for level in range(depth):
        for q in groups:
            if level < len(q.all_of[0]):
                term = q.all_of[0][level]
                key = " ".join(term.casefold().split())
                if key and key not in seen:
                    seen.add(key)
                    order.append((term, q.id))
    if first:  # names skipped last run go first this time
        wanted = {" ".join(t.casefold().split()) for t in first}
        order = ([o for o in order if " ".join(o[0].casefold().split()) in wanted]
                 + [o for o in order if " ".join(o[0].casefold().split()) not in wanted])
    return order[:limit], order[limit:]


def search_terms(queries: list[Query]) -> list[tuple[str, str]]:
    return plan_terms(queries)[0]


class SocialCollector:
    name = "social"

    def __init__(self, platforms: list[str], run: Callable[..., dict] = run_reach,
                 progress: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 own_accounts: list[str] | None = None,
                 watch_accounts: dict[str, list[str]] | None = None):
        self.platforms = [p for p in platforms if p in ROUTES]
        self.watch = {k: [h.strip().lstrip("@") for h in v if h.strip()]
                      for k, v in (watch_accounts or {}).items()}
        self.priority_terms: list[str] = []   # set by the pipeline: names skipped last run
        self.deferred_terms: list[str] = []   # names this run had no budget for
        # The organisation's own posts are not coverage, like its own website.
        handles = [h.strip().lstrip("@").lower() for h in own_accounts or [] if h.strip()]
        self._own = (re.compile(r"/(?:posts/|company/|in/|@)?(?:%s)(?:[_/?#]|$)"
                                % "|".join(map(re.escape, handles))) if handles else None)
        self.run = run
        self.progress = progress
        self.clock = clock

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        if not self.platforms:
            return [], []
        if self.progress:
            self.progress(f"Social: searching {', '.join(PLATFORM_NAMES[p] for p in self.platforms)}")
        start = self.clock()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda p: self._platform(p, queries, window, start), self.platforms))
        seen: dict[str, Item] = {}  # several search terms often find the same post
        for got, _ in results:
            for i in got:
                seen.setdefault(i.url, i)
        items = list(seen.values())
        statuses = [s for _, sts in results for s in sts]
        return items, statuses

    def _platform(self, platform: str, queries: list[Query], window: RunWindow,
                  start: float) -> tuple[list[Item], list[SourceStatus]]:
        channel, action, prefix = ROUTES[platform]
        items: list[Item] = []
        statuses: list[SourceStatus] = []
        if platform == "instagram":
            return self._watched(platform, channel, queries, window, start)
        terms, deferred = plan_terms(queries, first=self.priority_terms)
        for term, _ in deferred:
            if term not in self.deferred_terms:
                self.deferred_terms.append(term)
        statuses += [SourceStatus(self.name, f"{platform}:{term}", False, 0,
                                  "not searched this run (search budget); searched first next run", None)
                     for term, _ in deferred]
        for term, qid in terms:
            target = f"{platform}:{term}"
            if self.clock() - start > BUDGET_SECONDS:
                statuses.append(SourceStatus(self.name, target, False, 0, "time budget exceeded, skipped", None))
                continue
            text = f'{prefix} "{term}"' if prefix else term
            out = self.run([channel, action, text, "--limit", str(LIMIT)])
            if not out.get("ok"):
                err = out.get("error") or {}
                reason = _reason(err)
                statuses.append(SourceStatus(self.name, target, False, 0, reason, None))
                if err.get("code") in ("NEED_SETUP", "NEED_LOGIN", "PLATFORM_BLOCKED", "RATE_LIMITED"):
                    break  # the same answer would come back for every term
                continue
            raw = out.get("items") or []
            if platform == "youtube":
                raw = self._youtube_dates(raw)
            got = [it for it in (self._item(platform, r, queries, qid) for r in raw)
                   if it and window.since <= it.published_at < window.until]
            items.extend(got)
            statuses.append(SourceStatus(self.name, target, True, len(got), None, window.since))
        return items, statuses

    def _watched(self, platform: str, channel: str, queries: list[Query], window: RunWindow,
                 start: float) -> tuple[list[Item], list[SourceStatus]]:
        """Recent posts of the accounts the project watches, kept when they name a search term."""
        accounts = self.watch.get(platform) or []
        if not accounts:
            return [], [SourceStatus(self.name, f"{platform}:*", False, 0,
                                     "Instagram can't be searched for posts; add accounts to watch", None)]
        items: list[Item] = []
        statuses: list[SourceStatus] = []
        for handle in accounts:
            target = f"{platform}:@{handle}"
            if self.clock() - start > BUDGET_SECONDS:
                statuses.append(SourceStatus(self.name, target, False, 0, "time budget exceeded, skipped", None))
                continue
            out = self.run([channel, "posts", handle, "--limit", str(LIMIT)])
            if not out.get("ok"):
                err = out.get("error") or {}
                statuses.append(SourceStatus(self.name, target, False, 0, _reason(err), None))
                if err.get("code") in ("NEED_SETUP", "NEED_LOGIN", "PLATFORM_BLOCKED", "RATE_LIMITED"):
                    break
                continue
            got = []
            for r in out.get("items") or []:
                it = self._item(platform, r, queries, "")
                if it and window.since <= it.published_at < window.until and matching_ids(
                        queries, f"{it.title} {it.snippet}"):
                    got.append(it)
            items.extend(got)
            statuses.append(SourceStatus(self.name, target, True, len(got), None, window.since))
        return items, statuses

    def _youtube_dates(self, raw: list[dict]) -> list[dict]:
        out = []
        for r in raw[:YOUTUBE_READS]:
            if not r.get("published_at") and r.get("url"):
                got = self.run(["youtube", "read", r["url"]], timeout=30)
                first = (got.get("items") or [{}])[0] if got.get("ok") else {}
                r = {**r, "published_at": first.get("published_at")}
            out.append(r)
        return out

    def _item(self, platform: str, r: dict, queries: list[Query], qid: str) -> Item | None:
        published = _date(r.get("published_at"))
        url = (r.get("url") or "").strip()
        if not published or not url:
            return None  # undated posts cannot be placed in a report period
        if self._own and self._own.search(url.lower()):
            return None
        text = " ".join(str(r.get("text") or "").split())
        title = " ".join(str(r.get("title") or "").split()) or text[:140]
        author = str(r.get("author") or "").strip()
        name = PLATFORM_NAMES[platform]
        return Item(
            source=f"social:{platform}", outlet=f"{name} · {author}" if author else name,
            outlet_domain=PLATFORM_DOMAINS[platform], url=url, title=title,
            published_at=published, collected_at=datetime.now(timezone.utc),
            snippet=text[:SNIPPET_MAX],
            matched_queries=matching_ids(queries, f"{title} {text}") or [qid],
        )


def setup_platforms(platforms: list[str], run: Callable[..., dict] = run_reach) -> dict:
    """Prepare the private browser for platforms that need it. Installs software: ask first."""
    run(["configure", "browser-port", str(REACH_PORT)], timeout=60)
    done, failed = [], {}
    for p in platforms:
        channel = BROWSER_CHANNELS.get(p)
        if not channel:
            continue
        out = run(["add", channel], timeout=900)
        if out.get("ok"):
            done.append(p)
        else:
            failed[p] = (out.get("error") or {}).get("message") or "setup failed"
    return {"prepared": done, "failed": failed}


def check_platforms(platforms: list[str], run: Callable[..., dict] = run_reach) -> dict:
    """One small live search per platform: which work now, which need a login."""
    result = {}
    for p in platforms:
        if p not in ROUTES:
            continue
        channel, action, prefix = ROUTES[p]
        out = run([channel, action, f"{prefix} news" if prefix else "news", "--limit", "1"])
        code = (out.get("error") or {}).get("code")
        result[p] = "works" if out.get("ok") else ("needs login" if code in ("NEED_LOGIN", "NEED_SETUP")
                                                   else PLAIN_ERRORS.get(code, "not answering"))
    return result


def login(platform: str, run: Callable[..., dict] = run_reach) -> dict:
    """Open the private browser on the platform's login page; the user logs in themselves."""
    channel = BROWSER_CHANNELS.get(platform)
    if not channel:
        return {"ok": True, "login_needed": False}
    out = run(["login", channel], timeout=900)
    return {"ok": bool(out.get("ok")), "login_needed": True,
            "message": (out.get("error") or {}).get("message")}
