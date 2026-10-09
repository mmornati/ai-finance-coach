# Install

Pick one of three ways to install the coach; all of them end with `coach init` and `coach doctor`.

<figure class="shot" markdown>
![The Set up page of the web app](../assets/screens/setup-light.webp#only-light){ loading=lazy }
![The Set up page of the web app](../assets/screens/setup-dark.webp#only-dark){ loading=lazy }
<figcaption>Once installed, the Set up page shows the seven first-run steps and what the coach still needs to know.</figcaption>
</figure>

## Choose how

| Way | Best for | Secrets | Model backends |
|---|---|---|---|
| **uv or pipx** | Your Mac or Linux laptop | macOS Keychain | all four |
| **Docker Compose** | A home server, or a machine without a Keychain | files, mode 0600 | all four (Claude Code is opt-in, see [Run it on a server](server.md)) |
| **From source** | Trying the demo, developing, contributing | macOS Keychain | all four |

=== "uv / pipx"

    ```bash
    uv tool install ai-finance-coach        # or: pipx install ai-finance-coach   (Python 3.11+; uv fetches one for you)
    coach init                              # your private home
    coach doctor
    ```

    - Try it without installing: `uvx --from ai-finance-coach coach doctor`.
    - From a release file: `uv tool install ./ai_finance_coach-X.Y.Z-py3-none-any.whl`.
    - The package ships the built web app, the database migrations and the data files.

    !!! warning "Not from a git URL"
        Installing straight from a git URL does *not* include the web app, which is built, not committed. Use a release, or build it
        from a checkout (the "From source" tab).

=== "Docker Compose"

    ```bash
    cp -r <this repository> coach && cd coach           # or download a release
    mkdir -p secrets coach-home/data coach-home/memory coach-home/config && chmod 700 secrets
    for k in db_key backup_key proposal_key; do
      (umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > "secrets/$k")
    done
    export COACH_UID=$(id -u) COACH_GID=$(id -g)      # the volumes belong to you; the container never runs as root
    docker compose build
    docker compose run --rm coach doctor
    ```

    - On its first start the container runs `coach init --non-interactive` itself: configuration with container defaults, memory
      skeleton, empty encrypted database. Existing files are never overwritten.
    - Back up the `secrets/` folder in your password manager **now**.
    - The image is not published: you build it yourself.

    Then continue with the [Quickstart](quickstart.md) (prefix each command with `docker compose run --rm coach`) and
    [Run it on a server](server.md).

=== "From source"

    ```bash
    git clone https://github.com/mmornati/ai-finance-coach.git && cd ai-finance-coach
    uv sync                                  # Python and all wheels; no brew needed
    (cd web && pnpm install && pnpm build)   # the web app is built, not committed
    uv run coach init
    uv run coach doctor
    ```

    Every command is then `uv run coach ...`. The checkout itself is the home: `config.toml`, `data/` and `memory/` sit at its root
    (all git-ignored). See `CONTRIBUTING.md` to work on the code.

## What `coach init` does

`coach init` creates a fresh home and never overwrites an existing file:

1. **`config.toml`**, copied from the commented example (every setting is explained in it).
2. **A private data folder** (`data/`, mode 0700) for the database, logs and the web app's state.
3. **An empty memory skeleton** (`memory/`): templates only, no household data yet.
4. **Three secrets**, after you type `generate`. Their values are never shown.

    | Secret | What it protects |
    |---|---|
    | `db_key` | encrypts the database. Losing it makes the database unrecoverable. |
    | `backup_key` | encrypts the backups. Losing it makes them unreadable. |
    | `proposal_key` | seals the memory proposals the coach writes, so they cannot be tampered with. |

5. **An empty encrypted database** (SQLCipher).

`coach init --dry-run` shows what would be created and writes nothing.

## What `coach doctor` does

`coach doctor` checks the installation and prints the next step. It is read-only: it writes nothing, calls no network and never prints a
secret. It looks at:

- Python and the SQLCipher wheel
- the configuration and the secret store (Keychain or files), and the three secrets
- the database
- the Enable Banking settings and key file
- file permissions
- the built web app
- the `claude` command, only when a backend needs it
- `git` (for the memory change history)

Each check is `ok`, `info`, `warn` or `fail`. The exit code is 1 when anything fails; `--json` gives a machine-readable report.

## Where your files live

| Install | Home (the folder holding `config.toml`) |
|---|---|
| uv / pipx | `~/.ai-finance-coach`, or the folder in `COACH_HOME` |
| Docker Compose | `coach-home/` next to `docker-compose.yml` (mounted as `/data`, `/memory`, `/config`) |
| From source | the checkout itself |

A `config.toml` in the current folder wins over the default home. Relative paths in the configuration are resolved against the folder
that holds it.

## Where the secrets live

=== "macOS"

    In the Keychain, under the service `ai-finance-coach`. Nothing secret is written to `config.toml`. API keys and channel tokens go
    there too, with `coach config set-secret NAME`.

=== "Docker and Linux"

    Files, one per secret, holding the value only, mode 0600, in a folder that belongs to you (`secrets/`, mounted read-only on
    `/run/secrets`). On a Linux host without Docker, `coach init --secrets-backend file --secrets-dir DIR` does the same.

!!! warning "Back up your keys"
    Keep `db_key` and `backup_key` in your password manager. Without them, neither the database nor the backups can be opened, by you
    or anyone else. That is the point of the encryption.

## Updating

=== "uv / pipx"

    ```bash
    uv tool upgrade ai-finance-coach        # or: pipx upgrade ai-finance-coach
    coach doctor
    ```

=== "Docker Compose"

    ```bash
    docker compose build                     # with the new version of the repository or release
    docker compose up -d
    ```

=== "From source"

    ```bash
    git pull
    uv sync
    (cd web && pnpm install && pnpm build)   # after any change to the web app
    ```

Database changes apply by themselves the first time a command opens the database (`[db] auto_migrate = true`, the default). Before
migrating a database that holds data, the coach writes a safety copy (`*.pre-migrate-<timestamp>.bak`). Set `auto_migrate = false` if you
prefer to run `coach db migrate` yourself. Run `coach backup` before a big upgrade anyway.

## Removing it

- `coach schedule uninstall` removes the daily job on macOS.
- `coach export` writes an encrypted archive of all your data, if you want to keep a copy.
- `coach wipe --dry-run` lists everything that would be deleted, and deletes nothing.

!!! danger "`coach wipe` is permanent"
    `coach wipe` deletes the database, the memory and the data folder (backups and Keychain secrets are asked separately). It only runs
    in a terminal, writes a safety export first unless you say otherwise, and asks you to type `DELETE MY DATA`. There is no `--yes`.
    Read the dry run first. Details in [Privacy](../privacy.md).

Then uninstall the package (`uv tool uninstall ai-finance-coach`) or delete the Docker folder.

## See also

- [Quickstart](quickstart.md): from `coach init` to your first categorized month
- [Docker](../docker.md): volumes, secrets files, model backends in a container
- [CLI reference](../reference.md): every command and setting
- [Security](../security.md): `coach security audit` and the threat model
