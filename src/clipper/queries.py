from __future__ import annotations

import re
from dataclasses import dataclass

from clipper.models import Project
from clipper.textnorm import norm

# ISO 639-1 code -> the language name GDELT's sourcelang: expects.
GDELT_LANGS = {
    "ar": "arabic", "bg": "bulgarian", "bn": "bengali", "ca": "catalan", "cs": "czech",
    "da": "danish", "de": "german", "el": "greek", "en": "english", "es": "spanish",
    "et": "estonian", "fa": "persian", "fi": "finnish", "fr": "french", "he": "hebrew",
    "hi": "hindi", "hr": "croatian", "hu": "hungarian", "id": "indonesian", "it": "italian",
    "ja": "japanese", "ko": "korean", "lt": "lithuanian", "lv": "latvian", "ms": "malay",
    "nl": "dutch", "no": "norwegian", "pl": "polish", "pt": "portuguese", "ro": "romanian",
    "ru": "russian", "sk": "slovak", "sl": "slovenian", "sr": "serbian", "sv": "swedish",
    "sw": "swahili", "th": "thai", "tr": "turkish", "uk": "ukrainian", "ur": "urdu",
    "vi": "vietnamese", "zh": "chinese",
}


@dataclass
class Query:
    id: str
    label: str
    all_of: list[list[str]]  # each inner list is an OR group; groups are ANDed
    exclude: list[str]


def _clean(terms: list[str]) -> list[str]:
    return [t for t in terms if t.strip()]


def build_queries(p: Project) -> list[Query]:
    ex = _clean(p.exclude_terms)
    subject = _clean(p.subject)
    spokespeople = _clean(p.spokespeople)
    out = [Query("subject", "Subject", [subject], ex)]
    for name, terms in p.contexts.items():
        out.append(Query(f"subject×{name}", f"Subject with {name}", [subject, _clean(terms)], ex))
    if spokespeople:
        out.append(Query("subject×spokesperson", "Subject with spokesperson",
                         [subject, spokespeople], ex))
    for name, terms in p.competitors.items():
        out.append(Query(f"subject×{name}", f"Subject with {name}", [subject, _clean(terms)], ex))
        out.append(Query(name, name, [_clean(terms)], ex))
    # Standalone: baseline coverage was found by event names alone.
    for term in _clean(p.programmes):
        out.append(Query(f"programme:{term}", f"Programme {term}", [[term]], ex))
    for name in spokespeople:
        out.append(Query(f"person:{name}", f"Person {name}", [[name]], ex))
    return out


def _quote(t: str) -> str:
    return f'"{t}"' if " " in t else t


def _gdelt_quote(t: str) -> str:
    """GDELT rejects bare terms with dashes or other punctuation ("illegal character"): quote them."""
    return t if t.isalnum() else f'"{t}"'


def _group(terms: list[str], quote) -> str:
    parts = [quote(t) for t in terms]
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def to_google_news(q: Query, exclude_domains: list[str]) -> str:
    parts = [_group(g, _quote) for g in q.all_of]
    parts += [f"-{_quote(t)}" for t in q.exclude]
    parts += [f"-site:{d}" for d in exclude_domains]
    return " ".join(parts)


GDELT_MAX_WORDS = 5  # GDELT rejects queries with long phrases ("too short or too long")


def to_gdelt(q: Query, country: str, languages: list[str], exclude_domains: list[str]) -> str:
    """GDELT query text, or "" when no usable terms remain after dropping long phrases."""
    groups = [[t for t in g if len(t.split()) <= GDELT_MAX_WORDS] for g in q.all_of]
    groups = [g for g in groups if g]
    if not groups:
        return ""
    parts = [_group(g, _gdelt_quote) for g in groups]
    # `country` is GDELT's own (FIPS) code; the collector converts the project's ISO code.
    parts.append(f"sourcecountry:{country}")
    # A language GDELT does not know is left out rather than sent as a filter that matches nothing.
    langs = [f"sourcelang:{GDELT_LANGS[c]}" for c in languages if c in GDELT_LANGS]
    if len(langs) == 1:
        parts.append(langs[0])
    elif langs:
        parts.append("(" + " OR ".join(langs) + ")")
    parts += [f"-{_gdelt_quote(t)}" for t in q.exclude]
    parts += [f"-domain:{d}" for d in exclude_domains]
    return " ".join(parts)


def _has(term: str, text: str) -> bool:
    if not term.strip():
        return False  # an empty pattern would match everywhere
    # Lookarounds (not \b) so terms starting/ending in non-word chars still match.
    return re.search(r"(?<!\w)" + re.escape(norm(term)) + r"(?!\w)", text) is not None


def matches(q: Query, text: str) -> bool:
    t = norm(text)
    if any(_has(x, t) for x in q.exclude):
        return False
    return all(any(_has(x, t) for x in group) for group in q.all_of)


def matching_ids(queries: list[Query], text: str) -> list[str]:
    return [q.id for q in queries if matches(q, text)]
