# Quality: fixtures, gold set, evaluations, usage, logs (E12)

How the coach is kept honest: parsers are tested on real-shaped (but invented) fixtures, the categorisation is scored against labels you confirmed, models
and coach answers are compared, LLM usage is visible, and every scheduled run leaves a log with no personal data in it.

Everything below is **local**. Two commands can spend money and send redacted data to a backend (`eval models`, `eval coach --run`): they refuse without
a terminal and a typed confirmation, go through the egress gate, and have a `--dry-run` that shows the exact payload size and an estimate first. There
is no `--yes` on them.

| Command | What | Model call? |
|---|---|---|
| `coach eval fixtures synth` | write invented descriptors of the shape of one bank's real ones | no |
| `coach eval gold bootstrap / sample / label / list / remove` | the gold set | no |
| `coach eval classify` | accuracy by transaction and by money, per source and per category | no |
| `coach eval models --models ...` | compare models on the gold merchants (shadow run) | `--dry-run`: no. Real run: yes (typed confirmation) |
| `coach eval coach --offline` | score the stored answers and insights | no |
| `coach eval coach --run` | ask the curated question suite through the real runner and score the answers | `--dry-run`: no. Real run: yes (typed confirmation) |
| `coach eval runs` | the stored runs, to compare over time | no |
| `coach usage` | LLM calls, tokens, cost, duration; where the calls went | no |
| `coach logs` | the structured logs of the scheduled runs | no |

## 1. Real-shaped parser fixtures (E12-1)

Parsers are only as good as the lines they were tested on, and real lines cannot be committed. `coach eval fixtures synth --bank <bank> [--n 200] [--seed 12]
--out tests/fixtures/<bank>.json` reads the local database (a restored scratch copy is the right place: `coach backup`, `coach restore FILE --to DIR`,
`COACH_*` variables) and learns the **shapes**: the prefixes and format words of the bank (`CARTE dd/mm`, `PRLV SEPA`, `ECH PRET`, `VIR INST`, `ANN CARTE`...), how long
the fields are (banks truncate), where the dates, amounts, references and foreign-currency lines stand, which fields are padded or ` | `-separated. It writes
descriptors of that shape that contain nothing taken from the data:

* every word that is not a format word (a merchant, a person, a town, a creditor) is replaced by a word made of invented syllables **of the same
  length**; a given name becomes another given name from the shipped list of common ones, so the person heuristics of the parsers still see a person;
  the same real word always becomes the same invented word (a merchant seen twenty times stays one merchant);
* every digit is re-drawn; an amount keeps its number of digits and whether it is round; dates move by one global shift (a whole number of weeks) so a card date,
  a booking date and a due date keep their relations; Fortuneo's positional references (`<date>T00:00:00-<n>`) keep their position;
* an IBAN-like string becomes a **test IBAN**: the country letters, check digits `00`, random digits, redrawn until the mod-97 check fails too;
* the household (holders, family names, given names, the other banks) is invented the same way, and written in the fixture so a test can rebuild the same `Household`.

**It never emits a real token.** At generation time the output is checked against: the household guard terms (members, places, employers), every
alphabetic word of every merchant key, description, counterparty and account name of the database, every merchant key, and every digit string of 8+ characters.
A finding raises `SynthLeak` before anything is written (the finding says its kind, never the text). Format words that are also household terms are
removed from the format list at runtime. The output is also covered by the repository test `tests/test_egress_policy.py::test_no_file_of_the_repository_carries_a_token_from_the_real_data`
(salted hashes of real tokens; it scans `src`, `tests`, `docs`, `web/src`, `.claude/skills` and `evals`).

Each entry keeps the type the real shape had (`expect.tx_type`); a shape whose type changes once invented is retried and then dropped, a real `other` line
is skipped (the report says how many). Committed: `tests/fixtures/{fortuneo,caisse_epargne,cic,revolut}.json`, 200 transactions each (CIC has only 27 real
lines: they are drawn with replacement). Regenerate with the same command; the seed makes it reproducible for a given database.

`tests/test_quality_fixtures.py` runs over them: per-bank **coverage** (every line recognised, no `other`, the type of its real shape), **stable keys**
(the same transactions get the same `tx_key`s whatever the page size, and keys are unique), **pagination** (the original query is kept on every page with the
continuation key), **dedup** (the same pages again add nothing), **pending** (replaced at each sync, the booked version wins), a **rate limit in the middle of the
pages** (everything rolls back), all through the mocked Enable Banking client and `normalize`. The synthesizer itself is tested on an invented "real" database full of
sentinel words that must not appear in the output.

## 2. The gold set and the accuracy evaluation (E12-2)

### The gold set

Table `gold_labels` (migration 0021): `tx_key`, `category`, `labeled_by` (`user` | `memory` | `correction`), `labeled_at`, `note`, `origin`. A gold label is **your**
truth about one transaction. It is only read by the evaluations: it never changes a category of the app.

`coach eval gold bootstrap` adds what you already decided, **marked by origin**: `override` (a per-transaction category), `split` (the dominant part of a split),
`annotation` (a memory annotation with an explicit category), `merchant_label` (a merchant label of yours, source `user`). When one transaction has several, the
strongest is its origin (override > split > annotation > merchant label). It is safe to run again: rows follow your decisions (a changed label updates, a label you
withdrew drops its rows). A label given by hand (`manual`) is never touched. `--dry-run` previews.

`coach eval gold sample --n 300 [--strategy money|stratified] [--seed 7]` proposes transactions to label: only ones an automatic step decided (LLM, web, kNN, entity, rule, none),
never one already in the gold set or already decided by you. `money` draws with a probability proportional to the amount (so the money-weighted accuracy is estimated
without bias); `stratified` takes the same number from each (source, category group). `coach eval gold label [--n 20] [--strategy ...] [TX_KEY ...]` is the terminal loop:
for each transaction `explain()` and the current label, then **a**ccept / **f**ix (type an id or a word to search) / **s**kip / **q**uit. The web app's **Gold set** page does the same
(a card per transaction with a category picker; writes through the API with the CSRF token). Both write `gold_labels` and nothing else.
`coach eval gold list` / `remove` show and edit it.

### `coach eval classify` (method v2)

Offline and deterministic: nothing calls a model. It answers **two different questions, reported apart, never as one mixed headline**:

**1. Classifier accuracy on merchant-level truth (memory off).** Only gold rows that say "this merchant is this category" (`merchant_truth`): a label given by hand, a merchant label of
yours that replaced a model label, a memory annotation **without** a context condition. The prediction is what the automatic chain says without memory annotations and without your own decision the label
comes from (`classify/rules.resolve(..., ignore=)`). Reported by transaction and by money, for the LLM alone (`llm_only`: `llm` + `llm_web`), at **leaf**, **group** and **equivalent** level
(`src/coach/classify/taxonomy_equivalence.yaml`, or your copy `config/taxonomy_equivalence.yaml`: housing.mortgage ~ debt.loan_repayment, housing.rental_property_loan ~ debt.loan_repayment; pairs, not
transitive), with precision / recall / F1 per category, confusions, coverage, per prediction source, per origin and per annotation kind.

Rows that depend on context are `context_rule`: an annotation with `category_in`, `weekdays`, `amount_min/max`, `date_from/to` or `tx_keys`, a per-transaction override, a split. The classifier is not expected to know
them, so they **never count against it**. For `category_in` annotations a separate line reports the **base category agreement**: how often the classifier's category is one of the allowed base categories.

**2. Pipeline with memory (what you see).** The final category of **every** gold transaction (annotations, overrides, splits applied). It should be about 100 %: it is a regression test of your memory and rules.
The failures are listed (an annotation shadowed by an earlier one, or broken; an override or split that no longer says what the label says), by kind and with their transactions.

Also left out of the classifier figures and counted apart: **tautological** rows (a prediction from the very layer the gold label is: a user label against a `user` prediction; a disagreement means you changed your mind) and
**no counterfactual** rows (a merchant you labelled yourself: the model's own label was replaced and nothing else would have answered).

Every run is stored in `eval_runs` (`coach eval runs`, `--show ID`) with `method: v2`, both metrics, and a **fingerprint** of what it depends on (taxonomy, rules, label prompt, model settings, equivalence map, memory annotations,
gold set); the next run names what changed. Runs stored by the first version mixed both questions in one figure: they are marked **`method_v1` (deprecated)** when read and are never compared with v2 runs.
Warnings: the classifier (by transaction, by money, LLM) falling more than 2 points since the previous v2 run, or the pipeline with memory falling more than 1 point (a memory / rules regression).
It runs on demand, **after every `classify run` that labelled something**, and **once a week in `coach schedule run`** (step `eval`, warn-only; the log keeps `classifier_tx_pct`, `classifier_money_pct`, `pipeline_tx_pct`;
`[schedule] eval = false` switches it off; the date of the last run is `<data_dir>/logs/eval-classify.last`, 0600).

Loan instalments: a `loan_payment` on a non-rental account whose bank text matches the `payment_match` of a liability of kind `mortgage` is categorised **housing.mortgage** (it was `debt.loan_repayment`, "unknown loan");
on a rental account it stays `housing.rental_property_loan`; any other stays `debt.loan_repayment`. This changes the category of those payments in reports and budgets (a memory annotation still wins).

How to read the first numbers: label a `money` sample before drawing conclusions about a model; a gold set made only of annotations says little about the classifier.

## 3. Comparing models (E12-3)

`coach eval models --models haiku,sonnet[,ollama:<model>] [--sample 100] [--seed 7] [--batch 40] [--dry-run] [--show-payload]` re-labels the merchants that have gold transactions with each model and
scores the answers against the **merchant-level truth** of the gold set only (the same subset as the classifier metric: a model cannot know a context rule) by transaction and by money, at leaf and equivalent level, next to what the classifier (memory off) says today for the same transactions. It is a **shadow run**:
the answers live in memory, and nothing is written to `merchants`, `tx_overrides`, `merchant_eval` or memory (a test snapshots the tables before and after). It records the call in `llm_usage`
(purpose `eval`) and the result in `eval_runs` (kind `models`, one row per model): accuracy, **cost**, **latency** (wall time and per request), tokens.

It uses the same request as `classify run`: the same candidates (a person-like key is withheld; a merchant a type rule decides is not asked), the same person guard, the same redaction, the same prompt. Two
deliberate differences: the merchants being evaluated are **never shown to the model** as the user's examples or as nearest-neighbour hints (they would hand it the answer), and the Message Batches API is
not used (one request after the other, so latency is measured).

* `--dry-run` builds the exact requests and prints, per model: the number of requests, the **payload size in bytes**, the estimated tokens, an **estimated cost** (the API price table; notional for a subscription;
  free for ollama; "unknown price" for a model without one), the destination and whether the egress policy allows it. `--show-payload` prints the first request's dynamic part. No model is called, nothing is written.
* A real run needs a **terminal** and the typed phrase `send gold merchants`, and every request goes through the egress gate (`[privacy] local_only` refuses a cloud model before anything is built;
  the journal row's purpose is `eval.models`). `haiku` / `sonnet` are asked of the configured `[llm] backend` (`claude-code`, or `anthropic-api`; `claude-code` when that is `ollama`), `ollama:<model>` of the local server.

## 4. LLM usage (E12-4)

`coach usage [--days 30] [--json]` and the **AI usage** page: per job / purpose / backend / model: calls, tokens in / out (and cache), cost, duration; a per-day series; the egress journal's destinations (host, bytes,
refused calls: never a payload); the month against the threshold. Jobs are named from the `llm_usage.purpose`: `classify run`, `classify compare`, `classify enrich`, `memory doc extract`, `coach ask`, `coach digest`,
`coach skill <id>`, `eval models`, `eval coach`.

Costs are honest about what they are: a **`claude-code`** call has a **notional** cost (API prices for the tokens a subscription call used: nothing is billed); an **`anthropic-api`** cost is an estimate from the
price table; **ollama** is local and free; a model with **no known price** has no cost (counted apart, never shown as 0).

`[usage] monthly_warn_usd` (default 0 = off) raises the **`llm_usage_high`** alert when the month's cost passes it (medium; high at twice the threshold; one event per month). `[usage] include_notional = false` leaves
subscription calls out of that sum. The alert exists in the **local feed only**: no channel (ntfy, e-mail, Telegram, macOS) ever receives it and the weekly digest does not count it.

## 5. Coach answer checks (E12-5)

`coach eval coach --offline [--skill X] [--limit N] [--days N]` scores the answers and insights already stored (`insights` written by a model), and `--run` asks a curated suite through the real runner and scores the
answers the same way. The checks (each pass / fail / not applicable):

| Check | What |
|---|---|
| `numbers_traced` | every number of the text was returned by a tool: the ratio, and the unverified numbers listed (years and the integers 0-31 are not numbers, as in the runner) |
| `evidence_refs` | every evidence ref cited in the text (`h_...`, `rec_...`) is one a tool returned in that session |
| `ai_label` | a model-written text carries the AI-generated flag |
| `compliance` | `coach.compliance` finds no ISIN, product name or recommendation |
| `sections` | what the skill's prompt requires: `monthly-review` has a "Three actions" heading and **exactly three** numbered actions |
| `disclaimer` | a text about saving or investing ends with the general-advice sentence (any language); `subscription-audit`, `mortgage-check`, `tax-helper`, `contract-check` carry their own required wording |
| `length` | at most the number of words the skill's prompt asks for (+10 %: models count loosely), not empty |
| `completed` | (live runs) the run finished normally and called a tool, unless the question needs none |

The suite is `evals/coach_questions.yaml`: **generic questions only** (no name, merchant, amount, date or address; a test checks it), one or more per runnable skill, in English, French and Italian, plus an
investment-product question that must be refused with the disclaimer and a prompt-injection-flavoured one. A live run needs a terminal and the typed phrase `send coach questions`, is gated by the egress policy
(`[privacy] local_only` refuses a cloud backend), `--dry-run` prints the questions, the prompt sizes and the per-run cost bound (`[coach] max_budget_usd` x the skill's tool factor), and the answers are **not stored
as insights** (the feed must not fill with test questions). The usage is recorded with purpose `eval:coach`, the result in `eval_runs` (kind `coach`). The journal row's purpose is the coach's own (`coach.ask`,
`coach.skill`). Tests run the whole path with the fake `claude` of `tests/fake_claude.py`.

## 6. Logs and observability

`coach schedule run` writes, next to the one-line `schedule.log`, **JSON lines** to `<data_dir>/logs/runs.jsonl`:

```
{"ts": "...", "run": "20261005-073000-ab12", "event": "run_start", "version": "..."}
{"ts": "...", "run": "...", "event": "step", "step": "sync", "status": "ok", "ms": 1234, "counts": {"accounts": 3, "new": 12, "failed": 0}}
{"ts": "...", "run": "...", "event": "run_end", "outcome": "ok", "exit": 0, "ms": 5678, "steps": {"ok": 11, "skipped": 1}}
```

**No personal data**: a line holds only a run id, a step name from a fixed list, a status (`ok` | `warn` | `error` | `skipped`), a duration, integers with clean names, and for an exception its **class name** (an
exception text can quote the data that failed, so it is never kept; the one-line `schedule.log` and the warnings printed to stdout follow the same rule; an expiring consent names the bank, an institution, in those two places only, never in `runs.jsonl`). A test
runs a scheduled job on synthetic data full of sentinel words (merchants, a person, a town, a note, long numbers) and greps every file of the log folder.

**Rotation** (`[logs] max_kb = 512`, `keep = 5`, `max_age_days = 90`): `runs.jsonl`, `schedule.log` and `coach-init.log` are rotated by size at the start of a run (`name.1` ... `name.<keep>`; the small ones at a
quarter of `max_kb`, at least 64 KiB) and rotated files older than `max_age_days` are deleted (a live file never is). launchd's `schedule.out.log` / `schedule.err.log` are held open by the running job, so they are
rotated when the run is over (renaming them earlier would send the rest of the run's output to the renamed file); the next run opens a fresh one.

`coach logs` lists the runs (one line each: id, start, outcome, duration, failed / warned steps); `--tail N` the last N events; `--run ID` (a prefix) the events of one run; `--json`. The **Connections** page
and `GET /api/v1/health` show the last run (steps, durations, failed steps). Steps: `sync`, `consents`, `normalize`, `transfers`, `classify`, `memory`, `analytics`, `networth`, `coach`, `alerts`, `security`, `eval`, `egress-journal`.

## 7. Known gaps

* The gold set is only as good as your labelling: a bootstrap alone (annotations, merchant labels) measures agreement with your refinements, and the model's own label of a merchant you relabelled is gone (the
  `merchants` table keeps one row per merchant), which is why those rows are "no counterfactual". Label a `money` sample for a real accuracy figure.
* `eval models` costs: one request per batch of 40 merchants per model; `--sample` (default 100 merchants) bounds it, `--dry-run` shows the estimate. The estimate counts the whole prompt as new input (the API's
  prompt cache would make it cheaper) and a fixed output size per merchant.
* The coach checks cannot judge whether an answer is *useful* or a figure *correct*: they check traceability, shape and compliance. `numbers_traced` relies on the runner's own ledger (what the tools returned
  during the run), so an offline score is only as good as the `unverified_numbers` stored with the insight.
* Fixtures are shapes: they exercise the parsers' structure, not every real-world oddity. A new oddity found in real data needs a new synth (the real file is never committed) or a hand-written line in `tests/test_parsers.py`.
* Logs are rotated, not encrypted: they hold no personal data, but they are files in `data_dir` (0600, folder 0700).
