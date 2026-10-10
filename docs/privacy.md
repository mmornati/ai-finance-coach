# Privacy: data flows, egress, local mode, retention, export and delete (E11)

The coach holds a household's whole financial life. This page says where that data can go (every outbound path, in one registry),
what is applied before it leaves, how to keep everything on this machine, how long things are kept, and how to take or erase all of it.
The threat model (who could get at the data and how) is in [security.md](security.md).

Commands: `coach privacy status` (the effective mode), `coach privacy report` (inventory + the last 30 days of the egress journal),
`coach security audit`, `coach export`, `coach wipe`.

## 1. One policy for every outbound call

`src/coach/egress.py` is the single place that knows every way data can leave this machine:

* **`INVENTORY`**: each outbound path (kind), its destination, the categories of data sent, the redaction applied, what opts it in and how to
  switch it off. `coach privacy report` prints it.
* **`egress.allow(kind, meta)`**: the check every network-capable call site makes right before it talks to the outside. It applies the
  `[privacy]` settings (`local_only`, `offline`, `web_enrich`), raises `EgressDenied` (a clear message naming the setting) when the path is
  refused, and writes one row to the local **egress journal**.
* **`tests/test_egress_coverage.py`** reads the source (AST) and fails when a function that uses `requests`, `urllib.request`, `smtplib`,
  `socket`, `subprocess` (including `claude`), `webbrowser`, `anthropic` or `uvicorn` neither calls `egress.allow` nor is listed, with a reason,
  in `egress.EXEMPT_FUNCTIONS`, or when a file with such a call site is missing from `egress.CALL_SITES`. A new unregistered call site breaks the
  build. A second test pins that only `coach wipe` can delete a Keychain item.

### The egress journal

Table `egress_journal` (migration 0019): `ts`, `kind`, destination `host`, `bytes`, `purpose`, `redaction` mode, `outcome` (allowed | denied),
`reason` (the policy code of a refusal), `web` (1 when the call let a model search the web). **Never a payload, a URL path or query, a name,
an amount or a token.** The size is the length of what was handed to the gate (an approximation: a multi-turn coach run journals every request).
Switch it off with `[privacy] egress_journal = false`; rows older than `egress_journal_days` (365) are deleted by `schedule run`. A row that cannot be
written (migration pending, database locked) waits in memory and is retried; a journal failure never stops a sync.

## 2. The egress inventory

| Kind | Destination | Data sent | Redaction | Opt-in | Disable |
|---|---|---|---|---|---|
| `enable_banking` | `api.enablebanking.com` | app id + a JWT signed with your private key (the key never leaves), consent / session ids, account ids, date ranges. Bank data comes BACK; nothing from your database goes out | none needed (no household data) | `coach connect`, `coach sync`, the scheduled job | `[privacy] offline = true`; do not connect; `coach wipe` can revoke the sessions |
| `llm.claude-code` | Anthropic, through the `claude` CLI (your subscription) | `classify run` / `compare` / `eval models`: per merchant the redacted descriptor and one raw bank example, both without the known towns, the number of payments, the direction, the average amount rounded to an order of magnitude (1-2-5 series), the payment types, up to five nearest already-labelled merchants (key, name, category); plus the category list and a few of your own labels as examples (`classify run --dry-run` prints the exact request). `classify enrich`: the same, and the model searches the web with the descriptor. `coach ask` / digests / skills: the question and the REDACTED, pseudonymised results of the finance tools. `memory doc extract --send`: a redacted document text | `item-redact` (IBAN, e-mail, phone, long digit runs, ids, titled people, household name tokens), person-like merchants never sent, finance tools pseudonymise accounts and people and generalise merchants, documents redacted | running those commands; the scheduled job runs `classify run` (digests are opt-in) | `local_only = true` + backend `ollama` |
| `llm.anthropic-api` | `api.anthropic.com` | as above (no web search) | as above | `[llm]` / `[coach] backend = "anthropic-api"` + the `anthropic_api_key` secret | `local_only = true`; another backend |
| `llm.ollama` | `[llm] ollama_url` (default `localhost`) | as above (no web search) | as above | `backend = "ollama"` | another backend; a non-loopback URL needs `ollama_allow_remote` and is refused in local mode |
| `alerts.ntfy` | your ntfy server | an alert title and one short line (`minimal`: no merchant, account, bank or name; the `kid_budget` kind of E14-6 is never sent, not even as a count) | alert guard (`alerts/messages.py`) | `[alerts.ntfy] enabled = true` (off by default) | `enabled = false`; `local_only` |
| `alerts.email` | your SMTP server | the same | alert guard | `[alerts.email] enabled = true` (off) | same |
| `alerts.telegram` | `api.telegram.org` | the same (the bot token is in the request path) | alert guard | `[alerts.telegram] enabled = true` (off) | same |
| `alerts.macos` | this Mac | the alert, shown on screen | n/a | `[alerts.macos]` / `[notify] macos` | set to false (stays allowed in local mode: nothing leaves) |
| `mcp.finance` | the model behind the MCP client (Anthropic when it is Claude Code) | redacted, pseudonymised tool results. Household members (E14) are `adult-N` / `kid-N` only (no name, alias, birth year, or label of a rule, budget or allocation); the children's money and who-pays tools hold amounts, dates, categories and hashed refs; a `member` filter takes a pseudonym | `analytics-pseudonym` + the guard; `[privacy] model_detail` (`coarse` default) | you start `claude` here and approve the server | do not approve it; `local_only` makes `coach mcp serve` refuse |
| `mcp.finance` (E15) | the same model | `rental_overview`: a rental property as `asset-N` and the kind "rental property", its account / loan as pseudonyms, hashed transaction refs, amounts, dates and the figures the owner declared; never the address, the property manager, the lender, the tenant, the notes or a merchant name; the scheme name is scrubbed and wrapped as untrusted text | the same redactor and final assertion | as `mcp.finance` | as `mcp.finance` |
| `skill.web_search` | the search engine behind Claude Code's WebSearch | generic, non-personal queries written by `find-cheaper` / `mortgage-check` | by rule (never a name, address, account or identifying amount) | you run those skills | `local_only` (the skills read `coach privacy status` and refuse) |

E12 evaluations use the same rows and the same gate (see [quality.md](quality.md)): `coach eval models` sends the same redacted merchant requests as `classify run` (journal purpose
`eval.models`) and `coach eval coach --run` asks the coach's own prompts (journal purposes `coach.ask` / `coach.skill`); both need a terminal and a typed confirmation, and `local_only` / `offline` refuse them
like any other path. `coach usage` and the AI usage page read `llm_usage` and the journal's host and size columns only. The scheduled run's structured log (`logs/runs.jsonl`) holds step names, statuses and numbers,
never a description, a name, an amount or an exception text, and is rotated by size and age. Parser fixtures (`tests/fixtures/`) are invented from the shapes of the data and checked against it at generation time.

Not outbound by this application (listed in `egress.CALL_SITES` with the reason): macOS `osascript`, `launchctl`, `git` for the memory history
(no remote is ever configured), the browser the user is sent to for the bank consent, the loopback callback server, the loopback web app.
`pip` / `uv` / `pnpm` talk to package registries only when YOU run them to install or build; they are dev tooling, not part of the running coach.

### `classify enrich` (web search with merchant names): audited

This is the one path where a model searches the web with something derived from your transactions. As of E11 it is:

* **opt-in**: refused unless `[privacy] web_enrich = true` (and never under `local_only` / `offline`); the refusal is journaled;
* **shops only**: `enrich_candidates` withholds everything that could be a person (titled people, person-like names, a given name next to
  another name-like word whatever the length, keys seen on transfers or direct debits, the household's own names); known towns (declared
  places, derived places, last words shared by several merchants, also multi-word and truncated ones) are stripped BEFORE the person check;
  previously `enrich` did not apply the person guard, `classify run` did (it now has the stricter check too);
* **no town in the query**: descriptors are searched without their town ("name France"); a descriptor that is only a town is withheld; the
  prompt itself is checked at run time against the household's names and places;
* **redacted**: every descriptor goes through the same redaction as `classify run`;
* **visible first**: `coach classify enrich --dry-run` prints the exact request, sends nothing and works even when enrichment is off.

`classify compare` (a second opinion from another model) now applies the same person guard.

## 3. Fully local mode (`[privacy] local_only`, `offline`)

```toml
[privacy]
local_only = true      # every LLM path is ollama on THIS machine; no web search; no external alert channel
# offline = true       # nothing leaves the machine at all (implies local_only): no bank sync, file imports only
[llm]
backend = "ollama"
[coach]
backend = "ollama"     # a model that supports tool calling; otherwise the coach refuses
```

`coach privacy status` prints the effective mode and what each path may do. Under `local_only`:

* a cloud backend (`claude-code`, `anthropic-api`) is refused where it is chosen (`get_backend`, the coach runtime, the web app's availability)
  AND at the egress gate of every call, so a forgotten call site cannot bypass it; the message says to set the backend to `ollama`;
* `ollama` must be on loopback: a remote `ollama_url` is refused even with `ollama_allow_remote = true`;
* `classify enrich` is disabled; the web-search skills refuse (they read `coach privacy status --json` first: `web_search_skills`);
* ntfy, e-mail and Telegram are "not ready" (reason shown in `coach alerts channels`); the macOS notification stays;
* `coach mcp serve` refuses (the client would be a cloud model);
* Enable Banking stays allowed: it is the data source. With `offline = true` it is refused too (`coach sync` fails with the reason; the scheduled
  job skips the sync and a refused classify step is "skipped", not "failed"); import bank files with `coach import`.

Under `local_only` the bank sync still resolves DNS for `api.enablebanking.com` and talks to it (expected: it is the data source); nothing else
resolves or connects. `offline` stops that too.

Reduced quality is expected: small local models classify and explain less well than a cloud model, and the coach needs tool-calling support.

## 4. What is kept, where and for how long

| Data | Where | How long |
|---|---|---|
| transactions, balances, accounts, merchants, labels, insights, decisions, alternatives, alerts, net worth | the encrypted database (`data/finance.db`, SQLCipher, key in the Keychain) | until you delete them (`coach wipe`) |
| household memory (`memory/`) and its change history (`.history.git`) and proposals (`.proposals`) | files, owner-only | until you delete them; history is permanent unless `memory purge-history` |
| who a transaction belongs to (`tx_person`, `tx_person_log`: member ids and who changed it), logins of the web app (`ui_users`: id, role, member id, preferences), the audit of web changes (`audit_log`: method, endpoint, status, login; never a payload) (E14) | the encrypted database | until you delete them (`coach users remove` deletes a login; its audit lines stay) |
| LLM usage (`llm_usage`: model, tokens, cost) | database | indefinite (no content) |
| egress journal (host, size, purpose) | database | `egress_journal_days` (365) |
| compliance flags (`compliance_events`: codes + 100-character snippets of flagged answers) | database | indefinite |
| encrypted backups | `backups/` | the newest `[backup] retention` (14) |
| pre-migration safety copies | next to the database (`*.pre-migrate-*.bak`, encrypted with the same key) | the newest 2 |
| logs, coach init log, web session key and tokens, TLS key | `data/` | logs are rotated by size and age (`coach logs`); the session key is replaced every `[ui] key_rotation_days` (30) |
| exports | `data/exports/` (or where you put them) | until you delete them |
| secrets | macOS Keychain (service `ai-finance-coach`) | until you delete them (`coach wipe` asks separately) |

At the providers: what `claude -p` / the Anthropic API / Enable Banking keep is governed by their terms, not by this application. The coach sends
redacted data only (section 2), and prefers not to send at all (`local_only`). What Enable Banking is and keeps: [enable-banking.md](enable-banking.md).

### Other people's data

The database holds the money of a whole household, and the descriptions of its transactions name third parties (the person who paid you, the
shop, the school). The owner of the installation is the one who decides to run it and is responsible for the others:

* **The other adults and the children** of the household who get a login (E14) see their own view of the data; a child's scope is enforced on the
  server. Tell them, in plain words, that their transactions are stored here, that redacted and pseudonymised results of the analytics (figures,
  dates, categories, hashed references, never their name) reach the model backend you chose (Anthropic, OpenRouter, Eden AI, a local Ollama), and
  which privacy mode you run. Nothing in this app does that for you.
* **Counterparties** (people who transferred money to or from the household) are not sent to a model: person-like merchants are withheld from
  classification, the finance tools generalise them, and alerts never carry a name. Their names remain in the local database and in your memory
  files, which is where they would be in any bank statement.
* The household exemption of the GDPR covers purely personal use. If you run the app for someone outside your household, you become a data
  controller for them: the exemption no longer applies, and you owe them the information above in writing.

## 5. Export (`coach export`)

```
uv run coach export [--out FILE] [--format zip]        # encrypted archive (default <data_dir>/exports/coach-export-<time>.zip.enc, 0600)
uv run coach export --decrypt FILE --to EMPTY_DIR      # read it back
uv run coach export --plain                            # UNENCRYPTED zip: only on a terminal, after typing "EXPORT UNENCRYPTED"
```

The archive (a zip, encrypted with the backup crypto: AES-256-GCM, scrypt-derived key = the `backup_key` secret, magic `AFCEX1` so it is never
mistaken for a backup) holds `transactions.csv` (spreadsheet-safe: cells starting with `= + - @` are escaped), `data/<table>.json` for every
table of the database except bank session credentials (`sessions`, `pending_auth`, consent alerts, batch bookkeeping), `categories.json` (the
taxonomy), the `memory/` files including documents (not `.history.git` or `.proposals`), a `manifest.json` with row counts, and a README. It is
verified (decrypted again) after it is written; nothing is ever overwritten. `--decrypt` writes your data in clear, so like `--plain` it needs a
terminal and the typed phrase, and refuses a target inside the project folder, `data_dir`, the memory folder or the backup folder.

## 6. Delete everything (`coach wipe`)

```
uv run coach wipe --dry-run     # list exactly what would be deleted; nothing is deleted, nothing is sent; no terminal needed
uv run coach wipe               # interactive: terminal only, no --yes, typed phrase "DELETE MY DATA"
```

It asks, separately: revoke the Enable Banking sessions (`DELETE /sessions/{id}`; the only network call, made only after you said yes, through the
egress gate); delete the encrypted backups; delete the Keychain secrets. Unless `--no-export`, it first writes and verifies an encrypted export
(by default in your home folder, never inside what is deleted; no `backup_key`, no deletion), and any failure stops it before anything is deleted.
Folders must look like ones coach created (a known file such as `finance.db` or `household.yaml` inside; backups named `coach-backup-*`), and
the home folder, its parents and the well-known user folders (Documents, Desktop, Downloads, Library ...) are refused. If a safety export was
written and you also ask to delete the Keychain secrets, `backup_key` is KEPT (the export cannot be read without it) unless you answer a further
question, in which case it is shown once on the terminal. Backups and exports that live inside `data_dir` survive unless you chose to delete the backups.
Then it deletes the database files (and their `-wal`, safety copies), `memory/` (the repository's template files `README.md` / `_template.yaml`
are kept), the whole `data_dir` (keys, TLS, logs, exports) and, if asked, the backups and the Keychain items. This is the only code path that
deletes Keychain items. Files are unlinked, not shredded: rely on FileVault, and delete the Keychain secrets to make the encrypted data
unrecoverable. Deleting the secrets also makes every export or backup made with those keys unreadable: keep the keys of what you keep.

## 7. Compliance of what the coach says (E11-5)

* **AI-generated label** (EU AI Act transparency): every LLM-written text carries it in the web app (the coach answer, each coach insight), on the
  CLI (`coach coach ask` prints it before and after the answer; `coach coach digest` says so) and in storage (`insights.ai_generated`). Deterministic
  summaries (the weekly alerts digest, loan scenarios) are not labelled AI because no model wrote them.
* **Investment-product check**: after generation `coach.compliance` flags an ISIN (check-digit validated), a named fund / ETF / issuer / ticker /
  crypto asset, or "buy / invest in / place your money in ..." in FR, IT and EN. A flagged text gets a visible banner ("general information
  only, not personalised investment advice (FR: AMF / CIF; IT: Consob)") and is recorded (`insights.compliance`, `compliance_events`). The check is
  conservative and never rewrites or blocks the answer: expect an occasional false positive (a banner), and treat a miss as possible (it is a regex
  check, not a legal review).
* **One module of disclaimers**: `coach/disclaimers.py` (FR / IT / EN) is used by the skills' tools (contracts, loans, savings, tax), the
  cancellation letters and the coach's system prompt.
* The coach's system prompt forbids naming products, ISINs and tickers and claiming to be a licensed adviser, and ends savings / investing talk with the
  disclaimer in the language of the answer. This is a legal-hygiene feature, not legal advice.
