"""Validation of model-produced article tags before they are stored.

Article text is untrusted, so whatever the tagging model returns is checked
against fixed label sets and length limits; only whitelisted keys are kept.
"""
from __future__ import annotations

import json
from pathlib import Path

from clipper.models import Project
from clipper.store import Store

TYPES = {"news", "press_release_reprint", "interview", "opinion", "event_listing", "institutional"}
TONES = {"positive", "neutral", "negative"}
NAMING = {"explicit", "parent_only", "none"}

class TaggingIncomplete(ValueError):
    """The run cannot be reported: its candidates are not all tagged (or collection failed)."""


def check_reportable(stats: dict, status: str) -> None:
    """Raise unless every exported candidate has a committed tag. Guards render and draft input."""
    if status == "failed":
        raise TaggingIncomplete("collection failed for this run; collect again before reporting")
    if "candidate_ids" not in stats:
        raise TaggingIncomplete("candidates were never exported for this run")
    if stats.get("tagged") is not True:
        missing = stats.get("missing_ids")
        detail = f" ({len(missing)} articles still untagged)" if missing else ""
        raise TaggingIncomplete(f"tagging is not complete for this run{detail}")


SUMMARY_MAX = 240
TONE_REASON_MAX = 140


def _validate(entry: dict, programmes: dict[str, str]) -> dict | None:
    """Return the cleaned tag dict, or None if the entry is invalid."""
    if not isinstance(entry.get("relevant"), bool):
        return None
    for key, allowed in (("type", TYPES), ("tone", TONES), ("naming", NAMING)):
        v = entry.get(key)
        if not isinstance(v, str) or v.strip() not in allowed:
            return None
    if "related_to" not in entry:
        return None
    summary = entry.get("summary")
    if not isinstance(summary, str):
        return None
    tone_reason = entry.get("tone_reason", "")
    if tone_reason is None:
        tone_reason = ""
    if not isinstance(tone_reason, str):
        return None
    related = entry["related_to"]
    related = programmes.get(related.strip().casefold(), "other") if isinstance(related, str) else "other"
    return {
        "relevant": entry["relevant"],
        "type": entry["type"].strip(),
        "tone": entry["tone"].strip(),
        "tone_reason": tone_reason.strip()[:TONE_REASON_MAX],
        "naming": entry["naming"].strip(),
        "related_to": related,
        "summary": summary.strip()[:SUMMARY_MAX],
        "outlet_country": _country(entry.get("outlet_country")),
    }


def _country(value) -> str:
    """An optional two-letter country code for the outlet; anything else is dropped."""
    v = value.strip().upper() if isinstance(value, str) else ""
    return v if len(v) == 2 and v.isalpha() else ""


def apply_tags(store: Store, run_id: int, tags_file: Path, project: Project) -> dict:
    """Validate a tagging batch against the exported candidates.

    Valid tags are held as pending. Only when every exported candidate has one valid tag is the
    whole batch committed, in one transaction; until then nothing is visible to the report.
    A retry file only needs the ids listed in `missing_ids`.
    """
    run = store.run(run_id)
    if "candidate_ids" not in run.stats:
        raise ValueError("export the candidates first (clipper candidates)")
    candidates = set(run.stats["candidate_ids"])
    try:
        raw = json.loads(Path(tags_file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"tags file not found: {tags_file}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"tags file is not valid JSON: {tags_file} ({e})") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError(f'tags file must be a JSON object with an "items" list: {tags_file}')

    programmes = {p.casefold(): p for p in project.programmes}
    accepted: dict[int, dict] = {}
    rejected = unknown = 0
    for entry in raw["items"]:
        if not isinstance(entry, dict):
            rejected += 1
            continue
        item_id = entry.get("id")
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            rejected += 1
            continue
        if item_id not in candidates:
            unknown += 1
            continue
        tags = _validate(entry, programmes)
        if tags is None:
            rejected += 1
            continue
        accepted[item_id] = tags  # last valid entry for an id wins
    store.add_pending_tags(run_id, accepted)
    missing = sorted(candidates - set(store.pending_tags(run_id)))
    complete = not missing
    if complete:
        store.commit_pending_tags(run_id, sorted(candidates))
    store.merge_run_stats(run_id, {"tagged": complete, "missing_ids": missing})
    return {"accepted": len(accepted), "applied": len(candidates) if complete else 0,
            "rejected": rejected, "unknown_ids": unknown, "missing_ids": missing,
            "complete": complete}
