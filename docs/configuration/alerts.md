# Alerts & channels

Alerts always appear in the app; you choose whether ntfy, e-mail, Telegram or a macOS notification also gets a short, privacy-safe message.

<figure class="shot" markdown>
![The Alerts page of the demo household](../assets/screens/alerts-light.webp#only-light){ loading=lazy }
![The Alerts page of the demo household](../assets/screens/alerts-dark.webp#only-dark){ loading=lazy }
<figcaption>The Alerts page: a notice deadline at HomeSure, a spending spike, a consent at Nova Bank about to expire. Each one can be acknowledged, snoozed or muted.</figcaption>
</figure>

## How it works

The daily job (and `coach alerts check`) turns signals the app already computes into **alerts**: a consent about to expire, a sync that
keeps failing, an unusual charge, a price rise, a balance that may run short, a budget over, a loan payment missed, a notice deadline, a
service you cancelled that is still charged. Numbers come from code, never from a model.

- The **in-app feed** is always on: the Alerts page, the dashboard card and the badge in the menu.
- **Channels** are all off until you enable them in `config.toml`. The app, the API and the coach never turn one on.
- One situation is one alert. It is sent to a channel once, and again only when its severity goes up.

## Noise control

```toml
[alerts]
enabled = true                       # evaluate signals and keep the events (the in-app feed)
min_severity = "low"                 # low | medium | high: below it, kept but never sent outside the app
external_min_severity = "medium"     # nothing below it is sent by any channel
external_detail = "minimal"          # minimal | summary (see below)
quiet_hours = "22:00-08:00"          # local time; held alerts go out after it ("" = off)
max_per_week = 7                     # messages per channel in a rolling 7 days; 0 = nothing outside the app
disabled_kinds = []                  # kinds not evaluated at all
digest_only_kinds = ["price_increase", "unusual_charge"]   # in the app and the weekly summary, never sent one by one

[alerts.thresholds]
sync_failures = 3                    # failed syncs in a row before an alert (high at twice that)
anomaly_min_severity = "medium"      # unusual-charge alerts: medium | high
```

Kinds you can list: `consent`, `sync_failing`, `unusual_charge`, `price_increase`, `low_balance`, `budget`, `loan_alert`, `loa_end`,
`unused_subscription`, `contract_notice`, `decision_contradicted`, `scheme_end`, `scheme_check`, `rent_missing`.

!!! note "Quiet hours hold everything"
    Quiet hours and the weekly budget also hold back a high alert; there is no bypass. The in-app feed is never held.

Acknowledge, snooze and mute from the Alerts page (**Acknowledge**, **Snooze 7 days**, **Mute this kind**) or with `coach alerts ack|snooze|mute-kind`.
That state lives in the database, not in the config.

## Channels

Store each secret with `coach config set-secret`, never in the file. A channel that is enabled but misses a field or a secret is reported by
`coach alerts channels` and skipped; it never stops the daily job.

=== "ntfy"

    ```toml
    [alerts.ntfy]
    enabled = true
    url = "https://ntfy.sh/<long-random-topic>"    # https only; or your own server
    ```

    ```bash
    uv run coach config set-secret ntfy_token      # only for a protected topic
    ```

    On public ntfy.sh the topic name is the only secret: make it long and random, or use a self-hosted server with a token.

=== "E-mail"

    ```toml
    [alerts.email]
    enabled = true
    host = "smtp.example.com"
    port = 587
    tls = "starttls"                  # starttls | ssl; a connection without TLS is refused
    username = "coach@example.com"
    from = "coach@example.com"
    to = "anna@example.com"
    ```

    ```bash
    uv run coach config set-secret smtp_password
    ```

=== "Telegram"

    ```toml
    [alerts.telegram]
    enabled = true
    chat_id = "<your chat id>"        # not secret
    ```

    ```bash
    uv run coach config set-secret telegram_bot_token
    ```

=== "macOS"

    ```toml
    [alerts.macos]
    enabled = true
    ```

    A local notification. It may show the alert's title (a bank or merchant name: it stays on this Mac); IBAN-like values, e-mails and long
    numbers are masked. It replaces `[notify] macos`.

## What a message says: `external_detail`

ntfy, e-mail and Telegram are third-party transports. Their messages are built from a fixed vocabulary and numbers, never from the alert's own
text, and pass the same privacy guard as the finance tools. The title is always `Coach alerts`.

=== "minimal (default)"

    ```text
    Coach: 2 new alerts (1 high). Open the app.
    ```

=== "summary"

    ```text
    Coach: 2 new alerts (1 high).
    - Unusual charge (high), about 150 EUR
    - Bank consent expiring or expired (medium)
    Open the app.
    ```

    The kind, the severity and, for some kinds, an amount rounded to 10 EUR (100 from 1000 up).

**Never, in either mode**: a merchant, account, bank, person, employer, town, IBAN, e-mail, phone, address, contract number or balance. A
`summary` the guard refuses goes out as `minimal`. Children's budget alerts (`kid_budget`) and the AI-cost alert never leave the app, not even
as a count. `[privacy] local_only` turns ntfy, e-mail and Telegram off.

## The weekly summary

```toml
weekly_digest = true                 # build it once a week into the in-app feed (deterministic, local)
weekly_digest_day = "mon"            # mon .. sun
weekly_digest_to_channels = false    # also send a teaser: "Coach: your weekly summary is ready. Open the app."
app_url = ""                         # optional link added to external messages
```

It compares last week's variable spending with a usual week, lists the next 7 days of payments and deadlines, the open alerts, budgets at
risk and the savings tracker. A channel only ever gets the teaser, never the summary.

## Test without sending

```bash
uv run coach alerts channels                          # what is enabled and ready (secrets never printed)
uv run coach alerts test-channel ntfy --dry-run       # the exact test message, tokens masked; sends nothing
uv run coach alerts check --dry-run                   # what each channel would get for today's real alerts
uv run coach alerts digest --dry-run                  # preview the weekly summary
```

The **Test (dry run)** buttons in the Channels panel of the Alerts page do the same. A real test (`test-channel` without `--dry-run`) needs
an interactive terminal and a typed yes; run it yourself, once, to check the transport works.

## See also

- [Insights & alerts in the guide](../guide/insights-alerts.md)
- [Alerts reference](../alerts.md): every kind, dedupe and escalation rules
- [Privacy modes](privacy.md)
