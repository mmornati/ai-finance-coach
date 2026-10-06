# Coach skills (E7)

Nine analyses that sit on top of the finance tools (see [coach.md](coach.md)). Each one is built the same way:

1. **Deterministic helper(s) in Python** (`src/coach/skills/`) where a computation is needed - numbers come from code, never from the model.
2. **New finance MCP tools** exposing the helper through the same choke point as every other tool: free text wrapped as
   `untrusted_text`, memory ids pseudonymised, the final privacy assertion (redactor ⊇ guard). Read-only, except `questions_propose`
   (creates a sealed PROPOSAL of questions, like `memory_propose`).
3. **A `PromptSpec`** (`agent/prompt.py`, `SKILLS`) that the web app (quick prompts of the Coach page) and
   `uv run coach coach ask --skill <id>` run through the normal runner.
4. **A Claude Code skill** (`.claude/skills/<id>/SKILL.md`) for interactive use in this repository.

| Skill | Story | Tool(s) | Runs in web / CLI | Web search |
|---|---|---|---|---|
| `monthly-review` | E7-3 | `monthly_review` (+ `explain_spike`) | yes | no |
| `explain-spike` | E7-4 | `explain_spike` | yes | no |
| `subscription-audit` | E7-5 | `subscription_audit`, `questions_propose` | yes | no |
| `contract-check` | E7-6 | `cancellability` (+ `coach memory doc add/extract`) | yes (rules); documents: CLI | no |
| `find-cheaper` | E7-7, E8-4 | `savings_estimate`, `subscriptions_inventory`, `alternatives_record`, `add_insight` | **no: Claude Code only** | **yes** |
| `mortgage-check` | E7-8 | `mortgage_check`, `questions_propose` | yes (no market data of its own) | Claude Code only |
| `what-if` | E7-9 | `what_if` | yes | no |
| `tax-helper` | E7-10 | `tax_candidates` | yes | no |
| `onboarding-interview` | E7-11 | `onboarding_status`, `questions_propose`, `memory_propose` | yes + `coach onboarding` + Set up page | no |

## Rules every skill follows

- **Numbers come from code.** The model quotes amounts, percentages and dates exactly as a tool returned them; the answer's numbers
  are checked against the tool results (unverified ones are flagged).
- **Model-facing output is redacted** (coarse by default): accounts and people as pseudonyms, transactions as hashed `h_` refs,
  merchants generalised, third-party text as `untrusted_text`, memory ids as `liability-1`, `contract-1`, `kid-1`.
  The skills compute on the real dataset and rewrite only their free-text, reference and account fields (`skills/redact.py`) with the
  household redactor; the tool choke point still wraps, pseudonymises and asserts. Tests: every new tool on a synthetic household with
  names, an employer, a school and a town, plus a hostile merchant name.
- **Proposals only.** A skill never writes memory: `memory_propose` / `questions_propose` create sealed proposals that the user accepts
  themselves (web: Memory > Proposals; terminal: `uv run coach memory proposals`). Claude Code skills never run `memory accept`, never
  pass `--yes` / `--force` / `--insecure`, never read `memory/`, `data/` or the database directly, never edit `.claude/settings.json`.
- **No investment-product advice**; budgeting and education only; tax and legal output is "verify with your contract / on
  impots.gouv.fr / Agenzia delle Entrate", FR and IT wording where relevant (`memory/preferences.md` rules apply when visible).
- **Web search only in the interactive Claude Code skills `find-cheaper` and `mortgage-check`**, with generic non-personal queries
  (no name, address, account data or identifying amount); every price and rate is shown with its source and date, and anything older
  than 30 days is flagged possibly outdated. The web/CLI runtime has no web tool (its `claude -p` is started with `WebSearch` and
  `WebFetch` denied). Those two skills read `uv run coach privacy status --json` FIRST and refuse to search when `web_search_skills` is false
  (`[privacy] local_only` / `offline`, E11-4); `classify enrich` is a separate, opt-in path (`[privacy] web_enrich`, docs/privacy.md).
- Disclaimers (contract, loan, savings, tax, letters, the closing "general information, not financial advice") come from one module,
  `coach/disclaimers.py` (FR / IT / EN); every text a model writes is labelled AI-generated and checked for investment-product
  recommendations (`coach/compliance.py`, E11-5).
- Everything degrades: with missing data a skill says exactly what it needs and offers question proposals instead of guessing.

## `monthly-review` (E7-3)

- **Purpose**: one closed month against the usual, the biggest movers, budgets, cash flow, outlook, then exactly three concrete actions.
- **Inputs**: `month` (YYYY-MM, default the last closed month).
- **Tools**: `monthly_review`. *Usual* of a category = the average net spending, one-offs / capital / `exclude_from_averages` left out, over
  the last 12 months BEFORE the reviewed month that every major account carrying the category fully covers; fewer than 3 months = low
  confidence; none = the category is listed apart without a comparison. Returns the cash flow (income, spending, saved, debt service
  and estimated principal, drawn from savings accounts / unconnected accounts), `against_usual`, `movers` (|difference| >= 20 EUR, with
  evidence: top transactions, `rec_` series, `anm_` anomalies, `chg_` price changes), `one_offs` listed apart, `budgets` as they stood at
  month end, the forecast flags as of today, and coverage notes. The prompt asks for at most 250 words and `add_insight` (kind `review`).
- **Safety**: read-only; the prompt may store one insight; no memory proposal.
- **Limits**: the forecast part looks ahead from today, not from the reviewed month; a month not fully covered by an account is flagged
  and its figures are partial; movers need a baseline.

## `explain-spike` (E7-4)

- **Purpose**: why was a category / group / account high in a month.
- **Inputs**: `category` (leaf or group, e.g. `food`) OR `account` (pseudonym), `month`.
- **Tools**: `explain_spike`: totals against usual, the spending split by class - `one_off` (tagged), `recurring` (a detected series),
  `new_merchant` (first payment ever that month), `habitual` -, each merchant's change against its own usual (`delta = month - usual`;
  the class sums add up to the excess to the cent), categories, the top transactions (redacted refs), the same month last year only when
  every account carrying the spending covers it, coverage notes.
- **Safety**: read-only. **Limits**: the classes depend on the recurring detection; no baseline without an earlier covered month.

## `subscription-audit` (E7-5)

- **Purpose**: all active recurring costs grouped (streaming / media, software, telecom, memberships, insurance, energy / utilities),
  monthly and yearly cost, price rises, duplicates and overlaps, contracts on file, usage, ranked review candidates.
- **Inputs**: `limit`.
- **Tools**: `subscription_audit`; `subscriptions_inventory` for one service in detail (contract status, the usage the household recorded,
  cancellation rules, stored alternatives, the latest decision: E8, see [subscriptions.md](subscriptions.md)); `questions_propose` for the unknown usage (the questions carry the real service name only on the
  user's machine, and never name a possible person); `cancellability` and `find-cheaper` for the next step.
- **Signals**: `usage_unknown` (almost always: usage is not in bank data, so it is ASKED), `overlap` (several providers of a discretionary
  kind; telecom lines are informational), `duplicate_amount` (same amount, same day +-1, or one merchant paid from two accounts),
  `price_increase`, `marked_to_cancel` (the contract says `keep: false` and it is still paid), `compare_offers` (telecom, insurance,
  energy), `no_contract`.
- **Expected yearly savings ranges** are FIXED SHARES of the yearly cost: discretionary 30-100 %, telecom 10-40 %, insurance 5-25 %,
  energy / utilities 5-15 %, a possible duplicate 0 to its whole cost, a marked-to-cancel service its whole cost; a price rise raises the
  upper bound to its yearly effect. They are not market quotes; `find-cheaper` replaces them with dated quotes. Candidates are ranked by
  upper bound; the sum of the ranges is an upper-bound picture, the actions are alternatives.
- **Safety**: never advises cancelling ("worth reviewing"). **Limits**: loans, rent, taxes, school fees are not audited.

## `contract-check` (E7-6)

- **Purpose**: fill `contracts/*.yaml` from a contract PDF and say whether / when / how a contract can be cancelled.
- **Flow (CLI)**: `coach memory doc add FILE --kind contract --for ID` -> `coach memory doc extract DOC --into contract ID` (DRY RUN: it prints
  exactly what would be sent, redacted) -> the user reads it -> `--send`, which the user's permission rules ask about, and only with the
  user's yes -> a PROPOSAL with the source snippet of each field -> the user accepts it themselves.
- **E8**: the same rules answer every row of the subscriptions inventory (`cancellation`), the calendar's notice deadlines and the local
  cancellation letters (`coach subs letter`); a contract file can be drafted from a series with `contracts_draft` (proposals) or
  `coach subs draft-contracts`; see [subscriptions.md](subscriptions.md).
- **Tool**: `cancellability` (`contract` | `series` | `kind` + dates; `country`, `include_rules`). Output: `can_cancel_now`
  (true / false / null), `earliest_effective_date`, notice, method, `conditions`, `unknown`, `rules` (name + law), the next
  anniversary / contractual notice deadline, and "verify with your contract". A series with no contract file only knows the first
  payment seen, so a young contract is never claimed (the answer is `null`).
- **Rules table** (`skills/cancel.py`, law names, no URLs). FR: Loi Hamon (home and car insurance cancellable after one year, effect one
  month after the request), Loi Chatel for insurance renewals (two months before the anniversary, late notice = free cancellation),
  mutuelle infra-annual resiliation (loi 2019-733: after one year at any time, group contracts excluded), Loi Lemoine (borrower insurance
  at any time, answer in 10 working days), telecom (Loi Chatel: 24 months max, 25 % of the remainder after 12 months), energy (no fee,
  the new supplier handles the switch), "résiliation en 3 clics" (loi 2022-1158), Loi Chatel tacit renewal of service contracts
  (L215-1). IT: Legge Bersani for telecom and energy (no penalties, 30 days max), RC auto without tacit renewal (DL 179/2012), other
  insurance (disdetta; Codice civile art. 1899), insurance linked to a mortgage (DL 1/2012 art. 28, IVASS 40/2018), Codice del consumo
  for subscriptions. The CLI `uv run coach contract check [ID]` prints the same answer for the user with real provider names.
- **Limits**: general rules, not legal advice; a scanned PDF has no text layer (no OCR); `group_contract` and `tacit_renewal` must be given.

## `find-cheaper` (E7-7) - Claude Code only

- **Purpose**: cheaper alternatives for one recurring service, from official / neutral comparators first (FR: energie-info.fr comparator,
  ARCEP-related and Que Choisir comparators; IT: ARERA Portale Offerte, AGCOM, IVASS), as a dated table with sources.
- **Tool**: `savings_estimate(current_monthly, alternative_monthly, switching_costs, months, quote_date)`: monthly saving, yearly saving,
  net over `months` after switching costs, break-even months, and a warning when `quote_date` is older than 30 days or missing. Exact
  integer-cent arithmetic; a promo price is computed twice (promo months, then full price).
- **Safety**: generic queries only (category + cost bucket + features); sources with URLs and dates; never switches or contacts a
  provider; the finding is stored with `add_insight` (kind `finding`) with source URLs and dates in the body.
- **E8-4 store**: each alternative found is recorded with `alternatives_record` (service `ref` from `subscriptions_inventory`, provider,
  offer, monthly price, https `source_url`, `retrieved_at`): it appears in the subscription's alternatives table and `coach subs
  alternatives list`, with the savings computed by code; older than 30 days it is "outdated, re-check" and never the current best.
- **Limits**: prices change; a figure older than 30 days is possibly outdated.

## `mortgage-check` (E7-8)

- **Purpose**: amortization, remaining capital and interest, renegotiation / rachat (FR) or surroga (IT), borrower-insurance delegation.
- **Tool**: `mortgage_check(liability?, market_rate_pct?, market_rate_date?, mode?, bank_fees?, guarantee_fees?, other_fees?, penalty?,
  alternative_insurance_monthly?, current_insurance_monthly?, insurance_fees?, country?)`.
  - `amortization`: annuity, first instalment one month after `start_date`, the schedule, interest paid and to come, interest by year,
    whether the declared instalment matches. The remaining capital is the declared one while it is at most 120 days old, otherwise the
    computed one; a missing rate is back-solved from capital + instalment + months when possible and flagged `approximate`.
  - `renegotiation_estimate`: same remaining capital and months, new payment, interest saved, costs (IRA, bank, guarantee, other), net
    saving, break-even months, verdict, and the broker rule of thumb (>= 0.7 point, >= 70,000 EUR, >= 10 years) as a flag only.
    FR rachat: IRA = the LOWER of six months of interest and 3 % of the capital remaining due (Code de la consommation L313-47); same-bank
    renegotiation: no IRA; IT surroga: no penalty and no bank / notary costs by law (art. 120-quater TUB). A contract penalty amount
    overrides the cap.
  - `insurance_delegation_estimate`: (current - alternative) x remaining months, net of fees (Loi Lemoine FR; IT: bring your own policy,
    IVASS).
- **E9**: `state.schedule` is the loan's own amortization schedule (`loans/schedule.py`: first due date, `payment_day`, a deferral, the
  insurance as a flat premium or a rate on the initial / outstanding capital, a variable rate at its current rate flagged `approximate`); when it is
  computable the remaining capital and the months left come from it. `loans_overview` adds the payments seen, the alerts and the inferred
  suggestions (always marked `inferred`: to confirm with the user, never presented as recorded). The user's own calculators: `coach loans scenario
  prepay|renegotiate|insurance` and the loan page (see [loans.md](loans.md)).
- **Degradation**: with missing fields the tool lists them in `what_i_need` (never guessed) and `questions_propose(liabilities=[id])` creates the
  question proposal. The market rate must be GIVEN (with its date); the tool and the web/CLI runtime never look anything up; the
  interactive skill may search a generic observatory average and feeds it in.
- **Safety**: "Estimate only: consult your bank or a broker"; no product, lender or insurer recommended.

## `what-if` (E7-9)

- **Purpose**: baseline vs scenario on the forecast. **Tool**: `what_if(scenario)`, strict JSON schema, 1 to 6 changes, `days` 30-365.
- **Changes**: `cancel_recurring` (removes the series' future occurrences from the forecast), `adjust_category` (percent of the category's
  coverage-aware monthly average, spread evenly), `set_category_level` (to a target), `add_monthly` / `remove_monthly`, `one_off`
  (+ `direction: in`), `prepay_loan` (E9-3: exact before / after schedules on the loan's own amortization schedule: the capital due on the
  prepayment date (a prepayment dated on an instalment day is applied before it), the instalments left, deferral and insurance respected;
  `keep: payment` shortens the loan, `keep: term` lowers the instalment; without a computable schedule the old capital / rate / term route,
  and without a rate a zero-interest approximation flagged `approximate`; a possible IRA is reported apart), `change_income` (percent of the active salary series or a fixed monthly delta).
- **Output**: baseline and scenario (start, minimum balance and date, end balance, balance at 90 days, first negative / at-risk day,
  monthly savings), `delta` (including `yearly_impact` = recurring effects x 12 + one-offs in the next 12 months), per-change effects with
  evidence and notes. **Limits**: the baseline is the E4 forecast (balance snapshots required for the forecast part; the monthly and yearly
  effects work without).

## `tax-helper` (E7-10)

- **Purpose**: candidates that may open a reduction, credit or deduction for an INCOME year (`year`, default the current year from October,
  else the previous one) and `country` (household `country` in `household.yaml`, FR by default; IT supported). Reminders only, nothing is filed.
- **Tool**: `tax_candidates`. Amounts come from the transactions of the calendar year by CATEGORY or by TAG; each candidate has the rule, rate,
  ceilings used, estimated range, `documents_to_keep`, `missing_info`, the source (law), coverage notes, plus `no_data_for` with how to
  make a payment visible. Disclaimer: "verify on impots.gouv.fr / Agenzia delle Entrate".
- **FR**: `fr-dons` (category `charity.donations`: 66 %, 75 % of the first 1,000 EUR for organisations helping people in difficulty; the 20 %-of-income
  ceiling is not computed), `fr-emploi-domicile` (tags `emploi-domicile` / `cesu` / `services-personne`: 50 % within 12,000 EUR + 1,500 per
  dependent child, max 15,000), `fr-garde-enfants` (`kids.childcare`: 50 % within 3,500 EUR per child under 6 on 1 January, from the birth
  years of `household.yaml`), `fr-scolarite` (flat 61 / 153 / 183 EUR per child by the stage guessed from the age), `fr-pinel` (assets with
  `scheme: pinel` or `pinel_commitment_years`: annual reduction from price, purchase year and commitment; reduced 2023-2024 rates vs classic),
  `fr-revenus-fonciers` (E15-4: one candidate per rental property: gross rents, the micro-foncier abatement and the reel deductions with the loan interest from the amortization schedule, the lower candidate, documents; the Pinel candidate is computed from the price and the total rate the owner declared on the property when both are there, else from the table above; see [rental.md](rental.md)),
  `fr-pension-alimentaire` (tag), `fr-per` (tags `per`, `plan-epargne-retraite`; deductions: the saving depends on your marginal rate, not
  computed, not a recommendation).
- **IT 730**: `it-spese-mediche` (`health.pharmacy|doctors|optician`: 19 % above the 129.11 EUR franchise), `it-interessi-mutuo` (mortgage
  liabilities: 19 % of the interest up to 4,000 EUR, interest of the year from the amortization), `it-ristrutturazioni`
  (`housing.renovation` or tag `ristrutturazione`: 50 / 36 / 30 % by year and use, 96,000 EUR ceiling, 10 yearly instalments),
  `it-istruzione` (`kids.school`, `education.courses`: 19 % up to 800 EUR per student), `it-assicurazioni` (`insurance.life` or tags: 19 % up to
  530 EUR), `it-erogazioni-liberali` (`charity.donations`: 26-35 % up to 30,000 EUR).
- **Limits**: rates and ceilings are those known when the module was written; every candidate states what it used, so a change shows up against
  the official page. Months of the year not covered by the accounts make an amount a lower bound.

## `onboarding-interview` (E7-11)

- **Purpose**: complete what the coach does not know: household (members, birth years, country, privacy declarations - employers, towns,
  schools), account owners and purposes, loans (the fields `mortgage-check` needs), contracts, preferences, budgets, open questions.
- **Tool**: `onboarding_status` (steps `done` / `partial` / `todo`, what is missing, next actions with the exact commands).
- **Three ways**: the Claude Code skill (answers become `memory_propose` / `questions_propose` proposals); `uv run coach onboarding status`
  and `uv run coach onboarding run` (an INTERACTIVE interview that refuses to run without a terminal: every answer is a validated memory
  edit that is previewed as a diff and written after a typed yes, source `cli`; account owner / purpose through `coach accounts set`;
  it reuses the questions generator and finishes with `coach memory check`); and the web page "Set up" (checklist from
  `GET /api/v1/onboarding`, previewed writes through `PUT /onboarding/household`, `POST /onboarding/preferences`, and the existing
  `PUT /memory/{kind}/{id}` and `PATCH /accounts/{uid}` forms, source `ui`).
- **Limits**: in coarse privacy mode memory ids appear as pseudonyms to the model, so it tells the user the exact change rather than
  proposing against a real id it cannot see.

## Reference

- Tools and schemas: [coach.md](coach.md). Memory files and proposals: [memory.md](memory.md). Analytics behind the numbers: [analytics.md](analytics.md).
- Tests: `tests/test_skills_math.py` (loans, savings, cancellability), `test_skills_analysis.py` (review, audit, what-if, tax),
  `test_skills_tools.py` (tools, privacy, proposals), `test_skills_prompts.py` (PromptSpecs, CLI, fake claude), `test_skills_files.py`
  (the SKILL.md files), `test_skills_cli.py` (onboarding, contract check), `test_api_skills.py` (web).
