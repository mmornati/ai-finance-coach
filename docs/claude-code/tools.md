# The finance tools

The finance tools are the coach's only window on your data: each one returns computed, redacted figures, and only a handful can write, and then only a proposal.

<figure class="shot" markdown>
![An answer of the coach with evidence chips](../assets/screens/coach-answer-light.webp#only-light){ loading=lazy }
![An answer of the coach with evidence chips](../assets/screens/coach-answer-dark.webp#only-dark){ loading=lazy }
<figcaption>The same tools serve Claude Code, the web app's Ask the coach and the CLI. Here <code>coverage</code> and <code>explain_spike</code> answered "Why was July so expensive?".</figcaption>
</figure>

## How to read this page

The tools appear in Claude Code as `mcp__finance__<name>`. They take typed JSON parameters (accounts, owners and members as
pseudonyms, months, dates, categories) that are validated before the call. Every analytics tool also takes a `member` pseudonym
(<span class="pseudo">adult-1</span>, <span class="pseudo">kid-1</span>) to count only what belongs to that person. Long outputs are
capped and say what was cut.

Start with `coverage`: averages and comparisons depend on which months your accounts really cover.

## Coverage & cash flow

| Tool | What it returns |
|---|---|
| `coverage` | accounts, first and last transaction, last sync, gaps |
| `cashflow` | monthly income, spending, savings and savings rate (household and per account) |
| `category_averages` | the usual month per category, coverage-aware, one-offs apart |
| `year_review` | the year looked back on |

## Spending & transactions

| Tool | What it returns |
|---|---|
| `transactions_search` | redacted search (date, signed amount, category, pseudonyms, generalised merchant, hashed `ref`) with the **totals computed for you**; up to 50 per page |
| `explain_transaction` | why a transaction has its category (override, rules, labels, annotations) and whom it is attributed to |
| `anomalies` | category spikes, duplicate charges, new merchants, large payments, with `anm_` ids and evidence refs |
| `explain_spike` | a month's spending split into one-off, recurring, new merchant and habitual, by merchant, with the same month last year |
| `monthly_review` | one closed month against usual: cash flow, movers with evidence, one-offs, budgets, forecast flags |

## Recurring & subscriptions

| Tool | What it returns |
|---|---|
| `recurring` | subscriptions, bills, loans and salary: cadence, expected amount, yearly cost, `rec_` ids |
| `price_changes` | recurring payments that changed price (`chg_` ids) |
| `subscription_audit` | every active recurring cost grouped, duplicates and overlaps, price rises, ranked review candidates with savings ranges |
| `subscriptions_inventory` | the list of recurring costs and contracts: cost, contract status, usage you recorded, cancellation rules, alternatives, latest decision |
| `cancellability` | can a contract be cancelled now, earliest date, notice, method (FR / IT rules) |
| `savings_estimate` | the exact net saving of a cheaper alternative, break-even, a stale-quote flag |
| `savings_tracker` | your decisions, checked against the bank data, and the savings actually achieved |

## Budgets, calendar & forecast

| Tool | What it returns |
|---|---|
| `budget_status`, `budget_suggestions` | budget progress; suggested budgets (nothing is set) |
| `goals` | savings goals |
| `calendar` | upcoming payments, instalments, contracts, consents |
| `forecast` | balance projection with a band, at-risk dates and flags |
| `what_if` | baseline against a scenario (cancel a payment, cut a category, a one-off, prepay a loan, change the income): up to 6 changes |
| `tax_candidates` | payments of an income year that may open a tax reduction (FR / IT), with rule, ceiling and documents to keep |

## Loans & wealth

| Tool | What it returns |
|---|---|
| `net_worth` | balances + manual assets - loans, by category and owner, and its monthly history; unknowns listed, never counted |
| `loans_overview` | per loan: schedule or the missing fields, capital due, interest by year, payments seen, alerts, inferred suggestions |
| `mortgage_check` | amortization, renegotiation / rachat / surroga and borrower-insurance estimates; missing fields listed, never guessed |
| `rental_overview` | a rental property as <span class="pseudo">asset-1</span>: cash flow, yearly P&L, scheme commitment, tax-year candidates, renegotiate-or-sell indicators |

Loans and assets appear by **kind** only (`mortgage`, `house`), never by lender or contract number.

## Household

| Tool | What it returns |
|---|---|
| `household_overview` | members as pseudonyms with their roles, the accounts each owns, how many transactions belong to each |
| `kids_money` | pocket money, extra top-ups, spending, balance trend and kid budgets of the children |
| `who_pays` | shared costs split by your allocation rules: shares, what each paid, the settlement (a report, never a money move) |

## Memory, questions & set-up

| Tool | What it returns |
|---|---|
| `memory_context` | the household memory, redacted (free text withheld in `coarse` mode) |
| `open_questions` | what the coach still wants to know |
| `onboarding_status` | the checklist of what is missing, with the next commands |
| `memory_propose` | **writes a proposal**: a sealed change you accept yourself |
| `questions_propose` | **writes a proposal** of questions for you (usage of a service, missing loan fields) |
| `contracts_draft` | **writes proposals** of contract files for recurring payments without one |

## Insights & decisions

| Tool | What it returns |
|---|---|
| `add_insight` | **stores** a traceable finding in the Insights feed; refs and numbers are checked against what the tools returned |
| `alternatives_record` | **stores** one sourced, dated offer (https URL, price above 0, date not in the future); savings are computed by code |
| `decision_propose` | **stores** a proposed decision (cancel, downgrade ...) that does not count until you confirm it |

## What no tool can do

!!! warning "The boundary"
    No tool can write memory directly, accept, reject or revert a proposal, confirm a decision, sync a bank, call the network, run a
    command, read a file, or return a secret, a path, an account id, a label or an IBAN. The catalogue is fixed and a test asserts it,
    including which tools may write.

Every free-text field from a third party comes wrapped as `{"untrusted_text": "..."}`. If one reads like an instruction, the session is
marked suspicious and every proposal it creates needs a confirmation per field when you accept it.

## From the terminal

```bash
uv run coach coach tools --sizes                               # the tools and the size of each redacted output
uv run coach coach tool tax_candidates --args '{"year": 2026}' # run one read-only tool exactly as the model sees it
```

## See also

- [Using the coach from Claude Code](index.md)
- [Skills](skills.md)
- [The coach (LLM): the finance tools](../coach.md)
