"""Turn Google News redirect links into the publishers' own article URLs.

Google News feed links (news.google.com/rss/articles/<id>) only point to a redirect page.
That page carries a signature and timestamp; Google's own page then asks its batchexecute
endpoint for the real URL. This module makes the same requests, in the way the open-source
googlenewsdecoder package (MIT, github.com/SSujitX/google-news-url-decoder) found reliable:

- the article page is asked for with a locale (?hl=&gl=&ceid=); without it Google sends
  tools like this one to a "sorry" or consent page instead;
- many links share one batchexecute request, so a long list costs few requests.

Failures never stop a run: the caller keeps the Google link and records the reason.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit

ARTICLE_PAGE = "https://news.google.com/rss/articles/{id}"
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
BLOCKED_STATUSES = (429, 503)
BATCH_SIZE = 20
DEFAULT_LOCALE = ("en-US", "US", "US:en")
# Sent as well, for visitors from the EU, in case the locale alone is not enough.
CONSENT_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}
_SIG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
_CONTEXT = [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None,
             None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0]


class GoogleBlocked(Exception):
    """Google asked us to slow down: stop resolving for this run."""


class Unresolved(Exception):
    """The link could not be resolved; the message says why (for the run note)."""


def article_id(url: str) -> str | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "news.google.com":
        return None
    segments = [s for s in parts.path.split("/") if s]
    for key in ("articles", "read"):
        if key in segments:
            i = segments.index(key)
            if i + 1 < len(segments):
                return segments[i + 1]
    return None


def page_url(gid: str, source_url: str = "") -> str:
    """The article page with a locale, copied from the Google link when it has one."""
    qs = parse_qs(urlsplit(source_url).query)
    hl = qs.get("hl", [DEFAULT_LOCALE[0]])[0]
    gl = qs.get("gl", [DEFAULT_LOCALE[1]])[0]
    ceid = qs.get("ceid", [DEFAULT_LOCALE[2]])[0]
    return (ARTICLE_PAGE.format(id=gid)
            + f"?hl={quote(hl, safe='')}&gl={quote(gl, safe='')}&ceid={quote(ceid, safe='')}")


def signature(url: str, http) -> tuple[str, str, str]:
    """(id, timestamp, signature) from the article page. Raises GoogleBlocked or Unresolved."""
    gid = article_id(url)
    if not gid:
        raise Unresolved("not a Google News link")
    page = http.get(page_url(gid, url), timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                    cookies=CONSENT_COOKIES)
    if page.status_code in BLOCKED_STATUSES:
        raise GoogleBlocked()
    final = getattr(page, "url", "") or ""
    if "consent.google" in final:
        raise Unresolved("stopped at Google's consent page")
    if "google.com/sorry" in final:
        raise GoogleBlocked()
    if page.status_code != 200:
        raise Unresolved(f"redirect page answered HTTP {page.status_code}")
    sig, ts = _SIG.search(page.text), _TS.search(page.text)
    if not sig or not ts:
        raise Unresolved("redirect page had no signature")
    return gid, ts.group(1), sig.group(1)


def _body(items: list[tuple[str, str, str]]) -> str:
    envelopes = []
    for n, (gid, ts, sig) in enumerate(items):
        inner = ["garturlreq", _CONTEXT, gid, int(ts) if str(ts).isdigit() else ts, sig]
        envelopes.append(["Fbv4je", json.dumps(inner, separators=(",", ":")), None, str(n)])
    return "f.req=" + quote(json.dumps([envelopes], separators=(",", ":")))


def _parse(text: str) -> dict[int, str]:
    """{position in the batch: publisher URL} from a batchexecute answer."""
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text
    body = body.lstrip()
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[1] if "\n" in body else body[4:]
    out: dict[int, str] = {}
    for n, row in enumerate(json.loads(body)):
        if not (isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and isinstance(row[2], str)):
            continue
        try:
            found = json.loads(row[2])[1]
        except (ValueError, IndexError, TypeError):
            continue
        if not (isinstance(found, str) and found.startswith("http")):
            continue
        key = row[-1] if isinstance(row[-1], str) and row[-1].isdigit() else str(n)
        out[int(key)] = found
    return out


def decode(items: list[tuple[str, str, str]], http) -> dict[str, str]:
    """{id: publisher URL} for up to BATCH_SIZE signed ids, in one request."""
    if not items:
        return {}
    resp = http.post(BATCH_URL, timeout=TIMEOUT, data=_body(items), cookies=CONSENT_COOKIES,
                     headers={"User-Agent": USER_AGENT,
                              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    if resp.status_code in BLOCKED_STATUSES:
        raise GoogleBlocked()
    if resp.status_code != 200:
        raise Unresolved(f"lookup answered HTTP {resp.status_code}")
    try:
        found = _parse(resp.text)
    except (ValueError, TypeError):
        raise Unresolved("lookup answer could not be read") from None
    out = {}
    for n, url in found.items():
        if 0 <= n < len(items) and (urlsplit(url).hostname or "").lower() != "news.google.com":
            out[items[n][0]] = url
    return out


def resolve_many(urls: list[str], http, sleep: Callable[[float], None] = time.sleep,
                 pause: float = 1.0, limit: int | None = None) -> dict[str, str | Exception]:
    """{Google link: publisher URL, or the exception saying why not}. One page request per
    link, then one lookup per BATCH_SIZE links. Stops asking Google once it says slow down."""
    out: dict[str, str | Exception] = {}
    signed: list[tuple[str, tuple[str, str, str]]] = []
    blocked = False
    requests_made = 0
    todo = [u for u in dict.fromkeys(urls) if article_id(u)]
    for n, url in enumerate(todo):
        if blocked or (limit is not None and n >= limit):
            out[url] = GoogleBlocked() if blocked else Unresolved("look-up limit for this run reached")
            continue
        if requests_made:
            sleep(pause)
        requests_made += 1
        try:
            signed.append((url, signature(url, http)))
        except GoogleBlocked as e:
            blocked = True
            out[url] = e
        except Exception as e:  # Unresolved or network trouble: keep the reason
            out[url] = e if isinstance(e, Unresolved) else Unresolved(f"network error: {type(e).__name__}")
    for i in range(0, len(signed), BATCH_SIZE):
        chunk = signed[i:i + BATCH_SIZE]
        if blocked:
            out.update({u: GoogleBlocked() for u, _ in chunk})
            continue
        if requests_made:
            sleep(pause)
        requests_made += 1
        try:
            found = decode([s for _, s in chunk], http)
        except GoogleBlocked as e:
            blocked = True
            out.update({u: e for u, _ in chunk})
            continue
        except Exception as e:
            reason = e if isinstance(e, Unresolved) else Unresolved(f"network error: {type(e).__name__}")
            out.update({u: reason for u, _ in chunk})
            continue
        for url, (gid, _, _) in chunk:
            out[url] = found.get(gid) or Unresolved("lookup gave no publisher link")
    return out
