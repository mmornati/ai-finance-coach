# AI/LLM Layer for a Self-Hosted Personal Finance Coach (categorization + Claude Code analysis)

Research date: 2026-10-04. About 16 tool calls. Primary sources were preferred (Anthropic docs, arXiv, Plaid). Where only aggregators or my own reasoning were available, this is marked.

## Q1. Accuracy benchmarks for LLM transaction categorization (local vs cheap API models), latency, cost per 1000 transactions

### Takeaway
The published evidence is thin and mostly about SME/business transactions, not consumer EU bank strings. Zero-shot LLMs reach about 80% accuracy against human labels on a 24-category scheme. They are near 100% on frequent categories and weak on rare or ambiguous ones. Small fine-tuned models (0.8B to 8B) reach about 95-97% F1 on merchant extraction. Accuracy comes mostly from the pipeline (rules, memory, similar examples), not from model size. At Claude Haiku 4.5 list prices, LLM cost is well under $1 per 1000 transactions.

### Cited Findings
- ICAIF '24 workshop paper on SME bank transactions: 14,799 transactions in 24 categories, labelled by OpenAI "GPT-4 Mini" (zero-shot). It reached **80% accuracy vs manual expert annotation**. Frequent categories (Charges/Fees, Tax) were near 100%. Other Income, ATM Withdrawals, Cheques and Debt/Loan Repayments were much worse, and Sundries/Other were often confused. An embedding-based classifier trained on the labels reached about 79%. Cross-company generalisation dropped to **48%** (train on two companies, test on the third). The authors suggest few-shot learning for rare categories. — [Jess et al., LLMs for the categorisation of SME bank transactions (ICAIF'24 workshop)](https://www.sea.dev/assets/ICAIF_workshop_paper-llms.pdf)
- arXiv 2606.08051 (June 2026), "How Small Can You Go?". It ran 23 LoRA fine-tuning runs on Gemma 3 (270M-4B), Qwen 3.5 (0.8B-4B), Aya 3 (3.35B) and Llama 3.1-8B for **merchant information extraction** from transaction strings. Results: Llama 3.1-8B rank-32 **96.95% F1** (production baseline), rank-8 96.75%, **Qwen 3.5-4B 96.60% F1** with JSON-only prompting (91.67% exact match) at **3.8x lower latency** than the 8B model, **Qwen 3.5-0.8B 94.75% F1**. Aya lost 2.7-5.1 points in deployment. The paper has no GPT/Claude comparison. — [arXiv 2606.08051](https://arxiv.org/abs/2606.08051)
- Other SME categorisation work (arXiv 2508.05425) and the Kansas City Fed "Evaluating Local Language Models" paper also exist. The search-engine summary of them (e.g. "fine-tuned 73.4% vs GPT-4 zero-shot 60.4%", "Vicuna-13B 0.826") was **not verified against the full text**, so treat those numbers with caution. — [arXiv 2508.05425](https://arxiv.org/abs/2508.05425); [KC Fed RWP 23-12](https://www.kansascityfed.org/documents/9862/rwp23-12cookkazinnikhansenmcadam.pdf)
- In financial few-shot classification (Banking77-type intents), in-context learning with LLMs can beat fine-tuned masked LMs with fewer examples. **Selecting the most similar examples as few-shot demonstrations consistently beats random selection.** — [Loukas et al., Breaking the Bank with ChatGPT (FinNLP 2023)](https://arxiv.org/pdf/2308.14634); [Making LLMs Worth Every Penny (arXiv 2311.06102)](https://arxiv.org/pdf/2311.06102)
- Production latency example: Norman Finance's categorizer uses the TypeSafe "Jev" decision model. In a dev test with 68 eligible categories it had a **median latency of 265 ms and p95 of 302 ms**, with 30/30 valid outputs. These are synthetic tests, not an accuracy benchmark. — [Norman Finance blog](https://norman.finance/de/en/blog/fast-ai-categorization-jev)
- Plaid says its upgraded (LLM-enhanced) categorization model improved accuracy by "up to 10%" on primary and "20%" on detailed categories. It gives no absolute numbers. — [Plaid blog](https://plaid.com/blog/ai-enhanced-transaction-categorization/)
- **Current Anthropic API prices (official):** Claude Haiku 4.5 costs $1/MTok input and $5/MTok output. A 5-minute cache write costs $1.25/MTok and a cache hit $0.10/MTok. Batch API pricing is $0.50 in / $2.50 out. Sonnet 5 / 5.5 cost $2/$10 (the introductory $2/$10 for Sonnet 5 became the standard price, and the planned rise to $3/$15 was cancelled). Opus 5.5 costs $4/$20. Batch and caching discounts stack. Claude 4.7+ models use a new tokenizer that produces about 30% more tokens for the same text. — [Anthropic pricing docs](https://platform.claude.com/docs/en/about-claude/pricing)
- Aggregator prices for competitors were **inconsistent or wrong**. One listed GPT-4o mini at $2.50/$10, which contradicts its long-standing $0.15/$0.60. Another listed "Gemini 3.5 Flash" at $1.50/$9. I did not verify current OpenAI (GPT-5.x mini/nano), Gemini Flash/Flash-Lite or Mistral Small prices from primary sources. — [intuitionlabs aggregator](https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude)

### Inferences
- **Cost per 1000 transactions with Haiku 4.5 (my calculation from the official prices):** assume about 400 input tokens per transaction (instructions, category list and 5 retrieved examples) and about 30 output tokens (JSON).
  - Real-time: 0.4 MTok × $1 + 0.03 MTok × $5 ≈ **$0.55 per 1000**.
  - Batch API: ≈ **$0.28 per 1000**.
  - Sending 20-50 transactions per request with a cached system prompt and category list cuts input cost further, to roughly $0.10-0.20 per 1000.
  - A household has about 100-300 transactions/month, and after rules and memory only 10-30% reach the LLM. The **LLM cost is therefore cents per year**, so cost should not drive the model choice. Privacy and convenience should.
- Local models: the evidence (2606.08051) shows 4B-class models (Qwen 3.5-4B, Gemma 3 4B) are good enough at **structured extraction** when fine-tuned. Zero-shot small models will likely trail Haiku-class models on ambiguous Italian/French descriptors. A reasonable default is a local Qwen 3.x/3.5 4-8B or Gemma 3 4-12B via Ollama with JSON-schema constrained output for the "unknowns" step, with optional fallback to Haiku 4.5 for items below the confidence threshold. This is an inference; no head-to-head benchmark on EU consumer data was found.
- The 48% cross-company result suggests that **personalisation (the user's own labelled history) matters more than model choice**. Generic models do not know that "BONIFICO A ROSSI M." is your rent.

### Gaps
- No public benchmark found that compares Ollama-served Llama 3.x/4, Qwen 3, Gemma 3, Ministral or Phi against Haiku 4.5 / GPT mini / Gemini Flash on the same consumer transaction dataset. The project should build its own eval set of 300-500 hand-labelled transactions.
- Current OpenAI, Google and Mistral small-model prices were not verified from primary pricing pages.
- No measured tokens/sec data for local models on typical home hardware was collected.

## Q2. Best-practice hybrid pipeline and merchant normalization (IT/FR descriptors)

### Takeaway
Industry practice and the literature agree on a cascade:
1. deterministic rules
2. memory of user corrections
3. similarity retrieval over the user's labelled history
4. an LLM choosing from a closed list with structured output and retrieved few-shot examples
5. a confidence threshold that routes to a human review queue, whose corrections feed back into stage 2

### Cited Findings
- Norman Finance's production pipeline, in order:
  1. explicit accounting rules
  2. "company memory" (past customer corrections)
  3. an LLM ("Jev Choice") that picks from a **pre-filtered list of eligible categories**. The list is filtered by active status, account type and **payment direction**. The model receives merchant, payment reference, amount and **up to five similar past choices**.

  Invalid or low-confidence answers fall back to local logic. — [Norman Finance blog](https://norman.finance/de/en/blog/fast-ai-categorization-jev)
- Algoan describes pre-annotation with an LLM using "explicit instructions with few-shot learning" and an enforced structured output schema. High-confidence items are processed automatically, ambiguous ones go to expert annotators, and corrections feed the next model iteration. — [Algoan blog](https://www.algoan.com/en/blog/from-raw-data-to-model-update-an-automatic-categorization-pipeline)
- Similar-example selection beats random few-shot selection. — [arXiv 2308.14634](https://arxiv.org/pdf/2308.14634)
- JSON-only prompting gave the best accuracy/latency for small extractors (Qwen 3.5-4B 96.60% F1). — [arXiv 2606.08051](https://arxiv.org/abs/2606.08051)
- The Future Flow OSS project's plan: "use the local LLM to categorize transactions not confidently handled by rules". — [Future Flow issue #68](https://github.com/ExNihilo20/future-flow/issues/68)
- Firefly III has an open feature request for LLM categorization (Ollama/LocalAI). As of early 2026, Firefly III and Actual Budget had **no native LLM categorization**, while ezBookkeeping supports OpenAI, OpenRouter, Ollama and Google AI. — [Firefly III #9753](https://github.com/firefly-iii/firefly-iii/issues/9753); [ezBookkeeping comparison](https://ezbookkeeping.mayswind.net/comparison/)
- Bank descriptors are hard: "extreme abbreviations, limited context, and domain-specific terminology". — [Jess et al.](https://www.sea.dev/assets/ICAIF_workshop_paper-llms.pdf)

### Inferences
These are design recommendations from my domain knowledge, not sourced.
- **Normalization stage (deterministic, before any matching):**
  - Strip prefixes and boilerplate:
    - Italian: "PAGAMENTO POS", "PAG. POS", "OPERAZIONE CARTA", "ADDEBITO SDD", "ADDEBITO DIRETTO", "BONIFICO A/DA", "PRELIEVO BANCOMAT"
    - French: "CB", "PAIEMENT PAR CARTE", "PRLV SEPA", "VIR SEPA", "VIR INST", "RETRAIT DAB"
  - Strip dates ("CB CARREFOUR 12/09"), card PANs (masked), terminal and city suffixes, and SEPA mandate IDs (RUM/ICS).
  - Keep the SEPA creditor identifier (ICS) and creditor name. They are stable keys and ideal for recurring detection and rules.
  - Use the PSD2 aggregator's structured fields (creditorName, remittanceInformation, bank transaction code) when present, since they beat parsing free text.
- **Stage keys:**
  1. exact rule on normalized merchant, IBAN or ICS
  2. user correction memory keyed by normalized merchant
  3. kNN over embeddings of the normalized descriptor (a local multilingual embedding model such as bge-m3 or multilingual-e5 via Ollama; not benchmarked here), with auto-accept if the top-k neighbours agree above a similarity threshold
  4. LLM with the closed category enum as a JSON-schema / grammar-constrained output, the top-5 retrieved labelled neighbours as few-shot examples, and the amount sign as a hard filter on allowed categories
  5. review queue for low confidence or disagreement between kNN and the LLM
- Ask the LLM for the normalized merchant name as well as the category. That extraction fills the rule/memory table, so each merchant needs the LLM only once.

### Gaps
- I found no published, sourced catalogue of Italian/French bank descriptor patterns. Build one from real exports.
- No measured accuracy uplift numbers for "rules + memory + kNN + LLM" vs LLM-only on consumer data were found.

## Q3. Taxonomies (Plaid PFC, MCC, YNAB-like) and what "Jev" is

### Takeaway
Plaid's Personal Finance Category (PFC) taxonomy is the best-documented open reference: two levels, about 16 primary and about 104 detailed categories, with confidence levels. PFCv2 (December 2025) added income, loan and fee subcategories. **"Jev" is not a taxonomy.** It is TypeSafe's decision/choice model, used by Norman Finance (German bookkeeping) for categorization from September 2026.

### Cited Findings
- Plaid PFC: "16 primary and 104 detailed categories", designed around what users expect in a PFM app, with a confidence-level field. — [Plaid taxonomy blog](https://plaid.com/blog/transactions-categorization-taxonomy/); [PFC migration guide](https://plaid.com/docs/transactions/pfc-migration/)
- PFCv2 (December 2025) added 6 income subcategories, 6 loan-disbursement and 3 loan-repayment subcategories, plus bank-fee subcategories. — [Plaid PFC migration guide](https://plaid.com/docs/transactions/pfc-migration/index.html.md)
- Other European categorization taxonomies exist (finAPI PFM, FinTecSystems/Tink "facts" taxonomy). — [finAPI PFM docs](https://documentation.finapi.io/access/personal-finance-management); [FinTecSystems taxonomy](https://guide.fintecsystems.com/xs2a/integration-cs/additional-guides/the-facts-system/the-categorisation-taxonomy)
- Jev: "TypeSafe's decision model". Its "Choice" capability picks one option from a supplied set, here the company's existing chart of accounts. Norman started using it in production on 2026-09-25. — [Norman Finance blog](https://norman.finance/de/en/blog/fast-ai-categorization-jev)

### Inferences
- Use a PFC-inspired two-level taxonomy that the user can rename (YNAB-style editable groups). Keep a stable internal enum for the LLM and map user-facing labels on top.
- Add EU-specific leaves: utilities split into luce/gas/acqua (electricity/gas/water), telecom, RCA/auto insurance, condominio, bollo auto, mutuo (mortgage), LOA/leasing, tickets restaurant/buoni pasto, and taxes (F24, impôts).
- MCC codes are only available for card transactions and are rarely exposed by PSD2 APIs (unverified). Treat them as an optional signal.
- A YNAB-like "jobs" layer (needs, wants, savings, true expenses) can sit as a second dimension on top of categories.

### Gaps
- I did not fetch YNAB's default category list or an official MCC list. I did not check whether Enable Banking exposes MCC.
- I could not confirm whether the user meant something else by "Jev". TypeSafe's Jev is the only match found.

## Q4. Claude Code headless (`claude -p`, Agent SDK) on a Max subscription: policy, limits, cron; API alternative; skills/subagents/MCP

### Takeaway
As of October 2026, running the **unmodified Claude Code CLI** (`claude -p`) from your own cron for **your own personal use** on your own Max plan is within the documented model.
- It draws on normal subscription limits. The planned separate "Agent SDK credit" pool was **paused on June 15, 2026**.
- Advertised limits "assume ordinary, individual usage".
- OAuth subscription credentials must not be used by other products or routed for other users.
- Developers building products, including with the Agent SDK, should use API keys.

For a self-hosted app, the robust split is:
- deterministic and LLM categorization via a local model or the Haiku 4.5 API (an API key, billed per token)
- on-demand or scheduled personal deep analysis via `claude -p` with project skills and an MCP server over the finance DB, on the owner's own Max plan

### Cited Findings
- Official policy: "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications." "Developers building products or services that interact with Claude's capabilities, including those using the Agent SDK, should use API key authentication." Anthropic does not permit third parties "to route requests through Free, Pro, or Max plan credentials on behalf of their users" or to "collect, store, or intermediate Claude.ai credentials or session tokens." The page also says "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK", and that Anthropic may enforce "without prior notice". Free/Pro/Max use falls under the Consumer Terms. — [Claude Code Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- Agent SDK with a Claude plan: Anthropic had announced a separate monthly credit for SDK, `claude -p` and third-party app usage (Pro $20, Max 5x $100, Max 20x $200). As of June 15, 2026 this is **paused**: "Claude Agent SDK, `claude -p`, and third-party app usage still draw from your subscription's usage limits." — [Claude support: Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- Third-party projects reacting to the announced change (e.g. Multica daemon) treated `claude -p` as moving to the separate pool. That is now superseded by the pause. — [multica issue #2563](https://github.com/multica-ai/multica/issues/2563)
- Secondary sources report that Anthropic deployed server-side enforcement (January 2026) so that consumer OAuth tokens fail outside Claude Code/Claude.ai. They also report that a personal cron job is still covered, while many parallel agents exhaust weekly Max quotas quickly. These are not confirmed by a primary source. — [autonomee.ai explainer](https://autonomee.ai/blog/claude-code-terms-of-service-explained/); [claudefa.st guide](https://claudefa.st/blog/guide/development/claude-code-subscription)
- Headless mode docs: [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless) (referenced, not fetched in detail).
- API alternative costs: Haiku 4.5 is $1/$5 (batch $0.50/$2.50; cache hit $0.10). Sonnet 5.5 is $2/$10. Opus 5.5 is $4/$20 with cache hits at 0.05x ($0.20). Web search costs $10 per 1000 searches; web fetch has no extra charge. A 5-minute cache pays off after one read, a 1-hour cache after two. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Inferences
- **Allowed and low-risk:** a cron/systemd timer on the owner's own machine that runs `claude -p "/monthly-review" --output-format json` in the project directory, for the subscriber only, at low frequency (daily or weekly). This counts as ordinary individual use under the docs above.
- **Not allowed:** exposing the coach to family or other users through your Max login, extracting the OAuth token into your own app or Agent SDK code, or building a product that uses it. Use an API key for any of these.
- **Gray area:** high-volume automated loops (e.g. categorizing every transaction through `claude -p`). This conflicts with the "ordinary, individual usage" assumption, is wasteful, and makes the system depend on a policy that has already changed twice in 2026. Keep categorization off the subscription.
- Analysis framework mapping:
  - Claude Code **skills** (SKILL.md folders in `.claude/skills/`) hold one skill per analysis, e.g. `subscription-audit`, `monthly-review`, `contract-check`, `offer-comparison`, `loan-analysis`.
  - **subagents** run isolated deep dives (e.g. a web-research subagent for offer comparison).
  - an **MCP server** (or a read-only SQLite CLI) exposes the transaction DB, recurring-payment table and user-provided contracts.
  - **hooks** or a wrapper script write outputs back as markdown or JSON reports.
- Always design an **API-key fallback path** (same skills, run with `ANTHROPIC_API_KEY` and Sonnet 5/Haiku 4.5) in case subscription terms change again.

### Gaps
- I did not fetch the Consumer Terms text itself (e.g. clauses on automated or non-human access) or the current Max weekly usage-limit numbers.
- I did not verify current Claude Code skills/subagents documentation details in this pass.

## Q5. Subscription / recurring-payment detection algorithms and OSS implementations

### Takeaway
Detection is a well-understood deterministic problem:
- group by normalized merchant (or SEPA creditor ID)
- require at least 3 occurrences
- classify inter-payment gaps into cadence buckets with tolerance
- require amount stability (±15% or coefficient of variation ≤ 0.15)
- add price-hike detection and user confirmation

Use an LLM only to label or explain the results, not to detect them.

### Cited Findings
- offbook PR #413: a deterministic detector over non-transfer outflows. It groups by normalized merchant and requires **≥3 occurrences**, a cadence (weekly/monthly/annual) where **every gap falls inside that cadence's window**, and **amounts within ±15% of the group average**. — [gregwym/offbook PR #413](https://github.com/gregwym/offbook/pull/413); [issue #368](https://github.com/gregwym/offbook/issues/368)
- Example cadence windows: weekly 7±2 days, monthly 30±5, quarterly 90±10, annual 365±15; amount stability as coefficient of variation ≤ 0.15. — [search summary of fidy-ai / related issues](https://github.com/B4rz99/fidy-ai/issues/24) (partly from the search snippet, not fully verified)
- Subscription-Leak-Detector: alias-aware blocked fuzzy matching of noisy descriptors, interval-coverage validation of cycles, and persistent price-hike detection with fixed-point arithmetic. Its reported F1 = 1.00 is on a small "evidence-supported" sample and not meaningful as a benchmark. — [himanshuu21/Subscription-Leak-Detector](https://github.com/himanshuu21/Subscription-Leak-Detector)
- Other examples: CS03-Recurring-Payments (merchant normalization, cadence, amount variation, refund filtering, user review workflow); recur-scan (ML-based); FinanceManager PR on tightening amount clustering. — [CS03](https://github.com/lasyakota-hue/CS03-Recurring-Payments); [recur-scan](https://github.com/EbenezerOladipupoBankole/recur-scan); [FinanceManager PR #805](https://github.com/avresial/FinanceManager/pull/805)

### Inferences
- In SEPA countries, **SDD mandates (PRLV SEPA / ADDEBITO SDD) with the same creditor ID** are close to a ground-truth recurring signal. Card-based subscriptions (Netflix, Spotify) need descriptor clustering.
- Handle variable bills (energy, telecom with consumption) with a wider amount tolerance but a strict cadence. Bimonthly cadence (60±7) is common for Italian utilities and is missing from most OSS buckets.
- Outputs: next expected date, expected amount, annualized cost, price-change alerts and "missed payment" alerts. These feed the "cheaper alternatives" skill.

### Gaps
- No academic benchmark or published precision/recall on real data was found for recurring detection.
- Actual Budget's / Maybe Finance's built-in recurring detection was not examined.

## Q6. Agentic web search for cheaper offers (Facile.it, SOStariffe, Selectra, LeLynx): feasibility and pitfalls

### Takeaway
It is feasible as an on-demand Claude Code skill with web search/fetch. The cost is small ($10 per 1000 searches on the API, or included in Max usage). Accuracy risk is high: prices on comparators are dynamic, often personalised by consumption/postcode, sometimes behind forms, and LLMs may hallucinate or cite stale promos. Outputs must be cited, dated and framed as leads to verify.

### Cited Findings
- Claude API web search costs $10 per 1000 searches plus tokens. Web fetch has no extra charge beyond tokens. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Inferences
These are not source-backed.
- Comparators (Facile.it, SOStariffe.it, Segugio, Selectra, LeLynx, Hello Watt) usually need form input such as consumption kWh/Smc, CAP/postcode or vehicle data, and render with JavaScript. Plain fetch often returns partial content, and automated form submission may breach their ToS or hit bot protection. CAPTCHAs must not be bypassed.
- Better sources:
  - Italy: the official ARERA **Portale Offerte** (portaleofferte.it), an official comparator of electricity/gas offers.
  - France: the CRE/médiateur's **comparateur-offres.energie-info.fr**.
  - Telecom: operators' own offer pages.
- Guardrails:
  - require a URL and a retrieval date for every price
  - compute annual cost from the user's actual consumption pulled from transactions/bills
  - flag introductory or "promo for 12 months" prices
  - never auto-switch; present a shortlist
  - re-verify before acting

### Gaps
- I did not verify the terms of use or API availability of Facile.it, SOStariffe, Selectra or LeLynx, or the current URL/API of ARERA's and the French official comparators.
- No published evaluation of LLM agents' accuracy on price comparison was found.

## Q7. Contract/PDF analysis for cancellation terms (Bersani, Loi Hamon, Loi Chatel)

### Takeaway
LLMs can extract notice periods, renewal dates, early-termination fees and penalty clauses from contract PDFs. They should be grounded in a small, curated rules file of the legal regimes. The key regimes:
- Italian Bersani decree (L. 40/2007): telecom contracts can be withdrawn at any time, with notice of at most 30 days and only cost-justified charges.
- French Loi Hamon (2014, in force 2015): free termination of auto/home insurance after 1 year.
- French Loi Chatel: insurers and tacit-renewal service providers must notify the renewal deadline.

### Cited Findings
- Legge Bersani (D.L. 7/2007 converted by L. 40/2007, art. 1): adhesion contracts with electronic-communications operators must allow withdrawal "without time constraints" and without charges not justified by operator costs, with **notice of at most 30 days**. Early-termination penalties not justified by actual costs are null. — [AGCOM/Co.Re.Com deliberations](https://www.agcom.it/sites/default/files/migration/deliberazione/Deliberazione%20Co.re.com.%2018-03-2016%201479380292989.pdf)
- Loi Chatel: in force January 2008, it requires insurers to notify customers of the approaching anniversary/termination deadline. It applies to tacitly renewed contracts (telecom, gym, insurance). Loi Hamon (consumer law, in force 2015): termination of auto, moto and home insurance **after 1 year without penalty**. — [AG2R La Mondiale explainer](https://www.ag2rlamondiale.fr/particulier/auto-habitation/conseil-loi-chatel-et-loi-hamon-comment-resilier-une-assurance)
- Insurer summary table of termination cases. — [Altima résiliation table (PDF)](https://www.altima-assurances.fr/sites/default/files/reglementaire/Tableau_synthese_cas_de_resiliations.pdf)

### Inferences
- Design:
  1. Extract text from the PDF (pdftotext, or OCR for scans).
  2. Have Claude (Sonnet 5/Opus via Claude Code skill) fill a fixed JSON schema: provider, contract type, start date, minimum term, renewal type, notice period, termination channel, fees, and relevant clauses quoted verbatim with page numbers.
  3. A deterministic rule engine applies jurisdiction rules (IT telecom → Bersani; FR insurance >1yr → Hamon; etc.) from a versioned `legal_rules.md` / YAML, rather than relying on model memory.
  4. Output is a dated reminder and a draft letter, with "not legal advice" framing.
- Laws change: e.g. later French reforms on online termination and health insurance (mutuelle) termination, and Italian energy market liberalisation. These were not verified here, so the rules file needs citations and a "last checked" date.

### Gaps
- I did not verify later French measures (e.g. the "résiliation en trois clics" online termination requirement, or mutuelle rules) or Italian equivalents for energy/insurance (e.g. RC auto annual non-renewal rules) from primary legal sources (Légifrance, Normattiva).
- No benchmark of LLM accuracy on contract clause extraction in Italian/French was collected.

## Q8. Memory design for user-provided non-bank data (mortgage, car LOA, loans)

### Takeaway
No authoritative source was found. Based on reasoning: store **facts that drive calculations** (principal, rate, term, amortization schedule, LOA residual value and end date, insurance renewal dates) in a **structured store** (SQLite tables with a schema). Keep **narrative context and preferences** (goals, "I plan to change car in 2027", risk tolerance) in **markdown memory files** that Claude Code reads via CLAUDE.md/skills. Expose both to the analysis skills through MCP/SQL tools.

### Cited Findings
- No direct sources were found in this pass. The Claude Code legal page confirms that skills and Agent SDK use count as "ordinary, individual usage" of the plan, which is relevant only to where analysis runs. — [Claude Code Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)

### Inferences
- Structured tables:
  - `liabilities` (type, lender, principal, rate fixed/variable, start, term, installment, linked bank-transaction matcher)
  - `amortization_schedule` (generated deterministically, not by the LLM)
  - `contracts` (provider, category, renewal date, notice, legal regime, source PDF path)
  - `assets` (car, with LOA residual/option price)
  - `goals`
- Link each liability to its recurring transaction (via the recurring detector). Reconciliation then flags mismatches, such as a variable-rate change or missed payment.
- Markdown memory:
  - `memory/profile.md` (household, goals, preferences)
  - `memory/decisions.md` (past advice and outcomes)

  Markdown is easy for the user to edit and is naturally loaded by Claude Code. Numbers should not live only in markdown, because LLMs make arithmetic and transcription errors.
- Calculations (amortization, early repayment savings, LOA vs buy) belong in deterministic Python tools called by the skill. The LLM interprets and explains the results.

### Gaps
- No published best practice or benchmark on structured vs markdown memory for personal-finance agents was found.
- Claude Code's own memory features (CLAUDE.md hierarchy, auto-memory) were not re-verified in this pass.
