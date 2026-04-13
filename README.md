# AI Market Watch

Daily GitHub Actions monitor for AI-native market research and synthetic research companies.

The workflow checks a configured list of company sites once per day, detects newly discovered pages and meaningful page updates, writes a Markdown report, and opens a GitHub issue when there is something new to review.

## What it watches

- New article-like pages
- New product or service pages
- Meaningful changes to tracked pages

The starter configuration focuses on 20 companies in the AI market research and synthetic insights category.

## How it works

1. Fetch each site's configured seed pages.
2. Extract same-domain links from those pages.
3. Score likely article, news, product, feature, or launch URLs.
4. Snapshot normalized page content.
5. Compare the latest snapshot to the previous committed state.
6. Write `output/report.md`.
7. Open a daily issue only when changes are detected.
8. Commit refreshed state back to the repository.

## Repository layout

- `config/sites.json`: monitored companies and crawl hints
- `scripts/monitor.py`: crawler, extractor, diff engine, and report generator
- `state/site_state.json`: committed baseline used for future diffs
- `.github/workflows/daily-monitor.yml`: scheduled workflow

## Local run

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
python scripts/monitor.py
```

The report is written to `output/report.md`, and the machine-readable summary is written to `output/summary.json`.

## GitHub setup

1. Create an empty GitHub repository.
2. Push this folder into the repository.
3. Enable GitHub Actions.
4. Optionally add a `GH_PAT` secret if you want issue creation and bot commits to use a personal token. The built-in `GITHUB_TOKEN` also works.

## Default behavior

- Schedule: once per day
- Issue creation: only when changes were detected
- State persistence: committed back into `state/site_state.json`

## Customization

Edit `config/sites.json` to:

- add or remove companies
- change seed URLs
- tune per-site crawl depth
- add include or exclude path keywords

The initial site list is a practical starting point rather than a perfect canonical index. You should expect to tune it after a few days of runs.
