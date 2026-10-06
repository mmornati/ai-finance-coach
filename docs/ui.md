# The web app (E5)

A local, fast web app over the existing modules: React + TypeScript (Vite) in `web/`, a FastAPI API in
`src/coach/api/`, served together by `uv run coach ui`. **No business logic lives in the frontend**: every number is
computed by `coach.analytics`, `coach.memory` and `coach.classify`; the API selects and joins, the pages format.

## Run it

```
cd web && pnpm install && pnpm build        # once, and after changing the frontend (output: src/coach/api/static/)
uv run coach ui                              # prints a ONE-TIME LOGIN LINK, opens your browser on it
uv run coach ui --port 9000 --no-browser     # then copy the printed link into your browser
uv run coach ui --login-link                 # prints a fresh one-time link (the server may be running)
uv run coach ui --rotate-session-key         # signs every browser out
uv run coach ui --login-link --user mia-kid  # E14-8: the one-time link of ONE person's login (`coach users add`); without --user it is the owner's
```

There is no way in without that link: a plain visit to `http://127.0.0.1:8765/` only shows a page that says "open the login
link printed by `coach ui`". The link looks like `http://127.0.0.1:8765/login#t=TOKEN`; the token is in the URL *fragment*, so
the browser never sends it to a server, a log or a Referer. The page trades it once for the session cookie and removes it
from the address bar. A token works once and for 2 minutes.

Dev mode (hot reload): two terminals. The login mechanism is the same (no shortcut).

```
uv run coach ui --dev --no-browser           # API only, on 8765; prints a link on http://localhost:5173
cd web && pnpm dev                           # http://localhost:5173 , proxies /api to 8765
```

Tests: `uv run pytest tests/test_api.py tests/test_api_security.py tests/test_api_zz_coverage.py` (every endpoint through the
TestClient; login, cookie, CSRF, Host, headers, rate limits; "a refused request changes nothing" on every mutating endpoint;
preview == effect) and `cd web && pnpm test` (vitest: formatters, API client, UI kit, login, the category panel, review queue,
proposals and history pages).

To try it on realistic data without touching the real database or memory: `uv run coach backup`, then
`uv run coach restore BACKUP --to /some/scratch/dir`, and start `coach ui` with `COACH_DB`, `COACH_MEMORY_DIR`,
`COACH_DATA_DIR`, `COACH_CONFIG_DIR` (and a throw-away `COACH_CONFIG`) pointing there.

## Pages

| Page | Stories | What it shows |
|---|---|---|
| Dashboard `/` | E5-2 | balances (per account, household), this month against the coverage-aware usual month, cash flow (6 months, incomplete months drawn lighter), savings rate with the note on loan principal, top categories, budgets, 14-day upcoming payments, 90-day forecast with its band and at-risk flags, latest insights, open questions, connector health |
| Transactions `/transactions` | E5-3, E5-4, E5-5 | filters (date, account, person, purpose, category / group, merchant, amount range, direction, tag, source, event, text), totals of the **filtered set**, infinite scroll, CSV export of the same filter. A row opens a side panel: why this category (the decision chain of `coach explain`), change it (this transaction / this merchant / a memory annotation, always with a preview of what would change), tags, event and note |
| Categories `/categories`, `/categories/:id` | E5-6 | usual month per category, this and last month; the drill-down has the monthly chart (covered / incomplete / in-progress months), merchants, trend, one-offs apart, low-confidence and coverage notes |
| Review `/review` | E2-9 | the review queue (labels the app is unsure about, by money at stake): keep the label or pick the category |
| Budgets `/budgets` | E5-7 | progress, projected end of month, rollover, unbudgeted spending, suggestions (accept = preview of the diff, then a write through the store), savings goals |
| Subscriptions `/subscriptions` | E5-8, E8 | tab **Inventory**: every recurring cost by group with yearly cost, contract status (on file / missing / expired) and a *Create contract from this subscription* button (preview, then write), usage (frequency, last used) and its reminders, cancellation rules (can cancel now, earliest date, notice, early-termination cost, legal basis with source and review date, the "verify" disclaimer), the alternatives table (outdated offers flagged, savings computed by the app), decisions with their verification and the savings achieved, and *Prepare cancellation letter* (language, channel, copy, download; local only). Tab **Detected payments**: the E5-8 list (per month / year, next charge, price changes, linked contract or loan) |
| Loans & net worth `/wealth` | E5-9, E9 | net worth from balances + manual assets - known liabilities (the capital of each loan from its amortization schedule), by category and by person, every unknown listed and never guessed; a **monthly history chart** (stacked assets by category, what is owed below the axis, the net-worth line; months with unknown items drawn lighter, with a table view) and *Record today*; loan cards with the capital due and where it comes from (schedule / declared), payment alerts, the lease reminder and the fields still missing; **Details** (schedule with interest by calendar year, payments, scenarios: early repayment / renegotiation / insurance, suggestions inferred from the payments queued as a proposal only, lease end of contract with mileage projection and an odometer form); loan form with deferral, insurance, variable-rate and lease fields; assets with stale badges |
| Rental property `/rental` | E15 | one tab per section for a property (several: a switch): **Cash flow and P&L** (the last twelve closed months as a chart with a table view and the rent status of each month, the effort d'épargne, the vacancy with a *Declare a vacancy* form, the P&L of a year with the loan interest / principal split, the flows still in no property category, the links to the account and the loan, *Edit facts*); **Scheme commitment** (dates, progress, reminders, the extension decision form, the rent cap and tenant income checks on your own figures, the facts still missing); **Tax year** (micro-foncier and reel candidates, the lower one, the scheme reduction, the documents checklist, always "not a return, nothing is filed, not tax advice"); **Renegotiate or sell** (the market rate you type, the loan rate against it, the renegotiation on the real schedule, the net equity, the indicators). Every write shows its diff first. Reads are about the whole household (the person switch does not narrow them) and a child login cannot reach them |
| Calendar `/calendar` | E5-10 | month grid and agenda of recurring payments, loan instalments, contracts, bank consents, asset reminders; `.ics` download |
| Insights `/insights` | E5-11 | cards from anomalies, price changes, forecast flags, budget overruns and subscription reminders (unused for 60 days by your own record, a notice deadline within 30 days, a decision to check); dismiss / snooze are persisted; the coach's own insights (E6-7): digests, answers and findings with their evidence chips, provenance (backend, model, skill), warnings for unverified numbers and suspicious text, read / done / snooze / dismiss |
| Alerts `/alerts` | E10 | the alert events (consent expiring, sync failing, unusual charge, price rise, low forecast balance, budget, loans, subscriptions) with filters (status, kind, severity), acknowledge / snooze 7 days / restore / mute a kind, "Check now" (stores the events; sends nothing outside the app), the READ-ONLY channel view (enabled, ready, masked target, what is missing) with "Test (dry run)" buttons that show the exact message and send nothing, a preview of the weekly summary, and the per-kind mute / hold switches. Enabling a channel is done in `config.toml`, never here. The dashboard has an Alerts card and the nav a badge. Reference: [alerts.md](alerts.md) |
| Gold set `/gold` | E12-2 | the transactions whose category you confirmed: counts by origin (your labels, annotations, overrides, splits, labels given here), the latest accuracy by transaction / by money / for AI labels only, "Add what I already decided", "Score it now" (offline; stores a run), and a queue of transactions to label (biggest money first, or a bit of everything): keep the current category or pick another. It writes the gold set only: a category of the app never changes. Reference: [quality.md](quality.md) |
| AI usage `/usage` | E12-4 | what the AI calls cost: calls, tokens, notional cost (a subscription call is not billed), cost per day, per job and model, where the calls went (host and size from the egress journal, never a payload) and the month against `[usage] monthly_warn_usd`. The Connections page also shows the last scheduled run (steps, durations, failures). Reference: [quality.md](quality.md) |
| Ask the coach `/coach` | E5-12, E6-3 | chat panel: one question at a time through the configured backend (`[coach]`), streamed over SSE with the tools the coach looked at, clickable evidence refs (resolved on the server), proposals with the command to run in a terminal, usage and a cancel button; see [coach.md](coach.md) |
| Memory `/memory` | E5-13 | open-questions inbox, proposals (diff, reject; accepting is a terminal command shown on screen), household, events, loans / contracts / assets forms, annotations, memory check, change history (read-only; reverting is a terminal command) |
| Set up `/setup` | E7-11 | the onboarding checklist (household and privacy declarations, account owners, loans, contracts, preferences, budgets, questions) with what is missing and the next action; the country / declarations and the coach rules of `preferences.md` are written inline after a previewed diff; the other steps link to the Memory, Connections and Budgets forms; see [skills.md](skills.md). E13-2: a read-only **First run** card at the top shows the seven steps of `coach setup` (init, Enable Banking, first bank, first sync, categorize, interview, daily job) with their state and the terminal command: the steps ask for a typed consent before anything leaves the machine, so the page starts none of them |
| Household `/household` | E14-1, E14-2, E14-3, E14-8 | members (read-only: name, role, birth year, accounts, attributed transactions), the accounts with an **owner** (joint or one member) and a **purpose** you edit in place, the attribution rules (add with a preview of how many transactions it would attribute, remove), the logins (read-only: they are created in a terminal) with the commands to copy, "My preferences" for a stored login, and the audit (who changed what in the web app, and the memory history with its source) |
| Kids' money `/kids` | E14-5, E14-6 | one tab per child: balance, pocket money per month, extra top-ups, pocket vs extra; the detected pocket-money series (declare the expected amount), extra top-ups with their source (a parent's top-up shown as an internal transfer), spending by category and month, the month-end balance trend (an estimate), the kid budgets of the child with a progress bar (add / remove with a preview). Nothing about a child is sent outside the machine |
| Who pays what `/who-pays` | E14-9 | the allocation rules (equal / by income / custom percentages) with, per member, the share, the fair share of the cost, what they paid personally and the settlement, and all rules together; joint-account costs settle nothing; add a rule with a preview |
| My money (child login) | E14-6, E14-8 | what a child login gets INSTEAD of the app: their own balance, spending, limits with a gentle message, pocket money, top-ups (the source only as "a parent" / "family" / "someone else") and latest payments. No navigation, no other page |
| Connections `/connections` | E5-14 | consents with days left, health, accounts (label, owner, purpose, exclusion), sync now (daily limits, calls left), connect / reconnect, internal transfers |

UX (E5-15): mobile-first layouts (bottom tab bar, bottom sheets), dark / light following the system with a toggle, French
(`fr-FR`, default) or English (`en-GB`) number and date formatting in EUR, keyboard access and visible focus, native
`<dialog>` for focus trapping, a "Table" alternative to every chart, PWA manifest and an offline shell. All UI strings
are English (kept in the components, ready to be moved to a catalogue). No external font, CDN, analytics or telemetry:
everything is bundled, and the CSP forbids anything else.

Chart library: **Recharts**. It is React-native and declarative, ships SVG (works with the strict CSP, inspectable,
printable), supports exactly the marks needed (bars, areas with a range band, reference lines, tooltips) and costs about
115 kB gzip, loaded only by the pages that draw charts. ECharts is more powerful but imperative, canvas-first and
roughly twice the size for charts this simple. Colours are the validated categorical palette of the dataviz method (blue,
orange, aqua...), stepped separately for dark mode.

## Security model

The audit of this model (bind configuration, listening sockets, key age) is `coach security audit` (E11-3); the threat model is [security.md](security.md). Coach answers and coach insights show an "AI-generated" label and, when the investment-product check flags the text, a banner (E11-5).

The app shows your whole financial life. It is local by default and defended in depth.

1. **A one-time login link, never a plain page load.** No cookie is ever issued by `GET /` or any page. `coach ui` generates a
   random single-use token (valid 2 minutes; stored only as a SHA-256 hash in a 0600 file next to the secret, so
   `coach ui --login-link` in another terminal can mint more) and prints the link `…/login#t=TOKEN` on *its own terminal*. The token
   is in the URL fragment (never sent to a server, a log or a Referer); the page POSTs it once to
   `POST /api/v1/session/exchange`, which checks Host, Origin and content type, rate-limits attempts (8, then one every
   ~40 s) and answers with the cookie. A local process that merely asks the server for a page gets nothing. The same
   mechanism applies in dev mode and for remote binds.
2. **Loopback only.** `coach ui` binds `127.0.0.1` (`[ui] host`). Any other host is refused unless `[ui] allow_remote = true`
   **and** `[ui] remote_tls_ack = true` (you confirm that an HTTPS proxy terminates TLS in front, e.g.
   `tailscale serve --bg 8765`; plain http over a network would expose the session) **and** `[ui] allowed_hosts` names the
   hosts to accept. Remote sessions use `Secure` cookies and the printed link is `https://<first allowed host>/login#t=…`.
   Reach it through Tailscale or a VPN, never the open internet.
3. **Host header check** on every request (DNS-rebinding protection): `localhost:PORT`, `127.0.0.1:PORT`, `[::1]:PORT`
   only (plus `allowed_hosts`, plus the Vite ports in `--dev`). Anything else gets `421`.
4. **The session cookie** is named per port (`coach_session_<port>`: another app on another localhost port cannot replay
   or clobber it), HttpOnly, SameSite=Strict, signed (HMAC) together with its issue time, and valid for `[ui] session_hours`
   (default 12). `POST /api/v1/session/logout` (the sign-out button) revokes it **server-side** (a copied cookie dies too;
   the revocation list survives restarts). The signing secret (`<data_dir>` file, mode 0600) is replaced automatically when
   older than `[ui] key_rotation_days` (default 30) and on demand with `coach ui --rotate-session-key`: every session
   is signed out.
5. **CSRF and write limits.** Mutating calls (`POST`, `PUT`, `PATCH`, `DELETE`) also need `X-CSRF-Token`, an HMAC of the cookie
   fetched from `GET /api/v1/session`; a present `Origin` must be an allowed host; the body must be `application/json` (no
   form posts); `Sec-Fetch-Site: cross-site` is refused for every API call; there is no CORS. Writes are rate limited per
   session with a token bucket (30 at once, 2 per second; previews have their own larger bucket), `429` with `Retry-After`.
   A test sends every refusal case to every mutating endpoint and checks the database, the memory and the UI state are
   byte-identical afterwards.
6. **Headers on every response**, errors (421, 401, 403, 415, 429, 500) included: `Content-Security-Policy: default-src 'self';
   script-src 'self'; style-src 'self'; ...; frame-ancestors 'none'` (no inline script or style, no `unsafe-eval`),
   `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, cross-origin isolation
   headers, `Cache-Control: no-store` on the API.
7. **The database cannot be written from the read path.** The shared connection is `PRAGMA query_only` *and* has a SQLite
   authorizer that denies every statement except reads outside `AppState.write()` (INSERT, UPDATE, DELETE, DDL,
   `PRAGMA ... = value`, ATTACH, VACUUM all fail inside SQLite itself). Writes use the existing functions only:
   `classify.corrections` (override / merchant label / confirm), `classify.splits`, `transfers.link / unlink`,
   `ingest.accounts.set_account`, `analytics.anomalies.dismiss_anomaly`, `analytics.pricechanges.dismiss_price_change`. A second
   read-only connection serves the slow reads (memory check, review queue) so they never block quick requests.
8. **Memory is never written directly.** Every memory write is a `MemoryStore.edit(..., source="ui")`: schema and semantic
   validation, comment preservation, the change history (`coach memory history` shows `ui`). Every write endpoint takes
   `dry_run=true` and returns the diff first. Previews of category fixes are produced by running the real classifier on a
   rolled-back copy of the change, so a preview cannot disagree with the write.
9. **Accepting proposals and reverting history are terminal-only.** The page lists proposals with their diff, source,
   suspicious-text warnings and the exact command to run in *your own* terminal (`uv run coach memory accept <id>`, which
   needs an interactive TTY and a typed confirmation); history entries offer `uv run coach memory revert <id>` the same
   way. The web API has **no endpoint** to accept (with or without force) or revert, so nothing that reaches the page can
   write a coach proposal into your memory and no typed code is ever served by the API. The page keeps `reject` (harmless:
   nothing is written) behind a confirmation dialog.
10. **Sync / connect.** `POST /sync` runs `ingest.sync.sync_all` with `force=False`, so the per-account daily limit applies and
   skipped accounts are reported. Connect / reconnect run the existing `connect_flow` (local HTTPS callback server) in a
   background thread; the page only receives the bank's own login URL. No token, key, private key path or Enable Banking
   credential is ever returned. IBANs are masked to the last 4 digits; the `tx_key` is an identifier the page needs to
   address a transaction and appears only in URLs, never as text.
11. **Service worker** (`/sw.js`, served as `text/javascript` with `Service-Worker-Allowed: /`) caches static files only.
    `/api` is never cached, read from a cache or stored (checked in the browser: the cache holds the shell and the bundles
    only).
12. **Per-person logins (E14-8, [household.md](household.md)).** A one-time token can be made for one login (`coach ui --login-link --user ID`); the cookie then
    names the login (`sid.issued.login.signature`, signed, so it cannot be edited) and the login's role and member are read from the database on EVERY request
    (a disabled or deleted login is signed out at once; a token for one is refused). A session with no login is the owner's, as before. A **child** login gets
    `403 forbidden_scope` on every endpoint except an explicit allow-list (`CHILD_ALLOWED` in `coach.api.app`: session, sign-out, taxonomy, `/me`, `/me/summary`,
    `/me/transactions`, `/me/preferences`), whatever the method and whether the endpoint exists: deny by default, checked before CSRF and rate limits. `/me/*` takes the
    member from the login, never from a parameter, and the shared snapshot dependency forces a child's member too. Logins cannot be created, promoted or enabled from the
    web app. Every change a login makes is recorded in `audit_log` (method, endpoint, status, login: never a payload) and in the memory history as `ui:<login>`.

### Agents must not call this API

The API is for the human at the keyboard. Getting a session needs the login link printed on the terminal that runs
`coach ui` (or `coach ui --login-link`), the cookie lives in the user's browser, and the signing secret is a private file; a
process running as the same user could still read that file or run the command. The coordinator should deny, in the agents'
permission rules, requests to the app's port (`curl` / `wget` / `python -c` to `127.0.0.1:8765` / `localhost:8765`), reading the
files `ui-session.key`, `ui-login.json`, `ui-revoked.json` and `ui-state.json` of the data directory, and the `coach ui`
command, the same way `coach memory accept` is denied. The two decisions that matter most (accepting a proposal, reverting
memory) are not reachable through the API at all.

## API

OpenAPI JSON at `/api/openapi.json`, a plain server-rendered reference at `/api/docs` (no CDN, needs the cookie). Money is a
decimal **string** (`"-12.34"`) everywhere; analytics endpoints return the `to_dict()` structures of `docs/analytics.md`
unchanged, with `coverage` and `evidence`. Errors: `{"error": {"code", "message", "details"}}` with `401` (no session), `403`
(CSRF / cross-site), `404`, `409` (memory / state conflicts), `421` (Host), `422` (validation), `502` (bank API). Lists
paginate with `limit` / `offset` and return `total` and `next_offset`. Every analytics endpoint accepts `owner`, `purpose`,
`account` (the household / person switch) and every analytics READ endpoint (cash flow, averages, forecast, categories, calendar, balances, coverage, budgets, goals, subscriptions, price changes, anomalies, insights,
transactions and their export and detail, the subscriptions inventory) also accepts `member` (one household member id or `joint`, E14-4: only what is attributed to that person; 404 for an unknown member).
Household-wide endpoints (net worth, loans, contracts, alerts, the review queue, memory, every write) take no person filter, on purpose: a figure or a write computed on one member's
view would be wrong. A child login is always narrowed to its own member. Write endpoints accept `dry_run=true`.

| Method | Path | What |
|---|---|---|
| `POST` | `/api/v1/session/exchange` | Trade the one-time login token for the session cookie (the only call without a session) |
| `POST` | `/api/v1/session/logout` | End this browser session (revoked server-side) |
| `GET` | `/api/v1/session` | Session info and the CSRF token |
| `GET` | `/api/v1/meta/taxonomy` | Category taxonomy (groups and leaves) |
| `GET` | `/api/v1/meta/filters` | Values for every filter: accounts, owners, purposes, tags, events |
| `GET` | `/api/v1/health` | Connector health per bank and account, plus the memory check summary |
| `GET` | `/api/v1/accounts/balances` | Balance per account and the household total |
| `PATCH` | `/api/v1/accounts/{uid}` | Edit an account: label, owner, purpose, exclusion from analytics |
| `GET` | `/api/v1/analytics/averages` | Coverage-aware monthly averages per category and for the household |
| `GET` | `/api/v1/analytics/cashflow` | Income, spending, saved, debt service and savings rate per month |
| `GET` | `/api/v1/analytics/coverage` | Which months each account covers |
| `GET` | `/api/v1/analytics/forecast` | Balance forecast with band, events and at-risk flags |
| `GET` | `/api/v1/analytics/month-categories` | One month's spending per category next to its average |
| `GET` | `/api/v1/categories` | Every spending category with its average, this month and last month |
| `GET` | `/api/v1/categories/{category_id}` | Drill-down of a category or a group: monthly chart, merchants, trend, one-offs |
| `GET` | `/api/v1/transactions` | Filtered transactions with the totals of the whole filtered set |
| `GET` | `/api/v1/transactions/export.csv` | CSV export of the filtered set (same filters as the list); `locale=fr-FR`: UTF-8 BOM, `;` and decimal commas; formulas neutralised |
| `GET` | `/api/v1/transactions/detail` | One transaction with the decision chain behind its category (explain) |
| `POST` | `/api/v1/transactions/category` | Fix a category: this transaction, this merchant, or a memory annotation (`dry_run`) |
| `POST` | `/api/v1/transactions/override/clear` | Remove the per-transaction category override |
| `POST` | `/api/v1/transactions/split` | Split a transaction over categories (parts must add up exactly) |
| `POST` | `/api/v1/transactions/split/clear` | Remove the split of a transaction |
| `GET` | `/api/v1/annotations` | Memory annotations with how many transactions each decides |
| `POST` | `/api/v1/annotations` | Add tags / a note / an event / a category to transactions (`dry_run`) |
| `POST` | `/api/v1/annotations/{annotation_id}/delete` | Remove an annotation (`dry_run`) |
| `GET` | `/api/v1/transfers` | Internal transfers between own accounts: links and proposed pairs |
| `POST` | `/api/v1/transfers/link` | Link a debit and a credit as an internal transfer |
| `POST` | `/api/v1/transfers/unlink` | Remove an internal transfer link (the pair is not proposed again) |
| `GET` | `/api/v1/household/overview` | Members, accounts with owner and purpose, attribution rules, kid budgets, allocations, logins, warnings (adult logins only, like every `/household/*`) |
| `PUT` | `/api/v1/household/attribution/rules/{id}` | Create or replace an attribution rule (`dry_run=true`: the diff and how many transactions it matches) |
| `POST` | `/api/v1/household/attribution/rules/{id}/delete` | Remove an attribution rule |
| `GET` | `/api/v1/transactions/attribution` | Whose a transaction is and why (manual line, every rule, the account owner, the card digits, the log) |
| `POST` | `/api/v1/transactions/person` | Reassign a transaction to a member or `joint` by hand (recorded, reversible) |
| `POST` | `/api/v1/transactions/person/clear` | Remove the manual reassignment |
| `GET` | `/api/v1/household/attribution/log` | The recorded reassignments, newest first |
| `POST` | `/api/v1/household/attribution/log/{id}/undo` | Undo one recorded reassignment |
| `GET` | `/api/v1/household/kids` | Every child's money: pocket money, extra top-ups, spending, balance trend, ratio |
| `GET` | `/api/v1/household/kids/{member}` | One child's money |
| `GET` | `/api/v1/household/kid-budgets` | Kid budgets with this week's / month's progress |
| `PUT` | `/api/v1/household/kid-budgets/{id}` | Create or replace a kid budget (`dry_run=true`: preview) |
| `POST` | `/api/v1/household/kid-budgets/{id}/delete` | Remove a kid budget |
| `PUT` | `/api/v1/household/members/{member}/pocket-money` | Declare (or clear, with an empty body) the pocket money a child is meant to get |
| `GET` | `/api/v1/household/allocation` | Shared costs split by rule: shares, what each paid, the settlement |
| `PUT` | `/api/v1/household/allocations/{id}` | Create or replace an allocation rule (`dry_run=true`: preview) |
| `POST` | `/api/v1/household/allocations/{id}/delete` | Remove an allocation rule |
| `GET` | `/api/v1/household/users` | The logins (read-only: created with `coach users`) |
| `GET` | `/api/v1/household/audit` | The web app's audit log and the memory history with its sources |
| `GET` | `/api/v1/me` | Who this session is (login, role, member): the first endpoint a child may call |
| `GET` | `/api/v1/me/summary` | A member's own money (balance, pocket money, spending, budgets): the only analytics-like view of a child login |
| `GET` | `/api/v1/me/transactions` | The own payments of the member this login belongs to |
| `GET`, `PUT` | `/api/v1/me/preferences` | The preferences of this login (`locale`, `theme`, `default_member`, `landing`) |
| `GET` | `/api/v1/review` | Merchants to review: low-confidence, uncategorized or not labelled, by money at stake |
| `POST` | `/api/v1/review/confirm` | Confirm the current label of a merchant as yours |
| `POST` | `/api/v1/review/correct` | Set the category of a merchant (every transaction with this merchant key) |
| `GET` | `/api/v1/budgets` | Budgets with progress, projection and status; the biggest unbudgeted categories |
| `POST` | `/api/v1/budgets` | Create or update a budget (`dry_run`) |
| `GET` | `/api/v1/budgets/suggestions` | Suggested monthly amounts: the median of the last covered months, rounded |
| `POST` | `/api/v1/budgets/{budget_id}/delete` | Remove a budget (`dry_run`) |
| `GET` | `/api/v1/goals` | Savings goals with progress, pace and projected date |
| `POST` | `/api/v1/goals` | Create or update a goal (`dry_run`) |
| `GET` | `/api/v1/subscriptions` | Recurring payments: cost per month and year, next charge, price changes, contract link |
| `GET` | `/api/v1/subs/inventory` | Every recurring cost and contract in one list: yearly cost, contract status, usage, cancellation rules, alternatives, decision (E8-1) |
| `POST` | `/api/v1/subs/contracts/draft` | Create a contract file from a recurring series (`dry_run`: preview; source `ui`) |
| `PUT` | `/api/v1/subs/contracts/{contract_id}/usage` | Record how a service is used (`dry_run`) |
| `POST` | `/api/v1/subs/usage-questions` | Add the usage questions still to ask (`dry_run`: list them) |
| `GET` | `/api/v1/subs/alternatives` | Stored alternatives with savings by code and staleness (E8-4) |
| `POST` | `/api/v1/subs/alternatives` | Store an alternative (price > 0, https URL, date not in the future) |
| `DELETE` | `/api/v1/subs/alternatives/{alt_id}` | Delete a stored alternative |
| `GET` | `/api/v1/subs/letter` | A cancellation letter text, generated locally (`contract`, `lang`, `channel`, `holder`; nothing is sent) (E8-5) |
| `GET` / `PUT` | `/api/v1/subs/contact` | The contact block of the letters (local only; `dry_run`) |
| `GET` | `/api/v1/subs/savings` | Realised savings, decisions with their verification, the coach's proposed ones (E8-6) |
| `POST` | `/api/v1/subs/decisions` | Record a decision (`dry_run`: validate only) |
| `POST` | `/api/v1/subs/decisions/{dec_id}/confirm` / `reject` | Confirm or reject a decision the coach proposed |
| `DELETE` | `/api/v1/subs/decisions/{dec_id}` | Delete a decision |
| `GET` | `/api/v1/price-changes` | Price changes of recurring payments |
| `POST` | `/api/v1/price-changes/{change_id}/dismiss` | Dismiss a price change (kept dismissed across refreshes) |
| `GET` | `/api/v1/anomalies` | Anomalies: category spikes, duplicate charges, new merchants, large payments |
| `POST` | `/api/v1/anomalies/{anomaly_id}/dismiss` | Dismiss an anomaly |
| `POST` | `/api/v1/anomalies/{anomaly_id}/undismiss` | Bring a dismissed anomaly back |
| `GET` | `/api/v1/insights` | Insights feed: anomalies, price changes, forecast flags, budget overruns, plus the coach's own insights (E6-7) |
| `POST` | `/api/v1/insights/{insight_id}/dismiss` | Dismiss an insight (persisted) |
| `POST` | `/api/v1/insights/{insight_id}/snooze` | Hide an insight for some days (persisted) |
| `POST` | `/api/v1/insights/{insight_id}/restore` | Undo a snooze or a dismissal |
| `POST` | `/api/v1/insights/{insight_id}/done` | Mark an insight as done (the coach's: status done; a card: dismissed) |
| `POST` | `/api/v1/insights/{insight_id}/read` | Mark a coach insight as read |
| `GET` | `/api/v1/alerts` | Alerts: the events the engine kept, with their state (ack / snooze / mute) |
| `GET` | `/api/v1/alerts/summary` | Open alerts count for the dashboard badge |
| `POST` | `/api/v1/alerts/check` | Evaluate the signals now and keep the events (stores only: nothing is sent outside the app) |
| `POST` | `/api/v1/alerts/{alert_id}/ack` | Acknowledge an alert (it returns only if its severity goes up) |
| `POST` | `/api/v1/alerts/{alert_id}/snooze` | Hold an alert for some days |
| `POST` | `/api/v1/alerts/{alert_id}/restore` | Undo an ack, a snooze or a suppression |
| `POST` | `/api/v1/alerts/kinds/{kind}/mute` | Mute a kind of alert until it is unmuted |
| `POST` | `/api/v1/alerts/kinds/{kind}/unmute` | Unmute a kind of alert |
| `POST` | `/api/v1/alerts/kinds/{kind}/snooze` | Hold a whole kind of alert for some days (new ones too) |
| `POST` | `/api/v1/alerts/kinds/{kind}/wake` | End the snooze of a kind |
| `GET` | `/api/v1/alerts/channels` | The channels: enabled, ready, where they send (read-only: enable them in config.toml) |
| `POST` | `/api/v1/alerts/channels/{name}/test` | DRY RUN: the exact message a test send would produce (nothing is sent) |
| `GET` | `/api/v1/alerts/digest` | Preview of the weekly summary (rendered locally, nothing stored or sent) |
| `GET` | `/api/v1/gold` | The gold set: counts by origin, the latest accuracy run and a sample of transactions to label |
| `GET` | `/api/v1/gold/sample` | Transactions to label next (never one already in the gold set) |
| `POST` | `/api/v1/gold/label` | Confirm the category of one transaction (writes the gold set only) |
| `POST` | `/api/v1/gold/remove` | Take one transaction out of the gold set |
| `POST` | `/api/v1/gold/bootstrap` | Add what the user already decided, marked by origin (`dry_run=true` previews) |
| `GET` | `/api/v1/eval/runs` | The stored evaluation runs (classify, models, coach) |
| `GET` | `/api/v1/eval/runs/{run_id}` | One evaluation run with its full result |
| `POST` | `/api/v1/eval/classify` | Re-score the gold set now (offline: no model is called) and store the run |
| `GET` | `/api/v1/usage` | LLM usage: per job / model, per day, where the calls went, the month against the threshold |
| `GET` | `/api/v1/logs/runs` | The structured logs of the scheduled runs |
| `GET` | `/api/v1/liabilities` | Loans and other liabilities, with what is missing and the matching bank payments |
| `GET` | `/api/v1/assets` | Assets that are not synced, with stale value flags |
| `GET` | `/api/v1/contracts` | Contracts on file (provider, renewal, notice, keep decision) |
| `GET` | `/api/v1/net-worth` | Net worth from balances, manual assets and liabilities: unknowns are listed, never guessed (`history=true`: monthly history) |
| `GET` | `/api/v1/net-worth/history` | Monthly net worth: stored snapshots plus months rebuilt from the data |
| `POST` | `/api/v1/net-worth/snapshot` | Record today's snapshot and back-fill the past months (database only) |
| `GET` | `/api/v1/loans/{loan_id}` | One loan in full: schedule (every instalment), linked payments, alerts, suggestions, lease view |
| `GET` | `/api/v1/loans/{loan_id}/payments` | The bank payments linked to the loan, with its alerts |
| `POST` | `/api/v1/loans/{loan_id}/scenario` | Early repayment / renegotiation / insurance scenario (a calculator; `save` stores an insight) |
| `POST` | `/api/v1/loans/{loan_id}/odometer` | Record a mileage reading of a leased vehicle (`dry_run`) |
| `POST` | `/api/v1/loans/{loan_id}/infer/propose` | Queue the terms inferred from the bank payments as a memory proposal |
| `GET` | `/api/v1/calendar` | Upcoming payments, renewals, consents and reminders (a month, or the next N days) |
| `GET` | `/api/v1/calendar.ics` | The calendar as an iCalendar file |
| `GET` | `/api/v1/coach/status` | Is the coach available? Which backend, model and limits |
| `GET` | `/api/v1/coach/prompts` | Quick prompts for the chat panel (including the E7 skills: each has a `skill` id) |
| `POST` | `/api/v1/coach/stream` | Ask the coach (`{question, skill?}`: a skill id runs that skill's prompt, see skills.md); the answer streams as server-sent events (one job at a time) |
| `POST` | `/api/v1/coach/jobs` | Start a coach job without streaming (returns its id) |
| `GET` | `/api/v1/coach/jobs/{job_id}` | State of a coach job and the answer so far |
| `GET` | `/api/v1/coach/jobs/{job_id}/stream` | Replay and follow the events of a coach job |
| `POST` | `/api/v1/coach/jobs/{job_id}/cancel` | Cancel a running coach job |
| `POST` | `/api/v1/coach/resolve` | Resolve the evidence refs of an answer to transactions (server side) |
| `GET` | `/api/v1/memory/overview` | What the memory holds: files, counts, open questions, pending proposals, check summary |
| `GET` | `/api/v1/memory/check` | `coach memory check`: schema, semantic and database consistency issues |
| `GET` | `/api/v1/memory/history` | The change history of the memory (read-only) |
| `GET` | `/api/v1/memory/history/{change_id}/diff` | The patch of one recorded change, with the terminal command that would revert it |
| `PUT` | `/api/v1/memory/{kind}/{item_id}` | Create or update a liability, contract, asset, member or event (`dry_run`) |
| `GET` | `/api/v1/questions` | Open questions the coach asks (the inbox) |
| `POST` | `/api/v1/questions/{qid}/answer` | Record the answer (changing memory is a separate, previewed step) |
| `POST` | `/api/v1/questions/{qid}/dismiss` | Dismiss a question (it is not asked again) |
| `POST` | `/api/v1/questions/{qid}/reopen` | Reopen an answered or dismissed question |
| `GET` | `/api/v1/household` | Household members (names stay on this machine) |
| `GET` | `/api/v1/events` | Events (trips, works, one-off projects) that annotations can point to |
| `GET` | `/api/v1/setup/wizard` | The first-run wizard's seven steps and where you are; read-only (E13-2) |
| `GET` | `/api/v1/setup/doctor` | `coach doctor` as data: the installation checks and next steps; read-only (E13-1) |
| `GET` | `/api/v1/onboarding` | What the coach still does not know, as an ordered checklist with the next actions (E7-11) |
| `PUT` | `/api/v1/onboarding/household` | Set the country and the privacy declarations (employers, places, schools); `dry_run=true`: preview |
| `POST` | `/api/v1/onboarding/preferences` | Append coach rules (language, tone, goals, topics to avoid) to preferences.md; `dry_run=true`: preview |
| `GET` | `/api/v1/proposals` | Memory proposals queued by the coach or by a document extraction, with the terminal command to accept them |
| `POST` | `/api/v1/proposals/{pid}/reject` | Reject a proposal (it is closed, nothing is written). There is deliberately no accept endpoint |
| `GET` | `/api/v1/connections` | Accounts (IBAN masked), consents (days left), connector health and the sync state |
| `POST` | `/api/v1/sync` | Sync now (respects the daily limit per account; runs in the background) |
| `GET` | `/api/v1/sync/status` | State of the last / running sync |
| `GET` | `/api/v1/banks` | Banks available for a country (asks Enable Banking) |
| `POST` | `/api/v1/connections/connect` | Start a bank connection: poll `/connections/auth/status` for the bank's login URL |
| `POST` | `/api/v1/connections/reconnect` | Renew a consent for the same bank (the old session is retired when the new one completes) |
| `GET` | `/api/v1/connections/auth/status` | State of the running authorisation: waiting (with the bank URL), done, failed |

### Streaming contract of the coach (E5-12 / E6-3)

`POST /api/v1/coach/stream` with `{"question"}` starts ONE background job (409 `coach_busy` while another runs) and answers
`text/event-stream`; each event is `id: N` + `event: NAME` + `data: JSON`: `meta {job_id, configured, backend, model}`,
`status`, `tool_call {id, name, args, n, max}`, `tool_result {id, name, ok, chars, suspicious}`, `delta {text}`,
`proposal {id, file, command}`, `citation {label, ref, kind}`, `usage {...}`, `answer {insight_id, suspicious, proposals,
unverified_numbers, finish_reason}`, `notice {code, message}`, `error {code, message}`, `done {finish_reason, job_id,
insight_id}`. A page that reloads reattaches with `GET /coach/jobs/{id}/stream?after=N`; `POST /coach/jobs/{id}/cancel`
stops the job. The model runs with the finance MCP tools only: see [coach.md](coach.md).

## Configuration

```toml
[ui]
host = "127.0.0.1"        # loopback only
port = 8765
allow_remote = false      # true = accept another host (WARNING: use Tailscale / a VPN)
remote_tls_ack = false    # required with allow_remote: an HTTPS proxy (`tailscale serve`) terminates TLS in front of the app
allowed_hosts = []        # Host names accepted when allow_remote is true, e.g. ["mac.tailnet.ts.net"] (required then)
session_hours = 12        # a browser session lasts this long
key_rotation_days = 30    # the session secret is replaced when older than this
open_browser = true       # `coach ui` opens your browser on the one-time login link
```

Remote use, step by step: `tailscale serve --bg 8765` (HTTPS in front of the loopback port), set `allow_remote = true`,
`remote_tls_ack = true`, `allowed_hosts = ["<machine>.<tailnet>.ts.net"]`, run `coach ui` (it still listens on loopback by default:
`host = "127.0.0.1"` is correct behind the proxy), then open the printed `https://…/login#t=…` link.

## Figures worth knowing

* **Group totals.** A category group's usual month is computed by the analytics with the same coverage rule as a category
  (months covered by every account carrying the group's money), never summed in the page; the household figure is the
  analytics' own. The group figure in the overview equals the one in the group's drill-down.
* **Balances.** One balance per account, of the most booked type the bank gives (`CLBD`, `ITBD`, then `XPCD`, `CLAV`, `ITAV`,
  `OPBD`, `OPAV`, `FWAV`). The type is shown next to any non-booked balance (for example Revolut's available balance) and
  the household total says when it mixes types.
* **Previews.** "This transaction", "this merchant" and "memory annotation" previews count the transactions whose final
  category really changes, after the classifier has been re-run with the change applied (including annotations that stop
  or start to match), and name what would still win (an override, a transfer link, a type rule, an annotation).
* **Performance.** The analytics dataset is built once per data version (database file, memory files, taxonomy; invalidated by
  every write and sync) and shared by all requests; slow reads are cached per version. Typical API calls take 2-30 ms; the
  first call after a change takes up to about 1 s (dataset, memory check).

## Known limits

* Documents (loan offers, contracts) cannot be uploaded from the page yet; use `coach memory doc add` / `doc extract`.
* The net-worth history shows only what can be known (see [loans.md](loans.md)): bank balances rebuilt from the transactions, loans from their schedules, manual assets from their own date.
* The coach answers one question at a time and has no conversation memory (each question stands alone).
* The interface is available in English, French and Italian (the language in the header also sets the format of dates and numbers, see [i18n.md](i18n.md)). The migration of the pages is in progress: a page not yet migrated, text sent by the server and category names are still English.
* The offline shell shows the app frame only; data needs the local server.
