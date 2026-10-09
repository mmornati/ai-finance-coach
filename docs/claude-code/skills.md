# Skills

Skills are ready-made workflows in `.claude/skills/`: Claude Code picks the right one from your question, follows its steps and its guardrails.

<figure class="shot" markdown>
![A subscription audit answered by the coach](../assets/screens/coach-subscriptions-light.webp#only-light){ loading=lazy }
![A subscription audit answered by the coach](../assets/screens/coach-subscriptions-dark.webp#only-dark){ loading=lazy }
<figcaption>The subscription audit on the demo household: StreamBox and VideoMax overlap, StreamBox went up, the health insurance has no contract on file.</figcaption>
</figure>

## At a glance

| Skill | Use it for | Main tools | Web search |
|---|---|---|---|
| `analytics-overview` | any "how much / how is X" question | the read-only analytics tools | no |
| `categorize-transactions` | sync, classify, report | CLI (`coach sync`, `coach classify ...`) | no |
| `review-categories` | fix and teach categories | `transactions_search`, `explain_transaction`, CLI | no |
| `household-questions` | answer the open questions, fill the memory | `open_questions`, `coach questions ...` | no |
| `onboarding-interview` | first-run set-up, "what do you still need" | `onboarding_status`, `memory_propose`, `questions_propose` | no |
| `monthly-review` | a month against usual, three actions | `monthly_review` | no |
| `explain-spike` | why a category or account was high | `explain_spike` | no |
| `subscription-audit` | recurring costs, duplicates, what to review | `subscription_audit`, `subscriptions_inventory` | no |
| `contract-check` | fill contracts from a PDF; can I cancel? | `cancellability`, `contracts_draft` | no |
| `find-cheaper` | cheaper alternatives to one service | `savings_estimate`, `alternatives_record` | **yes** |
| `mortgage-check` | mortgage, renegotiation, loan insurance | `mortgage_check`, `loans_overview` | **yes** |
| `what-if` | a scenario on the forecast | `what_if` | no |
| `tax-helper` | deductible candidates (FR / IT) | `tax_candidates` | no |

## Rules every skill follows

- **Numbers come from code.** Amounts, percentages and dates are quoted exactly as a tool returned them.
- **Proposals only.** No skill writes memory on its own: `memory_propose` and `questions_propose` create proposals you accept yourself
  (Memory > Proposals, or `uv run coach memory proposals`). No skill runs `memory accept` or passes `--yes`, `--force` or `--insecure`.
- **No investment-product advice.** Budgeting and education only; tax and legal output ends with "verify with your contract" or the official site.
- **Web search only in `find-cheaper` and `mortgage-check`**, interactive in Claude Code. They first run `uv run coach privacy status --json`
  and refuse when `web_search_skills` is false. Queries are generic (no name, address, account or identifying amount), and every price or
  rate is shown with its source and date; anything older than 30 days is flagged as possibly outdated.
- **Missing data is said, not guessed.** A skill lists what it needs and offers question proposals.

Most skills also run outside Claude Code: `uv run coach coach ask --skill <id>` and the quick prompts of the web app's Ask the coach page.
`find-cheaper` is Claude Code only, because it needs web search.

## Everyday questions

### analytics-overview

**When**: "How much do we spend on groceries?", "What do our subscriptions cost?", "Will the joint account run short?"
**Tools**: `coverage`, `cashflow`, `category_averages`, `recurring`, `forecast`, `budget_status`, `transactions_search`, `net_worth` ... and,
for one person, any analytics tool with `member`. It hands deeper questions to the dedicated skills below.

### monthly-review

**When**: "How did September go?" **Tools**: `monthly_review` (+ `explain_spike`). One closed month against the usual, the biggest movers
with evidence, budgets and the outlook, ending with **exactly three** concrete actions. It may store one insight; it proposes no memory change.

### explain-spike

**When**: "Why was groceries high in September?" **Tools**: `explain_spike`. Splits the excess into one-off, recurring, new merchant and
habitual spending, by merchant, with the top transactions and the same month last year when it is covered.

### what-if

**When**: "What if we cancel StreamBox and cut eating out by 10 %?" **Tools**: `what_if`. Compares baseline and scenario: minimum
balance, balance in 90 days, monthly savings, yearly impact. A loan prepayment uses the loan's exact schedule.

## Subscriptions & contracts

### subscription-audit

**When**: "Which subscriptions should we review? Do we pay twice?" **Tools**: `subscription_audit`, `subscriptions_inventory`,
`questions_propose`. Ranks services "worth reviewing" with expected yearly savings ranges. Usage is not in bank data, so it is asked. It
never advises cancelling and never cancels.

### contract-check

**When**: "Can I cancel HomeSure? When does it renew?" **Tools**: `cancellability`, `contracts_draft`. Applies the FR (Loi Hamon, Chatel,
Lemoine ...) and IT (Bersani ...) rules. From a PDF it runs `coach memory doc extract` as a **dry run first**; sending the redacted text
needs your yes, and the result is a proposal. General rules, not legal advice.

### find-cheaper

**When**: "Is there a cheaper mobile plan than TelcoCo?" **Tools**: `subscriptions_inventory`, `savings_estimate`, `alternatives_record`,
`add_insight`. Searches official and neutral comparators with generic queries, shows a dated, sourced table, and the savings are computed
by code. Never switches or contacts a provider.

## Loans & taxes

### mortgage-check

**When**: "Is our mortgage rate still good? Could we renegotiate? Loan insurance?" **Tools**: `mortgage_check`, `loans_overview`,
`questions_propose`. Estimates on the loan's own amortization schedule; missing fields are listed and proposed as questions. A market rate
comes from you or a generic search, always dated. Estimates only: no lender or insurer is recommended.

### tax-helper

**When**: "What could be deductible for 2026?" **Tools**: `tax_candidates`. Lists payments that **may** open a reduction or deduction
(France, or the Italian 730), with ceilings and documents to keep. **Reminders only**: nothing is filed, and it ends with "verify on the
official site".

## Set-up & memory

### onboarding-interview

**When**: "Set up the coach", "What do you still need to know?" **Tools**: `onboarding_status`, `memory_propose`, `questions_propose`.
Walks through members, privacy declarations, accounts, loans, contracts, preferences and budgets; every answer becomes a proposal or an
open question. The same checklist is the web app's Set up page and `uv run coach onboarding run`.

### household-questions

**When**: "Let's answer the coach's questions." **Tools**: `open_questions`, `memory_context`, CLI `coach questions ...`. Takes the open
questions by money at stake, records your answers in your words and turns them into previewed memory changes. Only what you confirmed
becomes a fact; anything else stays a proposal.

### categorize-transactions

**When**: "Sync and categorize", after connecting a bank. **Steps**: `coach sync` (within the daily limit), `normalize`, `transfers match`,
`classify run`, `classify report`. Web enrichment only if you turned `[privacy] web_enrich` on yourself, with a dry run first.

### review-categories

**When**: "Why is this miscategorized?", "Let's fix the uncategorized ones." **Tools**: `transactions_search`, `explain_transaction`, then
`coach classify review` / `classify correct`. Asks you in small batches, biggest money first; merchants that may be people are never
searched on the web.

## See also

- [Using the coach from Claude Code](index.md)
- [The finance tools](tools.md)
- [Coach skills reference](../skills.md)
- [Ask the coach in the web app](../guide/coach.md)
