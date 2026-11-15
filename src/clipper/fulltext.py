"""Fetch an article page and extract its text; flag subscriber-only items.

No attempt is made to get around a paywall: a page that does not give its
text freely is only marked as subscribers only.
"""
from __future__ import annotations

import dataclasses
import time
from urllib.parse import urlsplit

import requests
import trafilatura

from clipper import gnews_links
from clipper.collectors.outlet_feeds import USER_AGENT
from clipper.models import Item, Outlet

TIMEOUT = 20
MAX_TEXT = 20_000
PAYWALL_OUTLET_MIN = 1200
PAYWALL_PHRASE_MAX = 600
BLOCKED_PATH_WORDS = ("noaccess", "login", "abbonati")
PAYWALL_PHRASES = ("abbonati", "riservato agli abbonati", "contenuto riservato",
                   "subscribe", "subscribers only")
BLOCKED_STATUSES = (401, 402, 403)
GOOGLE_PAUSE_SECONDS = 1.0
GOOGLE_BUDGET = 40  # links resolved per run at most, so a long list cannot get us blocked
_google = {"blocked": False, "used": 0}  # one CLI command is one run


def reset_google_state() -> None:
    _google.update(blocked=False, used=0)


def _publisher_url(url: str, http) -> str | None:
    """The publisher link behind a Google News link, within this run's limits."""
    if _google["blocked"] or _google["used"] >= GOOGLE_BUDGET:
        return None
    if _google["used"]:
        time.sleep(GOOGLE_PAUSE_SECONDS)
    _google["used"] += 1
    try:
        return gnews_links.resolve(url, http)
    except gnews_links.GoogleBlocked:
        _google["blocked"] = True
        return None


def fetch_full_text(item: Item, outlet: Outlet | None, session=None) -> Item:
    """Return a copy of `item` with full_text, canonical_url and subscribers_only set.

    Never raises: on any failure the item is returned unchanged.
    """
    try:
        return _fetch(item, outlet, session or requests)
    except Exception:
        return dataclasses.replace(item)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _on_outlet_site(host: str, outlet_domain: str) -> bool:
    """True if `host` is the outlet's domain or one of its subdomains."""
    domain = (outlet_domain or "").lower().removeprefix("www.")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def _fetch(item: Item, outlet: Outlet | None, http) -> Item:
    # A Google News link is only a redirect page: find the publisher's own link first.
    url = item.url
    via_google = _host(url) == "news.google.com"
    if via_google:
        url = _publisher_url(url, http)
        if not url:
            return dataclasses.replace(item)
    paywall = bool(outlet and outlet.paywall)
    resp = http.get(url, timeout=TIMEOUT, allow_redirects=True,
                    headers={"User-Agent": USER_AGENT})
    if resp.status_code != 200:
        blocked = paywall and resp.status_code in BLOCKED_STATUSES
        return dataclasses.replace(item, subscribers_only=item.subscribers_only or blocked,
                                   canonical_url=url if via_google else item.canonical_url)

    final = urlsplit(resp.url)
    # Bytes, not resp.text: trafilatura detects the encoding itself, so a UTF-8 page
    # without a declared charset keeps its accents.
    text = trafilatura.extract(resp.content, url=resp.url) or ""
    text = text[:MAX_TEXT]
    length = len(text)
    lowered = text.lower()
    target = (final.path + "?" + final.query).lower()

    subscribers_only = (
        (paywall and length < PAYWALL_OUTLET_MIN)
        or any(word in target for word in BLOCKED_PATH_WORDS)
        or (length < PAYWALL_PHRASE_MAX and any(p in lowered for p in PAYWALL_PHRASES))
    )
    # Only a page on the outlet's own site may become the report link.
    canonical = resp.url if _on_outlet_site(_host(resp.url), item.outlet_domain) else None
    if via_google and canonical is None:
        # Google's own answer is the publisher link, even if the outlet's domain is unknown.
        canonical = url
    return dataclasses.replace(
        item,
        full_text=text or None,
        canonical_url=canonical,
        subscribers_only=item.subscribers_only or subscribers_only,
    )
