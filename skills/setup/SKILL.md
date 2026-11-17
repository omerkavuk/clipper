---
name: setup
description: Set up media monitoring for an organisation with Clipper. Use when the user says "set up media monitoring", "set up Clipper", "start monitoring the news for my organisation", "new monitoring project", or wants a regular press, news or social media coverage report. This is the Clipper plugin, not Agent Reach.
---

# Clipper setup wizard

You run a fixed setup wizard for a communications colleague. It must feel like a product, not a chat.

## Rules for every screen

- **Ask every question with the question tool** (AskUserQuestion or the platform's equivalent) so the user can click an answer. Put the "→" line in the question text. Only if no such tool exists, show the options as a short numbered list. The question tool shows at most 4 options per question: when a screen has more (social media has 7), split it into two questions on the same screen, for example "News and video" (LinkedIn, YouTube, Reddit) and "Needs a login" (X, Instagram, Facebook, TikTok), both multi-select.
- **One screen per message.** A screen is: a bold heading, at most two short lines, then the question. Nothing else.
- **Never comment between screens.** No "Great!", no recap of the last answer, no explanations of how Clipper works, no file names, commands, codes or technical words. If the user asks something, answer in one or two sentences and repeat the current screen.
- **Use the wording below.** Fill in the `{…}` parts. Keep the "→" line: it tells the user what you will do with the answer.
- Work silently between screens: run commands and edit files without narrating them. If a step takes more than a few seconds, show one line ("Building your source list…") and nothing else.

## How to run the commands

The plugin root is two directories above this skill's base directory (Claude shows "Base directory for this skill" when the skill loads). Use that path exactly as shown, in its native form (on Windows, `C:\Users\...`). Never convert it to a `/c/Users/...` form, because the command then fails. Always put it in double quotes.

    uv run --directory "<plugin root>" clipper <command> ...

If `uv` is not found, use `python3 -m uv run …` (macOS and Linux) or `python -m uv run …` (Windows) instead, with the same arguments. Remember which form worked and use it for every later command.

Quote every path. Every command prints one JSON object on standard output; parse it. Do not hide or redirect error output: `test-queries` prints progress there for the user. On `"ok": false`, tell the user in one plain sentence what went wrong and offer to try again.

## The screens

**Screen 0: Welcome.** Show exactly:

> **Clipper watches the media for your organisation and sends you a report you can forward.**
>
> It can watch:
> - **News sites**: national and regional papers, news agencies, TV and radio sites, trade press
> - **Think tanks, research centres, government and institutional sites**
> - **Social media**: LinkedIn, X, Instagram, Facebook, YouTube, Reddit
>
> Setup is 10 short questions. At the end I build your source list, test it on last week's news and show you the cost. Nothing runs until you approve it.

Options: "Start setup" · "What does a report look like?" (show `examples/sample-report.md` from the plugin root, then show Screen 0 again).

**Screen 1: Organisation.** "**Which organisation should I watch?**" → "I'll search for this name, its translations and its acronym in every language you pick." Free text.

**Screen 1b: Existing project (only if one exists).** Right after Screen 1, silently run `clipper projects`. If a project with that name (or a close variant of it) is listed, read its `project.yaml` in the listed `folder`, and if `sources.yaml` exists there, count its outlets. Then ask: "**You already have a Clipper project for {organisation}.**" → "Updating keeps your past reports and history; starting fresh moves the old files into a backup folder." Options: "Update it" · "Start fresh".
- **Update it** (update mode): go through Screens 2–10 as usual, but take every default from the existing `project.yaml`: pre-tick or pre-fill what is already there (its country, languages, names in `subject`, programmes, spokespeople, contexts, exclusions, social platforms, frequency, readers) and keep its names exactly as written (for example the official full name) rather than generating new ones; you may add suggestions next to them. Do not run `init` in "Build the plan": edit the existing `project.yaml` in place. On Screen 11, if `sources.yaml` exists, first ask "**Keep your {n} sources?**" with options "Keep my list" (skip the research and go straight to the summary screen, using `clipper sources --project "<name>"` for the counts, and run it with `--tiers` only if the kinds of sources changed on Screen 7) · "Rebuild it" (do Screen 11 as usual).
- **Start fresh**: carry on as for a new project, and in "Build the plan" add `--replace` to `clipper init`. Do not mention the backup folder unless the user asks.

**Screen 2: Country.** "**Which country's media?**" → "At the end of setup I'll build a list of {country}'s news outlets, think tanks and institutions, and show it to you for approval." Offer up to 3 likely countries as options; the user can type another. Note its two-letter ISO code (FR, KE, BR) yourself; never show codes.

**Screen 3: Languages.** "**Which languages does that press write in?**" → "I'll search in each language you tick." Multi-select: the country's main language or languages, English, Other. Note two-letter codes (fr, en).

**Screen 4: Names to search.** "**Which names should count as a mention?**" → "Each ticked name becomes a search. Untick any the press doesn't use." Multi-select of names you generate: the official name, its translation into each chosen language, the acronym, common short forms. The user can type more. Also note, silently, the parent organisation's names (for example Aurora Foundation for Aurora Climate Lab) in `parent_names`; they help sort articles but are not searched on their own.

**Screen 5: Programmes, events and people.** "**Any programmes, events, products or spokespeople to follow?**" → "These usually find more coverage than the organisation's name. Names that are also everyday words are only counted next to your organisation's name." Options: "None" · "Type them". Sort what they type: people go to `spokespeople`; distinctive names go to `programmes`; a name that is an everyday word or a short acronym in any monitored language (for example "vela" in Italian) goes under `contexts`, for example `contexts: {initiatives: [VELA]}`, so it only counts together with the organisation's name. For every programme and spokesperson, also note silently the other ways the press writes the name (a shorter form, the acronym with or without the organisation's name, a translation in each chosen language, a common misspelling), and store them in `aliases`, for example `aliases: {Energy for Growth in Africa: [Energy for Growth, E4G]}`; every variant is searched. Do not tell the user about this sorting.

**Screen 6: Leave out.** "**What should never appear in the report?**" → "Matching articles are dropped before the report is written." Multi-select: "Your own websites: {domains you propose}" (pre-ticked) · "Your own social media accounts: {handles you propose, for example aurora-climate-lab on LinkedIn}" (pre-ticked) · "Job adverts, internships and calls for applications" (pre-ticked) · "Other meanings of your names (type them)". Own websites go to `exclude_domains`; own account handles (as they appear in the account's web address) go to `own_accounts`. Job adverts: add `exclude_terms` in each language (for example vacancy, internship, call for applications, tirocinio, stage, offerta di lavoro); job boards are already excluded.

**Screen 7: Types of sources.** "**Which kinds of sources should I include?**" → "I'll put these in your {country} source list." Multi-select, all pre-ticked except trade: National news (`national`) · Regional and local news (`regional`) · News agencies (`agency`) · TV and radio (`broadcaster`) · Trade and specialist press (`trade`, `specialist`) · Think tanks and research (`think_tank`) · Government and institutions (`institutional`).

**Screen 8: Social media.** "**Should I also watch social media?**" → "Platforms you pick are searched for posts that mention you. X, Instagram and Facebook need a one-time login in a private browser; I never see your password." Multi-select with these exact option descriptions:
- None
- LinkedIn: "public posts found through web search; some posts will be missed"
- X: "needs a one-time login"
- Instagram: "needs a one-time login"
- Facebook: "needs a one-time login"
- YouTube: "videos that mention you"
- Reddit: "discussions that mention you"

Store the picks in `social` as `linkedin`, `x`, `instagram`, `facebook`, `youtube`, `reddit`. TikTok cannot be searched by date yet: if the user asks for it, say so in one sentence.

**Screen 9: How often.** "**How often do you want the report?**" → "Each report covers everything since the last one." Options: "Weekly, on Monday morning" (default) · "Daily" · "Monthly".

**Screen 10: Who reads it.** "**Who will read the report?**" → "This sets how long the summary is. Reports are in English for now." Options: "Just me" · "My team" · "Management" · "Clients or partners". Store the answer in `readers`. Always set `report_language` to `en`.

## Build the plan (silent)

1. New project or "Start fresh": run `clipper init --name "<short project name>" --country <ISO code> --languages <codes, comma-separated>` (add `--replace` after "Start fresh"). It gives `project_file` and `folder`. In update mode, skip this step: `project_file` is the existing `project.yaml`. If `init` still reports that a project already exists, never stop there: show Screen 1b.
2. Edit `project_file` with your file tools so it holds every answer (fields: name, organisation, subject, contexts, aliases, competitors, exclude_terms, exclude_domains, programmes, spokespeople, country, languages, frequency, report_language, readers, outlets_file, social, own_accounts, parent_names, and `report_groups` if it already has one). Keep the structure and the `country` and `outlets_file` that `init` set.

**Screen 10b: Social login (only if X, Instagram or Facebook was picked).** "**X, Instagram and Facebook need a one-time setup.**" → "I'll install a private browser for Clipper (a download of a few hundred MB). Then you log in once in the window that opens. Your own browser, accounts and other tools are not changed." Options: "Set it up now" · "Skip these platforms". Only after "Set it up now": run `clipper social-setup --project "<name>"` (it can take several minutes), then for each of those platforms run `clipper social-login --platform <x|instagram|facebook>`, which opens a login window: tell the user "Log in to {platform} in the window that just opened, then close it." and wait for them. Finish with `clipper social-check --project "<name>"`. Any platform that still says "needs login" is reported in one line, and the rest of setup continues. On "Skip these platforms", remove them from `social`.

**Screen 11: Your source list.** Show one line: "**Building your {country} source list…**". Then, silently:

1. Research the country's sources of the kinds ticked on Screen 7 with your web search tool: the main national papers and news sites, regional papers for the main regions, news agencies, TV and radio news sites, think tanks and research centres, government ministries and public institutions relevant to the organisation's field. Aim for about 15–25 national, 10–20 regional, 2–5 agencies, 3–8 broadcasters, 5–15 think tanks and 5–15 government and institutional sites. Use each source's main website domain.
2. Write them to `<project folder>/work/source-candidates.json` as a list of `{"name": …, "domain": …, "tier": …}` using the tier codes from Screen 7 (add `"region": …` for regional ones).
3. Run `clipper sources --project "<name>" --tiers <ticked tier codes, comma-separated> --add "<project folder>/work/source-candidates.json"`. It keeps the matching part of any ready-made list for the country, checks every new site, finds its news feed, and saves the project's list. Unreachable sites are left out (`unreachable`).

Then show:

> **{sources} sources for {country}:**
> {count} national news · {count} regional · {count} agencies · {count} TV and radio · {count} think tanks · {count} government and institutions
> Plus: {social platforms, or "no social media"}.
>
> Examples: {six well-known names from the list}

Options: "Looks good" · "Add sources" · "Remove sources" · "See the full list". For adds, research the names they give, write a new candidates file and run `clipper sources --project "<name>" --add "<file>"`. For removes, run `clipper sources --project "<name>" --remove <domains, comma-separated>`. Show the updated screen. For the full list, read `<project folder>/sources.yaml` and show names grouped by type (no domains, no file).

**Screen 12: Test on last week.** Show "**Checking last week's news…**" and run `clipper test-queries --project "<name>"` (it takes a few minutes; its progress lines show on their own). Then:

- If the output has `"all_sources_failed": true`: say "The news sources aren't answering right now. I'll try again in 30 minutes." Show no numbers and no cost. Offer "Try again now" · "Try later".
- Otherwise show one line per search, "{label}: {hits} articles", with a plain-words warning where the output has one: "finds nothing" → "nothing found last week; check the spelling or add a variant"; "too generic" → "too common a word: I'll only count it next to your organisation's name" (and move it to `contexts`); "mostly your own website" → "mostly your own site, which is already left out". Options: "Looks right" · "Fix a search" (propose the fix, apply it, test again) · "Test again".

**Screen 13: Cost.** "**Each {weekly|daily|monthly} report uses about {estimate.per_run} tokens (about {estimate.per_month} a month).**" → "Most of it is reading and sorting the articles; the rest is writing the summary." Take both numbers from the last `test-queries` output exactly as given; do no arithmetic. Options: "Approve" · "Make it cheaper" (offer fewer sources or stricter searches, apply, test again). Do not continue without "Approve".

**Screen 14: Schedule.** "**Send the report automatically every {Monday at 08:07}?**" → "It runs on this computer. If the computer is off, the next run covers the missed period." Options: "Yes, schedule it" · "Not now: I'll ask for it". Create a schedule only after an explicit yes, following `scheduling.md` in this skill's folder.

**Screen 15: Done.** "**You're set.** First report: {date}. Ask "run my media report" any time, or "media monitoring status"."
