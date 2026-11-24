from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass
class Item:
    source: str
    outlet: str
    outlet_domain: str
    url: str
    title: str
    published_at: datetime  # timezone-aware UTC
    collected_at: datetime  # timezone-aware UTC
    snippet: str = ""
    full_text: str | None = None
    language: str = ""
    matched_queries: list[str] = field(default_factory=list)
    canonical_url: str | None = None
    subscribers_only: bool = False
    id: int | None = None


@dataclass
class Outlet:
    name: str
    domain: str
    feed: str | None
    tier: str
    region: str | None
    paywall: bool


@dataclass
class RunWindow:
    since: datetime
    until: datetime


@dataclass
class Project:
    name: str
    organisation: str
    subject: list[str]
    contexts: dict[str, list[str]]
    competitors: dict[str, list[str]]
    exclude_terms: list[str]
    exclude_domains: list[str]
    programmes: list[str]
    spokespeople: list[str]
    country: str
    languages: list[str]
    frequency: Literal["daily", "weekly", "monthly"]
    report_language: str
    readers: str
    outlets_file: str
    folder: Path
    estimate: dict | None = None
    caps: dict | None = None
    social: list[str] = field(default_factory=list)  # platforms watched through Agent Reach
    own_accounts: list[str] = field(default_factory=list)  # the organisation's own handles
    parent_names: list[str] = field(default_factory=list)  # e.g. Aurora Foundation for Aurora Climate Lab
    watch_accounts: dict[str, list[str]] = field(default_factory=dict)  # platform -> handles to read
    aliases: dict[str, list[str]] = field(default_factory=dict)  # topic or person -> other names
    report_groups: dict[str, list[str]] = field(default_factory=dict)  # report heading -> programmes
