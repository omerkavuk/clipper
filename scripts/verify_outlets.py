"""Check an outlet list: homepage reachable, feed discoverable, newest entry date.

Usage: python scripts/verify_outlets.py <outlets.yaml> [--write-feeds OUT.yaml]
Prints one line per outlet: domain, ok/FAIL, feed url, newest entry date.
Needs live network.
"""
from __future__ import annotations

import calendar
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import yaml

from clipper.collectors.outlet_feeds import TIMEOUT, USER_AGENT

FALLBACK_PATHS = ["/feed", "/rss", "/feed/", "/rss.xml"]
LINK_RE = re.compile(r"<link\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*["\']([^"\']*)["\']', tag, re.I)
    return m.group(1) if m else None


def feed_links(html: str, base: str) -> list[str]:
    out = []
    for tag in LINK_RE.findall(html):
        rel = (_attr(tag, "rel") or "").lower()
        typ = (_attr(tag, "type") or "").lower()
        href = _attr(tag, "href")
        if href and "alternate" in rel and typ in ("application/rss+xml", "application/atom+xml"):
            out.append(urljoin(base, href))
    return out


def newest_entry(session: requests.Session, url: str) -> tuple[bool, datetime | None]:
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return False, None
        feed = feedparser.parse(r.content)
        if not feed.entries:
            return False, None
        dates = []
        for e in feed.entries:
            parsed = e.get("published_parsed") or e.get("updated_parsed")
            if parsed:
                dates.append(datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc))
        return True, (max(dates) if dates else None)
    except Exception:
        return False, None


def check(session: requests.Session, domain: str) -> dict:
    res = {"domain": domain, "ok": False, "feed": None, "newest": None, "note": ""}
    html, base = None, None
    for host in (domain, "www." + domain):
        try:
            r = session.get(f"https://{host}/", timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        except Exception as e:
            res["note"] = type(e).__name__
            continue
        if r.status_code == 200:
            html, base = r.text, r.url
            break
        res["note"] = f"HTTP {r.status_code}"
    if html is None:
        return res
    res["ok"], res["note"] = True, ""
    candidates = feed_links(html, base) + [urljoin(base, p) for p in FALLBACK_PATHS]
    for url in dict.fromkeys(candidates):
        good, newest = newest_entry(session, url)
        if good:
            res["feed"], res["newest"] = url, newest
            break
    return res


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    outlets = yaml.safe_load(Path(argv[0]).read_text(encoding="utf-8"))["outlets"]
    session = requests.Session()
    results = []
    for o in outlets:
        r = check(session, o["domain"])
        results.append(r)
        newest = r["newest"].date().isoformat() if r["newest"] else "-"
        print(f"{r['domain']}\t{'ok' if r['ok'] else 'FAIL'}\t{r['feed'] or '-'}\t{newest}\t{r['note']}",
              flush=True)
    ok = sum(r["ok"] for r in results)
    print(f"# {ok} ok, {len(results) - ok} failed, {sum(1 for r in results if r['feed'])} with feed")
    if "--write-feeds" in argv:
        out = Path(argv[argv.index("--write-feeds") + 1])
        for o, r in zip(outlets, results):
            o["feed"] = r["feed"]
            o["verified"] = r["ok"]
        out.write_text(yaml.safe_dump({"outlets": outlets}, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
