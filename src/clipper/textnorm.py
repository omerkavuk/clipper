from __future__ import annotations

import re
import unicodedata


def norm(s: str) -> str:
    """Lowercase, strip accents, unify apostrophes, collapse whitespace. Pure."""
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()
