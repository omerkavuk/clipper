"""Turn a Google News redirect link into the publisher's own article URL.

Google News feed links (news.google.com/rss/articles/<id>) only point to a redirect page.
The page carries a signature and timestamp; Google's own page then asks its
batchexecute endpoint for the real URL. This module makes those same two requests.
Any failure returns None, and the caller keeps the Google link.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

ARTICLE_PAGE = "https://news.google.com/rss/articles/{id}"
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
BLOCKED_STATUSES = (429, 503)
# Without these, visitors from the EU are sent to Google's consent page instead of the article.
CONSENT_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}
_SIG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')


class GoogleBlocked(Exception):
    """Google asked us to slow down: stop resolving for this run."""


class Unresolved(Exception):
    """The link could not be resolved; the message says why (for the run note)."""


def article_id(url: str) -> str | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "news.google.com":
        return None
    segments = [s for s in parts.path.split("/") if s]
    if "articles" in segments:
        i = segments.index("articles")
        if i + 1 < len(segments):
            return segments[i + 1]
    return None


def _payload(gid: str, ts: str, sig: str) -> str:
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None,
               None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             gid, int(ts), sig]
    return json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])


def _parse(text: str) -> str | None:
    chunk = text.split("\n\n", 1)[1] if "\n\n" in text else text
    data = json.loads(chunk)
    for row in data:
        if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and isinstance(row[2], str):
            url = json.loads(row[2])[1]
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def resolve(url: str, http) -> str | None:
    """The publisher URL behind a Google News link, or None if `url` isn't one.
    Raises GoogleBlocked on 429/503, and Unresolved with the reason on any other failure."""
    gid = article_id(url)
    if not gid:
        return None
    headers = {"User-Agent": USER_AGENT}
    page = http.get(ARTICLE_PAGE.format(id=gid), timeout=TIMEOUT, headers=headers,
                    cookies=CONSENT_COOKIES)
    if page.status_code in BLOCKED_STATUSES:
        raise GoogleBlocked()
    if "consent.google" in (getattr(page, "url", "") or ""):
        raise Unresolved("stopped at Google's consent page")
    if page.status_code != 200:
        raise Unresolved(f"redirect page answered HTTP {page.status_code}")
    sig, ts = _SIG.search(page.text), _TS.search(page.text)
    if not sig or not ts:
        raise Unresolved("redirect page had no signature")
    resp = http.post(BATCH_URL, timeout=TIMEOUT,
                     headers={**headers,
                              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                     data={"f.req": _payload(gid, ts.group(1), sig.group(1))},
                     cookies=CONSENT_COOKIES)
    if resp.status_code in BLOCKED_STATUSES:
        raise GoogleBlocked()
    if resp.status_code != 200:
        raise Unresolved(f"lookup answered HTTP {resp.status_code}")
    try:
        found = _parse(resp.text)
    except (ValueError, IndexError, TypeError):
        raise Unresolved("lookup answer could not be read") from None
    if found and (urlsplit(found).hostname or "").lower() != "news.google.com":
        return found
    raise Unresolved("lookup gave no publisher link")
