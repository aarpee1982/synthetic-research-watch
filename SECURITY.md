# Security Policy

## Supported Version

Only the `main` branch is supported.

## Reporting a Vulnerability

Do not open a public issue for secrets, credentials, auth bypasses, or private data exposure.

Email the maintainer or use GitHub private vulnerability reporting if it is enabled for this repository.

Include:

- affected file or workflow
- reproduction steps
- expected impact
- whether any secret or private data was exposed

## Secret Handling

- Never commit `.env`, API keys, cookies, passwords, or tokens.
- Use GitHub Actions secrets for `OPENAI_API_KEY`.
- Use GitHub's built-in `GITHUB_TOKEN` for issue creation and state commits.
- Rotate any key immediately if it is accidentally committed.
