# Contributing

## Local Setup

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

## Pull Request Rules

- Keep changes focused.
- Add or update tests for monitor behavior.
- Do not commit secrets, private data, cookies, tokens, or generated output.
- Keep new sources GEO-relevant.
- Prefer source-specific seed pages over broad homepages.

## Source Rules

A source belongs here only if it regularly covers:

- generative engine optimization
- AI search
- answer engines
- Google AI Overviews or AI Mode
- ChatGPT Search, Perplexity, Bing/Copilot search
- LLM visibility or brand citation tracking

Do not add generic AI news sources unless the seed URL is specifically about AI search or GEO.
