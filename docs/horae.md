# Horae brief (`/mcp`)

[Horae](https://github.com/horae-dev/horae) is a personal pipeline that writes a morning note, creates tasks and sends the rare urgent push.
Its source [`mcp-brief.collect`](https://github.com/horae-dev/plugin-mcp-brief) calls one MCP tool, `brief`, on each service it knows and
turns the answer into a section of the note. The web app serves that tool at **`/mcp`** (MCP streamable HTTP), **off by default**.

What Horae does with it is fixed by the [brief contract v1](https://github.com/horae-dev/plugin-mcp-brief/blob/main/docs/brief-contract.md):
the headline, `info` / `warn` alerts and links go into the note, tasks become tasks, and only `crit` alerts are pushed (at most 3 a run, one
per key per day). The coach never writes to Horae and Horae never reads the coach's data: it gets this one document.

## What the brief holds

Aggregates only, built from fixed sentences and numbers by `coach/horae/brief.py`. The document ends up in a note that may be synced and
backed up, so it never carries a transaction, a merchant, a description, a bank or account name, an IBAN, a person or the amount of one payment.

| Part | Content | From |
|---|---|---|
| headline | "November so far: EUR 1,234 spent; budgets: 1 over, 2 at risk, 4 on track." | the analytics dataset: everyday spending of the month (transfers, savings, one-offs and capital left out), the budget status |
| status | `ok`; `degraded` with a reason when a consent expired, a bank keeps failing to sync, the newest transaction is more than 7 days old, or there is no data; `error` when the data could not be read | consents, sync log, dataset |
| `crit` alerts | an expired or revoked bank consent; a bank whose syncs failed twice the `[alerts.thresholds] sync_failures` count in a row | consent lifecycle, sync log |
| `warn` alerts | a consent expiring within 3 days or that could not be verified; a bank failing to sync (the alert threshold); a budget over its envelope ("Groceries budget exceeded: 112% used") | consent lifecycle, sync log, budget status |
| `info` alerts | a budget on course to overrun ("... 108% projected") | budget status |
| tasks | renew a consent before its date (due that day); reconnect an expired one; categorise N uncategorised transactions; check N merchant labels the AI was unsure about; review N pending memory proposals; answer N open questions | consents, the review queue, the memory |
| links | the app, Budgets, Review queue, Connections, Memory (only those that matter today) | `[ui] allowed_hosts` |

- **A child's money stays out**, as in the alerts: accounts a child owns or with purpose `kids`, payments attributed to a child and a child's
  budgets are left out of the total and the budget alerts.
- **Stable keys and ids**, so that Horae dedupes: a consent or a bank is named by a short hash of its session (`consent.3f9c2a1b7e.expired`),
  a budget by a hash of its id and the month (`budget.65e761bcb5.2026-11.over`), a chore by its ISO week (`review.uncategorised.2026-W48`: one
  task a week at most, however many runs see it).
- **Fail closed.** Every text passes the finance tools' privacy guard (household names, account labels, IBAN, e-mail, secrets, paths) and an
  item that trips it is dropped. The document is then checked against the vendored v1 schema (`coach/horae/brief-v1.schema.json`, byte for
  byte the plugin's) and the plugin's extra rules (unique keys, real dates, validity at most 48 hours); one that fails, or is over 16 KiB, is
  replaced by a fixed `error` brief. `valid_until` is `generated_at` + 24 hours.
- **Links** are `https://<first [ui] allowed_hosts>/...`, the host the one-time login link uses, and only when `[ui] allow_remote` is on.
  Without it the brief has no link at all.

Example (from the synthetic household of `tests/test_horae_brief.py`, shortened):

```json
{"schema_version": 1, "service": {"id": "finance-coach", "name": "Finance"},
 "generated_at": "2026-10-04T12:00:00Z", "valid_until": "2026-10-05T12:00:00Z",
 "status": {"state": "ok", "reason": null},
 "headline": "October so far: EUR 130 spent; budgets: 1 over, 1 on track.",
 "alerts": [{"key": "consent.cd85746aa0.expiring", "severity": "warn", "title": "A bank consent expires in 2 days",
             "body": "Renew it in the app (Connections) to keep the bank syncing.", "link": "https://coach.example.net/connections"},
            {"key": "budget.65e761bcb5.2026-10.over", "severity": "warn", "title": "Groceries budget exceeded: 150% used",
             "link": "https://coach.example.net/budgets"}],
 "tasks": [{"id": "consent.cd85746aa0.renew.2026-10-06", "title": "Renew a bank consent before 2026-10-06", "due": "2026-10-06",
            "link": "https://coach.example.net/connections"},
           {"id": "review.uncategorised.2026-W40", "title": "Categorise 6 uncategorised transactions",
            "link": "https://coach.example.net/review"}],
 "links": [{"label": "Finance coach", "url": "https://coach.example.net/"}]}
```

## The endpoint

| | |
|---|---|
| URL | `https://<your host>/mcp`, on the web app's own server (`coach ui`): the brief reads the same database, memory and warm analytics snapshot, so there is no second process and no second key |
| Protocol | MCP streamable HTTP, stateless, JSON answers (no event stream, no session kept), one tool: `brief(schema_version: int = 1)`. The finance tools of `coach mcp serve` are **not** reachable here |
| Auth | `Authorization: Bearer <token>`, compared in constant time with the secret `horae_brief_token`. Missing or wrong: `401` (the body is not read) |
| Off | without the secret, with a token shorter than 32 characters, or with `[privacy] local_only` or `offline`: `/mcp` answers `404` like any unknown path |
| Limits | POST only (`405` otherwise), request body 64 KiB (`413` above, also when streamed), 30 s per request, the document 16 KiB, no compression, `Cache-Control: no-store` |
| Logs | one line per brief with the state and counts; never the token, a request or an answer |
| Journal | each answer is one `horae.brief` row in the egress journal (size only), listed by `coach privacy report` |

The web app's own checks still run first: the `Host` must be one of `[ui] allowed_hosts`. The token opens `/mcp` only: every `/api` call still
needs a browser session, and the page routes are unchanged. `coach security audit` says whether the endpoint is on (`horae_brief`).

## Turn it on

1. Make a long random token and keep it in your password manager (Horae needs the same value):

    ```bash
    (umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > secrets/horae_brief_token)
    ```

2. Give it to the coach as the secret `horae_brief_token`:

    === "Docker"

        Add it to the `coach` service in `docker-compose.override.yml`, next to the other secrets (keep the lists complete):

        ```yaml
        services:
          coach:
            secrets: [db_key, backup_key, proposal_key, horae_brief_token]

        secrets:
          horae_brief_token:
            file: ./secrets/horae_brief_token
        ```

    === "On a Mac"

        `uv run coach config set-secret horae_brief_token` and paste the value (or set `COACH_HORAE_BRIEF_TOKEN` in the environment of
        `coach ui`).

3. Restart the web app (the token is read at start; restart again to rotate it). `coach security audit` now reports `horae_brief` as on.

4. In Horae, store the same value as `MCP_BRIEF_TOKEN_1` and add the service to the pipeline:

    ```yaml
    - mcp-brief.collect:
        services:
          - id: finance
            url: https://coach.example.net/mcp
            token_secret: MCP_BRIEF_TOKEN_1
            link_prefix:
              - https://coach.example.net/
    ```

    The plugin refuses an IP address, `localhost`, plain http and redirects: call the public `https` name through the proxy, and approve
    the host in `horae.lock`.

5. Stop the coach's own channel messages for what the brief already says (consents, sync, budgets: `[alerts]` in `config.toml`), or you
   are told twice. That choice is yours; the coach does not change it.

To turn the endpoint off, remove the secret and restart.

## Behind Traefik and authentik

With the [authentik recipe](configuration/remote.md#an-identity-aware-proxy-authentik), every route of the app goes through authentik's
forward-auth. Horae cannot sign in there, so `/mcp` gets its own router **without** the SSO middleware: the bearer token replaces it. Only
that exact path; everything else keeps SSO.

```diff
 labels:
   - "traefik.http.routers.coach.rule=Host(`coach.example.net`)"
   - "traefik.http.routers.coach.middlewares=authentik@file"
   - "traefik.http.services.coach.loadbalancer.server.port=8765"
   - "traefik.http.routers.coach-sso.rule=Host(`coach.example.net`) && PathPrefix(`/outpost.goauthentik.io/`)"
   - "traefik.http.routers.coach-sso.service=sso@file"
+  # Horae's brief: exactly /mcp, no SSO (the app checks the bearer token), higher priority than the catch-all router above
+  - "traefik.http.routers.coach-brief.rule=Host(`coach.example.net`) && Path(`/mcp`)"
+  - "traefik.http.routers.coach-brief.priority=1000"
+  - "traefik.http.routers.coach-brief.service=coach"
+  - "traefik.http.routers.coach-brief.entrypoints=websecure"
+  - "traefik.http.routers.coach-brief.tls=true"
```

- `Path` (not `PathPrefix`) matches `/mcp` only: `/mcp/x`, `/mcpx` or `/api/...` still go to the SSO router.
- Use the entry point and TLS settings of your existing router (`websecure` / `https`, the same `tls.certresolver`), and point `service=` at
  the service the app's router already uses.
- **Coolify** names its generated routers and services itself (`http-0-<id>`, `https-0-<id>`): add the five labels in the app's *Container
  Labels*, with `service=` set to the generated service name, and do not attach Coolify's `gzip` middleware to this router (Horae sends
  `Accept-Encoding: identity` and refuses a compressed answer).
- Horae on the same Docker host still calls `https://coach.example.net/mcp` (the plugin accepts only a public `https` name); Traefik must
  resolve and route it like any other request. Nothing else needs a published port.

Check it from the server (the token from your password manager in `$TOKEN`):

```bash
curl -sS https://coach.example.net/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"brief","arguments":{"schema_version":1}}}'
curl -sS -o /dev/null -w '%{http_code}\n' https://coach.example.net/mcp     # 401 without the token
curl -sS -o /dev/null -w '%{http_code}\n' https://coach.example.net/api/v1/session -H "Authorization: Bearer $TOKEN"   # authentik's redirect, as before
```

## What this trusts

Whoever holds the token reads the day's aggregates: the month's spending total, budget percentages by category, counts and consent dates. Keep
it with the other secrets (`secrets/`, mode 0600) and in Horae's own secret store, never in a URL, a config file or a log. If it leaks,
replace it in both places and restart. A brute force is not practical (a 256-bit token, compared in constant time), and nothing else of the
app is reachable with it.

## See also

- [Remote access](configuration/remote.md) (the proxy and SSO set-up this builds on)
- [Privacy](privacy.md) (the egress inventory, `horae.brief`) and [Security](security.md)
- [Alerts](alerts.md) (the coach's own channels, which the brief can replace)
