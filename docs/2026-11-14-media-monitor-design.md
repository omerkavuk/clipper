# Media Monitor Agent: Design

*Working name: "media-monitor". The final public name was the author's to choose; the design does not depend on it.*
*Date: 2026-11-14. Status: draft for approval.*

## 1. Intent and why

Small communications and marketing teams need to know what is being said about their organisation in the media and on social platforms. Paid tools such as Meltwater, Talkwalker and Brandwatch cost thousands a month and are out of reach for them. This project is a **public, open-source monitoring agent that runs inside Claude**. The user **sets up the monitoring in a conversation**, sees **an estimate of how much usage each run will take** before committing, and then gets **regular reports** on a schedule.

What makes it stand out: existing open-source tools (Radar, Harken, OpenMagpie) are dashboards. This one is an agent the user talks to, and it is honest about cost and coverage.

The author will use it first for their own work, then publish it on GitHub for other teams.

## 2. Who it serves

- **Primary:** one person on a small comms or marketing team who runs it on their own computer, with their own logins. They are not necessarily technical, but they can follow an install guide.
- **Readers of the report:** the person themselves and their colleagues or managers.

## 3. Goals, non-goals, anti-goals

**Goals**
- A detailed setup conversation that turns "monitor X in Italian media, Twitter and LinkedIn, weekly" into a concrete, tested monitoring plan.
- A usage estimate per run and per month, based on a real test collection rather than a guess.
- The **same sources checked every run**, and each report states what was checked ("checked 42 outlets, found coverage in 6").
- Reports where every item links to its source and nothing is invented.
- **Reports ready to forward:** a report never mentions how items were found (agent search, Google News, feeds) and never contains technical notes. Technical information goes only to the user, in a separate **run note**.
- **Any country:** whichever country the user picks, the agent builds an extensive, verified list of that country's online media to check (section 7.10).
- **Paywalled coverage counts:** a mention in a subscriber-only article is reported (headline, outlet, link, marked "subscribers only"), even though the full text can't be read.
- It runs on a schedule (weekly, daily or monthly), and can **email the report** to the user's preferred address.
- It is usable by other people after a public release: clear install, clear docs, sensible defaults.

**Non-goals (for the first version)**
- Real-time alerts. Scheduled reports only.
- Print, TV and radio monitoring.
- Getting around paywalls. Subscriber-only articles are *reported*, but nothing tries to unlock their text.
- A web dashboard. The report file (and the email) is the interface.
- Hosting for a whole team. It runs on one person's computer.

**Anti-goals (failure even if it "works")**
- A run that **silently** drops a source. If any platform or outlet could not be reached, the user is told in the run note (and in the email to them), never left to discover it later. The report itself stays clean.
- A report that needs editing before it can be forwarded to colleagues.
- Invented or unverifiable items. Every item must trace back to something a collector actually fetched.
- Touching the user's own Agent Reach install (settings, channels, logins, browser).
- Any write action on any platform: no posting, liking or messaging. Read only.
- Costs that run away: a run must never spend much more than its estimate without stopping.

## 4. Constraints

- **Agent Reach (MIT):** used as a private, pinned copy inside this project (see section 7.4). Its copyright notice is kept.
- **Radar (AGPL-3.0):** we study how it *behaves* and rewrite those ideas in our own code. We do **not** translate or copy its code. The README credits it as inspiration. This keeps the project's licence free to choose.
- **Project licence:** MIT, matching Agent Reach.
- **Platform rules:** this applies only if the user chooses a platform that needs a login (X, LinkedIn, Instagram, TikTok, Facebook). Collection then uses the user's own logged-in session, and these platforms forbid automation. The setup conversation says this plainly when such a platform is picked, and recommends a secondary account. Request volume stays low (a weekly run with a handful of searches). A news-only setup involves no logins at all.
- **Runs locally:** logins live on the user's computer, so scheduled runs happen there and the computer must be on. A missed run catches up on the next start.
- **Platforms:** Windows and macOS. Linux is best effort.
- **Data stays local:** collected items and reports live in the project folder. Item text goes to Claude for analysis, and nothing else leaves the machine except the collectors' own requests.

## 5. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| A | Claude does all the searching (like the 2026-11-14 test) | Simplest to build, but expensive (about 105k tokens for one month of news), different every run, and the estimate is a guess |
| **B (chosen)** | **Scripts collect, Claude reads and writes** | More code, but collection costs no tokens, the sources are fixed, and the estimate is grounded in real data |
| C | Standalone app with a dashboard and the Claude API (like Radar) | Runs without the Claude app, but loses the setup conversation, needs an API key and hosting, and competes with Radar head-on |

**The author chose B.** Evidence from the test: three Google News Italy feed queries, which cost nothing, found four outlets that 105k tokens of agent searching missed.

## 6. How it works (overview)

```
SETUP (conversation, once)
  interview → monitoring plan (project.yaml) → test collection on the last 7 days
  → coverage and noise check → usage estimate → user approves → schedule created

EACH RUN (scheduled)
  collectors (Python, no tokens) → raw items → dedupe and date filter → SQLite
  → Claude: relevance check + tagging (cheap model) → Claude: report (stronger model)
  → citation check (script) → report files + run log
```

Delivered as a **Claude Code plugin** containing:
- **Skills:** `setup` (the interview and plan), `run` (collect, analyse, report), `status` (health of sources and logins, past runs), and `edit` (change the plan later).
- **Python package** (`collectors/`, CLI entry `mm`) with one collector per source and a shared item format.
- **A scheduled task** created at the end of setup.

## 7. Components

### 7.1 Setup conversation

The skill interviews the user one question at a time and builds `project.yaml`. It covers:
1. **Organisation** and its other names: official name, translations, acronyms, and how the press refers to it.
2. **Programmes, events, products and spokespeople.** This is essential: in the test, event names (Green Schools, Solar Villages) found far more than the organisation's name.
3. **Context topics** to relate it to, **competitors or peers** (optional), and **exclusions**: meanings to drop, and the organisation's own websites. In the test, aurora.example swamped the results.
4. **Region and languages** (for example Italy plus Italian, with English optional).
5. **Platforms:** news (always). Optional: X, LinkedIn, Instagram, TikTok and Facebook (all through Agent Reach; X, Instagram, TikTok and Facebook are marked experimental), plus any extra RSS feeds or specific websites. Login steps happen only for the platforms chosen.
6. **Outlet list:** built by the outlet discovery system (7.10) for the chosen country and region, then reviewed by the user, who can add or remove outlets.
7. **Frequency and timing**, and **report language**.
8. **Report readers**: who it is for, which sets how detailed it is.
9. **Delivery:** saved to the project folder always. Optionally emailed to the user's preferred address, and to any extra recipients the user names (see 7.11).

**Query builder (Radar-inspired, rewritten):** the plan is built from *blocks*: subject (organisation plus name variants), contexts, competitors and exclusions. Each block is an OR of terms, and queries are combinations of blocks. Claude proposes terms in every language being followed, the user edits them, and each collector receives the queries in its own syntax.

**Test before save:** the setup runs every collector on the last 7 days, then reports per term and per query: hits, a few sample headlines, and warnings such as "finds nothing", "too generic (94% of all results)" or "mostly your own website". The user adjusts until it looks right.

### 7.2 Usage estimate

It is computed from the test collection, not guessed:
- Items per run (from the test, scaled to the run period) × the tokens per item for the relevance check, plus the tokens for writing the report, plus a fixed overhead.
- It is shown in plain terms: "about N tokens per weekly run, about M per month". It also explains what drives the cost and how to reduce it (fewer sources, a stricter relevance filter).
- **Hard cap:** each run gets a token and item budget set from the estimate (for example 2× the estimate). If the budget runs out, the run stops relevance-checking, writes a report from what it has, and says so in the run note.
- After each run, the actual usage is logged next to the estimate, so later estimates improve.

### 7.3 Collectors (no tokens)

All collectors output the same item format: `source, outlet, url, title, published_at, snippet, full_text (optional), language, matched_queries, collected_at`.

| Collector | How | Notes |
|---|---|---|
| **Google News** | Search feeds by query, with the right edition per country (e.g. `hl=it&gl=IT&ceid=IT:it`) and `when:Nd` | Free, broad. Shows ~100 results per query at most, so it uses many narrow queries rather than one broad one. Proven in the test. |
| **GDELT** | Article-search API with `sourcecountry` / `sourcelang` filters and date window | Free, 100k+ outlets. Strict rate limit (one request every ~5 seconds). The test got a "too many requests" error; the collector must pace itself and retry. |
| **Outlet feeds** | The RSS feeds of every outlet on the plan's list, filtered by the queries | Guarantees "checked these N outlets". The bundled Italian starter list is built during development. |
| **Full text** | Agent Reach `read` on the selected items only (after the date filter) | Needed for accurate relevance and quotes. Capped per run. If the page is **subscriber-only**, the item is kept: relevance is judged from the headline and snippet, and the item is marked "subscribers only". No attempt is made to get past the paywall. |
| **X / Twitter** | Agent Reach `twitter search` (the user's login, through Agent Reach's dedicated browser) | Only if chosen. Experimental; failures go to the run note, never hidden. |
| **LinkedIn** | Keyword **post search** with the user's login | Only if chosen. See 7.5. |
| **Instagram, TikTok, Facebook** | Agent Reach's channels for each (search, profiles, posts) | Only if chosen. Experimental; failures go to the run note. |

Google News and GDELT also surface subscriber-only articles from outlets such as Corriere and Il Sole 24 Ore. Those headlines are where most paywalled mentions will come from.

Dedupe by normalised URL and near-identical titles (press-release reprints are grouped as "same story, N outlets", not dropped). Date filter strictly to the run window.

### 7.4 Agent Reach: private copy

- Installed into the project's own Python environment at a **pinned version** (1.6.1 today). It never uses the user's `agent-reach` command.
- Every call runs with `HOME` pointed at a private folder (for example `<data dir>/reach-home`). Agent Reach uses `HOME` as its isolation boundary, so it gets its own config, channels, logins and browser. Verified in its code (`utils/paths.py: home_dir()`).
- A **separate browser port** (`REACH_BROWSER_PORT`), so the two dedicated browsers never clash.
- **OpenCLI** (needed for X and LinkedIn) can run only once per computer, on a fixed local address (`127.0.0.1:19825`). Our tool **must never install, update or reinstall OpenCLI if it is already present.** It reuses the existing one and checks that the version is compatible. If it is incompatible, it tells the user and does not change it. Logins stay separate either way, because they live in each copy's own browser.
- Agent Reach's own skill is **not** linked into the user's Claude by our install. Our setup must make sure Agent Reach's `setup` step does not register its skill in the user's real Claude folders. It sees the private `HOME`, so it would write to the private folder.
- Updating the pinned version is a deliberate maintainer action, tested before release.

### 7.5 LinkedIn post search

Agent Reach only connects LinkedIn *jobs*. Two candidates for keyword post search:
1. **`stickerdaniel/linkedin-mcp-server`** (Apache-2.0, ~4.2k stars). It has `search_posts` (keyword plus recency) and `get_company_posts`, and uses its own browser session.
2. **A custom OpenCLI command for LinkedIn content search**, run through our Agent Reach copy, so it shares the same browser and login system.

**Builder's first task is a short spike** to try option 1 as a library or command-line call from a collector script (not via Claude, so no tokens), and pick whichever works reliably. Requirements either way: read only, the user's own login, low volume, and a "couldn't reach LinkedIn" status in the report on failure. Also collect the posts of the organisation's own company page if the user gives it.

### 7.6 Analysis (Claude)

- **Relevance and tagging** with a cheaper model (Haiku or Sonnet, run as a subagent). For each item: is it really about the subject (not just a general mention), type (news, press-release reprint, interview, opinion, event listing, social post), tone (positive, neutral or negative, with a one-line reason), which programme or event it relates to, and whether the organisation is named explicitly or only its parent body. In the test, 6 of 9 items said "Aurora" without naming the Centre, and that is worth reporting.
- **Report writing** with a stronger model (Sonnet by default), using only the checked items.
- **Content is data, never instructions.** Item text is wrapped and labelled as untrusted, and the output is checked against a fixed set of labels.

### 7.7 Report

Format: **Markdown plus a self-contained HTML file** in `reports/`, named by project and date. A Word export can come later.

**The report is written to be forwarded as it is.** It never says how an item was found (search engine, feed, agent) and contains no technical notes, errors or usage figures.

Proposed structure (default; the user can reorder it during setup):
1. **Header:** organisation, period, and a neutral scope line (for example "Italian online media, X, LinkedIn"). *(Claude's call: the scope line describes what is monitored, not how.)*
2. **Summary:** 3 to 5 sentences on what mattered this period.
3. **Key items:** the most important coverage, each with date, outlet, original headline **as a link**, a one-line summary in the report language, tone and type. Subscriber-only articles are marked "subscribers only".
4. **By theme or event:** stories grouped, with reprints shown as "same story, N outlets".
5. **Social:** notable posts from the chosen social platforms, with the same fields.
6. **Things to notice:** for example the organisation not named, inconsistent titles, a competitor in the news.
7. **Compared with last period:** volume and tone trend (from the second run on).

**Every item has a working link.** If an outlet's own link can't be resolved, the collector's link is used, which still opens the article in a browser.

**Run note (user only, never forwarded):** a separate short file (and, with email on, a separate message to the user only) covering sources checked ("checked N of M outlets"), anything that could not be reached and why (for example "X login expired, run *status* to fix"), budget stops, citation-check removals, and usage against the estimate.

**Citation check (script):** every link in the report must match an item in that run's database. Anything else is removed, and the removal is noted in the run note.

### 7.8 Scheduling

- At the end of setup, create a **Claude desktop scheduled task** that runs the `run` skill for the project at the chosen frequency.
- It runs locally, because that is where the logins are. Missed runs (computer off) run at the next start and cover the full missed period.
- `status` shows the last run, the next run, source health and login health.

### 7.10 Outlet discovery system (any country)

For whatever country and region the user picks, the setup builds an extensive list of that country's online media. It runs in four steps, using free sources.

1. **Gather candidates** from several places, because each one misses outlets the others have:
   - **Wikidata and Wikipedia:** newspapers, news websites, news agencies, broadcasters and magazines with that country and an official website, plus the national-language Wikipedia lists of newspapers.
   - **GDELT:** the domains most active for that country (`sourcecountry`) over recent weeks. This catches active online-only and regional outlets.
   - **Google News for that country's edition:** the domains that appear across broad national, regional and city queries (politics, economy, the main cities and regions).
   - **Sector outlets:** for the user's field (for example climate, energy, development), Claude suggests trade and specialist outlets. Each is checked in step 2 like any other candidate.
   - **The user's own additions.**
2. **Verify each candidate (script):** the site is reachable, the language matches, it publishes news, and it was updated recently. It also finds each outlet's RSS feed (from the homepage's feed tags, common feed paths, or the news sitemap) and notes whether articles are subscriber-only.
3. **Classify:** national, regional or local (with region), news agency, broadcaster, trade or specialist, or organisation or institutional site. It removes duplicates such as mirrors and syndicated copies of the same outlet.
4. **Review with the user:** counts by type and region, the full list to browse, and easy add or remove.

**How each outlet is checked during runs:** through its feed if it has one, otherwise a Google News query limited to that site, otherwise its news sitemap. This is what lets the run note say "checked N of M outlets".

The list is cached per country, dated, and refreshed every few months. **Lists can be shared:** the project can ship community-maintained country lists, and the discovery step only adds to them. The Italian list is the first one, built and checked during development.

### 7.11 Email delivery (optional)

- During setup, the user gives a preferred email address (and, optionally, colleagues to receive the report). A test email is sent then, so the user sees exactly what will arrive.
- **The report email** goes to everyone on the list: a short summary in the email body and the HTML report attached.
- **The run note email** goes **only to the user**.
- **How it sends** *(Claude's call)*: through the user's own email account over SMTP (for example Gmail or Outlook with an app password). The user enters the password in their own terminal, it is stored locally and never shown in chat. If the user has a Gmail connector in Claude, saving a Gmail draft for them to send is offered as an alternative.
- **Consent:** sending happens on a schedule without asking each time, so setup asks the user to confirm the recipient list explicitly. It can be changed with `edit` and turned off at any time.

### 7.12 Storage

Per project folder: `project.yaml` (the plan), `data.sqlite` (items, runs, usage), `reports/`, `run-notes/`, `logs/`, `reach-home/` (the private Agent Reach). Outlet lists live in a shared per-country cache, so several projects in the same country reuse them. Several projects can exist side by side. Secrets (if any) go in a local file excluded from git, never in chat.

## 8. Worked example (the first real project)

Aurora Climate Lab for Clean Energy. Italian online media, X and LinkedIn, weekly. Subject variants: "Aurora Climate Lab", "Laboratorio Clima Aurora", the full Italian name. Programmes and events: Green Schools, Solar Villages in Africa (SV4), VELA, Clean Air Week. Spokesperson: Giulia Marchetti. Partner: Comune di Lumio. Exclusions: aurora.example, job and internship listings. Report in English.


## 9. Public release

- MIT licence, with Agent Reach's notice kept and a credits section (Agent Reach, Radar as inspiration, the LinkedIn tool if used).
- README covering what it does, honest limits, platform-rules warning, install steps for Windows and macOS, the first setup walkthrough, and how cost works.
- No personal data in the repo. Example config uses the Aurora case or a fictional organisation.

## 10. Settled after review

These points were settled after a builder read the draft. Each is **Claude's call**, not something the author has confirmed.

1. **Who pays, and what the estimate means.** It runs inside Claude, so it uses the user's Claude plan. No API key is needed. The estimate is shown in tokens, with a plain-language share of a typical plan's weekly allowance. Because a run cannot reliably read its own token count, **the hard cap is enforced in the Python layer by item counts**: the most items sent for relevance checking, the most full-text reads, and the most items in the report, each set from the estimate. That gives an approximate cap, which the doc and the UI say honestly.
2. **What a scheduled run can do.** **The first spike of the build** checks whether a Claude desktop scheduled task can run the `run` skill unattended (no permission prompts), start subagents, and choose models per step. If it can't, the fallback is a headless `claude -p` run started by Windows Task Scheduler or macOS launchd, with pre-approved permissions for this project only. The cap (point 1) does not depend on either, and neither does catching up on missed runs: the run reads the last successful run date from the database and covers everything since then.
3. **Logins (only when a login platform is chosen).** A news-only project has no login step. For each social platform the user picks, the setup starts the private browser's login (`login <channel>` on our Agent Reach copy) and the user logs in themselves, ideally with a secondary account. Claude never sees or types credentials. If a session expires, the **run note** (not the report) says "X not reached: login expired. Run *status* to fix". `status` re-opens the login window. Scheduled runs never try to log in.
4. **OpenCLI.** "Compatible" means the version range Agent Reach 1.6.1 itself accepts (≥ 1.8.7, not 1.8.8). If OpenCLI is **absent**, the installer may install it the same way Agent Reach does (user-level, no admin rights), after telling the user. This doesn't affect their own Agent Reach. If it is **present but incompatible**, leave it alone, keep X and LinkedIn off, and explain why in `status` and the report.
5. **Python and install.** An install script (Windows PowerShell and macOS shell) uses `uv` to create the project's private environment with Python 3.12 (uv downloads it if needed) and installs the pinned Agent Reach plus our package. The `setup` skill checks for that environment first and offers to run the install if it is missing.
6. **What "checked" means for an outlet.** An outlet counts as checked when its feed (or a site-limited Google News query) was fetched successfully, even if nothing matched. A failed fetch counts as "not reached" and is listed. Outlets without a feed are covered by a Google News query limited to that site. For feeds that keep only their latest few entries, the run note says how many days the feed actually covered when that is shorter than the run period, and the Google News site query fills the gap. The run note (not the report) says "checked N of M outlets".
7. **Where projects live.** By default `Documents/Media Monitor/<project name>/`, or a folder the user picks in setup. A small registry at `~/.media-monitor/projects.yaml` lists the projects and their folders, so the scheduled task and `status` can find them. All paths must work with spaces and apostrophes (for example `Sam's Skills`).
8. **Citation check failures.** An item whose link isn't in the run's database is removed from the report along with its claim. The run note says how many were removed. The report isn't rewritten.
9. **Other countries.** Replaced by the outlet discovery system (7.10), which works for any country. The Italian list is the first one built and shipped.

## 11. Left to the builder

- Internal code structure, libraries, database schema details, test strategy.
- The exact token-per-item numbers for the estimate (calibrated from real runs).
- The exact thresholds in outlet discovery (how recent "active" means, how many GDELT domains to take), and the shared-list file format.
- The HTML report styling.
- The LinkedIn option (after the spike in 7.5).
- The exact model per step, within the rule "cheap model for tagging, stronger model for the report".
