# Release checklist

`uv run coach dev release-check` runs the automatable part of this list and exits 1 until everything holds. The same command runs in CI (`.github/workflows/ci.yml`) with `--skip-tests --skip-web`: the suites run in the CI jobs it depends on, not a second time.

## Owner decisions (taken on 2026-10-06)

| Decision | Where |
|---|---|
| The licence: **MIT** (the comparison is kept in [LICENSE.choose.md](../LICENSE.choose.md)) | `LICENSE`, `license` in `pyproject.toml`, the README |
| The reporting address: GitHub's private vulnerability reporting of the repository | `SECURITY.md`, `CODE_OF_CONDUCT.md` |
| The public repository: `https://github.com/mmornati/ai-finance-coach` | `pyproject.toml` `[project.urls]`, the README clone line |
| The git history starts at the public release (the earlier, unversioned life of the project is not published) | `git init` on 2026-10-06 |

## What release-check verifies

| Check | What passes |
|---|---|
| LICENSE file | a `LICENSE` (or `COPYING`) file of real content exists at the root |
| Owner placeholders | no `TODO(license)`, `TODO(contact)` or `TODO(repo-url)` in any publishable file |
| Version | `pyproject.toml` version = `coach.__version__` = the top `## [x.y.z] - YYYY-MM-DD` section of `CHANGELOG.md` (dated) |
| Web app build | `src/coach/api/static/index.html` exists, references only files that exist, and is not older than `web/src` |
| Personal data scan | the structural rules (keys, home folders, valid IBANs, e-mails) and, with the local term file, the real-data rule find nothing in any publishable file or in the sdist member list (and in `dist/` archives when present) |
| Real-data terms | the LOCAL term file exists, is 0600 and non-empty: **a missing file fails** (`--ci` skips this rule, says so, and the run is not release-grade) |
| Package metadata | name, version, description, readme, `requires-python`, classifiers, keywords, the `coach` script |
| Wheel contents | `artifacts` for the built web app, `force-include` for the configuration example and import profiles |
| Shipped templates | the memory skeleton under `src/coach/templates/memory/` |
| Project URLs | `[project.urls]` has a Homepage or Repository |
| Documentation | README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, `docs/architecture.md`, `docs/docker.md`, `docs/release.md`, `docs/claude-settings.example.json` (valid JSON) |
| .gitignore | the personal-data paths are ignored |
| Docker files (static) | `Dockerfile`, `docker-compose.yml`, `.dockerignore` pass `coach/dockerlint.py` (not built) |
| CI workflow | `.github/workflows/ci.yml` runs pytest, the web tests, ruff and release-check |
| pytest, ruff, vitest, type check | they pass (`--skip-tests`, `--skip-web` skip them; the run is then reported as NOT release-grade) |

## What publishable means

The files that `.gitignore` does not exclude. The scan applies git's rules itself (the project may not be a repository yet), so a path ignored there is a path nobody
reviews: keep `.gitignore` and this list consistent. Ignored on purpose: `memory/*` except the README and the two templates, `data/`, `backups/`, `config.toml`,
`config/taxonomy.yaml` and `config/rules.yaml` (your copies), `.claude/settings.json` (absolute paths; the template is `docs/claude-settings.example.json`), `*.pem`,
`*.key`, `.env*`, `*.db`, `*.enc`, `secrets/`, `coach-home/`, `web/node_modules/`, and the built web app `src/coach/api/static/` (built by CI, the Dockerfile and step 4 below).

## Procedure

1. Decide the open items above. Save `LICENSE`; remove the placeholders.
2. Bump the version in `pyproject.toml` **and** `src/coach/__init__.py`; add the `## [x.y.z] - date` section to `CHANGELOG.md`; refresh the lock (`uv lock`).
3. Pin the Docker base images: replace each tag in the `Dockerfile` by `tag@sha256:<digest>` (see `docs/docker.md`) and build the image once.
4. `cd web && pnpm install --frozen-lockfile && pnpm build`.
5. On the machine that holds your data: `uv run coach dev hygiene --build-terms` (writes `hygiene-terms.txt`, 0600, git-ignored, never printed), then `uv run coach dev release-check` (everything, no skips, no `--ci`) until it prints READY. The terms come from your database and memory, so rebuild them whenever the household changes.
6. Read the diff of `docs/BACKLOG.md` and `README.md` once more for anything personal that a scan cannot know (a family situation, an employer, a town). The scan
   only knows the terms of the local file; it cannot know a situation that is in no term (the number of children, an employer you never declared).
7. Build: `uv build` (the wheel must contain the web app: `unzip -l dist/*.whl | grep api/static`, and `coach/templates/`). Install it in a clean environment
   (`uv tool install ./dist/*.whl`; `coach init`, `coach doctor`) before uploading.
8. Tag, publish (`uv publish`, with a token that only you hold), and publish the image if you want one.

## After a release

Add an empty `## [Unreleased]` section at the top of the changelog while you work: `release-check` skips it and judges the first dated section. When you cut the next release, rename it to the new version and date it.
