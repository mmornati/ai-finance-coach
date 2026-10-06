# Alerts and the weekly digest (E10)

The app tells you about what matters without you opening it, and stays quiet otherwise. One **alert engine** turns signals the app already
computes into **events** with stable ids; the **in-app alerts center** shows them; **channels** (all optional, all OFF by default) can also push
a short message; a deterministic **weekly summary** is built once a week. Numbers come from code, never from a model.

| Story | What | Where |
|---|---|---|
| E10-2 | the engine: signals -> events, stable ids, dedupe, escalation, `alert_events` | `alerts/signals.py`, `alerts/engine.py`, `alerts/store.py`, migration `0017` |
| E10-4 | noise control: per-kind toggles, minimum severity, quiet hours, weekly budget, digest-only kinds, ack / snooze / mute | `alerts/settings.py` (`[alerts]`), `alerts/engine.py`, `coach alerts ...` |
| E10-1 | channels: in-app feed, macOS, ntfy, e-mail (SMTP), Telegram; the privacy guard of the external ones | `alerts/channels.py`, `alerts/messages.py` |
| E10-3 | the weekly summary (+ teaser to channels) | `alerts/digest.py` |
| UI | alerts center, read-only channel view with dry-run tests, dashboard card and nav badge | `web/src/pages/Alerts.tsx`, `api/routes/alerts.py` |

## What raises an alert

Everything is evaluated by `coach alerts check` and at the end of `coach schedule run` (a warn-only step: whatever happens there never fails
the daily job). Nothing is computed twice: the engine reads the same functions as the rest of the app (the consent lifecycle, the sync log, and the
cards of the insights feed).

| Kind | When | Severity | Dedupe key (what "the same alert" means) |
|---|---|---|---|
| `consent` | a bank consent crosses D-14 (medium), D-3 (high) or has expired / been revoked (high) | medium / high | session + threshold: three successive events |
| `sync_failing` | an API account's last N syncs all failed (`[alerts.thresholds] sync_failures`, default 3; high at 2 x N) | medium / high | account + the first failure of the streak (a new streak is a new event) |
| `unusual_charge` | an anomaly (spending spike, duplicate charge, new merchant, large payment) of severity high or medium that you did not dismiss | as the anomaly | the anomaly id |
| `price_increase` | a CONFIRMED price increase of a recurring payment that you did not dismiss | medium | the price-change id |
| `low_balance` | the forecast says an account (or the household) goes negative (high) or may run short (medium); the projected date is in the text | medium / high | account + negative / at risk (the date moving a day is not a new alert) |
| `budget` | a budget is over (high) or on track to overrun (medium) | medium / high | budget + month |
| `loan_alert` | a missed or changed instalment, a payment from the wrong account, the schedule differing from the declared capital | as the loan alert | the loan alert id |
| `loa_end` | the LOA / LLD end-of-contract reminder, or a projected mileage over the limit | medium / high | the reminder id |
| `scheme_end` | the commitment of a rental property's tax-incentive scheme ends within `[analytics] rental_reminder_months` (12) and no extension decision is recorded (E15-3); steps at 12, 6 and 3 months, up to six months after the end | low / medium / high | the property + the end date + the step |
| `scheme_check` | the lease rent is above the rent cap, or the tenant income above the limit, YOU declared on the property (E15-3) | medium | the property + the two figures |
| `rent_missing` | a closed month with no rent although the account data cover it, or this month's rent has not arrived after the usual day + `rental_rent_grace_days` (E15-2) | medium / low | the property + the month |
| `unused_subscription` | the "unused for 60 days" / "never used but still paid" reminders (from your own record) | medium | the contract / service |
| `contract_notice` | a notice deadline within 30 days | medium / high | the deadline |
| `decision_contradicted` | a cancelled / downgraded service is still charged (verified against the bank data) | high | the decision |
| `kid_budget` (E14-6) | a child's weekly / monthly budget is close (80 %, low) or over (medium), or a payment of the last 7 days stands out against the child's usual ones (low) | low / medium | the kid budget + its period start, or the unusual payment |

`scheme_end`, `scheme_check` and `rent_missing` (E15) are ordinary kinds whose text is LOCAL (the property's id and the figures are in the in-app title and body): an external message about them is still the fixed vocabulary of `alerts/messages.py` ("Rental scheme commitment ending" ...), a count and a severity, never the property, a figure or a date (tested with sentinels, [rental.md](rental.md)).

`kid_budget` is **local only**, like `llm_usage_high`: no channel (ntfy, e-mail, Telegram, the macOS notification) ever receives it, not even as a count, and the weekly digest leaves it out, so no
child's name or amount leaves the machine through an alert. Its wording is gentle and may name the child because it stays in the local feed ([household.md](household.md)).

## Events: ids, dedupe, escalation

* **One event per situation.** The id (`alr_` + 12 hex) is a hash of the dedupe key, so no merchant or account name is in it, and running the
  check twice, or ten times, changes nothing.
* **Never re-sent.** Each channel the event was sent to is recorded with the severity it was sent at. An event goes to a channel once;
  it goes again **only when its severity goes up** (a budget at risk becomes over). A severity that comes back down is not an escalation and the
  stored severity is the highest reached.
* **Acknowledged** (`ack`) holds an event until an escalation, which reopens it. **Snoozed** (an event, or a whole kind including the events that
  arrive meanwhile) holds it until the date, and an escalation does not break a snooze. **Muted** kinds keep their events as `suppressed`.
* **Resolved.** When the signal disappears the event is marked resolved and kept as history. If it comes back within 7 days (a flapping
  signal) it is the same event, unresolved and not re-sent; after 7 days it is a new occurrence.
* Statuses: `new` (in the app only), `sent` (a channel got it), `acked`, `snoozed`, `suppressed` (muted kind, or under `min_severity`).

## Noise control (`config.toml [alerts]`)

```toml
[alerts]
enabled = true                  # evaluate and keep the events (the in-app feed)
min_severity = "low"            # below it: kept as 'suppressed', never sent outside the app
external_detail = "minimal"     # what ntfy / e-mail / Telegram may say: minimal | summary (see Privacy)
quiet_hours = "22:00-08:00"     # local time; no channel sends in the window, held alerts go out after it ("" = off)
max_per_week = 7                # messages per channel in a rolling 7 days (a batch of events is one message); 0 = nothing outside the app
disabled_kinds = []             # kinds not evaluated at all
digest_only_kinds = []          # kinds shown in the app and the weekly summary but never sent one by one
weekly_digest = true
weekly_digest_day = "mon"
weekly_digest_to_channels = false
app_url = ""
[alerts.thresholds]
sync_failures = 3
anomaly_min_severity = "medium"
```

Wrong VALUES (an `http://` URL, a TLS mode other than `starttls` / `ssl`, an unknown kind, a typo in a key) stop the config from loading with
a clear message. An ENABLED channel with a missing field or secret is only reported (`coach alerts channels`) and skipped, so a half-written
config never stops the daily job. A failed send is recorded (the exception class only, never a message that could echo a secret), the events
stay pending and are retried at the next check; failed sends do not use the weekly budget.

Runtime state, changed from the CLI or the app (database only, not the config): `ack`, `snooze` (an event or a kind), `restore`, `mute-kind`.

## Channels

| Channel | Config | Secret (secrets store) | What it does |
|---|---|---|---|
| in-app feed | always on | none | the `alert_events` table: alerts center, dashboard card, nav badge, `/insights` count; the weekly summary is an insight |
| macOS | `[alerts.macos] enabled` | none | local notification through `osascript`; the text is passed as ARGV to a fixed script, never interpolated into it |
| ntfy | `[alerts.ntfy] enabled, url` | `ntfy_token` (optional, protected topic) | HTTPS POST to your topic URL (ntfy.sh or self-hosted); `https://` only, no credentials in the URL, no redirect followed |
| e-mail | `[alerts.email] enabled, host, port, tls, username, from, to` | `smtp_password` | SMTP over STARTTLS or SSL (`tls` is required; a connection without TLS is refused) |
| Telegram | `[alerts.telegram] enabled, chat_id` | `telegram_bot_token` | HTTPS POST to the fixed `https://api.telegram.org` |

Store a secret with `uv run coach config set-secret smtp_password` (Keychain; an environment variable `COACH_SMTP_PASSWORD` also works).
**Enabling a channel is done by you, in `config.toml`**: the app, the API and the coach never turn one on. `coach alerts channels` shows what is
enabled and ready; secrets are never printed.

`coach alerts test-channel NAME --dry-run` prints exactly what a test message would be (URL, headers, body, with tokens masked), built from
invented SAMPLE events in the current detail mode, and sends nothing. Without `--dry-run` it sends for real, and only when the channel is enabled
and ready, from an interactive terminal and after a typed yes (there is no `--yes`). The web app's "Test (dry run)" buttons call the dry run only.
`coach alerts check --dry-run` shows what each enabled channel would receive for the real current alerts, writes nothing and sends nothing.

## Privacy of what leaves the machine

Every send goes through the egress gate (E11-1, [privacy.md](privacy.md)): the journal records the host and size, never the message; `[privacy] local_only` / `offline` make ntfy, e-mail and Telegram "not ready" (the reason is shown by `coach alerts channels`; the macOS notification stays).

ntfy, e-mail and Telegram are third-party transports. Their messages are built from a fixed vocabulary and numbers, never from the event's own
text (which may name a merchant, a bank or a person), and they pass the **same privacy guard as the finance tools** before anything is sent.

`external_detail = "minimal"` (default), the whole message:

```
Coach: 2 new alerts (1 high). Open the app.
```

`external_detail = "summary"`: the kind, the severity and, for four kinds, an amount rounded to the nearest 10 EUR (100 from 1000 up). A kind is
one of eleven fixed labels:

```
Coach: 2 new alerts (1 high).
- Unusual charge (high), about 150 EUR
- Bank consent expiring or expired (medium)
Open the app.
```

Never in either mode: an amount in `minimal`; any merchant, account label, bank name, person, member, employer, town, IBAN, e-mail, phone, address
or contract number; balances; the event's own title or text. The title of every external message is the fixed `Coach alerts`.

The guard (`coach.mcp.guard.PrivacyGuard`) is built from the memory and the database: members and aliases, account holders, declared and derived
employers, places and schools, account labels / uids / IBANs, the household `contact` block, contract numbers, plus the bank names; it also refuses
IBAN-, e-mail-, path- and key-like strings. A `summary` the guard refuses is sent as `minimal`; a text the guard refuses even then, or a guard that
cannot be built for a `summary`, is not sent (or degrades to `minimal`). Tests plant sentinels of every kind in the data (member names, a merchant
person name, IBANs, the contact block, the employer, towns, a contract number, bank and merchant names) and assert that none appears in what each
transport receives, in both modes, for every alert kind.

The **local macOS notification** may carry the alert's title (a bank or merchant name: it stays on this machine) but IBAN-like values, e-mail
addresses and long numbers are masked.

Remaining exposure, stated plainly: the ntfy topic URL / Telegram chat id / e-mail address are yours and the provider sees them; the provider sees
the time, the count of alerts and, in `summary`, the kinds and rounded amounts. On a PUBLIC ntfy.sh topic the topic name is the only secret: use a
long random one (or a self-hosted server with an access token).

## The weekly summary

Built locally (`coach alerts digest`, or the daily job when `[alerts] weekly_digest = true`, once a week since `weekly_digest_day`), all figures
computed by code:

* last week (the 7 days ending yesterday) against a usual week (mean of up to 8 earlier weeks, needs 3), the top categories;
* the payments and deadlines of the next 7 days;
* the open alerts; the budgets over or at risk; the savings tracker (realised, pending, contradicted);
* notes when the data may be incomplete (no recent transaction) or there is not enough history;
* when `[coach] schedule_weekly = true` produced a coach digest in the last 7 days, its text under a heading that says the model wrote it.

It is stored as an insight (`kind = digest`, skill `weekly-local`, backend `code`) in the in-app feed. With `weekly_digest_to_channels = true` the
enabled channels get a TEASER, never the summary: `minimal` = `Coach: your weekly summary is ready. Open the app.`; `summary` =
`Coach weekly summary: about 200 EUR spent last week (usual week about 110 EUR), 0 open alerts. Open the app.` Each channel gets a given
summary once, through the same guard, quiet hours and weekly budget as alerts.

The link at the foot of the summary is `http://<[ui] host>:<[ui] port>/alerts`. It works on the machine that runs the app, and from another device
only through your own Tailscale / VPN set-up (`[ui] allow_remote`, see `docs/ui.md`); `[alerts] app_url` replaces it, also in external messages
(default: none, just "Open the app.").

## Commands

```
uv run coach alerts check [--dry-run] [--no-send] [--json]     # evaluate, keep the events, send to the enabled channels
uv run coach alerts list [--status S] [--kind K] [--severity V] [--all] [--json] | show ID
uv run coach alerts ack ID | --all
uv run coach alerts snooze ID|KIND --days N | restore ID|KIND | mute-kind KIND | unmute-kind KIND
uv run coach alerts channels [--json]
uv run coach alerts test-channel ntfy|email|telegram|macos --dry-run
uv run coach alerts digest [--save] [--send] [--dry-run] [--force] [--json]
```

## API (`/api/v1`, session cookie, CSRF on every POST)

`GET /alerts` (filters `status`, `kind`, `severity`, `include_resolved`), `GET /alerts/summary` (the badge), `POST /alerts/check` (evaluates and keeps
the events, sends nothing), `POST /alerts/{id}/ack|snooze|restore`, `POST /alerts/kinds/{kind}/mute|unmute|snooze|wake`, `GET /alerts/channels`
(read-only), `POST /alerts/channels/{name}/test` (a DRY RUN: the exact message, `sent: false`), `GET /alerts/digest` (a preview, nothing stored).
There is no endpoint that enables a channel, sends, or reads a secret.

## Tables (migration 0017, additive)

`alert_events` (id, kind, severity, created, updated, last_seen, resolved_at, title, body, payload (JSON, local), status, snoozed_until, acked_at,
channels_sent (JSON {channel: {at, severity}}), escalations), `alert_deliveries` (one row per message a channel was handed: the weekly budget and
"a digest is sent once"), `alert_kind_prefs` (runtime mute / snooze per kind).

## Limits and honest gaps

* No real channel was exercised: every transport is tested with fakes. The HTTP helper, the SMTP helper (TLS handshake calls) and the osascript
  call are tested with faked sockets / runners; a first real `test-channel` from your terminal is the true check.
* Quiet hours and the weekly budget hold alerts back, including a high one (there is no "high bypasses the limit" switch).
* `price_increase` alerts follow the analytics' price-change detection and can be numerous on categories that are not subscriptions (groceries
  detected as a series); use `digest_only_kinds` or `mute-kind` if so.
* The sync-failure alert counts failed attempts, not days; a day with several retries counts several.
* The weekly summary's "usual week" is a plain mean of up to 8 weeks, one-off payments included.
* `[notify] macos` (consent warnings only, E1) is superseded by `[alerts.macos]` when that is on, to avoid two notifications.
* The pages were verified with vitest and the built bundle, not in a browser.

## Follow-ups after review (defaults and safeguards)

* **Quieter defaults.** `digest_only_kinds = ["price_increase", "unusual_charge"]` (shown in the app and the weekly summary, not sent one by one; set `[]` to send
  them) and `external_min_severity = "medium"` (no channel sends below it). `price_increase` alerts are for EXPENSE series only (never rent received or pay).
* **No backlog.** The first time a channel is seen enabled and ready, the alerts that already exist are marked as already sent to it (`alert_channel_state`,
  migration 0018) and only what arrives afterwards is sent; an escalation of an old alert is still sent. `check --dry-run` shows the count. (Alerts created in
  that very run are new and are sent.)
* **Sending context.** `coach alerts check` and `coach alerts digest --save --send` send to channels only from a terminal; without one (an agent, a pipe) they store
  and say so. The scheduler (`schedule run`) sends as configured.
* **No host name leaks.** SMTP uses `local_hostname="localhost"` and a `@coach.invalid` Message-ID; nothing reads the machine's name. `coach alerts channels` /
  `config show` warn when `[alerts] app_url` is not localhost, an IP or a host in `[ui] allowed_hosts` (that name is written into every external message).
* **Web previews** also mask the ntfy topic, the e-mail from / to / SMTP host / user and the Telegram chat id.
* **Digest "usual week"** compares VARIABLE spending only: payments of recurring series (subscriptions, insurance, energy, loan instalments) are left out, except
  the discretionary groups (food, shopping, leisure ...); the summary says so.
