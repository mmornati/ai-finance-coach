# Security: threat model, audit and residual risks (E11-2, E11-3)

This is a single-user, single-machine application whose database is a household's complete financial history. It runs as the user, on the
user's Mac. This page lists what must be protected, who could attack it, where the trust boundaries are, which controls exist, which
permission rules the user has adopted for coding agents, and what is left.

Run `uv run coach security audit [--json] [--fix-permissions]` at any time (read-only apart from the optional fix; exit code 1 on a critical;
no secret value is ever printed). `coach schedule run` runs it weekly as a warn-only step.

## 1. Assets

| Asset | Where | Why it matters |
|---|---|---|
| the database | `data/finance.db` (SQLCipher) | every transaction, balance, IBAN, merchant, insight |
| the household memory | `memory/` (+ `.history.git`, `.proposals`, documents) | names, loans, contracts, contract numbers, contact details |
| `db_key`, `backup_key`, `proposal_key` | macOS Keychain (service `ai-finance-coach`) or environment | decrypt the database / backups; seal memory proposals |
| Enable Banking private key | a `.pem` file, 0600, under the coach home (`enablebanking/eb-private-key.pem`; in Docker `coach-home/`, outside the repository: keep it there, the audit warns when it sits inside a checkout) | with the app id it can create bank sessions and read account data for as long as a consent lives |
| bank consents (sessions) | Enable Banking, ids in the database | read access to the accounts, up to 180 days (`coach connect --days`) / the bank's limit; see [enable-banking.md](enable-banking.md) |
| API keys / tokens | Keychain: `anthropic_api_key`, `ntfy_token`, `smtp_password`, `telegram_bot_token` | spend money, send messages as you |
| web app session key and login tokens | `data/ui-session.key`, `ui-login.json`, `ui-revoked.json`, `ui-state.json` | whoever holds a valid cookie sees all the data and can drive the app; a cookie of a CHILD login (E14-8) only reaches that child's own data (`/me/*`: the guard denies every other endpoint), and its role is read from the database on every request |
| passkeys (E16) | `ui_passkeys` table of the database: PUBLIC keys only, with their login and host name | nothing by itself (the private key stays on the device); a stolen or cloned device is the risk: remove its passkey in the web app |
| the SSO mapping (E16) | `[ui.sso_users]` in `config.toml`, and the proxy's own accounts | whoever controls a mapped identity-provider account opens that login; the token is verified against the provider's keys fetched from `[ui] sso_jwks_url` |
| backups and exports | `backups/`, `data/exports/` | the whole data set in one file (encrypted with `backup_key`) |
| the integrity of memory | `memory/*.yaml` | what the coach believes about the household; a poisoned memory corrupts every later answer |

## 2. Actors

| Actor | Capabilities | Main concern |
|---|---|---|
| **A coding agent running as the user** (Claude Code in this repo) | runs shell commands with the user's permissions, reads and edits files, can start `coach` commands, talks to a cloud model | reading `data/`, `memory/`, the Keychain; accepting its own memory proposals; driving the web app through its API; sending data out through a command; running destructive commands. This is the actor the permission rules target |
| **A prompt injection in bank data** | a merchant name or transfer description written by a third party, read by the coach or an agent | making the model act (propose a malicious memory change, leak data, call a tool) |
| **Another local user or process** | file reads with that user's permissions; connections to local ports | reading files with loose permissions; a local port that answers without authentication |
| **Someone on the network** | connects to an exposed port | the web app on a non-loopback interface; the callback server |
| **Someone who steals the machine or a backup** | the disk, a backup file | plaintext databases or exports, keys stored next to the data |
| **A provider** (Anthropic, Enable Banking, ntfy / SMTP / Telegram) | sees what is sent to it | that only redacted, minimal data is sent |
| **A malicious dependency or a bad update** | code execution as the user | beyond what an application can defend; mitigated by a small dependency set and lock files |
| **The user** | mistakes | committing `data/` or a key, leaving a plaintext copy, enabling a channel and forgetting |

## 3. Trust boundaries

```
 bank (PSD2)  --HTTPS-->  [ coach process ]  <--stdio-->  MCP client (Claude Code)  --HTTPS-->  model provider
                           |  encrypted DB (key: Keychain)         (redacted, pseudonymised tool results only)
                           |  memory/ (owner-only files)
                           |--HTTPS--> LLM backends (redacted)   |--> alert channels (minimal messages)
 browser --loopback + cookie + CSRF--> [ web app :8765 ]          [ callback server :8443, loopback, only during `coach connect` ]
```

1. **Machine boundary**: nothing listens beyond loopback (the web app refuses a non-loopback bind without `allow_remote` + a TLS acknowledgement +
   allowed hosts; the MCP server is stdio; the callback server binds loopback and only runs during `connect`). The audit checks the configuration
   and, when a `coach ui` is running, the real listening sockets.
2. **Redaction boundary**: everything that leaves toward a model passes the redaction layer (docs/privacy.md); the finance tools hand a model
   pseudonyms, hashed refs and computed figures, and wrap third-party text as `untrusted_text`.
3. **Agent boundary**: a coding agent is treated as untrusted code running as the user. The permission rules (section 5) and the TTY gates are
   the controls; the application also ensures that every state-changing step that matters needs a human (typed confirmation on a terminal):
   `memory accept`, `subs decisions confirm`, `loans add|edit`, `coach export --plain`, `coach wipe`.
4. **Storage boundary**: the database and backups are encrypted at rest; keys are only in the Keychain (or an environment variable, which the audit
   flags).

## 4. Controls and how they are checked

`coach security audit` (read-only):

| Area | Check | Status rules |
|---|---|---|
| secrets | `db_key`, `backup_key`, `proposal_key`, channel secrets, `anthropic_api_key`: present? where from (Keychain / env)? never the value | missing `db_key` = critical; env origin or missing backup / proposal key = warn |
| secrets | Enable Banking key file: mode, location | readable by others = critical; inside the repo folder = warn |
| secrets | web session key age against `[ui] key_rotation_days` | older = warn |
| storage | database encrypted (no SQLite header = SQLCipher) | plaintext = critical |
| storage | plaintext leftovers (`*.plaintext.bak`, `*.encrypting`, `*.importing`), any SQLite-headed file in `data_dir` or the repo, scratch databases in the repo | leftovers / plaintext = critical; scratch encrypted copy = warn |
| storage | permissions of `data_dir`, `backups`, `memory` (0700 / 0600) | key, database, backup files open to others = critical; the rest = warn |
| storage | backups carry the encrypted-archive header, none stray, newest age, retention | plaintext in `backups/` = critical; none / stale / over retention = warn |
| repository | `.gitignore` covers `data/`, `memory/` personal files, `config.toml`, `*.pem`, `backups/`, `*.bak`, `ui-*.json`, `.env*`, databases, exports | a core path uncovered = critical; others = warn |
| repository | secret-looking strings in the working tree (no git needed; `data`, `memory`, `backups`, `.venv`, `node_modules`, build output skipped): PEM private keys, API key formats, JWTs, high-entropy values assigned to secret-like names; `path:line` and the kind only | known formats = critical; entropy = warn. Mark a documented fixture with the text `allowlist secret` on its line |
| exposure | `[ui]` bind: loopback, or `allow_remote` + `remote_tls_ack` + `allowed_hosts` | non-loopback without allow_remote, or remote without TLS ack = critical; remote with ack = warn |
| exposure | listening sockets: `lsof -nP -iTCP -sTCP:LISTEN` parsed (read-only; skipped when lsof is missing; not run by the scheduled job) | a coach port on a non-loopback address = critical; another Python listener on a non-loopback address = warn |
| exposure | MCP server is stdio only (source + `.mcp.json` has no `url` / sse server) | network MCP = critical |
| exposure | callback server loopback (the redirect URL host); TLS key files private | non-local redirect = warn; loose TLS key = critical |
| exposure | `.claude/settings.json` holds deny rules | none = warn |
| exposure | the privacy mode (info) | |

`--fix-permissions` removes the group / other bits (`mode & ~0o077`: it never adds a bit) of the folders and files of `data_dir`, `backups` and
`memory`, never follows a symlink, refuses the home folder and its parents, and on a terminal offers the same for an open Enable Banking key
file. The audit judges every file of `data_dir` by its header (plaintext SQLite database, WAL or rollback journal, whatever the name) and checks
that every export archive is encrypted.

## 5. The permission rules the user has adopted (`.claude/settings.json`, by category)

The file is the user's; the agent does not edit it. It denies (by pattern on the command or the path):

* **Memory integrity**: accepting proposals (`memory accept`, `proposals.accept`), purging history, any edit of `memory/**`, reading or touching
  `.proposals` and `.history.git`, and the proposal key. Rejecting and reverting need a confirmation (ask).
* **The web app's secrets and state**: reading or editing `ui-session.key`, `ui-state.json`, `ui-login.json`, `ui-revoked.json`, editing `data/**`,
  and the classes of command that would reach the app.
* **Driving the web app from the agent**: `coach ui`, the API module, the login-link and session-exchange endpoints, and every spelling of the
  loopback address and port (`127.0.0.1`, `localhost`, `127.1`, `[::1]`, `0x7f...`, `:8765`), so the agent cannot fetch the one-time login link
  or call the API with `curl`.
* **Confirmation bypass**: any `coach ... --yes` / `-y`, and `script` (a pseudo-terminal that would satisfy a TTY gate).
* **Decisions and outside contact**: confirming savings decisions, the household contact block, `alerts test-channel`, `alerts digest --send`; asking
  before `doc extract --send`.
* **Logins (E14-8)**: `coach users ...` (creating a login, promoting a child to adult, enabling a disabled login is the owner's decision; the template denies the whole command). The login
  link of a person is `coach ui --login-link --user ID`, already covered by the `coach ui` rule.

A shareable **template** of these rules, built from the categories above without any path of the author's machine, is [claude-settings.example.json](claude-settings.example.json)
(E13-4): copy it to `.claude/settings.json` of the project where you run an agent and review every line. The author's own file is git-ignored.

### Recommended additions for E11 (the coordinator applies them; not applied here)

The new commands are destructive or read everything, so deny them to the agent (the application already requires a terminal and a typed phrase,
but a rule fails earlier and more visibly):

```json
"deny": [
  "Bash(*coach wipe*)",
  "Bash(*coach export*--plain*)",
  "Bash(*coach export*--decrypt*)",
  "Bash(*export*--plain*)",
  "Bash(*security find-generic-password*)",
  "Bash(*security find-internet-password*)",
  "Bash(*security dump-keychain*)",
  "Bash(*keyring*)",
  "Bash(*delete_secret*)",
  "Bash(*coach config set-secret*)",
  "Bash(*.zip.enc*)",
  "Read(**/exports/**)",
  "Read(**/backups/**)",
  "Read(//Users/you/ai-finance-coach-export-*)",
  "Read(**/*.pem)",
  "Edit(/backups/**)",
  "Edit(//Users/you/Projects/ai-finance-coach/config.toml)"
],
"ask": [
  "Bash(*security audit*--fix-permissions*)"
]
```

Notes: `config.toml` holds the privacy switches (`local_only`, `web_enrich`, channel enables): an agent that can edit it can enable a web search or a
channel, so edits are better made by the user (add the Edit deny, or accept the residual risk below). `Bash(*keyring*)` blocks `python -c "import
keyring"`; `uv run coach ...` itself is not blocked.

## 6. Residual risks (honest list)

1. **A coding agent running as the user can still read a lot.** The rules above block specific paths and commands; they cannot stop every
   way to read a file the user can read. Not denied today: reading `memory/**` with the Read tool (only Edit is denied), running read-only
   `coach` commands that print raw names (`coach classify review`, `coach memory show`, `coach cashflow`; the MCP tools redact, the CLI does not),
   and listing `data/` file names. CLAUDE.md tells the agent to use the MCP tools; that is an instruction, not a control.
2. **The Keychain is as strong as its ACL.** A same-user process may ask for an item and macOS then prompts; a user who clicks "Always allow" for a
   tool lets it read the secret silently. The macOS `security` CLI is not blocked by the current rules (recommended above).
3. **Environment secrets** (`COACH_DB_KEY` ...) are inherited by every child process of that shell; the audit warns. The scheduled job uses launchd's
   environment, which holds none unless you add them.
4. **Redaction is heuristic.** It removes IBANs, e-mails, phones, long numbers and household names and withholds person-like merchants, but a
   merchant title can still carry a town or a small business name, and a stranger's name that looks like a brand can pass. `[privacy] model_detail =
   "coarse"` (default) generalises more (bank names become their kind, towns are cut, amounts sent for classification are rounded to an order of
   magnitude); `"standard"` sends brand and bank names and free-text notes with name / regex scrubbing only; `local_only` removes the exposure.
5. **A provider sees what it is sent** (and Anthropic's `claude -p` may itself send telemetry according to its own settings): this application
   cannot audit the `claude` binary. The egress journal records what the coach handed to it, under the host `api.anthropic.com`: if you list
   `ANTHROPIC_BASE_URL` or a proxy variable in `[coach] claude_env`, the traffic goes where YOU pointed it and the journal still says Anthropic.
6. **The egress gate with no activated policy** loads `config.toml` (or `$COACH_CONFIG`) and applies it; if that is impossible every outbound
   call is refused. **The egress gate is cooperative inside the process**: it stops the application's own call sites (and the coverage test keeps them registered),
   it does not stop another program on the machine, nor a person editing `config.toml`. It cannot enforce the interactive skills' web search (a
   rule plus `coach privacy status`); `local_only` does not stop an agent that decides to use its own WebSearch tool, only Claude Code's own
   permissions can.
7. **The same-user file-write problem**: a process running as the user can edit memory files directly (the history, the proposal seal and the
   permission rules mitigate; they do not prevent). Without a `proposal_key` the proposal seal is a plain SHA-256.
8. **Unlinking is not shredding** (`coach wipe`, `restore --force`): FileVault and deleting the keys are the real erase.
9. **`tests` and the repo scan use heuristics**: the secret scan finds known key shapes and high-entropy assignments; it is not a full secret scanner,
   and it does not look at git history (there is no git in this working copy; run a scanner such as gitleaks before publishing a repository).
10. **Logs** under `data/logs/` can name a bank or account label in warnings; they are owner-only, rotated by size and age (`coach logs`).
11. **The web build** (`src/coach/api/static/`) is public code; it holds no secret (the audit scans `web/` sources; build output is skipped).
12. **Per-person logins (E14-8) are as private as the link and the device.** A child login's server-side scope is deny-by-default and tested over every route, but the session is a
    cookie: whoever holds the child's browser holds that session until it expires (`[ui] session_hours`) or the login is disabled (`coach users disable`, effective on the next request).
    The owner login (a session with no user) still sees everything and is for the person at the machine. A shared joint account can only be attributed to a child through the rules you write.
13. **A passkey (E16) is as safe as the device and its unlock.** The server checks origin, relying-party id, signature and counter, so a phishing page or a
    cloned authenticator is refused, but a person who can unlock the device opens the login the passkey belongs to. `--rotate-session-key` does not remove
    passkeys: remove a lost device's passkey in the web app. Enrolment happens from a session, so whoever holds a session can add a passkey for it.
14. **SSO (E16) moves the first gate to the proxy.** The app verifies the proxy's signed token, never a plain header, against keys fetched from its own
    configuration; what it cannot verify is that the proxy is the only way to reach it (another published port, another route without the forward-auth
    middleware) or that the identity-provider account is well protected (MFA, passkeys there). The JWKS fetch over plain http on a container network trusts
    that network; a TLS-terminating tunnel in front of the proxy sees the traffic in clear. `coach security audit` reports the mode; the one-time link keeps
    working and `sso = "none"` switches the mode off.

## 7. If something leaks

* A database or backup copy escaped: it is encrypted, so the risk is the key. If `db_key` / `backup_key` may also have escaped, treat the data
  as exposed. There is no command to re-encrypt the database under a new key yet (a gap: `coach export` writes everything out, but there is no
  import of an export either); until there is, the practical answer is to restore a backup into a new data folder with a new `db_key` by hand.
* The Enable Banking key escaped: revoke it in the Enable Banking console, create a new app / key, and revoke the sessions (`coach wipe` offers
  it, or the console).
* An API key escaped (Anthropic, ntfy, SMTP, Telegram): revoke at the provider, `coach config set-secret <name>` with the new one.
* The web session key or a login token escaped: `uv run coach ui --rotate-session-key`.
* A device with a passkey is lost: remove that passkey in the web app (Household > Passkeys; a child: My money), then rotate the session key.
* An identity-provider account used for SSO is compromised: disable it (or remove it from `[ui.sso_users]`), rotate the session key, review `audit_log`.
