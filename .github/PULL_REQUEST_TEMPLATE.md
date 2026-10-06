## What

<!-- one paragraph: what changes and why -->

## Checklist (CONTRIBUTING.md)

- [ ] `uv run pytest -q`, `uv run ruff check src tests` and, for the web app, `pnpm test` and `pnpm typecheck` pass
- [ ] `uv run coach dev hygiene --ci` finds nothing: no real name, merchant, amount, IBAN, key or home path in a test, a fixture or a doc
- [ ] every new network call, subprocess or browser call goes through `egress.allow(...)` and is registered in `coach/egress.py`
- [ ] a tool or command that changes memory, contracts, decisions or loans only PROPOSES (previews, typed yes): nothing accepted for the user
- [ ] alert messages stay minimal (no name, merchant, account, bank or IBAN); logs hold step names, statuses and numbers only
- [ ] docs updated (`docs/*.md`, `CHANGELOG.md` under Unreleased)
