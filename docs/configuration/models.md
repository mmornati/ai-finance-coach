# AI models

Pick the model backend that matches your privacy, cost and set-up wishes: your Claude subscription, an API key, any OpenAI-compatible provider or a local Ollama.

<figure class="shot" markdown>
![The AI usage page of the demo household](../assets/screens/usage-light.webp#only-light){ loading=lazy }
![The AI usage page of the demo household](../assets/screens/usage-dark.webp#only-dark){ loading=lazy }
<figcaption>The AI usage page: calls, tokens and cost per job and model, and the hosts the calls went to (from the local egress journal).</figcaption>
</figure>

## Where a model is used, and where it is not

**Numbers come from code, words come from the model.** Most of the app never calls a model at all.

| Uses a model | Never uses a model |
|---|---|
| the **long tail of categorization**: merchants that rules, your memory and near-duplicate matching could not label (`coach classify run`) | bank sync, normalization, internal transfers |
| the **coach**: Ask the coach, `coach coach ask`, the skills | analytics: averages, recurring payments, price changes, anomalies |
| the opt-in **digests** (weekly digest, monthly review) | forecasts, budgets, net worth, loan schedules, the weekly alerts summary |

Two settings choose the backend: `[llm] backend` for categorization and `[coach] backend` for the coach. They can differ.

## The four backends

=== "claude-code"

    Runs `claude -p` (Claude Code, headless) on **your Claude subscription**. The default.

    ```toml
    [llm]
    backend = "claude-code"
    model = "sonnet"          # sonnet | haiku | opus

    [coach]
    backend = "claude-code"
    # model = "sonnet"
    max_budget_usd = 1.0      # cap per question (digests x2 / x3); 0 = no cap
    ```

    - Needs the `claude` command, logged in on this machine.
    - **Personal, low-frequency use only.** A subscription is for interactive-scale use: you asking a few questions a day, the daily
      categorization and an opt-in weekly digest. For automation, sharing the app or anything frequent and unattended, use an API key.
      Check Anthropic's current usage terms.
    - The cost shown on AI usage is **notional** (API prices for the tokens used); nothing is billed per call.
    - `claude` runs in an empty temporary folder with only the finance tools allowed and a minimal environment (no key, no proxy).

    **In Docker**: build the CLI into the image with `docker compose build --build-arg WITH_CLAUDE_CODE=1` (pinned version, checked
    against a pinned hash). On your computer run `claude setup-token`, save the token in `secrets/claude_code_oauth_token` (`chmod 600`)
    and list it in a `docker-compose.override.yml`. Details: [Docker](../docker.md).

=== "anthropic-api"

    The Anthropic SDK with **your API key**: per-token billing, for automation or sharing.

    ```toml
    [llm]
    backend = "anthropic-api"
    anthropic_model = "claude-haiku-4-5"   # aliases haiku | sonnet | opus also accepted
    batch_api = false                       # true = one Message Batches request per run (50 % cheaper, async)

    [coach]
    backend = "anthropic-api"
    # model = "claude-sonnet-5-5"           # the default for this backend
    ```

    ```bash
    uv run coach config set-secret anthropic_api_key
    ```

    - Always talks to `https://api.anthropic.com` (`ANTHROPIC_BASE_URL` is ignored; `[llm] anthropic_base_url` overrides it).
    - Prompt caching on the static prompt and the tool definitions; the cost on AI usage is an estimate from the price table.
    - With `batch_api = true` a timed-out run resumes the same batch next time: nothing is paid twice.

=== "openai-compatible"

    Any OpenAI-style `/chat/completions` API: **OpenRouter, Eden AI**, a self-hosted vLLM ... The key is the secret `openai_api_key`
    (environment `COACH_OPENAI_API_KEY`, deliberately **not** `OPENAI_API_KEY`, so an unrelated OpenAI key is never sent to another provider).

    **OpenRouter**

    ```toml
    [llm]
    backend = "openai-compatible"
    openai_base_url = "https://openrouter.ai/api/v1"     # the default
    openai_model = "anthropic/claude-haiku-4.5"         # the PROVIDER's model id (required)
    openrouter_deny_data_collection = true              # only providers that do not store or train on prompts (default)

    [coach]
    backend = "openai-compatible"
    model = "anthropic/claude-sonnet-4.5"               # a model with tool calling
    ```

    **Eden AI**

    ```toml
    [llm]
    backend = "openai-compatible"
    openai_base_url = "<Eden AI's OpenAI-compatible base URL>"   # copy it from Eden AI's "OpenAI compatible" docs
    openai_model = "<a model id from Eden AI>"

    [coach]
    backend = "openai-compatible"
    # model = "<a model id with tool calling>"           # default: [llm] openai_model
    ```

    `openai_base_url` is the part **before** `/chat/completions`. Copy it, and the model ids, from Eden AI's "OpenAI compatible"
    documentation: this project does not ship a URL for it.

    ```bash
    uv run coach config set-secret openai_api_key
    ```

    - The base URL must be `https` (or `http` on this machine only).
    - The cost is logged only when the provider reports it (OpenRouter does); it is never guessed.
    - Structured output uses the JSON schema, with a JSON-mode fallback for models without it.

=== "ollama"

    A model on **your own machine**: free, and nothing leaves it.

    ```toml
    [llm]
    backend = "ollama"
    ollama_url = "http://localhost:11434"
    ollama_model = "llama3.1"       # must support the JSON-schema `format`
    ollama_allow_remote = false     # the server must be on loopback unless true

    [coach]
    backend = "ollama"              # a model that supports tool calling, or the coach refuses
    ```

    - Prompts contain (redacted) bank data, so a non-loopback `ollama_url` is refused unless `ollama_allow_remote = true`, and always
      refused under `[privacy] local_only`.
    - Small local models classify and explain less well than a cloud model. Answers are not streamed token by token.

!!! note "The coach needs tool calling"
    The coach reads your data only through the finance tools, so its model must support tool calling. With `ollama` or
    `openai-compatible` a model without it is refused with a clear message. Categorization does not need tools.

## Compare

| | claude-code | anthropic-api | openai-compatible | ollama |
|---|---|---|---|---|
| Where data goes | Anthropic, through `claude` | `api.anthropic.com` | the provider you set | this machine |
| Cost | your subscription (cost shown is notional) | per token, estimated | per token, when the provider reports it | free |
| Set-up | `claude` logged in | API key | API key + base URL + model id | an Ollama server + a model |
| Use | personal, low frequency | automation, sharing | when you have no Anthropic key | full privacy (`local_only`) |
| Works in Docker | opt-in build arg + token | yes | yes | yes (`ollama_allow_remote` for a host server) |

Every backend gets the same **redacted** input: IBANs, e-mails, phone numbers, long digit runs and your household's names are removed,
and anything that looks like a person is never sent. See [Privacy modes](privacy.md).

## Coach limits

| Key | Default | Meaning |
|---|---|---|
| `[coach] max_tool_calls` | `12` | tool calls per question (digests get twice as many) |
| `[coach] max_tokens` | `4096` | per model answer (API, ollama, openai-compatible) |
| `[coach] timeout_seconds` | `180` | per question (digests: three times) |
| `[coach] schedule_weekly` / `schedule_monthly` | `false` | opt-in digests written by the daily job |
| `[coach] allowed_builtin_agents`, `allowed_plugins`, `allow_builtin_plugins` | see template | what the `claude` start-up event may list; anything else aborts the run |
| `[coach] claude_env` | `[]` | extra environment variable names passed to `claude` (never a secret) |
| `[coach] claude_restricted` | `false` | also pass `--restricted` to `claude -p` (not verified with a subscription login) |

## See what would be sent, then send

```bash
uv run coach classify run --dry-run              # the exact redacted requests (items, examples, hints); nothing is called
uv run coach coach digest --weekly --dry-run     # the exact prompts and redacted tool outputs; no model is called
uv run coach coach ask --skill monthly-review --dry-run
```

During first-run set-up (`coach setup`) the categorization step shows this dry run first, then asks you to choose: run it with the
current backend, go fully local, or skip. Running it needs you to type `send`; nothing leaves the machine before.

!!! tip "Review the dry run before the first run on a new bank"
    Person detection is a heuristic. A family-named company can be held back, a shop named after its owner can pass. The dry run shows
    exactly what would go out; `coach classify review` lists what was held back.

## Costs and the AI usage page

Every call writes one row with backend, model, tokens, cost estimate, duration and purpose. See it with `coach usage` or on
**AI usage** in the web app (7, 30 or 90 days). Set `[usage] monthly_warn_usd` to get a local alert when a month costs more than you want.

## See also

- [The coach (LLM): backends, isolation, costs](../coach.md)
- [CLI reference: LLM backends](../reference.md)
- [Quality & AI usage](../guide/quality.md)
- [Docker: Claude Code in the container](../docker.md)
