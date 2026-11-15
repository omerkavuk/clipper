---
name: status
description: Show the monitoring status of a media monitoring project. Use when the user says "monitoring status", "when was the last media report", "is my media monitoring working", "when is the next media report due". This is the Clipper plugin, not Agent Reach.
---

# Monitoring status

Explain the state of the user's media monitoring in plain words. They are not technical.

## How to run the commands

The plugin root is two directories above this skill's base directory (Claude shows "Base directory for this skill" when the skill loads). Use that path exactly as shown in the Base directory line, in its native operating-system form (on Windows, `C:\Users\...`). Never convert it to a `/c/Users/...` form, because the command then fails. Always put it in double quotes.

    python -m uv run --directory "<plugin root>" clipper <command> ...

Always quote paths. Every command prints one JSON object. Parse it; on `"ok": false`, stop and explain plainly what went wrong. Do not hide or redirect the command's error output (no `2>$null`, no `2>/dev/null`): `collect` and `test-queries` print short progress lines there, and the user should see them. Only the standard output is the JSON.

## Steps

1. If the project is not named, run `clipper projects`. One project: use it. Several: ask which. None: say nothing is set up yet and offer to start the setup.
2. Run `clipper status --project "<name>"`.
3. Say in plain words: when the last report ran and how it went, mapping `last_run.status`: `ok` means "completed", `partial` means "completed, some sources unavailable", `failed` means "did not complete" (if there is no last run, say none has run yet and the first one is due now); if the last run is `ok` or `partial` but `last_run.stats.reported` is false, say the articles were gathered but the report was not finished yet, and offer to finish it now (the run skill picks it up); which places could not be reached last time (`failed_sources`), in a calm sentence, with the suggestion to run again or check later; and when the next report is due (`next_due`, as a friendly date). If `next_due` is in the past (or "now"), say the report is due and offer to run it now. If you have a tool that lists scheduled tasks, check whether one exists for this project and mention it in one sentence (for example "A task is set to run it every Monday at 08:07"); if none exists, say the report is not scheduled and offer to set it up (that follows the Schedule step of the setup skill, and needs their explicit yes).
4. Do not show raw JSON, run numbers or internal names. Offer to run the report now, or to change the plan.
