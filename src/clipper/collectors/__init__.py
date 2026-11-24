from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from clipper.models import Item, RunWindow
from clipper.queries import Query


@dataclass
class SourceStatus:
    source: str
    target: str
    ok: bool
    items: int
    error: str | None
    covered_since: datetime | None


class Collector(Protocol):
    name: str

    def collect(self, queries: list[Query], window: RunWindow) -> tuple[list[Item], list[SourceStatus]]:
        ...


def summarize_jobs(name: str, q_id: str, done: list, failed: list, items: int, window) -> "SourceStatus":
    """One status for a search run in several languages, periods or orders. It is ok only when
    every part worked; otherwise it says which parts failed, and keeps what was found."""
    from clipper.collectors import SourceStatus
    if not failed:
        return SourceStatus(name, q_id, True, items, None, window.since)
    reasons = "; ".join(dict.fromkeys(f"{label}: {why}" for label, why, _ in failed))
    if not done:
        return SourceStatus(name, q_id, False, 0, reasons, None)
    return SourceStatus(name, q_id, False, items,
                        f"incomplete: {len(failed)} of {len(done) + len(failed)} searches failed ({reasons})", None)
