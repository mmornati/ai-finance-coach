"""E2-6: pluggable LLM backends (all mocked: no network, no subprocess), usage log, redaction, person guard."""
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from coach.classify import backends, commands as cc
from coach.classify.backends import (AnthropicBackend, ClaudeCodeBackend, OllamaBackend, estimate_cost, get_backend,
                                     with_additional_properties_false)
from coach.classify.llm import LABEL_SCHEMA, label_keys, label_prompt, llm_label
from coach.classify.redact import redact, redact_item
from coach.config import ConfigError, load_config
from coach.db import connect
from helpers import add_bank, add_tx


# ---------------------------------------------------------------- redaction

@pytest.mark.parametrize("raw,must_not_contain", [
    ("PAIEMENT FR7630006000011234567890189 ACME", "FR76300060"),
    ("VIR ref IT60X0542811101000000123456", "IT60X05428"),
    ("CONTACT jean.dupont@example.org SHOP", "jean.dupont"),
    ("SHOP TEL 06 12 34 56 78 PARIS", "12 34 56"),
    ("SHOP +33 6 12 34 56 78", "12 34 56"),
    ("SHOP +39 333 1234567", "1234567"),
    ("MANDAT 123456789012 ACME", "123456789012"),
    ("REF 92235041-baaa-4443-8ea0-e39ca9973e5c WERO", "92235041"),
    ("TXN 8aa4ba2e10914c5da6a40c1ef0cc0022 SHOP", "8aa4ba2e1091"),
])
def test_redact_removes_identifiers(raw, must_not_contain):
    out = redact(raw)
    assert must_not_contain not in out, out
    assert "[" in out


def test_redact_keeps_ordinary_merchant_text_and_masks_household_names():
    assert redact("MC DONALDS QUIMPER 3") == "MC DONALDS QUIMPER 3"
    assert redact("CARTE 12 BOULANGERIE PAUL") == "CARTE 12 BOULANGERIE PAUL"
    assert redact("VIR DURAND PAUL ACME", names={"DURAND", "PAUL"}) == "VIR [NAME] [NAME] ACME"
    assert redact(None) is None and redact("") == ""


def test_redact_item_covers_every_text_field_including_similar():
    item = {"id": 1, "key": "SHOP a@b.co", "raw_example": "IBAN FR7630006000011234567890189", "n": 2,
            "similar": [{"key": "X 0612345678", "name": "Y", "category": "a.b"}]}
    out = redact_item(item)
    flat = json.dumps(out)
    assert "a@b.co" not in flat and "FR76300060" not in flat and "0612345678" not in flat
    assert out["n"] == 2 and out["id"] == 1 and item["key"] == "SHOP a@b.co"      # original untouched


# ---------------------------------------------------------------- fixtures

def results_json(n=1, category="food.groceries"):
    return {"results": [{"id": i, "merchant": f"Shop {i}", "category": category, "confidence": 0.9,
                         "recurring_hint": False} for i in range(n)]}


class FakeMessages:
    def __init__(self, payloads, stop_reason="end_turn"):
        self.payloads, self.calls, self.stop_reason = list(payloads), [], stop_reason
        self.batches = FakeBatches(self)

    def create(self, **kw):
        self.calls.append(kw)
        return self.message(self.payloads.pop(0))

    def message(self, payload):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload))], stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=3000,
                                  cache_creation_input_tokens=0))


class FakeBatches:
    def __init__(self, parent):
        self.parent, self.created, self.polls = parent, [], 0

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id="batch_1")

    def retrieve(self, bid):
        self.polls += 1
        return SimpleNamespace(processing_status="ended" if self.polls >= 3 else "in_progress")

    def results(self, bid):
        reqs = self.created[0]
        out = [SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(
            type="succeeded", message=self.parent.message(self.parent.payloads[int(r["custom_id"][3:])])))
            for r in reqs]
        return reversed(out)          # results arrive in any order


class FakeClient:
    def __init__(self, payloads, stop_reason="end_turn"):
        self.messages = FakeMessages(payloads, stop_reason)


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s", "Fortuneo", "FR", [("a", "FR76", "M OU MME DURAND PAUL")])
    return c


def seed(con, names):
    for i, n in enumerate(names):
        add_tx(con, "a", f"a:{i}", f"2026-09-{i + 1:02d}", -10.0 - i, f"CARTE 01/09 {n}", "card")
        con.execute("UPDATE tx_enriched SET merchant_raw=?, merchant_key=? WHERE tx_key=?", (n, n, f"a:{i}"))
    con.commit()


# ---------------------------------------------------------------- the three backends

def test_claude_code_backend_command_prompt_and_usage(monkeypatch):
    seen = {}

    def fake_run(cmd, input, capture_output, text, timeout, env=None):
        seen.update(cmd=cmd, prompt=input, env=env)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"structured_output": results_json(1), "total_cost_usd": 0.0123,
             "usage": {"input_tokens": 500, "output_tokens": 60, "cache_read_input_tokens": 10}}))
    monkeypatch.setattr("coach.classify.backends.subprocess.run", fake_run)
    res, cost = llm_label([{"id": 0, "key": "SHOP"}], "haiku", [("K", "N", "a.b")], backend=ClaudeCodeBackend())
    assert res[0]["category"] == "food.groceries" and cost == 0.0123
    assert seen["cmd"][:5] == ["claude", "-p", "--model", "haiku", "--output-format"]
    assert "--json-schema" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == ""
    static, dynamic = label_prompt([{"id": 0, "key": "SHOP"}], [("K", "N", "a.b")])
    assert seen["prompt"] == static + dynamic                      # the prompt is the two parts, unchanged
    u = llm_label.last_usage
    assert (u.backend, u.tokens_in, u.tokens_out, u.cache_read_tokens) == ("claude-code", 500, 60, 10)


def test_claude_code_error_is_raised(monkeypatch):
    monkeypatch.setattr("coach.classify.backends.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError, match="boom"):
        ClaudeCodeBackend().complete("s", "d", LABEL_SCHEMA, "sonnet")


def test_anthropic_backend_request_shape_caching_and_cost():
    fake = FakeClient([results_json(2)])
    b = AnthropicBackend(client=fake)
    data, usage = b.complete("STATIC PART", "DYNAMIC PART", LABEL_SCHEMA, "haiku", "label", 2)
    assert len(data["results"]) == 2
    kw = fake.messages.calls[0]
    assert kw["model"] == "claude-haiku-4-5"                                  # alias resolved
    assert kw["system"] == [{"type": "text", "text": "STATIC PART", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "DYNAMIC PART"}]
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["results"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in LABEL_SCHEMA                          # the shared schema is not mutated
    assert "tool_choice" not in kw and "temperature" not in kw
    assert (usage.tokens_in, usage.tokens_out, usage.cache_read_tokens) == (1000, 200, 3000)
    assert usage.cost_usd == estimate_cost("claude-haiku-4-5", 1000, 200, 3000, 0) == pytest.approx(
        (1000 * 1 + 3000 * 0.1 + 200 * 5) / 1e6)
    assert usage.cost_is_estimate is True


def test_anthropic_backend_refusal_and_truncation_raise():
    for reason in ("refusal", "max_tokens"):
        with pytest.raises(RuntimeError, match=reason):
            AnthropicBackend(client=FakeClient([results_json()], stop_reason=reason)).complete(
                "s", "d", LABEL_SCHEMA, "haiku")


def test_anthropic_backend_reads_the_key_from_the_secret_store(monkeypatch, fake_keyring):
    from coach import secrets
    secrets.set_secret("anthropic_api_key", "sk-test-not-real")
    made = {}

    class FakeAnthropic:
        def __init__(self, api_key=None, base_url=None):
            made["key"], made["url"] = api_key, base_url
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    assert AnthropicBackend().client is not None and made["key"] == "sk-test-not-real"
    assert made["url"] == "https://api.anthropic.com"          # the environment cannot redirect the key


def test_anthropic_batch_api_one_batch_results_keyed_by_custom_id(monkeypatch):
    fake = FakeClient([results_json(1, "food.groceries"), results_json(1, "health.pharmacy"), results_json(1, "travel.flights")])
    sleeps = []
    b = AnthropicBackend(client=fake, use_batch=True, sleep=sleeps.append, poll_seconds=5)
    jobs = [dict(static="S", dynamic=f"D{i}", schema=LABEL_SCHEMA, model="haiku", purpose="label", items=1)
            for i in range(3)]
    out = b.complete_many(jobs)
    assert [o[0]["results"][0]["category"] for o in out] == ["food.groceries", "health.pharmacy", "travel.flights"]
    assert len(fake.messages.batches.created) == 1 and len(fake.messages.batches.created[0]) == 3
    assert fake.messages.calls == []                       # no synchronous call
    assert sleeps == [5, 5]                                # polled until ended
    assert out[0][1].cost_usd == pytest.approx(estimate_cost("claude-haiku-4-5", 1000, 200, 3000, 0, batch=True))
    assert out[0][1].cost_usd < estimate_cost("claude-haiku-4-5", 1000, 200, 3000, 0)


def test_ollama_backend_http_contract(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, body=json, timeout=timeout)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "message": {"content": __import__("json").dumps(results_json(1))},
            "prompt_eval_count": 321, "eval_count": 45})
    monkeypatch.setattr("coach.classify.backends.requests.post", fake_post)
    b = OllamaBackend("http://llm.local:11434/", allow_remote=True)
    data, usage = b.complete("STATIC", "DYNAMIC", LABEL_SCHEMA, "llama3.1", "label", 1)
    assert sent["url"] == "http://llm.local:11434/api/chat"
    assert sent["body"]["format"] == LABEL_SCHEMA and sent["body"]["stream"] is False
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user"]
    assert (usage.backend, usage.tokens_in, usage.tokens_out, usage.cost_usd) == ("ollama", 321, 45, 0.0)
    assert data["results"][0]["merchant"] == "Shop 0"


def test_web_search_is_claude_code_only():
    for b in (AnthropicBackend(client=FakeClient([])), OllamaBackend()):
        assert b.supports_web_search is False
        with pytest.raises(NotImplementedError):
            b.complete("s", "d", {}, "m", web_search=True)
    assert ClaudeCodeBackend().supports_web_search


def test_get_backend_and_config(tmp_path):
    assert isinstance(get_backend(None, "claude-code"), ClaudeCodeBackend)
    assert isinstance(get_backend(None, "ollama"), OllamaBackend)
    assert isinstance(get_backend(None, "anthropic-api"), AnthropicBackend)
    with pytest.raises(ValueError):
        get_backend(None, "gpt")
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nbackend = "anthropic-api"\nanthropic_model = "claude-sonnet-5-5"\nbatch_api = true\n'
                 '[classify]\nknn_threshold = 0.95\n')
    cfg = load_config(p, env={})
    assert (cfg.llm_backend, cfg.llm_anthropic_model, cfg.llm_batch_api, cfg.knn_threshold) == (
        "anthropic-api", "claude-sonnet-5-5", True, 0.95)
    assert get_backend(cfg).use_batch is True
    p.write_text('[llm]\nbackend = "openai"\n')
    with pytest.raises(ConfigError):
        load_config(p, env={})
    p.write_text('[classify]\nknn_threshold = 0\n')
    with pytest.raises(ConfigError):
        load_config(p, env={})


def test_with_additional_properties_false_is_deep_and_non_mutating():
    s = {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "object", "properties": {}}}}}
    out = with_additional_properties_false(s)
    assert out["additionalProperties"] is False and out["properties"]["a"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in s


# ---------------------------------------------------------------- label_keys + usage log + redaction in the prompt

class Recorder(backends.LLMBackend):
    name = "recorder"

    def __init__(self):
        self.calls = []

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        self.calls.append((static, dynamic))
        ids = [i["id"] for i in json.loads(dynamic.split("avg_amount in EUR):\n")[1])]
        return {"results": [{"id": i, "merchant": "M", "category": "food.groceries", "confidence": 0.9,
                             "recurring_hint": False} for i in ids]}, backends.Usage(
            "recorder", model, purpose, items, 10, 5, 0, 0, 0.001, True, 0.1)


def test_label_keys_logs_usage_and_redacts_every_item(con):
    seed(con, ["SHOP A 0612345678", "BOULANGERIE FR7630006000011234567890189", "DURAND PAUL TRAITEUR"])
    rec = Recorder()
    res, cost = label_keys(con, ["SHOP A 0612345678", "BOULANGERIE FR7630006000011234567890189"], "m", 1, 2,
                           backend=rec, names={"DURAND", "PAUL"})
    assert len(res) == 2 and cost == pytest.approx(0.002)
    sent = " ".join(d for _, d in rec.calls)
    assert "0612345678" not in sent and "FR7630006000011234567890189" not in sent and "[PHONE]" in sent
    rows = con.execute("SELECT backend, model, purpose, items, tokens_in, tokens_out, cost_usd FROM llm_usage").fetchall()
    assert rows == [("recorder", "m", "label", 1, 10, 5, 0.001)] * 2
    # the static (cacheable) prompt part is identical for every call
    assert len({s for s, _ in rec.calls}) == 1


def test_cmd_run_with_each_backend_sends_no_person_names_and_logs_usage(cfg, con, monkeypatch):
    # a person-looking transfer, a doctor's direct debit and the household surname never leave the machine
    add_tx(con, "a", "t1", "2026-09-20", -50.0, "VIR INST VASSEUR T", "transfer_out")
    add_tx(con, "a", "t2", "2026-09-21", -60.0, "PRLV Dr HALBERT CLAIRE", "direct_debit")
    add_tx(con, "a", "t3", "2026-09-22", -70.0, "VIR FAC1 DURAND", "transfer_out")
    for k, raw in (("t1", "VASSEUR T"), ("t2", "DR HALBERT CLAIRE"), ("t3", "FAC DURAND")):
        con.execute("UPDATE tx_enriched SET merchant_raw=?, merchant_key=? WHERE tx_key=?", (raw, raw, k))
    seed(con, ["LIDL QUIMPER", "ACME ENERGIE"])
    con.commit()
    for name, make in (("claude-code", lambda: ClaudeCodeBackend()),
                       ("anthropic-api", lambda: AnthropicBackend(client=FakeClient(
                           [results_json(2), results_json(2)]))),
                       ("ollama", lambda: OllamaBackend())):
        con.execute("DELETE FROM merchants")
        con.execute("DELETE FROM llm_usage")
        con.commit()
        prompts = []
        be = make()
        if name == "claude-code":
            monkeypatch.setattr("coach.classify.backends.subprocess.run", lambda cmd, input, **k: (
                prompts.append(input), SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                    {"structured_output": results_json(2), "total_cost_usd": 0.01})))[1])
        elif name == "ollama":
            monkeypatch.setattr("coach.classify.backends.requests.post", lambda url, json=None, timeout=None: (
                prompts.append(json["messages"][1]["content"]), SimpleNamespace(
                    raise_for_status=lambda: None, json=lambda: {"message": {"content": __import__("json").dumps(
                        results_json(2))}, "prompt_eval_count": 5, "eval_count": 5}))[1])
        monkeypatch.setattr(cc, "get_backend", lambda cfg_, be=be: be)
        n = cc.cmd_run(Namespace(insecure=True, model="haiku", batch=50, workers=1, limit=None, refresh=False,
                                 include_ruled=False, no_knn=True), cfg)
        assert n == 2, name
        if name == "anthropic-api":
            prompts.append(be.client.messages.calls[0]["messages"][0]["content"])
        sent = " ".join(prompts).upper()
        for secret in ("VASSEUR", "HALBERT", "DURAND"):
            assert secret not in sent, (name, secret)
        assert "LIDL QUIMPER" in sent and "ACME ENERGIE" in sent
        assert con.execute("SELECT COUNT(*) FROM llm_usage WHERE backend=?", (be.name,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM merchants WHERE source='llm'").fetchone()[0] == 2


def test_enrich_refuses_a_backend_without_web_search(cfg, con, monkeypatch, capsys):
    monkeypatch.setattr(cc, "get_backend", lambda cfg_: OllamaBackend())
    cfg.privacy_web_enrich = True                       # E11-1: enrichment is opt-in; this test is about the backend
    with pytest.raises(SystemExit) as e:
        cc.cmd_enrich(Namespace(insecure=True, model=None, max_conf=0.7, limit=5, batch=5, workers=1), cfg)
    assert "claude-code" in str(e.value)


# ---------------------------------------------------------------- openai-compatible (OpenRouter, Eden AI, vLLM ...)

class OAResp:
    def __init__(self, body, status=200, text=""):
        self._b, self.status_code, self.text = body, status, text

    def json(self):
        return self._b


def oa_answer(content, usage=None, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20}}


def test_openai_compatible_http_contract_and_provider_cost():
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "body": json, "headers": headers})
        return OAResp(oa_answer(__import__("json").dumps(results_json(1)),
                                {"prompt_tokens": 300, "completion_tokens": 40, "cost": 0.0012,
                                 "prompt_tokens_details": {"cached_tokens": 200}}))
    b = backends.OpenAICompatBackend("https://openrouter.ai/api/v1/", api_key="test-key", post=post)
    data, usage = b.complete("STATIC", "DYNAMIC", LABEL_SCHEMA, "anthropic/claude-haiku-4.5", "label", 1)
    s = sent[0]
    assert s["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert s["headers"]["Authorization"] == "Bearer test-key"
    assert [m["role"] for m in s["body"]["messages"]] == ["system", "user"]
    rf = s["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == with_additional_properties_false(LABEL_SCHEMA)
    assert s["body"]["provider"] == {"data_collection": "deny"}               # OpenRouter: no provider that stores prompts
    assert data["results"][0]["merchant"] == "Shop 0"
    assert (usage.backend, usage.tokens_in, usage.tokens_out, usage.cache_read_tokens) == ("openai-compatible", 300, 40, 200)
    assert usage.cost_usd == 0.0012 and usage.cost_is_estimate is False       # reported by the provider, not guessed


def test_openai_compatible_other_hosts_get_no_openrouter_options_and_unknown_cost():
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return OAResp(oa_answer("```json\n" + __import__("json").dumps(results_json(1)) + "\n```"))
    b = backends.OpenAICompatBackend("https://llm.example.test/v1", api_key="k", post=post)
    data, usage = b.complete("S", "D", LABEL_SCHEMA, "some/model", "label", 1)
    assert "provider" not in sent[0] and data["results"][0]["id"] == 0      # a ```json fence is tolerated
    assert usage.cost_usd is None and usage.cost_is_estimate is True         # unknown, never shown as 0


def test_openai_compatible_falls_back_to_json_mode_when_the_model_has_no_schema_output():
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(__import__("copy").deepcopy(json))
        if json["response_format"]["type"] == "json_schema":
            return OAResp({}, 400, '{"error": "response_format json_schema is not supported by this model"}')
        return OAResp(oa_answer(__import__("json").dumps(results_json(1))))
    b = backends.OpenAICompatBackend("https://llm.example.test/v1", api_key="k", post=post)
    data, _ = b.complete("S", "D", LABEL_SCHEMA, "m", "label", 1)
    assert [x["response_format"]["type"] for x in sent] == ["json_schema", "json_object"]
    assert "JSON schema" in sent[1]["messages"][0]["content"] and data["results"]


def test_openai_compatible_truncated_answer_is_a_billed_error():
    b = backends.OpenAICompatBackend("https://llm.example.test/v1", api_key="k",
                                     post=lambda *a, **k: OAResp(oa_answer('{"results": [', finish="length")))
    with pytest.raises(backends.LLMError) as e:
        b.complete("S", "D", LABEL_SCHEMA, "m", "label", 1)
    assert e.value.usage.tokens_in == 100


@pytest.mark.parametrize("url", ["http://openrouter.ai/api/v1", "ftp://x.test/v1", "https://k:s@x.test/v1", "https://x.test/v1?key=1", ""])
def test_openai_compatible_rejects_unsafe_base_urls(url):
    with pytest.raises(ValueError):
        backends.check_openai_url(url)
    backends.check_openai_url("http://localhost:8000/v1")                   # a local vLLM / LM Studio may use plain http


def test_openai_compatible_web_search_is_refused():
    with pytest.raises(NotImplementedError):
        backends.OpenAICompatBackend(api_key="k").complete("s", "d", {}, "m", web_search=True)


def test_openai_compatible_config(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nbackend = "openai-compatible"\nopenai_base_url = "https://api.edenai.test/v3/llm"\n'
                 'openai_model = "openai/gpt-4o-mini"\n[coach]\nbackend = "openai-compatible"\nmodel = "anthropic/claude-sonnet-4.5"\n')
    cfg = load_config(p, env={})
    b = get_backend(cfg)
    assert isinstance(b, backends.OpenAICompatBackend) and b.base_url == "https://api.edenai.test/v3/llm"
    assert backends.default_model(cfg, b) == "openai/gpt-4o-mini" and cfg.coach_model_effective == "anthropic/claude-sonnet-4.5"
    p.write_text('[llm]\nbackend = "openai-compatible"\n')                    # no model: refused with a clear message
    with pytest.raises(ConfigError, match="openai_model"):
        load_config(p, env={})
    p.write_text('[llm]\nbackend = "openai-compatible"\nopenai_model = "m"\nopenai_base_url = "http://openrouter.ai/api/v1"\n')
    with pytest.raises(ConfigError, match="https"):
        load_config(p, env={})


def test_openai_secret_is_not_read_from_the_generic_openai_variable():
    from coach import secrets
    assert secrets.SECRETS["openai_api_key"] == "COACH_OPENAI_API_KEY"
    assert secrets.lookup("openai_api_key", {"OPENAI_API_KEY": "sk-unrelated"})[0] is None


def test_claude_code_backend_logs_in_with_the_oauth_token_secret_when_set(monkeypatch):
    seen = []

    def fake_run(cmd, input, capture_output, text, timeout, env=None):
        seen.append(env)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"structured_output": results_json(1), "usage": {}}))
    monkeypatch.setattr("coach.classify.backends.subprocess.run", fake_run)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    ClaudeCodeBackend().complete("S", "D", LABEL_SCHEMA, "haiku")
    from coach import secrets
    secrets.set_secret("claude_code_oauth_token", "test-oauth-token")
    ClaudeCodeBackend().complete("S", "D", LABEL_SCHEMA, "haiku")
    assert seen[0] is None                                          # no token: the CLI's own login, the call is unchanged
    assert seen[1]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-token"
