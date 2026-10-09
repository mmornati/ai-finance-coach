# FAQ

Straight answers to the questions people ask before they trust a program with their bank data.

## Privacy and safety

??? question "Is my data sent anywhere?"
    Only where you allow it, and never in full. The bank data comes *in* from Enable Banking; nothing from your database goes back. A model
    backend receives redacted merchant descriptors (for categorization) and redacted, already computed tool results (for the coach):
    pseudonyms such as <span class="pseudo">adult-1</span>, hashed transaction refs, no IBAN, no real name. Alert channels are off until you
    enable one, and their messages are minimal. There is no telemetry, CDN or analytics. `coach privacy status` lists every outbound path
    and `coach privacy report` shows a local journal of every call (host, size, purpose, never a payload).
    See [Privacy modes](configuration/privacy.md) and [Privacy](privacy.md).

??? question "Can I run it fully offline?"
    Yes, in two steps. `[privacy] local_only = true` makes every model call go to Ollama on your own machine and turns off web search and
    external alert channels; the bank sync is then the only network use. `[privacy] offline = true` goes further: nothing leaves the
    machine, so there is no bank sync either and you bring transactions in with file imports. See [Privacy modes](configuration/privacy.md).

??? question "Does it work without a model?"
    Yes. Syncing, imports, rules, your corrections, budgets, forecasts, subscriptions, loans and every page of the web app are plain code.
    Without a model, only the merchants your rules and memory do not recognise stay uncategorized (you can review them by hand), and the
    "Ask the coach" page is unavailable. See [AI models](configuration/models.md).

??? question "Why can't the coach change my data?"
    Because a model can be wrong, and because merchant names and transfer descriptions are written by strangers and can hide an
    instruction ("ignore your rules and..."). So the coach only reads computed results and can only *propose* a memory change. A proposal
    is sealed; you review it under **Memory > Proposals** and accept it yourself, in a terminal (`coach memory accept <id>`). Text in your
    data that looks like an instruction is treated as data and flagged. See [Memory & set-up](guide/memory.md).

??? question "Where are my data and keys stored?"
    In your home folder (`~/.ai-finance-coach` by default, or `coach-home/` with Docker). The database is encrypted with SQLCipher. The
    keys live in the macOS Keychain, or in files readable by you only (mode 0600) in Docker. The web app listens on `127.0.0.1` only,
    behind a one-time login link. See [Install](getting-started/install.md).

??? question "Is it safe to let Claude Code work in this folder?"
    Treat any coding agent as untrusted code running as you. The project ships a template of permission rules
    (`docs/claude-settings.example.json`), finance tools that only return redacted data, and confirmations that need a human at a
    terminal. Rules mitigate; they do not prevent. See [Claude Code & skills](claude-code/index.md) and [Security](security.md).

## Banks and countries

??? question "Which banks does it support?"
    Any bank Enable Banking reaches in your country, through PSD2: you link them with your own free Enable Banking application in
    restricted mode. The coach also ships description parsers tuned for Fortuneo, CIC, Caisse d'Epargne and Revolut, a generic Italian
    one and a generic fallback. Banks you cannot link can be imported from CSV, OFX or CAMT.053 files.
    See [Connect your banks](getting-started/banks.md).

??? question "Is it France and Italy only?"
    It is built for one household in France or Italy: the category taxonomy, the bank parsers, the subscription cancellation rules and
    the tax candidates are French and Italian. The web app speaks English, French and Italian. Another country works for syncing and
    budgets but needs parsers and rules to be as useful.

??? question "What happens when a bank consent expires?"
    A PSD2 consent lasts at most 180 days. From 14 days before the end, the Connections page turns amber, you get a consent alert and
    the daily job warns at every run. Click **Reconnect** (or run `coach reconnect "<bank>"`), log in at your bank again, and you are done:
    every account keeps its history, label and owner. See [Connect your banks](getting-started/banks.md#consents-expire).

??? question "Why does it sync at most four times a day?"
    PSD2 limits how often an app may read an account without you being present. `[sync] daily_limit = 4` keeps the coach under that
    cap, per account. See [Connect your banks](getting-started/banks.md#sync-limits).

## Cost

??? question "What does it cost?"
    The coach is free and open source (MIT). Enable Banking's restricted mode is free for your own accounts. The only cost is the model
    you choose: nothing with Ollama, your existing Claude subscription with Claude Code, or pay-per-use with the Anthropic API or an
    OpenAI-compatible provider. The **AI usage** page shows every call with its tokens and an estimated cost, and
    `[usage] monthly_warn_usd` can raise an alert. See [Quality & AI usage](guide/quality.md).

??? question "Which model should I pick?"
    Any backend works for categorization; the coach needs a model with tool calling. Claude Code suits personal, low-frequency use; an
    API key suits automation; Ollama keeps everything local. See [AI models](configuration/models.md).

## The household

??? question "Can my partner and kids log in?"
    Yes. Each person can have their own login of the web app: an adult sees everything, a child sees only their own money (pocket money,
    spending, their budget). The owner creates logins in a terminal with `coach users add`, and hands over a one-time link with
    `coach ui --login-link --user ID`. See [Household & kids](guide/household.md).

??? question "Can it handle several households or a small business?"
    No. It is built for one household: its members, accounts, loans, contracts and possibly a rental property.

## Accuracy and advice

??? question "How accurate is categorization?"
    It depends on your banks and on how much you have taught it. Most transactions are settled by the bank parsers, your memory, rules and
    a local nearest-neighbour step; a model labels only the long tail. You can measure it: label a sample on the **Gold set** page and
    `coach eval classify` scores the categorization against your labels, by transaction and by money, offline. Every correction you make
    on **Review** becomes permanent memory. See [Categories & review](guide/categories.md) and [Quality](quality.md).

??? question "Can I trust the numbers in the coach's answers?"
    The figures come from the tools, not from the model: the coach quotes amounts that code computed and cites the payments behind them.
    A number in an answer that cannot be traced to a computed figure is flagged on screen. The wording can still be wrong, so every
    answer carries an "AI-generated" label. See [Ask the coach](guide/coach.md).

??? question "Is this financial advice?"
    No. The coach explains your own spending and saving habits. It does not recommend funds, shares, crypto, loans or insurers, and gives
    no tax or legal advice. Estimates (a renegotiation, a cancellation date, tax candidates) are labelled as estimates; check them with a
    regulated professional.

## Running it

??? question "Can I try it before connecting my bank?"
    Yes: the repository ships an invented household (the Rossi family) with a scripted coach that makes real tool calls on invented data.
    No bank, model or key needed. See [Try the demo](getting-started/demo.md).

??? question "Can I run it on a server and use it from my phone?"
    Yes, in Docker on a home server, with the app on the server's loopback and Tailscale's HTTPS in front. Never expose it to the
    internet. See [Run it on a server](getting-started/server.md).

??? question "How do I back it up?"
    `coach backup` writes an encrypted archive of the database and the memory and keeps the newest ones. Keep `db_key` and `backup_key`
    in your password manager, separately. See [Daily job & backups](configuration/schedule.md).

??? question "How do I delete everything?"
    `coach wipe --dry-run` lists what would be deleted. `coach wipe` itself only runs in a terminal, writes a safety export first and
    asks you to type a confirmation phrase. It cannot be undone. See [Install](getting-started/install.md#removing-it).
