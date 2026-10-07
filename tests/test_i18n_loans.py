"""i18n step 4f: the loans and net worth sentences carry, next to their English, a message the web translates (`<field>_msg`:
{code, params, text}); the loan fields, the prepayment options and the verdicts are codes the web looks up (labels.loanField / loanOption /
loanVerdict). The MCP finance tools (net_worth, loans_overview, mortgage_check, what_if) and the loans CLI's --json keep the English only.

Synthetic data only."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace as NS

from coach.api.routes.wealth import LOAN_FIELD_LABEL
from coach.loans import loa as LOA, networth as NW, scenario as SC, schedule as S, service as LS
from mcphelpers import payload, session, world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("en", "fr", "it")


def _bundle(lang: str) -> dict:
    return json.loads((ROOT / "web" / "src" / "locales" / lang / "server.json").read_text(encoding="utf-8"))


def _has(bundle: dict, code: str) -> bool:
    *path, last = code.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return False
    return isinstance(node, dict) and (isinstance(node.get(last), str) or isinstance(node.get(f"{last}_other"), str))


def _msgs(obj, path="$"):
    """Every (path, english sibling, message) of a payload: `x_msg` next to `x`, lists aligned."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_msg"):
                yield f"{path}.{k}", obj.get(k[:-4]), v
            else:
                yield from _msgs(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _msgs(v, f"{path}[{i}]")


def _check_aligned_and_known(payload_: dict) -> set:
    """The English text of every message is its sibling field, and every code is known in every language. Returns the codes seen."""
    bundles = {lang: _bundle(lang) for lang in LANGS}
    seen = set()
    for where, english, msg in _msgs(payload_):
        pairs = list(zip(english, msg)) if isinstance(msg, list) else [(english, msg)]
        if isinstance(msg, list):
            assert len(english) == len(msg), where
        for text, m in pairs:
            if m is None:
                continue
            assert m["text"] == text, where
            for lang, b in bundles.items():
                assert _has(b, m["code"]), (where, lang, m["code"])
            seen.add(m["code"])
    return seen


def _keys(obj, out=None) -> set:
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


# ---------------------------------------------------------------- the codes built at run time and the label families

def test_the_codes_built_at_run_time_exist_in_every_language():
    codes = [f"netWorth.{b}{d}" for b in ("fromSchedule", "fromScheduleApprox", "declaredRolled", "declaredAsIs") for d in ("", "Differs")]
    codes += [m["code"] for m in LOA.checklist_msgs()]
    codes += [f()["code"] for f in LS.PAYMENT_SOURCE.values()]
    for lang in LANGS:
        b = _bundle(lang)
        assert [c for c in codes if not _has(b, c)] == [], lang


def test_the_checklist_messages_are_the_english_checklist():
    assert [m["text"] for m in LOA.checklist_msgs()] == LOA.RESTITUTION_CHECKLIST


def test_every_loan_label_family_is_translated_and_the_english_is_the_servers():
    from coach.skills.loans import insurance_delegation_estimate, renegotiation_estimate   # noqa: F401 - the verdict codes they return
    verdicts = set(SC.PREPAY_VERDICT) | {"no_saving", "costs_exceed_saving", "break_even_after_the_loan_ends", "worth_asking_for_quotes",
                                         "small_saving_check_the_costs", "fees_exceed_saving", "saving"}
    for lang in LANGS:
        labels = _bundle(lang)["labels"]
        assert set(LOAN_FIELD_LABEL) <= set(labels["loanField"]), lang
        assert set(SC.OPTION_LABEL) <= set(labels["loanOption"]), lang
        assert verdicts <= set(labels["loanVerdict"]), lang
    en = _bundle("en")["labels"]
    assert {k: en["loanField"][k] for k in LOAN_FIELD_LABEL} == LOAN_FIELD_LABEL
    assert {k: en["loanOption"][k] for k in SC.OPTION_LABEL} == SC.OPTION_LABEL
    assert {k: en["loanVerdict"][k] for k in SC.PREPAY_VERDICT} == SC.PREPAY_VERDICT


def test_the_renegotiation_and_insurance_verdicts_are_label_codes():
    from coach.skills.loans import insurance_delegation_estimate, renegotiation_estimate
    labels = _bundle("fr")["labels"]["loanVerdict"]
    r = renegotiation_estimate(current_rate_pct=3.5, new_rate_pct=2.5, remaining_capital=150000, remaining_months=180, country="FR")
    i = insurance_delegation_estimate(current_monthly=50, alternative_monthly=20, remaining_months=120, country="FR")
    assert r.verdict in labels and i.verdict in labels


# ---------------------------------------------------------------- the pure modules

def _loan(**kw):
    base = dict(id="l1", kind="mortgage", lender="X", principal=100000, rate=NS(type="variable", nominal=3.0, taeg=None), start_date=dt.date(2024, 1, 15),
                end_date=None, term_months=240, first_payment_date=None, payment_day=5, outstanding=None, outstanding_as_of=None,
                monthly_payment=900.0, insurance=None, deferral=NS(months=3, kind="partial"), holder=None, holders=None)
    base.update(kw)
    return NS(**base)


def test_a_schedule_sends_its_assumptions_hints_and_missing_fields_as_messages():
    sch = S.compute(_loan(), dt.date(2026, 10, 4))
    d = sch.to_dict()
    assert d["status"] == "computed" and len(d["assumptions"]) == len(d["assumptions_msg"]) >= 4
    assert d["assumptions"][0] == "annuity loan, interest = capital x nominal rate / 12 per instalment"           # the English is unchanged
    assert d["assumptions_msg"][1] == {"code": "schedule.firstDueDay", "params": {"first_date": "2024-02-05", "day": 5},
                                       "text": "first instalment due 2024-02-05, debited on day 5"}
    assert d["assumptions_msg"][2]["code"] == "schedule.deferralPartial" and d["assumptions_msg"][2]["params"] == {"count": 3}
    assert d["payment_check"]["status"] == "differs" and d["payment_check"]["hint_msg"]["code"] == "schedule.paymentDiffersHint"
    seen = _check_aligned_and_known(d)
    assert "schedule.variableRate" in seen
    missing = S.compute(_loan(principal=None, start_date=None, rate=None), dt.date(2026, 10, 4)).to_dict()
    assert missing["missing"] == ["principal", "rate.nominal", "start_date"]
    assert [m["code"] for m in missing["missing_msg"]] == ["schedule.missing.principal", "schedule.missing.rateNominal", "schedule.missing.startDate"]
    lease = S.compute(_loan(kind="loa"), dt.date(2026, 10, 4)).to_dict()
    assert lease["alternative_msg"]["code"] == "schedule.leaseAlternative" and lease["alternative"].endswith("(`coach loans lease`)")


def test_a_prepayment_sends_its_penalty_basis_options_and_notes_as_codes():
    lb = _loan(rate=NS(type="fixed", nominal=3.0, taeg=None), deferral=None, insurance=NS(monthly=20.0, rate_pct=None, basis=None))
    sch = S.compute(lb, dt.date(2026, 10, 4))
    r = SC.prepay(lb, sch, dt.date(2026, 10, 4), 10000, country="FR")
    assert r["status"] == "computed" and r["penalty_basis"].startswith("IRA (Code de la consommation L313-47): the lower of six months")
    assert r["penalty_basis_msg"]["code"] == "scenario.penalty.ira" and set(r["penalty_basis_msg"]["params"]) == {"six_months_amount", "cap_amount"}
    assert [o["verdict_code"] for o in r["options"]] == ["saves_money", "saves_money"]
    assert r["options"][0]["verdict"] == "saves money over the life of the loan" and r["options"][0]["label"] == "keep the instalment, finish earlier"
    assert r["notes"][-1].startswith("estimate from the stored schedule at the current nominal rate; the lender's table decides. Estimate from")
    assert r["notes_msg"][-1]["code"] == "scenario.estimate" and r["disclaimer_key"] == "loan"
    assert "scenario.flatInsurance" in _check_aligned_and_known(r)
    needs = SC.prepay(_loan(principal=None), S.compute(_loan(principal=None), dt.date(2026, 10, 4)), dt.date(2026, 10, 4), 1000)
    assert needs["status"] == "needs_fields" and needs["note_msg"]["code"] == "scenario.needsSchedule" and needs["missing_msg"]
    _check_aligned_and_known(needs)


def test_the_net_worth_unknowns_and_notes_carry_messages():
    asset = NS(id="a1", kind="vehicle", description="Car", holder=None, holders=None, connected=False, amount=None, as_of=None)
    ds = NS(today=dt.date(2026, 10, 4), accounts_in=lambda _x: [], memory=NS(assets=[asset], liabilities=[("l.yaml", _loan(principal=None))]))
    nw = NW.build(ds, dt.date(2026, 10, 4)).to_dict()
    assert [u["reason"] for u in nw["unknown"]] == ["no value recorded", "capital unknown: principal"]
    assert [u["reason_msg"]["code"] for u in nw["unknown"]] == ["netWorth.noValue", "netWorth.capitalUnknownFields"]
    assert nw["notes_msg"][0]["code"] == "netWorth.knownOnly"
    _check_aligned_and_known(nw)


# ---------------------------------------------------------------- what a model and the CLI read

def test_the_loan_mcp_tools_carry_no_msg_and_keep_their_english(cfg, world, session):
    from test_loans_tools import setup_memory
    setup_memory(cfg, world)
    outs = {"net_worth": session.call("net_worth", {"history": True, "months": 6}), "loans_overview": session.call("loans_overview", {}),
            "mortgage_check": session.call("mortgage_check", {"market_rate_pct": 2.5, "market_rate_date": "2026-10-01"}),
            "what_if": session.call("what_if", {"scenario": {"changes": [{"type": "adjust_category", "category": "food", "percent": -10}]}})}
    for name, r in outs.items():
        assert r.ok, name
        assert not [k for k in _keys(json.loads(r.text)) if k.endswith("_msg")], name
        assert "verdict_code" not in r.text and "disclaimer_key" not in r.text, name
    nw = payload(outs["net_worth"])
    assert "no value recorded" in [u["reason"] for u in nw["unknown"]]                     # the English reasons, as before
    lease = next(c for c in nw["components"] if c["label"] == "car lease (LOA)")
    assert lease["status"] == "excluded"
    mc = payload(outs["mortgage_check"])
    said = {a for x in mc["mortgages"] for a in (x["state"].get("schedule") or {}).get("assumptions", [])}
    assert {"annuity loan, interest = capital x nominal rate / 12 per instalment",
            "the declared capital differs from the theoretical table: the declared figure is rolled forward from its date"} <= said
    lo = payload(outs["loans_overview"])
    lease_row = next(x for x in lo["loans"] if x["kind"] == "loa")
    assert "needs_msg" not in lease_row["lease"]["mileage"]


def test_the_loans_cli_json_keeps_the_english_only():
    from coach.loans.commands import _j
    sch = S.compute(_loan(), dt.date(2026, 10, 4))
    d = json.loads(_j(sch))
    assert d["assumptions"][0].startswith("annuity loan") and not [k for k in _keys(d) if k.endswith("_msg")]
