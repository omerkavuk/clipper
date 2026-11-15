"""Country names and GDELT codes, keyed by ISO 3166-1 alpha-2 (the code a project stores)."""
from __future__ import annotations

from functools import cache
from importlib import resources

import yaml

# Names that read naturally with "the" in "online media in ...".
_WITH_THE = ("United ", "Netherlands", "Philippines", "Bahamas", "Gambia", "Maldives",
             "Holy See", "Comoros", "Seychelles", "Czech Republic", "Dominican Republic",
             "Central African Republic", "Democratic Republic", "U.S. ")


@cache
def _table() -> dict[str, dict]:
    text = (resources.files("clipper") / "data" / "countries.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["countries"]


def is_known(code: str) -> bool:
    return (code or "").upper() in _table()


def name(code: str) -> str:
    """English short name; the code itself when unknown."""
    entry = _table().get((code or "").upper())
    return entry["name"] if entry else code


def in_country(code: str) -> str:
    """'Italy', 'the United Kingdom': the name as it reads after 'in'."""
    n = name(code)
    if n.startswith(_WITH_THE) or n.endswith(("Islands", "Republic", "Emirates")):
        return "the " + n
    return n


def gdelt_code(code: str) -> str | None:
    """GDELT's source-country code (FIPS 10-4), or None when GDELT has none."""
    entry = _table().get((code or "").upper())
    return entry["gdelt"] if entry else None


def outlet_list(code: str) -> str:
    """Name of the bundled outlet list for this country, or "" when none ships yet."""
    key = (code or "").lower()
    if not key.isalpha():
        return ""
    found = (resources.files("clipper") / "data" / "outlets" / f"{key}.yaml").is_file()
    return key if found else ""
