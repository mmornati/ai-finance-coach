# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/)
(below 1.0, minor versions may change behaviour). The top section must carry the version in `pyproject.toml` and a date: `coach dev release-check` enforces it.

## [Unreleased]

### Added: multi-language web app, part 4h (see `docs/i18n.md`, "Server text")
- Household, Kids' money and Who pays notes, the Set up checklist and first-run wizard, the Connections health problems and sync results, the calendar titles and the
  generated memory questions are shown in the interface language. Generated questions store optional `topic_code` / `question_msg` / `context_msg` in
  `open-questions.yaml` (older files load unchanged; questions proposed by the coach stay plain text). The `.ics` export, the MCP tools and the CLI keep the English.

### Added: multi-language web app, part 4i (see `docs/i18n.md`, "Server text")
- The memory check (Memory > Check), the warnings of a memory write preview and the invalid budget entries are shown in the interface language (`memoryCheck.*`, `memoryLoad.*`);
  `coach ...` commands, file names and ids stay as they are, Pydantic's own text is passed through. `coach memory check` and the MCP tools are unchanged.

### Added: multi-language web app, part 4g (see `docs/i18n.md`, "Server text")
- The rental property page shows the missing facts, the scheme warnings, the tax-year candidates (items, bounds, sources, unknowns, documents), the reduction notes,
  the loan-rate, market and equity readings, the signals and the scenarios in the interface language. Scheme names stay as proper nouns; the general-advice, tax and
  loan disclaimers come from `disclaimers.py` in the same language. New param type `*_num` (a plain decimal in the reader's number format). MCP and CLI unchanged.

### Added: multi-language web app, part 4f (see `docs/i18n.md`, "Server text")
- Loans and net worth: the unknown values and their reasons, the missing loan fields, the schedule hints and assumptions, the scenarios (early repayment, renegotiation,
  insurance: notes, penalty explanations, options, verdicts), the fields inferred from the payments and the lease checks are shown in the interface language.
  Legal citations stay as written; the loan disclaimer comes from `disclaimers.py`. The MCP tools and the CLI keep the English.

### Added: multi-language web app, part 4d (see `docs/i18n.md`, "Server text")
- The transaction panel shows why a transaction has its category (the steps of the decision chain and their details), why an annotation or an attribution rule does not match,
  and the warnings of a category change in the interface language. `coach explain` and the MCP `explain_transaction` output are byte-identical to before.

### Added: multi-language web app, part 4c (see `docs/i18n.md`, "Server text")
- Insight cards (anomalies, price changes, forecast, budgets, subscription reminders, loan and rental alerts) and alert events (bank consent, failing sync, kid budgets, AI usage)
  carry `title_msg` / `body_msg` and are shown in the interface language on Insights, the Dashboard, Alerts and the loan pages. New rows store the codes in the existing
  `anomalies.payload` and `alert_events.payload` (no migration); older rows keep their English. Messages sent outside the machine (ntfy, e-mail, Telegram) and the CLI are unchanged.
- New short disclaimer `tax_short` in `disclaimers.py` (the English is the text the rental scheme card already showed).

### Added: multi-language web app, part 4e (see `docs/i18n.md`, "Server text")
- The subscription inventory shows the cancellation rules (name, summary, method, conditions, missing facts), decision checks, usage, offer notes and contract-draft warnings
  in the interface language; the legal citations stay as written, and the contract disclaimers come from `disclaimers.py` in the same language (`GET /meta/disclaimers`).
  The MCP tools, the CLI, letters and the calendar keep the English (`cancellability(..., messages=False)` by default).

### Added: multi-language web app, part 4b (see `docs/i18n.md`, "Server text")
- The coverage notes of the analytics results (incomplete months, non-EUR transactions left out, low confidence of a category, partial year, ...) carry `notes_msg` next to `notes`
  and are shown in the interface language on the Dashboard, Categories, a category's page and Subscriptions. The MCP finance tools and the CLI `--json` output never contain a `*_msg` key.

### Added: multi-language web app, part 4a (see `docs/i18n.md`, "Server text")
- Server text the web translates: next to an English sentence the API can send `<field>_msg` = `{code, params, text}` (built by `coach.i18n_msg.server_msg`, raw params typed by their name:
  `*_date`, `*_month`, `*_amount`, `*_pct`, `*_category`, `*_group`, `count`); the web renders it with `tServer()` (new namespace `server`) and falls back to the English text. The CLI, the MCP finance tools and the stored rows keep the English.
  First use: the balances note of the Dashboard.
- Fixed vocabularies sent as codes are named in the interface language: alert kinds, subscription groups, balance types, the first-run wizard steps, the onboarding steps, account purposes, the household line of the forecast,
  and the kinds of assets, loans and contracts in Memory and Wealth.
- Category and group names are translated by id (new namespace `taxonomy`, every group and leaf of the built-in taxonomy); a custom leaf keeps its title-cased id. A few English names changed too ("Electricity and gas", "Online marketplaces", ...).
- API errors are shown in the interface language for the generic codes (session, CSRF, rate limit, not found, validation, migration pending...); a domain rule keeps the server's own message.
- `GET /meta/disclaimers?lang=` returns the AI-generated label in one language (its wording still lives only in `src/coach/disclaimers.py`); the web no longer copies it, and a coach insight carries `ai_label_short`.
- The forecast flag "N account(s) without balance left out" is now the code `accounts_without_balance:N` (the MCP `forecast` / `what_if` output shows the code; the insight id of a household forecast card changes once).

### Added: multi-language web app, part 3 of 5 (see `docs/i18n.md`)
- Translated into French and Italian: the pages Alerts, Loans & net worth, Rental property, Memory, Set up, Connections, Household, Who pays what, Kids' money, the child's home page, Gold set and AI usage
  (namespaces `alerts`, `wealth`, `rental`, `memory`, `setup`, `connections`, `household`, `kids`, `quality`). Text sent by the server, the `coach ...` commands and the legal terms of a rental scheme stay as they are.

### Added: multi-language web app, part 2 of 5 (see `docs/i18n.md`)
- The pages Dashboard, Transactions, Categories (with a category's page and Merchants to review), Budgets, Subscriptions & contracts (with the inventory), Calendar, Insights, Ask the coach and "page not found"
  are translated into French and Italian: one namespace per page area (`dashboard`, `transactions`, `categories`, `budgets`, `subscriptions`, `calendar`, `insights`, `coach`). Text sent by the server is still in English (step 4).

### Added: multi-language web app, part 1 of 5 (see `docs/i18n.md`)
- The web app has one **language** setting (English, French, Italian) in the header and the "More" sheet; it replaces the "dates and numbers" selector and sets the interface text, the date / number / money format (`en-GB`, `fr-FR`, `it-IT`) and `<html lang>`.
  The choice is saved in the browser (the former `coach.locale` value is migrated); by default the first supported language of the browser is used, else English.
- `i18next` and `react-i18next`, bundled JSON files under `web/src/locales/<language>/<namespace>.json`, typed keys, a single language registry (`web/src/i18n/languages.ts`): adding a language is a folder of JSON files and one entry.
  A completeness test fails when a translation misses a key, a plural form or a `{{variable}}`.
- Translated in this step: the navigation, the header, the sign-in page and the shared components (dialogs, charts, transaction panel, loan dialog, item forms). The pages follow in steps 2 and 3, the text sent by the server in step 4.

### Changed: release hygiene and hardening (security scan of 2026-10-06)
- The project is licensed under MIT (`LICENSE`); the security contact is GitHub's private vulnerability reporting; the repository URL is set in `pyproject.toml`.
- CI actions are pinned to commit SHAs, Dependabot watches uv, pnpm, GitHub Actions and Docker; the Docker base images are pinned by digest.
- The CAMT.053 import parses with `defusedxml` (DTD, entities and external references refused) in addition to the existing pre-check.
- An unexpected API error no longer names the exception type in the response body.

### Added: rental property under a tax-incentive scheme (E15, see `docs/rental.md`)
- A rental property is an asset of kind `real_estate_rental` with its `account`, `loan`, `scheme` (a name: data), a `commitment` block (start, length, end, rent cap, tenant income limit, reduction rate, extension decision), a typed `market_rate`
  and declared `vacancies`: every figure is the owner's own, nothing is looked up and a missing fact is listed and asked (one open question per property, `memory check` on the links).
- Property flows: three new taxonomy leaves (`housing.property_tax`, `housing.property_management`, `housing.property_insurance`) and generic French rules (taxe fonciere, gestion locative, PNO / GLI, copropriete, an incoming loyer); an older copy gets
  them with `coach taxonomy merge-package`.
- `coach rental list / show / cashflow / pnl / scheme / tax / indicators / flows` and the writes `add / edit / extension / vacancy / market-rate` (previewed, typed yes); the Rental page (`/rental`) and `GET /rental/...`: the monthly cash flow with the effort d'epargne and the
  vacancy months, the yearly P&L (loan interest and principal from the E9 schedule), the commitment with its reminders, the tax-year candidates of the rental-income return (micro-foncier vs reel, the scheme reduction from the declared price and rate, a documents checklist),
  and the renegotiate-or-sell indicators (loan rate vs the market rate typed, end of the commitment, net equity).
- Alert kinds `scheme_end`, `scheme_check`, `rent_missing` (local content, minimal external messages); a `rental` card kind in the Insights feed.
- New MCP tool `rental_overview` (read-only; a property is `asset-N`, never an address, manager, lender or tenant); `tax_candidates` gains `fr-revenus-fonciers` and computes the Pinel reduction from the declared price and rate.
- `[analytics]` settings `rental_reminder_months`, `rental_rent_grace_days`, `rental_rate_gap_pts`. No migration.

### Added: household and people (E14, see `docs/household.md`)
- Members and owners: the Household page (members, account owner and purpose editing, attribution rules, logins, audit); `coach accounts set` stores the member id and refuses an owner that is neither
  `joint` nor a declared member.
- Attribution: every transaction belongs to a person (`member id | joint | nobody`): a manual reassignment (`coach household assign / unassign / why / log / undo`, the transaction panel; recorded
  in `tx_person_log`, reversible), then `attribution` rules in `household.yaml` (account, card last four digits, merchant / description pattern, direction, amount), then the owner of the account.
  `coach explain` and the panel say why.
- Person views: `?member=` on every analytics endpoint, the person switch in the header, a `member` pseudonym argument on the analytics MCP tools (a member view of the dataset: their
  transactions on any account, the balances of the accounts they own).
- The children's money (`coach household kids`, the Kids' money page, the `kids_money` tool): regular pocket money detected or declared, extra top-ups with their source, spending, month-end
  balance trend, pocket versus extra. Kid budgets (`coach household budget`) with a gentle, LOCAL-ONLY `kid_budget` alert kind.
- Transfers across the household's banks: `[transfers] cross_bank_window_days` (default 5) and household members named in a leg as strong evidence; a parent's top-up stays an internal transfer for the
  household and is the child's income in the children's view.
- Who pays what (`coach household allocation / allocate`, the Who pays what page, the `who_pays` tool): shared costs split equally, by income or by custom percentages; the settlement adds up to zero.
- Per-person logins (`coach users add / list / set-role / disable / enable / remove / prefs / audit`, `coach ui --login-link --user ID`): adult = all data, child = own data through `/api/v1/me/*` only,
  enforced server-side on every endpoint, deny by default; per-login preferences; an audit log of web changes and `Source: ui:<login>` in the memory history. The one-time link, CSRF, Host checks, CSP and
  the terminal-only acceptance of memory proposals are unchanged.
- Cross-bank transfer matching runs in two passes (the original window first, pairs locked; then a wider, strong-evidence-only pass on the leftovers): it can only add links. A model gets an age band, never a birth year.
- New MCP tools `household_overview`, `kids_money`, `who_pays`; migration 0022 (additive: `tx_person`, `tx_person_log`, `ui_users`, `audit_log`).

### Changed
- `LoginTokens.issue(user=...)`, `Security.new_cookie(user=...)` and `parse_session` carry a login in the one-time token and the signed cookie; `coach.api.state.UiMemoryStore` derives the history
  source from the login of the current request.

## [0.2.0] - 2026-10-06

First packaged release: everything of epics E0 to E12, installable in ten minutes, and ready to publish once the owner chooses a licence.

### Added: packaging and first run (E13)
- `coach init`: a fresh private home in one command (commented `config.toml`, 0700 data folder, memory skeleton of templates only, secrets generated after a typed
  confirmation, empty encrypted database). Idempotent, never overwrites, `--dry-run`.
- `coach doctor`: Python, SQLCipher, secret store and secrets, database, Enable Banking, permissions, built web app, `claude` command, git; every problem comes
  with its next step; `--json`.
- `coach setup`: the resumable first-run wizard (init, Enable Banking, first bank, first sync, categorize with a printed dry run and an explicit privacy choice,
  onboarding interview, optional daily job). Nothing leaves the machine without a typed word at the step that sends it. The web Setup page shows the same steps read-only.
- `coach setup enablebanking`: guided creation of your own Enable Banking application in restricted mode, with key handling and an optional `coach check`.
- Docker: multi-stage `Dockerfile`, `docker-compose.yml` (non-root, read-only root filesystem, dropped capabilities, port on 127.0.0.1 only, docker secrets),
  `file` secrets backend (`COACH_SECRETS_BACKEND=file`), `coach schedule loop` for the daily job without launchd, container defaults. Statically checked in the tests; not built by them.
- Package metadata, classifiers, the built web app, migrations, data files and the shipped templates in the wheel; `uvx` / `uv tool` / `pipx` install paths.

### Added: open-source readiness (E13-4)
- README for newcomers, CONTRIBUTING (parsers, skills, review checklist), SECURITY, CODE_OF_CONDUCT, `docs/architecture.md`, `docs/reference.md` (the former README),
  `docs/docker.md`, `docs/release.md`, `LICENSE.choose.md`, a Claude Code permission-rules template (`docs/claude-settings.example.json`).
- `coach dev hygiene` and `coach dev release-check`. The scan covers every publishable file (everything `.gitignore` does not exclude) and the sdist member list, with structural rules
  (keys, home-folder paths, valid IBANs, e-mail addresses) and a real-data rule driven by a LOCAL term file (`hygiene-terms.txt`, never in the tree: `coach dev hygiene --build-terms`
  derives it from your database and memory). No hash, salt or list of real terms ships with the package. A missing local file FAILS the release check; `--ci` skips only that rule and says so.
- GitHub Actions workflow (pytest, vitest, type check, lint, release-check).

### Changed
- `.gitignore` reviewed: the built web app (`src/coach/api/static/`) is not committed and is built by CI, Docker and the release procedure; `.claude/settings.json`,
  `config/taxonomy.yaml`, `config/rules.yaml`, `secrets/` and `coach-home/` are ignored.
- The market research moved from `reports/` and `research_notes/` to `docs/research/` (it contains no personal data).
- Test data, docs and examples were re-invented where they echoed real-looking names, places, merchants, amounts and the household's situation; built-in name lists are generic.
- `coach config set-secret`, `wipe` and the scheduler preflight work on the active secret store.
- The web app may listen on all interfaces only inside a real container (marker file or cgroup) AND with `[ui] container_bind = true` (written by the image's `coach init` only); it warns at start
  that the port must be published on `127.0.0.1`, and does not print the login link into the container logs. The compose file publishes on the host's loopback only; a lint rule rejects any example that does not.
- Connecting a bank from the Docker image: the HTTPS redirect server listens on 0.0.0.0 inside a real container only (marker AND `[callback] container_bind`, written by the
  image's init), published as `-p 127.0.0.1:8443:8443`; hints name `docker compose run --rm coach ...`; `coach finish` tolerates a shell-escaped or quoted paste and never echoes the code.
- The `file` secrets backend checks the folder (owner, 0700) and writes with a random temporary name, `O_EXCL | O_NOFOLLOW`, 0600, fsync, atomic rename.
- The sdist excludes `docs/research` and `evals`.

### Known limits
- No licence yet (owner's decision): the release check fails until `LICENSE` exists.
- The Docker image has not been built or run by the automated checks; the Enable Banking guide has not been run against the live control panel.

## Earlier work (summarised by epic; this project had no public releases before 0.2.0)

- **E0 Foundations**: single `coach` package and CLI, `config.toml` + secrets in the Keychain or environment, numbered migrations, SQLCipher encryption, launchd scheduler, encrypted backups.
- **E1 Bank ingestion**: Enable Banking connect / finish flow with a local HTTPS callback, longest history at first link, deduplication, pending transactions, balances, PSD2 call limits,
  consent tracking and renewal, connector health, CSV / OFX / QFX / CAMT.053 imports, internal transfer matching.
- **E2 Normalization and categorization**: per-bank description parsers, a two-level taxonomy, rules, household memory annotations, a local nearest-neighbour step, canonical merchants, split transactions,
  LLM labelling with redaction, batches and a review queue, corrections as permanent memory.
- **E3 Household memory**: plain-file memory (Markdown + YAML) with schemas, validated and recorded writes (local change history), proposals the user accepts, open questions, documents, explain and context for models.
- **E4 Analytics**: coverage-aware averages, recurring payments and price changes, anomalies, cash-flow forecast, budgets, goals, calendar, year in review.
- **E5 Web app**: local React app (dashboard, transactions, categories, budgets, subscriptions, loans, calendar, insights, memory, connections) behind a one-time login link, loopback only.
- **E6 Coach runtime**: finance MCP server (redacted read-only tools, injection guard), coach backends (Claude Code, Anthropic API, Ollama), "Ask the coach", digests, insights.
- **E7 Skills**: monthly review, explain a spike, subscription audit, contract check, find cheaper, mortgage check, what-if, tax helper, onboarding interview.
- **E8 Subscriptions and contracts**: inventory, usage questions, cancellability rules (FR / IT), alternatives with sources, letters, decisions and savings tracking.
- **E9 Loans, mortgage and net worth**: schedules, payment alerts, inference from bank data, net-worth history, scenarios, end-of-lease reminders.
- **E10 Alerts**: signals to events, noise control, the weekly summary, in-app centre, optional macOS / ntfy / e-mail / Telegram channels (all off by default, minimal messages).
- **E11 Privacy, security and compliance**: egress policy and journal, `local_only` / `offline` modes, security audit, export and wipe, AI-generated labels and disclaimers, the agent threat model.
- **E12 Quality**: parser fixtures synthesised without real data, gold set and evaluations, model comparison, usage and cost tracking, structured run logs.
