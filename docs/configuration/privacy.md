# Privacy modes

The `[privacy]` section decides what a model may see and whether anything may leave your machine at all, and the egress journal shows what did.

<figure class="shot" markdown>
![The AI usage page with the hosts the calls went to](../assets/screens/usage-light.webp#only-light){ loading=lazy }
![The AI usage page with the hosts the calls went to](../assets/screens/usage-dark.webp#only-dark){ loading=lazy }
<figcaption>"Where the calls went" on the AI usage page comes from the local egress journal: the host and the size, never what was sent.</figcaption>
</figure>

## The settings

```toml
[privacy]
model_detail = "coarse"       # what a model is told: coarse (default) | standard
local_only = false            # true = every model is Ollama on this machine; no web search; no external alert channel
offline = false               # true = nothing leaves this machine (implies local_only); no bank sync
web_enrich = false            # opt-in: `classify enrich` searches the web with redacted shop names
egress_journal = true         # log every outbound call (host, size, purpose; never a payload)
egress_journal_days = 365     # the daily job deletes older journal rows (30 to 3650)
```

These are **your** decisions: the app, the web pages and the coach never change them.

## What a model sees: `model_detail`

Whatever the mode, names, accounts and keys are always pseudonymised: accounts become <span class="pseudo">account-main-1</span>,
people <span class="pseudo">adult-1</span> or <span class="pseudo">kid-1</span>, transactions hashed refs like <span class="pseudo">h_0123456789</span>.

| | `coarse` (default) | `standard` |
|---|---|---|
| Salary payer | `[employer]` | `[employer]` (declared and derived employers are always masked) |
| School payees | `[school]` | declared schools scrubbed |
| Small or person-like merchants | `[merchant:<category>]-<hash>` | merchant titles kept, except what the person guard flags |
| Towns in merchant titles | cut | declared and derived towns masked (`[place]`) |
| Banks, lenders, insurers in memory | by what they are (`regional bank`, `car-lease company`) | real names |
| Free text of memory (profile, preferences, notes) | withheld | shown, with names scrubbed |

Choose `standard` if you want the coach to follow the tone and language rules of your `preferences.md`.

??? info "Redaction examples (demo household)"
    | In your data | What a model sees |
    |---|---|
    | the joint account at Banque Aurore | <span class="pseudo">account-main-1</span> |
    | Anna, Mia | <span class="pseudo">adult-1</span>, <span class="pseudo">kid-1</span> |
    | a card payment at Fresh Market | the payment's date, signed amount, category and <span class="pseudo">h_0123456789</span> |
    | a small local shop named after its owner | `[merchant:food]-<hash>` (coarse) |
    | Luca's salary | income from `[employer]` |
    | a merchant name that reads like an instruction | `{"untrusted_text": "..."}`, flagged as suspicious |

    A final check on every tool output refuses the whole result if a member name, an account label, an IBAN, an e-mail or a path slips
    through. It fails closed.

## Fully local: `local_only`

```toml
[privacy]
local_only = true
[llm]
backend = "ollama"
[coach]
backend = "ollama"        # a model with tool calling
```

Under `local_only`:

- every model is Ollama **on this machine** (loopback); a cloud backend is refused, even a remote Ollama with `ollama_allow_remote`;
- `classify enrich` and the web-search skills (`find-cheaper`, `mortgage-check`) refuse;
- ntfy, e-mail and Telegram alerts are "not ready"; the macOS notification stays (nothing leaves);
- `coach mcp serve` refuses, because its client would be a cloud model;
- Enable Banking stays allowed: it is your data source.

**`offline = true`** goes further: the bank sync is refused too, and you import bank files with `coach import`. Expect lower quality from
small local models.

## Web enrichment: `web_enrich`

`coach classify enrich` is the one path where a model searches the web with something derived from your transactions. It is off unless you
set `web_enrich = true`, it only searches **shops** (never anything that could be a person), strips towns and redacts every descriptor.

```bash
uv run coach classify enrich --dry-run    # the exact request; works even when enrichment is off; sends nothing
```

## The egress journal

Every outbound call goes through one gate that applies the settings above and writes one row to a local journal: time, kind, destination
host, size, purpose, redaction mode, allowed or denied. **Never** a payload, a URL path, a name, an amount or a token.

```bash
uv run coach privacy status         # the effective mode and what each outbound path may do (--json for scripts)
uv run coach privacy report         # every outbound path + the last 30 days of the journal
```

| Outbound path | Where to | What is sent |
|---|---|---|
| Enable Banking | `api.enablebanking.com` | ids and date ranges; bank data comes back, nothing of yours goes out |
| a model backend | Anthropic, your provider, or Ollama | redacted merchant descriptors, or the coach's question and redacted tool results |
| the finance MCP server | the model behind Claude Code | redacted, pseudonymised tool results |
| alert channels | your ntfy, SMTP or Telegram | a minimal message (see [Alerts & channels](alerts.md)) |
| web search skills | the search engine of Claude Code | generic, non-personal queries |

## What is stored where

| Data | Where | How long |
|---|---|---|
| transactions, accounts, labels, insights, alerts, net worth | the encrypted database (SQLCipher) | until you delete them |
| household memory, its history and proposals | `memory/`, owner-only files | until you delete them |
| AI usage (model, tokens, cost; no content) | database | kept |
| egress journal | database | `egress_journal_days` (365) |
| encrypted backups | `backups/` | the newest `[backup] retention` (14) |
| logs | `data/logs/` | rotated by `[logs]` |
| secrets | Keychain or the secrets folder | until you delete them |

What a provider keeps of what it receives is governed by its own terms.

## Export and delete

`coach export` writes an encrypted archive of all your data (readable with `backup_key`). `coach wipe` deletes the database, memory,
data folder and, if you say so, the backups and the secrets.

!!! warning "Both are for you, at a terminal"
    `coach wipe --dry-run` lists what would be deleted and touches nothing. The real `coach wipe` asks for a typed phrase on a terminal,
    has no `--yes`, and first writes an encrypted export. Exporting in clear also needs a terminal and a typed phrase. Never let an agent run
    either; the permission template denies them.

## See also

- [Privacy reference](../privacy.md): the full inventory, retention and export format
- [Security](../security.md): the threat model and residual risks
- [The coach (LLM): privacy model](../coach.md)
