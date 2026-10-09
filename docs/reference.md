# AI finance coach: reference manual

> The newcomer's introduction, the install paths and the quick start are in the [README](https://github.com/mmornati/ai-finance-coach/blob/main/README.md). This is the full reference of
> every command and setting, kept from the development of epics E0 to E12 and extended for E13 (packaging). Commands are shown as
> `uv run coach ...` (from a source checkout); with an installed package, drop `uv run`.

Personal finance coach: bank ingestion (Enable Banking, PSD2) -> classification (rules, household
memory, LLM) -> analytics, stored in a local encrypted SQLite (SQLCipher) database.
Everything is driven by one CLI: `uv run coach --help`.

```
src/coach/
  cli.py  config.py  secrets.py  db.py  schedule.py  backup.py
  ingest/     Enable Banking client + sync, consents, auth + local callback server, accounts, health, file imports
  transfers.py  internal transfer matching between the household's accounts
  notify.py   optional macOS notification (osascript)
  classify/   parsers/ (one per bank), rules (+ taxonomy.yaml, rules.yaml), memory annotations, LLM backends,
              kNN, canonical merchants, splits, taxonomy, commands
  memory/     the memory store (schemas, validated + recorded writes, history, proposals, questions, documents, check,
              explain, context): see docs/memory.md
  analytics/  report functions returning data
  mcp/        the finance MCP server (E6): redacted read-only tools, privacy assertion, injection guard: docs/coach.md
  agent/      the coach runtime (E6): backends (claude -p / Anthropic API / Ollama), prompts, jobs, insights, digests
  skills/     the coach skills (E7): deterministic helpers (loans, cancellability, review, subscription audit, what-if, tax,
              onboarding) and their finance tools: see docs/skills.md
  subs/       the subscriptions & contracts optimizer (E8): inventory, usage, alternatives, letters, decisions: see docs/subscriptions.md
  loans/      loans, mortgage & net worth (E9): schedules, payment alerts, inference, net-worth history, scenarios, LOA end of contract: see docs/loans.md
  rental/     rental property under a tax-incentive scheme (E15): cash flow, P&L, vacancy, scheme commitment, tax-year figures, indicators: see docs/rental.md
  alerts/     alerts & the weekly summary (E10): signals -> events, noise control, channels (macOS / ntfy / e-mail / Telegram), privacy guard: see docs/alerts.md
  api/        the local web API (FastAPI) and the built web app (static/): see docs/ui.md
web/          the web app source (React + TypeScript + Vite + Tailwind, pnpm): `pnpm build` writes src/coach/api/static/
  migrations/ numbered SQL files (0001_initial ... 0017; `.sql` files, or `.py` data migrations that define `run(con)`)
memory/       household memory read by the classifier and by the LLM jobs (see memory/README.md)
tests/        pytest suite (synthetic data only)
```

## Setup

The short way (E13, details in the README and [docs/docker.md](docker.md)):

```
uv run coach init                  # config.toml (commented), private data folder, memory skeleton, secrets (typed confirmation), empty encrypted DB
uv run coach doctor                # python, SQLCipher, secrets, database, Enable Banking, permissions: every problem comes with its next step
uv run coach setup enablebanking   # guided creation of YOUR Enable Banking application (restricted mode); validates with `coach check` if you say yes
uv run coach setup                 # the first-run wizard: bank link, first sync, categories (privacy choice), interview, daily job
```

`coach init` never overwrites anything that exists and prints what it did (`--dry-run` shows the plan). The secrets are generated only after
you type `generate` in a terminal (the Keychain always asks; the file backend can be told `--non-interactive` for containers). `coach setup`
is resumable: every step detects its own state, and nothing leaves the machine without a typed word at the step that sends it (`coach setup
--status` shows where you are; `--step NAME` runs one step; `--reset` forgets the wizard's own state file). The web Setup page shows the
same steps read-only.

The manual way:

```
uv sync                                  # Python 3.13 (see .python-version), all wheels, no brew needed
cp config.example.toml config.toml       # then edit; or migrate the old prototype .env (below)
uv run coach config set-secret db_key --generate      # SQLCipher key -> macOS Keychain
uv run coach config set-secret backup_key --generate  # backup key    -> macOS Keychain
uv run coach config show                 # effective configuration, secrets masked
```

Back up the generated keys in your password manager: losing `db_key` makes the database
unrecoverable, losing `backup_key` makes the backups unreadable.

### Configuration

Non-secret settings live in `config.toml` (git-ignored; template: `config.example.toml`): data and
memory directories, DB path, Enable Banking `app_id` / `redirect_url` / `private_key_path`, LLM
backend/model, sync daily limit, scheduler time, backup dir and retention. Relative paths are
relative to the folder holding `config.toml`. Override the file with `--config PATH` or `$COACH_CONFIG`.

Enable Banking values are resolved as: env vars `EB_APP_ID`, `EB_REDIRECT_URL`, `EB_PRIVATE_KEY_PATH`
> `config.toml` > legacy `prototype/ingest/.env`. Migrate the legacy file once with
`uv run coach config import-env`, then delete it.

### Secrets

Looked up in this order: environment variable -> secret store -> error. The secret store is the macOS Keychain (service `ai-finance-coach`,
the default) or, where there is no Keychain (Docker, a Linux server), a folder of files: set `COACH_SECRETS_BACKEND=file` and
`COACH_SECRETS_DIR=/path` (default `/run/secrets`, where Docker mounts its secrets). One file per secret, named like the secret (`db_key`),
holding only the value, mode 0600 in a 0700 folder; a file readable by others is refused (`COACH_SECRETS_ALLOW_READABLE=1` accepts
group / other READABLE files, for Docker swarm / Kubernetes mounts that are 0444; a writable one is never accepted). `coach config set-secret`,
`coach init` and `coach wipe` work on whichever store is active.

| secret | env var | used for |
|---|---|---|
| `db_key` | `COACH_DB_KEY` | SQLCipher encryption of the database |
| `backup_key` | `COACH_BACKUP_KEY` | AES-256-GCM encryption of backups |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | optional; needed only for `llm.backend = "anthropic-api"` |
| `openai_api_key` | `COACH_OPENAI_API_KEY` | optional; needed only for the `openai-compatible` backend (OpenRouter, Eden AI ...). Not `OPENAI_API_KEY`, on purpose |

Set with `uv run coach config set-secret NAME [--generate]`. `coach config show` only reports
whether a secret is set and where it comes from, never its value.

## Database

All code goes through `coach.db.connect()`. A plaintext DB is refused unless you pass the global
`--insecure` flag or set `insecure_plaintext_db = true` in `config.toml`.

```
uv run coach db import-prototype --source PATH   # copy a prototype-shaped DB -> data/finance.db, migrate, verify counts
                                   # (the shipped prototype data was removed: there is no default source any more)
                                   # (the result is ALWAYS plaintext: run `db encrypt` next)
uv run coach db encrypt            # plaintext -> SQLCipher; keeps finance.db.plaintext.bak: delete it after checking!
uv run coach db status             # encryption state, applied/pending migrations, row counts
uv run coach db migrate [--create] # also applied automatically on connect; --create makes a new DB
```

No command silently creates a database: a missing file is an error ("No database at ..."), so a wrong `db_path`
never produces an empty DB. `db migrate --create` and `db import-prototype` are the explicit ways to create one
(encrypted when `db_key` is set). `db import-prototype --force` moves an existing DB aside to `*.pre-import.bak`.
Before applying migrations to a non-empty DB a safety copy `*.pre-migrate-<ts>.bak` is written; a DB migrated by a newer
version is refused. If `db encrypt` is interrupted, a leftover `finance.db.encrypting` blocks startup until you delete it.
`db encrypt` holds a write lock on the source from the export until the swap, so a concurrent sync/classify fails
with "database is locked" instead of silently losing updates; run it when no job is running. `db status` and `db encrypt`
list any plaintext copies (`*.plaintext.bak`, `*.pre-migrate-*.bak`, `*.pre-import*.bak`) to delete after verification.
DB files are 0600 and `data/`, `logs/`, `backups/` are 0700.

Migrations are numbered SQL files in `src/coach/migrations/` tracked in `schema_migrations`; applying
them to a prototype database is idempotent and lossless (`CREATE TABLE IF NOT EXISTS`).

SQLCipher binding: `sqlcipher3-wheels` (self-contained wheels for macOS arm64, Python 3.11-3.14), so
no `brew install sqlcipher` is needed.

## Everyday use

```
uv run coach check | banks --country IT --q fineco
uv run coach connect --bank "<name>" --country IT   # login in the browser, redirect received locally (see below)
uv run coach sync [--account UID|label] [--full] [--force]
uv run coach consents | health | accounts | transfers      # all offline, read the local DB only
uv run coach stats
uv run coach normalize
uv run coach classify run | enrich | review [--json] [--accept KEY...] | correct "<key or regex>" group.leaf --name "Name" | compare | report
uv run coach taxonomy list | add group.leaf "description" | rename old.id new.id
uv run coach merchants list [--proposals] | group | merge KEY... [--name N] | split KEY | rename ENTITY NAME | category ENTITY group.leaf
uv run coach split TX_KEY food.groceries:70 housing.furniture:rest   # also --clear, or no parts to show
uv run coach ui [--port 8765] [--no-browser]                         # the web app on http://127.0.0.1:8765/ (see below)
uv run coach privacy status | report                                 # what may leave this machine; egress inventory + journal (E11)
uv run coach security audit [--json]                                 # secrets, permissions, plaintext leftovers, exposure (E11)
uv run coach export | wipe --dry-run                                 # take / list the deletion of all your data (E11)
```

## Bank connections (E1-7, E1-8, E1-9)

**Local callback.** `connect` / `reconnect` start an HTTPS server on the host and port of
`[enable_banking] redirect_url` (default `https://localhost:8443/callback`, which must be whitelisted in your Enable
Banking app), print/open the bank login URL, receive `?state&code`, check the state against `pending_auth`, complete
the session through the same code path as `coach finish`, show a success/error page and shut down. It gives up after
`[callback] timeout_seconds` (default 600; `--timeout` overrides) and leaves the authorisation pending, so
`coach finish "<redirect url>"` still works afterwards (a pending authorisation older than 24 h is expired). The
bank's `/sessions` response is saved in `pending_auth` before it is interpreted (the code is single-use): if storing
it fails, fix the cause and run `coach finish --replay <state>`. `--no-server` keeps the manual copy-paste flow; a redirect URL
whose host is not loopback also falls back to it. The server binds to loopback only and ignores any other path.

TLS: a self-signed certificate for `localhost`/`127.0.0.1`/`::1` is generated with the `cryptography` package into
`<data_dir>/tls/` (key mode 0600, directory 0700) on first use and reused until 30 days before expiry. The browser
warns once about it: accept it for the redirect page. No mkcert or brew involved.

**Consents.** `coach consents` lists each bank session with days left and a status: `ok`, `expiring` (<= 14 days),
`urgent` (<= 3), `expired`, `revoked` (exit code 1 for the last two), or `unknown` (amber: the bank's live status is not
`AUTHORIZED` nor a known dead one, or the live check failed, e.g. HTTP 404; only `AUTHORIZED` counts as live-ok); `--refresh` asks `GET /sessions/{id}` for the
bank's live status (a sync does this too, so a dead consent is reported instead of retried). The daily job
(`schedule run`) logs a warning on every run from D-14 and fails (exit 1) on an expired/revoked consent. With
`[notify] macos = true` it also sends one macOS notification per session and threshold (`d14`, `d3`, `expired`) via
`osascript`; default off.

**Reconnect.** `coach reconnect "<bank name or session id>"` starts a new authorisation for the same bank and
country. When it completes, the old session is marked `replaced`. `accounts.uid` is a stable internal id: if the
bank returns NEW account uids, each is matched ONE-TO-ONE to an existing record of the same bank whose session is
being replaced or is dead, by (hash of the IBAN, currency, account type): currency pockets sharing an IBAN stay
separate. Accounts without IBAN (cards) match on the product name + currency + type. The record keeps its uid,
label/owner/purpose and history, and only `accounts.api_uid` (the uid used in API calls) changes. When the match
is ambiguous nothing is merged silently: the new account and its candidates get `needs_review` (left out of
analytics, not synced, flagged in `accounts`/`health`) until you run `coach accounts merge OLD NEW` (NEW is folded
into OLD, duplicates by key dropped) or `coach accounts set UID --resolve`. Accounts the
new session does not return stay on the retired session and are no longer synced (reported).

**One consent per bank.** `connect` refuses to start a second active consent for the same bank and country unless
`--replace`: a bank allows few concurrent authorisations per application and user, so a second consent (for example
from the BankMCP server using the same Enable Banking app) can invalidate the first one. Keep one owner per bank.
`sync` goes through every active session; a failure (HTTP error, network, dead consent) on one bank or account is
recorded in `sync_log` and does not stop the others.

**Accounts.** `coach accounts` lists bank, label, owner, purpose and the masked IBAN;
`coach accounts set <uid|label> --label ... --owner joint|<member> --purpose main|cards|rental|kids|savings`
and `--exclude` / `--include` (an excluded account stays in the DB but is left out of `categorised()`, hence of
every report). With members declared (E14-2), `--owner` must be `joint` or a member (id, name or alias) and the member id is what is stored.

## Household and people (E14)

Reference: [household.md](household.md).

```
uv run coach household show [--json]                       members, owners and purposes, transactions by person, rules, logins
uv run coach household assign TX MEMBER|joint [--note T]   reassign one transaction by hand (recorded, reversible);  unassign TX;  why TX;  log [TX];  undo LOG_ID
uv run coach household rule list|add|remove ...            attribution rules of household.yaml (preview first; --dry-run, --propose, or a terminal and a typed yes)
uv run coach household kids [MEMBER] [--months N]          pocket money, extra top-ups, spending, balance trend, pocket vs extra
uv run coach household pocket set|clear MEMBER ...        declare the pocket money a child is meant to get
uv run coach household budget list|set|remove ...          weekly / monthly kid budgets
uv run coach household allocation [--months N]             who pays what;   household allocate set|remove ...   edits the allocation rules
uv run coach users list|add|set-role|disable|enable|remove|prefs|audit    per-person logins of the web app (terminal only)
```

## Connector health (E1-11)

`coach health [--json] [--refresh]` (code: `coach.ingest.health.health(con)` returns a `HealthReport` dataclass,
`.to_dict()` for the future API) shows per bank and account: last successful sync, last error, syncs used today
vs the daily limit (UTC bounds of the local day), consent days left, transaction date range, pending count, and a
level. **red** = consent expired/revoked/<= 3 days, or the latest sync attempt failed; **amber** = consent <= 14
days, never synced, or stale (no successful sync for more than `[health] stale_days`, default 2). Exit code 1 if
anything is red. It never calls the network unless `--refresh`.

## File imports (E1-10)

```
uv run coach import FILE --account <uid|label|new:Label> [--profile NAME] [--dry-run]
uv run coach import --list-profiles
```

Formats: CSV (profile-driven), OFX/QFX (SGML 1.x and XML 2.x), CAMT.053 XML (a DOCTYPE/ENTITY is refused). The
format is detected from the extension/content. `--account new:Label` creates a manual account (`source='import'`,
uid `imp-<label slug>`, reused on the next import); an existing uid, bank uid or label imports into that account
(also one that is synced). The file's IBAN/currency must match the account, and `new:Label` is refused when the
file's IBAN already belongs to an existing account (use `--account <uid>`, or `--force`).

CSV mapping profiles are TOML files in `config/import_profiles/` (`[import] profiles_dir`): encoding
(`auto` = utf-8 then cp1252/latin-1), delimiter, `skip_rows` or `header_contains` (locate the header line),
`skip_footer`, date formats (`%d/%m/%Y`...), decimal comma/point, separate `debit`/`credit` columns or one signed
`amount`, `fee`, a `[filter]` on a state column. Shipped: `generic-csv-fr`, `caisse-epargne-csv`, `revolut-csv`,
written from publicly documented layouts and **to verify with a real export**. Build your own inline
(`--date-col --desc-col --amount-col|--debit-col --credit-col --delimiter --decimal --date-format --skip-rows
--header-contains`) and keep it with `--save-profile NAME`.

Dedup: every row gets the fingerprint (booking date, amount, normalised description: accents/case/punctuation
ignored). Per account and fingerprint, a file with n rows and a DB holding m inserts max(0, n - m): re-importing a
file adds 0, overlapping files add only the difference, two genuine identical purchases on the same day are kept.
Rows imported into an account that is also synced are replaced by the API version when it brings the same
fingerprint (tx_overrides and transfer links follow the new key); rows already synced are not imported again.
Limits: the bank feed and the bank's export often word descriptions differently or date a booking one day apart;
such rows are then not recognised as the same and appear twice (use `--dry-run`, import only the period the API
does not cover). Imported rows need `coach normalize` / `classify run` like any other. PDF statements are not
supported yet (follow-up).

## Internal transfers (E1-12, E14-7)

`coach transfers match [--dry-run] [--window N]` links a debit on account A and a credit on account B (A != B,
same currency, same absolute amount, booking dates at most `[transfers] window_days` = 3 apart, neither leg already
linked, neither a card/ATM operation, both rows normalised i.e. with a known tx_type) when ALL hold: the pair is
unambiguous both ways; both legs look like transfers (tx_type transfer_*/internal_transfer/wero_*/person_transfer_*
or a transfer word in the text); neither leg is classified `income.*`; and the confidence reaches
`[transfers] min_confidence` (0.85). Confidence is high (0.98 - 0.03/day) only with strong evidence, i.e. the other
account's IBAN in a description, a different holder's name as leading counterparty, or BOTH legs recognised by the
parser as own-account transfers (one such leg is not enough, and the holder's own name inside a salary line proves
nothing: own-account detection looks at the counterparty at the start of the descriptor, using the tokens of one real
holder, never product names such as "Visa Premier"); a leg labelled or annotated `income.*` is never a candidate;
card payments to a top-up merchant (`[transfers] topup_merchants`, default REVOLUT, LYDIA, PAYPAL) can pair with
the credit on the other account but only as proposals; mere transfer-looking
legs score 0.70 - 0.05/day, so e.g. rent paid vs rent received or a salary vs a same-amount payment are never
auto-linked. Pairs below the threshold are proposals: `coach transfers --proposals`. The scheduled job only
proposes unless `[transfers] auto_link = true` (it logs the count). Ambiguous candidates are never auto-linked: `coach transfers --unmatched` lists them with their tx_keys and `coach transfers link OUT_KEY IN_KEY`
(a unique fragment of the key is enough) settles them by hand; `coach transfers unlink <id|tx_key>` undoes a link
and the pair is not proposed again. `coach transfers` lists the links (`transfer_links`, confidence, method).
`schedule run` runs the matching step after `normalize` (propose-only by default); `normalize`/`classify` themselves never create links.

**Across the household's banks (E14-7).** Legs on DIFFERENT banks may be booked up to `[transfers] cross_bank_window_days` (default 5) apart (a weekend, a holiday, an instant
credit), against `window_days` for the same bank. Descriptions rarely agree between banks, so a household MEMBER named in a leg (every word of the name, or an alias) who owns the account
on the other side, and not this leg's own account, is strong evidence too (a parent's debit naming the child, the child's credit naming the parent). `coach transfers --proposals` marks
`household transfer` (and `to a child`); the pair carries `scope: household` and `to_child` in the API. Both legs stay `transfer.internal`, so a parent's top-up leaves the household's
spending and income, and it is still the child's income in `coach household kids`.

Classification precedence: per-transaction override -> **matched transfer leg (`transfer.internal`)** -> type rule
-> user merchant memory -> regex rule -> LLM label -> other; memory annotations on top. Both legs of a link
therefore leave spending and income (analytics already ignore `transfer.*`). A single-account database has no
cross-account pair, so its report is unchanged.

Classification precedence per transaction: per-transaction override -> matched internal transfer ->
type rule -> user merchant memory -> regex rule (`rules.yaml`) -> LLM merchant label -> other.uncategorized;
memory annotations (`memory/categorization.yaml`) are applied on top. The LLM labels unique merchants, never
individual transactions or person-to-person transfers.

## Normalisation and categorisation (E2)

**Parsers.** `normalize` parses every descriptor with the parser of its account's bank
(`classify/parsers/`: Fortuneo, Caisse d'Epargne, CIC, Revolut, a generic Italian one, and a generic fallback chosen by
`accounts.bank`, then by an `IT` IBAN / country). All return the same fields: `tx_type`, `op_date`, `merchant_raw`,
`merchant_key`, FX, and (stored in `tx_parse_meta`) counterparty, mandate reference, creditor id (ICS) and a reference
(loan number, SEPA end-to-end id). Direct debits drop mandate / ICS / date / number noise so every month of one
creditor shares one merchant key. Types: card, card_refund, atm, direct_debit, loan_payment, bank_fee, topup,
savings_internal (Revolut vault moves), fx_exchange, cheque, and the transfer types (internal / person / company).
To add a bank: `parsers/<bank>.py` with `parse(RawTx, Household) -> dict`, `register(...)`, fixtures in
`tests/test_parsers.py` and a real-shaped fixture generated with `coach eval fixtures synth` (see "Quality" below). The Italian parser is verified on synthetic samples only.

**Residual privacy risk.** The guard recognises people by titles, given names, initials and the absence of any
organisation signal. A legal entity named after a family ("SCI DURAND", "CABINET MARTIN") or a shop named after its
owner can pass as an organisation or be held back wrongly; held-back items are visible in `classify review` and
`run --dry-run` shows exactly what would be sent. Review the dry run before the first run on a new bank.

**Own accounts and people.** A transfer is "internal" only when ALL name tokens (at least two) of one holder lead the
counterparty (holders come from `accounts.owner` / account names and from `memory/household.yaml` `members:` when that
file exists); a lone surname only marks a probable family member. Person names are never LLM items: person transfers
have their own types, and a second guard (`classify/candidates.py`) holds back anything that still looks like a
person (shown in `classify review` as `held_back_person_like`). That guard is DEFAULT-DENY for anything that is not a
shop: a transfer / direct-debit counterparty is sent to an LLM only if it looks like an organisation (legal form or
organisation word such as SAS, CPAM, ASSURANCE, SYNDIC; a single brand-like word), is already a known card/direct-debit
merchant, or matches `[classify] llm_allowlist`. P2P prefixes (PAYLIB, LYDIA, WERO, SATISPAY) and Revolut
"Payment from / Sent from" lines are person transfers. Few-shot examples and kNN hints come only from shops and
creditors (never transfer.* / income.* labels, never person-like keys), titled names ("DR X") are masked, and
`coach classify run --dry-run` prints the exact redacted requests (items, examples, hints) without calling anything.

**Database changes.** `[db] auto_migrate` (default true) lets any command apply pending migrations when it opens the
database, with a notice on stderr; set it to false and only `coach db migrate` changes the schema (other commands refuse
to run on a database that is behind). Migrations already applied are never edited.

**Loans.** `loan_payment` maps to `debt.loan_repayment` (a bank can hold a house loan AND an investment-property loan, so
`housing.mortgage` is never assumed); on an account whose purpose is `rental` it is `housing.rental_property_loan`
(`type_rules_by_account_purpose` in `rules.yaml`). A memory annotation overrides both.

**Bank references (H1).** Fortuneo's `entry_reference` is `<date>T00:00:00-<position>`; a late booking shifts the
positions. Such transactions are keyed by content (`<account>:cfp:<hash of date, amount, currency, normalised
description, occurrence>`). Migration 0007 re-keys existing rows without touching their content and logs
old -> new in `tx_key_remap`; annotations in `memory/categorization.yaml` that still list an old key keep matching and
are reported (`normalize`, `classify report`, `sync` print a warning; memory/ is never edited). A stored transaction is
never overwritten by a different one: if an incoming row with a known key has other content, both are kept and the
conflict is logged. A bank that re-words the same transaction (same reference, same amount, date within a day) just
gets its text refreshed; `coach health` flags syncs that kept both rows.

**LLM backends (`[llm]`).** `claude-code` (headless `claude -p`, started with the coach runtime's minimal environment: no `COACH_*` key, no API key), `anthropic-api` (official SDK, key from the
`anthropic_api_key` secret, JSON-schema output, cached static prompt, optional `batch_api = true` Message Batches run)
`ollama` (local server, `format` = JSON schema) and `openai-compatible` (any OpenAI-style `/chat/completions` API such as
OpenRouter or Eden AI: `openai_base_url`, `openai_model`, secret `openai_api_key`, JSON schema through `response_format` with a
JSON-mode fallback for models without it). Same prompt and output contract; every call is recorded in
`llm_usage` (backend, model, tokens in/out/cache, cost estimate, duration, purpose). Every item is redacted first
(IBAN, e-mail, phone, long digit runs, ids, the household's own name tokens). `classify enrich` (web search) needs
`claude-code` and is OFF unless `[privacy] web_enrich = true` (shops only, never a person-like name; `--dry-run` shows the request). Model output is validated (category must be in the taxonomy, confidence clamped, ids in range). Each
finished batch is committed with its usage before the next one; a failed batch never loses the paid ones (re-run asks
only for the missing merchants). With `batch_api = true` the batch id is saved in `llm_batches`: after a timeout the same
command resumes it (nothing is paid twice) and only failed jobs are retried. `ollama_url` must be a loopback address
unless `ollama_allow_remote = true`; the Anthropic client always uses https://api.anthropic.com (`ANTHROPIC_BASE_URL`
is ignored; `llm.anthropic_base_url` overrides). Prompt caching: the static prompt (instructions + taxonomy) is the
cacheable prefix; models have a minimum cacheable length (up to 4096 tokens on Haiku 4.5) below which caching silently
does not happen: `llm_usage.cache_read_tokens` tells whether it did. Unknown model ids log cost NULL, never 0.

**Similar merchants (`[classify]`).** Character n-gram TF-IDF over merchant keys (pure Python). The 5 nearest labelled
merchants go to the LLM with each shop / creditor item; a key whose nearest labelled neighbours (user-confirmed or
LLM >= 0.85) are >= `knn_threshold` similar and agree is labelled without the LLM (source `knn`; never feeds the corpus;
never applied to transfers). `classify run --no-knn` disables it.

**Canonical merchants, splits, taxonomy.** `coach merchants group` merges keys whose merchant NAME normalises
identically ("MacBurger's" / "Mac Burger's"); similar names are only proposed. An entity's category is a default for
alias keys without a label; a category the USER sets (`merchants category`) also beats LLM / kNN labels of its keys;
neither ever overrides a user label or a rule. Placeholder names ("Unknown merchant", "N/A"...) never group keys. `coach split` divides one
transaction over categories (parts sum exactly to the amount, sign preserved, no NaN or fractions of a cent); reports
use the parts. A memory annotation's tags and event still apply to every part, but its category does not (the command
warns when an annotation would have set one).
Taxonomy edits go to your own copy (`config/taxonomy.yaml`, `config/rules.yaml`, created on the first edit; the
packaged files are never modified, so a reinstall cannot lose renamed ids).
`coach taxonomy rename` migrates merchants, overrides, comparisons, entities, splits and `rules.yaml`, and prints the
`memory/` annotations that mention the old id (it never edits memory/).

## Household memory (E3)

`memory/` (git-ignored) holds what the bank cannot tell: members, annotations that explain transactions, loans,
assets, contracts, events, open questions. Full reference: `docs/memory.md`. Everything programmatic goes through
`coach.memory` (the API the UI of E5 and the coach of E6 will call); the CLI is a thin layer over it.

```
uv run coach memory show <file|id> [--json]          # a file or one item (annotation, asset, loan...) by id
uv run coach memory set <file|id> <path> <value>     # validated (pydantic schemas), comments kept: set livrets-a balance 1200
uv run coach memory annotate --merchant-key '^X' --category food.groceries --tags one_off --note ... [--dry-run]
                                                     # preview: matched transactions (count, total, dates, sample merchants)
uv run coach memory new liability|contract <id> --kind ...      # then `memory set`
uv run coach memory member add --id ... --name ... --role adult|child [--birth-year --alias]   # household.yaml
uv run coach memory append events.md "## event-id ..."          # Markdown files
uv run coach memory history [file] | diff [change-id|file] | revert <change-id>
uv run coach memory propose|proposals|accept|reject  # the coach only PROPOSES; a human accepts
uv run coach memory check [--json] [--no-db]         # schemas, annotations vs transactions, stale facts; exit 1 on errors
uv run coach memory context [--md|--json] [--max-tokens N] [--names]   # privacy-aware summary for an LLM
uv run coach memory totals [--json]                  # manual assets / liabilities (net worth input)
uv run coach memory doc add|list|extract             # documents; extract = redacted dry run, --send -> a PROPOSAL
uv run coach questions list|answer|dismiss|reopen|add|generate [--dry-run]
uv run coach memory migrate-questions [--write]      # one-time: open-questions.md -> open-questions.yaml
uv run coach explain <tx_key|fragment> [--json]      # the full decision chain of a transaction
```

* **Writes are safe.** YAML is edited with a round-trip parser: comments, order and layout survive (an untouched
  file is rewritten byte for byte; an edit that would drop a comment is refused); files are written atomically
  (temp + `fsync` + `os.replace`) under a lock, and every change is committed to a private git repository in
  `memory/.history.git` (never pushed, never the project repository), which also snapshots your hand edits.
  `coach memory revert <change-id>` undoes any change.
* **The coach never writes memory.** `coach memory propose` (or `annotate --propose`) stores a validated change in
  `memory/.proposals/`; only `coach memory accept <id>` applies it.
* **Open questions** are structured (`open-questions.yaml`; the Markdown file is a generated view).
  `coach questions generate` derives them from the data (money at stake, unexplained large payments, recurring
  debits without a contract, empty loan fields, stale values, accounts without owner, members without birth year) and
  never repeats an answered or dismissed one. The `household-questions` skill walks you through them.
* **Documents** go to `memory/documents/` (0600, hashed names). `doc extract` reads text locally (no OCR), redacts
  it, shows exactly what would be sent, and only with `--send` asks the LLM backend; the result is a proposal
  with the source snippet for every field.
* **Privacy.** `memory context` replaces the household's names by member ids (declare them in `household.yaml`) and
  removes IBANs, e-mails, phones and raw bank descriptions; `--names` is for local reading only.
* `coach health` and `coach schedule run` include a (warn-only) memory check; `[memory]` in `config.toml` sets the
  history switch and the stale / threshold values (`config.example.toml`).

## Analytics (E4)

Every number is computed by code (`src/coach/analytics/`), reproducibly; the coach and the UI only quote it. Full
reference (definitions, rules, formats): `docs/analytics.md`. All commands accept `--json` (money as strings with two
decimals, every result carries `coverage` and `evidence`) and `--as-of YYYY-MM-DD`.

```
uv run coach coverage                                # per-account first/last date and fully covered months: the base of every average
uv run coach averages [--top N]                      # monthly spending per category over the months the accounts carrying it cover
uv run coach cashflow [--months 6] [--by purpose|owner]   # income, spending (refunds netted, transfers out), saved, savings rate
uv run coach recurring [--all] | changes | missing | refresh | dismiss-change ID   # subscriptions, loans, salary; price changes; no-contract feed (reads never write; --refresh stores)
uv run coach anomalies [--all] | refresh | dismiss ID [--note ..]   # spikes, duplicates, new merchants, large payments
uv run coach forecast [--days 90] [--account X] [--events]    # 30/60/90-day balances with a band, negative-balance flags
uv run coach budget suggest | set CATEGORY AMOUNT [--rollover --source coach --yes|--propose] | list | status
uv run coach calendar [--days 60] [--ics plan.ics]   # recurring, loan instalments/ends, renewals, notice deadlines, consents, stale assets
uv run coach goals list | set ID --target N --asset|--account|--tag X [--date ..] | status
uv run coach review year [YYYY] [--json]             # totals incl. one-offs, events, top merchants, changes vs last year
uv run coach analytics refresh                       # recompute + store recurring series and anomalies (also run by schedule)
```


* **Coverage first.** The accounts have different histories; a category is averaged over the months fully covered by
  every account that carries it, never over 12 by default. `classify report` uses this too (`--legacy` shows the former
  12-month division).
* **Budgets and goals are memory** (`memory/budgets.yaml`, `memory/goals.yaml`): `budget set` / `goals set` preview the
  diff and write through the memory store (validated, atomic, history with `--source`) only after a confirmation or
  `--yes`; the coach can only `--propose`. Recurring series and anomalies are cached in the database
  (`recurring_series`, `anomalies`, migration 0011) and refreshed idempotently.
* Thresholds are in `[analytics]` of `config.toml`; `coach schedule run` ends with a warn-only analytics step
  (`[schedule] analytics`). The `analytics-overview` skill tells the coach which command answers what.

## Subscriptions & contracts optimizer (E8)

One inventory of every recurring cost, with contract status, usage, cancellation rules, cheaper offers, local cancellation letters and a
savings tracker. Nothing is cancelled or sent: you act, the app keeps the books. Reference: [docs/subscriptions.md](subscriptions.md).

```
uv run coach subs list [--json] [--all] [--group G] [--missing-contract]   # the canonical inventory (also the Subscriptions page)
uv run coach subs show REF                          # one service: price history, contract, usage, cancellation rules, alternatives
uv run coach subs draft-contracts [--dry-run|--write]   # contract drafts for the series without a contract file (proposals by default)
uv run coach subs usage REF --frequency never --last-used 2026-06-01   # how you use it (reminder after 60 days unused, by your record)
uv run coach subs usage-questions [--add]           # the usage questions still to ask (never duplicated)
uv run coach subs alternatives add REF --provider P --offer O --price 8.99 --url https://... | list | remove ID
uv run coach subs letter CONTRACT [--lang fr|it|en] [--channel lrar|email|online] [--out FILE]   # text only, generated locally
uv run coach subs contact show | set --address "..." --email ...        # for the letters: local only, never sent to a model
uv run coach subs decide REF cancelled|renegotiated|switched|downgraded|kept --before 12.99 --after 0 | decisions list|confirm|reject|remove
uv run coach subs savings [--json]                  # realised savings, verified against the bank data
```

## Loans, mortgage & net worth (E9)

Every loan and lease with its amortization schedule (capital due today, interest per calendar year, total cost), its bank payments and alerts
(missed / changed / extra payment, wrong account), terms inferred from the payments (only ever proposed), early-repayment and renegotiation scenarios
on the real schedule, the LOA end-of-contract view, and the net worth by category and person with its monthly history. A missing figure is listed,
never guessed. Reference: [docs/loans.md](loans.md).

```
uv run coach loans list | show ID | schedule ID [--rows] [--year Y] [--propose-questions] | payments ID | alerts   # what the data say
uv run coach loans add ID --kind mortgage            # guided (or --set rate.nominal=3.1 ...): validated, previewed, written after a typed yes
uv run coach loans edit ID --set insurance.monthly=40 | odometer ID --km 21000 | lease [ID]
uv run coach loans infer ID [--propose]              # suggest missing terms from the observed instalments (never written; --propose queues a proposal)
uv run coach loans scenario prepay ID --amount 20000 [--on D] [--penalty EUR] | renegotiate ID --new-rate 1.9 [--bank-fees E] | insurance ID --alternative 18 [--save]
uv run coach networth [--history] [--json] [--record]   # accounts + assets - loans, unknowns listed; --record stores today's snapshot
```

## Rental property (E15)

The cash flow and the yearly P&L of a rental flat (rent - loan - charges - fees - taxes, the monthly effort d'epargne, the vacancy months), the commitment of its tax-incentive scheme (end date, reminders,
rent cap and tenant income checks on YOUR figures, the extension decision), the figures of the rental-income return as candidates (micro-foncier vs reel, loan interest from the schedule, the scheme
reduction from the price and rate you declared, a documents checklist) and the renegotiate-or-sell indicators (loan rate vs the market rate you entered, end of the commitment, net equity). A missing fact is
listed, never guessed; nothing is looked up. Reference: [docs/rental.md](rental.md).

```
uv run coach rental list | show [ID] | cashflow [ID] [--months N] | pnl [ID] [--year Y] | flows [ID]     # what the data say (reads never write)
uv run coach rental scheme [ID] [--propose-questions]      # the commitment, reminders, rent cap / income checks, the missing facts (one open question)
uv run coach rental tax [ID] [--year Y]                    # micro-foncier vs reel candidates, scheme reduction, documents: not a return, nothing is filed
uv run coach rental indicators [ID] [--market-rate R --market-date D] [--bank-fees E ...]   # loan rate vs the rate you typed, end of commitment, net equity
uv run coach rental add ID --set account=LABEL --set loan=ID | edit ID --set commitment.years=9 | extension ID --decision extend --years 3
uv run coach rental vacancy ID --start D [--end D] | market-rate ID --rate R [--as-of D]               # previewed, written after a typed yes
```

## Alerts & the weekly summary (E10)

One engine turns signals the app already computes (a bank consent about to expire, a sync that keeps failing, an unusual charge, a price rise,
a balance that may run short, a budget over, loan alerts, lease end, unused subscriptions, a cancelled service still charged) into **events** with
stable ids: the same situation is one event however often it is checked, it is sent to a channel once, and again only if its severity goes up.
The in-app **alerts center** (`/alerts`) is always on. Everything outside the app is opt-in and OFF by default: a macOS notification, **ntfy**,
**e-mail (SMTP over TLS)** and **Telegram**, enabled by you in `config.toml` `[alerts.*]`, secrets in the Keychain. Messages to ntfy / e-mail /
Telegram are **minimal and non-identifying** ("Coach: 2 new alerts (1 high). Open the app."; an opt-in `summary` mode adds the kind and an amount
rounded to 10 EUR) and pass the same privacy guard as the finance tools. Noise control: per-kind switches, minimum severity, quiet hours, a
weekly budget per channel, digest-only kinds, ack / snooze / mute. A deterministic **weekly summary** (last week against usual, top categories,
the next 7 days, alerts, budgets, savings) is built locally into the in-app feed. Reference: [docs/alerts.md](alerts.md).

```
uv run coach alerts check [--dry-run] [--no-send]    # evaluate, keep the events, send to the channels you enabled (--dry-run: nothing written or sent)
uv run coach alerts list | show ID | ack ID|--all | snooze ID|KIND --days N | restore ID|KIND | mute-kind KIND | unmute-kind KIND
uv run coach alerts channels                         # what is enabled and ready (secrets never shown)
uv run coach alerts test-channel ntfy --dry-run      # print EXACTLY what a message would be; a real send needs a terminal and a typed yes
uv run coach alerts digest [--save] [--send] [--dry-run]   # the weekly summary (local); --save puts it in the feed
```

## Web app (E5)

A local web app over everything above: dashboard, transactions (filters, inline category fixes with a preview, tags and
events), categories, budgets, subscriptions, loans and net worth, calendar, insights, the memory (questions, proposals,
forms, history), connections (sync now, reconnect) and the coach: a chat panel that streams answers with clickable evidence
(E6). Details, pages, the
security model and the API list: [docs/ui.md](ui.md).

```
cd web && pnpm install && pnpm build     # once, and after frontend changes (output: src/coach/api/static/)
uv run coach ui                          # http://127.0.0.1:8765/
```

* `coach ui` prints a **one-time login link** (and opens your browser on it); there is no other way in: a plain page load never
  gets a session. `coach ui --login-link` prints a fresh one. The token travels in the URL fragment, works once, for 2 minutes.
* It listens on `127.0.0.1` only and refuses any other host unless `[ui] allow_remote = true` with `remote_tls_ack = true`
  (an HTTPS proxy such as `tailscale serve` in front) and `allowed_hosts`. The session cookie (HttpOnly, SameSite=Strict, per
  port, 12 h, revocable; secret rotated every 30 days or with `coach ui --rotate-session-key`) is signed with a per-install
  secret; writes also need a CSRF token and are rate limited; the Host header is checked; the database cannot be written
  from the read path.
* Nothing is calculated in the browser and nothing leaves the machine: no CDN, font, analytics or telemetry.
* Every memory change goes through the memory store (validated, previewed, recorded as `ui` in the history). Accepting a
  coach proposal and reverting memory are **terminal-only** (`coach memory accept|revert`): the page shows the diff and the
  command, the API has no endpoint for them.
* Agents must not call this API (it is for the human at the keyboard).
* Dev mode with hot reload: `uv run coach ui --dev --no-browser` (prints the login link) and `cd web && pnpm dev` (http://localhost:5173).
* Try it on realistic data without risk: `uv run coach backup`, `uv run coach restore FILE --to /tmp/scratch`, then run
  `coach ui` with `COACH_DB`, `COACH_MEMORY_DIR`, `COACH_DATA_DIR`, `COACH_CONFIG_DIR` pointing at the copy.

## The LLM coach (E6)

**Numbers come from code, words come from the LLM.** The coach sees your data only through a local *finance MCP server*
(`uv run coach mcp serve`) whose tools return computed, REDACTED results (pseudonymised accounts and people, hashed
transaction refs, generalised merchants, third-party text marked `untrusted_text`, a final privacy assertion that fails
closed), and it can only *propose* memory changes. Full design, tools, privacy and prompt-injection model, backends and
costs: [docs/coach.md](coach.md).

```
uv run coach coach ask "Why was September high?"      # [coach] backend: claude-code | anthropic-api | ollama | openai-compatible
uv run coach coach digest --weekly --dry-run          # the exact prompt + the redacted tool outputs; no model is called
uv run coach coach digest --monthly                   # opt-in in the scheduler: [coach] schedule_weekly / schedule_monthly
uv run coach coach tools --sizes                      # the 28 tools and the size of each (redacted, privacy-checked) output
```

* **Claude Code in this repo**: `.mcp.json` declares the `finance` server (approve it with `/mcp`); `CLAUDE.md` tells Claude how
  to answer (use the tools, cite evidence refs, never compute numbers, propose memory changes only, no investment-product advice).
* **Web app**: the Ask the coach page and the Insights feed (the coach's digests and findings with evidence, dismiss / done /
  snooze). One question at a time, cancellable, with a timeout; usage logged in `llm_usage`.
* **Backends**: `claude-code` runs headless `claude -p` on your Claude subscription (personal, low-frequency use: for automation
  or sharing use an API key); `anthropic-api` uses your API key with prompt caching; `ollama` and `openai-compatible` (OpenRouter, Eden AI ...) need a model with tool support.
* **Proposals**: the coach's `memory_propose` (and `questions_propose`) creates a sealed proposal (source `coach-llm`); review it in
  the web app and accept it yourself in a terminal. A session that saw instruction-like text in your data marks its proposals so
  that each field needs its own confirmation.

## Coach skills (E7)

Nine analyses built on the same tools, each with a deterministic helper, new finance tools, a prompt (web quick prompt and CLI) and
a Claude Code skill: `monthly-review`, `explain-spike`, `subscription-audit`, `contract-check`, `find-cheaper` (web search, Claude
Code only), `mortgage-check`, `what-if`, `tax-helper`, `onboarding-interview`. Purpose, inputs, tools, safety and limits of each:
[docs/skills.md](skills.md).

```
uv run coach coach skills                                       # the skills and how to run them
uv run coach coach ask --skill monthly-review --month 2026-09   # a skill through the configured backend (--dry-run: no model)
uv run coach coach ask --skill what-if "what if we cancel the biggest software subscription?"
uv run coach coach ask --skill tax-helper --year 2026 --country FR
uv run coach coach tool tax_candidates --args '{"year": 2026}'  # one read-only tool exactly as the model sees it (redacted)
uv run coach contract check                                     # can my contracts be cancelled? (FR / IT rules; verify with your contract)
uv run coach onboarding status                                  # what the coach still does not know
uv run coach onboarding run                                     # interactive interview: every change previewed, written after a typed yes
```

Web search exists only in the interactive Claude Code skills (`find-cheaper`, `mortgage-check`), with generic non-personal queries
and dated sources; the web app and `coach coach ask` never search the web. The web app has a **Set up** page (the onboarding
checklist with previewed writes) and skill quick prompts on the Ask the coach page.

## Scheduler (launchd, or a loop)

```
uv run coach schedule install --dry-run   # print the plist, launchctl commands and preflight problems only
uv run coach schedule install             # writes ~/Library/LaunchAgents/com.ai-finance-coach.daily.plist and loads it
uv run coach schedule status | uninstall
uv run coach schedule loop [--run-now]    # the same job in a loop at [schedule] time, without launchd (Docker, Linux); a restart never runs the missed time
uv run coach schedule run                 # the job: sync (respects the daily limit) -> normalize -> classify run -> memory check -> analytics -> net-worth snapshot -> alerts + weekly summary -> security audit (weekly) -> evaluation (weekly)
uv run coach logs [--tail N] [--run ID]   # the structured log of the scheduled runs (E12)
```

`install` first checks that the job can work (DB exists and opens without `--insecure`, `db_key` resolvable, Enable Banking
configured; `--skip-preflight` to override). The job exits non-zero if any account sync fails. It runs daily at `[schedule] time`; stdout/stderr go to `<data_dir>/logs/schedule.{out,err}.log` and one summary line
per run to `<data_dir>/logs/schedule.log`; every run also writes JSON lines (run id, step names, durations, outcomes, counts: never a description, a name or an
exception text) to `<data_dir>/logs/runs.jsonl`, and all logs of the folder are rotated by size and age (`[logs]`): see "Quality" below. `--agents-dir` / `$COACH_LAUNCHAGENTS_DIR` override the LaunchAgents folder.

Keychain note: the background job reads `db_key` from the Keychain, and macOS ties that access to the Python binary
that runs it (`.venv/bin/python` -> the uv-managed interpreter). After a uv Python upgrade the Keychain may prompt
once (click "Always Allow") or the job may fail until you run `coach` interactively and approve it. `schedule install`
therefore requires `db_key` in the Keychain and Enable Banking settings in `config.toml`, not in your shell environment.

With `[coach] schedule_weekly = true` and / or `schedule_monthly = true` (both off by default) the job ends with a warn-only
`coach` step that writes the weekly digest / monthly review through the coach backend (skipped when nothing is new; never fails
the daily job): see [docs/coach.md](coach.md).

## Quality: fixtures, gold set, evaluations, usage, logs (E12)

```
uv run coach eval fixtures synth --bank fortuneo --out tests/fixtures/fortuneo.json   # invented descriptors of the SHAPE of the real ones (reads the local DB, writes no real token)
uv run coach eval gold bootstrap | sample | label | list | remove                      # the gold set: transactions whose category YOU confirmed
uv run coach eval classify [--json]                                                   # accuracy by transaction and by money, per source and category (offline)
uv run coach eval models --models haiku,sonnet[,ollama:<m>] [--sample N] --dry-run     # compare models on the gold merchants (a shadow run: no label is written)
uv run coach eval coach --offline | --run [--skill X] [--dry-run]                      # score the coach's answers, stored or from a curated question suite
uv run coach eval runs [--kind K] [--show ID]                                          # every run is stored: compare over time
uv run coach usage [--days 30] [--json]                                               # LLM calls, tokens, notional cost, duration per job / model / day
uv run coach logs [--tail N] [--run ID]                                               # the structured logs of the scheduled runs
```

The web app has a **Gold set** page (label transactions, see the scores) and an **AI usage** page; the Connections page shows the last scheduled run.
`eval models` and `eval coach --run` cost money (or notional quota) and send redacted data to the backend: they refuse without a terminal and a typed
confirmation, go through the egress gate, and `--dry-run` prints the exact payload size and an estimate first. Everything else is offline and local.
Details: [docs/quality.md](quality.md).

## Backups

```
uv run coach backup                                   # <backup dir>/coach-backup-YYYYmmdd-HHMMSS.tar.enc, keeps N newest
uv run coach restore FILE --to DIR [--force]          # never overwrites existing files without --force
```

`restore` only writes into the `--to` directory (`DIR/finance.db`, `DIR/memory/...`, files 0600); moving them back to
`data/` and `memory/` is a manual step, on purpose. The restored `finance.db` is still SQLCipher-encrypted, so
opening it also needs the `db_key` that was in use when the backup was taken (keep both keys).
The DB is snapshotted with the SQLite backup API (safe while a sync runs), the written archive is decrypted and
checked before old backups are pruned, and file names carry UTC timestamps. Symlinked files in `memory/` are stored
as regular files; symlinked directories and special files are skipped with a warning.

Archive = tar of the DB file + `memory/`, encrypted with AES-256-GCM; the key is derived from `backup_key`
with scrypt; salt and nonce are stored in the file header.

## Privacy, security & compliance (E11)

Where the data can go, in one registry: `coach privacy report` lists every outbound path (Enable Banking, the three LLM backends, the alert
channels, the MCP server, the interactive web-search skills) with its data, redaction and opt-in, and summarises the local **egress journal** (host,
size, purpose: never a payload). Every network-capable call site goes through `egress.allow(...)`; a test fails on a new unregistered one.
`[privacy] local_only = true` makes every LLM path ollama on this machine and disables web search and the external alert channels;
`offline = true` also disables the bank sync (file imports only). `coach security audit` checks keys, file permissions, encryption, plaintext
leftovers, `.gitignore`, secret-looking strings and what listens on the network (exit 1 on a critical; weekly and warn-only in
`schedule run`). Every LLM-written text is labelled AI-generated and checked for investment-product recommendations (banner + record).
`coach export` writes an encrypted archive of all your data; `coach wipe` (terminal only, typed phrase) deletes it. Details:
[docs/privacy.md](privacy.md) (data flows, inventory, local mode, retention, export / delete) and
[docs/security.md](security.md) (threat model, the agent permission rules, residual risks).

## Tests

`uv run pytest` (synthetic fixtures only; HTTP and the LLM are mocked; a fake keyring replaces the real Keychain). Parser tests also run over
`tests/fixtures/<bank>.json`: invented, real-shaped descriptors generated by `coach eval fixtures synth` (docs/quality.md); the repository test of
real-data tokens (`tests/test_egress_policy.py`) covers them.
`cd web && pnpm test` runs the frontend tests (vitest); `pnpm typecheck` checks the types. `pnpm build` writes the app into `src/coach/api/static/`
(NOT committed, it is in `.gitignore`: CI, the Dockerfile and the release procedure build it, and the wheel packages it; rebuild it after a UI change).

## Maintainer tools (E13-4)

```
uv run coach dev hygiene          # scan every publishable file (not excluded by .gitignore) with the structural rules and your LOCAL term file; never prints a value
uv run coach dev hygiene --build-terms   # derive that local file from your database and memory (names, places, merchants, exact amounts...); prints counts only
uv run coach dev hygiene --ci     # no local file (CI): the real-data rule is skipped and reported as skipped
uv run coach dev release-check    # licence, version, build, hygiene, docs, docker, CI, tests: exit 1 until everything holds
```

See [docs/release.md](release.md) for the checklist, and [CONTRIBUTING.md](https://github.com/mmornati/ai-finance-coach/blob/main/CONTRIBUTING.md) for the development workflow.
