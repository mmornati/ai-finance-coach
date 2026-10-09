# Configuration

One commented TOML file, `config.toml`, holds every setting of the coach; secrets never go in it.

<figure class="shot" markdown>
![The Set up page of the demo household](../assets/screens/setup-light.webp#only-light){ loading=lazy }
![The Set up page of the demo household](../assets/screens/setup-dark.webp#only-dark){ loading=lazy }
<figcaption>The Set up page shows the first-run steps and what the coach still needs. The settings themselves live in <code>config.toml</code>.</figcaption>
</figure>

## How it works

`coach init` writes `config.toml` for you from the commented template `config.example.toml`. You can also copy the template by hand
and edit it. Every key is optional: a key you leave out takes its default.

**Where the file is found**, in this order:

1. `--config PATH` on the command line, or the `COACH_CONFIG` environment variable (the file's folder becomes the project root);
2. `COACH_HOME`: the folder that holds `config.toml`;
3. the current folder or its nearest parent that holds a `config.toml` (or `config.example.toml`, as in a checkout);
4. for an installed package (`uvx`, `pipx`): `~/.ai-finance-coach`.

In Docker the file is `coach-home/config/config.toml` on the host (`/config/config.toml` in the container).

**Precedence**: environment variable > `config.toml` > built-in default. Relative paths (`data_dir`, `memory_dir`, `[backup] dir` ...)
are resolved against the folder that holds `config.toml`.

```bash
uv run coach config show      # the effective configuration, where each value comes from, secrets masked
```

A wrong value (a typo in a key, an `http://` alert URL, an unknown alert kind) stops the configuration from loading with a clear message.

## Secrets never go in the file

Keys and passwords live in a **secret store**, never in `config.toml`. Why: the config file is plain text you edit, copy and share when you
ask for help; a key in it would travel with it.

| Store | When | How |
|---|---|---|
| macOS Keychain (default) | on a Mac | service `ai-finance-coach`, one item per secret |
| a folder of files | Docker, a Linux server | `COACH_SECRETS_BACKEND=file` and `COACH_SECRETS_DIR` (default `/run/secrets`): one file per secret, named like it, holding only the value, mode `0600` |

```bash
uv run coach config set-secret db_key --generate      # create a random key in the active store
uv run coach config set-secret smtp_password          # type a value (it is never echoed or printed)
```

A secret is looked up as: environment variable, then the store, then a clear error. `coach config show` and `coach doctor` only say whether a
secret is set and where it comes from.

| Secret | Environment variable | Needed for |
|---|---|---|
| `db_key` | `COACH_DB_KEY` | the encrypted database (SQLCipher). Required |
| `backup_key` | `COACH_BACKUP_KEY` | encrypted backups and exports |
| `proposal_key` | `COACH_PROPOSAL_KEY` | sealing memory proposals against tampering (optional, recommended) |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | the `anthropic-api` backend |
| `openai_api_key` | `COACH_OPENAI_API_KEY` | the `openai-compatible` backend (deliberately not `OPENAI_API_KEY`) |
| `claude_code_oauth_token` | `CLAUDE_CODE_OAUTH_TOKEN` | the `claude-code` backend in a container |
| `ntfy_token`, `smtp_password`, `telegram_bot_token` | `COACH_NTFY_TOKEN`, `COACH_SMTP_PASSWORD`, `COACH_TELEGRAM_BOT_TOKEN` | alert channels |

!!! warning "Keep a copy of `db_key` and `backup_key`"
    Save both in your password manager. Losing `db_key` makes the database unrecoverable; losing `backup_key` makes the backups unreadable.

## Environment variables

| Variable | Effect |
|---|---|
| `COACH_HOME` | the project root (the folder of `config.toml`, data and memory) |
| `COACH_CONFIG` | the path of `config.toml` (same as `--config`) |
| `COACH_DB` | the database file (overrides `db_path`) |
| `COACH_DATA_DIR`, `COACH_MEMORY_DIR` | override `data_dir` and `memory_dir` |
| `COACH_CONFIG_DIR` | where your editable copies of `taxonomy.yaml` / `rules.yaml` live (default `config/` next to `config.toml`) |
| `COACH_SECRETS_BACKEND`, `COACH_SECRETS_DIR` | the secret store: `keychain` (default) or `file`, and the folder of the `file` store |
| `COACH_SECRETS_ALLOW_READABLE` | `1` accepts secret files readable (never writable) by others, for swarm or Kubernetes mounts |
| `EB_APP_ID`, `EB_REDIRECT_URL`, `EB_PRIVATE_KEY_PATH`, `EB_API_URL` | override the `[enable_banking]` values |
| the secret variables above | override the secret store |

!!! tip "Try things on a scratch copy"
    Point `COACH_DB`, `COACH_MEMORY_DIR`, `COACH_DATA_DIR` and `COACH_CONFIG_DIR` at a restored backup (`coach restore FILE --to DIR`) to test a
    setting without touching your real data.

## Section by section

The reference below follows `config.example.toml`. Topics with their own page: [AI models](models.md) (`[llm]`, `[coach]`),
[Privacy modes](privacy.md) (`[privacy]`), [Alerts & channels](alerts.md) (`[alerts]`), [Daily job & backups](schedule.md) (`[schedule]`,
`[backup]`, `[logs]`) and [Remote access](remote.md) (`[ui]`).

### Top level

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | `"data"` | database, logs, TLS certificate, web-app state |
| `memory_dir` | `"memory"` | the household memory (`categorization.yaml`, `profile.md` ...) |
| `db_path` | `<data_dir>/finance.db` | the database file |
| `insecure_plaintext_db` | `false` | `true` allows an unencrypted database (same as `--insecure`); leave it off |

### `[db]`, `[enable_banking]`, `[sync]`, `[callback]`

| Key | Default | Meaning |
|---|---|---|
| `db.auto_migrate` | `true` | any command applies pending migrations (with a notice); `false` = only `coach db migrate` does, others refuse |
| `enable_banking.app_id` | `""` | your application id from the Enable Banking control panel |
| `enable_banking.redirect_url` | `""` | must be whitelisted in your Enable Banking app (usually `https://localhost:8443/callback`) |
| `enable_banking.private_key_path` | `""` | the `.pem` you downloaded; keep it outside the project folder |
| `enable_banking.api_url` | `https://api.enablebanking.com` | commented out; rarely changed |
| `sync.daily_limit` | `4` | unattended syncs per account per day (PSD2 cap) |
| `callback.timeout_seconds` | `600` | how long `coach connect` / `reconnect` wait for the bank redirect |
| `callback.container_bind` | `false` | written `true` only by the Docker image's `coach init`; never set it on a host |

### `[llm]` and `[coach]`

The backend for categorization (`[llm]`) and for the coach (`[coach]`), their models and limits. Every key is explained on
[AI models](models.md).

### `[classify]`

| Key | Default | Meaning |
|---|---|---|
| `knn_enabled` | `true` | near-duplicates of merchants you already labelled are labelled without a model |
| `knn_threshold` | `0.92` | minimum similarity for such an automatic label |
| `knn_examples` | `5` | nearest labelled merchants sent with each item as examples |
| `llm_allowlist` | `[]` | regexes of transfer-like merchant keys (companies without a legal form in their name) that may be sent to a model; by default anything that could be a person is held back |

### `[transfers]`

| Key | Default | Meaning |
|---|---|---|
| `window_days` | `3` | the two legs of an internal transfer must be within this many days |
| `cross_bank_window_days` | `5` | the same when the legs are on different banks |
| `auto_link` | `false` | `true` = the daily job links confident pairs itself; `false` = it only proposes them |
| `topup_merchants` | `["REVOLUT", "LYDIA", "PAYPAL"]` | card payments to these may be top-ups of another own account (proposals only, never auto-linked) |
| `min_confidence` | `0.85` | pairs below this are never auto-linked |

### `[memory]`, `[health]`, `[import]`, `[notify]`

| Key | Default | Meaning |
|---|---|---|
| `memory.history` | `true` | keep a change history of `memory/` (needs `git`) |
| `memory.stale_months` | `6` | `coach memory check` reports loan / contract facts older than this |
| `memory.asset_stale_months` | `3` | ... and asset values older than this |
| `memory.big_tx_threshold` | `2000` | `coach questions generate`: one-off payments above this (EUR) not explained by memory |
| `memory.question_min_stake` | `300` | merchants with less money at stake (EUR) are not asked about |
| `health.stale_days` | `2` | `coach health`: no successful sync for longer = stale |
| `import.profiles_dir` | `"config/import_profiles"` | CSV mapping profiles for `coach import` |
| `notify.macos` | `false` | a macOS notification when a consent is about to expire (superseded by `[alerts.macos]`) |

### `[analytics]`

Thresholds of the deterministic analytics. Every key is optional and commented out in the template; the defaults suit most households.

??? info "All analytics keys and their defaults"
    | Key | Default | Meaning |
    |---|---|---|
    | `average_window_months` / `average_min_months` | `12` / `3` | months behind a category average; fewer = low confidence |
    | `recurring_amount_tolerance` | `0.15` | recurring amounts within ±15 % of the median |
    | `recurring_min_occurrences` | `3` | payments needed for a series (yearly: 2, low confidence) |
    | `recurring_ended_factor` | `1.5` | no payment for 1.5 × the cadence = ended |
    | `recurring_variable_categories` | energy, water, telecom | bills whose amount varies |
    | `recurring_yearly_pair_groups` | subscriptions, insurance, housing, taxes ... | groups where two payments a year apart can form a yearly series |
    | `price_change_threshold_pct` / `price_change_min_abs` | `3.0` / `0.50` | a price change is at least 3 % and 0.50 EUR |
    | `price_change_variable_pct` | `20.0` | variable bills: only 20 % moves count |
    | `anomaly_min_history_months` | `6` | earlier covered months needed for a category spike |
    | `anomaly_z`, `anomaly_min_excess`, `anomaly_min_ratio` | `3.5`, `50.0`, `1.5` | how unusual a category month must be |
    | `anomaly_duplicate_days` / `anomaly_duplicate_min_amount` | `3` / `10.0` | same merchant and amount within N days = duplicate |
    | `anomaly_new_merchant_days` / `anomaly_new_merchant_min` | `60` / `150.0` | a new merchant with a payment of at least this |
    | `anomaly_large_tx_min`, `anomaly_lookback_days`, `anomaly_months` | `100.0`, `90`, `3` | large payments; how far back to look |
    | `forecast_band_z` | `1.28` | forecast band (about 80 %) |
    | `forecast_variable_months`, `forecast_balance_stale_days` | `6`, `3` | variable-spend estimate; stale balance flag |
    | `budget_suggest_months`, `calendar_days`, `asset_stale_months` | `6`, `60`, `3` | budget suggestions, calendar window, asset reminder |
    | `loan_grace_days`, `loan_reminder_months` | `5`, `6` | missing loan payment; lease end reminders |
    | `rental_reminder_months`, `rental_rent_grace_days`, `rental_rate_gap_pts` | `12`, `7`, `0.5` | rental scheme end, missing rent, rate gap |

### `[privacy]`, `[alerts]`, `[schedule]`, `[backup]`, `[logs]`, `[ui]`

See [Privacy modes](privacy.md), [Alerts & channels](alerts.md), [Daily job & backups](schedule.md) and [Remote access](remote.md).

### `[usage]`

| Key | Default | Meaning |
|---|---|---|
| `monthly_warn_usd` | `0` | above `0`: a local `llm_usage_high` alert when a month's AI cost passes it (never sent to a channel) |
| `include_notional` | `true` | count the notional cost of subscription (`claude-code`) calls in that threshold |

## See also

- [CLI reference: Setup, Configuration, Secrets](../reference.md)
- [Docker](../docker.md) for the container layout of the config, data and secrets
- [Security](../security.md) for why secrets stay out of the file
