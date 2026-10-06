# Loans, mortgage and net worth (E9)

Everything about what the household owes and owns, computed by code from the loan files in `memory/liabilities/`, the manual assets in
`memory/assets.yaml` and the bank data. **A missing figure stays missing**: a loan that cannot be scheduled lists the fields it needs, an
asset without a value is listed as unknown and left out of the total, and nothing inferred is ever written silently.

| Story | What | Where |
|---|---|---|
| E9-1 | the complete loan record, guided CLI / UI forms, contract extraction, **inference** of missing terms | `memory/schemas.py` (`Liability`), `loans/infer.py`, `loans/commands.py`, `memory/documents.py` |
| E9-2 | payments linked to the loan, alerts (missed / changed / extra / wrong account) | `loans/payments.py`, `loans/service.py` |
| E9-3 | amortization schedule, interest per calendar year, forecast / savings rate from the schedule | `loans/schedule.py` |
| E9-4 | net worth (categories, owners), monthly history, chart, `coach networth`, MCP `net_worth` | `loans/networth.py`, `loans/history.py`, migration `0016` |
| E9-5 | early repayment / renegotiation / insurance scenarios on the real schedule | `loans/scenario.py` |
| E9-6 | LOA / LLD end of contract: reminders, residual value, mileage projection, return checklist | `loans/loa.py` |

The annuity maths (instalment, schedule, IRA, renegotiation, prepayment) stays in `skills/loans.py`; the `loans/` package builds on it and
never duplicates it.

## 1. The loan record (E9-1)

`liabilities/<id>.yaml`, validated by `memory.schemas.Liability` (a wrong type, a negative amount, a day outside 1-31, an unknown enum,
`first_payment_date` before `start_date`, a deferral as long as the term or decreasing odometer readings are errors). Unknown keys are kept.

| Field | Meaning |
|---|---|
| `kind` | `mortgage`, `car_loan`, `loa`, `lld`, `consumer_loan`, `bnpl` |
| `lender`, `asset`, `holder` / `holders` | who lends, what it finances, who owes it (member id or `joint`: the net worth by person) |
| `principal` | amount borrowed (EUR) |
| `start_date`, `end_date`, `term_months` | the term is `term_months`, else the whole months from start to end |
| `first_payment_date`, `payment_day` | when the first instalment is due (default one month after the start) and the debit day |
| `rate.type` `fixed` / `variable` / `mixed`, `rate.nominal`, `rate.taeg` | nominal annual rate (%) and TAEG |
| `rate.index`, `rate.margin`, `rate.cap` | variable rate: **stored only**; the schedule uses `nominal` as the current rate and says so |
| `monthly_payment` | total debited per month (insurance included; a lease: the monthly rent) |
| `insurance.monthly` | flat premium (EUR a month) |
| `insurance.rate_pct` + `insurance.basis` | premium as a yearly % of the `initial` capital or the capital still due (`outstanding`) |
| `insurance.provider`, `insurance.delegated` | provider, delegated to another insurer (Loi Lemoine, FR) |
| `deferral.months`, `deferral.kind` | `partial` (interest only) or `total` (nothing paid, the interest is added to the capital) |
| `outstanding`, `outstanding_as_of` | the capital still due from a statement, and its date |
| `debited_account`, `payment_match`, `amount_match` | the account it leaves; regex of its bank label; `{amount, tolerance_pct}` to tell apart loans that share a label |
| `early_repayment_penalty` | the clause (text or EUR) |
| LOA / LLD | `first_payment` (apport / first rent), `residual_value` (option price), `mileage_limit_km` (whole contract), `excess_km_fee` (EUR per km), `initial_km` (odometer at the start, 0 for a new car), `odometer: [{date, km}]` |
| `documents`, `notes` | local paths of the contract PDFs; free text |

**Ways to record a loan** (all validated, previewed, recorded in the memory history):

* `uv run coach loans add <id> --kind mortgage` (guided questions in a terminal, or `--set rate.nominal=3.1 --set start_date=2021-03-05 ...`),
  `coach loans edit <id> [--set path=value] [--unset path]`, `coach loans odometer <id> --km N [--date D]`. The change is shown as a diff and
  written only after a typed yes. `--yes` only skips that prompt for a person at a terminal (stdin AND stdout): without a terminal every memory-writing
  command (loans, subs, budget, goals ...) refuses ("run this yourself in a terminal, or use --propose"), and no flag can be abbreviated (`--ye` is a parse
  error: `allow_abbrev` is off on every parser). The history source is `cli`.
* The **Loans & net worth** page of the web app (Add a loan / the pencil on a card): the same fields, a dry-run preview, source `ui`. The
  lease fields appear for a lease, the rate and insurance fields for a loan, the variable-rate fields for a variable rate.
* **Contract extraction**: `coach memory doc add contract.pdf --kind loan --for <id>` then `coach memory doc extract <doc> --into liability <id>`
  (dry run first). The extraction field set now covers the first payment date, the term, the variable-rate index / margin / cap, the insurance
  rate and basis, the deferral, the first rent, the excess-km fee and the initial odometer. Every value must be verified against a snippet of the
  document and becomes a **proposal** the user accepts in a terminal.

### Inference (`infer_loan`, `coach loans infer <id> [--propose]`)

From the observed instalment P (median of the last 12 matched payments) and any two of (principal C, rate, term n), the third is solved with the
annuity relation `P = C r / (1 - (1+r)^-n)`, `r` = nominal % / 1200:

| Unknown | Solved by |
|---|---|
| principal | `C = P (1 - (1+r)^-n) / r`, rounded to the euro |
| term | `n = -ln(1 - C r / P) / ln(1+r)`, rounded to a whole month; `end_date = start_date + n` |
| rate | **Newton's method** on `f(r) = C r / (1 - (1+r)^-n) - P` (no solution when the instalments total less than the capital), rounded to 0.01 % |

A second route uses the capital still due on a date (`outstanding`, `outstanding_as_of`) and the months left to `end_date` (rate), or the rate
(end date). The start date comes only from `end_date - term`: the earliest payment in the bank data is **never** used (the history is shorter than
the loan). The result carries `inferred: true`, a **confidence** (high = at least 6 steady payments and the insurance accounted for; medium = 3-5, or
the insurance unknown, or a variable rate; low = fewer than 3 or unstable amounts) and the assumptions (the insurance is subtracted when known; when
unknown the capital and the rate are said to be overstated). **Nothing is written**: `--propose` (CLI) or *Queue as a memory proposal* (UI) creates a
sealed memory proposal (source `loans-inference`) that the user accepts themselves with `uv run coach memory accept <id>`.

## 2. Payment linking and alerts (E9-2)

A payment is a debit whose merchant key / description / entity matches `payment_match` (and, with `amount_match`, an amount in its band): the same
linking as the contracts and the recurring series. `coach loans payments <id>` and the loan page list them; each loan also shows its recurring
series (`next_expected`).

| Alert | Raised when |
|---|---|
| `missed_payment` | a due date (the schedule's, on the usual day of the month; else the usual day) plus the **grace period** (`[analytics] loan_grace_days`, default 5) has passed, no matching debit fell in `[due - 4 days, due + grace]`, and the debited account's data are complete through `due + grace` (never when the data stop earlier). Look-back 75 days. |
| `amount_changed` | the last payment differs from the previous one (or from the schedule / declared amount) by more than 2 % and 1 EUR |
| `extra_payment` | a debit at least 1.5 x the instalment (and 100 EUR above it), or a second debit in a month away from the usual day: a possible partial prepayment |
| `wrong_account` | a matching payment left an account other than `debited_account` |

Alerts are insight cards (kind `loan`, Insights page, `coach loans alerts`) with stable ids; a lease also gets calendar entries.

## 3. Amortization (E9-3)

`loans/schedule.py: compute(liability, today)`. Conventions (also in every result's `assumptions`): instalment k is due `first_payment_date + (k-1)`
months, else `start_date + k` months (the day clamped to the month end), on `payment_day` when given; interest = capital before the instalment x
nominal rate / 12, to the cent (half up); the last instalment clears the capital; a **deferral** is interest-only (`partial`) or capitalising
(`total`), the insurance is still paid; **insurance** is shown apart (flat, or % of the initial / outstanding capital) and added to what is
debited; a variable or mixed rate is `approximate`. Two modes: `from_principal` (principal, rate, start, term: the whole table) and
`from_outstanding` (the capital on a date, the rate and the end date or the instalment: the future table only). A **lease owes no capital**
(`not_applicable`). When neither is possible the result lists the missing fields (`coach loans schedule <id> --propose-questions` adds an open
question; `questions_propose` does the same for the coach).

It gives the capital due today, the instalments made / left, interest and insurance paid and still to pay, the total cost, and **interest per
calendar year** (the figure for an Italian 730 mortgage-interest line or a French rental-income declaration for the Pinel loan: check it against the
lender's annual statement). It also feeds:

* the **forecast** and the **calendar**: a liability with no matching recurring series is forecast from the schedule (exact dates and amounts,
  insurance included, `certainty: scheduled`) instead of a flat `monthly_payment` (`assumed`, still the fallback);
* the **cash flow**: `loan_principal` and the savings rate including principal use the principal part of the instalment due nearest to each debit;
* `mortgage_check` (`state.schedule`, the capital due and the months left) and `what_if prepay_loan` (the capital due on the prepayment date, the
  instalments left; a prepayment dated on an instalment day is applied before that instalment);
* `coach memory check` (`schedule_capital_differs`, `schedule_payment_differs`, `lease_end_missing` at info level).

## 4. Net worth (E9-4)

`coach networth [--history] [--months N] [--json] [--record]`, the web page, the MCP tool `net_worth`, `GET /api/v1/net-worth[/history]`.

* **Bank accounts**: the latest balance of the most booked type (CLBD, ITBD ... see analytics); a balance that is not a booked one is counted and
  flagged (`balance_type`); no balance = unknown, never zero. A balance older than 7 days is flagged stale.
* **Manual assets**: `balance` / `value` with `as_of` (stale after `[memory] asset_stale_months`), no value = unknown, `connected: true` = counted
  through its account. Categories: `cash` (current accounts), `savings` (savings accounts, regulated savings), `investments` (employee savings,
  life insurance, securities, pension, crypto), `real_estate`, `vehicles`, `other`.
* **Liabilities**: the declared `outstanding` + `outstanding_as_of` is a statement, so it **wins over the theoretical table** when the two differ beyond
  a tolerance (the larger of 50 EUR and 2 %): it is rolled forward with the loan's rate and instalments (`source: declared_rolled`; used as it is when
  it cannot be rolled, `declared`), the theoretical table staying only as `outstanding_check` (an insight card: early repayment or renegotiation? update
  the loan). Without a declared capital, or when it agrees with the table: the table's capital (`schedule`). Else the declared capital with no
  schedule (stale after `[memory] stale_months`), else **unknown**. The forecast, the calendar and the cash-flow principal follow the same figure. A lease is `excluded` (its remaining rents are shown as a commitment, not added).
* `net_worth` = known assets - known liabilities; `complete` is false as soon as one item is unknown and the page says "known part only"; the MCP tool adds `partial: true`, the unknown count, `known_share` and a
  `caveat` string so the total is never quoted as the whole net worth. By
  owner: the account owner, the asset / loan `holder(s)` (several = `joint`), else `unassigned`.

**History** (`net_worth_history`, migration `0016`, additive): a *snapshot* row is stored at each `coach sync` and scheduled run (one per day,
replaced the same day; `coach networth --record`, the page's *Record today*), and **back-filled** month-end rows rebuild the past, only where it can
be known: bank balances = the latest balance minus the transactions dated after the month end (only when the account's data start before it and the
balance is not older than it), liabilities = the schedule's balance on that date (0 before the loan starts; unknown when no schedule), manual assets
= their recorded value **from their own `as_of` date on** (earlier months list them as unknown). Each month stores `n_unknown` and the unknown items;
the chart draws such months lighter with hollow dots and says what was not counted, and marks (dashed line, `newly_counted`) the month an item is first
 counted, where the total jumps without any change in wealth. Rebuilt months carry a caveat when some balances are available / expected ones (ITAV, XPCD). A back-fill never replaces a snapshot of the same month. Back-fill
uses the transactions table (booked): an available / expected balance (ITAV, XPCD) can differ slightly from it.

## 5. Scenarios (E9-5)

`coach loans scenario prepay|renegotiate|insurance <id> ...` and the *Scenarios* tab of the loan page, built on `skills/loans.py`:

* **prepay** (`--amount`, `--on`, `--penalty`): the capital due on that date and the instalments left come from the schedule. Both options are
  compared: *keep the instalment, finish earlier* (months saved) and *keep the end date, lower the instalment*. For each: interest saved (exact
  schedules before / after), insurance saved, the penalty, net saving, and the break-even (first month whose cumulative interest saving covers the
  penalty; none if never). Penalty: FR mortgage = IRA (the lower of six months of interest on the amount prepaid and 3 % of the capital due before the
  prepayment, if the contract applies it); IT mortgage = none by law (art. 120-ter TUB: a natural person's mortgage for the purchase or renovation of residential
  property); FR consumer / car loan = the L312-34 cap (no indemnity when the amount repaid over 12 months is at most 10,000 EUR, else at most 1 % of the amount
  repaid with more than a year left, 0.5 % otherwise; earlier prepayments of the same 12 months add up); other loans = the amount you give from the contract, else 0.
* **renegotiate** (`--new-rate`, `--variant`, fees): `renegotiation_estimate` on the remaining capital / instalments, with the IRA (rachat, FR), none for
  a surroga (IT) or a same-bank amendment, the break-even and the rule-of-thumb flag.
* **insurance** (`--alternative`, `--fees`): `insurance_delegation_estimate` with the current premium taken from the next instalment of the schedule.

`--save` (CLI) or *Store as an insight* (UI) stores the result as an insight (kind `finding`, skill `loan-scenario`; the title and body name the loan
kind only, never its id or lender). Everything is an estimate and ends with "general information, not financial advice".

## 6. LOA / LLD end of contract (E9-6)

`coach loans lease [id] [--market-value EUR]`, the *End of contract* tab, insight cards, calendar.

* **Reminder** `[analytics] loan_reminder_months` (default 6) before `end_date`: a calendar entry on that date and a card until the end (high severity
  in the last 90 days).
* **Residual value**: the option price; with the user's own dated `market-value` the difference is shown. Nothing is looked up.
* **Mileage**: readings `{date, km}` (`coach loans odometer`, the page). Pace = (latest - `initial_km`) / days since `start_date` (without
  `initial_km`: between the first and the latest reading, two needed, at least 30 days apart); projected odometer at the end = latest + pace x days left;
  `excess_km` = projected contract km - `mileage_limit_km`, cost = excess x `excess_km_fee`. A missing limit, fee or reading is listed, never guessed.
* **Questions**: a lease with no `end_date`, `residual_value`, `mileage_limit_km` or `excess_km_fee` gets the usual "fill" question; a lease ending
  within a year with no reading in 3 months asks for the odometer.
* **Return checklist**: general (notice period, inspection, documents, tyres, mileage proof, insurance and direct debit, buying the car): verify your
  own contract.

## API, CLI, MCP

| | |
|---|---|
| CLI | `coach loans list|show|schedule|payments|alerts|infer|add|edit|odometer|lease|scenario`, `coach networth` |
| API | `GET /liabilities` (schedule summary, capital + source, alerts, lease, suggestions), `GET /loans/{id}`, `GET /loans/{id}/payments`, `POST /loans/{id}/scenario`, `POST /loans/{id}/odometer` (`dry_run`), `POST /loans/{id}/infer/propose`, `GET /net-worth[?history=true]`, `GET /net-worth/history`, `POST /net-worth/snapshot` (session + CSRF on every mutation) |
| MCP (read-only) | `net_worth(history?, months?)`, `loans_overview()`; `mortgage_check` and `what_if` use the schedules |
| Schedule | the daily job stores a net-worth snapshot and back-fills the past (warn-only step `networth`); `coach sync` stores one too |

**Privacy of the MCP tools** (same choke point as every tool): accounts are `account-main-1`, owners `joint` / `adult-1`, assets and loans are
`asset-3` / `liability-2` named by **kind only** (`employee savings plan`, `house`, `mortgage`, `car lease (LOA)`), never by their description, provider,
lender or financed asset; alert evidence is hashed refs; amounts and dates are allowed. Contract numbers, account numbers, payment labels
(`payment_match`) and the contact block are never read. Tests use sentinel strings for each of them (`tests/test_loans_tools.py`).

## Known limits

* A variable-rate loan uses its current nominal rate for the whole term; the bank's table differs when the index moves. Modulated instalments, fees
  rolled into the capital and per-instalment fees are not modelled.
* The schedule assumes instalments debited monthly; quarterly or yearly repayments, a balloon payment and a re-amortisation after a partial
  prepayment recorded at the bank are not modelled (record the new capital and its date: the `from_outstanding` mode takes over).
* A back-filled month uses booked transactions; a balance of another type (available, expected) can differ by pending items. A bank account whose
  data start later than a month is unknown for that month.
* The legal figures (IRA, surroga, Loi Lemoine) are summaries with their source named in the result; verify with your contract.
* The loan templates in `memory/liabilities/_template.yaml` and `memory/README.md` (the household's own files) are not rewritten by the code: the
  fields above are documented here and in `docs/memory.md`.

## Tests

Synthetic data only; every formula has a hand-computed expectation in the test (10,000 EUR at 12 % over 12 months = 888.49, a 100,000 EUR 3 % 240-month
loan, a 15,000 km LOA ...): `tests/test_loans_schedule.py` (annuity, deferral, insurance, dates, interest per year), `test_loans_infer.py` (solving,
confidence, never silent), `test_loans_payments.py` (link, alert timing), `test_loans_networth.py` (composition, owners, unknowns, history, back-fill),
`test_loans_scenario.py`, `test_loans_loa.py`, `test_loans_integration.py` (cash flow, what-if, feed, calendar), `test_loans_cli.py`,
`test_api_loans.py` (auth / CSRF, previews write nothing), `test_loans_tools.py` (MCP privacy with sentinels); vitest: `web/src/pages/Wealth.test.tsx`,
`web/src/components/ItemForm.test.tsx`, `web/src/pages/Insights.test.tsx`.

A rental property financed by a loan is followed on its own page (cash flow, the interest of the return, the net equity and the renegotiate-or-sell indicators, all on the schedule of this document): [rental.md](rental.md).
