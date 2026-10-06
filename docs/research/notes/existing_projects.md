# Existing Open-Source Projects Overlapping an Self-Hosted "AI Personal Financial Coach" (as of Oct 2026)

Planned scope used for comparison: (A) cron sync from Enable Banking, no LLM; (B) cheap/local LLM classifies into a label taxonomy; (C) local storage + simple web UI; (D) on-demand LLM analysis (spending, subscriptions, contract review, cheaper alternatives); (E) "memory" of off-bank liabilities (mortgage, LOA/car lease, loans).

## 1. Self-hosted PFM apps: status and native EU/PSD2 bank sync

### Takeaway
Two mature self-hosted apps now support Enable Banking: Actual Budget (built in) and Firefly III (through its Data Importer). Sure, the community fork of the abandoned Maybe Finance, has beta Enable Banking support plus a built-in AI assistant. GoCardless Bank Account Data is effectively closed to new users, which makes Enable Banking the default free EU path. Any of these covers about A+B+C of the planned scope. None covers D or E.

### Cited Findings
**Actual Budget** (local-first, envelope budgeting)
- Official bank-sync providers: Akahu (NZ), Enable Banking (EU, active), GoCardless BankAccountData (EU, "not accepting new accounts"), SimpleFIN (N. America) and Pluggy.ai (Brazil) — [Actual docs: Connecting Your Bank](https://actualbudget.org/docs/advanced/bank-sync/)
- Enable Banking setup in Actual: More → Bank Sync → "Set up Enable Banking", paste the App ID and upload the credential file, then use Link account → Enable Banking → country/bank — [Actual docs: Enable Banking](https://actualbudget.org/docs/advanced/bank-sync/enable-banking); integration PR — [actualbudget/actual#7345](https://github.com/actualbudget/actual/pull/7345)
- Caveats: bank-sync tokens are stored on the server and are **not** end-to-end encrypted. Sync needs actual-server and is started manually, not automatically — [Actual docs](https://actualbudget.org/docs/advanced/bank-sync/)
- A separate community bridge, "enable-actual", imports Enable Banking transactions into Actual and keeps them in sync automatically. It has read-only access, and Enable Banking is free for personal use — [2manyvcos/enable-actual](https://github.com/2manyvcos/enable-actual)

**Firefly III** (double-entry PFM, PHP)
- The Data Importer supports third-party providers GoCardless (Nordigen), **Enable Banking**, Spectre and SimpleFIN, plus CSV and CAMT.053 — [Firefly III docs: third-party providers](https://docs.firefly-iii.org/how-to/data-importer/import/third-party-providers/)
- Enable Banking's free "restricted mode" gives access to your own pre-authorized accounts with no paid subscription (2,500+ banks, 29 countries). GoCardless is "shifting away" from Bank Account Data, and those instructions remain only for existing users — [Firefly III docs](https://docs.firefly-iii.org/how-to/data-importer/import/third-party-providers/); [GoCardless tutorial](https://docs.firefly-iii.org/tutorials/data-importer/gocardless/)

**Maybe Finance → Sure**
- Maybe Finance stopped open-source support with v0.6.0 as its final release ("no further updates, maintenance, or community support") and pivoted to B2B data and scenario planning. The repo is still accessible but unmaintained — [PieFed thread](https://piefed.jeena.net/post/210491); archive repo — [maybe-finance/maybe-archive](https://github.com/maybe-finance/maybe-archive)
- **Sure** (we-promise/sure) is the community fork: about 10.4k stars, 591 forks, 405 open issues and 3,602 commits on main, under AGPLv3. It runs on browser, macOS desktop, mobile, API clients "and LLM agents", with "Simple AI"/"External AI" deploy variants — [GitHub we-promise/sure](https://github.com/we-promise/sure)
- Sure v0.6.5 refactored its providers, added **beta Enable Banking support**, stabilized SimpleFIN and made Lunch Flow a first-class provider — [Sure discussion #388](https://github.com/we-promise/sure/discussions/388)
- Sure's README lists an AI assistant, LLM categorization, Plaid/GoCardless/Enable Banking/SimpleFIN sync and recurring-transaction detection. The stack is Rails, Postgres, Redis, optional OpenAI and pgvector — [Sure README (jsDelivr mirror)](https://cdn.jsdelivr.net/gh/we-promise/sure@main/README.md). Note: this list came from an LLM summary of the README, and a second fetch of the GitHub page could not confirm the provider list. Treat the details as needing verification, except Enable Banking, which discussion #388 confirms.
- After Maybe's shutdown, community members named Actual Budget, Firefly III and Ghostfolio as replacements — [PieFed thread](https://piefed.jeena.net/post/210491)

### Inferences
- For an EU user, Actual and Firefly III both already solve A (Enable Banking sync) and C (local data + UI). Sure solves A+B+C and part of D (chat assistant, recurring detection) in a single app, but its EU sync is still beta and it is a heavy Rails/Postgres/Redis stack.
- Actual's own Enable Banking sync is manual-trigger. Cron-style automatic sync needs enable-actual or the actual-ai cron, or an external script using @actual-app/api. This matches the planned "cron, no LLM" design.

### Gaps
- I did not verify current status, stars or bank-sync support for Ghostfolio, Beancount+Fava, hledger, Paisa, ezBookkeeping, Wealthfolio, Spliit, Budibase or Lunch Money (commercial) in this session. From general knowledge, most of these have no native PSD2 sync (Ghostfolio and Wealthfolio are investment trackers, Spliit is expense splitting, and Beancount/hledger/Paisa are plain-text ledgers fed by importers), but no source was fetched to confirm this.
- No star counts or last-commit dates were retrieved for Actual or Firefly III.

## 2. AI/LLM transaction categorization projects

### Takeaway
LLM categorization is a solved and commoditized problem for Actual (actual-ai, MIT, about 528 stars, works with Ollama and Anthropic) and Firefly III (several categorizers). Part B of the plan can be borrowed rather than built.

### Cited Findings
- **actual-ai** (sakowicz): about 528 stars, 58 forks, MIT, 249 commits. It categorizes uncategorized Actual transactions with OpenAI, Anthropic, Google, **Ollama**, Groq or OpenRouter. Other features: cron schedule, sync before classifying, a "guessed" tag on each result for review, suggesting and optionally auto-creating new categories, web search for unknown merchants (ValueSerp or DuckDuckGo), Handlebars prompt templates and a dry-run default — [github.com/sakowicz/actual-ai](https://github.com/sakowicz/actual-ai)
- **actual-categorizer**, an alternative Actual categorizer — [annis-souames/actual-categorizer](https://github.com/annis-souames/actual-categorizer) (no details fetched)
- Firefly III categorizers listed in the official third-party apps page:
  - **firefly-iii-ai-categorize** (bahuma20), OpenAI-based — [Firefly III docs: third-party apps](https://docs.firefly-iii.org/references/firefly-iii/third-parties/apps/); repo [bahuma20/firefly-iii-ai-categorize](https://github.com/bahuma20/firefly-iii-ai-categorize)
  - **FFIIITC** (akopulko), Naive Bayes, no LLM — [akopulko/ffiiitc](https://github.com/akopulko/ffiiitc)
  - **Toolbox for Firefly III** (xenolphthalein): AI category/tag suggestions, duplicate detection, **subscription pattern detection** and transaction matching — [xenolphthalein/toolbox-for-firefly-iii](https://github.com/xenolphthalein/toolbox-for-firefly-iii); summary via [WebSearch result / Firefly docs](https://docs.firefly-iii.org/references/firefly-iii/third-parties/apps/)
- Beancount ecosystem: **smart_importer** uses scikit-learn's SGD classifier with bag-of-words on payee/narration. **Beanborg** is rules-first with ML/ChatGPT suggestions. A suggested hybrid is to let the LLM label the long tail and then let smart_importer learn from the corrected labels — [beancount.io: Using LLMs with Beancount](https://beancount.io/docs/Solutions/using-llms-to-automate-and-enhance-bookkeeping-with-beancount)
- Sure has built-in LLM categorization (OpenAI-compatible) — [Sure README](https://cdn.jsdelivr.net/gh/we-promise/sure@main/README.md)

### Inferences
- The planned "rules/merchant cache first, cheap or local LLM for the long tail, flag guesses for review" pattern already exists in actual-ai. That makes it a good reference design, and possibly a drop-in, if Actual is chosen as the store.

### Gaps
- Activity dates (last commit) for actual-ai and the Firefly categorizers were not retrieved. I found no reliable accuracy benchmarks comparing local and cloud models.

## 3. LLM finance agents / MCP servers / Claude Code skills

### Takeaway
MCP connectors exist for nearly every PFM backend (Actual, Firefly III, YNAB, Lunch Money, Plaid, ZenMoney, Copilot Money). For EU banks specifically, there is **BankMCP**, an Enable Banking read-only MCP server (MIT), which is the very server attached to this user's environment. Generic "personal finance coach" Claude skills exist, but they work on CSV/PDF input and keep no persistent memory or bank sync.

### Cited Findings
- **BankMCP** (noskillish/bankmcp, bankmcp.dk): open-source MCP server for 2,700+ European banks via Enable Banking. It is read-only, single-user, has no payment tools and is MIT-licensed. Config is kept in `/data/bank.json`. Transactions are **not stored** and are fetched on demand. It needs a remote-OAuth MCP client (Claude Desktop, ChatGPT) — [Railway: Deploy BankMCP](https://railway.com/deploy/bankmcp); [glama listing](https://glama.ai/mcp/servers/ya2n1gsgss)
- Firefly III MCP servers: **fireflyiii-mcp** (daften; TypeScript, 140 tools in 14 groups, OAuth 2.0), **mcp-firefly-iii** (YakupEmreYerli; write previews) and **Universal Firefly III AI Bridge** (fabianonetto; full API coverage) — [Firefly III docs: third-party apps](https://docs.firefly-iii.org/references/firefly-iii/third-parties/apps/)
- Actual Budget MCP servers exist (query/manage budgets, accounts, transactions, categories, spending analysis) — [Glama: Actual Budget MCP servers](https://glama.ai/mcp/servers/integrations/actual-budget)
- YNAB has 10+ MCP implementations. Jtewen/ynab-mcp (about 10 stars, 13 tools) is described as the most featured — [ChatForest via search snippet](https://chatforest.com/reviews/personal-finance-mcp-servers/) (the page returned 404 on fetch, so this figure comes from the snippet only)
- Lunch Money MCP servers: akutishevsky/lunchmoney-mcp (v2 API) and gilbitron/lunch-money-mcp — [Claude Code Marketplaces](https://claudemarketplaces.com/mcp/akutishevsky/lunchmoney-mcp); [Glama](https://glama.ai/mcp/servers/@gilbitron/lunch-money-mcp/blob/339ef6318bd809b8fd952611822c6460686ce2fb/README.md)
- Other finance MCPs and skills listed on Claude Code marketplaces: a local-first finance MCP over Plaid (balances, holdings, transactions, liabilities), ZenMoney MCP (8 tools including auto-suggesting categories), OpenFinance MCP, Copilot Money and Organizze skills, a "personal-finance-coach" skill (portfolio math, tax-loss harvesting) and a "finance-manager" skill (CSV/PDF bank statement → analysis + charts) — [claudemarketplaces.com search results](https://claudemarketplaces.com/skills/erichowens/some_claude_skills/personal-finance-coach); [finance-manager](https://claudemarketplaces.com/skills/ailabs-393/ai-labs-claude-skills/finance-manager); [finance-mcp](https://claudemarketplaces.com/mcp/adelaidasofia/finance-mcp)
- A "transaction-categorizer" Claude skill exists (lyndonkl/claude) — [claudeskills.info](https://claudeskills.info/zh/skills/lyndonkl/claude/transaction-categorizer/)
- Sure advertises access by "LLM agents" and an AI assistant — [GitHub we-promise/sure](https://github.com/we-promise/sure)

### Inferences
- The planned pipeline (A) could reuse the Enable Banking auth flow from Actual, Firefly or BankMCP. But BankMCP is on-demand only and stores nothing, so it does not replace a local transaction store, the cron sync or the classification pipeline. It is useful as a live, read-only complement.
- A credible "extend" path: Actual (or Firefly) as store + UI, actual-ai for categorization, an Actual or Firefly MCP for Claude Code, and new Claude Code skills for coaching, contracts and the liabilities memory.

### Gaps
- I found no GoCardless-specific MCP server, and no Monarch or Plaid-official MCP details were fetched. Star counts and activity for most MCP repos were not retrieved.
- I found no open-source project that packages Claude Code *skills + agents* as a full personal-finance coach on top of a synced local ledger.

## 4. Subscription detection / cancellation tools

### Takeaway
Open-source subscription tools are mostly **manual trackers** (Wallos, SubTrackr, SubOS). Automatic detection from bank data exists only inside PFM add-ons: Toolbox for Firefly III and Sure's recurring detection. No OSS tool does cancellation or negotiation the way Rocket Money does.

### Cited Findings
- **Wallos**: self-hosted tracker for recurring subscriptions and payments, with due-date reminders. Entries are manual — [github.com/ellite/Wallos](https://github.com/ellite/Wallos); [GIGAZINE review, May 2026](https://www.gigazine.net/gsc_news/en/20260504-wallos)
- **SubTrackr**: self-hosted subscription tracker with monthly/annual stats, an iCal renewal calendar, multi-currency support and an API — [selfhostyourself.com](https://selfhostyourself.com/services/subtrackr)
- **SubOS**: self-hosted subscription manager with "AI-powered insights", OCR receipt processing and multi-currency support — [github.com/aref-vc/SubOS](https://github.com/aref-vc/SubOS)
- **Toolbox for Firefly III**: subscription pattern detection from Firefly transactions — [Firefly docs](https://docs.firefly-iii.org/references/firefly-iii/third-parties/apps/)
- Sure: subscription and recurring-transaction detection (from README summary, needs verification) — [Sure README](https://cdn.jsdelivr.net/gh/we-promise/sure@main/README.md)

### Inferences
- Detecting recurring charges from synced transactions is cheap to implement deterministically (merchant + amount + periodicity) and does not need an LLM. The LLM adds value in explaining charges and proposing cancellations or cheaper alternatives.

### Gaps
- Commercial references (Rocket Money, Emma, Bankin', Linxo, Finary, Tink-based apps in Italy/France) were not researched in this session. Feature and pricing claims for them are unsourced here.

## 5. Gaps: what no existing project covers

### Takeaway
Bank sync, LLM categorization, a local UI and chat-over-transactions are all covered by existing OSS. I found **no** project that combines: (1) a persistent user "memory" of off-bank liabilities (mortgage, LOA/car lease, loans) fed into analysis; (2) contract/document review (insurance, energy, telecom, loan terms); (3) web search for cheaper alternatives tied to detected subscriptions and contracts; (4) proactive, scheduled coaching reports for EU users.

### Cited Findings
- Existing AI features stop at categorization, merchant web lookup, new-category suggestion ([actual-ai](https://github.com/sakowicz/actual-ai)), subscription pattern detection ([Toolbox for Firefly III](https://docs.firefly-iii.org/references/firefly-iii/third-parties/apps/)) and a chat assistant over the ledger ([Sure](https://cdn.jsdelivr.net/gh/we-promise/sure@main/README.md)).
- Available Claude skills either analyse one-off CSV/PDF statements ([finance-manager](https://claudemarketplaces.com/skills/ailabs-393/ai-labs-claude-skills/finance-manager)) or focus on portfolio and tax math ([personal-finance-coach](https://claudemarketplaces.com/skills/erichowens/some_claude_skills/personal-finance-coach)). Neither is tied to a synced EU ledger or a liabilities memory.
- BankMCP gives live read-only EU bank access but stores no history and has no analysis layer — [Railway: BankMCP](https://railway.com/deploy/bankmcp)

### Inferences
- **Build vs. extend vs. fork:**
  - *Extend (lowest effort):* use Actual Budget with Enable Banking, run actual-ai with Ollama for categorization, and add an Actual MCP. The differentiators (memory, contract review, alternatives search, coaching) would be built as Claude Code skills reading Actual data plus a small memory store (e.g. a markdown/YAML file of loans and contracts). This reuses about 60–70% of the plan, with the limit that Actual's category model is budget-envelope oriented rather than a free label taxonomy.
  - *Fork Sure:* the most feature-complete overlap (AI chat, LLM categorization, recurring detection, beta Enable Banking, loans and property accounts in the Maybe lineage). The cost is a heavy Rails stack, AGPLv3 and beta EU sync.
  - *Build:* justified if the user wants a minimal stack (SQLite + small web UI + Python cron) and full control over the taxonomy and memory schema. Actual's and Firefly's Enable Banking code, and actual-ai's prompt design, can serve as references.
- The novel value of the project lies entirely in D and E (coaching, contracts, alternatives, liabilities memory). Bank sync and categorization are commodity.

### Gaps
- Whether Sure's account model (inherited from Maybe: loans, property, vehicles) already acts as a "liabilities memory" usable by its AI assistant was not verified in this session.
- I found no sources on OSS tools for automated contract review in personal finance, or for EU price-comparison APIs (energy, telecom, insurance) that a "cheaper alternatives" agent could call.
