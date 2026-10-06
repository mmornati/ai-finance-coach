# Prototype (retired)

The prototype scripts (`eb.py`, `classify.py`) were replaced by the `coach` package and CLI
(see the root `README.md`): `uv run coach --help`.

The legacy prototype data (`ingest/data/finance.db` and `ingest/.env`) was deleted on purpose, at the
user's request; nothing here is needed any more.

- `uv run coach db import-prototype` has no default source any more: it stops with an explanation when
  `prototype/ingest/data/finance.db` is missing. It still works with `--source PATH` for a prototype-shaped DB
  (the original is never modified).
- Enable Banking settings now live in `config.toml` (`[enable_banking]`); the legacy `ingest/.env` fallback is
  only read if such a file exists. A new database is created with `uv run coach db migrate --create`.
