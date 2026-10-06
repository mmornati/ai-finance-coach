# Household memory (E3)

`memory/` holds what the bank data cannot tell: who the household is, what a payment really was, loans, assets,
contracts, goals. Plain files (YAML and Markdown), readable and hand-editable, but every programmatic write goes
through one validated, recorded path (`coach.memory`). The folder contains personal data: it is git-ignored by the
project and never leaves the machine except in the redacted forms described below.

## Files

| File | Format | Schema | Written by |
|---|---|---|---|
| `categorization.yaml` | YAML | `annotations: [{id, match, category, tags, event, note}]`; `match` keys: `merchant_key` / `description` (regex), `tx_keys`, `date_from` / `date_to`, `amount_min` / `amount_max`, `category_in`, `weekdays`. Unknown keys are errors. First matching annotation wins | `memory annotate`, `memory set` |
| `assets.yaml` | YAML | `assets: [{id, kind, provider, holder(s), balance, value, as_of, liquidity, ...}]`. A rental property (kind `real_estate_rental`, E15) adds `account` (uid or label), `loan` (liability id), `scheme` (its name, free text), `rent_monthly`, `purchase_price`, `purchase_date`, `commitment{start_date, years, end_date, surface_m2, rent_cap_m2, rent_cap_monthly, tenant_income_limit, tenant_income, reduction_rate_pct, reduction_base_cap, reduction_first_year, reduction_schedule[{years, yearly_rate_pct}], extension{decision undecided/extend/not_extend, years, additional_rate_pct, decided_on, note}}`, `market_rate{rate_pct, as_of, source}` and `vacancies[{start, end, note}]`: every figure is the owner's own, see [rental.md](rental.md). A payment paid from another account counts for a property with the tag `property-<id>` | `memory set`, `coach rental add / edit` |
| `liabilities/*.yaml` | YAML, one loan per file | `id, kind (mortgage/car_loan/loa/lld/consumer_loan/bnpl), lender, dates, principal, outstanding(+_as_of), rate{type,nominal,taeg,index,margin,cap}, monthly_payment, insurance{monthly,rate_pct,basis,delegated}, deferral{months,kind}, first_payment_date, term_months, payment_day, payment_match, amount_match, holder(s), LOA: first_payment, residual_value, mileage_limit_km, excess_km_fee, initial_km, odometer[{date,km}]` (all fields: [loans.md](loans.md)) | `coach loans add/edit`, `memory new liability`, `memory set` |
| `contracts/*.yaml` | YAML, one contract per file | `id, provider, kind (energy, telecom, insurance_*, health, streaming, software, membership, other), merchant_match, start_date, renewal, billing{amount,period}, commitment_end, notice_period_days, contract_number (letters only, never shown to a model), usage{frequency daily/weekly/monthly/rarely/never/unknown, last_used, note} (E8-2; a plain text still loads), keep, ...` | `memory new contract`, `memory set` |
| `household.yaml` | YAML (new, E14-1) | `members: [{id, name, role adult/child, birth_year, aliases}]`, `employers`, `places`, `schools` (privacy declarations) and `country` (`FR` / `IT`: tax and cancellation rules of the coach skills, FR when absent) and the optional `contact: {address, email, phone}` (E8-5: filled into cancellation letters on this machine; no tool or context reads it, the guard refuses its values, the coach cannot propose a change to it: `coach subs contact set`). `id` is what LLMs see; `name` and `aliases` (holder-name spellings in bank data) stay local. E14 adds, per member, `pocket_money: {amount, period weekly/monthly, day}` (children), and the lists `attribution: [{id, member, match{account, card_last4, merchant_key, description, direction, amount_min, amount_max}, note}]` (who a transaction belongs to), `kid_budgets: [{id, member, period, limit, category/group, note}]` and `allocations: [{id, title, match{category/group/tag/merchant_key/account}, method equal/income/custom, among, shares}]`: see [household.md](household.md). The coach may only PROPOSE changes to them | `memory member add`, `coach household rule / budget / pocket / allocate` |
| `budgets.yaml` | YAML (E4-7) | `budgets: [{id, category (group.leaf) or group, monthly, rollover, start, owner, account, note}]`: exactly one of `category` / `group`; the category must exist in the taxonomy. See `docs/analytics.md` | `coach budget set` |
| `goals.yaml` | YAML (E4-9) | `goals: [{id, title, target_amount, target_date, asset / account / tag (exactly one), monthly_contribution, baseline, start}]` | `coach goals set` |
| `events.md` | Markdown (unchanged) | an event is a `## slug` heading; annotations reference the slug with `event:` | `memory append events.md` |
| `events.yaml` | YAML, optional | `events: [{id, title, start, end, budget, status}]`: structured data for the same ids (the Markdown stays the prose) | `memory set` |
| `open-questions.yaml` | YAML, source of truth | `questions: [{id, status open/answered/dismissed, topic, question, evidence, suggested_target{file,field}, stake, key, origin, created, answered, answer, note}]` | `coach questions ...` |
| `open-questions.md` | Markdown, generated view | regenerated after every change; a hand-edited copy is saved to `.backups/` first | never by hand |
| `documents.yaml` + `documents/` | YAML + files | `documents: [{id, sha256, filename, stored_as, kind, size, added, for}]`; files stored `0600` under a hashed name | `memory doc add` |
| `profile.md`, `preferences.md` | Markdown | free text read by the LLM | `memory append` |
| `.history.git/`, `.proposals/`, `.backups/`, `.lock` | internal | change history, pending proposals, backups of overwritten views, writer lock | the store |

Dates are ISO, amounts EUR (negative = money out), unknown = `null`. Files starting with `_` (`_template.yaml`) are
templates and are ignored.

## Writing: validation, comments, atomicity

* One module, `coach.memory.store.MemoryStore`, finds, loads, validates (pydantic schemas in `coach.memory.schemas`)
  and writes every file. A write is: apply the operation to a *round-trip* document (ruamel.yaml) -> validate
  schema and semantics (category exists in the taxonomy, event exists, regexes compile, ids unique) -> refuse if a
  comment would be lost -> take the writer lock -> snapshot hand edits -> write a temp file + `fsync` +
  `os.replace` -> record in the history.
* **Comments and layout survive.** The YAML round trip is configured so that an untouched file dumps back
  byte for byte (tested on the shapes of the real files: null as `null`, `{ a: b }` flow style, folded notes,
  trailing comments). A `set` changes one line. An edit that would drop a comment (removing an item that carries
  one) is **refused** unless `--drop-comments` is passed. A newly appended list item gets the blank line the
  hand-written items have.
* Unknown fields in assets / liabilities / contracts are kept and reported as `info`; `memory set` refuses to
  *add* an unknown field unless `--new-field` (a typo would otherwise silently create one). Annotations reject
  unknown keys outright.
* `open-questions.md` and any file the store does not manage cannot be written through it.

## Change history (git inside `memory/`)

Chosen over a journal of patches because history, per-file log, diff and a three-way revert come for free, are
robust (content-addressed) and inspectable with any git tool. The repository's git directory is
`memory/.history.git` and its work tree is `memory/` itself:

* created on the first write; the files that exist at that moment are the first snapshot, so the first change is
  already revertible;
* each change is one commit `coach: <action> <file> (<detail>)` with `Source:` and `Reason:` trailers;
* edits made by hand between coach writes are snapshotted (`coach: snapshot external edits (...)`) before the next
  write, so nothing is lost by a revert and `coach memory diff` shows unrecorded hand edits;
* the git directory has a non-standard name on purpose: the project repository (or any parent) never sees a nested
  `.git` and keeps ignoring `memory/`; every git call pins `GIT_DIR` / `GIT_WORK_TREE`, ignores the user's global and
  system git config and hooks, and never configures a remote or pushes;
* not versioned: `documents/` (binary, content-addressed), `.proposals/`, `.backups/`, `.lock`;
* `coach backup` already archives the whole folder, history included.
* `[memory] history = false` writes without history; git missing + history on = the write is refused with a clear
  message (a silent loss of history is worse).

`coach memory history [file]`, `coach memory diff [change-id|file]`, `coach memory revert <change-id>` (a new
change; refused cleanly on conflict, on the first snapshot, or if the result would be invalid).

## Proposals (E6-5): the coach never writes

`coach.memory.proposals.create(store, file, ops, reason, source)` validates a change against the current memory
(dry run) and stores it in `memory/.proposals/<id>.json`: file, operations (`set`, `unset`, `append`, `remove`,
`create`, `append_text`, `replace_text`), reason, source (`coach-llm`, `doc-extract:<doc-id>`, `user`), the rendered
diff, a field-by-field view (old -> new, with the source snippet for document extractions) and the hash of the file
it was computed from. Nothing else is touched. `coach memory proposals [--json]` lists them, `coach memory accept
<id>` applies one (validated again against the file as it is *now*, recorded in the history with the proposal's
source and reason), `reject <id>` closes it. The coach/LLM runtime calls `create` only: the finance MCP server's `memory_propose`
tool (source `coach-llm`, see [coach.md](coach.md)) is the single door. A proposal created in a session where tool data held
instruction-like text carries the per-field `suspicious` flags, so accepting it needs `--confirm-field` for each field.
`questions_propose` (E7) is the second door: it builds usage / missing-loan-field questions on the server and creates the same kind of
sealed proposal on `open-questions.yaml`; accepting one regenerates the Markdown view `open-questions.md` like any `coach questions`
write (and refuses if that view was edited by hand: `coach questions regenerate-view` first).

## Open questions (E3-3)

`coach questions list [--open] [--json]`, `answer <id> "text"` (records the answer only, no other file changes),
`dismiss <id> [--reason]`, `reopen <id>`, `add "question" [--topic --target file:field --stake --evidence k=v]`.

`coach memory migrate-questions` converts the old free-form `open-questions.md` (dry run: counts, per-topic
numbers, the exact diff of the regenerated view; `--write` applies it and keeps a backup in `.backups/`). Each
checkbox line becomes one question: `[x]` -> answered (the line is the answer, `(answered DATE)` becomes the date),
`[ ]` -> open; the original text is kept verbatim in `source_text`, and the regenerated view prints it unchanged, so
nothing is lost. It is a one-time command and refuses to run twice.

`coach questions generate [--dry-run]` derives questions from the data and the memory. Each has a stable key and id
(`q-<kind>-<hash>`) and is never created again once a question with that key exists in *any* status:

| kind | when |
|---|---|
| `merchant` | uncategorized / low-confidence merchants with money at stake >= `[memory] question_min_stake` (not person-like, not already covered by a liability/contract) |
| `held-back` | one aggregated question for counterparties that may be people (no names) |
| `large` | one-off payments >= `[memory] big_tx_threshold` not explained by memory (merchant seen at most twice) |
| `recurring` | monthly debits (>= 3 months, stable amount) in housing/subscriptions/insurance/debt with no liability or contract file |
| `fill` | liabilities / contracts / assets with null key fields |
| `stale` | an `as_of` / `outstanding_as_of` older than the thresholds |
| `acct` | account without owner or purpose |
| `household`, `birth` | no `household.yaml` though accounts have owners; member without `birth_year` |

Questions contain amounts, counts, dates and business names only. Person-like keys use the classifier's guard plus a
stricter one (a given name and no organisation word); they never get a question of their own. If a hand-written
question already names the merchant, no generated one is added.

## `coach memory check` (E3-6, E3-8)

Errors (exit 1): schema/YAML errors with file, path (`annotations[some-id].match.date_from`) and line; unknown
category; broken documents. Warnings: unknown event; annotation matching 0 transactions, matching more than 20 % of
an account's transactions, or fully shadowed by an earlier one (the first match wins); `tx_keys` not found or
re-keyed (`tx_key_remap`); `payment_match` / `merchant_match` matching nothing, amounts more than 10 % off
`monthly_payment` / `billing.amount`, a pattern that catches two loans or payments of very different sizes;
`outstanding_as_of` older than `[memory] stale_months` (6) or missing; asset value older than
`asset_stale_months` (3) or undated; account owners that are not household members; `open-questions.md` out of date;
E14: an attribution rule, kid budget or allocation naming a member that does not exist, a category or group that does not exist, a rule on an account
that is not known (`unknown_member`, `unknown_category`, `unknown_group`, `attribution_unknown_account`), pocket money on an adult, a household with no adult.
Info: unused events, fields not in the schema, incomplete liabilities (end date, outstanding capital, rate), assets
without a value, members without a birth year, questions that look resolved. `coach health` prints a one-line
summary; `schedule run` ends with a warn-only memory step (`[schedule] memory_check = false` to disable). Checks
against transactions need the database and are skipped with `--no-db`.

`coach.memory.totals.manual_totals(store)` returns the manual assets / liabilities totals for net worth (E9):
total and by kind, items with `as_of` and a stale flag, outstanding capital, monthly payments, and what is unknown;
`coach memory totals [--json]` prints it. Synced bank accounts are not in it. The full net worth (bank balances, categories, owners, the
capital of each loan from its amortization schedule, the monthly history) is `coach networth` (E9-4, [loans.md](loans.md)).

## `coach explain` (E3-7)

`coach explain <tx_key|fragment> [--json]` (function `coach.memory.explain.explain`): parser output (type, merchant
raw/key, counterparty, mandate, reference), every step of the precedence (override, transfer link, type rule, your
merchant label, rule, entity default, LLM / kNN / web label with confidence, model and date), whether it
decides / applies but is outranked / does not apply, every memory annotation with why it matched or not and its line,
the final category, tags and event, and where to change it. The chain is re-evaluated and checked against the
classifier's own answer (`consistent`). The web evidence note of an enriched merchant is not stored (only the
label), and the explanation says so.

## Documents (E3-5)

`coach memory doc add FILE --kind loan|contract|insurance|statement|other [--for ID]` copies the file into
`memory/documents/` (dir 0700, file 0600, hashed name) and records metadata; a duplicate hash is refused; `--for`
also lists the path in the liability/contract's `documents:`. `coach memory doc list`.

`coach memory doc extract DOC --into liability|contract|asset ID [--dry-run | --send] [--create --target-kind K]`:

1. text is read **locally**: pypdf for PDFs with a text layer, plain text files. **OCR is out of scope**: a scanned
   PDF is refused with that message;
2. text is **redacted**: IBAN, e-mail, phone, long numbers, titled names, household names and aliases (members,
   account holders), street addresses, postcodes, birth dates. Amounts with decimals or a currency are kept (they are
   the point). It is best-effort, which is why step 3 exists;
3. `--dry-run` (default) prints **exactly** what would be sent (instructions, the JSON schema of the target's fields,
   the redacted text) and what was redacted; nothing leaves the machine;
4. `--send` calls the configured backend (`[llm] backend`, claude-code by default) and logs it in `llm_usage`
   (purpose `doc_extract`). Each returned field must be a known field, parse and validate, and carry a **snippet
   that is present in the redacted text** and that actually states the value (see below); otherwise it is dropped and reported. The surviving fields (status `snippet-found`: evidence was found, not a guarantee of truth) become a
   **proposal** with old -> new and the snippet per field: never a direct write.

## Safety rules (hardening)

* **One reader for the classifier.** `categorization.yaml` is read by exactly one loader (`coach.memory.loader`): the
  store's validated, strictly typed annotations (numbers for amounts, ISO dates without time, a list of strings for
  tags, no unknown keys, no empty regex). The classifier never sees a value the store would reject. It never raises:
  a missing file means no annotations; an unreadable, non-UTF-8, broken or invalid file means **no memory
  annotations and a loud warning on stderr**, so a bad hand edit cannot stop sync / normalize / transfers / classify
  (`coach memory check` shows the details). `household.yaml` is read defensively the same way.
* **Proposals are sealed, never trusted, and accepted by a human.** A proposal stores a seal over EVERY field that
  decides or documents what is applied: id, created, base hash, file, operations, reason, source (the proposer) and
  evidence. The seal is an HMAC-SHA256 if the optional secret `proposal_key` (env `COACH_PROPOSAL_KEY` or Keychain)
  exists, plain SHA-256 otherwise. The stored `status`, `diff` and `changes` are display leftovers and are **never**
  used: `memory proposals` and `accept` recompute the changes and the diff from the operations against the file as
  it is now. A proposal's resolution lives in the change history (`accept <id>` commits) and in a chained, MAC'd
  registry `.proposals/.resolved`, so editing the JSON can never make an accepted or rejected proposal pending again.
  `coach memory accept` requires **stdin and stdout to be a terminal** (no `--yes`; `--force`, for a stale target,
  is also terminal-only), shows the fresh diff, asks for typed confirmation, refuses a modified or unsealed proposal,
  records the accepting actor (`--source`, default `cli`) and the proposer in the history. The coach must never run it.
  Honest limit: a process running as the same user can always edit the memory files directly; the mitigations are the
  history attribution, the terminal gate, the seal and the 0700/0600 permissions.
* **Sources.** Every writing command takes `--source NAME` (recorded in the history; the coach uses `coach`).
* **History keeps the past.** Every earlier value, note and reason stays in `memory/.history.git` (and in backups).
  `coach memory purge-history --before YYYY-MM-DD` (terminal only; takes an encrypted `coach backup` first; refuses a
  repository with extra branches; a cutoff after the newest change keeps only the current state) rewrites the private history so older commits and their
  objects are gone for good (the files are untouched; the first kept commit becomes a snapshot). Manual route:
  delete `memory/.history.git` (a new history starts at the next write).
* **Revert** is all-or-nothing: hand edits are snapshotted first; per-file and cross-file validation (for example an
  annotation whose event would disappear) refuse it; any failure restores the folder exactly. Reverting a file
  creation or deletion works.
* **`memory set` values**: only plain numbers, `true`/`false`, ISO dates, `null` and `[..]`/`{..}` are typed; any
  other text stays a raw string (`Loan # 2` keeps its `#`); wrap in quotes to force text. Leading-zero numbers stay text.
* **Questions view.** If `open-questions.md` was edited by hand, the next `coach questions` write is refused (nothing
  is overwritten) until `coach questions regenerate-view` saves your copy to `.backups/` and rewrites the view.
* **Generated questions and the LLM.** A merchant is named in a generated question only when it clearly is not a
  person (processor prefixes like PAYPAL are looked through; a given name, or an unknown single word, is treated as a
  person). `memory context` never shows a generated merchant question's text: it restates it from the evidence
  (counts, amounts, dates); hand-written questions lose capitalised runs that hold a given name. `--coarse` drops
  profile / event / note text and masks the `employers:` / `places:` declared in `household.yaml`.
* **Documents.** A field is `snippet-found` only if the snippet is at least 12 characters, a contiguous part of the
  redacted text, not instruction-like (possible prompt injection), AND states the value (numbers and dates are
  compared normalised: `250 000,00` = 250000, `05/02/2020` = 2020-02-05; enums accept French synonyms). The prompt
  marks the document as untrusted data. Redaction removes personal data BEFORE protecting amounts (amounts, rates, distances and durations then survive), also
  untitled names next to person cues (demeurant, emprunteur, souscripteur, `X et Y`, née/épouse) unless they carry an
  organisation word (SA, SAS, BANQUE, CREDIT, ASSURANCE...); numbers must carry their unit (EUR, %, km, mois, jours) and
  dates are never read as numbers; the sentences around a snippet must not read like an instruction. Redaction removes: names after
  `M.` / `Mme` / `Monsieur` / labels (Unicode, accented, hyphenated), birth date and place, French/international phone
  numbers in any separator, NIR, card numbers, IBAN, BIC, RUM / mandate references, loan and file references,
  addresses, e-mail. Original file names are not kept in `documents.yaml` (`<kind>-document-<hash>.<ext>`). A
  document longer than the prompt limit is truncated and the command says so.
* **Files.** Writes keep BOM and CRLF line endings, fsync the file and its directory, and follow a symlink inside
  the folder; a symlink that leaves the folder is never read or written and is reported by `memory check`.
  `coach backup` takes the memory lock. A malformed proposal file is skipped with a warning. Symlinked `liabilities/`, `contracts/` or `documents/` folders that leave
  the memory folder are never read (reported as errors); a file symlink leaving it is not followed into backups either.
  A restored or moved history never points at the old folder (`core.worktree` is removed on open and on restore).
* **Regexes.** A pattern that matches everything (`.*`, `^`, `a|`, `(?:)`) is refused in annotations, `payment_match`,
  `merchant_match` and `bank_match`. Mixed line endings are preserved line by line (changed lines get the dominant one).

## Trust boundaries (read this before using documents or agents)

* **Document redaction is best effort.** It removes what it recognises (names after titles, cues, signatures, tables and
  "Nom | Prénom" headers, untitled `Prénom NOM` pairs, particles, French / Italian / German titles, IBAN, phones, NIR,
  cards, references, addresses, birth data, matricule / codice fiscale) and keeps organisations (generic org words plus
  well-known lenders and insurers) and every amount, rate, distance and duration (also numbers after amount labels
  such as "Capital emprunté : 250000"). It will miss things and over-redact others. **You must read the dry run
  (`coach memory doc extract ...` without `--send`) before any `--send`**: that payload is exactly what leaves the machine.
* **Tainted documents.** If an instruction-like passage exists ANYWHERE in the document ("note to the extraction model",
  "ignore previous instructions", "you must...", French equivalents), every extracted field is marked suspicious: the
  proposal can only be accepted with an explicit `--confirm-field PATH` for each one, after checking it against the
  document. Fields that differ from what memory holds today are flagged too.
* **Proposals fail closed.** A resolved proposal is moved to `.proposals/resolved/`, recorded in the change history
  (`accept <id>` commit; a rejection is an empty `reject <id>` commit) and in the registry. Any one of these makes it
  unusable, whatever the editable JSON says; a file whose inner id differs from its name is refused.
* **The history repository is data, not configuration.** Its config is rewritten to a fixed minimal template and
  `info/attributes` and hooks are deleted every time a store is opened and before every git call; git runs with
  hooks / attributes / pager / ssh / fsmonitor / external diff neutralised, so a planted filter or textconv never runs.
* **The boundary against agents is the permission system, not the files.** A process running as you can always edit
  `memory/` directly. What protects you: the Claude Code permission rules (the user's settings deny Bash commands that
  touch the history directory, the proposals directory, `proposal_key`, `memory accept`, `purge-history` and
  `script ` invocations), the terminal-only gate of accept / purge, the seal, the history attribution (`--source`),
  and the file modes (0700 / 0600). Keep those deny rules; do not work around a denial.

## Context for the coach (prep for E6)

`coach memory context [--md|--json] [--max-tokens N] [--names]` (function `coach.memory.context.build_context`):
members, accounts map (bank, owner, purpose; no labels, no IBAN), liabilities, assets and manual totals, contracts,
events, preferences, profile, open questions, annotation notes. Privacy by default: members' names and aliases,
account-holder names and owner ids from the database, and common given names are replaced by a pseudonym (the
member id, or `adult-1` / `kid-1` when the id itself would reveal the name; `[family]` for the surname,
`[person]` for unknown given names), IBAN / e-mail / phone / long numbers by the classifier's redaction layer, no raw
bank descriptions. `--names` keeps the names (local use only). `--max-tokens` drops the least useful parts first
(annotation notes, profile, event text, questions...), estimating 4 characters per token. Declaring the members in
`household.yaml` is what makes the pseudonyms meaningful; without it unknown names become `[person]`.

## Configuration

```toml
[memory]
history = true               # git change history of memory/
stale_months = 6             # liabilities/contracts facts
asset_stale_months = 3       # assets.yaml values
big_tx_threshold = 2000      # `questions generate`: unexplained one-off payments above this
question_min_stake = 300     # `questions generate`: merchants with less money at stake are not asked
[schedule]
memory_check = true          # warn-only memory check at the end of `schedule run`
```

## Known limits

* Writing is serialised by an `flock` on `memory/.lock` (same machine); there is no cross-machine merge.
* The YAML round trip keeps formatting for the styles of the real files; exotic YAML (anchors, multi-document
  files) is not supported and is rejected rather than rewritten.
* Document redaction and the person-like guards are heuristics; the dry run is the control.
* `memory check` judges "too broad" only on accounts with at least 30 transactions.
* `events.md` ids are `## slug` headings; a heading that is not a slug is prose, not an event.
