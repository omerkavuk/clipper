# Brave as a second news route: test plan

The question: does Brave News Search find relevant publisher articles that Google News misses, in Italy and two other markets? And what does a report look like when Google is blocked?

## Setup

- The Brave key is the user's own (api-dashboard.search.brave.com). Save it in `~/.clipper/brave-key.txt`, or set `BRAVE_API_KEY`. Never paste it into a chat.
- When a key is present, Brave is used automatically in `collect` and `test-queries`. Without a key, nothing changes.
- Brave serves a fixed list of countries. For a country it doesn't serve, Clipper searches worldwide in the project's languages, falling back once per run.

## Markets and periods

| Market | Project | Languages | Period |
|---|---|---|---|
| Italy | Aurora Climate Lab (`examples/aurora-climate-lab`) | it, en | 14 Oct – 14 Nov 2026 (the baseline month) |
| Kenya | Aurora Kenya | en, sw | the last 30 days |
| Germany | Aurora in Germany | de, en | the last 30 days |

## Steps for each market

1. Compare the routes on the same searches and period. Each route runs on its own:

       clipper compare --project <p> --since <ISO> --until <ISO> --out <dir>

2. Tag `<dir>/articles.json` with the normal tagging schema (one Haiku subagent; every article gets one entry), and write the result to `<dir>/tags.json`.
3. Score the relevant articles found only by Brave, only by Google, or by both. Each count is split into outlets on the source list, other outlets in the country, and outlets outside it or of unknown location:

       clipper compare-score --project <p> --articles <dir>/articles.json --tags <dir>/tags.json

4. Run once with Google switched off, then do the normal candidates → tag → draft → render steps. This shows what a report looks like during a block:

       clipper collect --project <p> --skip google_news --since <ISO> --until <ISO>

5. Be careful with Google: at most one Google comparison per market, with at least 30 minutes between markets.

## Decision

- **Brave becomes the fallback:** if it finds most of Google's relevant publisher articles in every market, it is used when Google is blocked.
- **Brave becomes a second source:** if it adds relevant articles that Google misses, it runs every time.
- **Brave is dropped and NewsData.io is tested next:** if it is weak in the target markets. NewsData.io's free plan only covers the last 48 hours, so it needs daily polling or a paid plan.
