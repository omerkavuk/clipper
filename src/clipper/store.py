from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from clipper.collectors import SourceStatus
from clipper.dedupe import is_duplicate, normalize_url
from clipper.models import Item, RunWindow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, since TEXT NOT NULL, until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', stats TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
    source TEXT, outlet TEXT, outlet_domain TEXT, url TEXT, url_norm TEXT, title TEXT,
    published_at TEXT, collected_at TEXT, snippet TEXT, full_text TEXT, language TEXT,
    matched_queries TEXT, canonical_url TEXT, subscribers_only INTEGER);
CREATE INDEX IF NOT EXISTS items_run ON items(run_id);
CREATE INDEX IF NOT EXISTS items_url ON items(url_norm);
CREATE INDEX IF NOT EXISTS items_domain ON items(outlet_domain);
CREATE TABLE IF NOT EXISTS tags (item_id INTEGER PRIMARY KEY REFERENCES items(id), tags TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_tags (
    run_id INTEGER NOT NULL REFERENCES runs(id), item_id INTEGER NOT NULL, tags TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id));
CREATE TABLE IF NOT EXISTS stories (item_id INTEGER PRIMARY KEY REFERENCES items(id), story_id INTEGER NOT NULL);
-- An article can belong to several report windows (a week, then the month that holds it).
CREATE TABLE IF NOT EXISTS run_items (
    run_id INTEGER NOT NULL REFERENCES runs(id), item_id INTEGER NOT NULL REFERENCES items(id),
    PRIMARY KEY (run_id, item_id));
-- Tags belong to one report's completed tagging batch; a new batch replaces them all.
CREATE TABLE IF NOT EXISTS run_tags (
    run_id INTEGER NOT NULL REFERENCES runs(id), item_id INTEGER NOT NULL REFERENCES items(id),
    tags TEXT NOT NULL, PRIMARY KEY (run_id, item_id));
CREATE TABLE IF NOT EXISTS source_status (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
    source TEXT, target TEXT, ok INTEGER, items INTEGER, error TEXT, covered_since TEXT);
"""

# A run is "reported" once `clipper render` succeeded for it (stats key "reported": true).
_REPORTED = "json_extract(stats, '$.reported') = 1"


@dataclass
class RunRecord:
    id: int
    since: datetime
    until: datetime
    status: str
    stats: dict


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {d!r}")
    return d.astimezone(timezone.utc).isoformat()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _row_to_item(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], source=r["source"], outlet=r["outlet"], outlet_domain=r["outlet_domain"],
        url=r["url"], title=r["title"], published_at=_dt(r["published_at"]),
        collected_at=_dt(r["collected_at"]), snippet=r["snippet"], full_text=r["full_text"],
        language=r["language"], matched_queries=json.loads(r["matched_queries"]),
        canonical_url=r["canonical_url"], subscribers_only=bool(r["subscribers_only"]),
    )


def _alive(pid: int) -> bool:
    """Is the process still running? (Windows has no harmless os.kill test.)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # query limited information
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Store:
    def __init__(self, db_path: Path):
        self.lock_path = Path(str(db_path) + ".collect.lock")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Databases from before 1.5: every article belongs to the run that found it, and its
        tags to that run."""
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO run_items (run_id, item_id) SELECT run_id, id FROM items")
            if not self._db.execute("SELECT 1 FROM run_tags LIMIT 1").fetchone():
                self._db.execute("INSERT OR IGNORE INTO run_tags (run_id, item_id, tags)"
                                 " SELECT i.run_id, t.item_id, t.tags FROM tags t JOIN items i ON i.id=t.item_id")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # runs
    def start_run(self, window: RunWindow) -> int:
        with self._db:
            cur = self._db.execute("INSERT INTO runs (since, until) VALUES (?, ?)",
                                   (_iso(window.since), _iso(window.until)))
        return cur.lastrowid

    # collection lock: one collect at a time, and a running one is never mistaken for a dead one
    def collect_running(self) -> bool:
        try:
            pid = int(self.lock_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return False
        return _alive(pid)

    def take_collect_lock(self) -> None:
        if self.collect_running():
            raise RuntimeError("another collection is already running for this project")
        self.lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    def release_collect_lock(self) -> None:
        try:
            if self.lock_path.read_text(encoding="utf-8").split()[0] == str(os.getpid()):
                self.lock_path.unlink()
        except (OSError, IndexError):
            pass

    def close_interrupted_runs(self) -> int:
        """Mark runs left at 'running' by a stopped or crashed collect as failed. Does nothing
        while a collect is really running. Returns how many."""
        if self.collect_running():
            return 0
        rows = self._db.execute("SELECT id, stats FROM runs WHERE status='running'").fetchall()
        with self._db:
            for r in rows:
                stats = json.loads(r["stats"] or "{}")
                stats["error"] = "interrupted before it finished"
                self._db.execute("UPDATE runs SET status='failed', stats=? WHERE id=?",
                                 (json.dumps(stats), r["id"]))
        return len(rows)

    def finish_run(self, run_id: int, status: Literal["ok", "partial", "failed"], stats: dict) -> None:
        with self._db:
            self._db.execute("UPDATE runs SET status=?, stats=? WHERE id=?",
                             (status, json.dumps(stats), run_id))

    def run(self, run_id: int) -> RunRecord:
        r = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            raise KeyError(run_id)
        return RunRecord(r["id"], _dt(r["since"]), _dt(r["until"]), r["status"], json.loads(r["stats"]))

    def last_run(self) -> RunRecord | None:
        r = self._db.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return self.run(r["id"]) if r else None

    def merge_run_stats(self, run_id: int, extra: dict) -> None:
        """Add keys to a run's stats, keeping the ones already there."""
        stats = self.run(run_id).stats
        stats.update(extra)
        with self._db:
            self._db.execute("UPDATE runs SET stats=? WHERE id=?", (json.dumps(stats), run_id))

    def mark_reported(self, run_id: int) -> None:
        """Record that a report was rendered for this run (stats key "reported")."""
        self.merge_run_stats(run_id, {"reported": True})

    def _reported_runs(self) -> list[RunRecord]:
        """Runs that finished ok or partial and have a rendered report, newest first."""
        rows = self._db.execute(
            "SELECT id FROM runs WHERE status IN ('ok','partial')"
            f" AND {_REPORTED} ORDER BY id DESC").fetchall()
        return [self.run(r["id"]) for r in rows]

    def previous_run(self, run_id: int) -> RunRecord | None:
        """The most recent earlier run that finished ok or partial and was reported."""
        r = self._db.execute(
            "SELECT id FROM runs WHERE id<? AND status IN ('ok','partial')"
            f" AND {_REPORTED} ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return self.run(r["id"]) if r else None

    def last_reported_run(self) -> RunRecord | None:
        """The reported ok/partial run with the latest window end."""
        runs = self._reported_runs()
        return max(runs, key=lambda r: (r.until, r.id)) if runs else None

    def last_successful_until(self) -> datetime | None:
        """End of the latest window that was collected ok/partial and reported.

        A run that was collected but never rendered does not count, so its
        period is collected again next time.
        """
        last = self.last_reported_run()
        return last.until if last else None

    # items
    def _existing(self, item: Item, url_norm: str) -> int | None:
        """The id of an article already stored that this one duplicates, if any."""
        rows = self._db.execute("SELECT * FROM items WHERE url_norm=? OR outlet_domain=?",
                                (url_norm, item.outlet_domain)).fetchall()
        return next((r["id"] for r in rows if is_duplicate(item, _row_to_item(r))), None)

    def add_items(self, run_id: int, items: list[Item]) -> list[int]:
        """Store new articles and attach them to the run. An article already known is not stored
        twice: the run just includes it. Returns the ids of the articles that were new."""
        ids: list[int] = []
        with self._db:
            for it in items:
                url_norm = normalize_url(it.url)
                known = self._existing(it, url_norm)
                if known is not None:
                    self._db.execute("INSERT OR IGNORE INTO run_items (run_id, item_id) VALUES (?, ?)",
                                     (run_id, known))
                    continue
                cur = self._db.execute(
                    "INSERT INTO items (run_id, source, outlet, outlet_domain, url, url_norm, title,"
                    " published_at, collected_at, snippet, full_text, language, matched_queries,"
                    " canonical_url, subscribers_only) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, it.source, it.outlet, it.outlet_domain, it.url, url_norm, it.title,
                     _iso(it.published_at), _iso(it.collected_at), it.snippet, it.full_text,
                     it.language, json.dumps(it.matched_queries), it.canonical_url,
                     int(it.subscribers_only)))
                self._db.execute("INSERT INTO run_items (run_id, item_id) VALUES (?, ?)",
                                 (run_id, cur.lastrowid))
                ids.append(cur.lastrowid)
        return ids

    def attach_window(self, run_id: int) -> int:
        """Include in the run every known article published in its window, however it was
        found before (a monthly report holds the articles of its weekly reports). Returns how
        many were added."""
        run = self.run(run_id)
        with self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO run_items (run_id, item_id) SELECT ?, id FROM items"
                " WHERE published_at >= ? AND published_at < ?", (run_id, _iso(run.since), _iso(run.until)))
        return cur.rowcount

    def items(self, run_id: int) -> list[Item]:
        rows = self._db.execute("SELECT i.* FROM items i JOIN run_items m ON m.item_id=i.id"
                                " WHERE m.run_id=? ORDER BY i.id", (run_id,))
        return [_row_to_item(r) for r in rows]

    def get_item(self, item_id: int) -> Item | None:
        r = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _row_to_item(r) if r else None

    def update_item(self, item: Item) -> None:
        """Persist the fields that full-text fetching can change."""
        if item.id is None:
            raise ValueError("item has no id")
        with self._db:
            self._db.execute(
                "UPDATE items SET full_text=?, canonical_url=?, subscribers_only=? WHERE id=?",
                (item.full_text, item.canonical_url, int(item.subscribers_only), item.id))

    # tags and stories
    def set_tags(self, item_id: int, tags: dict, run_id: int | None = None) -> None:
        """Tag one article, for one run, or for every run that holds it."""
        runs = [run_id] if run_id is not None else [
            r["run_id"] for r in self._db.execute("SELECT run_id FROM run_items WHERE item_id=?", (item_id,))]
        with self._db:
            self._db.executemany("INSERT OR REPLACE INTO run_tags (run_id, item_id, tags) VALUES (?, ?, ?)",
                                 [(r, item_id, json.dumps(tags)) for r in runs])

    def add_pending_tags(self, run_id: int, tags: dict[int, dict]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO pending_tags (run_id, item_id, tags) VALUES (?, ?, ?)",
                [(run_id, i, json.dumps(t)) for i, t in tags.items()])

    def pending_tags(self, run_id: int) -> dict[int, dict]:
        rows = self._db.execute("SELECT item_id, tags FROM pending_tags WHERE run_id=?", (run_id,))
        return {r["item_id"]: json.loads(r["tags"]) for r in rows}

    def clear_pending_tags(self, run_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM pending_tags WHERE run_id=?", (run_id,))

    def commit_pending_tags(self, run_id: int, item_ids: list[int]) -> None:
        """Make a complete batch the run's tags, in one transaction. Tags from an earlier batch
        for this run are removed, so a report only ever uses the current batch."""
        pending = self.pending_tags(run_id)
        with self._db:
            self._db.execute("DELETE FROM run_tags WHERE run_id=?", (run_id,))
            self._db.executemany("INSERT INTO run_tags (run_id, item_id, tags) VALUES (?, ?, ?)",
                                 [(run_id, i, json.dumps(pending[i])) for i in item_ids])
            self._db.execute("DELETE FROM pending_tags WHERE run_id=?", (run_id,))

    def tags(self, run_id: int) -> dict[int, dict]:
        rows = self._db.execute("SELECT item_id, tags FROM run_tags WHERE run_id=?", (run_id,))
        return {r["item_id"]: json.loads(r["tags"]) for r in rows}

    def set_story(self, item_id: int, story_id: int) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO stories (item_id, story_id) VALUES (?, ?)",
                             (item_id, story_id))

    def stories(self, run_id: int) -> dict[int, list[int]]:
        rows = self._db.execute(
            "SELECT s.story_id, s.item_id FROM stories s JOIN run_items m ON m.item_id=s.item_id"
            " WHERE m.run_id=? ORDER BY s.story_id, s.item_id", (run_id,))
        out: dict[int, list[int]] = {}
        for r in rows:
            out.setdefault(r["story_id"], []).append(r["item_id"])
        return out

    # source status
    def add_source_status(self, run_id: int, status: SourceStatus) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO source_status (run_id, source, target, ok, items, error, covered_since)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, status.source, status.target, int(status.ok), status.items, status.error,
                 _iso(status.covered_since)))

    def source_statuses(self, run_id: int) -> list[SourceStatus]:
        rows = self._db.execute("SELECT * FROM source_status WHERE run_id=? ORDER BY id", (run_id,))
        return [SourceStatus(r["source"], r["target"], bool(r["ok"]), r["items"], r["error"],
                             _dt(r["covered_since"])) for r in rows]
