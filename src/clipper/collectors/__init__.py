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
