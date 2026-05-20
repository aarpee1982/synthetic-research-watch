# GEO News Watch

Daily GitHub Actions monitor for generative engine optimization, AI search, answer engines, AI Overviews, and LLM visibility news.

It watches configured public sources, filters out pages that are not GEO-relevant, writes a Markdown report, and opens a GitHub issue only when new or materially changed GEO pages are found.

## What It Watches

- AI search and answer-engine platform updates
- Google AI Overviews / AI Mode changes
- ChatGPT Search, Perplexity, Bing/Copilot, and similar discovery systems
- GEO / AEO / LLM visibility platform updates
- Search-industry coverage that directly affects AI-search visibility

## What It Does Not Watch

- Generic AI startup news
- Broad market research company pages
- Product pages with no AI-search or GEO relevance
- Login, pricing, privacy, PDF, image, and CDN assets

## How It Works

1. Fetch each source's seed URLs.
2. Extract same-domain links.
3. Score likely news, blog, product, and platform URLs.
4. Keep only pages that match GEO keywords.
5. If `OPENAI_API_KEY` is set, use your OpenAI key for stricter relevance checks on borderline pages.
6. Snapshot title, description, headline, summary, and hashes.
7. Compare against the previous committed state.
8. Write `output/report.md` and `output/summary.json`.
9. Open a GitHub issue only when changes are detected.
10. In GitHub Actions, commit refreshed state to a separate `monitor-state` branch so `main` can stay protected.

## Repository Layout

- `config/sites.json`: monitored GEO sources, keywords, crawl limits
- `scripts/monitor.py`: crawler, GEO relevance filter, diff engine, report generator
- `state/site_state.json`: starter baseline for local runs and first GitHub Actions run
- `.github/workflows/daily-monitor.yml`: scheduled monitor
- `.github/workflows/ci.yml`: tests and syntax checks
- `tests/`: local unit tests

## Quick Start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
python scripts/monitor.py
```

Output:

- `output/report.md`
- `output/summary.json`

## Use Your Own OpenAI Key

The monitor works without OpenAI. It uses deterministic GEO keyword filtering.

For stricter AI-assisted relevance classification:

1. Copy `.env.example` to `.env`.
2. Add your own key locally:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.5
```

For GitHub Actions:

1. Open your fork or repo on GitHub.
2. Go to `Settings -> Secrets and variables -> Actions`.
3. Add repository secret `OPENAI_API_KEY`.
4. Optional: add repository variable `OPENAI_MODEL`.

Never commit `.env`, real API keys, tokens, passwords, cookies, or private customer data.

## GitHub Setup

1. Fork this repository or create a new repo from it.
2. Enable GitHub Actions.
3. Run `AI Market Watch` manually once from the Actions tab.
4. Check `Issues` for the generated daily report.

The workflow uses GitHub's built-in `GITHUB_TOKEN`. You do not need a personal access token for normal use.

Scheduled runs persist changing state on a separate `monitor-state` branch. Keep `main` protected and use pull requests for code changes.

## Customization

Edit `config/sites.json` to:

- add or remove GEO sources
- tune crawl limits
- add source-specific include/exclude URL hints
- tune `geo_keywords`
- change the OpenAI model

Keep sources focused on GEO or AI-search discovery. If a source mostly publishes generic AI news, do not add it unless the source has a GEO-specific seed page.

## Security

- Secrets are read only from environment variables or GitHub Actions secrets.
- `.env` is ignored by Git.
- The committed state stores summaries and hashes, not full page bodies.
- The monitor does not scrape login-only content.
- See `SECURITY.md` for reporting and handling security issues.

## License

MIT. See `LICENSE`.
