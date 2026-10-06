"""Opus review of E2: privacy guards (P1-P5), sync/batch/taxonomy/entity fixes (D2-D8), validation and nits."""
import json
import os
import shutil
from argparse import Namespace
from types import SimpleNamespace

import pytest

from coach import db as dbm
from coach.classify import backends, candidates, commands as cc, entities, knn, llm, rules as rules_mod, splits, taxonomy
from coach.classify.backends import BatchPending, OllamaBackend, estimate_cost, safe_diagnostic
from coach.classify.llm import LabelRunError, label_keys, prepare_jobs, validate_results
from coach.classify.normalize import normalize_all
from coach.classify.parsers import parse_tx
from coach.classify.parsers.common import Household, RawTx, strip_vir_prefix
from coach.classify.rules import categorised, resolve
from coach.config import ConfigError, load_config
from coach.db import connect
from coach.ingest.sync import sync_account
from helpers import FakeClient, add_bank, add_tx, eb_tx


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    add_bank(c, "s1", "Fortuneo", "FR", [("fo", "FR76A", "M OU MME DURAND PAUL")])
    add_bank(c, "s2", "Caisse d'Epargne X", "FR", [("ce", "FR76B", "CPT COURANT TEST")])
    add_bank(c, "s3", "CIC", "FR", [("cic", "FR76C", "MME J DURAND ET M P DURAND")])
    add_bank(c, "s4", "Revolut", "LT", [("rp", "LT1", "Paul Durand")])
    add_bank(c, "s5", "Banca Test", "IT", [("it", "IT60X0542811101000000123456", "MARIO ROSSI")])
    return c


def tx(con, key, mkey, amount=-10.0, uid="fo", ttype="card", date="2026-09-01"):
    add_tx(con, uid, key, date, amount, f"CARTE 01/09 {mkey}", ttype)
    con.execute("UPDATE tx_enriched SET merchant_raw=?, merchant_key=? WHERE tx_key=?", (mkey, mkey, key))
    con.commit()


def label(con, key, cat, source="user", conf=1.0, name=None):
    con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,NULL,'t')", (key, name or key.title(), cat, conf, source))
    con.commit()


def put(con, uid, key, amount, desc, code="", cp=""):
    con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, amount, currency, "
                "counterparty, description, bank_tx_code, raw, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,'EUR',?,?,?,'{}','t','t')", (key, uid, key, "2026-03-02", amount, cp, desc, code))


class Rec(backends.LLMBackend):
    """Records every request; labels everything as groceries."""
    name = "rec"

    def __init__(self):
        self.calls = []

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        self.calls.append((static, dynamic))
        ids = [i["id"] for i in json.loads(dynamic.split("avg_amount in EUR):\n")[1])]
        return {"results": [{"id": i, "merchant": "M", "category": "food.groceries", "confidence": 0.9,
                             "recurring_hint": False} for i in ids]}, backends.Usage("rec", model, items=len(ids))


def run_ns(**kw):
    base = dict(insecure=True, model="m", batch=50, workers=1, limit=None, refresh=False, include_ruled=False,
                no_knn=False, knn_threshold=None, dry_run=False)
    base.update(kw)
    return Namespace(**base)


# ================================================================ P1: hints and examples

def test_p1_person_like_and_transfer_labels_never_become_hints_or_examples(cfg, con):
    # user-confirmed labels of: a doctor paid by direct debit, a person (transfer), a titled card merchant, a shop
    for k, mk, t, cat in (("d1", "DR OAKLEY MARIE", "direct_debit", "health.doctors"),
                          ("d2", "JANE ROE", "transfer_out", "transfer.to_people"),
                          ("d3", "SALARY CORP SAS", "transfer_in", "income.salary"),
                          ("d4", "CARREFOUR MARKET QUIMPER", "card", "food.groceries"),
                          ("d5", "DR PEPPER SHOP", "card", "food.groceries"),
                          ("d6", "ACME ASSURANCES IARD", "direct_debit", "insurance.other")):
        tx(con, k, mk, ttype=t)
        label(con, mk, cat)
    eligible = candidates.eligible_label_keys(con)
    assert eligible == {"CARREFOUR MARKET QUIMPER", "ACME ASSURANCES IARD"}
    assert {k for k, _, _ in knn.labelled_corpus(con)} == eligible
    assert {k for k, _, _ in llm.user_examples(con)} == eligible
    # nothing of it reaches a prompt: neither as few-shot example nor as nearest-neighbour hint
    tx(con, "n1", "CARREFOUR MARKET DINAN")
    tx(con, "n2", "DR OAKLEY MARIE CABINET")
    idx = knn.build_index(con)
    (keys, job), = prepare_jobs(con, ["CARREFOUR MARKET DINAN"], "m", 10, idx)
    text = job["dynamic"] + job["static"]
    for forbidden in ("OAKLEY", "JANE ROE", "SALARY CORP", "DR PEPPER"):
        assert forbidden not in text.upper()
    assert "ACME ASSURANCES IARD" in text and "CARREFOUR MARKET QUIMPER" in text


def test_p1_a_key_seen_once_as_a_transfer_is_not_eligible(con):
    tx(con, "a", "SHOPPY", ttype="card")
    tx(con, "b", "SHOPPY", ttype="transfer_out", date="2026-09-02")
    label(con, "SHOPPY", "shopping.clothing")
    assert candidates.eligible_label_keys(con) == set()


# ================================================================ P2: default-deny guard

PERSON_LINES = [
    ("ce", "VIR INST YASMINE BENALI", -50.0, ""),
    ("ce", "VIR SEPA NGUYEN THI HOA", -50.0, ""),
    ("ce", "VIR INST KOFFI ADJOUMANI", 50.0, ""),
    ("ce", "VIR SEPA RECU /DE JANE ROE /MOTIF LOYER AVRIL /REF 8837", 300.0, ""),
    ("ce", "PAYLIB ZED QUINN", -20.0, ""),
    ("ce", "PRLV SEPA SAMIR KHALIFA", -40.0, ""),
    ("fo", "VIR INST ZELDA FINCH", -30.0, ""),
    ("fo", "VIR INST Paylib Zelda Finch", -30.0, ""),
    ("cic", "VIR ZELDA FINCH | REMBOURSEMENT", 30.0, ""),
    ("it", "BONIFICO A FAVORE DI GIULIO ROSSI", -25.0, ""),
    ("rp", "Payment from Anya Volk", 25.0, ""),
    ("rp", "Sent from Revolut | Anya Volk", -25.0, ""),
]
ORG_LINES = [
    ("ce", "VIR SEPA ACME SARL", 100.0), ("ce", "VIR SEPA CPAM QUIMPER", 12.0), ("ce", "PRLV MUTUELLE ASSURANCES IARD", -9.0),
    ("ce", "PRLV ZENOVIA", -9.0), ("cic", "VIR SAS   NEXITY NORMAND | E2E-1", 700.0),
    ("it", "ADDEBITO SDD ENEL ENERGIA SPA MANDATO ABC1234567", -60.0),
]


def test_p2_unknown_names_are_never_candidates_but_organisations_are(cfg, con):
    for i, (uid, desc, amt, cp) in enumerate(PERSON_LINES):
        put(con, uid, f"p{i}", amt, desc, code="TRANSFER" if uid == "rp" else "", cp=cp)
    for i, (uid, desc, amt) in enumerate(ORG_LINES):
        put(con, uid, f"o{i}", amt, desc)
    con.commit()
    normalize_all(con, cfg.memory_dir)
    cands, held = candidates.llm_candidates(con)
    sent = {c["key"] for c in cands}
    blob = " ".join(sent).upper()
    for name in ("YASMINE", "BENALI", "NGUYEN", "HOA", "KOFFI", "ADJOUMANI", "JANE", "ROE", "LOYER", "QUINN", "ZED",
                 "SAMIR", "KHALIFA", "ZELDA", "FINCH", "GIULIO", "ANYA", "VOLK", "REMBOURSEMENT"):
        assert name not in blob, name
    assert {"ACME SARL", "CPAM QUIMPER", "MUTUELLE ASSURANCES IARD", "ZENOVIA", "ENEL ENERGIA SPA"} <= sent
    types = dict(con.execute("SELECT tx_key, tx_type FROM tx_enriched"))
    assert types["p4"] == "person_transfer_out"                          # PAYLIB <name>
    assert types["p10"] == "person_transfer_in" and types["p11"] == "person_transfer_out"   # Revolut, no code
    assert types["p3"] == "person_transfer_in" and con.execute(
        "SELECT merchant_key FROM tx_enriched WHERE tx_key='p3'").fetchone()[0] == "JANE ROE"   # tag text stripped


def test_p2_allowlist_and_known_merchants_override_the_hold(cfg, con):
    put(con, "ce", "x1", -9.0, "PRLV COFIDIS")
    put(con, "ce", "x2", -9.0, "PRLV QUARTZ MOTION")
    con.commit()
    normalize_all(con, cfg.memory_dir)
    held = lambda **kw: {c["key"] for c in candidates.llm_candidates(con, **kw)[1]}
    assert "COFIDIS" not in held()                    # organisation word in the shared vocabulary
    assert "QUARTZ MOTION" in held()                   # two unknown words in a direct debit: held back
    assert "QUARTZ MOTION" not in held(allow=(r"^QUARTZ MOTION$",))
    # already known as a card merchant -> not a person
    tx(con, "k1", "QUARTZ MOTION", ttype="card", date="2026-03-05")
    label(con, "QUARTZ MOTION", "shopping.electronics", "llm", 0.9)
    assert "QUARTZ MOTION" not in {c["key"] for c in candidates.llm_candidates(con, refresh=True)[1]}


def test_p2_dry_run_prints_the_exact_redacted_payload_and_calls_nothing(cfg, con, monkeypatch, capsys):
    tx(con, "q1", "LIDL QUIMPER 0612345678")
    tx(con, "q2", "CABINET DR OAKLEY MARIE PARIS")
    tx(con, "q4", "DR OAKLEY MARIE")                                # starts with a title: held back entirely
    label(con, "LIDL REDON", "food.groceries")
    tx(con, "q3", "LIDL REDON")
    monkeypatch.setattr(cc, "get_backend", lambda c: pytest.fail("a backend must not be created in a dry run"))
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("merchants", "llm_usage")}
    n = cc.cmd_run(run_ns(dry_run=True), cfg)
    out = capsys.readouterr().out
    assert n == 0 and "DRY RUN" in out
    payload = json.loads(out[out.index("["):])
    body = payload[0]["dynamic_prompt"]
    assert "[PHONE]" in body and "0612345678" not in body and "CABINET DR [NAME] PARIS" in body and "OAKLEY" not in body
    assert "LIDL REDON" in body                                   # the hint shown to the model is visible
    assert before == {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("merchants", "llm_usage")}


# ================================================================ P3 / P4 / P5

def test_p3_memory_decided_merchants_are_not_sent(cfg, con, monkeypatch):
    tx(con, "m1", "PINNED SHOP")
    tx(con, "m2", "OTHER SHOP")
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: pin\n    match: {merchant_key: '^PINNED'}\n    category: food.groceries\n")
    rec = Rec()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    cc.cmd_run(run_ns(), cfg)
    sent = " ".join(d for _, d in rec.calls)
    assert "OTHER SHOP" in sent and "PINNED SHOP" not in sent


def test_p4_sepa_tags_and_reasons_do_not_enter_the_key():
    assert strip_vir_prefix("VIR SEPA RECU /DE ACME SAS /MOTIF LOYER MARS /REF 123") == "ACME SAS"
    r = parse_tx(RawTx("VIR SEPA RECU /DE ACME SAS /MOTIF LOYER MARS", 100.0, "2026-03-02"), Household(), bank="CIC")
    assert r["merchant_key"] == "ACME SAS"


def test_p5_titled_names_are_masked_but_the_title_stays():
    from coach.classify.redact import redact, redact_item
    assert redact("DR OAKLEY MARIE") == "DR [NAME]"
    assert redact("CABINET DR OAKLEY MARIE PARIS") == "CABINET DR [NAME] PARIS"
    assert redact_item({"key": "MME ROE", "raw_example": "Dr. Oakley"})["raw_example"] == "Dr. [NAME]"
    assert redact("DRIVE LEADER PRICE") == "DRIVE LEADER PRICE"


# ================================================================ output validation

def test_llm_output_is_validated():
    out = validate_results([
        {"id": 0, "merchant": "A", "category": "food.groceries", "confidence": 1.7, "recurring_hint": 1},
        {"id": 1, "merchant": "B", "category": "made.up", "confidence": 0.95, "recurring_hint": False},
        {"id": 2, "merchant": "C", "category": "food.groceries", "confidence": "x", "recurring_hint": False},
        {"id": 3, "merchant": "D", "category": "food.groceries", "confidence": float("nan")},
        {"id": 9, "merchant": "out of range", "category": "food.groceries", "confidence": 1},
        {"id": -1}, {"id": "1"}, {"id": True}, "junk", None], 4)
    assert [r["id"] for r in out] == [0, 1, 3]                  # id 2: a confidence that is not a number
    assert out[0]["confidence"] == 1.0 and out[0]["recurring_hint"] is True
    assert (out[1]["category"], out[1]["confidence"]) == ("other.uncategorized", 0.3)
    assert out[2]["confidence"] == 0.0                         # NaN is clamped
    assert validate_results(None, 3) == [] and validate_results("x", 3) == []


def test_label_schema_follows_the_taxonomy_after_a_rename(cfg, con, tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "uc"))
    try:
        assert "shopping.clothing" in llm.label_schema()["properties"]["results"]["items"]["properties"]["category"]["enum"]
        taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
        enum = llm.label_schema()["properties"]["results"]["items"]["properties"]["category"]["enum"]
        assert "shopping.apparel" in enum and "shopping.clothing" not in enum
    finally:
        monkeypatch.delenv("COACH_CONFIG_DIR")
        rules_mod.reload_taxonomy()


# ================================================================ D2: re-worded transactions

@pytest.fixture
def scon(con):
    return con


def sync(con, *txs, uid="ce"):
    fc = FakeClient({uid: [{"transactions": list(txs)}]})
    out = []
    res = sync_account(con, fc, uid, full=True, force=True, out=out.append)
    return res, out


def test_d2_same_reference_enriched_description_is_one_transaction(con):
    d = "2026-03-02"
    sync(con, eb_tx("REF-1", d, -57.35, "PRLV ORANGE SA"))
    res, out = sync(con, eb_tx("REF-1", d, -57.35, "PRLV ORANGE SA | ECHEANCE 10/2026"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert res["new"] == 0 and res["conflicts"] == [] and res["refreshed"] == ["ce:ref:REF-1"]
    assert con.execute("SELECT description FROM transactions").fetchone()[0] == "PRLV ORANGE SA | ECHEANCE 10/2026"
    assert not any("warning" in o for o in out)
    # a booking date moved by a day is still the same transaction
    sync(con, eb_tx("REF-1", "2026-03-03", -57.35, "PRLV ORANGE SA | ECHEANCE 10/2026"))
    assert con.execute("SELECT COUNT(*), MAX(booking_date) FROM transactions").fetchone() == (1, "2026-03-03")


def test_d2_real_amount_or_date_difference_keeps_both_and_health_shows_it(con):
    from coach.ingest.health import health
    d = "2026-03-02"
    sync(con, eb_tx("REF-1", d, -10.0, "PRLV A"))
    res, out = sync(con, eb_tx("REF-1", d, -99.0, "PRLV A"))
    assert len(res["conflicts"]) == 1 and con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    res, _ = sync(con, eb_tx("REF-1", "2026-03-20", -10.0, "PRLV A"))
    assert len(res["conflicts"]) == 1
    con.execute("UPDATE accounts SET source='api' WHERE uid='ce'")
    rep = health(con)
    acc = next(a for b in rep.banks for a in b.accounts if a.uid == "ce")
    assert acc.key_conflicts == 1 and any("reused a bank reference" in p for p in acc.problems)


# ================================================================ D3 / D4: batches

def jobs(n):
    return [dict(static="S", dynamic=f"D{i}", schema=llm.label_schema(), model="haiku", purpose="label", items=1)
            for i in range(n)]


class FB:
    """Fake anthropic client whose batch results can be scripted per custom_id."""
    def __init__(self, results, pending_polls=0):
        self.script, self.pending_polls, self.created = results, pending_polls, 0
        self.polls = 0
        self.messages = SimpleNamespace(batches=self, create=lambda **kw: None)

    def create(self, requests):
        self.created += 1
        self.n = len(requests)
        return SimpleNamespace(id=getattr(self, "bid", "batch_X"))

    def list(self, limit=20):
        return [SimpleNamespace(id=getattr(self, "bid", "batch_X"), created_at="2999-01-01T00:00:00Z",
                                request_counts=SimpleNamespace(processing=getattr(self, "n", 0), succeeded=0, errored=0,
                                                               canceled=0, expired=0))] if getattr(self, "n", 0) else []

    def retrieve(self, bid):
        self.polls += 1
        return SimpleNamespace(processing_status="in_progress" if self.polls <= self.pending_polls else "ended")

    def results(self, bid):
        for cid, kind in self.script(self.n):
            if kind == "ok":
                msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"results": [
                    {"id": 0, "merchant": "M", "category": "food.groceries", "confidence": 0.9, "recurring_hint": False}]}))],
                    stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
                yield SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=msg))
            elif kind == "maxtok":
                msg = SimpleNamespace(content=[], stop_reason="max_tokens", usage=SimpleNamespace(
                    input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))
                yield SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=msg))
            else:
                yield SimpleNamespace(custom_id=cid, result=SimpleNamespace(type=kind))


def make_batch_backend(fb):
    be = backends.AnthropicBackend(client=SimpleNamespace(messages=SimpleNamespace(
        batches=fb, create=lambda **kw: pytest.fail("sync call"))), use_batch=True, sleep=lambda s: None,
        max_wait_seconds=3, poll_seconds=1)
    return be


def test_d3_per_job_failures_do_not_discard_the_rest(con):
    fb = FB(lambda n: [("job2", "ok"), ("job0", "ok"), ("job1", "errored"), ("jobX", "ok"), ("job99", "ok")])
    out = make_batch_backend(fb).complete_many(jobs(4))
    assert isinstance(out[0], tuple) and isinstance(out[2], tuple)
    assert isinstance(out[1], RuntimeError) and "errored" in str(out[1])
    assert isinstance(out[3], RuntimeError) and "no result" in str(out[3])        # missing custom_id
    # unknown / out-of-range custom ids are ignored, not applied to some job
    fb = FB(lambda n: [("job0", "maxtok"), ("job1", "expired"), ("job2", "canceled")])
    out = make_batch_backend(fb).complete_many(jobs(3))
    assert all(isinstance(o, Exception) for o in out)


def test_d3_label_keys_stores_what_succeeded_retries_only_failures_and_logs_usage(con):
    for i in range(3):
        tx(con, f"b{i}", f"SHOP {chr(65 + i)}")
    fb = FB(lambda n: [("job0", "ok"), ("job1", "errored"), ("job2", "ok")])
    be = make_batch_backend(fb)
    sync_calls = []
    orig = be.complete
    be.complete = lambda **j: (sync_calls.append(j["dynamic"]), orig(**j))[1] if False else (_ for _ in ()).throw(
        RuntimeError("still failing"))
    stored = []
    with pytest.raises(LabelRunError) as e:
        label_keys(con, ["SHOP A", "SHOP B", "SHOP C"], "haiku", 1, 1, backend=be,
                   on_batch=lambda b, pairs: stored.extend(k for k, _ in pairs))
    assert sorted(stored) == ["SHOP A", "SHOP C"]                      # the paid results are kept
    assert [k for k, _ in e.value.failed] == [["SHOP B"]]
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2     # usage logged for what succeeded
    # the failed job is retried synchronously (once) before giving up
    fb2 = FB(lambda n: [("job0", "ok"), ("job1", "expired"), ("job2", "ok")])
    fb2.bid = "batch_Y"
    be2 = make_batch_backend(fb2)
    be2.complete = lambda **j: ({"results": [{"id": 0, "merchant": "M", "category": "food.groceries",
                                              "confidence": 0.9, "recurring_hint": False}]},
                                backends.Usage("anthropic-api", "haiku"))
    con.execute("DELETE FROM llm_batches")
    con.execute("DELETE FROM llm_batch_jobs")
    con.commit()
    res, cost = label_keys(con, ["SHOP A", "SHOP B", "SHOP C"], "haiku", 1, 1, backend=be2)
    assert sorted(k for k, _ in res) == ["SHOP A", "SHOP B", "SHOP C"]


def test_d3_timeout_saves_the_batch_id_and_a_rerun_resumes_without_paying_twice(con):
    for i in range(2):
        tx(con, f"r{i}", f"SHOP {chr(65 + i)}")
    fb = FB(lambda n: [("job0", "ok"), ("job1", "ok")], pending_polls=100)
    be = make_batch_backend(fb)
    with pytest.raises(BatchPending):
        label_keys(con, ["SHOP A", "SHOP B"], "haiku", 1, 1, backend=be)
    assert fb.created == 1
    row = con.execute("SELECT batch_id, status FROM llm_batches").fetchone()
    assert row == ("batch_X", "pending")
    # later: the same run finds the saved batch, creates nothing, collects the results
    fb.pending_polls = 0
    got = []
    n = llm.collect_pending_batches(con, make_batch_backend(fb), lambda b, pairs, m=None: got.extend(pairs))
    assert fb.created == 1 and n == 2 == len(got)
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "ended"
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2


def test_d4_a_failing_batch_does_not_lose_the_ones_already_paid(cfg, con, monkeypatch):
    for i in range(3):
        tx(con, f"s{i}", ["SHOP ALPHA", "SHOP BRAVO", "SHOP CHARLIE"][i], amount=-100.0 + i)

    class Flaky(Rec):
        def complete(self, static, dynamic, schema, model, **kw):
            if "SHOP BRAVO" in dynamic:
                raise RuntimeError("boom")
            return super().complete(static, dynamic, schema, model, **kw)
    monkeypatch.setattr(cc, "get_backend", lambda c: Flaky())
    with pytest.raises(LabelRunError):
        cc.cmd_run(run_ns(batch=1, no_knn=True), cfg)
    got = {r[0] for r in con.execute("SELECT merchant_key FROM merchants WHERE source='llm'")}
    assert got == {"SHOP ALPHA", "SHOP CHARLIE"}
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2        # committed per batch
    # a re-run asks only for the missing one
    rec = Rec()
    monkeypatch.setattr(cc, "get_backend", lambda c: rec)
    cc.cmd_run(run_ns(batch=1, no_knn=True), cfg)
    assert len(rec.calls) == 1 and "SHOP BRAVO" in rec.calls[0][1]


# ================================================================ D5: ollama only on this machine

@pytest.mark.parametrize("url,ok", [("http://localhost:11434", True), ("http://127.0.0.1:11434", True),
                                    ("http://[::1]:11434", True), ("http://10.0.0.5:11434", False),
                                    ("https://llm.example.com", False), ("http://localhost.evil.com", False),
                                    ("file:///tmp/x", False)])
def test_d5_ollama_url_must_be_loopback(url, ok):
    if ok:
        OllamaBackend(url)
    else:
        with pytest.raises(ValueError):
            OllamaBackend(url)
    OllamaBackend("http://10.0.0.5:11434", allow_remote=True)


def test_d5_config_enforces_it_and_allows_an_explicit_opt_in(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nbackend = "ollama"\nollama_url = "http://gpu-box.lan:11434"\n')
    with pytest.raises(ConfigError, match="loopback"):
        load_config(p, env={})
    p.write_text('[llm]\nbackend = "ollama"\nollama_url = "http://gpu-box.lan:11434"\nollama_allow_remote = true\n')
    cfg = load_config(p, env={})
    assert backends.get_backend(cfg).base_url == "http://gpu-box.lan:11434"


# ================================================================ D6: taxonomy

@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "userconf"))
    yield tmp_path / "userconf"
    monkeypatch.delenv("COACH_CONFIG_DIR")
    rules_mod.reload_taxonomy()


def test_d6a_group_header_with_a_trailing_comment(user_dir, con):
    pkg = (rules_mod.HERE / "taxonomy.yaml").read_text()
    user_dir.mkdir()
    (user_dir / "taxonomy.yaml").write_text(pkg.replace("\nfood:\n", "\nfood:   # everyday food\n"))
    shutil.copy(rules_mod.HERE / "rules.yaml", user_dir / "rules.yaml")
    rules_mod.reload_taxonomy()
    before = set(rules_mod.CATEGORIES)
    taxonomy.add_leaf("food.snacks", "Snacks")
    assert set(rules_mod.CATEGORIES) == before | {"food.snacks"}
    text = (user_dir / "taxonomy.yaml").read_text()
    assert text.count("\nfood:") == 1 and "# everyday food" in text
    taxonomy.rename_leaf(con, "food.snacks", "food.treats")
    assert set(rules_mod.CATEGORIES) == before | {"food.treats"}
    assert (user_dir / "taxonomy.yaml").read_text().count("\nfood:") == 1


def test_d6a_a_malformed_edit_is_refused_before_writing():
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy._validated("a:\n  b: x\na:\n  c: y\n", {"a.b", "a.c"})
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy._validated("a:\n  b: x\n", {"a.b", "a.c"})
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy._validated("a: [\n", {"a.b"})


def test_d6b_edits_go_to_a_user_copy_and_the_package_is_untouched(user_dir, con):
    pkg_t = (rules_mod.HERE / "taxonomy.yaml").read_bytes()
    pkg_r = (rules_mod.HERE / "rules.yaml").read_bytes()
    assert rules_mod.taxonomy_file() == rules_mod.HERE / "taxonomy.yaml"           # default: the packaged file
    taxonomy.add_leaf("housing.garden", "Garden")
    taxonomy.rename_leaf(con, "pets.pets", "pets.vet")
    assert (rules_mod.HERE / "taxonomy.yaml").read_bytes() == pkg_t and (rules_mod.HERE / "rules.yaml").read_bytes() == pkg_r
    assert rules_mod.taxonomy_file() == user_dir / "taxonomy.yaml" and "housing.garden" in rules_mod.CATEGORIES
    rules_mod.reload_taxonomy()                                                      # a fresh process reads the copy
    assert "pets.vet" in rules_mod.CATEGORIES and "pets.pets" not in rules_mod.CATEGORIES


def test_d6c_rename_refuses_to_run_inside_an_open_transaction(user_dir, con):
    label(con, "SHOPX", "shopping.clothing")
    con.execute("INSERT INTO sessions(session_id, aspsp_name) VALUES ('pending-work','x')")      # caller's open work
    with pytest.raises(taxonomy.TaxonomyError, match="open transaction"):
        taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    assert con.in_transaction                                    # neither committed nor rolled back for the caller
    assert "shopping.clothing" in rules_mod.CATEGORIES and not (user_dir / "taxonomy.yaml").exists()
    con.rollback()
    taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='SHOPX'").fetchone()[0] == "shopping.apparel"


def test_d6c_database_and_yaml_stay_consistent_when_the_swap_fails(user_dir, con, monkeypatch):
    label(con, "SHOPX", "shopping.clothing")
    taxonomy.ensure = None
    t_before = rules_mod.ensure_user_file("taxonomy").read_text()
    real_replace = os.replace

    def failing(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(taxonomy.os, "replace", failing)
    with pytest.raises(OSError):
        taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    monkeypatch.setattr(taxonomy.os, "replace", real_replace)
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='SHOPX'").fetchone()[0] == "shopping.clothing"
    assert rules_mod.taxonomy_file().read_text() == t_before and "shopping.clothing" in rules_mod.CATEGORIES
    # and a failing DB update leaves the files alone
    con.execute("DROP TABLE tx_splits")
    with pytest.raises(Exception):
        taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    assert rules_mod.taxonomy_file().read_text() == t_before
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='SHOPX'").fetchone()[0] == "shopping.clothing"


# ================================================================ D7: entities

def test_d7_user_set_entity_category_beats_llm_labels_but_not_user_labels_or_rules(con):
    label(con, "CAFE A", "food.restaurants", "llm", 0.9, "Cafe A")
    label(con, "CAFE B", "food.restaurants", "llm", 0.9, "Cafe A")
    label(con, "CAFE C", "shopping.clothing", "user", 1.0, "Cafe A")
    eid = entities.merge(con, ["CAFE A", "CAFE B", "CAFE C"], "Cafe A")
    assert resolve(con, "x", "card", "CAFE A")[0] == "food.restaurants"           # auto/majority category: a default only
    con.execute("UPDATE merchant_entities SET category='food.cafes_bars', category_user=0")
    assert resolve(con, "x", "card", "CAFE A") == ("food.restaurants", "llm")
    entities.set_category(con, "Cafe A", "food.cafes_bars")                        # the user decides
    assert resolve(con, "x", "card", "CAFE A") == ("food.cafes_bars", "entity")
    assert resolve(con, "x", "card", "CAFE B") == ("food.cafes_bars", "entity")
    assert resolve(con, "x", "card", "CAFE C") == ("shopping.clothing", "user")    # user label wins
    label(con, "NETFLIX", "food.restaurants", "llm", 0.9, "Cafe A")
    con.execute("INSERT INTO merchant_aliases VALUES ('NETFLIX', ?, 'user', 't')", (eid,))
    assert resolve(con, "x", "card", "NETFLIX")[0] == "subscriptions.video_streaming"   # a rule wins
    entities.set_category(con, "Cafe A", None)
    assert resolve(con, "x", "card", "CAFE A") == ("food.restaurants", "llm")


@pytest.mark.parametrize("name", ["Unknown merchant", "Unknown", "N/A", "n/a", "", "Unknown Business", "None", "Merchant",
                                  "Inconnu", "Not provided"])
def test_d7_generic_llm_names_never_form_an_entity(con, name):
    label(con, "KEY ONE", "food.groceries", "llm", 0.9, name)
    label(con, "KEY TWO", "shopping.clothing", "llm", 0.9, name)
    assert entities.auto_group(con)["created"] == 0
    assert con.execute("SELECT COUNT(*) FROM merchant_aliases").fetchone()[0] == 0


def test_d7_aliased_keys_are_not_excluded_from_refresh_forever(con):
    tx(con, "e1", "MCDO A")
    label(con, "MCDO A", "food.fast_food", "llm", 0.9, "McDonalds")
    tx(con, "e2", "MCDO B")
    label(con, "MCDO B", "food.fast_food", "llm", 0.9, "McDonalds")
    entities.auto_group(con)
    assert con.execute("SELECT category FROM merchant_entities").fetchone()[0] == "food.fast_food"
    assert candidates.llm_candidates(con)[0] == []                          # labelled: nothing to ask
    assert {c["key"] for c in candidates.llm_candidates(con, refresh=True)[0]} == {"MCDO A", "MCDO B"}
    tx(con, "e3", "MCDO C")                                                  # a new unlabelled variant
    con.execute("INSERT INTO merchant_aliases VALUES ('MCDO C', (SELECT id FROM merchant_entities), 'user', 't')")
    assert "MCDO C" not in {c["key"] for c in candidates.llm_candidates(con)[0]}      # inherits the entity category


# ================================================================ D8: review --accept

def test_d8_accept_only_queued_keys_and_prints_the_category(cfg, con, capsys):
    tx(con, "a1", "QUEUED SHOP", -50.0)
    label(con, "QUEUED SHOP", "food.groceries", "llm", 0.5)
    tx(con, "a2", "SURE SHOP", -50.0)
    label(con, "SURE SHOP", "food.groceries", "llm", 0.95)                   # confident: not in the queue
    tx(con, "a3", "NETFLIX", -9.0)
    label(con, "NETFLIX", "food.groceries", "llm", 0.4)                      # decided by a rule
    tx(con, "a4", "PINNED", -9.0)
    label(con, "PINNED", "food.groceries", "llm", 0.4)
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: pin\n    match: {merchant_key: '^PINNED$'}\n    category: food.restaurants\n")
    ns = lambda *k: Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=list(k))
    for key in ("SURE SHOP", "NETFLIX", "PINNED", "NOPE"):
        with pytest.raises(SystemExit):
            cc.cmd_review(ns(key), cfg)
        assert con.execute("SELECT source FROM merchants WHERE merchant_key=?", (key,)).fetchone() in (("llm",), None)
    assert "not in the review queue" in capsys.readouterr().out
    cc.cmd_review(ns("QUEUED SHOP"), cfg)
    assert "confirming food.groceries" in capsys.readouterr().out
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='QUEUED SHOP'").fetchone()[0] == "user"


# ================================================================ minor + nits

def test_unknown_model_cost_is_unknown_not_zero(con):
    assert estimate_cost("some-new-model", 1000, 100) is None
    assert estimate_cost("claude-haiku-4-5", 1000, 100) > 0
    backends.record_usage(con, backends.Usage("anthropic-api", "some-new-model", cost_usd=None))
    assert con.execute("SELECT cost_usd FROM llm_usage").fetchone()[0] is None
    assert backends.Usage("ollama", "x").cost_usd == 0.0           # a local model genuinely costs nothing


def test_claude_code_errors_do_not_echo_the_prompt(monkeypatch):
    prompt_text = "Items: PRIVATE MERCHANT NAME LONG ENOUGH TO MATCH"
    monkeypatch.setattr("coach.classify.backends.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="error near: PRIVATE MERCHANT NAME LONG ENOUGH TO MATCH\nrate limited"))
    with pytest.raises(RuntimeError) as e:
        backends.ClaudeCodeBackend().complete("static ", prompt_text, {}, "sonnet")
    assert "PRIVATE" not in str(e.value) and "rate limited" in str(e.value)
    assert safe_diagnostic("x" * 1000, "") == "x" * 200


def test_anthropic_client_is_pinned_to_the_official_endpoint():
    assert backends.AnthropicBackend().base_url == "https://api.anthropic.com"
    assert backends.AnthropicBackend(base_url="https://proxy.example").base_url == "https://proxy.example"


def test_split_input_validation_memory_precedence_and_merge_duplicates(cfg, con):
    tx(con, "sp1", "SUPERMARKET", -100.0)
    for bad in ("NaN", "Infinity", "-Infinity", "33.333", "1e-3", "abc"):
        with pytest.raises(splits.SplitError):
            splits.set_split(con, "sp1", [f"food.groceries:{bad}", "housing.furniture:rest"])
    assert con.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0] == 0
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: market\n    match: {merchant_key: '^SUPERMARKET'}\n    category: food.groceries\n"
        "    tags: [one_off]\n    event: ev1\n")
    splits.set_split(con, "sp1", ["food.groceries:60", "housing.furniture:rest"])
    w = splits.memory_conflicts(con, "sp1", cfg.memory_dir)
    assert len(w) == 1 and "market" in w[0] and "split parts take precedence" in w[0]
    parts = [t for t in categorised(con, memory_dir=cfg.memory_dir) if t["tx_key"] == "sp1"]
    assert sorted(t["category"] for t in parts) == ["food.groceries", "housing.furniture"]       # parts keep their own
    assert all(t["tags"] == {"one_off"} and t["event"] == "ev1" for t in parts)                  # memory tags apply


def test_account_merge_duplicate_path_carries_splits(con):
    from coach.ingest.accounts import merge_accounts
    add_bank(con, "s9", "Fortuneo", "FR", [("fo2", "FR76A", "M OU MME DURAND PAUL")])
    for uid in ("fo", "fo2"):
        con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, amount, currency, "
                    "description) VALUES (?,?,?,?,?,?,?)", (f"{uid}:ref:R1", uid, "R1", "2026-03-02", -40.0, "EUR", "X"))
    con.execute("INSERT INTO tx_splits(tx_key, amount, category) VALUES ('fo2:ref:R1', -10, 'food.groceries')")
    con.execute("INSERT INTO tx_splits(tx_key, amount, category) VALUES ('fo2:ref:R1', -30, 'housing.furniture')")
    con.commit()
    merge_accounts(con, "fo", "fo2")
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert con.execute("SELECT tx_key, COUNT(*) FROM tx_splits GROUP BY 1").fetchall() == [("fo:ref:R1", 2)]


def test_knn_labels_follow_their_sources(cfg, con):
    tx(con, "n1", "CARREFOUR MARKET QUIMPER")
    label(con, "CARREFOUR MARKET QUIMPER", "food.groceries", "user", 1.0)
    label(con, "CARREFOUR MARKET QUIMPER SUD", "food.groceries", "knn", 0.95)
    label(con, "ORPHAN LABELLED SHOP", "food.groceries", "knn", 0.95)
    idx = knn.build_index(con)
    r = knn.refresh_knn_labels(con, idx, 0.8)
    assert r == {"changed": 0, "dropped": 1}                                 # the orphan has no supporting neighbour
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='CARREFOUR MARKET QUIMPER SUD'").fetchone()[0] == "food.groceries"
    # the user corrects the source label: the derived label follows
    label(con, "CARREFOUR MARKET QUIMPER", "shopping.department_general", "user", 1.0)
    r = knn.refresh_knn_labels(con, knn.build_index(con), 0.8)
    assert r["changed"] == 1
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='CARREFOUR MARKET QUIMPER SUD'").fetchone()[0] == "shopping.department_general"
    # the source disappears (becomes a person-like / ineligible label): the derived label is dropped
    label(con, "CARREFOUR MARKET QUIMPER", "transfer.to_people", "user", 1.0)
    assert knn.refresh_knn_labels(con, knn.build_index(con), 0.8)["dropped"] == 1


def test_report_shows_the_similarity_hit_rate(cfg, con, capsys):
    tx(con, "h1", "SHOP ONE")
    label(con, "SHOP ONE", "food.groceries", "llm", 0.9)
    label(con, "SHOP ONE TWO", "food.groceries", "knn", 0.95)
    cc.cmd_report(Namespace(insecure=True), cfg)
    assert "similarity (kNN) hit rate 1/2 = 50%" in capsys.readouterr().out


# ================================================================ round 2: sync

def test_r2_same_reference_same_amount_other_merchant_is_a_different_transaction(con):
    d = "2026-03-02"
    sync(con, eb_tx("REF-9", d, -9.99, "CB NETFLIX"))
    res, out = sync(con, eb_tx("REF-9", d, -9.99, "CB SPOTIFY"))
    assert len(res["conflicts"]) == 1 and res["refreshed"] == []
    assert sorted(r[0] for r in con.execute("SELECT description FROM transactions")) == ["CB NETFLIX", "CB SPOTIFY"]
    assert any("different content" in o for o in out)
    # remembered: the next syncs neither warn nor count it again, and add nothing
    res, out = sync(con, eb_tx("REF-9", d, -9.99, "CB SPOTIFY"))
    assert res["conflicts"] == [] and not any("warning" in o for o in out)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert "key_conflicts" not in con.execute("SELECT note FROM sync_log ORDER BY rowid DESC").fetchone()[0]


def test_r2_the_same_reference_twice_in_one_response_keeps_both(con):
    d = "2026-03-02"
    res, _ = sync(con, eb_tx("REF-D", d, -5.0, "CB COFFEE ONE"), eb_tx("REF-D", d, -5.0, "CB COFFEE TWO"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2 and len(res["conflicts"]) == 1
    res, out = sync(con, eb_tx("REF-D", d, -5.0, "CB COFFEE ONE"), eb_tx("REF-D", d, -5.0, "CB COFFEE TWO"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2 and res["conflicts"] == []


@pytest.mark.parametrize("old,new,refresh", [
    ("PRLV ORANGE SA", "PRLV ORANGE SA | ECHEANCE 10/2026", True),          # prefix
    ("PRLV ORANGE SA", "ECHEANCE 10/2026 PRLV ORANGE SA", True),            # all old words still there
    ("PRLV ORANGE SA | ECHEANCE 10/2026", "PRLV ORANGE SA", True),          # shortened: same transaction, keep detail
    ("CB NETFLIX", "CB SPOTIFY", False), ("PRLV EDF", "PRLV ENGIE", False)])
def test_r2_only_an_enrichment_refreshes_the_stored_row(con, old, new, refresh):
    d = "2026-03-02"
    sync(con, eb_tx("R-1", d, -20.0, old))
    res, _ = sync(con, eb_tx("R-1", d, -20.0, new))
    assert bool(res["refreshed"]) is refresh and bool(res["conflicts"]) is (not refresh)
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == (1 if refresh else 2)


# ================================================================ round 2: P3

def test_r2_p3_only_keys_fully_decided_by_memory_are_excluded(cfg, con):
    tx(con, "m1", "SALAD CO", date="2026-09-01")
    tx(con, "m2", "SALAD CO", date="2026-09-17")
    tx(con, "m3", "WHOLLY PINNED", date="2026-09-01")
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: a\n    match: {merchant_key: '^SALAD CO$', date_to: 2026-09-10}\n    category: food.work_meals\n"
        "  - id: b\n    match: {merchant_key: '^WHOLLY PINNED$'}\n    category: food.restaurants\n")
    keys = candidates.memory_decided_keys(con, cfg)
    assert keys == {"WHOLLY PINNED"}
    sent = {c["key"] for c in candidates.llm_candidates(con, memory_keys=keys)[0]}
    assert "SALAD CO" in sent and "WHOLLY PINNED" not in sent
    assert "SALAD CO" in {r["key"] for r in cc.review_rows(con, cfg)}


# ================================================================ round 2: batches

def pending_run(con, fb_polls=100):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"w{i}", k)
    fb = FB(lambda n: [("job0", "ok"), ("job1", "ok")], pending_polls=fb_polls)
    be = make_batch_backend(fb)
    with pytest.raises(BatchPending):
        label_keys(con, ["SHOP ALPHA", "SHOP BRAVO"], "haiku", 1, 1, backend=be)
    return fb


def test_r2_d3_a_new_transaction_after_a_timeout_does_not_orphan_the_old_batch(cfg, con, monkeypatch):
    fb = pending_run(con)
    assert con.execute("SELECT custom_id, keys_json FROM llm_batch_jobs ORDER BY custom_id").fetchall() == [
        ("job0", '["SHOP ALPHA"]'), ("job1", '["SHOP BRAVO"]')]
    tx(con, "w9", "SHOP CHARLIE")                                  # a new merchant: the prompts differ now
    fb.pending_polls = 0
    be = make_batch_backend(fb)
    calls = []
    be.complete = lambda **j: (calls.append(j["dynamic"]), ({"results": [{"id": 0, "merchant": "M",
                               "category": "food.groceries", "confidence": 0.9, "recurring_hint": False}]},
                                                             backends.Usage("anthropic-api", "haiku")))[1]
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    cc.cmd_run(run_ns(batch=1, no_knn=True), cfg)
    assert fb.created == 1                                          # nothing was submitted a second time
    got = {r[0] for r in con.execute("SELECT merchant_key FROM merchants WHERE source='llm'")}
    assert got == {"SHOP ALPHA", "SHOP BRAVO", "SHOP CHARLIE"}
    assert len(calls) == 1 and "SHOP CHARLIE" in calls[0] and "SHOP ALPHA" not in calls[0]
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "ended"


def test_r2_d3_a_batch_still_running_blocks_new_submissions_without_failing(cfg, con, monkeypatch, capsys):
    fb = pending_run(con)
    be = make_batch_backend(fb)
    be.submit = lambda jobs: pytest.fail("must not submit while a batch is pending")
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    assert cc.cmd_run(run_ns(batch=1, no_knn=True), cfg) == 0
    assert "still running" in capsys.readouterr().out


def test_r2_d3_interruption_while_storing_loses_nothing_that_was_paid(con):
    fb = pending_run(con)
    fb.pending_polls = 0
    seen = []

    def store(keys, pairs, model=None):
        if keys == ["SHOP BRAVO"]:
            raise KeyboardInterrupt()                                # the process dies while storing job 2
        seen.append(keys)
    with pytest.raises(KeyboardInterrupt):
        llm.collect_pending_batches(con, make_batch_backend(fb), store)
    con.rollback()
    st = dict(con.execute("SELECT custom_id, status FROM llm_batch_jobs"))
    assert st == {"job0": "stored", "job1": "pending"} and con.execute("SELECT status FROM llm_batches").fetchone()[0] == "pending"
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 1          # job 1 paid and recorded
    seen2 = []
    llm.collect_pending_batches(con, make_batch_backend(fb), lambda k, p, m=None: seen2.append(k))
    assert seen == [["SHOP ALPHA"]] and seen2 == [["SHOP BRAVO"]]                  # only the missing one
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "ended" and fb.created == 1


def test_r2_d3_unknown_batch_id_is_marked_failed_and_never_retried(con):
    fb = pending_run(con)
    be = make_batch_backend(fb)

    def gone(bid):
        raise type("NotFoundError", (Exception,), {"status_code": 404})("nope")
    fb.retrieve = gone
    n = llm.collect_pending_batches(con, be, lambda *a: None)
    assert n == 0
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "failed"
    assert {r[0] for r in con.execute("SELECT status FROM llm_batch_jobs")} == {"failed"}
    fb.retrieve = lambda bid: pytest.fail("a failed batch must not be polled again")
    assert llm.collect_pending_batches(con, be, lambda *a: None) == 0


def test_r2_d3_usage_is_logged_for_unusable_answers_in_both_paths(con):
    # batch path: max_tokens result still billed
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"u{i}", k)
    fb = FB(lambda n: [("job0", "maxtok"), ("job1", "maxtok")])
    be = make_batch_backend(fb)
    be.complete = lambda **j: (_ for _ in ()).throw(RuntimeError("still bad"))
    with pytest.raises(LabelRunError):
        label_keys(con, ["SHOP ALPHA", "SHOP BRAVO"], "haiku", 1, 1, backend=be)
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2
    # sync path: bad JSON from the API
    con.execute("DELETE FROM llm_usage")
    con.commit()
    msgs = SimpleNamespace(create=lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="text", text="not json")], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=7, output_tokens=3, cache_read_input_tokens=0, cache_creation_input_tokens=0)))
    sync_be = backends.AnthropicBackend(client=SimpleNamespace(messages=msgs))
    with pytest.raises(LabelRunError):
        label_keys(con, ["SHOP ALPHA"], "haiku", 1, 1, backend=sync_be)
    assert con.execute("SELECT tokens_in, tokens_out FROM llm_usage").fetchall() == [(7, 3)]


def test_r2_d4_storing_failure_of_one_batch_does_not_stop_the_others(con):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO", "SHOP CHARLIE"]):
        tx(con, f"v{i}", k)
    stored = []

    def store(keys, pairs, model=None):
        if keys == ["SHOP BRAVO"]:
            raise RuntimeError("disk")
        stored.extend(keys)
    with pytest.raises(LabelRunError) as e:
        label_keys(con, ["SHOP ALPHA", "SHOP BRAVO", "SHOP CHARLIE"], "m", 1, 1, backend=Rec(), on_batch=store)
    assert sorted(stored) == ["SHOP ALPHA", "SHOP CHARLIE"] and "storing the results failed" in str(e.value)
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 3          # usage of all three calls


# ================================================================ round 2: D6

def test_r2_d6_copy_only_the_edited_file_with_a_version_header_and_stale_warning(user_dir, con):
    taxonomy.add_leaf("housing.garden", "Garden")
    assert (user_dir / "taxonomy.yaml").exists() and not (user_dir / "rules.yaml").exists()
    assert (user_dir / "taxonomy.yaml").read_text().startswith(rules_mod.HASH_HEADER)
    assert rules_mod.stale_copy_warnings() == []
    # a rename that touches rules.yaml copies that one too (and only then)
    taxonomy.rename_leaf(con, "subscriptions.video_streaming", "subscriptions.video")
    assert (user_dir / "rules.yaml").exists() and "category: subscriptions.video\n" in (user_dir / "rules.yaml").read_text()
    # the package changes later: the copy is reported as stale
    t = user_dir / "taxonomy.yaml"
    t.write_text(t.read_text().replace(t.read_text().split("\n", 1)[0], rules_mod.HASH_HEADER + "0123456789abcdef", 1))
    w = rules_mod.stale_copy_warnings()
    assert len(w) == 1 and "older version" in w[0] and "taxonomy.yaml" in w[0]


def test_r2_d6_the_user_copy_is_found_from_the_config_root_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_CONFIG_DIR", raising=False)
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "taxonomy.yaml").write_text(
        (rules_mod.HERE / "taxonomy.yaml").read_text().replace("  other: Other income\n", "  other: Other income\n  windfall: Lottery\n"))
    try:
        rules_mod.set_config_root(root)
        assert rules_mod.taxonomy_file() == root / "config" / "taxonomy.yaml" and "income.windfall" in rules_mod.CATEGORIES
    finally:
        rules_mod.set_config_root(None)
        assert "income.windfall" not in rules_mod.CATEGORIES


# ================================================================ round 2: Q1, D7, D8

def test_r2_q1_card_keys_that_read_like_a_person_are_held_back_and_never_hints(cfg, con):
    tx(con, "q1", "LUCA PARIS")                                       # given name + city, no organisation word
    tx(con, "q2", "BOULANGERIE PAUL")
    tx(con, "q3", "JULES VERNE QUIMPER")
    cands, held = candidates.llm_candidates(con)
    assert {c["key"] for c in cands} == {"BOULANGERIE PAUL"} and {h["key"] for h in held} == {"LUCA PARIS", "JULES VERNE QUIMPER"}
    assert {c["key"] for c in candidates.llm_candidates(con, allow=(r"^LUCA PARIS$",))[0]} == {"BOULANGERIE PAUL", "LUCA PARIS"}
    label(con, "LUCA PARIS", "food.restaurants")                      # even user-labelled: not a hint / example
    label(con, "BOULANGERIE PAUL", "food.groceries")
    assert candidates.eligible_label_keys(con) == {"BOULANGERIE PAUL"}


def test_r2_d7_refresh_skips_keys_of_an_entity_with_a_user_category(con):
    for k in ("CAFE X", "CAFE Y"):
        tx(con, k, k)
        label(con, k, "food.restaurants", "llm", 0.9, "Cafe Exy")
    entities.auto_group(con)
    assert {c["key"] for c in candidates.llm_candidates(con, refresh=True)[0]} == {"CAFE X", "CAFE Y"}
    entities.set_category(con, "Cafe Exy", "food.cafes_bars")
    assert candidates.llm_candidates(con, refresh=True)[0] == []


def test_r2_d7_merging_entities_keeps_the_category_the_user_set(con):
    for k, n in (("AA ONE", "Alpha One"), ("BB TWO", "Beta Two"), ("CC TRE", "Gamma Tre")):
        label(con, k, "food.restaurants", "llm", 0.9, n)
    e1 = entities.merge(con, ["AA ONE"], "Alpha One")
    e2 = entities.merge(con, ["BB TWO"], "Beta Two")
    e3 = entities.merge(con, ["CC TRE"], "Gamma Tre")
    entities.set_category(con, "Beta Two", "health.pharmacy")
    entities.set_category(con, "Gamma Tre", "health.doctors")
    warnings = []
    # the target has no user category: it adopts the other's
    entities.merge(con, [str(e1), str(e2)], warn=warnings.append)
    assert con.execute("SELECT category, category_user FROM merchant_entities WHERE id=?", (e1,)).fetchone() == ("health.pharmacy", 1)
    assert warnings == []
    # both user-set and different: the target's wins, with a warning
    entities.merge(con, [str(e1), str(e3)], warn=warnings.append)
    assert con.execute("SELECT category FROM merchant_entities WHERE id=?", (e1,)).fetchone()[0] == "health.pharmacy"
    assert len(warnings) == 1 and "health.doctors" in warnings[0] and "health.pharmacy" in warnings[0]


def test_r2_d8_keys_under_a_user_category_entity_are_not_reviewed_or_acceptable(cfg, con):
    tx(con, "x1", "CAFE X", -50.0)
    label(con, "CAFE X", "food.restaurants", "llm", 0.4, "Cafe Exy")
    assert "CAFE X" in {r["key"] for r in cc.review_rows(con, cfg)}
    entities.merge(con, ["CAFE X"], "Cafe Exy")
    entities.set_category(con, "Cafe Exy", "food.cafes_bars")
    assert "CAFE X" not in {r["key"] for r in cc.review_rows(con, cfg)}
    with pytest.raises(SystemExit):
        cc.cmd_review(Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=["CAFE X"]), cfg)
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='CAFE X'").fetchone()[0] == "llm"


# ================================================================ round 2: P4, validation, env, nits

@pytest.mark.parametrize("text,expected", [
    ("VIR SEPA RECU ACME SAS/MOTIF LOYER", "ACME SAS"),
    ("VIR SEPA RECU /DE: ACME SAS /MOTIF LOYER", "ACME SAS"),
    ("VIR SEPA RECU /DE ACME SAS /OBJET CONSULTATION DR X SUIVI", "ACME SAS"),
    ("VIR SEPA ACME SAS /REF:123/LIB TEXTE", "ACME SAS"),
    ("VIR INST ACME SAS/EREF NOTPROVIDED", "ACME SAS"),
    ("VIR SEPA ACME SAS /RUM ABC /ICS FR12ZZZ123456", "ACME SAS")])
def test_r2_p4_sepa_tag_variants(text, expected):
    assert strip_vir_prefix(text) == expected


def test_r2_validation_dedupes_ids_and_drops_incomplete_entries():
    ok = {"merchant": "M", "category": "food.groceries", "confidence": 0.9, "recurring_hint": False}
    out = validate_results([{"id": 0, **ok}, {"id": 0, **{**ok, "category": "health.doctors"}},
                            {"id": 1, "category": "food.groceries", "confidence": 0.9},          # no merchant
                            {"id": 2, "merchant": "M", "confidence": 0.9},                      # no category
                            {"id": 3, "merchant": "M", "category": "food.groceries"},           # no confidence
                            {"id": 4, "merchant": "  ", "category": "food.groceries", "confidence": 1}], 5)
    assert [(r["id"], r["category"]) for r in out] == [(0, "food.groceries")]


def test_r2_tests_cannot_see_the_shells_database_settings():
    assert not [v for v in os.environ if (v.startswith(("COACH_", "EB_")) and v != "COACH_CONFIG_DIR")
                or v == "ANTHROPIC_API_KEY"]
    assert "_user_config" in os.environ["COACH_CONFIG_DIR"]           # set by conftest, inside tmp_path


def test_r2_localhost_subdomains_are_not_loopback():
    for url in ("http://ollama.localhost:11434", "http://evil.localhost"):
        with pytest.raises(ValueError):
            OllamaBackend(url)
        OllamaBackend(url, allow_remote=True)


def test_r2_split_memory_warning_follows_a_rekey_and_merge_writes_the_remap(cfg, con):
    from coach.ingest.accounts import merge_accounts
    tx(con, "newkey", "SUPERMARKET", -100.0)
    con.execute("INSERT INTO tx_key_remap VALUES ('oldkey','newkey','t','t')")
    con.commit()
    (cfg.memory_dir / "categorization.yaml").write_text(
        "annotations:\n  - id: pinned\n    match: {tx_keys: [oldkey]}\n    category: food.groceries\n")
    assert len(splits.memory_conflicts(con, "newkey", cfg.memory_dir)) == 1
    add_bank(con, "s9", "Fortuneo", "FR", [("fo2", "FR76A", "M OU MME DURAND PAUL")])
    for uid in ("fo", "fo2"):
        con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, amount, currency, "
                    "description) VALUES (?,?,?,?,?,?,?)", (f"{uid}:ref:R1", uid, "R1", "2026-03-02", -40.0, "EUR", "X"))
    con.commit()
    merge_accounts(con, "fo", "fo2")
    assert con.execute("SELECT new_key FROM tx_key_remap WHERE old_key='fo2:ref:R1'").fetchone()[0] == "fo:ref:R1"


def test_r2_stale_similarity_labels_are_refreshed_even_when_knn_is_switched_off(cfg, con, monkeypatch):
    label(con, "ORPHAN STORE", "food.groceries", "knn", 0.95)             # nothing supports it any more
    monkeypatch.setattr(cc, "get_backend", lambda c: Rec())
    cfg.knn_enabled = False
    cc.cmd_run(run_ns(), cfg)
    assert con.execute("SELECT COUNT(*) FROM merchants WHERE merchant_key='ORPHAN STORE'").fetchone()[0] == 0


def test_r2_auto_migrate_opt_out_and_notice(cfg, tmp_path, capsys):
    old = tmp_path / "mig8"
    old.mkdir()
    for m in dbm.available_migrations():
        if m.version <= 8:
            shutil.copy(m.path, old / m.path.name)
    c = dbm.connect(cfg, insecure=True, create=True, migrate=False)
    dbm.apply_migrations(c, old, safety_copy=False)
    c.close()
    cfg.db_auto_migrate = False
    with pytest.raises(dbm.MigrationError, match="auto_migrate"):
        dbm.connect(cfg, insecure=True)
    c = dbm.connect(cfg, insecure=True, migrate=False)              # `db migrate` / `db status` stay possible
    assert [v for v, *_ in dbm.status(c)["pending"]] == [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
    c.close()
    cfg.db_auto_migrate = True
    capsys.readouterr()
    c = dbm.connect(cfg, insecure=True)
    assert "applied migration(s) 0009_batch_jobs, 0010_" in capsys.readouterr().err and dbm.status(c)["pending"] == []
    dbm.connect(cfg, insecure=True).close()
    assert capsys.readouterr().err == ""                            # nothing to say the next time
    from coach.config import load_config
    p = tmp_path / "c.toml"
    p.write_text("[db]\nauto_migrate = false\n")
    assert load_config(p, env={}).db_auto_migrate is False


# ================================================================ round 3

def cmp_ns(**kw):
    base = dict(insecure=True, model="haiku", sample=10, seed=7, batch=1, workers=1)
    base.update(kw)
    return Namespace(**base)


def test_n1_a_compare_batch_is_never_collected_as_primary_labels(cfg, con, monkeypatch, capsys):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"c{i}", k)
        label(con, k, "shopping.clothing", "llm", 0.9)
    fb = FB(lambda n: [("job0", "ok"), ("job1", "ok")], pending_polls=100)      # the opinions say food.groceries
    be = make_batch_backend(fb)
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    cc.cmd_compare(cmp_ns(), cfg)
    assert fb.created == 1 and con.execute("SELECT purpose, status FROM llm_batches").fetchone() == ("compare", "pending")
    # `classify run` sees the finished compare batch but leaves it alone: primary labels are not overwritten
    fb.pending_polls = 0
    cc.cmd_run(run_ns(batch=1, no_knn=True), cfg)
    assert {r[0] for r in con.execute("SELECT category FROM merchants")} == {"shopping.clothing"}
    assert con.execute("SELECT COUNT(*) FROM merchant_eval").fetchone()[0] == 0
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "pending"
    # `classify compare` resumes ITS batch: opinions go to merchant_eval only, nothing is paid for twice
    capsys.readouterr()
    cc.cmd_compare(cmp_ns(), cfg)
    out = capsys.readouterr().out
    assert fb.created == 1 and "collected 2 opinions" in out and "agreement" in out
    assert [r[0] for r in con.execute("SELECT category FROM merchant_eval")] == ["food.groceries"] * 2
    assert {r[0] for r in con.execute("SELECT category FROM merchants")} == {"shopping.clothing"}
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "ended"
    cc.cmd_compare(cmp_ns(), cfg)                                       # again: everything is already evaluated
    assert fb.created == 1


def test_n2_n3_sepa_reason_text_never_becomes_a_key():
    h = Household()
    dd = parse_tx(RawTx("PRLV SEPA EDF /MOTIF CONSULTATION PSY DUPONT", -50.0, "2026-03-02"), h, bank="Caisse d'Epargne X")
    assert (dd["tx_type"], dd["merchant_key"]) == ("direct_debit", "EDF")
    assert parse_tx(RawTx("PRLV SEPA EDF/MOTIF CONSULTATION", -5.0, "2026-03-02"), h, bank="CIC")["merchant_key"] == "EDF"
    for text, key in (("VIR SEPA RECU /MOTIF LOYER MALADIE", ""), ("VIR SEPA RECU /OBJET CONSULTATION /REF 12", ""),
                      ("VIR SEPA RECU /DE ACME SAS /MOTIF LOYER MALADIE", "ACME SAS"),
                      ("VIR SEPA RECU /MOTIF LOYER /DE ACME SAS", "ACME SAS"),
                      ("VIR SEPA /BEN: ACME SAS /LIB TEXTE", "ACME SAS")):
        r = parse_tx(RawTx(text, 100.0, "2026-03-02"), h, bank="CIC")
        assert r["merchant_key"] == key and "MALADIE" not in r["merchant_raw"] and "CONSULTATION" not in r["merchant_raw"], text


def test_n3_an_empty_counterparty_is_never_sent(cfg, con):
    put(con, "ce", "e1", 100.0, "VIR SEPA RECU /MOTIF LOYER MALADIE")
    con.commit()
    normalize_all(con, cfg.memory_dir)
    assert candidates.llm_candidates(con) == ([], [])


def test_d2_a_shortened_text_refreshes_and_keeps_the_longer_description(con):
    d = "2026-03-02"
    sync(con, eb_tx("R-S", d, -20.0, "PRLV ORANGE SA | ECHEANCE 10/2026"))
    res, _ = sync(con, eb_tx("R-S", d, -20.0, "PRLV ORANGE SA"))
    assert res["refreshed"] and not res["conflicts"]
    assert con.execute("SELECT description FROM transactions").fetchone()[0] == "PRLV ORANGE SA | ECHEANCE 10/2026"


def test_d2_b_identical_entry_listed_twice_in_one_response_is_one_row(con):
    d = "2026-03-02"
    res, out = sync(con, eb_tx("R-D", d, -5.0, "CB COFFEE"), eb_tx("R-D", d, -5.0, "CB COFFEE"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1 and res["conflicts"] == []
    assert not any("warning" in o for o in out)


# ---- N4 / N5: batches whose id was never recorded, legacy batches

def submit_crash(con, monkeypatch, exc):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"z{i}", k)
    fb = FB(lambda n: [("job0", "ok"), ("job1", "ok")])
    be = make_batch_backend(fb)
    seen = {}

    def boom(jobs):
        seen["row"] = con.execute("SELECT status, jobs FROM llm_batches").fetchone()      # recorded BEFORE submitting
        fb.n = len(jobs)
        raise exc
    be.submit = boom
    with pytest.raises(type(exc)):
        label_keys(con, ["SHOP ALPHA", "SHOP BRAVO"], "haiku", 1, 1, backend=be)
    assert seen["row"] == ("submitting", 2)
    return fb


def test_n4_a_submit_that_may_have_reached_the_provider_is_recovered_not_repeated(con):
    fb = submit_crash(con, None, ConnectionError("link dropped after the request was sent"))
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "submitting"
    be = make_batch_backend(fb)
    be.submit = lambda jobs: pytest.fail("must not submit again")
    got = []
    n = llm.collect_pending_batches(con, be, lambda k, p, m=None: got.extend(k))
    assert sorted(got) == ["SHOP ALPHA", "SHOP BRAVO"] and n == 2           # found by request count + creation time
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "ended"


def test_n4_ambiguous_or_missing_batch_blocks_resubmission_until_the_user_decides(cfg, con, monkeypatch, capsys):
    fb = submit_crash(con, monkeypatch, ConnectionError("link dropped"))
    fb.n = 0                                                                  # the provider lists nothing like ours
    be = make_batch_backend(fb)
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    assert cc.cmd_run(run_ns(batch=1, no_knn=True), cfg) == 0
    assert "may have submitted a batch" in capsys.readouterr().out
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "submitting"
    be.submit = lambda jobs: "batch_NEW"
    fb.bid = "batch_NEW"
    cc.cmd_run(run_ns(batch=1, no_knn=True, abandon_unrecorded=True), cfg)     # the user accepts the risk
    assert con.execute("SELECT status FROM llm_batches WHERE batch_id<>'batch_NEW' ORDER BY rowid LIMIT 1").fetchone()[0] == "abandoned"
    assert con.execute("SELECT status FROM llm_batches WHERE batch_id='batch_NEW'").fetchone()[0] == "ended"


def test_n4_a_request_refused_outright_leaves_no_trace(con):
    class Refused(Exception):
        status_code = 400
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"y{i}", k)
    be = make_batch_backend(FB(lambda n: []))
    be.submit = lambda jobs: (_ for _ in ()).throw(Refused("bad request"))
    with pytest.raises(Refused):
        label_keys(con, ["SHOP ALPHA", "SHOP BRAVO"], "haiku", 1, 1, backend=be)
    assert con.execute("SELECT COUNT(*) FROM llm_batches").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM llm_batch_jobs").fetchone()[0] == 0


def test_n5_a_legacy_pending_batch_without_a_mapping_is_never_closed_silently(cfg, con, capsys, monkeypatch):
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('fp','old_batch','anthropic-api','2026-01-01','pending',3)")
    con.commit()
    be = make_batch_backend(FB(lambda n: []))
    llm.collect_pending_batches(con, be, lambda *a: None)
    assert "no saved request mapping" in capsys.readouterr().out
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "pending"
    out = llm.abandon_unrecorded(con, be)
    assert [b for b, _ in out] == ["old_batch"] and "no saved request mapping" in out[0][1]
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "abandoned"


# ---- N6 / N7: taxonomy copy, merge-package, interrupted swap

def make_old_copy(user_dir):
    """A user copy made from an older package: no income.rental, a renamed leaf, a custom leaf, a custom rule."""
    user_dir.mkdir(parents=True, exist_ok=True)
    pkg = (rules_mod.HERE / "taxonomy.yaml").read_text()
    ids = sorted(f"{g}.{leaf}" for g, ls in __import__("yaml").safe_load(pkg).items() for leaf in ls)
    old_ids = [i for i in ids if i not in ("income.rental", "housing.property_charges")]
    text = "\n".join(l for l in pkg.splitlines() if not l.startswith(("  rental:", "  property_charges:")))
    text = text.replace("  clothing:", "  apparel:").replace("\nother:", "\nmine:\n  custom: My own leaf\nother:")
    (user_dir / "taxonomy.yaml").write_text(f"{rules_mod.HASH_HEADER}deadbeefdeadbeef\n{rules_mod.IDS_HEADER}{','.join(old_ids)}\n{text}\n")
    rules = (rules_mod.HERE / "rules.yaml").read_text()
    rules = rules.replace("shopping.clothing", "shopping.apparel")
    drop = rules.index("  - match: '\\bSYNDIC")
    rules = rules[:drop] + rules[rules.index("  # money received"):]          # the user copy lacks the syndic rule
    rules += "  - match: '\\bMYSHOP\\b'\n    category: mine.custom\n"
    pkg_rules = yaml_load(rules_mod.HERE / "rules.yaml")["merchant_rules"]
    base = [i for i in rules_mod.package_rule_ids() if i != rules_mod.rule_id(
        next(m for m in pkg_rules if "SYNDIC" in m["match"]))]
    (user_dir / "rules.yaml").write_text(f"{rules_mod.HASH_HEADER}deadbeefdeadbeef\n{rules_mod.RULES_HEADER}{','.join(base)}\n{rules}")
    rules_mod.reload_taxonomy()


def test_n6_stale_copy_is_detected_and_merge_package_adds_only_what_is_new(user_dir, con):
    make_old_copy(user_dir)
    w = " ".join(rules_mod.stale_copy_warnings())
    assert "older version" in w and "housing.property_charges" in w or "income.rental" in w
    assert "income.rental" in rules_mod.unknown_references()
    before_rules = yaml_load(user_dir / "rules.yaml")
    r = taxonomy.merge_package()
    assert set(r["leaves"]) == {"income.rental", "housing.property_charges"}
    cats = rules_mod.CATEGORIES
    assert "income.rental" in cats and "housing.property_charges" in cats
    assert "shopping.apparel" in cats and "shopping.clothing" not in cats      # the user's rename is not undone
    assert "mine.custom" in cats                                              # nor the custom leaf
    merged = yaml_load(user_dir / "rules.yaml")
    matches = [m["match"] for m in merged["merchant_rules"]]
    assert any("SYNDIC" in m for m in matches) and any("MYSHOP" in m for m in matches)       # new + the user's own
    assert len(merged["merchant_rules"]) == len(before_rules["merchant_rules"]) + 1
    assert [m for m in merged["merchant_rules"] if "NEXITY" in m["match"]][0]["category"] == "income.rental"
    assert rules_mod.stale_copy_warnings() == [] and rules_mod.unknown_references() == []
    again = taxonomy.merge_package()                                          # idempotent
    assert again["leaves"] == [] and again["merchant_rules"] == []


def yaml_load(p):
    return __import__("yaml").safe_load(p.read_text())


def test_n7_a_crash_between_commit_and_swap_is_completed_at_the_next_start(user_dir, con, monkeypatch):
    label(con, "SHOPX", "shopping.clothing")
    with monkeypatch.context() as m:
        m.setattr(taxonomy.os, "replace", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    assert con.execute("SELECT category FROM merchants WHERE merchant_key='SHOPX'").fetchone()[0] == "shopping.apparel"
    assert taxonomy.journal_path().exists()
    assert "shopping.clothing" in (user_dir / "taxonomy.yaml").read_text()          # files still old: inconsistent
    msg = taxonomy.recover_journal(con)                                              # what connect() runs on start
    assert "completed" in msg and not taxonomy.journal_path().exists()
    assert "shopping.apparel" in rules_mod.CATEGORIES and "shopping.clothing" not in rules_mod.CATEGORIES
    body = (user_dir / "taxonomy.yaml").read_text()
    assert "  clothing:" not in body and "  apparel:" in body          # (the ids snapshot in the header is history)
    assert not list(user_dir.glob("*.new"))


def test_n7_an_uncommitted_rename_is_forgotten(user_dir, con):
    taxonomy.add_leaf("housing.garden", "Garden")
    t = user_dir / "taxonomy.yaml"
    before = t.read_text()
    (user_dir / "taxonomy.yaml.new").write_text("garbage")
    taxonomy.journal_path().write_text(json.dumps({"token": "never-committed", "db_id": taxonomy.db_identity(con),
                                                   "db_path": taxonomy._db_file(con), "old": "a.b", "new": "c.d", "tpath": str(t),
                                                   "rpath": str(user_dir / "rules.yaml"),
                                                   "tmp_t": str(user_dir / "taxonomy.yaml.new"),
                                                   "tmp_r": str(user_dir / "rules.yaml.new")}))
    assert "discarded" in taxonomy.recover_journal(con)
    assert t.read_text() == before and not (user_dir / "taxonomy.yaml.new").exists() and not taxonomy.journal_path().exists()


def test_n7_connect_runs_the_recovery(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "uc"))
    c = connect(cfg, insecure=True, create=True)
    (tmp_path / "uc").mkdir()
    taxonomy.journal_path().write_text(json.dumps({"token": "x", "db_id": taxonomy.db_identity(c),
                                                   "db_path": taxonomy._db_file(c), "old": "a.b",
                                                   "new": "c.d", "tpath": "t", "rpath": "r",
                                                   "tmp_t": str(tmp_path / "t.new"), "tmp_r": str(tmp_path / "r.new")}))
    c.close()
    connect(cfg, insecure=True).close()
    assert not taxonomy.journal_path().exists()


# ---- N8 / N9 / N10 / nits

def test_n8_merging_by_key_keeps_a_user_set_entity_category(con):
    for k, n in (("CAFE ONE", "Cafe One"), ("CAFE TWO", "Cafe Two"), ("BAR THREE", "Bar Three")):
        label(con, k, "food.restaurants", "llm", 0.9, n)
    entities.merge(con, ["CAFE ONE", "CAFE TWO"], "Cafes")
    entities.set_category(con, "Cafes", "food.cafes_bars")
    warnings = []
    eid = entities.merge(con, ["CAFE ONE", "BAR THREE"], "Cafes and bars", warn=warnings.append)     # keys only
    assert con.execute("SELECT category, category_user FROM merchant_entities WHERE id=?", (eid,)).fetchone() == ("food.cafes_bars", 1)
    assert resolve(con, "x", "card", "BAR THREE") == ("food.cafes_bars", "entity")
    assert warnings == []


def test_n9_every_failure_is_printed_at_the_end_of_the_run(cfg, con, monkeypatch, capsys):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO", "SHOP CHARLIE"]):
        tx(con, f"f{i}", k, amount=-100.0 + i)

    class Flaky(Rec):
        def complete(self, static, dynamic, schema, model, **kw):
            if "BRAVO" in dynamic:
                raise RuntimeError("provider said no")
            return super().complete(static, dynamic, schema, model, **kw)
    monkeypatch.setattr(cc, "get_backend", lambda c: Flaky())
    with pytest.raises(LabelRunError):
        cc.cmd_run(run_ns(batch=1, no_knn=True), cfg)
    out = capsys.readouterr().out
    assert "FAILED 1 merchant(s)" in out and "provider said no" in out


def test_n10_and_nits_validation_per_item():
    ok = {"merchant": "M", "category": "food.groceries", "confidence": 0.9, "recurring_hint": False}
    out = validate_results([{"id": 0, **{**ok, "category": ["food.groceries"]}}, {"id": 1, **{**ok, "category": {"a": 1}}},
                            {"id": 2, **{**ok, "category": 7}}, {"id": 3, **ok}], 4)
    assert [(r["id"], r["category"]) for r in out] == [(0, "other.uncategorized"), (1, "other.uncategorized"),
                                                      (2, "other.uncategorized"), (3, "food.groceries")]
    assert all(r["confidence"] <= 0.3 for r in out[:3]) and out[3]["confidence"] == 0.9
    # the first VALID entry for an id wins (an invalid first one does not block a valid second one)
    out = validate_results([{"id": 0, "merchant": "", "category": "x", "confidence": 1},
                            {"id": 0, **ok}, {"id": 0, **{**ok, "category": "health.doctors"}}], 1)
    assert [r["category"] for r in out] == ["food.groceries"]
    # booleans and strings are not confidences
    assert validate_results([{"id": 0, **{**ok, "confidence": True}}, {"id": 1, **{**ok, "confidence": "0.9"}},
                             {"id": 2, **{**ok, "confidence": 1}}], 3)[0]["id"] == 2


def test_nit_accept_refuses_a_similarity_label_with_the_right_reason(cfg, con, capsys):
    tx(con, "k1", "CAFE ZETA")
    label(con, "CAFE ZETA", "food.cafes_bars", "knn", 0.95)
    con.execute("UPDATE merchants SET model='knn:CAFE ZETA NORD' WHERE merchant_key='CAFE ZETA'")
    con.commit()
    with pytest.raises(SystemExit):
        cc.cmd_review(Namespace(insecure=True, max_conf=0.7, limit=40, json=False, accept=["CAFE ZETA"]), cfg)
    out = capsys.readouterr().out
    assert "similarity label derived from 'CAFE ZETA NORD'" in out
    assert con.execute("SELECT source FROM merchants WHERE merchant_key='CAFE ZETA'").fetchone()[0] == "knn"


def test_nit_two_collectors_never_count_or_store_the_same_result_twice(con):
    fb = pending_run(con)
    fb.pending_polls = 0
    be = make_batch_backend(fb)
    stored = []
    bid = con.execute("SELECT batch_id FROM llm_batches").fetchone()[0]
    llm.process_batch(con, be, bid, lambda k, p, m=None: stored.append(k), wait_seconds=0)
    # a second collector that read the job rows before the first one finished
    con.execute("UPDATE llm_batch_jobs SET status='pending'")
    con.execute("UPDATE llm_batches SET status='pending'")
    con.commit()
    llm.process_batch(con, be, bid, lambda k, p, m=None: stored.append(k), wait_seconds=0)
    assert sorted(map(tuple, stored)) == [("SHOP ALPHA",), ("SHOP BRAVO",)]          # stored once
    assert con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2           # counted once
    with pytest.raises(Exception):
        con.execute("INSERT INTO llm_usage(ts, backend, batch_ref) VALUES ('t','b',?)", (f"{bid}:job0",))


# ================================================================ round 4

def pkg_state():
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in (rules_mod.HERE / "taxonomy.yaml",
                                                                      rules_mod.HERE / "rules.yaml")}


def test_r4_nothing_can_write_into_the_package(user_dir, con, monkeypatch):
    before, listing = pkg_state(), sorted(p.name for p in rules_mod.HERE.iterdir())
    with pytest.raises(taxonomy.TaxonomyError, match="installed package"):
        taxonomy._atomic_write(rules_mod.HERE / "rules.yaml", "x")
    label(con, "SHOPX", "shopping.clothing")
    taxonomy.add_leaf("housing.garden", "Garden")
    taxonomy.rename_leaf(con, "pets.pets", "pets.vet")                       # rules.yaml untouched: no copy, no temp file
    with monkeypatch.context() as m:                                          # a failing swap: the restore path too
        m.setattr(taxonomy.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            taxonomy.rename_leaf(con, "shopping.clothing", "shopping.apparel")
    assert pkg_state() == before                                              # bytes AND modification times
    assert sorted(p.name for p in rules_mod.HERE.iterdir()) == listing         # no .new / .tmp left in src/
    assert not (user_dir / "rules.yaml").exists()


# ---- NEW-1 / NEW-2 / NEW-4: the journal

def crash_rename(con, monkeypatch, old="shopping.clothing", new="shopping.apparel"):
    with monkeypatch.context() as m:
        m.setattr(taxonomy.os, "replace", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            taxonomy.rename_leaf(con, old, new)


def test_new1_a_journal_belongs_to_one_database(cfg, user_dir, con, monkeypatch, capsys):
    label(con, "SHOPX", "shopping.clothing")
    crash_rename(con, monkeypatch)
    jp = taxonomy.journal_path()
    other = connect(cfg, insecure=True, path=cfg.db_path.with_name("other.db"), create=True)   # ANOTHER database
    capsys.readouterr()
    assert taxonomy.recover_journal(other) is None
    assert jp.exists() and "belongs to another database" in capsys.readouterr().err
    assert "  clothing:" in (user_dir / "taxonomy.yaml").read_text()                 # nothing decided for it
    with pytest.raises(taxonomy.TaxonomyError, match="interrupted rename"):         # and edits wait for the owner
        taxonomy.add_leaf("housing.garden", "Garden")
    assert "completed" in taxonomy.recover_journal(con) and not jp.exists()           # its own database resolves it
    assert "  apparel:" in (user_dir / "taxonomy.yaml").read_text()


def test_new2_recovery_reapplies_the_rename_to_the_current_files(user_dir, con, monkeypatch):
    label(con, "SHOPX", "shopping.clothing")
    crash_rename(con, monkeypatch)
    t = user_dir / "taxonomy.yaml"
    t.write_text(t.read_text().replace("\nother:", "\nmine:\n  custom: Added later\nother:"))   # a later edit
    assert "completed" in taxonomy.recover_journal(con)
    text = t.read_text()
    assert "  custom: Added later" in text and "  apparel:" in text and "  clothing:" not in text
    assert set(rules_mod.CATEGORIES) >= {"mine.custom", "shopping.apparel"}


def test_new2_a_rename_already_in_the_files_is_not_repeated(user_dir, con, monkeypatch, capsys):
    label(con, "SHOPX", "shopping.clothing")
    crash_rename(con, monkeypatch)
    t = user_dir / "taxonomy.yaml"
    t.write_text(t.read_text().replace("  clothing:", "  apparel:"))                  # the swap had in fact happened
    before = t.read_text()
    capsys.readouterr()
    assert taxonomy.recover_journal(con) == "already applied"
    assert t.read_text() == before and not taxonomy.journal_path().exists() and capsys.readouterr().err == ""


def test_new4_a_damaged_journal_is_quarantined_with_a_warning_and_never_blocks(cfg, user_dir, con, capsys):
    jp = taxonomy.journal_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    for body in ('{"token": "x"}', "not json at all", "[1, 2]", '{"token":"x","db_id":"d","old":1,"new":"a.b","tpath":"t","rpath":"r","tmp_t":"a","tmp_r":"b"}'):
        jp.write_text(body)
        capsys.readouterr()
        connect(cfg, insecure=True).close()                                         # must not raise
        err = capsys.readouterr().err
        assert "unusable" in err and not jp.exists(), body
    assert len(list(jp.parent.glob(".taxonomy-journal.json.bad-*"))) >= 1
    taxonomy.add_leaf("housing.garden", "Garden")                                    # editing works again


# ---- NEW-3: merge-package baseline

def test_new3_merge_package_never_brings_back_rules_the_user_deleted_or_edited(user_dir, con):
    taxonomy.rename_leaf(con, "subscriptions.video_streaming", "subscriptions.video")      # copies rules.yaml + baseline
    rp = user_dir / "rules.yaml"
    assert rules_mod.RULES_HEADER in rp.read_text().split("\n", 3)[1] + "\n"
    text = rp.read_text()
    head, body = text.split("merchant_rules:\n", 1)
    netflix = "  - match: '\\bNETFLIX\\b'\n    category: subscriptions.video\n"
    assert netflix in body
    body = body.replace(netflix, "")                                                      # the user DELETED this rule
    body = body.replace("'\\bSPOTIFY\\b'", "'\\bSPOTIFY PREMIUM\\b'")                         # and EDITED this one
    rp.write_text(head + "merchant_rules:\n" + body)
    rules_mod.reload_taxonomy()
    n_before = len(yaml_load(rp)["merchant_rules"])
    r = taxonomy.merge_package()
    assert r["merchant_rules"] == [] and r["type_rules"] == []
    after = yaml_load(rp)["merchant_rules"]
    assert len(after) == n_before
    assert not any("NETFLIX" in m["match"] for m in after) and any("SPOTIFY PREMIUM" in m["match"] for m in after)
    assert not any(m["match"] == "\\bSPOTIFY\\b" for m in after)


def test_new3_a_copy_without_a_baseline_merges_no_rules(user_dir, con):
    user_dir.mkdir(parents=True)
    (user_dir / "rules.yaml").write_text((rules_mod.HERE / "rules.yaml").read_text().replace(
        "  - match: '\\bNETFLIX\\b'\n    category: subscriptions.video_streaming\n", ""))
    rules_mod.reload_taxonomy()
    r = taxonomy.merge_package()
    assert r["merchant_rules"] == [] and any("does not record" in x for x in r["skipped"])
    assert not any("NETFLIX" in m["match"] for m in yaml_load(user_dir / "rules.yaml")["merchant_rules"])


# ---- reason tags

@pytest.mark.parametrize("text,key,tx_type", [
    ("PRLV SEPA EDF MOTIF: SEANCE PSY", "EDF", "direct_debit"),
    ("PRLV SEPA /MOTIF PSY SEANCE /DE ACME SARL", "ACME SARL", "direct_debit"),
    ("PRLV SEPA /MOTIF PSY SEANCE", "", "direct_debit"),
    ("PRLV SEPA EDF /RI 12 /LIB SEANCE", "EDF", "direct_debit"),
    ("PRLV SEPA EDF REF: 123 MOTIF SEANCE", "EDF", "direct_debit"),
    ("VIR SEPA ACME SARL MOTIF THERAPIE", "ACME SARL", "transfer_out"),
    ("VIR SEPA RECU ACME SARL REF: 12 MOTIF X", "ACME SARL", "transfer_out"),
    ("VIR SEPA RECU /DE ACME SARL /RI 77 /LIB LOYER", "ACME SARL", "transfer_out"),
    ("VIR SEPA RECU /LIB LOYER /RI 77", "", "transfer_out")])
def test_r4_reason_tags_in_every_shape(text, key, tx_type):
    r = parse_tx(RawTx(text, -50.0, "2026-03-02"), Household(), bank="Caisse d'Epargne X")
    assert (r["merchant_key"], r["tx_type"]) == (key, tx_type), text
    for leaked in ("SEANCE", "PSY", "THERAPIE", "LOYER"):
        assert leaked not in r["merchant_raw"].upper()


# ---- NEW-5 / NEW-6 / N9

def test_new5_abandon_needs_a_provider_listing_attaches_what_it_finds_and_names_what_it_drops(cfg, con, monkeypatch, capsys):
    fb = submit_crash(con, monkeypatch, ConnectionError("dropped"))
    be = make_batch_backend(fb)
    # a legacy row of ANOTHER backend must not be touched
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('fp2','other_backend_batch','ollama','2026-01-01','pending',1)")
    con.commit()
    fb.list = lambda limit=20: (_ for _ in ()).throw(ConnectionError("provider unreachable"))
    with pytest.raises(backends.BatchUnresolved, match="nothing abandoned"):
        llm.abandon_unrecorded(con, be)
    assert con.execute("SELECT status FROM llm_batches WHERE batch_id LIKE 'submitting-%'").fetchone()[0] == "submitting"
    # the listing works and the batch IS there: it is attached, not abandoned
    fb.list = FB.list.__get__(fb)
    fb.n = 2
    assert llm.abandon_unrecorded(con, be) == []
    assert con.execute("SELECT status FROM llm_batches WHERE batch_id='batch_X'").fetchone()[0] == "pending"
    # nothing matches: it is abandoned, named, with its own wording
    con.execute("DELETE FROM llm_batches WHERE batch_id='batch_X'")
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('submitting-zzz','submitting-zzz','anthropic-api','2026-01-01T00:00:00','submitting',7)")
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('legacy1','legacy1','anthropic-api','2026-01-01','pending',3)")
    con.commit()
    out = dict(llm.abandon_unrecorded(con, be))
    assert "without its id being recorded" in out["submitting-zzz"] and "no saved request mapping" in out["legacy1"]
    assert "submitting-zzz" in out and con.execute("SELECT status FROM llm_batches WHERE batch_id='other_backend_batch'").fetchone()[0] == "pending"


def test_new6_pending_batches_of_unknown_purpose_are_not_collected_as_labels(cfg, con, monkeypatch, capsys):
    fb = pending_run(con)
    con.execute("UPDATE llm_batches SET purpose=NULL")                  # as left by a version before 0010
    con.commit()
    fb.pending_polls = 0
    be = make_batch_backend(fb)
    got = []
    n = llm.collect_pending_batches(con, be, lambda k, p, m=None: got.extend(k))
    assert n == 0 and got == [] and "before batch purposes were recorded" in capsys.readouterr().out
    assert con.execute("SELECT status FROM llm_batches").fetchone()[0] == "pending"
    assert llm.claim_legacy_batches(con, be, "label") == 1
    n = llm.collect_pending_batches(con, be, lambda k, p, m=None: got.extend(k))
    assert n == 2 and sorted(got) == ["SHOP ALPHA", "SHOP BRAVO"]


def test_n9_compare_prints_what_it_could_not_store(cfg, con, monkeypatch, capsys):
    for i, k in enumerate(["SHOP ALPHA", "SHOP BRAVO"]):
        tx(con, f"c{i}", k)
        label(con, k, "shopping.clothing", "llm", 0.9)
    fb = FB(lambda n: [("job0", "ok"), ("job1", "errored")], pending_polls=100)
    be = make_batch_backend(fb)
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    cc.cmd_compare(cmp_ns(), cfg)
    fb.pending_polls = 0
    be.complete = lambda **j: (_ for _ in ()).throw(RuntimeError("no"))
    capsys.readouterr()
    with pytest.raises(LabelRunError):                               # the failed merchant is asked again and fails too
        cc.cmd_compare(cmp_ns(), cfg)
    assert "not stored: 1 merchant(s)" in capsys.readouterr().out


# ---- nits

def test_r4_strict_booleans_and_text_merchants():
    ok = {"merchant": "M", "category": "food.groceries", "confidence": 0.9}
    out = validate_results([{"id": 0, **ok, "recurring_hint": "false"}, {"id": 1, **ok, "recurring_hint": "true"},
                            {"id": 2, **ok, "recurring_hint": 0}, {"id": 3, **ok, "recurring_hint": None},
                            {"id": 4, **{**ok, "merchant": 123}}, {"id": 5, **{**ok, "merchant": ["x"]}}], 6)
    assert [(r["id"], r["recurring_hint"]) for r in out] == [(0, False), (1, True), (2, False), (3, False)]


def test_r4_recover_submitting_treats_naive_timestamps_as_utc(con):
    fb = FB(lambda n: [("job0", "ok"), ("job1", "ok")])
    be = make_batch_backend(fb)
    fb.n = 2
    for created in ("2026-01-01T00:00:00", "2025-12-31T23:59:59+00:00"):      # naive and aware, both before 2999
        con.execute("DELETE FROM llm_batch_jobs")
        con.execute("DELETE FROM llm_batches")
        con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                    "VALUES ('submitting-a','submitting-a','anthropic-api',?,'submitting',2)", (created,))
        con.commit()
        llm.recover_submitting(con, be)
        assert con.execute("SELECT status, batch_id FROM llm_batches").fetchone() == ("pending", "batch_X")


def test_r4_a_conflicting_reference_listed_twice_identically_gives_one_content_row(con):
    d = "2026-03-02"
    sync(con, eb_tx("R-C", d, -10.0, "CB ONE"))
    res, _ = sync(con, eb_tx("R-C", d, -10.0, "CB OTHER"), eb_tx("R-C", d, -10.0, "CB OTHER"))
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2 and len(res["conflicts"]) == 1


def test_r4_migration_0010_is_idempotent(cfg):
    import importlib.util
    c = connect(cfg, insecure=True, create=True)
    spec = importlib.util.spec_from_file_location("m10", dbm.MIGRATIONS_DIR / "0010_batch_purpose_usage_ref_rename_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run(c)
    mod.run(c)                                              # a second run changes nothing and does not fail
    assert any(r[1] == "purpose" for r in c.execute("PRAGMA table_info(llm_batches)"))


# ================================================================ round 5

def test_h1_the_recovery_flags_work_end_to_end_through_main(cfg, con, monkeypatch, capsys):
    from coach.cli import main
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('l','legacy_b','anthropic-api','2026-01-01','pending',2)")
    con.execute("INSERT INTO llm_batches(fingerprint, batch_id, backend, created_at, status, jobs) "
                "VALUES ('u','unknown_b','anthropic-api','2026-01-01','pending',1)")
    con.execute("INSERT INTO llm_batch_jobs(batch_id, custom_id, keys_json, model) VALUES ('unknown_b','job0','[\"X\"]','haiku')")
    con.commit()
    be = make_batch_backend(FB(lambda n: []))
    monkeypatch.setattr(cc, "get_backend", lambda c: be)
    # L3: claiming runs BEFORE abandoning, so the unknown-purpose batch is claimed (and kept), not abandoned
    main(["--config", str(cfg.config_path), "--insecure", "classify", "run", "--no-knn",
          "--claim-legacy-batches", "compare", "--abandon-unrecorded"])
    out = capsys.readouterr().out
    assert "now count as 'compare'" in out and "abandoned batch legacy_b" in out and "unknown_b" not in out.split("abandoned batch")[-1]
    st = dict(con.execute("SELECT batch_id, status FROM llm_batches"))
    assert st == {"legacy_b": "abandoned", "unknown_b": "pending"}
    assert con.execute("SELECT purpose FROM llm_batches WHERE batch_id='unknown_b'").fetchone()[0] == "compare"


# ---- M2: nothing is written into the package, whatever the environment says

def test_m2_paths_inside_the_package_are_refused(monkeypatch, tmp_path):
    pkg = rules_mod.HERE
    for var, value in (("COACH_TAXONOMY_FILE", pkg / "taxonomy.yaml"), ("COACH_RULES_FILE", pkg / "rules.yaml"),
                       ("COACH_CONFIG_DIR", pkg), ("COACH_CONFIG_DIR", pkg / "sub")):
        monkeypatch.setenv(var, str(value))
        with pytest.raises(rules_mod.UnsafeConfigPath):
            rules_mod.check_paths()
        monkeypatch.undo()
        monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "ok"))
    # a symlink into the package is resolved
    link = tmp_path / "link.yaml"
    link.symlink_to(pkg / "taxonomy.yaml")
    monkeypatch.setenv("COACH_TAXONOMY_FILE", str(link))
    with pytest.raises(rules_mod.UnsafeConfigPath):
        rules_mod.taxonomy_file()
    monkeypatch.delenv("COACH_TAXONOMY_FILE")
    rules_mod.reload_taxonomy()
    assert "food.groceries" in rules_mod.CATEGORIES            # reading the defaults still works after a refusal


def test_m2_the_cli_refuses_to_start_with_such_a_path(cfg, monkeypatch):
    from coach.cli import main
    monkeypatch.setenv("COACH_CONFIG_DIR", str(rules_mod.HERE))
    with pytest.raises(SystemExit) as e:
        main(["--config", str(cfg.config_path), "--insecure", "taxonomy", "list"])
    assert "inside the installed package" in str(e.value)


def test_m2_every_write_and_swap_is_guarded(user_dir, con, monkeypatch):
    pkg_t = rules_mod.HERE / "taxonomy.yaml"
    for fn in (lambda: taxonomy._write(pkg_t.with_name("x.new"), "x"), lambda: taxonomy._replace(user_dir / "a", pkg_t),
               lambda: taxonomy._replace(pkg_t, user_dir / "a"), lambda: taxonomy._atomic_write(pkg_t, "x")):
        with pytest.raises(taxonomy.TaxonomyError, match="installed package"):
            fn()
    assert not (rules_mod.HERE / "x.new").exists()


# ---- M1: a copy of the database is not the database

def test_m1_a_backup_copy_with_the_same_identity_never_resolves_the_journal(cfg, user_dir, con, monkeypatch, capsys):
    label(con, "SHOPX", "shopping.clothing")
    crash_rename(con, monkeypatch)
    con.commit()
    copy = cfg.db_path.with_name("copy-of-finance.db")
    shutil.copyfile(cfg.db_path, copy)
    dup = connect(cfg, insecure=True, path=copy)
    assert taxonomy._existing_db_id(dup) == taxonomy._existing_db_id(con)          # same id: it IS a plain copy
    capsys.readouterr()
    assert taxonomy.recover_journal(dup) is None
    assert taxonomy.journal_path().exists() and "belongs to another database" in capsys.readouterr().err
    assert "completed" in taxonomy.recover_journal(con)                              # the original resolves it


def test_m1_restore_and_encrypt_give_the_output_a_new_identity(cfg, db_key, tmp_path, fake_keyring, monkeypatch):
    from coach import backup as backup_mod, secrets
    from coach.cli import cmd_restore
    secrets.set_secret("backup_key", "k-backup")
    c = connect(cfg, create=True)
    original = taxonomy.db_identity(c)
    c.close()
    path, _ = backup_mod.create_backup(cfg)
    target = tmp_path / "restored"
    cmd_restore(Namespace(archive=str(path), to=str(target), force=False), cfg)
    restored = next(target.rglob("*.db"))
    r = connect(cfg, path=restored)
    assert taxonomy.db_identity(r) != original
    # encrypting a plaintext database: the encrypted file is a new database; a pending journal follows it
    plain_cfg = cfg
    plain = cfg.db_path.with_name("plain.db")
    monkeypatch.delenv("COACH_DB_KEY")                          # a plaintext database (no key available at creation)
    p = dbm.connect(cfg, insecure=True, path=plain, create=True)
    pid = taxonomy.db_identity(p)
    monkeypatch.setenv("COACH_DB_KEY", "test-db-key")
    p.close()
    jp = taxonomy.journal_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps({"token": "t", "db_id": pid, "db_path": str(plain.resolve()), "old": "a.b", "new": "c.d",
                              "tpath": "t", "rpath": "r", "tmp_t": "a", "tmp_r": "b"}))
    dbm.encrypt_database(cfg, path=plain)
    journal_id = json.loads(jp.read_text())["db_id"]                       # (before any connect resolves it)
    e = dbm.connect(cfg, path=plain)
    new_id = taxonomy._existing_db_id(e)
    assert new_id != pid and journal_id == new_id


# ---- L1 / L2

def test_l1_a_missing_taxonomy_copy_quarantines_the_journal_instead_of_deleting_it(user_dir, con, monkeypatch, capsys):
    label(con, "SHOPX", "shopping.clothing")
    crash_rename(con, monkeypatch)
    (user_dir / "taxonomy.yaml").unlink()
    capsys.readouterr()
    assert taxonomy.recover_journal(con) is None
    assert not taxonomy.journal_path().exists() and "no longer exists" in capsys.readouterr().err
    assert list(taxonomy.journal_path().parent.glob(".taxonomy-journal.json.bad-*"))


def test_l2_an_unreachable_provider_is_reported_as_such(con):
    fb = submit_crash(con, None, ConnectionError("dropped"))
    be = make_batch_backend(fb)
    fb.list = lambda limit=20: (_ for _ in ()).throw(ConnectionError("offline"))
    with pytest.raises(backends.BatchUnresolved) as e:
        llm.collect_pending_batches(con, be, lambda *a: None)
    msg = str(e.value)
    assert "provider unreachable" in msg and "Retry later" in msg and "--abandon-unrecorded" not in msg
    assert "0 candidate" not in msg


# ---- L4: purpose rules in merge-package

def test_l4_merge_package_handles_account_purpose_rules_with_the_same_baseline(user_dir, con):
    taxonomy.rename_leaf(con, "subscriptions.video_streaming", "subscriptions.video")    # copy with a baseline
    rp = user_dir / "rules.yaml"
    lines = rp.read_text().splitlines()
    head = [ln for ln in lines if ln.startswith("#")]
    base = [ln for ln in head if ln.startswith(rules_mod.RULES_HEADER)][0]
    # as if the package had gained the rental rule since the copy: remove it from the copy AND from the baseline
    text = rp.read_text().replace("type_rules_by_account_purpose:\n  rental:\n    loan_payment: housing.rental_property_loan\n", "")
    text = text.replace(base, base.replace(",purpose:rental:loan_payment", ""))
    rp.write_text(text)
    rules_mod.reload_taxonomy()
    assert "type_rules_by_account_purpose" not in yaml_load(rp) or not yaml_load(rp)["type_rules_by_account_purpose"]
    r = taxonomy.merge_package()
    assert r["purpose_rules"] == ["rental/loan_payment"]
    assert yaml_load(rp)["type_rules_by_account_purpose"] == {"rental": {"loan_payment": "housing.rental_property_loan"}}
    assert taxonomy.merge_package().get("purpose_rules", []) == []                      # idempotent
    # a purpose rule the user DELETED (it is in the baseline) does not come back
    rp.write_text(rp.read_text().replace("type_rules_by_account_purpose:\n  rental:\n    loan_payment: housing.rental_property_loan\n", ""))
    rules_mod.reload_taxonomy()
    assert taxonomy.merge_package().get("purpose_rules", []) == []
    # a purpose block that already exists gets the new key inside it
    rp.write_text(rp.read_text().replace("merchant_rules:\n", "type_rules_by_account_purpose:\n  rental:\n    atm: cash.atm_withdrawal\n\nmerchant_rules:\n", 1))
    rp.write_text(rp.read_text().replace(rules_mod.RULES_HEADER, rules_mod.RULES_HEADER + "x,", 1).replace(",purpose:rental:loan_payment", ""))
    rules_mod.reload_taxonomy()
    r = taxonomy.merge_package()
    assert yaml_load(rp)["type_rules_by_account_purpose"]["rental"] == {"atm": "cash.atm_withdrawal",
                                                                        "loan_payment": "housing.rental_property_loan"}


# ---- nits

def test_r5_incomplete_provider_listing_entries_are_skipped_with_a_warning(capsys):
    good = SimpleNamespace(id="b1", created_at="2999-01-01T00:00:00Z",
                           request_counts=SimpleNamespace(processing=2, succeeded=0, errored=0, canceled=0, expired=0))
    bad = [SimpleNamespace(id=None, created_at="x", request_counts=good.request_counts),
           SimpleNamespace(id="b2", created_at="x", request_counts=None), SimpleNamespace(created_at="x")]
    client = SimpleNamespace(messages=SimpleNamespace(batches=SimpleNamespace(list=lambda limit=20: [*bad, good])))
    out = backends.AnthropicBackend(client=client).list_recent_batches()
    assert out == [{"id": "b1", "created_at": "2999-01-01T00:00:00Z", "requests": 2}]
    assert capsys.readouterr().out.count("skipping a batch") == 3


def test_r5_fortuneo_pipe_separated_lines_get_the_reason_cut_too():
    h = Household()
    for text in ("VIR SEPA ACME SARL | MOTIF: THERAPIE", "VIR INST ACME SARL | /MOTIF THERAPIE", "VIR SEPA ACME SARL  MOTIF THERAPIE"):
        r = parse_tx(RawTx(text, -50.0, "2026-03-02"), h, bank="Fortuneo")
        assert r["merchant_key"] == "ACME SARL" and "THERAPIE" not in r["merchant_raw"], text
