"""Report renderer, citation check, draft input export and run note."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from clipper import layout
from clipper.estimate import (REPORT_BASE_TOKENS, RUN_OVERHEAD_TOKENS,
                                    TOKENS_PER_REPORT_ITEM, TOKENS_PER_TAGGED_ITEM)
from clipper.models import Project
from clipper.pipeline import Caps, load_caps
from clipper.sources import project_outlets
from clipper.store import Store, _iso
from clipper.tags import check_reportable

ESTIMATE_REPORT_ITEMS = 40

METHOD_WORDS = re.compile(
    r"\bgoogle news\b|\bgdelt\b|\brss\b|\bfeeds?\b|\bsearch engine\b|\bscrap\w*"
    r"|\bai agent\b|\bthe agent\b|\btokens?\b", re.IGNORECASE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
LINE_PREFIX = re.compile(r"^(\s*(?:[-*]\s+|\d+\.\s+|#{1,6}\s+))?(.*)$", re.DOTALL)


# ---------------------------------------------------------------- helpers

def _date(d: datetime) -> str:
    return f"{d:%d %b %Y}"


def _file_date(until: datetime) -> str:
    return f"{(until - timedelta(seconds=1)).date():%Y-%m-%d}"


# ---------------------------------------------------------------- method-word guard

def _guard_line(line: str, removed: list[str]) -> str | None:
    """Drop sentences that mention how items were found. None means the whole line goes."""
    if not METHOD_WORDS.search(line):
        return line
    prefix, body = LINE_PREFIX.match(line).groups()
    kept = []
    for sentence in SENTENCE_BREAK.split(body.strip()):
        if METHOD_WORDS.search(sentence):
            removed.append(sentence.strip())
        else:
            kept.append(sentence)
    if not kept:
        return None
    return (prefix or "") + " ".join(kept)


# ---------------------------------------------------------------- reference replacement

class _Refs:
    """The run's items, tags and stories."""

    def __init__(self, store: Store, run_id: int):
        self.items = {i.id: i for i in store.items(run_id)}
        self.tags = store.tags(run_id)
        self.relevant = {i for i, t in self.tags.items() if t.get("relevant") is True} & set(self.items)
        self.stories = store.stories(run_id)


_ANY_BRACKET_REF = re.compile(r"\[\[[^\]]*\]\]")
_DRAFT_LINK = re.compile(r"(?<!\[)\[([^\[\]]*)\]\(([^)]*)\)")
_BARE_URL = re.compile(r"<?(?:https?://|www\.)[^\s<>]*>?", re.IGNORECASE)
_LIST_OR_HEADING = re.compile(r"\s*(?:[-*]\s+|\d+\.\s+|#{1,6}\s)")


def _join_wrapped(text: str) -> list[str]:
    """Join consecutive plain lines into one paragraph line, so wrapped sentences stay whole."""
    out: list[str] = []
    joining = False
    for line in text.splitlines():
        plain = bool(line.strip()) and not _LIST_OR_HEADING.match(line)
        if plain and joining:
            out[-1] += " " + line.strip()
        else:
            out.append(line)
        joining = plain
    return out


def _strip_links(lines: list[str]) -> tuple[list[str], int]:
    """Draft prose may not carry its own links: keep link labels, drop bare URLs."""
    out: list[str] = []
    removed = 0
    for line in lines:
        line, n1 = _DRAFT_LINK.subn(lambda m: m.group(1), line)
        line, n2 = _BARE_URL.subn("", line)
        removed += n1 + n2
        if n1 + n2:
            line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
            if not _LIST_OR_HEADING.sub("", line).strip():
                continue
        out.append(line)
    return out, removed


# ---------------------------------------------------------------- report

def _summary(draft: Path, removed_sentences: list[str]) -> tuple[str, int, int]:
    """Claude's short summary: plain prose only, at most three sentences."""
    path = Path(draft)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [ln for ln in _join_wrapped(text) if ln.strip() and not re.match(r"\s*#", ln)]
    lines, removed_links = _strip_links(lines)
    removed_refs = 0
    prose: list[str] = []
    for line in lines:
        line, n = _ANY_BRACKET_REF.subn("", line)
        removed_refs += n
        line = re.sub(r"^\s*(?:[-*]\s+|\d+\.\s+)", "", line)
        kept = _guard_line(re.sub(r"[ 	]{2,}", " ", line).strip(), removed_sentences)
        if kept:
            prose.append(kept)
    text = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(prose)).strip()
    sentences = [s for s in SENTENCE_BREAK.split(text) if s]
    return " ".join(sentences[:layout.SUMMARY_SENTENCES]), removed_refs, removed_links


def _guard_text(text: str) -> str:
    kept = _guard_line(text, [])
    return kept or ""


def render_report(store: Store, run_id: int, draft: Path, project: Project, out_dir: Path) -> dict:
    run = store.run(run_id)
    check_reportable(run.stats, run.status)
    refs = _Refs(store, run_id)
    relevant = len(refs.relevant)
    removed_sentences: list[str] = []
    summary, removed_refs, removed_links = ("", 0, 0)
    if relevant:
        summary, removed_refs, removed_links = _summary(draft, removed_sentences)

    prev = store.previous_run(run_id)
    previous = None
    if prev is not None:
        previous = sum(1 for t in store.tags(prev.id).values() if t.get("relevant") is True)
    data = layout.ReportData(
        project=project, since=run.since, last_day=run.until - timedelta(seconds=1),
        items=refs.items, tags=refs.tags, stories=refs.stories, outlets=project_outlets(project),
        previous_relevant=previous, summary=summary,
        max_items=load_caps(project).max_report_items)
    blocks = layout.build(data, guard=_guard_text)

    date = _file_date(run.until)
    reports = Path(out_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    md_path = reports / f"{project.name}-{date}.md"
    docx_path = reports / f"{project.name}-{date}.docx"
    md_path.write_text(layout.to_markdown(blocks), encoding="utf-8")
    layout.to_docx(blocks, docx_path)
    return {"report_md": str(md_path), "report_docx": str(docx_path),
            "removed_refs": removed_refs, "removed_links": removed_links,
            "removed_sentences": removed_sentences, "fallback": bool(relevant and not summary),
            "relevant": relevant}


# ---------------------------------------------------------------- draft input

def _counts(tags: dict[int, dict]) -> tuple[int, dict, dict]:
    rel = [t for t in tags.values() if t.get("relevant") is True]
    return (len(rel), dict(Counter(t["tone"] for t in rel)),
            dict(Counter(t["related_to"] for t in rel)))


def export_draft_input(store: Store, run_id: int, project: Project, caps: Caps, out: Path) -> dict:
    _run = store.run(run_id)
    check_reportable(_run.stats, _run.status)
    tags = store.tags(run_id)
    items = {i.id: i for i in store.items(run_id)}
    relevant = [items[i] for i, t in tags.items() if t.get("relevant") is True and i in items]
    relevant.sort(key=lambda i: i.published_at, reverse=True)
    relevant.sort(key=lambda i: tags[i.id].get("naming") != "explicit")
    chosen = relevant[:caps.max_report_items]

    all_stories = store.stories(run_id)
    story_of = {i: s for s, ids in all_stories.items() for i in ids}
    relevant_ids = {i.id for i in relevant}
    stories = {}
    for story_id, ids in all_stories.items():
        members = [i for i in ids if i in relevant_ids]
        if members:
            stories[str(story_id)] = members

    previous = None
    prev_run = store.previous_run(run_id)
    if prev_run is not None:
        n, by_tone, by_related = _counts(store.tags(prev_run.id))
        previous = {"relevant": n, "by_tone": by_tone, "by_related_to": by_related}

    payload = {
        "organisation": project.organisation,
        "items": [{
            "id": i.id, "outlet": i.outlet, "title": i.title, "published_at": _iso(i.published_at),
            "type": tags[i.id]["type"], "tone": tags[i.id]["tone"],
            "tone_reason": tags[i.id]["tone_reason"], "naming": tags[i.id]["naming"],
            "related_to": tags[i.id]["related_to"], "summary": tags[i.id]["summary"],
            "subscribers_only": i.subscribers_only, "story_id": story_of.get(i.id),
        } for i in chosen],
        "stories": stories,
        "previous": previous,
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"exported": len(chosen), "relevant": len(relevant),
            "truncated": len(relevant) - len(chosen)}


# ---------------------------------------------------------------- run note

def _source_line(label: str, statuses: list) -> str:
    ok = sum(1 for s in statuses if s.ok)
    line = f"- {label}: {ok} of {len(statuses)} requests ok"
    failed = [f"{s.target} ({s.error})" for s in statuses if not s.ok]
    if failed:
        line += f"; failed: {', '.join(failed)}"
    return line


def render_run_note(store: Store, run_id: int, project: Project, render_stats: dict,
                    out_dir: Path) -> Path:
    run = store.run(run_id)
    statuses = store.source_statuses(run_id)
    tags = store.tags(run_id)
    relevant = sum(1 for t in tags.values() if t.get("relevant") is True)
    rs = render_stats or {}

    lines = [f"# Run note: {project.name}, {_file_date(run.until)}", "",
             f"- Window: {_iso(run.since)} to {_iso(run.until)}",
             f"- Status: {run.status}", "", "## What was checked", ""]

    outlets = [s for s in statuses if s.source == "outlet"]
    lines.append(f"- Checked {sum(1 for s in outlets if s.ok)} of {len(outlets)} outlets")
    not_reached = [s for s in outlets if not s.ok]
    if not_reached:
        lines.append("- Outlets not reached:")
        lines += [f"  - {s.target}: {s.error}" for s in not_reached]
    lines.append(_source_line("Google News", [s for s in statuses if s.source == "google_news"]))
    lines.append(_source_line("GDELT", [s for s in statuses if s.source == "gdelt"]))
    late = [s for s in statuses if s.covered_since and s.covered_since > run.since]
    if late:
        lines.append("- Sources that only cover part of the window:")
        lines += [f"  - {s.source} {s.target}: covered since {_iso(s.covered_since)}" for s in late]

    stats = run.stats
    lines += ["", "## Collection", "",
              f"- Items collected: {stats.get('collected', 0)} · new: {stats.get('new', 0)}"
              f" · malformed: {stats.get('malformed', 0)}"]
    candidates = rs.get("candidates", rs.get("exported"))
    if candidates is not None or "truncated" in rs:
        lines.append(f"- Candidates: {candidates if candidates is not None else 'n/a'}"
                     f" · truncated: {rs.get('truncated', 0)}")
    links = rs.get("google_links") or stats.get("google_links")
    if links and (links.get("resolved") or links.get("failed")):
        line = (f"- Google News links traced to the publisher: {links.get('resolved', 0)}"
                f" · not traced: {links.get('failed', 0)}")
        reasons = links.get("reasons") or {}
        if reasons:
            line += " (" + "; ".join(f"{r}: {n}" for r, n in reasons.items()) + ")"
        lines.append(line)
    if any(k in rs for k in ("applied", "rejected", "unknown_ids", "unknown")):
        lines.append(f"- Tags applied: {rs.get('applied', 0)} · rejected: {rs.get('rejected', 0)}"
                     f" · unknown: {rs.get('unknown_ids', rs.get('unknown', 0))}")

    lines += ["", "## Report", "", f"- Relevant items: {relevant}",
              f"- Citations removed: {rs.get('removed_refs', 0)}",
              f"- Links removed from draft: {rs.get('removed_links', 0)}"]
    if rs.get("fallback"):
        lines.append("- Draft unusable — fallback list used")
    sentences = rs.get("removed_sentences") or []
    if sentences:
        lines += ["", "### Removed from draft", ""]
        lines += [f"- {s}" for s in sentences]

    tokens = (len(tags) * TOKENS_PER_TAGGED_ITEM + REPORT_BASE_TOKENS
              + min(relevant, ESTIMATE_REPORT_ITEMS) * TOKENS_PER_REPORT_ITEM
              + RUN_OVERHEAD_TOKENS)
    per_run = (project.estimate or {}).get("per_run")
    estimated = f"{per_run:,}" if per_run is not None else "no estimate yet"
    lines += ["", "## Usage", "",
              f"- Estimated tokens per run: {estimated} · This run (count-based): {tokens:,}"]

    notes = Path(out_dir) / "run-notes"
    notes.mkdir(parents=True, exist_ok=True)
    path = notes / f"{project.name}-{_file_date(run.until)}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
