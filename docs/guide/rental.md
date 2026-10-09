# Rental property

The real return of a rental flat and the obligations of the tax-incentive scheme it sits in, computed from its bank account, its loan
schedule and the facts you recorded.

<figure class="shot" markdown>
![The cash flow and P&L of the demo rental flat](../assets/screens/rental-light.webp#only-light){ loading=lazy }
![The cash flow and P&L of the demo rental flat](../assets/screens/rental-dark.webp#only-dark){ loading=lazy }
<figcaption>Cash flow and P&L: the last twelve closed months, the effort d'épargne, the vacancy and the year's P&L.</figcaption>
</figure>

!!! info "Your figures, never looked up"
    Every scheme figure (rent cap, tenant income limit, reduction rate, market rate, valuation) is the one **you** typed from your deed or
    a quote. Nothing is looked up on the web and nothing is guessed: a missing fact is listed and becomes an open question.

## Setting it up

A property is an asset of kind "rental property" in your memory, linked to its bank account (purpose `rental`) and to the loan that
financed it. **Declare a property** creates one (use an id with no address in it); **Edit facts** adds the scheme, the rent, the price and
the commitment. The property's flows are labelled by generic rules: rent received, loan instalment, co-ownership charges, property tax,
management fees, landlord insurance (PNO, GLI). A cost paid from another account counts when you tag it `property-<id>`.

The page is about the whole household: the person switch does not narrow it, and a child login cannot open it. With several properties,
a switch picks one. The tabs are **Cash flow and P&L**, **Scheme commitment**, **Tax year** and **Renegotiate or sell**.

## Cash flow and P&L

**Last twelve closed months** is a cash flow: the loan principal counts as money out.

| Figure | Meaning |
|---|---|
| **Rent expected** | the rent you declared |
| **Average monthly net** | rent minus every cost, loan included, over the complete months |
| **Average monthly effort d'épargne** | what you had to add each month when the rent did not cover the costs: out of your pocket |
| **Occupancy** | the share of months with rent, with the months without rent and those you declared |

The chart shows **Rent received** against **Costs (loan included)**, and the table each month's rent, loan, other costs, net, effort and
**Rent status** (rent received, partial, late, declared vacancy...). A month the account data do not fully cover is drawn lighter and
never averaged.

**Vacancy** lists the months without the expected rent once the property is let. **Declare a vacancy** records a period you know it was
empty (works, between two tenants), so it is not reported as a missing rent.

**P&L** of a year (pick it in the selector): rent received, loan instalments, co-ownership charges, management fees, property tax,
insurance, works and repairs, other flows, and the **Net cash result**. Under it, the loan of the year split into interest, borrower
insurance and principal from the schedule, and the result if the principal repaid is not counted as a cost. Your own transfers into the
account are never rent.

## Scheme commitment

<figure class="shot" markdown>
![The scheme commitment of the demo rental flat](../assets/screens/rental-scheme-light.webp#only-light){ loading=lazy }
![The scheme commitment of the demo rental flat](../assets/screens/rental-scheme-dark.webp#only-dark){ loading=lazy }
<figcaption>The commitment: start, length, end, time left, the extension decision and the two checks.</figcaption>
</figure>

- **Start**, **Length**, **End** (start + length; check it against your deed) and the time **Left**, with a progress bar.
- **Reminders** 12, 6 and 3 months before the end while no decision is recorded. They go through the [alerts](insights-alerts.md); an
  external message only says that an alert exists. **Record the decision** (extend or not) stops them.
- **Rent cap**: the lease rent against the cap you declared ("within the cap").
- **Tenant income**: the tenant's income against the limit you declared ("within the limit").
- **Facts the coach does not know**: what is missing to check the commitment, also asked as an open question.

## Tax year

<figure class="shot" markdown>
![The tax-year candidates of the demo rental flat](../assets/screens/rental-tax-light.webp#only-light){ loading=lazy }
![The tax-year candidates of the demo rental flat](../assets/screens/rental-tax-dark.webp#only-dark){ loading=lazy }
<figcaption>Micro-foncier against the real regime, the scheme reduction candidate and the documents to gather.</figcaption>
</figure>

Pick the income year (it is declared the spring after it). The page computes **candidates** for the French rental-income return:

| Card | What |
|---|---|
| **Micro-foncier** | gross rents less the flat 30 % abatement |
| **Real regime** | gross rents less the deductible costs: management fees, insurance, property tax, co-ownership charges, works, the flat management allowance, and the **loan interest and borrower insurance from the schedule** |
| **Which is lower?** | the lower taxable figure and the difference |
| **Scheme reduction (candidate)** | from the price and the rate you declared: a tax reduction, not a deduction from the rental income |
| **Documents to gather** | a checklist for yourself (not saved) |

Several real-regime lines are **upper bounds** (only part of the charges, the property tax or the works may be deductible: the syndic's
statement and the invoices decide), and the interest comes from the theoretical table: report the lender's annual statement.

!!! warning "Not a return, nothing is filed, not tax advice"
    These are candidates computed from the bank flows. Rules, rates and ceilings change every year and depend on your situation, and
    choosing the real regime binds you for several years. Verify on the official site (impots.gouv.fr in France) or with an adviser before
    declaring anything. The Italian return is not modelled.

## Renegotiate or sell

Facts with a neutral reading, never a recommendation:

- **Market rate**: a rate **you** looked up for a loan of similar duration, with its date. A rate older than 30 days, or without a date,
  is flagged.
- **Loan rate vs market**: the loan's rate against it. When the gap is at least 0.5 points, the page says a quote may be worth asking for
  and prices a **Renegotiation on the real schedule** with the fees you give (your bank or a broker gives the binding figure).
- **End of the commitment**: its date, the months left, and a reminder that ending a scheme early can call the advantage into question.
- **Net equity**: the value you declared minus the capital still due, with the loan-to-value ratio. It is **before** selling costs, the
  early-repayment penalty and any capital-gains tax, none of which is modelled.

The app never says to sell, keep or switch, and names no lender or product. General information, not financial advice.

## From the terminal

```bash
uv run coach rental list
uv run coach rental cashflow
uv run coach rental pnl --year 2026
uv run coach rental scheme
uv run coach rental tax --year 2026
uv run coach rental indicators
uv run coach rental flows          # flows in no property category, to label
```

`coach rental add`, `edit`, `extension`, `vacancy` and `market-rate` preview the change and write after a typed yes in your terminal.

## See also

- [Rental property reference](../rental.md): the model, the rules, the scheme and the tax-year maths.
- [Loans & net worth](wealth.md): the loan behind the property.
- [Insights & alerts](insights-alerts.md): commitment reminders and rent not received.
