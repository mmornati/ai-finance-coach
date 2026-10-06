# Contributing

Thank you for helping. This project handles people's bank data, so the rules below are about **privacy first**: a contribution that leaks
real data, even in a test, is rejected however good the code is.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go to [SECURITY.md](SECURITY.md), not to a public issue.

## Development setup

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python, 3.11 to 3.13) and, for the web app, Node 22 with
[pnpm](https://pnpm.io/).

```bash
uv sync                                  # Python dependencies; SQLCipher comes as a wheel, no system library
(cd web && pnpm install && pnpm build)   # the web app; the build goes to src/coach/api/static/ and is NOT committed
uv run coach --help
```

Never point a development run at your real data. Use a scratch home, which also exercises the install path:

```bash
export COACH_HOME=$(mktemp -d)
COACH_SECRETS_BACKEND=file COACH_SECRETS_DIR=$COACH_HOME/secrets uv run coach init --non-interactive
```

(`COACH_*` variables select the home, data, memory and database; the file secrets backend keeps a throwaway key out of your Keychain.)

## Tests and checks

```bash
uv run pytest                            # the Python suite: synthetic data only, no network, no real Keychain
uv run ruff check src tests              # lint: correctness rules (pyproject [tool.ruff.lint])
cd web && pnpm test && pnpm typecheck    # vitest and the TypeScript check
uv run coach dev hygiene --ci            # structural rules over every publishable file (the real-data rule needs your local term file: see below)
uv run coach dev release-check --skip-tests --skip-web   # the quick part of the release gate
```

The same checks run in CI (`.github/workflows/ci.yml`). A pull request needs them green.

### Rules for tests

* **Synthetic data only.** Invent every name, merchant, amount, IBAN and address. IBAN-like strings must fail the checksum (or be one of the published
  examples). Never paste a line from your own statements, not even "anonymised by hand": use `coach eval fixtures synth` (below).
* **No network, no real Keychain, no real database.** The conftest installs a fake keyring and clears `COACH_*` / `EB_*` variables. HTTP is mocked
  (inject a fake client), LLMs are mocked, alert channels use injected `Transports`. Tests use `tmp_path` homes and the `file` secrets backend where a
  key is needed.
* **Name the configuration file in tests `cfg.toml` or use `load_config(path, env={})`**, never the real `config.toml`.
* The hygiene test (`tests/test_hygiene.py`) fails when any publishable file holds a key, a home-folder path, a valid IBAN or a real-looking e-mail address. The real-data rule
  uses a LOCAL file of terms that identify YOUR household, built from your own database and memory by `coach dev hygiene --build-terms` (default `<home>/hygiene-terms.txt`, 0600,
  git-ignored, never printed); run `coach dev hygiene` before you push. Do not put a real name, place, merchant or amount in a test, not even to "exclude" it. Build trigger strings in tests at run time (see that file) rather than writing them.

## Coding conventions

* Python 3.11+, type hints where they help, short docstrings that say **why**. Match the surrounding style; there is no formatter to run.
* **Numbers come from code, words come from the model.** Anything the coach quotes is computed by a deterministic function and returned by a tool.
* **Every new network call, subprocess or browser call goes through `egress.allow(...)` and is registered in `coach/egress.py`** (`CALL_SITES`, `INVENTORY`).
  `tests/test_egress_coverage.py` fails otherwise. A new model-facing path must redact (`coach/privacy.py`, `analytics/privacy.py`) and is covered by a
  privacy test with sentinel words.
* **The coach only proposes.** Nothing an agent or model runs may write memory, accept a proposal, pass `--yes`, enable a channel or change `[privacy]`.
  Commands that change the user's records preview first and ask for a typed confirmation in a terminal (see `export.py`, `wipe.py`).
* Logs hold step names, statuses and numbers, never a description, merchant, name, amount or exception text.
* Database changes are numbered migrations in `src/coach/migrations/` (`0022_name.sql`, or `.py` with `run(con)`), never edits of old ones.
* User-visible words: English, plain; French / Italian only where a rule table or a disclaimer needs it (`coach/disclaimers.py`).
* Disclaimers and the "AI-generated" label live in one place (`coach/disclaimers.py`); do not reword them in a skill.

## How to add a bank parser

A parser turns one bank's free-text descriptions into a type, a merchant and references (`src/coach/classify/parsers/__init__.py` explains the contract).

1. Write `src/coach/classify/parsers/<bank>.py` with `parse(tx: RawTx, household: Household) -> dict` returning the keys of `common.PARSED_KEYS`
   (use the helpers of `common.py`; start from `revolut.py` for a bank with transaction codes, `fortuneo.py` for free text). A line you do not recognise
   returns type `other`, so the generic parser can try it.
2. Register it in `parsers/__init__.py`: `register("<bank>", <bank>.parse, r"\bpattern of the bank name\b")`.
3. **Fixtures without real data.** On a *scratch restore* of your own database (`coach backup`, `coach restore FILE --to DIR`, then point `COACH_DB`,
   `COACH_MEMORY_DIR`, `COACH_DATA_DIR`, `COACH_CONFIG_DIR` at it), run
   `coach eval fixtures synth --bank <bank> --out tests/fixtures/<bank>.json`. It learns the *shapes* (prefixes, lengths, padding, where dates and
   references stand) and writes invented descriptors; it refuses to write if anything real would leak. Details: [docs/quality.md](docs/quality.md).
   If you have no bank data, write the fixture by hand from the bank's public documentation, with invented values.
4. Add the tests: parser coverage over the fixture (`tests/test_quality_fixtures.py` style: every line recognised and typed), a few hand-written edge
   lines in `tests/test_parsers.py`, and the through-the-pipeline checks (ingest, normalize, nothing left as `other`).
5. Check the person guard: a line naming a person must never reach a model (`classify/candidates.py`); add a case if your bank has a new form.
6. Run `uv run coach dev hygiene` (with your local term file) or `--ci`. Mention the bank in the README table of supported banks only if you tested a real statement yourself.

For banks Enable Banking cannot link, add a CSV profile instead (`config/import_profiles/<name>.toml`, see the shipped examples and `coach import --help`).

## How to add a skill

A skill is a deterministic helper, a read-only finance tool, a prompt, and an interactive Claude Code skill (docs/skills.md lists the rules):

1. The computation in `src/coach/skills/` (pure, deterministic, tested on synthetic households).
2. The tool in `src/coach/skills/tools.py` through the same choke point as the others (free text wrapped as untrusted text, ids pseudonymised, the final
   privacy assertion). Read-only, except proposals.
3. A `PromptSpec` in `agent/prompt.py` (`SKILLS`) so the web app and `coach coach ask --skill ID` can run it.
4. `.claude/skills/<id>/SKILL.md` with the front matter (`name`, `description`) and the safety rules every skill repeats (never `memory accept`, never `--yes`,
   never read `memory/` or `data/`, no investment advice). `tests/test_skills_files.py` checks them.
5. A section in `docs/skills.md` and a row in the index of `CLAUDE.md`. Web search only in interactive skills with generic queries (see the rules there).

## Review checklist (for authors and reviewers)

Privacy and safety
- [ ] No real name, merchant, amount, IBAN, address, key or token anywhere (code, tests, docs, fixtures, screenshots). `coach dev hygiene` is clean (with your local term file when you have data).
- [ ] New outbound path: gated by `egress.allow`, registered, redacted, honours `local_only` / `offline`, shown in `coach privacy status`, tested.
- [ ] New model-facing text: pseudonymised / generalised, third-party text wrapped as untrusted, tested with a hostile merchant name.
- [ ] Nothing writes the user's memory or configuration without a preview and a typed confirmation; nothing an agent can run bypasses that.
- [ ] Logs and error messages contain no personal text.
- [ ] No new secret in a file, environment default, image layer or log; secrets via `coach.secrets` only.

Quality
- [ ] Numbers come from tested code; rounding and currency handled; coverage-aware where an average is involved.
- [ ] Tests are synthetic, fast, deterministic, and fail without the change; no test touches the network, the Keychain or `data/`.
- [ ] Docs and `CHANGELOG.md` updated; the CLI help (`EPILOG` in `cli.py`) lists a new command; a UI change is rebuilt (`pnpm build`) and tested.
- [ ] Migrations are new numbered files and keep existing data.

## Pull requests

Small, focused changes with a clear description of what and why. Say what you ran (pytest, vitest, ruff, hygiene). By contributing you agree your work is
licensed under the project's licence, MIT (see `LICENSE`).
