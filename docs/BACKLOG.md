# AI Finance Coach: epics and stories

Self-hosted personal finance coach for a European household: bank data collected without an LLM,
categorized cheaply, stored locally, shown in a usable web app, and analysed on demand by an LLM
that knows the household's context through a plain-file **memory**.

Status legend: ✅ done in prototype · 🟡 partly done · ⬜ to do.
Phases: **MVP** (usable by the author daily) · **P2** (coach that saves money) · **P3** (polish, OSS release).

---

## Target architecture

```
                ┌──────────── cron / scheduler (no LLM) ────────────┐
Enable Banking ─┤ ingest: sync, dedup, pending, balances, CSV import │──► SQLite (encrypted)
                └───────────────────────────────────────────────────┘        │
                                                                              ▼
memory/ (md + yaml) ──► classify: parse → memory → rules → user memory → LLM merchant labels
        ▲                                                                     │
        │                         analytics (deterministic SQL/Python):       ▼
        │                averages w/o one-offs, recurring, forecasts, budgets, net worth
        │                                                                     │
        │      ┌──────────────── finance MCP server (read tools + gated memory writes) ─────┐
        │      │                                                                            │
        └──────┤  Coach runtime:  claude-code (claude -p + skills, personal Max)            │
               │                  anthropic-api (Agent SDK, API key)  · local (Ollama)      │
               └──────────────┬─────────────────────────────────────────────────────────────┘
                              ▼
          Web app (API + SPA): dashboard · transactions · budgets · subscriptions · loans
                               · calendar · insights feed · "Ask the coach" · memory editor
```

Principles

- **LLM never in the ingestion path.** Sync and analytics are deterministic and free; the LLM labels
  unknown merchants and does analysis/coaching on request or on a low-frequency schedule.
- **Numbers come from code, words come from the LLM.** The coach calls tools that return computed
  figures; it never does arithmetic over raw rows.
- **Memory is files.** `memory/` is human-readable and editable by the user, the UI and the coach.
- **Privacy by default.** Data stays local; people's names and IBANs never go to a cloud LLM; a local
  model option exists for categorization.

---

## Epic overview

| # | Epic | Phase | Status |
|---|---|---|---|
| E0 | Foundations (repo, config, secrets, storage) | MVP | ✅ |
| E1 | Bank ingestion (Enable Banking + imports) | MVP | ✅ (4 banks live; PDF import later) |
| E2 | Normalization & categorization | MVP | ✅ (CLI; UI parts in E5) |
| E3 | Household memory | MVP | ✅ (CLI; UI editing in E5) |
| E4 | Analytics engine (deterministic) | MVP→P2 | ✅ (CLI + API registry; UI in E5) |
| E5 | Web app | MVP→P2 | ✅ (coach chat/insights wired in E6) |
| E6 | LLM coach runtime & "Ask the coach" | MVP→P2 | ✅ (claude-code backend exercised interactively; API / ollama mock-tested) |
| E7 | Coach skills (analyses) | P2 | ✅ (monthly-review exercised; find-cheaper live web path untested) |
| E8 | Subscriptions & contracts optimizer | P2 | ✅ (contract proposal flow exercised; letters not lawyer-reviewed; find-cheaper live untested) |
| E9 | Loans, mortgage & net worth | P2 | ✅ (schedules need loan facts from the user) |
| E10 | Alerts & digests | P2 | ✅ (external channels off by default; first real send by the user) |
| E11 | Privacy, security & compliance | MVP→P3 | ✅ (`coach security audit --fix-permissions` tightens file permissions) |
| E12 | Quality: tests, evals, observability | MVP→P3 | ✅ (method v2; label a gold sample for LLM accuracy) |
| E13 | Packaging & open-source release | P3 | 🟡 (engineering reviewed and closed; E13-1/-2/-4 built and tested with fakes; blocked on the owner's licence decision; Docker image not built, wizard and Enable Banking guide not run live; E13-3 moved to E14) |
| E14 | Household & people (multi-person finances) | MVP→P2 | 🟡 (reviewed and closed; real database not yet migrated to 0022; E14-1..9 built and tested with synthetic data: members, owners, attribution, person views, the children's money, kid budgets, cross-bank transfers, per-person logins with a server-enforced child scope, who pays what; [household.md](household.md). Open: first use on the real household, a real browser session per login, remote use of a child login) |
| E15 | Rental property (tax-incentive scheme) | P2 | 🟡 (E15-1..5 built and tested with synthetic data, and the reads checked on a scratch restore of the real data (aggregates only); reviewed and closed (incl. the 9+3 Pinel reduction split), no migration; [rental.md](rental.md). Open: the real property's facts (scheme dates, cap, limits, rates) are the owner's to record, the real taxonomy copy needs `coach taxonomy merge-package`, the page was verified with vitest only, never in a browser) |

---

## E0: Foundations

Goal: one coherent project instead of two prototype scripts.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E0-1 | As a developer I want a single Python package (`coach/`) with `ingest`, `classify`, `analytics`, `api` modules and one CLI (`coach sync`, `coach classify`…) | `uv run coach --help` lists all commands; prototype scripts removed | MVP | ✅ |
| E0-2 | As a user I configure everything in one place | `config.toml` (non-secret) + secrets in macOS Keychain / env; `.env.example` documented | MVP | ✅ |
| E0-3 | As a developer I want versioned DB migrations | Alembic or numbered SQL migrations; prototype schema migrated without data loss | MVP | ✅ |
| E0-4 | As a user I want the DB encrypted at rest | SQLCipher (key in Keychain) or encrypted volume; app refuses to start with plaintext DB unless `--insecure` | MVP | ✅ |
| E0-5 | As a user I want a scheduler without extra infra | `launchd` plist / cron entry generated by `coach schedule install`; logs to `data/logs/` | MVP | ✅ |
| E0-6 | As a user I want backups | `coach backup` writes an encrypted, dated archive of DB + `memory/`; restore tested | P2 | ✅ |

## E1: Bank ingestion

Goal: all accounts kept up to date daily, at zero cost, without duplicates.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E1-1 | As a user I link a bank through Enable Banking (restricted mode, my own app) | `connect` + `finish` flow; state verified; session + accounts stored | MVP | ✅ |
| E1-2 | As a user I get the longest history at first link | first sync uses `strategy=longest`: the longest history the bank allows (often 90 days to 2 years) | MVP | ✅ |
| E1-3 | As a user incremental syncs never duplicate | dedup on `entry_reference`, fallback fingerprint; re-sync adds 0 | MVP | ✅ |
| E1-4 | As a user pending transactions are shown but don't pollute history | pending table replaced each sync; booked version replaces pending | MVP | ✅ |
| E1-5 | As a user balances are snapshotted each sync | balance history table → net-worth chart | MVP | ✅ |
| E1-6 | As a user the sync respects bank limits | ≤4 unattended calls/account/day; 429 handled, retried next window | MVP | ✅ |
| E1-7 | As a user I'm warned before a consent expires and can renew in one click | consent expiry tracked; alert at D-14/D-3; UI "Reconnect" button runs the auth flow with a local callback page | MVP | ✅ |
| E1-8 | As a user the redirect works without copy-pasting URLs | local HTTPS callback (`https://localhost:8443/callback`, self-signed or mkcert) completes the session automatically | MVP | ✅ |
| E1-9 | As a user I connect several banks (IT + FR) | per-bank sessions; one consent owner per bank (no clash with BankMCP); tested with 2nd bank | MVP | 🟡 |
| E1-10 | As a user I import what PSD2 doesn't give (credit cards, closed accounts, old history) | CSV / OFX / CAMT.053 / PDF-statement import with column mapping saved per source; same dedup | P2 | 🟡 |
| E1-11 | As a user I see connector health | page: last sync per account, errors, calls left today, consent days left | MVP | ✅ |
| E1-12 | As a user transfers between my own accounts are matched | pairs (−X on A, +X on B within 3 days) linked and excluded from spend/income | P2 | ✅ (across the household's banks, a wider window and member names as evidence: E14-7) |

Notes on E1-7..E1-12 (CLI delivered; the UI parts belong to E5-14):
E1-7/E1-11 are exposed as `coach consents` / `coach health` and as functions for the future API (the "Reconnect"
button and health page are E5-14). E1-9 stays 🟡 (also: ambiguous reconnect matches need a human, `coach accounts merge`): multi-bank is tested with mocked banks only, no second real bank
has been connected yet. E1-10 stays 🟡: CSV/OFX/CAMT.053 are done, PDF statements are a follow-up and the shipped CSV
profiles (generic FR, Caisse d'Epargne, Revolut) are to be verified with real exports.

## E2: Normalization & categorization

Goal: ≥95% of transactions (and ≥98% of money) categorized; user corrects in seconds and is never
asked twice.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E2-1 | As a user bank descriptors are cleaned into type, date, merchant, FX | Fortuneo parser (CARTE, ANN CARTE, VIR, VIR INST WERO, RET DAB, PRIME) | MVP | ✅ |
| E2-2 | As a developer I add a parser per bank via a plugin interface | `parsers/<bank>.py` + fixtures; generic fallback parser; IT bank parser (POS, PAGAMENTO, BONIFICO, ADDEBITO SDD) | MVP | ✅ |
| E2-3 | As a user categories follow a clear 2-level taxonomy | `taxonomy.yaml` (incl. debt.bnpl, housing.renovation); UI to rename/add leaves | MVP | ✅ |
| E2-4 | As a user deterministic things never hit the LLM | type rules (ATM, own transfers, person transfers) + regex rules (subscriptions, BNPL, utilities) | MVP | ✅ |
| E2-5 | As a user unknown merchants are labelled by an LLM, per merchant not per transaction | batches of 50, JSON schema, closed category list, confidence, recurring hint | MVP | ✅ |
| E2-6 | As a user the LLM backend is pluggable | `claude-code`, `anthropic-api` (Haiku/Sonnet + prompt caching + Batch API), `ollama` (local); chosen in config | MVP | 🟡 |
| E2-7 | As a user costly unknown merchants are researched on the web | `enrich` with evidence note; only merchant name + city sent | MVP | ✅ |
| E2-8 | As a user my corrections are permanent and teach the model | `correct` (key or regex); user labels win; used as few-shot examples | MVP | ✅ |
| E2-9 | As a user I review a queue ordered by money at stake | review queue in UI + `review-categories` skill; excludes rule/memory-decided merchants | MVP | ✅ (CLI + the web Review page) |
| E2-10 | As a user memory annotations override everything | `memory/categorization.yaml` matches (merchant/description/date/amount/tx) → category, tags, event | MVP | ✅ |
| E2-11 | As a user similar past transactions guide new labels | kNN on embeddings (local model) of merchant keys, used as examples / auto-label above threshold | P2 | ✅ |
| E2-12 | As a user merchant variants are grouped | canonical merchant entity (e.g. all MacBurger's cities); aliases editable | P2 | ✅ |
| E2-13 | As a user I can split a transaction | one payment → several categories (e.g. supermarket: groceries + household) | P2 | ✅ |
| E2-14 | As a user refunds net against the original category | card refunds and merchant refunds (EDF, airlines) land in the spending category | MVP | ✅ |

Notes on E2 (CLI delivered; UI parts belong to E5): E2-2 ships parsers for Fortuneo, Caisse d'Epargne, CIC, Revolut and a
generic Italian bank (synthetic samples only: verify with a real Italian export when one is connected). E2-6 stays 🟡:
the three backends are covered by mocked tests, none of `anthropic-api` / `ollama` has run on real data yet, and web
enrichment (`classify enrich`) remains `claude-code` only. E2-9 was 🟡 until the review UI (now the web Review page): the CLI queue has
`--json` and `--accept`. E2-11 uses character n-gram TF-IDF over merchant keys (no embedding model) as agreed.

## E3: Household memory

Goal: give the LLM (and the analytics) everything the bank can't tell.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E3-1 | As a user my context lives in readable files | `memory/` with README, profile, preferences, events, categorization, liabilities, contracts, open-questions | MVP | ✅ |
| E3-2 | As a user I explain a transaction once and it's treated correctly forever | e.g. 2025 house renovation: tagged `one_off`/`capital`, linked to event, excluded from averages | MVP | ✅ |
| E3-3 | As a user the coach asks me what it can't infer | `open-questions.md` maintained by skills; UI "Questions for you" card; answers written back | MVP | ✅ (CLI + generation + the web inbox) |
| E3-4 | As a user I edit memory in the UI | forms for events, loans, contracts; YAML validated against schema; git-style history of changes | P2 | ✅ (store, schemas, history, proposals, CLI, web forms) |
| E3-5 | As a user I attach documents (contracts, loan offers, statements) | files stored locally under `memory/documents/`; LLM extracts fields into the YAML with my confirmation | P2 | ✅ (CLI; text PDFs only, no OCR; UI in E5) |
| E3-6 | As a user memory is validated | `coach memory check`: schema errors, annotations that match 0 or too many tx, stale facts (e.g. outstanding capital older than 6 months) | MVP | ✅ |
| E3-8 | As a user I record savings/investments the app can't sync (savings accounts, an employee savings plan, the home) | `memory/assets.yaml` with value + `as_of`; stale-value reminder (>3 months); feeds net worth (E9-4) and "can we afford" questions | MVP | ✅ (schema, reminders, `manual_totals()`; net worth UI in E9) |
| E3-7 | As a user I see why a transaction has its category | "explain" shows source (memory id / rule / user / llm + confidence + evidence) | MVP | ✅ (`coach explain` and the transaction panel) |

## E4: Analytics engine (deterministic)

Goal: every number the UI or the coach shows is computed by code, reproducibly.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E4-1 | As a user I see monthly spending and averages without one-offs | averages/trends ignore `one_off`/`exclude_from_averages`; both figures available | MVP | ✅ |
| E4-2 | As a user I see income vs spending and savings rate per month | transfers excluded; internal transfers matched (E1-12) | MVP | ✅ |
| E4-3 | As a user recurring payments are detected automatically | cadence (weekly, monthly ±5d, bimonthly, quarterly, yearly ±15d), amount tolerance ±15%, ≥3 occurrences; next expected date | MVP | ✅ |
| E4-4 | As a user I'm told when a recurring price changes | price increase/decrease detection vs previous occurrences | P2 | ✅ |
| E4-5 | As a user I see unusual spending | per-category z-score / IQR vs own history; duplicate charges; new merchant with large amount | P2 | ✅ |
| E4-6 | As a user I see a cash-flow forecast | 30/60/90-day projected balance from recurring items + average variable spend + loan schedules | P2 | ✅ |
| E4-7 | As a user I set budgets per category and see progress | monthly envelopes, rollover option, suggested budgets from averages | MVP | ✅ |
| E4-8 | As a user I see an upcoming-payments calendar | recurring + loans + contract renewals + consent expiries | P2 | ✅ |
| E4-9 | As a user I track goals | target amount/date, linked account or savings flow, projected completion | P2 | ✅ |
| E4-10 | As a user I see a year in review | yearly totals incl. one-offs, by event, top merchants, biggest changes vs last year | P3 | ✅ |

Notes on E4 (delivered as `coach.analytics` + CLI; reference: `docs/analytics.md`): the coverage model is the base of every
average (a category is averaged over the months fully covered by the accounts that carry it; `classify report` uses it,
`--legacy` shows the old division by 12). Results are typed dataclasses with `to_dict()` (money = cents inside, two-decimal
strings in JSON; every result has `coverage` and `evidence`); `analytics.api.registry()` is the list of read-only tools E6-1
will expose. Budgets and goals live in `memory/budgets.yaml` / `goals.yaml` (written through the memory store, preview first;
the coach only ever runs them with `--propose`, the user applies the proposal); recurring series and anomalies are cached in tables (migration 0011). The UI parts (budget page,
subscriptions page, calendar view, insights feed) belong to E5; amortization schedules to E9-3 (done: the forecast uses a loan's
schedule when it is computable, its declared `monthly_payment` otherwise).

## E5: Web app

Goal: a simple, fast, good-looking local web app; works on phone via VPN/Tailscale.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E5-1 | As a developer I have an API layer | FastAPI (or similar) over the analytics/memory modules; OpenAPI docs; localhost only by default | MVP | ✅ (`coach ui`, /api/v1, docs/ui.md) |
| E5-2 | As a user I see a dashboard | balance, this month vs average, top categories, budget status, upcoming payments, latest insights, open questions | MVP | ✅ |
| E5-3 | As a user I browse and search transactions | filters (date, account, category, merchant, amount, tags, source), full-text search, infinite scroll | MVP | ✅ |
| E5-4 | As a user I fix a category inline | change category → choose "this transaction only" / "this merchant" / "rule"; teaches memory | MVP | ✅ |
| E5-5 | As a user I tag and annotate transactions | add tags (`one_off`, `reimbursable`…), note, event; written to `memory/categorization.yaml` | MVP | ✅ |
| E5-6 | As a user I see category drill-downs | category page: monthly chart, merchants, average, trend, one-offs shown separately | MVP | ✅ |
| E5-7 | As a user I manage budgets | budget page with progress bars and suggested amounts | MVP | ✅ |
| E5-8 | As a user I see my subscriptions | list with cost/month & /year, next charge, price changes, contract status, keep/cancel decision | P2 | ✅ |
| E5-9 | As a user I see loans and net worth | loan cards (remaining capital, end date, rate), net-worth chart (accounts + assets − liabilities) | P2 | ✅ (loan cards with the schedule, assets, net worth with unknowns, history chart: E9) |
| E5-10 | As a user I see a calendar | month view of upcoming payments and renewals | P2 | ✅ |
| E5-11 | As a user I read the coach's insights | insights feed (cards with evidence links to transactions, dismiss / done / snooze) | MVP | ✅ (the coach's insights come from E6-7; feed tested with synthetic rows) |
| E5-12 | As a user I ask the coach a question | chat panel, streaming answers, cited transactions clickable, quick prompts ("Why was September high?") | MVP | 🟡 (live through E6-3; verified with fake backends only, first real answer pending) |
| E5-13 | As a user I edit memory | events, loans, contracts, preferences forms; open questions inbox | P2 | 🟡 (questions, proposals, forms, history; no preferences / documents upload) |
| E5-14 | As a user I manage connections | connector health (E1-11), reconnect button, sync now (respecting limits) | MVP | ✅ (mocked banks only) |
| E5-15 | As a user the app is pleasant on mobile and in dark mode | responsive, dark/light, PWA installable | P2 | 🟡 (responsive, dark / light, PWA manifest + offline shell; install not verified) |

## E6: LLM coach runtime & "Ask the coach"

Goal: one way to call the LLM from CLI, Claude Code and the UI, with the same tools and memory.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E6-1 | As a developer the coach gets data through tools, not raw dumps | local **finance MCP server**: `summary(period)`, `transactions(filter)`, `category_trend`, `recurring`, `budgets`, `loans`, `forecast`, `memory_read`, `memory_propose_change` | MVP | ✅ (`coach mcp serve`, 18 tools: docs/coach.md) |
| E6-2 | As a user I ask questions from Claude Code in this repo | project `.mcp.json` + skills; answers cite tx ids; works with Max subscription (interactive) | MVP | 🟡 (`.mcp.json`, `CLAUDE.md`, skills done; not yet exercised in a real Claude Code session) |
| E6-3 | As a user I ask from the web app | API endpoint starts a coach job (`claude -p` with skills + MCP, or Agent SDK with API key); streamed to UI; stored in `insights` | MVP | 🟡 (job, SSE, cancel, timeouts, usage, evidence links done and tested with a fake `claude` + the real MCP server; the first real supervised question is pending) |
| E6-4 | As a user I choose the backend | `claude-code` (personal Max, low frequency) · `anthropic-api` (API key, needed for automation at scale / sharing) · `ollama` (local, tool-calling models only) | MVP | 🟡 (`[coach]` config + validation, 3 backends, `coach config show`, policy note in docs/coach.md done; real backend calls pending the first supervised run) |
| E6-5 | As a user the coach can update memory only with my approval | memory writes are proposals (diff) accepted in UI/chat; never silent | MVP | ✅ (proposals only, enforced at the tool layer; UI shows them with the CLI accept command; accepting stays terminal-only) |
| E6-6 | As a user scheduled analyses run rarely and cheaply | weekly digest + monthly review via scheduler; skipped if no new data; usage logged | P2 | 🟡 (`coach coach digest`, opt-in `schedule run` step, `--dry-run` done and tested with fakes; first real run pending) |
| E6-7 | As a user every insight is traceable | insight = text + structured findings + evidence tx ids + skill + model + date | MVP | ✅ (`insights` table, `add_insight` with evidence and number checks, feed) |
| E6-8 | As a user prompts are protected against injected text | transaction descriptions passed as data fields; tools are read-only except gated memory proposals | MVP | ✅ (untrusted_text wrapping, suspicious sessions, per-field confirmation) |

## E7: Coach skills (analyses)

Each skill = `SKILL.md` + tool calls + output schema; runnable from Claude Code, UI quick-prompt or schedule.

| ID | Skill | What it produces | Phase | Status |
|---|---|---|---|---|
| E7-1 | `categorize-transactions` | sync → normalize → label → enrich → report | MVP | ✅ |
| E7-2 | `review-categories` | guided review; writes corrections + memory annotations | MVP | ✅ |
| E7-3 | `monthly-review` | month vs average (one-offs excluded), biggest movers with reasons, budget status, 3 concrete actions | MVP | ✅ (`monthly_review` tool, PromptSpec, web quick prompt, Claude Code skill: docs/skills.md) |
| E7-4 | `explain-spike` | "why was X high?" decomposition by merchant/transactions | MVP | ✅ (`explain_spike`) |
| E7-5 | `subscription-audit` | all recurring costs, yearly total, unused/duplicate candidates, questions on usage | P2 | ✅ (`subscription_audit`, `questions_propose`) |
| E7-6 | `contract-check` | reads a contract (PDF) → fills `contracts/*.yaml`, cancellability (Hamon/Chatel/Bersani), notice dates | P2 | ✅ (`cancellability` rules table FR / IT, `coach contract check`, skill around `doc add/extract`) |
| E7-7 | `find-cheaper` | web search for alternatives (official comparators first, dated prices, sources), savings estimate | P2 | 🟡 (`savings_estimate` tool + interactive skill; the web-search path is a text skill, not exercised live) |
| E7-8 | `mortgage-check` | rate vs current market, renegotiation / surroga (IT) / rachat de crédit (FR) and borrower-insurance delegation (Lemoine) estimate; no product advice | P2 | ✅ (`mortgage_check`: amortization, IRA cap, surroga, Lemoine; degrades to "what I need" + question proposals) |
| E7-9 | `what-if` | scenario: cancel X, change budget Y, prepay loan Z → impact on forecast | P2 | ✅ (`what_if`, strict schema) |
| E7-10 | `tax-helper` | flag deductible candidates (FR: dons, emploi à domicile, garde d'enfants; IT 730: spese mediche, interessi mutuo, ristrutturazioni) for the year; no filing | P3 | ✅ (`tax_candidates` FR / IT) |
| E7-11 | `onboarding-interview` | first-run conversation filling profile, preferences, loans, contracts, open questions | MVP | ✅ (`onboarding_status`, Claude Code skill, `coach onboarding`, Set up page) |

## E8: Subscriptions & contracts optimizer

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E8-1 | As a user every recurring cost is listed with its yearly cost | from E4-3 + contract files; grouped (streaming, software, telecom, insurance, energy) | P2 | ✅ (`coach subs list`, `GET /subs/inventory`, Subscriptions page; `draft-contracts` bootstraps the contract files: proposals / previewed writes) |
| E8-2 | As a user I record whether I use each subscription | usage field (asked by coach); "unused for 60 days" reminders where measurable | P2 | ✅ (`usage{frequency,last_used,note}`; reminders only from the user's own record or "never" while still paid; nothing inferred for software / streaming without a record) |
| E8-3 | As a user I know if/when/how I can cancel | contract terms + legal rules file (FR: Hamon, Chatel, résiliation 3 clics; IT: Bersani) with sources | P2 | ✅ (the E7-6 rules engine on every row and contract page, calendar deadlines from it; general rules, verify with your contract) |
| E8-4 | As a user I get cheaper alternatives with sources | `find-cheaper` results stored with date and link; never presented as current if older than 30 days | P2 | 🟡 (`alternatives` table, `alternatives_record`, CLI, UI table done and tested; the find-cheaper web search path itself is still not exercised live) |
| E8-5 | As a user I get a ready cancellation letter/email | template filled from contract data; I send it myself | P3 | 🟡 (FR / IT / EN, LRAR / e-mail / online, local templates with snapshot tests; text only - no PDF writer in the dependencies; the wording cites only the rules table and has not been read by a lawyer) |
| E8-6 | As a user I track savings achieved | "cancelled/renegotiated" decisions with before/after monthly cost; cumulative savings counter | P3 | ✅ (`decisions` table, verification against the recurring series, dashboard card, `savings_tracker`; the coach only proposes) |

## E9: Loans, mortgage & net worth

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E9-1 | As a user I record my mortgage (mutuo) and car loan / LOA | `liabilities/*.yaml` from template or UI form or contract extraction | P2 | ✅ (complete schema for every kind: principal, rate, TAEG, dates, term, payment day, insurance flat / % initial / % outstanding, deferral, variable-rate index / margin / cap stored only, LOA rent / first payment / residual / mileage / excess fee / odometer; `coach loans add|edit|odometer` previewed with a typed yes, the UI form, extraction fields extended; `infer_loan` solves the missing term from the observed instalments with Newton for the rate, marked `inferred` with a confidence and only ever proposed. Contract extraction of the new fields is tested with a fake backend only) |
| E9-2 | As a user loan payments in the bank data are linked to the loan | `payment_match` regex → category + loan id; missed/changed payment alert | P2 | ✅ (`payment_match` + `amount_match` linking, per-loan payments list, alerts: missed payment after a grace period (only when the account's data are complete), amount changed, extra payment / possible prepayment, wrong account; insight cards + calendar entries from the schedule) |
| E9-3 | As a user I see the amortization schedule | computed from principal/rate/term; remaining capital today; interest paid per year | P2 | ✅ (`loans/schedule.py`: deferral, insurance, first due date, payment day; remaining capital, interest and insurance by calendar year, total cost; variable rate approximated and flagged; not computable = the exact missing fields + a question; feeds the forecast / calendar (exact dates and amounts), the savings rate incl. principal, `mortgage_check`, `what_if`) |
| E9-4 | As a user I see net worth | accounts (balances) + assets (house value, car) − liabilities; monthly history | P2 | ✅ (categories and owners, unknowns listed and never counted, stale flags; `net_worth_history` (migration 0016): a snapshot at each sync / scheduled run + back-fill of past months from balances and transactions, schedules and each asset from its own date; history chart, `coach networth`, read-only MCP `net_worth`. The page was verified with vitest only, not in a browser) |
| E9-5 | As a user I understand early repayment / renegotiation impact | scenario calculator incl. penalties (IRA / penale) and insurance | P3 | ✅ (`loans/scenario.py` on the real schedule: reduce term vs reduce instalment, interest and insurance saved, FR IRA / IT no penalty / contract amount, break-even; renegotiation / rachat / surroga and insurance delegation; CLI `coach loans scenario`, loan page, stored as an insight on request) |
| E9-6 | As a user LOA end-of-contract is anticipated | reminders for mileage, residual value decision 6 months before end | P3 | ✅ (calendar entry + insight card from 6 months before the end, buy-or-return with the user's own market value, odometer readings → projected km, excess cost, return checklist, questions for a missing end date / reading) |

| E10-1 | As a user I get notified without opening the app | channel: email / ntfy / Telegram / macOS notification (configurable) | P2 | ✅ (in-app feed always on; macOS (argv-safe), ntfy (https only, token in the secrets store), e-mail (SMTP, STARTTLS or SSL required, password in the secrets store), Telegram (fixed https API host, bot token in the secrets store); every external channel OFF by default and enabled only in `config.toml`; external messages are minimal and non-identifying and pass the finance tools' privacy guard, `external_detail = "summary"` adds a kind and a rounded amount; `coach alerts test-channel NAME --dry-run` prints the exact message, a real send needs an enabled channel, a terminal and a typed yes; sentinel tests in both modes) |
| E10-2 | As a user I'm alerted on important events | consent expiring, sync failing, unusual charge, price increase, low forecast balance, budget exceeded | P2 | ✅ (`alerts` package: signals from the consent lifecycle, the sync log and the insight cards (anomalies, confirmed price rises, forecast, budgets, loans, LOA, unused subscriptions, notice deadlines, contradicted decisions) -> `alert_events` (migration 0017) with stable ids, dedupe, escalation only on a severity increase, resolve / reopen; the warn-only last step of `schedule run` and `coach alerts check [--dry-run]`) |
| E10-3 | As a user I receive a weekly digest | deterministic numbers + short coach commentary; link to app | P2 | ✅ (deterministic local Markdown: last week against a usual week, top categories, next 7 days, alerts, budgets, savings tracker; stored in the in-app feed once a week; the coach commentary (E6-6, `schedule_weekly`) is included when recent and labelled; channels get a teaser only; the link is the local address, which works on this machine or through your own Tailscale) |
| E10-4 | As a user alerts aren't noisy | thresholds in preferences; snooze; max N per week | P2 | ✅ (`[alerts]` in `config.toml`: per-kind switches, minimum severity, quiet hours, `max_per_week` per channel, digest-only kinds, thresholds; `coach alerts ack|snooze|restore|mute-kind|unmute-kind`; the alerts center; never re-sent, escalation only on severity increase) |

## E11: Privacy, security & compliance

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E11-1 | As a user people's names, IBANs and account numbers never go to a cloud LLM | person transfers resolved by rules ✅; redaction layer on every LLM call with tests; egress inventory + one policy (`coach/egress.py`: every outbound path registered, `egress.allow` at every call site, local egress journal, `coach privacy report`); `classify enrich` audited (opt-in, shops only, redacted, `--dry-run`) | MVP | ✅ |
| E11-2 | As a user secrets are protected | Enable Banking key + API keys in Keychain; never in repo; `.gitignore`; `coach security audit` (secrets, permissions, plaintext leftovers, `.gitignore` coverage, secret scan, backups, key age; weekly warn-only in `schedule run`) | MVP | ✅ |
| E11-3 | As a user the app is not exposed | binds to localhost; optional auth; remote access via Tailscale/VPN only; audited (bind config, listening sockets, MCP stdio, callback loopback); threat model in docs/security.md | MVP | ✅ |
| E11-4 | As a user I can run fully local | `[privacy] local_only` (Ollama for every LLM path, no web search, no external alert channel, refused at the egress layer) and `offline` (no sync); `coach privacy status` | P3 | ✅ (reduced quality acknowledged; verified with mocked backends only: no real ollama run) |
| E11-5 | As a user advice stays within legal limits | AI-generated label (web, CLI, stored); post-generation investment-product check (ISIN, names, buy / invest phrases FR / IT / EN) with banner + record; disclaimers FR/IT/EN in one module; coach prompt rules re-checked | MVP | ✅ (regex check: conservative, not a legal review) |
| E11-6 | As a user I can export and delete everything | `coach export` (encrypted zip: CSV + JSON tables + memory; `--plain` with TTY + typed phrase; `--decrypt`); `coach wipe` (TTY + typed phrase, dry run, separate questions for session revocation / backups / Keychain) | P3 | ✅ (no import of an export yet; no re-key command) |

Notes on E11 (docs/privacy.md, docs/security.md): the egress layer is cooperative inside the process (it gates the application's own call sites and a
test keeps them registered; it cannot stop another program, an agent's own tools or the interactive skills' web search, which rely on a rule plus
`coach privacy status`). The compliance check is a conservative regex check. The coordinator applies the recommended deny rules for the new
destructive commands (docs/security.md section 5). Known gaps: no export import, no database re-key (logs are rotated since E12).

## E12: Quality: tests, evals, observability

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E12-1 | As a developer parsers are tested on real-shaped fixtures | anonymised fixtures per bank; pagination/dedup/pending tests (mocked API) | MVP | ✅ (`coach eval fixtures synth`: INVENTED descriptors of the shape of the real ones (same-length invented words, re-drawn digits, test IBANs, shifted dates), checked at generation time against the household terms, every merchant key / description word and long digit string of the database, and by the repository hash test; committed for Fortuneo, Caisse d'Epargne, CIC, Revolut (200 each); parser coverage / stable keys / pagination / dedup / pending / rate-limit tests over them; docs/quality.md section 1) |
| E12-2 | As a developer categorization accuracy is measured | gold set of 300–500 user-confirmed tx; accuracy by tx and by money; run on every model/prompt change | MVP | ✅ (`gold_labels` + `eval_runs` (migration 0021); bootstrap by origin from your labels, annotations, overrides, splits; `gold sample` (money / stratified), terminal `gold label`, web **Gold set** page; `coach eval classify`: accuracy by transaction and by money, per source and category (precision / recall / F1), confusions, coverage, tautological rows left out and reported apart; stored runs with a fingerprint, re-scored after `classify run` and weekly in `schedule run` (warn-only). The set is only as good as your labelling: see docs/quality.md section 7) |
| E12-3 | As a developer I compare models | `compare` (second opinion) ✅ + gold-set scoring for haiku / sonnet / local | MVP | ✅ (`coach eval models`: a shadow run on the gold merchants, same candidates / person guard / redaction as `classify run`, the gold merchants held out of the examples, no label ever written; `--dry-run` prints exact payload bytes and a cost estimate; a real run needs a terminal, a typed phrase and the egress gate; accuracy, cost and latency per model next to the pipeline today. Verified with fake backends only: no real model was called) |
| E12-4 | As a user I see LLM usage | per job: model, tokens/cost (notional on subscription), duration | P2 | ✅ (`coach usage` and the **AI usage** page: per job / purpose / backend / model, per day, destinations from the egress journal, notional vs estimated cost, unknown prices counted apart; `[usage] monthly_warn_usd` -> the `llm_usage_high` alert, local feed only) |
| E12-5 | As a developer coach answers are checked | numeric claims cross-checked against tool outputs; eval prompts for skills | P2 | ✅ (`coach eval coach --offline` over the stored answers: numbers traced, evidence refs, AI label, compliance, sections (monthly-review: exactly three actions), disclaimers, length; `--run` asks `evals/coach_questions.yaml` (generic questions, every runnable skill) through the real runner with a terminal, a typed phrase and the egress gate; tested with the fake `claude`; no real model was called) |
| E12-6 | As a developer I can observe the scheduled runs | structured logs with run id, step durations, outcomes, counts; rotation; no personal data | MVP | ✅ (`<data_dir>/logs/runs.jsonl` (closed vocabulary: step names, statuses, integers, exception class names), size / age rotation of every log of the folder, `coach logs [--tail] [--run]`, the last run on the Connections page and in `/health`; a test greps the logs of a run on sentinel data) |

Notes on E12 (docs/quality.md): nothing here called a real model or the network; `eval models` and `eval coach --run` were exercised with fakes and
`--dry-run`. The first real numbers (on a scratch restore) are bootstrap-only: they measure agreement with the user's annotations and labels, not general
accuracy, until a `money` sample is labelled. Known gaps: the model-cost estimate is an upper-bound approximation (no prompt cache, fixed output size);
the coach checks judge traceability, shape and compliance, not usefulness; a live coach evaluation journals its calls under the coach's own purposes.

## E13: Packaging & open-source release

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E13-1 | As a new user I install in 10 minutes | `docker compose up` or `uvx`; guided setup of my own Enable Banking app | P3 | 🟡 (`coach init`, `coach doctor`, `file` secrets backend, `coach schedule loop`, `coach setup enablebanking`, wheel metadata and data files, Dockerfile + compose statically checked; the image was never built or run, the wheel never installed in a clean environment, the guide never run against the live control panel) |
| E13-2 | As a new user I'm onboarded | setup wizard: bank link → first sync → categorization → onboarding interview | P3 | ✅ (`coach setup`, 7 resumable steps with a typed consent before anything is sent, read-only web card; tested with recording fakes: a live run with a real bank is the user's first use) |
| E13-4 | As a maintainer the project is OSS-ready | licence, docs, no personal data in repo, contribution guide for bank parsers | P3 | ✅ (README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, architecture, release checklist, hygiene scan over all publishable files and the sdist list with a local real-data term file, CI file with pinned actions and Dependabot, `coach dev release-check`; MIT licence, reporting contact and repository URL decided on 2026-10-06) |

## E14: Household & people (multi-person finances)

Goal: one household, several people (N adults, M children), each account and transaction
attributable to a person, with household and per-person views.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E14-1 | As a household we declare members | `memory/household.yaml`: members (id, name/alias, role adult/child, birth year), shown in UI; aliases only sent to LLMs | MVP | ✅ (schema, `memory member add`, the Household page (read-only members, counts, owners), `household show`; models see `adult-N` / `kid-N` only: names, aliases, birth years, rule / budget ids never reach a tool, tested with sentinels; [household.md](household.md)) |
| E14-2 | As a user each account has owner(s) and a purpose | account → owners (joint / person) + purpose (main, cards, rental, kids) in profile/UI; used by analytics | MVP | ✅ (`coach accounts set` and the Household page store the member id, refuse an owner that is not `joint` or a declared member, `memory check` warns about old values; the owner is the default person of every transaction on the account. An account has ONE owner or `joint`: a joint account of only some of several adults is not modelled) |
| E14-3 | As a user transactions are attributed to a person | default = account owner; rules for shared cards (e.g. a prepaid card per child); manual reassignment | MVP | ✅ (manual reassignment > `attribution` rules of household.yaml (account, card last four digits, merchant / description regex, direction, amount) > account owner; `coach household assign / unassign / why / log / undo / rule`, the transaction panel; every change logged in `tx_person_log` and reversible; `coach explain` and the panel say why. Migration 0022) |
| E14-4 | As a user I switch between household and person views | filter "Household / one entry per member" on every page | P2 | 🟡 (`?member=` on every endpoint that reads the analytics dataset, the person switch, a `member` argument on the analytics MCP tools, all tested. A person's view counts the transactions attributed to them and the balances / forecast of the accounts they OWN, never a joint account's; pages about the whole household by nature (net worth, loans, connections, memory, alerts, coach) ignore it; the forecast of a person is only as good as the balance of their own accounts) |
| E14-5 | As parents we track the children's money | per child: pocket money in (a regular amount), extra top-ups with source, spending by category, balance trend; "pocket money vs extra" ratio | P2 | ✅ (`coach household kids`, `/household/kids`, the Kids' money page, the `kids_money` tool (pseudonyms): regular pocket money detected (3+ credits, same amount, weekly / fortnightly / monthly) or declared, extra top-ups with their source, spending by category and month, month-end balance trend (an estimate, rebuilt backwards), pocket vs extra ratio; synthetic data only, never run on a real child's accounts) |
| E14-6 | As parents we set kid budgets and get gentle alerts | weekly/monthly limit per child, alert on unusual spend; optional simple kid view (read-only, own data only) | P3 | ✅ (`kid_budgets` in household.yaml, `coach household budget`, the Kids' page; E10 alert kind `kid_budget` (limit close / over, unusual payment) that is LOCAL ONLY: no channel, not even a count, and left out of the digest; the read-only kid page `/me/*` shows only the child's own data. No live alert run on real data) |
| E14-7 | As a user internal money moves across the household's banks are recognised | transfers between the household's banks paired and excluded from spend (extends E1-12) | MVP | ✅ (cross-bank window `[transfers] cross_bank_window_days` (default 5), a household member named in a leg who owns the other account counts as strong evidence, `scope: household` / `to_child` on a pair; both legs stay `transfer.internal` (out of spend and income) and a parent's top-up is still the child's income in E14-5; tested with synthetic multi-bank pairs only, no real second bank) |
| E14-8 | As adults we may each log in | per-user login, roles (adult = all data, child = own data), per-user preferences; audit of who changed memory | P3 | 🟡 (`coach users ...` (terminal only), `coach ui --login-link --user`, the cookie names the login and its role is read from the database on every request, child scope enforced SERVER-SIDE and deny by default on every endpoint (systematic test over the whole OpenAPI schema), per-login preferences, `audit_log` + `Source: ui:<login>` in the memory history; accept / reject / revert stay CLI-only. Never driven from a real browser session and a child login on another device (remote use) is untested; the owner login still sees everything) |
| E14-9 | As a user per-person costs are fair | shared costs (house, cars, insurance) allocated by rule (50/50, by income) for "who pays what" view | P3 | ✅ (`allocations` in household.yaml: equal / income / custom percentages, first matching rule takes a payment; fair share, what each paid personally, settlement that adds up to zero (largest remainder), the joint account settles nothing; `coach household allocation / allocate`, the Who pays what page, the `who_pays` tool; a split of the past, no money moves, no legal or tax view) |

## E15: Rental property (tax-incentive scheme)

Goal: see the real return of the rental investment and never miss an obligation of the scheme.

| ID | Story | Acceptance criteria | Phase | Status |
|---|---|---|---|---|
| E15-1 | As an owner one account is a "property" account | all its flows categorized to the property: rent, loan, copro charges, property tax, management fees, insurance (PNO, GLI), works | P2 | ✅ (a `real_estate_rental` asset names its `account` (or the only rental account is linked and flagged `only_one`) and its `loan`; several properties by id; three new leaves `housing.property_tax`, `housing.property_management`, `housing.property_insurance` and generic French rules (taxe fonciere before the public-finance rule, gestion locative, PNO / GLI, copropriete, an incoming LOYER) with no brand; the loan instalment of a `rental` account was already labelled; what no rule recognises is listed (`coach rental flows`) and counted in "other flows"; a cost paid from another account counts with the tag `property-<id>`; `memory check` reports broken links. Not done: a blanket "every unknown flow of the account is the property's" (an incoming transfer may be the owner's own top-up, so it is never guessed)) |
| E15-2 | As an owner I see the property's monthly cash flow and yearly P&L | rent − loan − charges − fees − taxes; effort d'épargne mensuel; vacancy months | P2 | ✅ (`coach rental cashflow / pnl`, `GET /rental/{id}`, the Rental page with chart, table and vacancy form, the `rental_overview` tool: monthly rows, the effort d'épargne (the shortfall of a month), the yearly P&L with the loan split into interest / insurance / principal from the E9 schedule and the economic result, the gross yield of a complete year, vacancy months (received / partial / paid late / declared / missing / unknown) with the expected rent declared or the median seen, months the data do not cover never counted. A cash flow, not accounting: the loan principal counts as money out) |
| E15-3 | As an owner I track the scheme commitment | start date, commitment length, rent cap and tenant income limits, end date reminders, extension decision | P2 | ✅ (a generic `commitment` block (the scheme name is data; 6 / 9 / 12 for a Pinel-type scheme, any length accepted) with the owner's own rent cap, tenant income limit and reduction rate, no web lookup; end date and reminders at 12 / 6 / 3 months through the E10 engine (kinds `scheme_end`, `scheme_check`, `rent_missing`: local content, external messages minimal), the extension decision recorded in memory (CLI, page) which stops them; rent cap and tenant income checked against the owner's figures; missing facts listed by `coach rental scheme`, `memory check` and one open question per property. The end date counts start + length minus a day: to verify with the deed) |
| E15-4 | As an owner tax-year figures are ready | annual summary for the rental-income return and the scheme reduction (amounts to report), with documents list; no filing | P3 | ✅ (FR only: gross rents, micro-foncier abatement vs the reel deductions (fees, insurance, property tax, charges, works, loan interest and borrower insurance from the E9 schedule, unknown when the loan is not known), the lower candidate, the scheme reduction from the declared price and rate, a documents checklist, `coach rental tax`, the Tax year tab, the `fr-revenus-fonciers` candidate and the declared Pinel reduction in `tax_candidates`; candidates computed from the bank flows with their bounds stated, always the shared disclaimer. The 15,000 EUR ceiling and 30 % abatement are constants known at the time: to verify. The Italian return is not modelled) |
| E15-5 | As an owner I know when to renegotiate or sell | loan rate vs market, end of the commitment period, valuation vs outstanding loan (net equity) | P3 | ✅ (the loan rate against the market rate the owner typed (argument or recorded with its date; older than 30 days or undated is flagged, none is never invented), priced by the E9-5 renegotiation on the real schedule with the fees given; the end of the commitment; net equity = declared valuation − capital from the schedule (unknown when the loan is, stale valuation flagged), before selling costs, penalty and tax; neutral readings, no lender or product, scenarios stay in `coach loans scenario`, `mortgage_check` and `what_if`) |

---

## MVP cut (suggested order)

1. E0-1/2/3/4 consolidate prototype into a package with migrations and encrypted DB.
2. E1-7/8/9/11 consents, auto-callback, second bank, health.
3. E2-2/6/9 + E12-2 parser plugin, pluggable LLM, review queue, gold set.
4. E3-3/6/7 + E7-11 memory checks, open questions, onboarding interview.
5. E4-2/3/7 income/savings, recurring detection, budgets.
6. E5-1→7, 11, 12, 14 web app core.
7. E6-1/3/5/7/8 + E7-3/4 finance MCP, ask-the-coach, monthly review.

## Open decisions

- **Frontend stack**: React + Vite + a chart lib, or SvelteKit, or server-rendered (HTMX). Recommendation: React/Vite SPA + FastAPI.
- **LLM for categorization in steady state**: `claude -p` (Sonnet, fine for small daily volume) vs API key Haiku with caching vs local Ollama. Decide with the gold set (E12-2).
- **Memory edits from UI**: write YAML directly vs DB-backed with YAML export. Recommendation: files stay the source of truth.
- **BankMCP**: keep for ad-hoc chat or retire in favour of the finance MCP (avoid two consent owners per bank).
