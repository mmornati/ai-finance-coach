# Analytics engine (E4)

Every number the app or the coach shows is computed here, by code, from the same inputs: the categorised
transactions, the account list, the latest balances and the memory. Nothing in this package calls a network, a model or
the clock by itself (`today` is a parameter): the same database and memory give the same output.

```
load_dataset(con, memory_dir, today, settings)  ->  Dataset  ->  cashflow(ds) / detect_recurring(ds) / forecast(ds) / ...  ->  result.to_dict()
```

* `src/coach/analytics/dataset.py`: the `Dataset` (transactions with category, source, tags, event, entity; accounts;
  balances; consents; a read-only snapshot of the memory) loaded once per call (about 0.4 s for 4,000 transactions).
* One module per metric: `coverage`, `averages`, `cashflow`, `recurring`, `pricechanges`, `anomalies`, `forecast`,
  `budgets`, `upcoming` (calendar), `goals`, `review`. `api.registry()` lists them: these are the read-only tools the
  finance MCP server (E6-1, `coach.mcp`: see coach.md) exposes through `redacted_registry`, each a thin wrapper `build_dataset` + function + `to_dict()`.
* `src/coach/analytics/commands.py` is the CLI; it only formats.
* Thresholds live in `[analytics]` of `config.toml` (`config.example.toml`, `coach config show`). The rental ones (E15, [rental.md](rental.md)): `rental_reminder_months` (12), `rental_rent_grace_days` (7), `rental_rate_gap_pts` (0.5).

## Result format (for the UI and the MCP tools)

* Every result is a dataclass with `to_dict()`: JSON-safe, keys in a stable order.
* **Money is an integer number of cents inside the code** (no float ever carries an amount) and a **string with two
  decimals in JSON** (`"-1843.27"`). Parse it with a decimal type. Ratios (savings rate, percentages, scores) are
  floats rounded to 4 decimals.
* Signs: a transaction amount keeps the bank's sign (money out is negative). Summary figures called `income`,
  `spending`, `saved`, `budget`, `spent`... are positive magnitudes; `net`, `delta` and balances are signed.
* Every result has `coverage` (the months and accounts the figure is based on, months skipped, notes) and `evidence`
  (transaction keys, or series / anomaly ids) so that an answer can cite what it rests on.
* Dates are `YYYY-MM-DD`, months `YYYY-MM`; date arithmetic is calendar arithmetic (month ends clamp: 31 Jan + 1 month =
  28/29 Feb; no time zones, so daylight-saving changes cannot move a date).
* Only EUR is analysed. A transaction in another currency is left out of every figure and counted in the coverage notes
  (`"N non-EUR transaction(s) left out"`); no conversion is attempted.

## Coverage model (the prerequisite)

The accounts do not share a history window (here: two banks with ~24 months, one from mid-February, one from early
April). Dividing a 12-month total by 12 for a category that only exists on a 7-month account understates it.

* `first` = first booked transaction of the account; `last` = the later of its last booked transaction and its last
  successful sync, never after `today`.
* A month is **covered** by an account when `first <= the 1st of the month`, `last >= the last day` and the month ended
  before `today`. The first month and the current month are partial and never used.
* **Per category**, the months used are those covered by *every account that ever carries that category* (at least one
  transaction in it), at most the last `average_window_months` (12). The result lists the months and accounts it used;
  fewer than `average_min_months` (3) is flagged `low_confidence`; a category with no common covered month is listed
  under `unavailable`, never divided by 12.
* **Household** figures use the months covered by every account that carries income or spending (an account that only
  moves money internally, such as an empty savings pocket, cannot make a month incomplete).
* `coach coverage` prints the table.

## E4-1 / E4-2: averages, income, spending, savings rate

`coach averages`, `coach cashflow [--months 6] [--by purpose|owner] [--owner X --purpose Y --account Z]`.

* **Spending** = every category except `transfer.*` and `income.*`. Transfers between own accounts (also to the
  children's accounts and savings pockets) and to / from people never count as spending or income; their totals are
  shown under `transfers`.
* **Income** = `income.*` except `income.refund`. **Refunds** (positive amounts inside a spending category, and
  `income.refund`) are *negative spending*.
* **Saved** = net outflow of transactions tagged `savings` / `investment` (e.g. a monthly life-insurance plan). They are
  not spending whatever their category.
* **Net** = income - spending (it includes what was saved). **Savings rate** = net / income (null without income);
  `saved_rate` = saved / income.
* One-offs (`one_off`, `exclude_from_averages`) and `capital` items: *included* in the monthly cash flow (it is the real
  cash), shown separately (`one_off_spending`, `spending_ex_one_offs`), and *excluded* from the category averages, where
  they are listed (`excluded`, `capital`).
* A month is `complete` when every flow account of the scope covers it; `totals_complete` sums complete months only.
* The same figures per account `purpose` (main, cards, rental, kids, savings) and per `owner` (joint or a member id):
  children's accounts are household spending but can be separated; the rental account (`purpose=rental`) carries the
  rent and the rental loan.
* **Person views (E14-4).** Besides the account-level `owner` scope, a result can be taken for ONE member: `Dataset.member_view(member)` keeps only the transactions
  ATTRIBUTED to that member (`Tx.person`: manual reassignment > `household.yaml` rule > account owner, see [household.md](household.md)), on any account, plus the accounts they own or spent
  through; the balances (and so the forecast and the balance list) cover only the accounts they OWN. Every function here then works on it unchanged: the API's `?member=`, the
  `member` argument of the MCP tools. A joint account's own spending belongs to `joint`, not to a person.
* `classify report` now uses the coverage-aware averages; `classify report --legacy` prints the former computation
  (the total of the last 12 full months divided by 12, whatever the history of the accounts carrying the category). The
  numbers change where an account has less than 12 months: a category carried only by an account with 7 covered months is
  now divided by 7 (a 7-month mortgage history showed 7/12 of the real monthly amount), and the household average is taken
  over the months every spending account covers instead of over all months of the longest history.

## E4-3 / E4-4: recurring payments and price changes

`coach recurring [list|changes|missing|refresh] [--all] [--json]`. Algorithm in `recurring.py`:

1. group by (direction, key, account); the key is the SEPA creditor id (+ mandate) when the parser found one, else the
   canonical merchant entity;
2. cadence from the median gap between bookings: weekly (5-9 days), biweekly (11-17), monthly (25-35), bimonthly
   (53-68), quarterly (81-101), semiannual (167-197), yearly (350-380); >= 60 % of the gaps must fit and >= 80 % fit or are
   a multiple (a skipped occurrence);
3. amounts: `fixed` when >= 80 % are within +-15 % (`recurring_amount_tolerance`) of the median, or when they move in a few
   steps (price changes); `variable` for bills that vary (`recurring_variable_categories`: energy, water, telecom...);
   otherwise the group is split into amount clusters and tested again (two subscriptions of one merchant);
4. >= 3 occurrences (`recurring_min_occurrences`); yearly accepts 2, with confidence <= 0.5, and only for contract-like
   category groups (`recurring_yearly_pair_groups`: two restaurant bills a year apart are a coincidence);
5. descriptor variants of one service that alternate (`X PARIS` / `X AMSTERDAM`) are joined when the union still fits the
   cadence; the cleaner fix is `coach merchants group`;
6. `next_expected` = last + one cadence step (calendar months); `status` = `ended` when the last payment is older than
   1.5 x the cadence; `overdue_days` counts days past a missed date; expected amount = median of the last 3 (fixed) or 6
   (variable) payments, range = smallest / largest of the last 6; yearly cost = |expected| x payments per year;
7. `links`: a contract (`merchant_match`) or a liability (`payment_match`) of the memory matching >= half of the payments;
   `missing_contract` = a subscription / insurance / energy / loan-like series with no link (the feed of E8).

Ids are stable: `rec_` + hash of direction, account and the FIRST payment of the series (so new payments, price steps and cadence re-estimation do not change it); `refresh` also carries a stored id over to a new series that shares at least half of its payments with it. `refresh` stores the result in
`recurring_series` / `recurring_members` (migration 0011): upsert by id, delete the vanished, idempotent (a second run
changes nothing, `updated_at` included).

**Price changes** (`coach recurring changes [--since DATE] [--confirmed]`): the sequence of amounts of a series is walked;
a change of at least `price_change_threshold_pct` (3 %) and `price_change_min_abs` (0.50 EUR) against the current level
is reported when the next payment stays near the new amount (`confirmed`) or when it is the latest payment (not yet
confirmed); a single odd charge followed by a return to the old level is ignored. Utility-like series are compared over
3 payments against the 3 before with the 20 % `price_change_variable_pct`. Output: date, old / new amount, absolute and
percentage change, `effect` (`costs_more` / `costs_less` / `pays_more` / `pays_less`), yearly impact.

## E4-5: anomalies

`coach anomalies [list|refresh|dismiss ID|undismiss ID] [--all] [--json]`.

| type | rule | severity |
|---|---|---|
| `category_spike` | a closed month far above the category's own history: robust z = (x - median) / max(1.4826 x MAD, 10 % of median, 10 EUR) >= 3.5, excess >= 50 EUR, x >= 1.5 x median; at least 6 earlier covered months; one-offs left out; a category with a median of 0 (intermittent) needs 200 EUR more and is never `high` | high: z >= 8 or +500 EUR; medium: z >= 5 or +200 EUR |
| `duplicate_charge` | same merchant + amount (>= 10 EUR) on the same account within 3 days, last 90 days, outside one recurring series, split parts ignored | high >= 100 EUR duplicated, low < 20 EUR |
| `new_merchant` | a merchant never seen before (all accounts), first payment in the last 60 days, >= 150 EUR, not tagged one_off / capital | high >= 3 x 150 EUR |
| `large_transaction` | a payment >= max(Q3 + 3 x IQR, 3 x median, 100 EUR) of its category (>= 8 payments), last 90 days, not recurring, not already a new merchant | by ratio to the threshold |

Each anomaly has a stable id (`anm_...`), the evidence transaction keys, a deterministic message and a baseline. Dismissals
(`dismiss ID --note`) are stored in the `anomalies` table and survive refreshes.

## E4-6: cash-flow forecast

`coach forecast [--days 90] [--account X] [--events] [--json]`. Model in `forecast.py`:
latest booked balance (CLBD, then ITBD, XPCD, ITAV...) + recurring items replayed from their next expected date + loan
instalments of memory liabilities that no recurring series explains (E9-3: from the loan's amortization schedule, exact due dates and
amounts with the insurance, when it is computable; else the declared `monthly_payment` on the day of `start_date` / `payment_day` until
`end_date`; account from `debited_account` / `debited_from`) - the variable spend. Variable spend = average monthly net outflow not covered by a recurring series
(spending plus irregular transfers, one-offs / capital / savings left out) over the last 6 months covered by the account,
spread evenly; accounts with `purpose=savings` get none; one covered month or less is no history. Internal transfers
between own accounts are series like any other (one per account) and cancel at household level when both legs are
detected. Band: expected +- `forecast_band_z` (1.28 = about 80 %) x the standard deviation of the monthly variable spend
x sqrt(days / 30.44), widened by the amount range of variable bills. Flags: `projected_negative`, `at_risk` (only the low
band < 0), `balance_stale`, `no_balance`, `no_variable_history`. Non-recurring income is not projected (conservative).

## E4-7: budgets

`memory/budgets.yaml` (validated by the memory store; schema in `docs/memory.md`):

```yaml
budgets:
  - id: food-groceries
    category: food.groceries      # or group: food
    monthly: 800
    rollover: true                # the unspent part of earlier months (since `start`) is carried over
    start: 2026-10-01
    owner: joint                  # optional: only the accounts of this owner
    account: CE main              # optional: only this account (uid or label)
```

* `coach budget suggest`: median of the last 6 covered months per category, rounded (5 EUR below 100, 10 below 1,000, then 50).
* `coach budget set TARGET AMOUNT [--rollover] [--owner] [--account] [--start] [--source NAME] [--reason TEXT]`: shows the
  diff first; writes only with `--yes` (or an interactive "y"); `--dry-run` previews; `--propose` queues a proposal
  instead (what the coach / LLM uses: the user applies it with `coach memory accept`). Writes go through the memory
  store: validated, atomic, recorded in the history with the `--source`.
* `coach budget list`, `coach budget status`: spent / available / remaining / percent for the current month (one-offs
  and capital apart), projection to the end of the month (recurring payments still to come + the pace of the
  non-recurring spend so far; plain linear pace in the first 3 days), status `ok` / `at_risk` / `over`, and the biggest
  spending without a budget.

## E4-8: upcoming payments

`coach calendar [--days 60] [--transfers] [--ics FILE] [--json]`: recurring series (next occurrences), liability
instalments and end dates (LOA end), contract renewals / commitment ends / notice deadlines (renewal minus
`notice_period_days`), bank consent expiries, stale manual asset reminders. `--ics` writes an RFC 5545 file with stable
UIDs (the same input gives the same file).

## E4-9: goals

`memory/goals.yaml`: target amount, optional target date, exactly one source (`asset`, `account` or `tag`), optional
`monthly_contribution`, `baseline`, `start`. `coach goals list|status|set` (same preview / `--yes` / `--propose` rules as
budgets). Progress from the asset value, the account balance or the tagged flows; pace = average of the last 6 covered
months (tag / account) or the planned contribution; projected completion = today + remaining / pace; status `achieved`,
`on_track`, `behind`, `overdue`, `no_pace`, `no_data`; `required_monthly` to hit the date.

## E4-10: year in review

`coach review year [YYYY] [--json|--md]`: totals including one-offs (shown separately), by event, top merchants /
entities, biggest increases and decreases of the monthly category average against the previous year (each year over its
own covered months; fewer than 3 covered months in either year = `not_comparable`; averages over fewer than 12 months
are flagged `partial`), income and savings. A year is `partial` when it is not over or some months lack data for an
account that carries income or spending; with no complete month, income and net are flagged as not meaningful.

## Scheduler

`coach schedule run` ends with a warn-only analytics step (`[schedule] analytics = true`): refresh the recurring series
and the anomalies, look at the budgets. It prints `WARNING` lines for new anomalies and over-budget envelopes, logs one
summary, and never fails the job. `coach analytics refresh` runs the same step by hand. Right after it, a second warn-only step stores today's net-worth snapshot and back-fills the
past months (E9-4, [loans.md](loans.md); same `analytics` switch); `coach sync` stores one too.

## Known limits

* Data before the first booking of an account is unknown: a household month is complete only when every account that
  carries income or spending covers it, so an account that started late shortens the common history (reported, never
  hidden).
* Merchant descriptors that vary are only joined by the cadence rule above; group variants with `coach merchants group`
  for exact entities.
* The forecast does not project non-recurring income, tax payments of unusual size or seasonal spending; the variable
  spend is a flat average.
* Amounts in another currency are excluded, not converted.


## Rules added after the review round

* **Read-only reads.** `coach recurring` / `anomalies` / `forecast`... never write; `recurring refresh` (or `--refresh`),
  `anomalies refresh`, `analytics refresh` and the dismissals do. `--as-of DATE` truncates transactions, balances and sync
  dates at that date and refuses every write.
* **Forecast scope.** Series are detected on all accounts whatever the scope. A liability is debited from
  `debited_account` (uid or label, exact) or, failing that, from the account named in the free text `debited_from`
  (accent-folded match). A liability whose account is unknown is charged to the household forecast only, never to a scoped
  one (a warning says so). Variable spend = the MEDIAN of the monthly non-recurring outflow, without payments flagged
  large / new merchant and without early payments that look like instalments of an active series.
* **Recurring.** Grouping keys ignore per-payment references (`NAME*REF`, tokens mixing letters and digits). Same-day
  payments are separate series (lanes by amount); interleaved descriptors of one brand are pooled; a payment of another
  payer (same account, direction, category group, amount within the tolerance, inside the cadence window) is accepted as
  the missed occurrence (`aliases`). Two payments are enough for a yearly or quarterly series in contract-like groups
  (low confidence). Restaurants / shopping / fuel-like categories need 5 regular payments with near-fixed amounts. Next
  dates use the usual day of the month (or month end) and roll weekends to Monday. After a price step the expected amount
  is the latest level.
* **Price changes** skip ended series, only report confirmed raises of at least 5 % for income, use 20 % for bill-like
  categories and can be dismissed (`recurring dismiss-change ID`, table `price_change_dismissals`, migration 0012).
* **Coverage.** An account that carries less than `coverage_min_share` (10 %) of a category's money over the last 12
  closed months is a *minor* carrier: it does not shorten the window (`ignored_accounts`). Lumpy categories (`travel.`,
  `housing.renovation`, `taxes.`) are averaged over at least 12 covered months and flagged below. The averages also give
  the household as the SUM of per-account averages over each account's own months (`household_sum`), flagged when it differs
  from the common-months estimate by more than 10 %.
* **Cash flow context.** `debt_service` (instalments of the memory loans), `loan_principal` (E9-3: the principal part of the
  amortization schedule's instalment due nearest to the debit; else estimated from rate, outstanding capital and instalment; null if unknown) and `savings_rate_incl_principal`; `drawn_unconnected` /
  `sent_unconnected` for internal transfers whose other leg is in no connected account.
* **income.refund** is negative spending everywhere: cash flow (`refunds`), averages (its own row and the household figure),
  budgets (`unallocated_refunds`: it belongs to no envelope).
* **Budgets / goals.** `budget set --id X` refuses to retarget another budget (`--replace` does); owner and account are
  validated when written; rollover counts only months the contributing accounts cover; a tag goal starts counting today;
  goals sharing a source or without start are flagged; NaN / inf are refused in every memory money field.
* **Privacy boundary.** `api.redacted_registry(con, cfg)` is the only registry a model may call (E6): pseudonymous accounts
  (`account-main-1`) and owners (`joint`, `adult-1`, `kid-1`), hashed transaction keys, scrubbed free text, masked
  `employers` / `places` / `schools` of `household.yaml`, anomaly messages rebuilt from structured fields. The CLI keeps raw values.
