# Loans & net worth

What you own, what you owe, and what is still unknown. Nothing is estimated: a missing figure stays missing, and the page tells you which.

<figure class="shot" markdown>
![Loans and net worth of the demo household](../assets/screens/wealth-light.webp#only-light){ loading=lazy }
![Loans and net worth of the demo household](../assets/screens/wealth-dark.webp#only-dark){ loading=lazy }
<figcaption>Net worth (known part only), its history, the loan cards and the assets not synced from a bank.</figcaption>
</figure>

## Net worth

**Net worth, known part only** = bank balances + manual assets with a value − what is owed on the loans. Next to it: **Bank balances**,
**Manual assets (counted)** and **Owed (known)**. The figure is split **By category** (Cash, Savings, Investments, Real estate, Vehicles,
Other, Owed) and **By person** (the account owner, the holder of an asset or a loan, or joint).

!!! warning "Unknowns are listed, never guessed"
    An asset without a value, a bank account without a balance or a loan without a schedule is **not counted**, and the page says so:
    "This is not the full picture", with the list under **Not counted because unknown**. A total that left something out is never shown
    as your whole net worth, by the page or by the coach.

**Values to refresh** lists assets whose value is getting old.

### Net worth over time

A monthly chart: assets stacked by category above the axis, what is owed below it, and the net-worth line. A snapshot is recorded after
each sync, and past months are rebuilt where the data allow it: bank balances from the transactions, loans from their schedules, manual
assets from their own date on.

- **Lighter bars and hollow dots** mark months where some item had no value (hover to see which).
- A **dashed line** ("value known from here") marks the month an asset is first counted. The total jumps there without any change in
  wealth.
- **Record today** stores today's snapshot on demand. **Table** gives the numbers.

## Loan cards

One card per loan or lease: the monthly payment, the **Capital still due** with where it comes from (**schedule** when the amortization
table can be computed, else the capital you declared), the next instalment, the end date, the rate, the account it is **Debited from**
and the **Last bank payment** found. A yellow strip lists what is **Missing** ("Missing: end date. 1 open question").

For a lease (LOA / LLD) the card shows **Capital owed: none (a lease)**, the monthly rent and the option price: a lease owes no capital,
so its remaining rents are a commitment, not a debt.

**Add a loan** and the pencil open the loan form: kind, dates, amount borrowed, rate (fixed, variable, mixed), borrower insurance,
deferral, the bank label of the payment, and the lease fields for a lease. Every save shows the diff first.

### Details

<figure class="shot" markdown>
![The details of a car lease](../assets/screens/loan-detail-light.webp#only-light){ loading=lazy }
![The details of a car lease](../assets/screens/loan-detail-dark.webp#only-dark){ loading=lazy }
<figcaption>The End of contract tab of a lease: option price, mileage limit and projection, odometer readings.</figcaption>
</figure>

**Details** opens a dialog "computed from the loan file and the bank payments". Its tabs:

| Tab | What you find |
|---|---|
| **Schedule** | the amortization table: capital still due, instalments left, total cost, interest still to pay, and **Interest by calendar year** |
| **Payments** | the bank payments linked to the loan and its alerts |
| **Scenarios** | early repayment, renegotiation, insurance delegation |
| **Suggestions** | terms inferred from the payments, never recorded |
| **End of contract** | for a lease: the buy-or-return reminder and the mileage |

!!! tip "Interest by calendar year"
    It is the figure an Italian 730 mortgage-interest line or a French rental-income return asks for. Check it against the lender's annual
    statement before using it.

### Payment checks

The app links each bank payment to its loan by its bank label and raises an alert when:

| Alert | When |
|---|---|
| missed payment | a due date plus a grace period (5 days) passed with no matching debit, and the account's data cover that date |
| amount changed | the last payment differs from the previous one or the schedule by more than 2 % and 1 EUR |
| extra payment | a debit far above the instalment, or a second one in a month: maybe a partial prepayment |
| wrong account | the payment left another account than the one on file |

When every instalment due was seen at the expected amount, the tab says so.

### Scenarios

Estimates on the loan's **real schedule** (capital and instalments left, insurance, deferral). Nothing is changed anywhere, and the lender's
figures decide.

=== "Early repayment"

    An **Amount to repay** on a date, with the penalty from your contract (blank = the legal cap for a French or Italian mortgage). Two
    options are compared: keep the instalment and finish earlier, or keep the end date and lower the instalment. For each: interest and
    insurance saved, net of the penalty, and the break-even.

=== "Renegotiation / rachat"

    An **Offered nominal rate** and the **Procedure** (renegotiation with the same bank, buy-back by another bank in France, surroga in
    Italy), with bank and guarantee fees: the new instalment, the gross and net saving, and the break-even.

=== "Insurance"

    Another borrower-insurance policy (a dated quote for equivalent guarantees) and the switching fees: the monthly and net saving.

**Store as an insight** keeps the result in your [Insights](insights-alerts.md). Every scenario is general information, not financial
advice; the app names no lender or insurer.

### Suggestions: inferred, not recorded

When some loan terms are missing, the app can work them out from the instalments seen in the bank data (annuity maths), with a
confidence level. These values are **never written** to your memory: **Queue as a memory proposal** creates a proposal that you check
against the contract and accept yourself in a terminal.

### End of contract (leases)

The end date and the reminder 6 months before it (decide between buying and returning the car), the **Option price (residual value)**, the
**Mileage limit** and the fee per excess km, and the mileage **Projected at the end** from your readings. **Record the odometer** adds a
reading. A **Return checklist** lists the usual steps; verify the notice and return conditions in your own contract.

## Assets not synced from a bank

The house, an employee savings plan, a life-insurance contract, the leased car, a rental flat: each with its value and the date of that
value. A **refresh** badge marks a value that is getting old; **value unknown** marks one you never gave (and that is therefore not
counted). **Asset** adds one.

## From the terminal

```bash
uv run coach networth --history
uv run coach loans list
uv run coach loans schedule <id>
uv run coach loans payments <id>
uv run coach loans scenario prepay <id> --amount 10000
uv run coach loans infer <id>              # suggestions, never written (--propose queues a proposal)
uv run coach loans lease <id>
```

`coach loans add`, `edit` and `odometer` preview the change and write after a typed yes in your terminal.

## See also

- [Loans reference](../loans.md): the loan record, inference, schedules, net worth and scenarios.
- [Rental property](rental.md): a loan that finances a rental flat.
- [Memory & set-up](memory.md): the loan, contract and asset forms.
