from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from clipper.models import Item
from clipper.textnorm import norm

_DROP_PARAMS = {"fbclid", "gclid", "oc"}
JACCARD_THRESHOLD = (3, 5)  # 0.6 as an exact fraction

STOPWORDS = frozenset(
    "il lo la i gli le un uno una di da del dello della dei degli delle al allo alla ai agli alle "
    "dal dalla dai dalle nel nella nei nelle sul sulla sui per con tra fra che non "
    "the and of for with from in on at to an are was were its his her their this that".split()
)


def normalize_url(url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # malformed URL (bad port, bad IPv6 literal): don't abort the batch
        return url.split("#", 1)[0]
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if port:
        host = f"{host}:{port}"
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS
    ])
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def is_duplicate(a: Item, b: Item) -> bool:
    if normalize_url(a.url) == normalize_url(b.url):
        return True
    return a.outlet_domain == b.outlet_domain and norm(a.title) == norm(b.title)


def _tokens(title: str) -> set[str]:
    # Pure numbers (years, counts) are ignored: they vary between reprints and add noise.
    return {
        t for t in re.findall(r"\w+", norm(title))
        if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()
    }


def title_match(a: str, b: str) -> bool:
    """True when two titles have token Jaccard similarity >= 0.6 (the story-grouping rule)."""
    ta, tb = _tokens(a), _tokens(b)
    union = len(ta | tb)
    num, den = JACCARD_THRESHOLD
    return bool(union) and len(ta & tb) * den >= num * union


def group_stories(items: list[Item]) -> list[list[int]]:
    """Group items about the same story. Returns lists of indices into `items`.

    Two items are linked when the Jaccard similarity of their title tokens is
    >= 0.6; groups are the connected components (union-find).
    """
    toks = [_tokens(i.title) for i in items]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    num, den = JACCARD_THRESHOLD
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            union = len(toks[i] | toks[j])
            if union and len(toks[i] & toks[j]) * den >= num * union:
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())
