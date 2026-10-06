# Rental property under a tax-incentive scheme (E15)

See the real return of a rental investment and never miss an obligation of the scheme it sits in. Everything is computed by code from the bank flows of the
property's account, the amortization schedule of its loan (E9) and the facts YOU recorded in `memory/assets.yaml`. **A fact that is not recorded stays
missing** (it is listed and becomes an open question); **a figure you typed** (rent cap, tenant income limit, reduction rate, market rate, valuation) is used as
given. Nothing is looked up on the web and nothing is guessed. The tax-year figures are candidates, not a return; the indicators are facts with a neutral
reading, never a recommendation.

| Story | What | Where |
|---|---|---|
| E15-1 | a property account (purpose `rental`) linked to a `real_estate_rental` asset; every flow of it labelled to the property by generic French rules | `rental/model.py`, `classify/rules.yaml`, `classify/taxonomy.yaml` |
| E15-2 | monthly cash flow, yearly P&L, effort d'epargne, vacancy months | `rental/cashflow.py`, `coach rental cashflow / pnl`, `/rental`, `rental_overview` |
| E15-3 | the scheme commitment: start, length, end, rent cap and tenant income checks, reminders, extension decision, missing facts as questions | `rental/scheme.py`, `coach rental scheme / extension`, alert kinds `scheme_end`, `scheme_check`, `rent_missing` |
| E15-4 | tax-year figures: micro-foncier vs reel, loan interest from the schedule, scheme reduction candidate, documents checklist | `rental/taxyear.py`, `coach rental tax`, `tax_candidates` |
| E15-5 | renegotiate-or-sell indicators: loan rate vs the market rate you entered, end of the commitment, net equity | `rental/indicators.py`, `coach rental indicators`, `/rental` |

There is no migration: the facts are memory (files you can read and edit), everything else is computed, and the alert events use the existing `alert_events` table.

## 1. The property and its account (E15-1)

A property is an asset of kind `real_estate_rental` in `assets.yaml`; its id is the **property id** (several properties are supported; keep the id free of an address or a town: models see it only as
`asset-N`).

```yaml
assets:
  - id: rental-flat-1
    kind: real_estate_rental
    account: Main rental account        # uid or label of its bank account (purpose: rental)
    loan: rental-loan                   # the loan file that financed it (else a loan whose `asset` is this id, else the mortgage debited from its account)
    scheme: pinel                       # the scheme's NAME: free text, data only
    value: 200000                       # what you believe it is worth today, with as_of
    as_of: 2026-09-01
    rent_monthly: 620                   # the lease rent
    purchase_price: 190000
    purchase_date: 2021-02-01
    commitment:                         # E15-3, every figure is YOURS (the deed, the scheme's table)
      start_date: 2021-03-05
      years: 9
      surface_m2: 40
      rent_cap_monthly: 600             # or rent_cap_m2 (EUR a month per m2, as you computed it) with surface_m2
      tenant_income_limit: 30000
      tenant_income: 28000
      reduction_rate_pct: 18            # total over the commitment (E15-4)
      reduction_base_cap: 300000        # optional ceiling of the eligible price
      reduction_schedule:               # optional: a scheme that spreads its reduction unevenly (years at a yearly % of the price); wins over everything
        - { years: 9, yearly_rate_pct: 2 }
        - { years: 3, yearly_rate_pct: 1 }
      reduction_first_year: 2021
      extension: { decision: extend, years: 3, additional_rate_pct: 6, decided_on: 2029-06-01 }   # E15-3
    market_rate: { rate_pct: 3.1, as_of: 2026-09-30, source: a quote }                           # E15-5, typed by you
    vacancies:                          # E15-2: periods you know it was empty
      - { start: 2026-02-01, end: 2026-02-28, note: works }
```

`coach rental add / edit` and the Rental page write these fields (validated, previewed, recorded in the memory history). The older `pinel_commitment_years` still gives the length when no
`commitment.years` is set.

**The account.** The asset names it (`account`). When it does not, and exactly one property is still without an account and exactly one account has `purpose: rental` that no property names, that account
is used and reported as `only_one` so you can confirm it. A declared account that is not in the data is reported (`memory check`: `rental_unknown_account`). Several properties = several accounts; two
properties on one account need the tag below.

**Everything it carries is a property flow.** The shipped, generic French rules (no brand) put the usual flows in the property's categories; the loan instalment of an account whose purpose is `rental`
was already `housing.rental_property_loan`:

| Flow | Category | Rule (generic words) |
|---|---|---|
| rent received | `income.rental` | `LOYER` coming in, unless the label says deposit, guarantee, caution, refund or regularisation; a property manager's name coming in (existing) |
| loan instalment | `housing.rental_property_loan` | type rule of a rental account; the linked loan's own payments count whatever the account |
| co-ownership charges | `housing.property_charges` | `SYNDIC`, `COPROPRIETE`, `CHARGES DE COPRO` |
| property tax | `housing.property_tax` (new) | `TAXE FONCIERE` (before the public-finance rule: it is paid to the tax office but is a cost of the property) |
| management fees | `housing.property_management` (new) | `GESTION LOCATIVE`, `GERANCE`; `FRAIS` / `HONORAIRES DE GESTION` only with LOCATIVE, LOYER, GERANCE or IMMOBILIER in the same label (a bare fee is also a bank, life-insurance or PEA fee) |
| insurance | `housing.property_insurance` (new) | `PNO`, `PROPRIETAIRE NON OCCUPANT`, `GLI`, `GARANTIE LOYERS IMPAYES` |
| works | `housing.renovation` / `housing.maintenance_diy` | your annotations (a contractor is a name, not a rule) |

An older copy of the taxonomy gets the three new leaves with `uv run coach taxonomy merge-package` (it never overwrites your edits). What the rules do not recognise stays in **other flows** and is listed
by `coach rental flows` (and the Rental page) so you can label it with a memory annotation; the account's own bank fees stay in other flows but are not asked about. A cost paid from ANOTHER
account counts for the property when a memory annotation gives it the tag `property-<id>` (for example the property tax paid from the main account). The owner's own transfers into the property
account (`transfer.*`) are never rent and never a cost: they are shown apart (`owner_in`).

## 2. Cash flow, P&L, effort and vacancy (E15-2)

All amounts are exact cents, money in the API / tools is a decimal string. A closed month is covered when the account's data cover it entirely (the coverage model of E4); a month that is not is shown but
not averaged and never counted as a vacancy.

* **Monthly cash flow**: `rent - loan - charges - fees - taxes - insurance - works - other`. The loan is a CASH flow (principal included). The month's **effort d'epargne** is its shortfall, `max(0, -net)`: what you
  had to add. The page shows the average of the last twelve complete months.
* **Yearly P&L** (`coach rental pnl --year Y`): the sum of the closed months of the year, the number of months expected and found, the months that are not fully covered, and the loan of the year split
  into interest, borrower insurance and principal from the E9 schedule. The **economic result** takes the principal out (`rent - other costs - interest - borrower insurance`). The gross yield on the declared value appears for a complete year.
* **Vacancy**: once the property is let (the first rent, or the commitment start), a closed month is `received` (at least half of the expected rent), `partial`, `late_paid` (no rent but the next month holds about two
  rents), `declared_vacancy` (a period you declared), `unknown` (data incomplete) or `missing` (nothing explains it). The expected rent is the declared `rent_monthly`, else the median of the rents seen. The
  month in progress is `pending` and only becomes `late` after the usual rent day plus `[analytics] rental_rent_grace_days` (7).

## 3. The scheme commitment (E15-3)

The model is generic: the scheme's **name is data** (`scheme`), its length is `commitment.years` (6, 9 or 12 for a Pinel-type scheme: another number is accepted, a Pinel name with another number is a warning),
and the rent cap, the tenant income limit, the tenant's reference income and the reduction rate are what you typed from the deed or the scheme's own table.

* **End date** = the day before the anniversary of `commitment.start_date` + `years` (or `commitment.end_date`, which wins). With a recorded extension (`extension.decision: extend`, `years`), the effective end moves by
  those years. Check the date against your deed.
* **Reminders** run while the extension decision is `undecided`: from `[analytics] rental_reminder_months` (12) before the end, then at 6 and 3 months, and up to six months after the end. A recorded decision (extend or not extend, `coach rental extension`
  or the Rental page) stops them. They are insight cards of kind `rental` and alert events of kind `scheme_end` (low above six months, medium from six, high from three or once ended).
* **Rent cap**: `rent_cap_monthly`, or `rent_cap_m2 x surface_m2`, against the lease rent (`rent_monthly`; the rent received only when no lease rent is declared, since it may be net of fees). Above the cap = an insight
  card and an alert event `scheme_check`. **Tenant income**: `tenant_income` against `tenant_income_limit`, same treatment.
* **Missing facts** (the account, the loan, the value, the rent, the price and date, and for a property under a scheme the start, the length, the cap, the income limit, the reduction rate) are listed by
  `coach rental scheme`, reported by `memory check`, and generated as ONE open question per property (`fill:rental:<id>`, `coach questions list`); they resolve by themselves once the facts are recorded.

Alert content is LOCAL (the property id appears in the title and body). External channels (ntfy, e-mail, Telegram) keep their minimal vocabulary: the kind label ("Rental scheme commitment ending",
"Rental scheme condition to check", "Rent not received"), a count and a severity, never an amount, an id or a figure (tested with sentinels).

## 4. The tax-year summary (E15-4)

`coach rental tax [ID] --year Y` (income year Y, declared in the spring of Y+1; default: the year to declare next), `GET /rental/{id}/tax`, the Tax year tab and the `tax_candidates` tool (a `fr-revenus-fonciers`
candidate per property, and the Pinel candidate computed from your declared price and rate when you gave them). France only; the Italian return is not modelled.

* **Micro-foncier**: the household's gross rents (all properties together, to compare with the ceiling) less the flat abatement: the taxable figure.
* **Reel**: gross rents less the costs the return lets you deduct: management fees, insurance, property tax, co-ownership charges, works, **loan interest and borrower insurance from the amortization schedule** of the linked loan (when the loan is
  unknown the interest is listed as UNKNOWN, never estimated, and the net is flagged as an upper bound). The loan principal and your own transfers are never deducted. Co-ownership charges, property tax and works are
  upper bounds (only part may be deductible: the syndic's statement and the invoices decide), and the interest is the theoretical table (report the lender's annual statement).
* **The lower of the two** is named, with the difference. Choosing the real regime has consequences over several years: verify with the tax office or an adviser.
* **Scheme reduction** (a candidate), ONE function (`rental/reduction.py`) shared by the page, the tool and `tax_candidates`. The eligible price is `purchase_price` capped by your `reduction_base_cap` and, for a Pinel, by 300,000 EUR and (when `surface_m2` is recorded) 5,500 EUR/m2, whichever is lowest. The yearly shape, in order: your `reduction_schedule`; for a Pinel the known split (6 and 9 years even at 2 % a year for the classic rates; 12 years: 9 years at the 9-year rate then 3 years at the extra rate, 21 % in all, i.e. 5,000 a year then 2,500 on 250,000 EUR; reduced rates for a 2023 or 2024 purchase keep the 9 + 3 pattern; dated `LAST_REVIEWED`, verify); otherwise your `reduction_rate_pct` spread evenly, flagged as an ASSUMPTION. It starts at `reduction_first_year` (else the purchase year, else the commitment start); an extension with its own `additional_rate_pct` adds years. Outside the window the candidate is 0. It lowers the tax, it is not a deduction from the rental income.
* **Documents checklist** (manager's annual statement, leases, the property tax notice, the syndic's statement with the recoverable split, insurance receipts, invoices, the lender's statement, bank statements; and for a scheme the deed, the
  scheme's own declaration, the tenant income evidence). The page lets you tick them for yourself (not saved).

The reel regime also shows two dated candidate lines: the EUR 20 flat management allowance per property, and, when the result is negative, the **land deficit** (EUR 10,700 a year imputable on the global income for the part not due to the loan interest, the interest part and the excess carried forward on later rental income). The ceiling (15,000 EUR) and the abatement (30 %) are those known when the module was written (`taxyear.LAST_REVIEWED`): verify them. Every result carries the wording of `coach.disclaimers` ("not tax advice, nothing is filed").

## 5. Renegotiate or sell (E15-5)

`coach rental indicators [ID] [--market-rate R --market-date D --bank-fees N ...]`, `GET /rental/{id}/indicators`, the last tab.

* **Loan rate vs market**: the nominal rate of the loan against the market rate YOU entered (an argument, or `market_rate` recorded on the property with its date). A rate at least `[analytics] rental_rate_gap_pts` (0.5) above
  it is flagged "a quote may be worth asking for" and priced by the renegotiation scenario of E9-5 on the real schedule (capital and months left, IRA penalty for a French mortgage, the fees you give, break-even). A market rate older than 30 days or without a date is flagged; none = `missing`, never invented.
* **End of the commitment**: state, date, months left, whether a decision is pending; a neutral reading ("ending it early can call the advantage into question: verify with the scheme's rules").
* **Net equity**: the valuation you declared less the capital still due from the schedule (else the declared outstanding; an unknown loan makes the equity unknown), with the loan-to-value ratio. It is BEFORE selling costs, the early-repayment penalty and any tax
  on a capital gain: none of them is modelled.
* Scenarios stay where they are: `coach loans scenario renegotiate|prepay`, the `mortgage_check` and `what_if` tools. No lender, product or insurer is ever named or recommended; the closing line is "General information, not financial advice".

## 6. For the coach (MCP) and the web app

* `rental_overview(property?, sections?, months?, year?, market_rate_pct?, market_rate_date?, bank_fees?, guarantee_fees?, other_fees?, penalty?)` (read-only, E15-2..5): a property is `asset-N` and the kind "rental property", its account
  `account-rental-N`, its loan `liability-N` and its kind, a payment a hashed ref; the scheme name goes through the household scrubber and arrives wrapped as untrusted text; the address, the property manager, the lender, the tenant and the notes are
  never read. Missing facts are listed. A model that wants to record one calls `memory_propose` or `questions_propose`; it never writes.
* The Rental page (`/rental`): the cash flow chart and table with the rent status of each month, the vacancy and the P&L; the commitment with its reminders and the extension decision; the tax year; the indicators. Every write previews its diff first. Like the loans, the page
  is about the whole household: the person switch does not narrow it. A child login cannot reach it (deny by default).
* Settings (`[analytics]`): `rental_reminder_months` (12), `rental_rent_grace_days` (7), `rental_rate_gap_pts` (0.5).

## Limits, stated honestly

* One property account per property (a shared account needs the `property-<id>` tag); a property financed by several loans is supported but the rate indicator uses the first mortgage.
* The P&L is as good as the categories of the flows and the coverage of the account: a payment in the wrong category moves every figure.
* The scheme's rules are not encoded: no rent cap table, no income-limit table, no reduction table (they change, and you own the deed). The coach checks YOUR figures against each other and reminds you of the dates.
* Rent paid net of the manager's fees makes the received rent lower than the gross rent of the return: the manager's annual statement is the figure to report.
* Nothing here was run on a real browser session; the figures were checked on synthetic data and on a scratch restore of the real data (aggregates only).
