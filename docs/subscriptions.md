# Subscriptions & contracts optimizer (E8)

One place for every recurring cost: what it costs per year, whether a contract file backs it, whether the household uses it, whether and
how it can be cancelled, whether a cheaper offer exists, what was decided about it and what that really saved. It builds on E4-3 (recurring
series), E7-5 (`subscription_audit`), E7-6 (`cancellability`, the rules table) and E7-7 (`savings_estimate`, `find-cheaper`). Code:
`src/coach/subs/`. **Nothing here cancels, sends or contacts anything**: the user acts, the app keeps the books.

| Story | What | Where |
|---|---|---|
| E8-1 | the inventory (API, UI, CLI) and the contract-file bootstrap | `subs/inventory.py`, `subs/draft.py`, `coach subs list / show / draft-contracts`, `GET /subs/inventory`, Subscriptions page |
| E8-2 | usage per contract, usage questions, reminders from what the user recorded | `subs/usage.py`, `subs/reminders.py`, `contracts/*.yaml` `usage`, `coach subs usage / usage-questions` |
| E8-3 | cancellation info wired to the rules engine, calendar deadlines from it | `skills/cancel.py` (`cancellation_info`, `notice_deadlines`), `analytics/upcoming.py` |
| E8-4 | alternatives store, staleness, savings by code | `subs/alternatives.py`, table `alternatives` (migration 0014), `coach subs alternatives`, MCP `alternatives_record` |
| E8-5 | cancellation letter / e-mail text, generated locally | `subs/letters.py`, `coach subs letter`, `GET /subs/letter`, `contact` in `household.yaml` |
| E8-6 | decisions and the savings tracker | `subs/decisions.py`, table `decisions` (migration 0015), `coach subs decide / decisions / savings`, MCP `savings_tracker` |

## The inventory (E8-1)

`coach subs list [--json] [--all] [--group G] [--missing-contract]`, `GET /api/v1/subs/inventory`, the **Subscriptions** page (tab
*Inventory*). One row per service, merging the recurring series with the contract files:

- `group`: `streaming_media`, `software_cloud`, `telecom`, `memberships`, `other_subscriptions`, `insurance`, `energy_utilities` (the groups
  of the subscription audit). Loans, mortgages, rent, taxes and school fees are not subscriptions and are not listed.
- cost: `monthly`, `yearly` (the series' yearly cost = |expected amount| x payments per year; monthly = yearly / 12, half-even), `next_charge`,
  `price_history` (the distinct price levels with the date each started) and `price_changes`. A contract file whose payments the bank data
  does not show is a row with `cost_source: contract` (its billing amount per period converted to a month and a year).
- `contract.status`: `on_file`, `missing` (no contract file matches the series: `draftable`), `expired` (every date on file - renewal,
  commitment end - is in the past: the contract may have been renewed or ended; refresh it). `linked_series` lists the series a contract explains.
- `usage`, `cancellation`, `alternatives`, `decision`: see below. `totals` and `groups` add up the ACTIVE services.

### Bootstrap the contract files

`coach subs draft-contracts [--dry-run | --write] [--series REC_ID ...]` drafts a contract file for every contract-like recurring series
without one:

- `kind` from the category group (`insurance_home` / `insurance_car` / `insurance_health` when the category says so, `insurance_other` for any
  other insurance, `energy`, `water`, `telecom`, `streaming`, `software`, `membership`); `holder` is left empty (letters then carry a placeholder); `provider` = the cleaned merchant name; `billing` = the series' amount and period (monthly, bimonthly, quarterly, yearly;
  a weekly, biweekly or semiannual payment has no matching period: billing stays empty); `merchant_match` = a regex of the series' bank
  label, checked to link the series; `start_date` = the first payment seen (a lower bound: the contract may be older); everything else
  null, and one contract per series. Each draft's pattern is tested against EVERY series' payments before it is proposed: a label that
  also matches another series (``^ORANGE\s+SA`` against `ORANGE SA PARIS`, several policies of one insurer) gets an `amount_match` band
  (`{amount, tolerance_pct >= 3}`, a field of the contract schema read by the link) that tells them apart; series that stay indistinguishable
  are merged explicitly into one file; a post-check (`draft.link_report`) flags any series not matched by exactly one contract. A shared label
  also adds "what does this contract cover, and who holds it?" to the open question `fill:contract:<id>`. The open question `fill:contract:<id>` lists the missing fields (the same key as `questions generate`, so never asked twice).
- Default: sealed **proposals** (accept them in the web app or `uv run coach memory proposals`); `--dry-run`: show the drafts, create nothing;
  `--write`: previewed direct writes after a typed yes (source `cli`). A series with a pending draft proposal is not proposed again.
- Web: the **Create contract from this subscription** button previews the diff (`POST /subs/contracts/draft?dry_run=true`) and writes it
  (source `ui`). Coach: the MCP tool `contracts_draft` creates proposals only.

## Usage tracking (E8-2)

`contracts/<id>.yaml`: `usage: {frequency: daily|weekly|monthly|rarely|never|unknown, last_used: YYYY-MM-DD, note: "..."}` (a plain text
`usage:` of older files still loads and counts as recorded). Edit it with `coach subs usage <ref> --frequency ... [--last-used D] [--note]`
(previewed), the inventory's usage form (`PUT /subs/contracts/{id}/usage`), the contract form, or a `memory_propose`.

- **What is measurable, and what is not.** The bank data cannot say whether a service is used. Only two signals exist, both from the user's
  own words: `unused_60_days` (the recorded `last_used` is more than 60 days old, and the service is not known to have stopped) and
  `paid_but_never_used` (the user recorded `never` and the payments continue). A service whose use leaves no trace in the bank data (software,
  streaming, a gym card) has **no signal until the user records something**: usage stays `unknown`, nothing is inferred.
- Reminders appear in the inventory, the insights feed (kind `subscription`), the calendar (`unused_reminder`, `never_used_reminder`, `decision_check`) and the dashboard counters.
- Questions: `coach subs usage-questions [--add]`, `POST /subs/usage-questions`, or the coach's `questions_propose` create `usage:<series>`
  questions; the key is shared, so a question is never asked twice (open, answered or dismissed).

## Cancellation info (E8-3)

Each row carries the rules engine's answer for its contract (or, without a file, for the series with only the first payment as a lower
bound of the start): `can_cancel_now` (true / false / unknown), `earliest_effective_date`, `notice_period_days`, `method`,
`early_termination_cost` (amount, basis, remaining months, free exit date, e.g. a telecom inside its commitment), `legal_basis` (rule name,
law, **source**, **last reviewed**), `unknown` (what to fill), and the "verify with your contract" disclaimer. The country is `household.yaml`
`country` (FR when absent). The rules table (`skills/cancel.py`) is a general summary of consumer law, not legal advice; every rule has its
source and a review date.

The calendar and the insights use the same engine for dates: `notice_deadline` = the contract's own notice before the renewal or commitment
end, or the legal anniversary deadline of French insurance (two months, art. L113-12) when the contract states none; `cancel_window_opens` =
the day a free cancellation opens (Hamon / mutuelle after the first year).

## Alternatives (E8-4)

Table `alternatives` (contract and/or series ref, provider, offer, monthly price in cents, switching costs, features, **source URL (https)**,
`retrieved_at`, `method` find-cheaper | manual, notes, `source` cli | ui | coach-llm). `coach subs alternatives add|list|remove`, the
alternatives table of each subscription in the web app, and the MCP tool `alternatives_record` for the `find-cheaper` skill.

- Validation, the same for every writer: price > 0, `retrieved_at` a date not in the future, a source URL that is a public `https` page
  (required from the coach path; no IP address, no user-info, no localhost), bounded text. The coach path masks household names instead of
  refusing (a refusal would reveal what is on the list), stores at most ten offers per session and does not store the same offer twice.
- **Staleness**: a quote older than 30 days is labelled "outdated, re-check", stays listed, and is never the "current best". The current best
  is the fresh quote with the highest net saving over 12 months.
- `savings` are computed by code (`savings_estimate`: monthly = current - alternative; yearly = x 12; net = yearly - switching costs;
  break-even = ceil(costs / monthly saving) months), never by a model.

## Cancellation letters (E8-5)

`coach subs letter <contract> [--lang fr|it|en] [--channel lrar|email|online] [--holder MEMBER] [--out FILE] [--json]`,
`GET /api/v1/subs/letter`, and **Prepare cancellation letter** in the web app (language and channel selectors, copy, download .txt).

- Generated **locally from templates** (`subs/letters.py`): no model, no network, nothing sent. The user sends it themselves.
- Filled from the contract file (provider, kind, contract number or a visible placeholder), the household holder (first adult, or
  `--holder`) and the optional **`contact`** block of `household.yaml` (`address`, `email`, `phone`; `coach subs contact set`, or the letter
  dialog). The signer is the contract's `holder` (a member id, or `joint` = every adult); without one the letter carries a [contract holder]
  placeholder, never the first adult. The legal references are those of the contract's country, quoted in its language; a letter written in
  another language says so in its notes. The legal basis comes from the rules engine: Hamon (L113-15-2), the mutuelle infra-annual rule (loi 2019-733), non-renewal at the
  anniversary (L113-12), Lemoine (L313-30, a substitution request), telecom (loi Chatel), energy (free choice of supplier), "3 clics"
  (L215-1-1), tacit renewal (L215-1); IT: Bersani (D.L. 7/2007), RC auto (D.L. 179/2012), art. 1899 c.c., Codice del consumo. English
  letters cite the same texts by their French / Italian names. References are limited to those in the rules table.
- Channels: `lrar` (registered letter: sender, recipient, place and date, subject, body, signature line), `email` (subject and signature
  block), `online` (a short message for the cancellation form plus steps).
- Notes tell when to send it (not before a free-cancellation window opens; by the notice deadline), what leaving early costs, "do not cancel
  an energy contract before the new supplier is signed", and what is still a [placeholder].
- **Privacy: the `contact` block is local only.** No MCP tool reads it, `memory_context` and every context builder leave it out, the privacy
  guard refuses its values (address lines and words, postal code, e-mail, phone) in any tool output and the redactors mask them, the
  document-extraction redaction removes them, and the coach cannot propose a change to it. The CLI is gated the same way as `memory accept`:
  `coach subs letter` and `coach subs contact show` fill in / print the household's name, address, e-mail, phone and the contract number
  only when run in a terminal; an agent-driven shell (stdin not a terminal) gets [placeholders] and a note. The contract number is masked from model-facing
  output the same way (no tool returns it). Tests assert it on a household with a sentinel address and e-mail.
- **PDF**: text only. No pure-Python PDF writer is in the dependency set (`pypdf` reads and merges PDFs; it does not lay out text).
- **Review**: the templates are a careful draft of the customary tone and cite only what the rules table sources; they have not been read
  by a lawyer. Snapshot tests (`tests/snapshots/letters/`) pin every template; after a deliberate change regenerate with
  `UPDATE_SNAPSHOTS=1` and read the diff.

## Savings tracker (E8-6)

`coach subs decide <ref> cancelled|renegotiated|switched|downgraded|kept [--before E] [--after E] [--date D] [--effective D] [--note]`
(previewed), the Decision form of each subscription, `GET/POST /subs/decisions`, `GET /subs/savings`, `coach subs savings`, the dashboard
card and the Subscriptions page stats. Table `decisions` (before / after monthly cost in cents, dates, source, `state`).

- **Verification against the bank data** (computed at read time, `subs/decisions.py`): cancelled / switched = VERIFIED when no payment of the
  series is dated after the effective date + 5 days (billing delay) and the series ended or its next payment is more than 7 days overdue;
  CONTRADICTED ("still paid") when a later payment exists; renegotiated / downgraded = VERIFIED when the latest payment on or after the
  effective date is, as a monthly amount, within 2 % (at least 1 cent) of the recorded `after`; CONTRADICTED when it is still the old amount
  or another amount; otherwise PENDING with a check date, from which a reminder (insights card, calendar item) appears.
- **Realised savings count verified decisions only**: monthly saving = before - after; since the decision = monthly saving x whole calendar
  months from the first month AFTER the last payment made within the grace window (else the effective date) to today. Positive amounts
  (refunds) after a cancellation are not payments. A decision on a contract that covers several series is `ambiguous` (no check, a reminder)
  until it is recorded on one named series. Pending decisions are reported apart as "claimed, not
  confirmed by the bank data". `kept` records a decision with no saving.
- **The coach only proposes.** `decision_propose` stores a `proposed` row (source `coach-llm`) that does not count; the user confirms it in
  the web app or with `coach subs decisions confirm ID` (terminal only, like accepting a memory proposal). `savings_tracker` is read-only.

## MCP tools (docs/coach.md has the full catalogue)

| Tool | Writes | What |
|---|---|---|
| `subscriptions_inventory` | no | the redacted inventory: costs, contract status, usage as recorded, cancellation rules, alternatives, latest decision |
| `savings_tracker` | no | decisions, their verification and the realised savings |
| `alternatives_record` | an alternative (validated) | store one sourced, dated offer for a service; savings computed by code |
| `decision_propose` | a PROPOSED decision | not counted until the user confirms it |
| `contracts_draft` | memory PROPOSALS | contract drafts for series without a contract file (no file name or provider in the answer) |

Refs: a service is addressed by the `ref` the inventory returned (a `rec_` series id, or `contract:<pseudonym>` for a contract file with no
payments in the bank data). A name or a real contract id is refused with the same neutral message as an unknown one (no oracle). Provider
names pass through the household redactor and are wrapped as `untrusted_text`; stored offer text (`offer`, `features`, `source_url`) is
third-party text too and is wrapped and scanned for instruction-like text when it comes back.

## Gaps and limits

- Usage is only what the user records; nothing is measured from the bank data beyond "still paid".
- The savings tracker needs the recurring series to exist: a decision about a series the detector no longer lists stays pending ("not found").
- `alternatives_record` returns only an id, the price and the computed savings: provider and offer text are not echoed to the model.
- Letters are text only; a postal address of the provider is a placeholder (the app does not look it up).
- Insurance other than home / car / health is `insurance_other`: the Code des assurances anniversary rule (L113-12), no Hamon; `water` has no
  supplier-switching rule and is never given the energy basis.
- Rules and templates cover FR and IT; other countries get the generic French / Italian flow only if `country` is set.
- `.claude/settings.json` (not editable by this change) has no deny rule for `coach subs decisions confirm` or `coach subs contact`: the
  commands refuse / mask without a terminal, but adding `Bash(*subs decisions confirm*)` and `Bash(*subs contact*)` to the deny list is
  recommended. A Bash-capable agent can still read `memory/household.yaml` directly, as it can any memory file (see CLAUDE.md).
- Accepting a coach-proposed decision is a CLI / web action; the `.claude/settings.json` deny list does not (and cannot, from this change)
  list `coach subs decisions confirm`: the command refuses to run without a terminal, like `memory accept`.
