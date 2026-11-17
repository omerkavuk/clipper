---
name: edit
description: Change a media monitoring plan. Use when the user says "change my monitoring keywords", "add a programme to monitor", "exclude a website from my media report", "change the report frequency or language", "add or remove an outlet". This is the Clipper plugin, not Agent Reach.
---

# Change the monitoring plan

Help a non-technical colleague change their monitoring plan by conversation. Never show file formats or code.

## How to run the commands

The plugin root is two directories above this skill's base directory (Claude shows "Base directory for this skill" when the skill loads). Use that path exactly as shown in the Base directory line, in its native operating-system form (on Windows, `C:\Users\...`). Never convert it to a `/c/Users/...` form, because the command then fails. Always put it in double quotes.

    uv run --directory "<plugin root>" clipper <command> ...

If `uv` is not found, use `python3 -m uv run …` (macOS and Linux) or `python -m uv run …` (Windows) instead, with the same arguments. Remember which form worked and use it for every later command.

Always quote paths. Every command prints one JSON object. Parse it; on `"ok": false`, stop and explain plainly what went wrong. Do not hide or redirect the command's error output (no `2>$null`, no `2>/dev/null`): `collect` and `test-queries` print short progress lines there, and the user should see them. Only the standard output is the JSON.

## Steps

1. Find the project: `clipper projects` (ask which if several; offer the setup if none). Its plan is the `project.yaml` file in the project folder.
2. Read the plan file and tell the user, in plain words, what is currently being monitored. Ask what they want to change, one thing at a time.
3. Make the change by editing the plan file, keeping its structure. Possible changes: names and variants, programmes, events, spokespeople, contexts, competitors, excluded terms or websites, country and languages, frequency, report language, readers, social platforms (`social`), other names for a programme or person (`aliases`), and how programmes are grouped in the report (`report_groups`: a report heading mapped to the programmes it covers, for example `Solar Villages and VELA: [Solar Villages]`; the key `other` renames the heading for coverage outside the programmes). Propose variants in every language they follow. Give a plain summary of what changed; never show the file.
   - **Sources:** to add sources, research their main website and kind, write `<project folder>/work/source-candidates.json` (a list of `{"name", "domain", "tier"}`, tiers: national, regional, agency, broadcaster, specialist, trade, institutional, think_tank) and run `clipper sources --project "<name>" --add "<file>"`. To remove, run `clipper sources --project "<name>" --remove <domains>`. To show the list, run `clipper sources --project "<name>"` and give the counts by kind. Never edit the bundled country lists inside the plugin.
4. Run `clipper test-queries --project "<name>"`. If it reports `"all_sources_failed": true`, say the sources could not be reached, show no estimate, and offer to retry in about 30 minutes. Otherwise explain the results and warnings in plain words (finds nothing, too generic, mostly your own website), and iterate if needed.
5. Show the new estimate: "about N tokens per run, about M per month", reading N from `estimate.per_run` and M from `estimate.per_month` in the `clipper test-queries` output directly (no arithmetic of your own), and what drives it. Ask whether they accept it. If the frequency changed, say the schedule will need updating (offer to update the scheduled task to match, following the Schedule step of the setup skill, and change it only after their explicit yes).
