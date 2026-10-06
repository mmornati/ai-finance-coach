"""E11-1 / E11-4: the egress policy, the local journal, `local_only` / `offline` refusals at the egress layer, and the `classify enrich` audit.

Everything is synthetic and mocked: no network, no subprocess, no Keychain, no real LLM.
"""
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from alerthelpers import ALL_ON, Net, settings
from coach import cli, egress, privacy
from coach.agent import prompt as P
from coach.agent.runner import CoachUnavailable, run_agent
from coach.alerts import channels as ch
from coach.alerts import messages as M
from coach.classify import backends, commands as cc
from coach.classify.backends import AnthropicBackend, ClaudeCodeBackend, LLMBackend, OllamaBackend, get_backend
from coach.config import load_config
from coach.db import connect
from helpers import add_bank, add_tx
from test_llm_backends import FakeClient, results_json

MSG = M.Message("Coach alerts", "Coach: 2 new alerts (1 high). Open the app.", "high", ["bell"])


def make_cfg(tmp_path, privacy_lines="", extra=""):
    (tmp_path / "memory").mkdir(exist_ok=True)
    p = tmp_path / "config.toml"
    p.write_text('data_dir = "data"\nmemory_dir = "memory"\n' + extra + "\n[privacy]\n" + privacy_lines + "\n")
    return load_config(p, env={})


@pytest.fixture
def local(tmp_path):
    return make_cfg(tmp_path, "local_only = true\n")


def no_call(*a, **k):
    raise AssertionError("an outbound call was made")


# ---------------------------------------------------------------- the inventory


def test_the_inventory_covers_every_backend_channel_and_the_bank():
    kinds = set(egress.KINDS)
    assert {"enable_banking", "mcp.finance", "skill.web_search"} <= kinds
    assert {f"llm.{b}" for b in backends.BACKENDS} <= kinds
    from coach.alerts.settings import CHANNELS
    assert {f"alerts.{c}" for c in CHANNELS} <= kinds
    for fl in egress.INVENTORY:
        assert fl.destination and fl.data and fl.redaction and fl.opt_in and fl.disable
    # the three LLM purposes the epic names are listed for the claude-code path, web search included
    cc_flow = egress.KINDS["llm.claude-code"]
    assert {"classify.label", "classify.enrich", "classify.compare", "coach.ask", "coach.digest", "coach.skill",
            "memory.doc_extract"} <= set(cc_flow.purposes)
    assert "classify.enrich" not in egress.KINDS["llm.anthropic-api"].purposes      # web search needs the claude-code backend


def test_an_unregistered_kind_is_refused():
    with pytest.raises(egress.EgressDenied) as e:
        egress.allow("made.up")
    assert e.value.code == "unregistered"


def test_with_the_default_test_policy_everything_registered_is_allowed_and_nothing_is_journaled():
    assert egress.active().cfg is None                   # the permissive default policy of the test session
    egress.allow("llm.claude-code", {"purpose": "classify.label", "bytes": 5})
    assert egress._pending == []


# ---------------------------------------------------------------- the journal


def journal(cfg):
    con = connect(cfg, insecure=False)
    try:
        return con.execute("SELECT ts, kind, host, bytes, purpose, redaction, outcome, reason, web FROM egress_journal ORDER BY id").fetchall()
    finally:
        con.close()


def test_the_journal_records_host_size_purpose_redaction_and_never_the_payload(cfg, db_key):
    connect(cfg, create=True).close()
    egress.allow("llm.anthropic-api", {"host": "api.anthropic.com", "bytes": 1234, "purpose": "classify.label",
                                        "payload": "SECRET-MERCHANT-NAME", "url": "https://x.example/secret/path?q=1"}, cfg=cfg)
    rows = journal(cfg)
    assert len(rows) == 1
    ts, kind, host, nbytes, purpose, redaction, outcome, reason, web = rows[0]
    assert (kind, host, nbytes, purpose, redaction, outcome, reason, web) == (
        "llm.anthropic-api", "api.anthropic.com", 1234, "classify.label", "item-redact", "allowed", "", 0)
    con = connect(cfg)
    dump = json.dumps(con.execute("SELECT * FROM egress_journal").fetchall())
    con.close()
    assert "SECRET-MERCHANT-NAME" not in dump and "/secret/path" not in dump
    cols = {r[1] for r in connect(cfg).execute("PRAGMA table_info(egress_journal)")}
    assert not cols & {"payload", "body", "prompt", "content", "url", "path"}


def test_a_refused_attempt_is_journaled_with_its_reason(tmp_path, db_key):
    cfg = make_cfg(tmp_path, "local_only = true\n")
    connect(cfg, create=True).close()
    with pytest.raises(egress.EgressDenied):
        egress.allow("alerts.telegram", {"purpose": "alert"}, cfg=cfg)
    (row,) = journal(cfg)
    assert row[1] == "alerts.telegram" and row[6] == "denied" and row[7] == "local_only"


def test_journal_off_writes_nothing(tmp_path, db_key):
    cfg = make_cfg(tmp_path, "egress_journal = false\n")
    connect(cfg, create=True).close()
    egress.allow("enable_banking", {"purpose": "sync"}, cfg=cfg)
    assert journal(cfg) == []


def test_rows_wait_when_the_table_is_missing_and_are_written_later(tmp_path, db_key):
    cfg = make_cfg(tmp_path)
    egress.activate(cfg)
    egress.allow("enable_banking", {"purpose": "sync"})                 # no database yet: the row stays pending, nothing raises
    assert len(egress._pending) == 1
    connect(cfg, create=True).close()
    egress.flush()
    assert egress._pending == [] and len(journal(cfg)) == 1


def test_summary_and_report_text(tmp_path, db_key, capsys):
    cfg = make_cfg(tmp_path)
    connect(cfg, create=True).close()
    egress.activate(cfg)
    egress.allow("llm.claude-code", {"purpose": "classify.label", "bytes": 2000})
    egress.allow("llm.claude-code", {"purpose": "classify.label", "bytes": 3000})
    egress.allow("enable_banking", {"purpose": "sync", "host": "api.enablebanking.com", "bytes": 10})
    con = connect(cfg)
    js = egress.journal_summary(con, 30)
    con.close()
    assert js["total"] == 3 and js["bytes"] == 5010 and js["denied"] == 0
    row = next(r for r in js["rows"] if r["kind"] == "llm.claude-code")
    assert row["count"] == 2 and row["bytes"] == 5000 and row["purpose"] == "classify.label"
    privacy.cmd_report(Namespace(json=False, days=30, insecure=False), cfg)
    out = capsys.readouterr().out
    assert "egress inventory" in out and "[llm.claude-code]" in out and "[enable_banking]" in out
    assert "3 call(s)" in out and "api.enablebanking.com" in out
    privacy.cmd_report(Namespace(json=True, days=30, insecure=False), cfg)
    data = json.loads(capsys.readouterr().out)
    assert data["journal"]["total"] == 3 and len(data["inventory"]) == len(egress.INVENTORY)


# ---------------------------------------------------------------- local_only (E11-4)


def test_status_describes_the_effective_mode(tmp_path):
    std = egress.status(make_cfg(tmp_path))
    assert std["mode"] == "standard" and std["bank_sync"] and not std["web_search"] and std["web_search_reason"] == "web_enrich_off"
    loc = egress.status(make_cfg(tmp_path, "local_only = true\n"))
    assert loc["mode"] == "local_only" and loc["bank_sync"] is True and not loc["web_search"] and not loc["web_search_skills"]
    assert not loc["external_alert_channels"] and loc["llm_misconfigured"] == [{"purpose": "classify", "backend": "claude-code"},
                                                                              {"purpose": "coach", "backend": "claude-code"}]
    off = egress.status(make_cfg(tmp_path, "offline = true\n"))
    assert off["mode"] == "offline" and off["local_only"] is True and off["bank_sync"] is False
    ok = egress.status(make_cfg(tmp_path, "local_only = true\n", '[llm]\nbackend = "ollama"\n[coach]\nbackend = "ollama"\n'))
    assert ok["llm_misconfigured"] == []


def test_status_text_is_readable(tmp_path):
    t = privacy.status_text(egress.status(make_cfg(tmp_path, "local_only = true\n")))
    assert "LOCAL ONLY" in t and "find-cheaper" in t and "REFUSED" in t


@pytest.mark.parametrize("name", ["claude-code", "anthropic-api"])
def test_local_only_refuses_a_cloud_backend_at_construction(local, name):
    with pytest.raises(egress.EgressDenied) as e:
        get_backend(local, name)
    assert e.value.code == "local_only" and "ollama" in str(e.value)


def test_local_only_allows_ollama_on_loopback(local):
    assert isinstance(get_backend(local, "ollama"), OllamaBackend)


def test_local_only_claude_code_backend_is_refused_before_the_process_starts(local, monkeypatch):
    monkeypatch.setattr("coach.classify.backends.subprocess.run", no_call)
    with pytest.raises(egress.EgressDenied):
        ClaudeCodeBackend(cfg=local).complete("s", "d", {}, "haiku")


def test_local_only_anthropic_backend_never_touches_its_client(local):
    client = FakeClient([results_json(1)])
    with pytest.raises(egress.EgressDenied):
        AnthropicBackend(client=client, cfg=local).complete("s", "d", {"type": "object"}, "haiku")
    assert client.messages.calls == []
    with pytest.raises(egress.EgressDenied):                                  # batches too
        AnthropicBackend(client=client, cfg=local).submit([{"static": "s", "dynamic": "d", "schema": {}, "model": "haiku"}])


def test_local_only_ollama_runs_and_is_journaled_as_local(tmp_path, db_key, monkeypatch):
    cfg = make_cfg(tmp_path, "local_only = true\n")
    connect(cfg, create=True).close()
    body = {"message": {"content": json.dumps(results_json(1))}, "prompt_eval_count": 5, "eval_count": 2}
    monkeypatch.setattr("coach.classify.backends.requests.post",
                        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: body))
    data, usage = OllamaBackend(cfg=cfg).complete("s", "d", {"type": "object"}, "llama3.1", "label", 1)
    assert data["results"][0]["category"] == "food.groceries"
    (row,) = journal(cfg)
    assert row[1] == "llm.ollama" and row[2] == "localhost" and row[6] == "allowed"


def test_local_only_refuses_a_remote_ollama_even_with_allow_remote(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, "local_only = true\n", '[llm]\nbackend = "ollama"\nollama_url = "http://10.1.2.3:11434"\nollama_allow_remote = true\n')
    monkeypatch.setattr("coach.classify.backends.requests.post", no_call)
    with pytest.raises(egress.EgressDenied) as e:
        get_backend(cfg)
    assert e.value.code == "non_loopback"
    with pytest.raises(egress.EgressDenied):
        OllamaBackend("http://10.1.2.3:11434", allow_remote=True, cfg=cfg).complete("s", "d", {}, "m")


@pytest.mark.parametrize("backend", ["claude-code", "anthropic-api"])
def test_local_only_refuses_the_coach_runtime_with_a_reason(local, backend, monkeypatch):
    monkeypatch.setattr("coach.agent.runner.subprocess.Popen", no_call)
    with pytest.raises(CoachUnavailable, match="local_only"):
        run_agent(local, P.ASK, "how much do we spend?", backend=backend, client=None)


def test_coach_availability_says_why(local):
    from coach.api.coachjobs import availability
    ok, msg = availability(local)
    assert not ok and "local_only" in msg


def test_local_only_refuses_the_web_search_everywhere(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, "local_only = true\nweb_enrich = true\n")
    monkeypatch.setattr("coach.classify.backends.subprocess.run", no_call)
    ok, code, _ = egress.evaluate("llm.claude-code", {"web_search": True}, cfg=cfg)
    assert not ok and code == "local_only"
    with pytest.raises(SystemExit, match="local_only"):
        cc.cmd_enrich(Namespace(insecure=True, model=None, max_conf=0.7, limit=5, batch=5, workers=1, dry_run=False), cfg)


def test_local_only_disables_the_external_alert_channels_at_the_egress_layer(local):
    s = settings(**ALL_ON)
    net = Net()
    t = net.transports()
    for name in ("ntfy", "email", "telegram"):
        probs = ch.problems(s, name, t, local)
        assert any("local_only" in p for p in probs), (name, probs)
        with pytest.raises(ch.ChannelError, match="local_only"):
            ch.send(s, name, MSG, t, local)
    assert net.total() == 0                                                  # nothing was posted, mailed or run
    ch.send(s, "macos", MSG, t, local)                                       # a notification on this Mac stays allowed
    assert len(net.macos) == 1


def test_alerts_dispatch_reports_a_policy_blocked_channel_as_not_ready(local, db_key):
    from coach.alerts import engine
    con = connect(local, create=True)
    net = Net()
    res = engine.dispatch(con, local, settings(**{"ntfy": ALL_ON["ntfy"]}), transports=net.transports())
    assert res[0]["status"] == "not_ready" and "local_only" in res[0]["reason"]
    assert net.total() == 0


def test_local_only_mcp_server_refuses_to_start(local):
    with pytest.raises(SystemExit, match="local_only"):
        cli.cmd_mcp_serve(Namespace(insecure=False, only=None, session=None), local)


# ---------------------------------------------------------------- offline


def test_offline_refuses_the_bank_and_everything_else(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, "offline = true\n", '[enable_banking]\napp_id = "x"\nprivate_key_path = "k.pem"\n')
    monkeypatch.setattr("coach.ingest.client.requests.request", no_call)
    from coach.ingest.client import EnableBankingClient
    c = EnableBankingClient("app", "k.pem", "https://api.example.test", cfg=cfg)
    c.token = lambda: "jwt"
    with pytest.raises(egress.EgressDenied) as e:
        c.call("GET", "/accounts/a/transactions")
    assert e.value.code == "offline" and "file imports" in str(e.value)
    for kind in ("llm.claude-code", "llm.anthropic-api", "alerts.ntfy", "mcp.finance"):
        assert not egress.evaluate(kind, cfg=cfg)[0]
    assert egress.evaluate("llm.ollama", {"host": "localhost"}, cfg=cfg)[0]
    assert egress.evaluate("alerts.macos", cfg=cfg)[0]


def test_local_only_still_allows_the_bank(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, "local_only = true\n")
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url))
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {"ok": 1}, text="{}")
    monkeypatch.setattr("coach.ingest.client.requests.request", fake)
    from coach.ingest.client import EnableBankingClient
    c = EnableBankingClient("app", "k.pem", "https://api.example.test", cfg=cfg)
    c.token = lambda: "jwt"
    assert c.call("GET", "/application") == {"ok": 1} and calls == [("GET", "https://api.example.test/application")]


def test_the_scheduled_job_skips_sync_offline_and_classify_when_the_backend_is_refused(tmp_path, db_key, monkeypatch):
    from coach import schedule as sch
    cfg = make_cfg(tmp_path, "offline = true\n")
    connect(cfg, create=True).close()
    monkeypatch.setattr("coach.classify.backends.subprocess.run", no_call)
    out = []
    code = sch.run_daily(cfg, client=SimpleNamespace(call=no_call), out=out.append)
    log = "\n".join(out)
    assert code == 0 and "sync: skipped" in log and "classify: skipped" in log


# ---------------------------------------------------------------- the web enrichment audit


class RecBackend(LLMBackend):
    name = "claude-code"
    supports_web_search = True

    def __init__(self):
        self.calls = []

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        self.calls.append(SimpleNamespace(static=static, dynamic=dynamic, web=web_search))
        return {"results": [{"id": i, "merchant": f"Shop {i}", "category": "food.groceries", "confidence": 0.95, "recurring_hint": False,
                             "evidence": "found"} for i in range(items)]}, backends.Usage("claude-code", model, purpose, items)


@pytest.fixture
def world(tmp_path, db_key):
    cfg = make_cfg(tmp_path, "web_enrich = true\n")
    (cfg.memory_dir / "household.yaml").write_text("members:\n  - id: a\n    name: Anna Durand\n    role: adult\nplaces: [\"Roquemont lez Roquefort\"]\n")
    con = connect(cfg, create=True)
    add_bank(con, "s", "Fortuneo", "FR", [("a", "FR76", "M OU MME DURAND PAUL")])
    keys = {                                            # key: (tx_type, merchant_raw)
        "QUINCAILLERIE ZORBAX QUIMPER": ("card", "QUINCAILLERIE ZORBAX QUIMPER"),
        "ZORBAX GADGET 0612345678": ("card", "ZORBAX GADGET 0612345678"),
        "LUCA PARIS": ("card", "LUCA PARIS"),                          # reads like a person
        "DR HALBERT CLAIRE": ("card", "DR HALBERT CLAIRE"),             # a titled person
        "MME LEFEBVRE": ("card", "MME LEFEBVRE"),
        "VASSEUR T": ("transfer_out", "VASSEUR T"),                       # a transfer: never a shop
        "DURAND PAUL TRAITEUR": ("card", "DURAND PAUL TRAITEUR"),       # the household's own name
        "VELLARD NATHALIE ROQUEMONT LEZ ROQUEFORT": ("card", "VELLARD NATHALIE ROQUEMONT LEZ ROQUEFORT"),   # a person, then a multi-word declared town
        "LEROY SOPHIE": ("card", "LEROY SOPHIE"),
        "BRASSERIE DU PORT ROQUEMONT LEZ ROQUEFORT": ("card", "BRASSERIE DU PORT ROQUEMONT LEZ ROQUEFORT"),   # a shop in the home town
    }
    for i, (k, (typ, raw)) in enumerate(keys.items()):
        add_tx(con, "a", f"a:{i}", f"2026-09-{i + 1:02d}", -100.0 - i, f"CARTE {raw}", "card")
        con.execute("UPDATE tx_enriched SET tx_type=?, merchant_raw=?, merchant_key=? WHERE tx_key=?", (typ, raw, k, f"a:{i}"))
        con.execute("INSERT INTO merchants VALUES (?,?,?,?,0,'llm','m','t')", (k, k.title(), "other.uncategorized", 0.3))
    con.commit()
    return cfg, con


def enrich_args(**kw):
    base = dict(insecure=False, model=None, max_conf=0.7, limit=40, batch=8, workers=1, dry_run=False)
    return Namespace(**{**base, **kw})


def test_enrich_is_opt_in_and_sends_nothing_by_default(world, monkeypatch):
    cfg, con = world
    cfg.privacy_web_enrich = False
    rec = RecBackend()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    with pytest.raises(SystemExit, match="web_enrich"):
        cc.cmd_enrich(enrich_args(), cfg)
    assert rec.calls == []


def test_enrich_refusal_is_journaled(world):
    cfg, con = world
    cfg.privacy_web_enrich = False
    with pytest.raises(SystemExit):
        cc.cmd_enrich(enrich_args(), cfg)
    con.close()
    rows = journal(cfg)
    assert rows and rows[-1][6] == "denied" and rows[-1][7] == "web_enrich_off" and rows[-1][8] == 1


def test_enrich_never_searches_a_person_like_merchant_and_redacts_the_rest(world, monkeypatch, capsys):
    cfg, con = world
    rec = RecBackend()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    cc.cmd_enrich(enrich_args(), cfg)
    sent = "\n".join(c.static + c.dynamic for c in rec.calls)
    assert rec.calls and all(c.web for c in rec.calls)
    assert "QUINCAILLERIE ZORBAX QUIMPER" in sent                              # a shop is searched
    for person in ("LUCA", "HALBERT", "LEFEBVRE", "VASSEUR", "DURAND", "VELLARD", "NATHALIE", "LEROY", "SOPHIE"):
        assert person not in sent.upper(), person
    assert "BRASSERIE DU PORT" in sent and "ROQUEMONT" not in sent.upper() and "ROQUEFORT" not in sent.upper()      # the home town is never searched
    assert "France" in sent                                                    # the prompt says to search the name with France
    assert "0612345678" not in sent and "[PHONE]" in sent                     # the redaction layer ran on what is sent
    out = capsys.readouterr().out
    assert "NOT searched because they may be people" in out


def test_enrich_dry_run_shows_the_exact_request_and_calls_nothing(world, monkeypatch, capsys):
    cfg, con = world
    cfg.privacy_web_enrich = False                                             # a dry run works (and says it is disabled)
    rec = RecBackend()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    cc.cmd_enrich(enrich_args(dry_run=True), cfg)
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "DISABLED" in out and "QUINCAILLERIE ZORBAX QUIMPER" in out and "LUCA" not in out and rec.calls == []


def test_the_web_search_flag_reaches_the_gate(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)                                                   # standard mode, web_enrich off
    monkeypatch.setattr("coach.classify.backends.subprocess.run", no_call)
    with pytest.raises(egress.EgressDenied) as e:
        ClaudeCodeBackend(cfg=cfg).complete("s", "d", {}, "sonnet", "enrich", 1, web_search=True)
    assert e.value.code == "web_enrich_off"


def test_compare_does_not_send_person_like_merchants(world, monkeypatch, capsys):
    cfg, con = world
    rec = RecBackend()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    monkeypatch.setattr(cc, "collect_pending_batches", lambda *a, **k: 0, raising=False)
    cc.cmd_compare(Namespace(insecure=False, model="x", sample=50, seed=1, batch=40, workers=1), cfg)
    sent = "\n".join(c.dynamic for c in rec.calls).upper()
    assert "ZORBAX" in sent
    for person in ("LUCA", "HALBERT", "LEFEBVRE", "VASSEUR", "DURAND"):
        assert person not in sent, person


# ---------------------------------------------------------------- config


def test_privacy_settings_load_and_show_in_config(tmp_path):
    cfg = make_cfg(tmp_path, "local_only = true\nweb_enrich = true\negress_journal = false\n")
    assert cfg.privacy_local_only and cfg.privacy_web_enrich and not cfg.privacy_egress_journal and not cfg.privacy_offline
    from coach.config import effective
    rows = {k: v for k, v, _ in effective(cfg)}
    assert rows["privacy.local_only"] == "true" and rows["privacy.web_enrich"] == "true" and rows["privacy.offline"] == "false"


def test_the_example_config_documents_the_new_settings():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text()
    for key in ("local_only", "offline", "web_enrich", "egress_journal"):
        assert key in text


def test_the_journal_is_pruned_after_its_retention(tmp_path, db_key):
    import datetime as dt
    cfg = make_cfg(tmp_path, "egress_journal_days = 60\n")
    assert cfg.privacy_egress_journal_days == 60
    con = connect(cfg, create=True)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat(timespec="seconds")
    con.execute("INSERT INTO egress_journal(ts, kind) VALUES (?, 'enable_banking')", (old,))
    con.execute("INSERT INTO egress_journal(ts, kind) VALUES (datetime('now'), 'enable_banking')")
    con.commit()
    assert egress.prune_journal(con, 60) == 1
    assert con.execute("SELECT COUNT(*) FROM egress_journal").fetchone()[0] == 1
    from coach.config import ConfigError
    with pytest.raises(ConfigError, match="egress_journal_days"):
        make_cfg(tmp_path, "egress_journal_days = 5\n")


def test_the_web_search_skills_read_the_privacy_status_first():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / ".claude" / "skills"
    for skill in ("find-cheaper", "mortgage-check"):
        t = (root / skill / "SKILL.md").read_text()
        assert "coach privacy status" in t and "web_search_skills" in t, skill
        assert t.index("coach privacy status") < t.index("## Safety rules"), skill        # before any rule about searching, i.e. first
        assert "STOP" in t or "do NOT search" in t
    enrich = (root / "categorize-transactions" / "SKILL.md").read_text()
    assert "web_enrich" in enrich and "--dry-run" in enrich and "Never enable it yourself" in enrich


def test_a_refused_backend_choice_is_journaled_and_the_web_message_is_specific(tmp_path, db_key):
    cfg = make_cfg(tmp_path, "local_only = true\n")
    connect(cfg, create=True).close()
    with pytest.raises(egress.EgressDenied):
        get_backend(cfg, "claude-code")
    with pytest.raises(egress.EgressDenied, match="web search is disabled"):
        egress.require("llm.claude-code", {"web_search": True, "purpose": "classify.enrich"}, cfg=cfg)
    rows = journal(cfg)
    assert [(r[1], r[4], r[6], r[7]) for r in rows] == [("llm.claude-code", "classify.backend", "denied", "local_only"),
                                                         ("llm.claude-code", "classify.enrich", "denied", "local_only")]
    egress.require("llm.ollama", {"host": "localhost"}, cfg=cfg)                 # an allowed path: nothing journaled by require()
    assert len(journal(cfg)) == 2


def test_person_descriptors_with_towns_are_held_and_the_web_prompt_names_nothing_of_the_household(world):
    from coach.classify import candidates as K
    from coach.classify.llm import web_request, LABEL_HEAD
    cfg, con = world
    places = K.known_places(con, cfg.memory_dir)
    assert ("ROQUEMONT", "LEZ", "ROQUEFORT") in places
    assert K.strip_places("BRASSERIE DU PORT ROQUEMONT LEZ ROQUEFORT", places) == "BRASSERIE DU PORT"
    assert K.strip_places("VELLARD NATHALIE ROQUEMONT LEZ", places) == "VELLARD NATHALIE"
    ok, held = K.enrich_candidates(con, ["VELLARD NATHALIE ROQUEMONT LEZ ROQUEFORT", "LEROY SOPHIE", "BRASSERIE DU PORT ROQUEMONT LEZ ROQUEFORT",
                                         "ROQUEMONT"], (), cfg.memory_dir)
    assert ok == ["BRASSERIE DU PORT ROQUEMONT LEZ ROQUEFORT"] and len(held) == 3          # a descriptor that is only a town is held too
    fam, first = K.household_names(con)
    for name in ("MARIE DUPONT", "PAUL MARTIN QUIMPER", "DE SMET LUCIE", "ZORBAX CHLOE LYS LEZ"):
        assert K.hold_back(name, name, {"card"}, fam, first, frozenset(), (), places=places), name
    for shop in ("QUINCAILLERIE ZORBAX", "LEROY MERLIN", "BOULANGERIE PAUL", "CARREFOUR MARKET"):
        assert not K.hold_back(shop, shop, {"card"}, fam, first, frozenset(), (), places=places), shop
    static, _, _ = web_request([], fam)
    assert K.prompt_leaks(static + LABEL_HEAD, places, fam | first) == []
    assert K.prompt_leaks("search ZORBAX in ROQUEMONT", places, set()) == ["ROQUEMONT"]
