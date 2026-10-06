# Household memory

Facts the bank data cannot tell: what a payment really was, one-off events, loans, contracts,
goals and preferences. Every LLM job (categorization, analysis, coaching) reads this folder
first; the `coach` CLI reads the YAML files.

Plain files on purpose: readable, diffable, editable by hand, by the UI, or by Claude through the
`review-categories` / coach skills. This folder contains personal data: keep it out of public git.

| File | Read by | Contains |
|---|---|---|
| `profile.md` | LLM | Household, accounts, income sources, context for analysis |
| `preferences.md` | LLM | How the coach should behave, what matters to the user |
| `household.yaml` | analysis + LLM | Members (adult / child, pocket money), country, declared employers, places and schools (masked for any model), attribution rules, kid budgets, shared-cost allocations (docs/household.md) |
| `categorization.yaml` | `coach classify` + LLM | Annotations that override categories and tag transactions (one-off, exclude from averages, event) |
| `events.md` | LLM | Life events / projects (renovation, birth, move) with dates and budgets |
| `assets.yaml` | analysis scripts + LLM | Savings and investments not connected to the app (regulated savings, employee plans, real estate) for net worth |
| `liabilities/*.yaml` | analysis scripts + LLM | Mortgage, car loan / LOA, consumer loans (start from `_template.yaml`, or `coach memory new liability ...`) |
| `contracts/*.yaml` | subscription skills + LLM | Insurance, energy, telecom, subscriptions: terms, renewal, notice period |
| `open-questions.yaml` | `coach questions` | Things the coach must ask the user next time; answers move into the files above |

## Conventions

- Dates ISO (`2025-06-07`), amounts in EUR, negative = money out (same as the bank data).
- Tags understood by the analysis layer:
  - `one_off`: exceptional, not a habit; shown in totals, excluded from monthly averages and trends.
  - `exclude_from_averages`: excluded from averages/forecasts even if not exceptional.
  - `capital`: an investment in an asset (house, car), reported separately from living costs.
  - `reimbursable`: expected to be paid back (by employer, insurance, someone else).
- `event:` links a transaction to an entry in `events.md`.
- When the user explains something in chat, the coach PROPOSES the change; you accept it yourself
  (`coach memory proposals`, `coach memory accept ID`).
