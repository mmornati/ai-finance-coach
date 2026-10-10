"""Pluggable LLM backends (E2-6). All of them answer the same question with the same contract:

    complete(static, dynamic, schema, model, ...) -> (parsed JSON dict, Usage)

``static`` is the part of the prompt that never changes between calls (instructions + taxonomy): the Anthropic API
backend caches it (prompt caching); ``dynamic`` carries the user's examples and the items. Backends:

* ``claude-code``   headless ``claude -p`` (personal subscription, cost is notional) - the original behaviour
* ``anthropic-api`` official SDK, key from the ``anthropic_api_key`` secret, JSON-schema structured output, cached
                    static prompt, optional Message Batches API (50 % cheaper, asynchronous)
* ``ollama``        local HTTP server, JSON schema through ``format``
* ``openai-compatible`` any OpenAI-style ``/chat/completions`` endpoint (OpenRouter, Eden AI, a self-hosted vLLM / LM Studio ...): base
                    url in ``[llm] openai_base_url``, key from the ``openai_api_key`` secret, JSON schema through ``response_format``

Nothing here knows about banks or people: the caller redacts every item first (:mod:`coach.classify.redact`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

import requests

from coach import egress
from coach.claude_cli import DENIED_BUILTINS, ISOLATION_ARGS, claude_env

BACKENDS = ("claude-code", "anthropic-api", "ollama", "openai-compatible")
ALIASES = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# USD per million tokens (input, output); cached reads cost 0.1x input, cache writes 1.25x, batch calls 0.5x
PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0),
          "claude-sonnet-4-6": (3.0, 15.0), "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0),
          "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
          "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0)}


@dataclass
class Usage:
    backend: str
    model: str
    purpose: str = "label"
    items: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = 0.0         # None = unknown (model without a known price): never shown as 0
    cost_is_estimate: bool = True
    duration_s: float = 0.0
    ref: str | None = None               # batch_id:custom_id of a batch result (counted once)


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0,
                  batch: bool = False) -> float | None:
    """USD estimate, None for a model whose price we do not know."""
    if model not in PRICES:
        return None
    pin, pout = PRICES[model]
    usd = (tokens_in * pin + cache_read * pin * 0.1 + cache_write * pin * 1.25 + tokens_out * pout) / 1e6
    return round(usd * (0.5 if batch else 1.0), 6)


def with_additional_properties_false(schema: dict) -> dict:
    """Structured outputs require `additionalProperties: false` on every object."""
    s = json.loads(json.dumps(schema))

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                n.setdefault("additionalProperties", False)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(s)
    return s


@dataclass
class Outcome:
    """Result of one request of a batch: parsed data, or an error; usage when tokens were billed either way."""
    data: dict | None = None
    usage: "Usage | None" = None
    error: str | None = None


class LLMError(RuntimeError):
    """A call that was billed but whose answer is unusable (refusal, truncation, bad JSON): `usage` still counts."""

    def __init__(self, msg, usage=None):
        super().__init__(msg)
        self.usage = usage


class BatchMissing(RuntimeError):
    """The batch id is unknown to the provider (404): it will never complete."""


class BatchUnresolved(RuntimeError):
    """A batch may have been submitted without its id being recorded (interrupted run): it cannot be told apart
    safely, so nothing new is submitted until the user decides."""


class BatchPending(RuntimeError):
    """An asynchronous batch did not finish in time. Its id is saved: running the same command again resumes it."""


def safe_diagnostic(text: str | None, prompt: str, limit: int = 200) -> str:
    """A stderr / error snippet that cannot echo the prompt (merchant names...): lines that overlap the prompt are
    dropped, the rest is truncated."""
    keep = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln and not (len(ln) >= 12 and any(ln[i:i + 12] in prompt for i in range(0, len(ln) - 11, 6))):
            keep.append(ln)
    return " ".join(keep)[:limit]


def journal_purpose(purpose: str) -> str:
    """The purpose written to the egress journal for a Usage purpose ('label' | 'enrich' | 'compare' | 'doc_extract' | 'coach:...')."""
    if purpose in ("label", "enrich", "compare"):
        return f"classify.{purpose}"
    if purpose == "doc_extract":
        return "memory.doc_extract"
    if purpose == "eval":
        return "eval.models"
    return purpose if "." in purpose else f"coach.{purpose.replace('coach:', '')}"


class LLMBackend:
    name = "base"
    cfg = None                      # the configuration whose [privacy] policy applies (None: the process-wide policy)

    def complete(self, static: str, dynamic: str, schema: dict, model: str, purpose: str = "label",
                 items: int = 0, web_search: bool = False) -> tuple[dict, Usage]:
        raise NotImplementedError

    def complete_many(self, jobs: list[dict], resume_id: str | None = None, on_created=None) -> list:
        """jobs: dicts of `complete` keyword arguments. Returns one (data, usage) or Exception per job. Default: one
        call after the other."""
        out = []
        for j in jobs:
            try:
                out.append(self.complete(**j))
            except Exception as e:      # noqa: BLE001
                out.append(e)
        return out

    @property
    def supports_web_search(self) -> bool:
        return False


# ---------------------------------------------------------------- claude -p

def claude_classify_command(cfg, model: str, schema: dict, web_search: bool) -> tuple[list[str], int]:
    """The `claude -p` of a classification / extraction call, with the SAME isolation as the coach runtime (`docs/coach.md`): no
    settings file, no hook, no slash command, no built-in tool (WebSearch only for `classify enrich`), `--restricted` when configured.
    Returns (argv, timeout)."""
    denied = DENIED_BUILTINS
    if web_search:
        denied = ",".join(t for t in DENIED_BUILTINS.split(",") if t != "WebSearch")
        tools = ["--tools", "WebSearch", "--allowedTools", "WebSearch"]
    else:
        tools = ["--tools", ""]
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
           *ISOLATION_ARGS, *tools, "--disallowedTools", denied, "--permission-mode", "dontAsk",
           "--json-schema", json.dumps(schema)]
    if getattr(cfg, "coach_claude_restricted", False):
        cmd.append("--restricted")
    return cmd, 900 if web_search else 600


class ClaudeCodeBackend(LLMBackend):
    name = "claude-code"
    supports_web_search = True

    def __init__(self, cfg=None):
        self.cfg = cfg

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        prompt = static + dynamic
        # E11-1 / E11-4: the egress gate (refused under [privacy] local_only; web search also needs [privacy] web_enrich = true)
        egress.allow("llm.claude-code", {"purpose": journal_purpose(purpose), "bytes": len(prompt.encode("utf-8")),
                                         "web_search": bool(web_search)}, cfg=self.cfg)
        t0 = time.monotonic()
        cfg = egress.policy_of(self.cfg).cfg          # the activated or loaded configuration the egress gate above used
        cmd, timeout = claude_classify_command(cfg, model, schema, web_search)
        # the same minimal environment as the coach runtime: never COACH_DB_KEY, COACH_BACKUP_KEY, ANTHROPIC_API_KEY ...
        env = claude_env(cfg)
        # ... and the same isolation: an EMPTY temporary directory outside the repository, so no CLAUDE.md, .claude/ or memory file
        # of the current directory is added to the prompt (the scheduled job runs from the project root)
        run_dir = tempfile.mkdtemp(prefix="coach-classify-")
        try:
            out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=run_dir)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        res = json.loads(out.stdout) if out.stdout.strip().startswith("{") else {}
        if out.returncode != 0 or res.get("is_error"):
            diag = safe_diagnostic(out.stderr, prompt)
            raise RuntimeError(f"claude -p failed (exit {out.returncode}"
                               + (f", {res.get('subtype')}" if res.get("subtype") else "") + ")"
                               + (f": {diag}" if diag else ""))
        u = res.get("usage") or {}
        try:
            data = res.get("structured_output") or json.loads(res["result"])
        except Exception as e:                                    # noqa: BLE001
            raise LLMError(f"claude -p returned an unusable answer ({type(e).__name__})", Usage(
                self.name, model, purpose, items, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                0, 0, float(res.get("total_cost_usd") or 0), True, round(time.monotonic() - t0, 2))) from e
        usage = Usage(self.name, model, purpose, items, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                      int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
                      float(res.get("total_cost_usd") or 0), True, round(time.monotonic() - t0, 2))
        return data, usage


# ---------------------------------------------------------------- Anthropic API

class AnthropicBackend(LLMBackend):
    name = "anthropic-api"

    OFFICIAL_URL = "https://api.anthropic.com"

    def __init__(self, api_key: str | None = None, client=None, use_batch: bool = False, max_tokens: int = 16000,
                 sleep=time.sleep, poll_seconds: float = 30.0, max_wait_seconds: float = 1800,
                 base_url: str | None = None, cfg=None):
        self.cfg = cfg
        self._key, self._client = api_key, client
        self.base_url = base_url or self.OFFICIAL_URL      # pinned: ANTHROPIC_BASE_URL in the environment is ignored
        self.use_batch, self.max_tokens = use_batch, max_tokens
        self.sleep, self.poll_seconds, self.max_wait = sleep, poll_seconds, max_wait_seconds

    @property
    def raw_client(self):
        """The SDK client WITHOUT the egress gate: only after a gate was passed (see :meth:`gated_client`)."""
        if self._client is None:
            import anthropic
            from coach import secrets
            key = self._key or secrets.get_secret("anthropic_api_key")
            self._client = anthropic.Anthropic(api_key=key, base_url=self.base_url)
        return self._client

    def gated_client(self, purpose: str = "api", nbytes: int = 0):
        """The SDK client, after the egress check (E11-1: refused under [privacy] local_only) and a journal row."""
        egress.allow("llm.anthropic-api", {"host": egress.host_of(self.base_url), "purpose": purpose, "bytes": nbytes}, cfg=self.cfg)
        return self.raw_client

    @property
    def client(self):
        return self.gated_client()

    @staticmethod
    def model_id(model: str) -> str:
        return ALIASES.get(model, model)

    def _params(self, static, dynamic, schema, model) -> dict:
        return dict(
            model=self.model_id(model), max_tokens=self.max_tokens,
            # the static part (instructions + taxonomy) is a cacheable prefix shared by every call
            system=[{"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": dynamic}],
            output_config={"format": {"type": "json_schema", "schema": with_additional_properties_false(schema)}})

    @staticmethod
    def _parse(msg) -> dict:
        if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
            raise RuntimeError(f"model stopped with {msg.stop_reason}")
        text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
        return json.loads(text)

    @staticmethod
    def _usage(msg, model, purpose, items, duration, batch=False) -> Usage:
        u = msg.usage
        tin, tout = int(u.input_tokens or 0), int(u.output_tokens or 0)
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        return Usage("anthropic-api", model, purpose, items, tin, tout, cr, cw,
                     estimate_cost(model, tin, tout, cr, cw, batch), True, round(duration, 2))

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        if web_search:
            raise NotImplementedError("web enrichment needs the claude-code backend (WebSearch tool)")
        mid = self.model_id(model)
        t0 = time.monotonic()
        msg = self.gated_client(journal_purpose(purpose), len(static) + len(dynamic)).messages.create(
            **self._params(static, dynamic, schema, mid))
        usage = self._usage(msg, mid, purpose, items, time.monotonic() - t0)
        try:
            return self._parse(msg), usage
        except Exception as e:                                    # noqa: BLE001  (refusal, max_tokens, bad JSON)
            raise LLMError(str(e), usage) from e

    # -- Message Batches: submit / wait / fetch (a run is resumable from the saved batch id)

    def submit(self, jobs) -> str:
        reqs = [{"custom_id": f"job{i}", "params": self._params(
            j["static"], j["dynamic"], j["schema"], self.model_id(j["model"]))} for i, j in enumerate(jobs)]
        n = sum(len(j["static"]) + len(j["dynamic"]) for j in jobs)
        return self.gated_client("classify.batch", n).messages.batches.create(requests=reqs).id

    def list_recent_batches(self, limit: int = 20) -> list[dict]:
        """[{id, created_at, requests}] of the latest batches of this account (to find one whose id was lost)."""
        out = []
        for b in self.gated_client("classify.batch_list").messages.batches.list(limit=limit):
            rc, bid = getattr(b, "request_counts", None), getattr(b, "id", None)
            if not bid or rc is None:             # an entry we cannot compare with: skipped, not guessed at
                print(f"  warning: skipping a batch in the provider's listing without id or request counts "
                      f"({bid or 'no id'})")
                continue
            n = sum(int(getattr(rc, k, 0) or 0) for k in ("processing", "succeeded", "errored", "canceled", "expired"))
            out.append({"id": bid, "created_at": str(getattr(b, "created_at", "")), "requests": n})
        return out

    def wait(self, batch_id: str, max_wait: float | None = None) -> None:
        """Returns when the batch has ended. BatchPending after `max_wait` seconds (0 = look once);
        BatchMissing if the provider does not know the id (404)."""
        limit = self.max_wait if max_wait is None else max_wait
        waited = 0.0
        client = self.gated_client("classify.batch_wait")        # one journal row for the whole wait, not one per poll
        while True:
            try:
                status = client.messages.batches.retrieve(batch_id).processing_status
            except Exception as e:                                # noqa: BLE001
                if getattr(e, "status_code", None) == 404 or type(e).__name__ == "NotFoundError":
                    raise BatchMissing(f"batch {batch_id} not found at the provider (404)") from e
                raise
            if status == "ended":
                return
            if waited >= limit:
                raise BatchPending(f"batch {batch_id} is still running at Anthropic: run the same command again to "
                                   "collect it (nothing is paid twice)")
            self.sleep(self.poll_seconds)
            waited += self.poll_seconds

    def fetch(self, batch_id: str, jobs_meta: dict[str, dict]) -> dict[str, Outcome]:
        """custom_id -> Outcome for the ids in `jobs_meta` ({custom_id: {model, purpose, items}}); ids we never
        sent are ignored, results arrive in any order."""
        out: dict[str, Outcome] = {}
        for r in self.gated_client("classify.batch_fetch").messages.batches.results(batch_id):
            cid = str(getattr(r, "custom_id", ""))
            meta = jobs_meta.get(cid)
            if meta is None:
                continue
            if r.result.type != "succeeded":
                out[cid] = Outcome(error=f"batch request {r.result.type}")
                continue
            msg = r.result.message
            usage = None
            try:
                usage = self._usage(msg, self.model_id(meta["model"]), meta.get("purpose", "label"),
                                    meta.get("items", 0), 0.0, batch=True)
            except Exception:                                     # noqa: BLE001
                pass
            try:
                out[cid] = Outcome(data=self._parse(msg), usage=usage)
            except Exception as e:                                # noqa: BLE001  (max_tokens, refusal, bad JSON)
                out[cid] = Outcome(usage=usage, error=str(e)[:200])
        return out

    def complete_many(self, jobs, resume_id=None, on_created=None):
        if not self.use_batch or len(jobs) < 2 or any(j.get("web_search") for j in jobs):
            return super().complete_many(jobs)
        batch_id = resume_id
        if not batch_id:
            batch_id = self.submit(jobs)
            if on_created:
                on_created(batch_id)          # saved before waiting: a crash or timeout can be resumed
        self.wait(batch_id)
        meta = {f"job{i}": j for i, j in enumerate(jobs)}
        got = self.fetch(batch_id, meta)
        out: list = []
        for i in range(len(jobs)):
            oc = got.get(f"job{i}")
            if oc is None:
                out.append(RuntimeError("no result returned for this job"))
            elif oc.error or oc.data is None:
                out.append(RuntimeError(oc.error or "empty result"))
            else:
                out.append((oc.data, oc.usage))
        return out


def check_ollama_url(url: str, allow_remote: bool = False) -> None:
    """Prompts contain bank data: the server must be on this machine unless the user opted in explicitly."""
    import ipaddress
    from urllib.parse import urlparse
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        raise ValueError(f"ollama_url {url!r} is not an http(s) URL")
    if allow_remote:
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"            # *.localhost names can be mapped elsewhere: refused
    if not loopback:
        raise ValueError(f"ollama_url {url!r} is not a loopback address: your bank data would leave this machine. "
                         "Set [llm] ollama_allow_remote = true to allow it knowingly.")


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 600.0, allow_remote: bool = False, cfg=None):
        self.cfg = cfg
        check_ollama_url(base_url, allow_remote)
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        if web_search:
            raise NotImplementedError("web enrichment needs the claude-code backend (WebSearch tool)")
        t0 = time.monotonic()
        egress.allow("llm.ollama", {"host": egress.host_of(self.base_url), "purpose": journal_purpose(purpose),
                                    "bytes": len(static) + len(dynamic)}, cfg=self.cfg)
        r = requests.post(f"{self.base_url}/api/chat", timeout=self.timeout, json={
            "model": model, "stream": False, "format": schema, "options": {"temperature": 0},
            "messages": [{"role": "system", "content": static}, {"role": "user", "content": dynamic}]})
        r.raise_for_status()
        body = r.json()
        data = json.loads(body["message"]["content"])
        usage = Usage(self.name, model, purpose, items, int(body.get("prompt_eval_count") or 0),
                      int(body.get("eval_count") or 0), 0, 0, 0.0, False, round(time.monotonic() - t0, 2))
        return data, usage


# ---------------------------------------------------------------- OpenAI-compatible (OpenRouter, Eden AI, vLLM ...)

def check_openai_url(url: str) -> None:
    """The base url of an OpenAI-compatible provider: https, or plain http only on this machine (a local vLLM / LM Studio)."""
    from urllib.parse import urlparse
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    if not host or u.scheme not in ("http", "https"):
        raise ValueError(f"openai_base_url {url!r} is not an http(s) URL (for example https://openrouter.ai/api/v1)")
    if u.scheme == "http" and not egress._loopback(host):
        raise ValueError(f"openai_base_url {url!r} must use https: your redacted bank data would cross the network in clear text")
    if u.query or u.fragment or u.username or u.password:
        raise ValueError("openai_base_url must not carry a query, a fragment or credentials (the key is the openai_api_key secret)")


def _strict_ok(schema) -> bool:
    """OpenAI's strict structured output needs every property of every object listed in `required`."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" and set(schema.get("properties") or {}) - set(schema.get("required") or []):
            return False
        return all(_strict_ok(v) for v in schema.values())
    if isinstance(schema, list):
        return all(_strict_ok(v) for v in schema)
    return True


def parse_json_text(text: str) -> dict:
    """The JSON object of a model answer; tolerates a ```json fence around it (json_object mode on some models)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return json.loads(t)


class OpenAICompatBackend(LLMBackend):
    name = "openai-compatible"

    def __init__(self, base_url: str = "https://openrouter.ai/api/v1", api_key: str | None = None, timeout: float = 600.0,
                 deny_data_collection: bool = True, post=None, cfg=None):
        self.cfg = cfg
        check_openai_url(base_url)
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
        self._key, self._post = api_key, post
        self.deny_data_collection = deny_data_collection

    @property
    def host(self) -> str:
        return egress.host_of(self.base_url)

    def _headers(self) -> dict:
        key = self._key
        if key is None:
            from coach import secrets
            key = secrets.get_secret("openai_api_key", required=False)
        if not key:
            raise RuntimeError("no API key for the openai-compatible backend: run `coach config set-secret openai_api_key`")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def provider_options(self) -> dict:
        """OpenRouter only: route to upstream providers that do not store or train on the prompts ([llm] openrouter_deny_data_collection)."""
        if self.deny_data_collection and self.host == "openrouter.ai":
            return {"provider": {"data_collection": "deny"}}
        return {}

    def chat(self, body: dict, purpose: str, timeout: float | None = None):
        """POST /chat/completions after the egress gate. Returns the raw response (status_code, json(), text)."""
        payload = {**body, **self.provider_options()}
        egress.allow("llm.openai-compatible", {"host": self.host, "purpose": purpose,
                                               "bytes": len(json.dumps(payload, default=str))}, cfg=self.cfg)
        post = self._post or requests.post
        return post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers(), timeout=timeout or self.timeout)

    @staticmethod
    def usage_of(body: dict) -> tuple[int, int, int, float | None]:
        """(tokens_in, tokens_out, cached_in, provider-reported USD cost or None)."""
        u = body.get("usage") or {}
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        cost = u.get("cost")
        return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cached, \
            (float(cost) if isinstance(cost, (int, float)) else None)

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        if web_search:
            raise NotImplementedError("web enrichment needs the claude-code backend (WebSearch tool)")
        t0 = time.monotonic()
        s = with_additional_properties_false(schema)
        msgs = [{"role": "system", "content": static}, {"role": "user", "content": dynamic}]
        body = {"model": model, "temperature": 0, "messages": msgs,
                "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": _strict_ok(s), "schema": s}}}
        r = self.chat(body, journal_purpose(purpose))
        if r.status_code == 400 and any(w in (r.text or "") for w in ("response_format", "json_schema", "structured")):
            # a model without JSON-schema output: JSON mode, with the schema spelled out in the instructions
            body["response_format"] = {"type": "json_object"}
            body["messages"] = [{"role": "system", "content": static + "\n\nAnswer with ONE JSON object matching this JSON schema:\n"
                                 + json.dumps(s)}, msgs[1]]
            r = self.chat(body, journal_purpose(purpose))
        if r.status_code >= 400:
            raise RuntimeError(f"{self.host} answered HTTP {r.status_code}: {safe_diagnostic(r.text, static + dynamic)}")
        res = r.json()
        tin, tout, cached, cost = self.usage_of(res)
        usage = Usage(self.name, model, purpose, items, tin, tout, cached, 0, cost, cost is None, round(time.monotonic() - t0, 2))
        choice = (res.get("choices") or [{}])[0]
        try:
            if choice.get("finish_reason") in ("length", "content_filter"):
                raise RuntimeError(f"model stopped with {choice['finish_reason']}")
            return parse_json_text((choice.get("message") or {}).get("content") or ""), usage
        except Exception as e:                                    # noqa: BLE001  (truncation, refusal, bad JSON)
            raise LLMError(str(e)[:200], usage) from e


def get_backend(cfg=None, name: str | None = None) -> LLMBackend:
    name = name or (cfg.llm_backend if cfg else "claude-code")
    if cfg is not None and name in BACKENDS:      # E11-4: a configured backend the privacy mode forbids is refused up front
        host = egress.host_of(getattr(cfg, "llm_ollama_url", "")) if name == "ollama" else (
            egress.host_of(getattr(cfg, "llm_openai_base_url", "")) if name == "openai-compatible" else "")
        egress.require(f"llm.{name}", {"host": host, "purpose": "classify.backend"}, cfg=cfg)
    if name == "claude-code":
        return ClaudeCodeBackend(cfg=cfg)
    if name == "anthropic-api":
        return AnthropicBackend(use_batch=bool(getattr(cfg, "llm_batch_api", False)),
                                base_url=getattr(cfg, "llm_anthropic_base_url", None), cfg=cfg)
    if name == "ollama":
        return OllamaBackend(getattr(cfg, "llm_ollama_url", "http://localhost:11434"),
                             allow_remote=bool(getattr(cfg, "llm_ollama_allow_remote", False)), cfg=cfg)
    if name == "openai-compatible":
        return OpenAICompatBackend(getattr(cfg, "llm_openai_base_url", "https://openrouter.ai/api/v1"),
                                   deny_data_collection=bool(getattr(cfg, "llm_openrouter_deny_data_collection", True)), cfg=cfg)
    raise ValueError(f"unknown llm backend {name!r}; allowed: {', '.join(BACKENDS)}")


def default_model(cfg, backend: LLMBackend) -> str:
    if backend.name == "anthropic-api":
        return getattr(cfg, "llm_anthropic_model", "claude-haiku-4-5")
    if backend.name == "ollama":
        return getattr(cfg, "llm_ollama_model", "llama3.1")
    if backend.name == "openai-compatible":
        return getattr(cfg, "llm_openai_model", "")
    return cfg.llm_model


def record_usage(con, usage: Usage) -> bool:
    """Log one call. A batch result carries `ref` (batch_id:custom_id): it is stored once, whoever collects it; returns
    False when that result was already counted."""
    from coach.db import now_iso
    cur = con.execute("""INSERT OR IGNORE INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out,
                   cache_read_tokens, cache_write_tokens, cost_usd, cost_is_estimate, duration_s, batch_ref)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (now_iso(), usage.backend, usage.model, usage.purpose, usage.items, usage.tokens_in,
                       usage.tokens_out, usage.cache_read_tokens, usage.cache_write_tokens, usage.cost_usd,
                       int(usage.cost_is_estimate), usage.duration_s, usage.ref))
    return cur.rowcount > 0
