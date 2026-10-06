# Architecture

How the pieces fit, where data flows, and where the trust boundaries are. Command-level detail is in [reference.md](reference.md); the threat model in
[security.md](security.md); the data flows in [privacy.md](privacy.md).

## Principles

1. **No model in the ingestion path.** Sync, deduplication, normalization, analytics and forecasts are deterministic code. A model labels merchants the
   rules and memory leave open, and answers on request.
2. **Numbers come from code, words come from the model.** The model calls read-only tools that return computed, redacted figures.
3. **Memory is files.** Household facts live as Markdown and YAML you can read and edit; every write is validated and recorded.
4. **Privacy by default.** Data stays local and encrypted; identities and account numbers never reach a model; a fully local mode exists.
5. **The model proposes, you decide.** Memory changes and anything destructive need a human in a terminal.

## Data flow

```
 Enable Banking (PSD2 REST, JWT RS256) ───────┐        CSV / OFX / QFX / CAMT.053 files ──────┐
                                              ▼                                               ▼
                         ingest/  client · auth · callback (local HTTPS) · consent · sync · accounts · health · imports
                                              │  dedup on entry_reference / fingerprint; pending replaced each sync; balances snapshotted
                                              ▼
                         ┌────────────── encrypted SQLite (SQLCipher, key = secret db_key) ──────────────┐
                         │ sessions · accounts · transactions · balances · tx_enriched · merchants · …  │
                         └───────────────────────────────────────────────────────────────────────────────┘
                              ▲ normalize/classify          │ read                        ▲ memory-driven
 classify/  parsers (per bank) → memory annotations → rules → kNN → LLM label (redacted, long tail only)
                                                           analytics/  coverage · averages · recurring · anomalies · forecast · budgets · goals
 memory/  (Markdown + YAML, local)                         subs/ · loans/ · alerts/  optimizer, loans & net worth, signals → events
 household · assets · liabilities · contracts · notes              │
        ▲                                                          ▼
        │ proposals (sealed)                    mcp/  finance MCP server: read tools + proposal tools, redaction, injection guard, privacy assertion
        │                                                          │ stdio
        └─────────────  you accept (terminal)  ◄────  agent/  coach runtime: claude -p │ Anthropic API │ Ollama · prompts · digests · insights
                                                                   │
                                  api/ (FastAPI, 127.0.0.1) + web/ (React SPA) · cli.py · schedule (launchd | loop) · notify / alerts channels
```

## Packages (`src/coach/`)

| Package / module | Role |
|---|---|
| `cli.py`, `__main__.py` | The single `coach` command; every subcommand is registered here or by its package's `register()` |
| `config.py`, `home.py`, `secrets.py` | Configuration (`config.toml`, env overrides), where a home lives and the shipped templates, the secret store (Keychain or `file` backend) |
| `db.py`, `migrations/` | SQLCipher connection, numbered migrations (`.sql` or `.py` with `run(con)`), encryption, snapshots |
| `setup/` | `coach init`, `coach doctor`, `coach setup` (wizard), `coach setup enablebanking` (E13) |
| `ingest/` | Enable Banking client, connect / finish flow, local HTTPS callback, consents, sync, accounts, health, file imports |
| `classify/` | Bank parsers, taxonomy and rules (`taxonomy.yaml`, `rules.yaml`), memory annotations, LLM backends and redaction, kNN, canonical merchants, splits |
| `memory/` | The memory store: schemas, validated and recorded writes, change history (local git), proposals, questions, documents, check, explain, context |
| `analytics/` | Deterministic reports: coverage, averages, recurring, anomalies, forecast, budgets, goals, calendar, year review |
| `household/` | People (E14): members and owners, who a transaction belongs to (attribution), the children's money, kid budgets, shared-cost allocation, per-person logins and the audit: [household.md](household.md) |
| `rental/` | Rental property under a tax-incentive scheme (E15): the property and its account, monthly cash flow and yearly P&L, vacancy, the scheme commitment and its reminders, the tax-year figures, the renegotiate-or-sell indicators: [rental.md](rental.md) |
| `subs/`, `loans/`, `alerts/`, `skills/` | Subscriptions and contracts, loans and net worth, alerts and the weekly summary, the coach skills' helpers and tools |
| `mcp/` | The finance MCP server: tool registry, redaction, injection guard, privacy assertion |
| `agent/` | The coach runtime and its backends, prompts, jobs, insights, digests |
| `api/` | The local web API (FastAPI): security (one-time login, CSRF, Host check), routes, jobs; serves the built SPA from `api/static/` |
| `egress.py`, `privacy.py`, `security.py`, `compliance.py`, `disclaimers.py`, `export.py`, `wipe.py` | The egress gate and journal, privacy modes, the security audit, AI labelling and disclaimers, export and delete |
| `quality/` | Fixture synthesis, gold set, evaluations, usage, logs |
| `hygiene.py`, `dockerlint.py`, `release.py` | Maintainer tooling (E13-4): the personal-data scan (structural rules + a local term file), the static Docker policy, `coach dev release-check` |
| `templates/` | What `coach init` copies: the memory skeleton (the configuration example and CSV profiles come from the repository root and are force-included in the wheel) |

`web/` holds the React + TypeScript + Vite + Tailwind source; `pnpm build` writes `src/coach/api/static/`, which is git-ignored and packaged in the wheel.

## Trust boundaries

1. **This machine's files** (database, memory, backups, keys): private to the user (0700 / 0600), encrypted where it matters.
2. **Enable Banking and your bank**: the only network use that carries no household data out (a signed request in, your transactions back).
3. **A model backend** (Anthropic through `claude -p` or the API, or local Ollama): receives only redacted, minimal payloads, each gated by `egress.allow`,
   refused under `local_only` / `offline`, and journaled (host, size, purpose; never a payload).
4. **A coding agent running as you**: untrusted. It reaches data only through the MCP tools, can only propose memory changes, and is fenced by permission rules
   (template: `docs/claude-settings.example.json`) and terminal-only gates (typed phrases, no `--yes`).
5. **Third-party text** (merchant names, document text, web pages): data, never instructions; wrapped as untrusted and scanned for injection.
6. **The browser**: one-time login link in the URL fragment, HttpOnly SameSite=Strict cookie, CSRF token on writes, Host check, loopback bind. Per-person logins (E14-8): the role is read from the database on every request, a child login reaches only an explicit allow-list of `/me/*` endpoints (deny by default).
7. **A container**: the same code under a non-root user, a read-only root filesystem, dropped capabilities, secrets as files, a port on the host loopback only.

## Where things live

| What | Default location |
|---|---|
| Home (configuration root) | the checkout; or `$COACH_HOME`; or `~/.ai-finance-coach` for an installed package; `/config` in Docker |
| `config.toml` | `<home>/config.toml` (or `--config` / `$COACH_CONFIG`) |
| Database, logs, TLS certificate, web-app state, setup state | `<data_dir>` (default `<home>/data`, mode 0700) |
| Memory | `<memory_dir>` (default `<home>/memory`) |
| Your taxonomy / rules copies, CSV profiles | `<home>/config/` (`$COACH_CONFIG_DIR`) |
| Secrets | macOS Keychain service `ai-finance-coach`, or `$COACH_SECRETS_DIR` files with `COACH_SECRETS_BACKEND=file` |
| Backups | `[backup] dir` (encrypted archives, `backup_key`) |

## Packaging

The wheel (hatchling) contains the package, the built web app (`artifacts`), migrations and YAML data files (inside the package), the memory templates, and, through
`force-include`, `config.example.toml` and `config/import_profiles` (one source of truth at the repository root). `coach dev release-check` verifies the build is
present and not older than `web/src`. The Docker image builds the web app with Node, builds the wheel in a second stage with locked dependencies (`uv export --frozen`),
and copies only the virtualenv into a slim runtime stage.
