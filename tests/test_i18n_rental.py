"""i18n step 4g: the rental property's server text (scheme, indicators, tax year, reduction, links) carries, next to its English, the
messages the web translates (`<field>_msg`), and the disclaimer KEYS the web shows in its language (their wording stays in
coach/disclaimers.py). The MCP tool `rental_overview`, `tax_candidates` and the CLI's --json keep the English only, unchanged.

Synthetic data only (tests/rentalhelpers.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import coach.mcp.tools  # noqa: F401  (the tool modules import each other: this one first)
from apihelpers import PORT, TODAY, Ctx, make_client, static_dir
from coach import disclaimers as D
from coach.api.app import create_app
from coach.mcp.tools import ToolSession
from rentalhelpers import rental_world

ROOT = Path(__file__).resolve().parents[1]
GENERAL = D.GENERAL_ADVICE["en"]


def _bundle(lang: str) -> dict:
    return json.loads((ROOT / "web" / "src" / "locales" / lang / "server.json").read_text(encoding="utf-8"))


def _lookup(bundle: dict, code: str) -> list[str]:
    """The string(s) of a code: the key itself or its plural forms."""
    *path, last = code.split(".")
    node = bundle
    for p in path:
        node = node[p]
    return [v for k, v in node.items() if k == last or re.fullmatch(rf"{last}_(one|many|other)", k)]


def _msgs(obj, path="") -> list[tuple[str, dict, object]]:
    """Every (path, message, English sibling) of a payload."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_msg"):
                eng = obj.get(k[:-4])
                if isinstance(v, list):
                    out += [(f"{path}.{k}[{i}]", m, eng[i] if isinstance(eng, list) and i < len(eng) else None) for i, m in enumerate(v) if m]
                elif v:
                    out.append((f"{path}.{k}", v, eng))
            else:
                out += _msgs(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _msgs(v, f"{path}[{i}]")
    return out


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


# a property with few facts: no loan, no value, no rent, no prices, an unusual length, a commitment ending within the reminder window
SPARSE_ASSET = """
  - id: rental-flat-1
    kind: real_estate_rental
    account: Rentbank courant
    scheme: pinel
    commitment:
      start_date: 2017-01-01
      years: 10
"""


def _ctx(cfg, tmp_path, sparse: bool = False) -> Ctx:
    con = rental_world(cfg, loan=False, asset=SPARSE_ASSET) if sparse else rental_world(cfg)
    con.close()
    app = create_app(cfg, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    return Ctx(app, make_client(app), cfg)


@pytest.fixture
def rctx(cfg, tmp_path):
    return _ctx(cfg, tmp_path)


def _api_payloads(c) -> list[dict]:
    pid = "rental-flat-1"
    return [c.get(f"/rental/{pid}").json(), c.get(f"/rental/{pid}/tax", year=2026).json(), c.get(f"/rental/{pid}/tax", year=2025).json(),
            c.get(f"/rental/{pid}/indicators", market_rate=1.0, market_date="2026-01-01").json(),
            c.get(f"/rental/{pid}/indicators", market_rate=3.4).json(), c.get(f"/rental/{pid}/indicators").json()]


EXPECTED = {
    False: {"rental.basis.capMonthly", "rental.basis.rentDeclared", "rental.scheme.endComputed", "rental.tax.item.interest", "rental.tax.note.yearInProgress",
            "rental.tax.deficit", "rental.reduction.verify", "rental.rate.above", "rental.rate.close", "rental.market.old", "rental.market.missing",
            "rental.signal.commitmentRunning", "rental.signal.equityPositive", "rental.scenario.prepay", "rental.equity.beforeCosts"},
    True: {"rental.basis.rentObserved", "rental.need.loan", "rental.need.value", "rental.need.rentMonthly", "rental.need.purchase", "rental.need.rentCap",
           "rental.need.tenantIncome", "rental.need.reductionRate", "rental.scheme.pinelYears", "rental.signal.commitmentEnding",
           "rental.rate.missingLoan", "rental.equity.missingValue", "rental.equity.missingLoan", "rental.tax.unknownInterest"},
}


@pytest.mark.parametrize("sparse", [False, True])
def test_every_message_of_the_api_is_its_english_sibling_and_known_in_every_language(cfg, tmp_path, sparse):
    bundles = {lang: _bundle(lang) for lang in ("en", "fr", "it")}
    seen = set()
    for payload in _api_payloads(_ctx(cfg, tmp_path, sparse)):
        for path, m, eng in _msgs(payload):
            seen.add(m["code"])
            assert m["code"].startswith("rental."), path
            if isinstance(eng, str):                                  # the English the CLI and the tool read is the message's text,
                assert eng == m["text"] or eng == f"{m['text']} {GENERAL}", path       # plus the general-advice line for a reading
            for lang, b in bundles.items():
                forms = _lookup(b, m["code"])
                assert forms, (lang, m["code"])
                for f in forms:                                       # a param of the message is a variable of the sentence, and back
                    assert set(re.findall(r"\{\{(\w+)\}\}", f)) <= set(m["params"]) | {"count"}, (lang, m["code"])
                    assert {p for p in m["params"] if p != "count"} <= set(re.findall(r"\{\{(\w+)\}\}", f)), (lang, m["code"])
    assert not sorted(EXPECTED[sparse] - seen)


def test_a_reading_with_the_general_advice_line_sends_it_as_a_key_and_the_tax_and_loan_disclaimers_are_keys(rctx):
    i = rctx.get("/rental/rental-flat-1/indicators", market_rate=1.0, market_date="2026-09-20").json()
    lr = i["loan_rate"]
    assert lr["reading"].endswith(GENERAL)                                       # the English field: unchanged (the CLI, the tool)
    assert lr["reading_msg"]["disclaimer"] == "general_advice" and GENERAL not in lr["reading_msg"]["text"]
    ending = [s for s in i["signals"] if s["id"] == "commitment_ending"]
    above = next(s for s in i["signals"] if s["id"] == "rate_above_market")
    assert above["reading_msg"]["disclaimer"] == "general_advice"
    assert all(s["reading_msg"]["disclaimer"] == "general_advice" for s in ending)
    assert all("disclaimer" not in s["reading_msg"] for s in i["signals"] if s["id"] not in ("rate_above_market", "commitment_ending"))
    assert i["disclaimer"] == D.LOAN["en"] and i["disclaimer_key"] == "loan"
    t = rctx.get("/rental/rental-flat-1/tax", year=2026).json()
    assert t["disclaimer"] == D.TAX_BY_COUNTRY["FR"] and t["disclaimer_key"] == "tax"
    texts = rctx.get("/meta/disclaimers", lang="it").json()["texts"]              # the web reads the wording in its language
    assert texts["general_advice"] == D.GENERAL_ADVICE["it"] and texts["tax"] == D.TAX["it"] and texts["loan"] == D.LOAN["it"]


def test_the_mcp_tool_and_the_cli_json_carry_no_message(cfg):
    from coach.analytics import api as analytics_api
    from coach.rental import commands as rc, indicators as IND, model as M, service as RS, taxyear as TX
    con = rental_world(cfg)
    try:
        s = ToolSession(cfg, con=con, insecure=True, today=TODAY, session_id="s_rental_i18n")
        for name, args in (("rental_overview", {}), ("rental_overview", {"market_rate_pct": 1.0, "market_rate_date": "2026-01-01", "year": 2025}),
                           ("tax_candidates", {"year": 2026})):
            res = s.call(name, args)
            assert res.ok, res.text
            d = json.loads(res.text)
            assert not any(k.endswith("_msg") or k == "disclaimer_key" for k in _keys(d)), name
        d = json.loads(s.call("rental_overview", {"market_rate_pct": 1.0, "market_rate_date": "2026-01-01"}).text)
        lr = d["properties"][0]["indicators"]["loan_rate"]
        assert lr["reading"].endswith(GENERAL)                                   # the model reads the line, in English
        ds = analytics_api.build_dataset(con, cfg, TODAY)
        (p,) = M.properties(ds)
        out = json.loads(rc._j({**RS.overview(ds, p), "tax": TX.tax_year(ds, p, 2025), "indicators": IND.indicators(ds, p, market_rate_pct=1.0)}))
        assert not any(k.endswith("_msg") for k in _keys(out))
        assert out["indicators"]["loan_rate"]["reading"].endswith(GENERAL)
    finally:
        con.close()


def test_the_documents_checklist_is_a_label_family_in_every_language():
    from coach.rental.taxyear import CHECKLIST, SCHEME_CHECKLIST
    items = {i: (t, w) for i, t, w in CHECKLIST + SCHEME_CHECKLIST}
    en = _bundle("en")["labels"]
    assert {k: v[0] for k, v in items.items()} == en["rentalDocument"]          # the English web label is the server's own
    assert {k: v[1] for k, v in items.items()} == en["rentalDocumentFrom"]
    for lang in ("fr", "it"):
        labels = _bundle(lang)["labels"]
        assert set(labels["rentalDocument"]) == set(items) and set(labels["rentalDocumentFrom"]) == set(items), lang


def test_the_reduction_notes_are_messages_and_tax_candidates_keeps_the_english():
    import datetime as dt
    from types import SimpleNamespace as NS
    from coach.rental import reduction as RED
    com = NS(years=9, reduction_first_year=None, start_date=dt.date(2021, 3, 5), reduction_schedule=None, reduction_rate_pct=None,
             reduction_base_cap=150000, surface_m2=20, extension=None)
    a = NS(scheme="pinel", commitment=com, purchase_price=190000, purchase_date=dt.date(2021, 2, 1), pinel_commitment_years=None)
    r = RED.compute(a, 2025)
    assert r["status"] == "computed" and [m["text"] for m in r["notes_msg"]] == r["notes"]
    assert [m["code"] for m in r["notes_msg"]][:2] == ["rental.reduction.cappedDeclared", "rental.reduction.cappedM2"]
    assert r["notes"][1] == "the price is capped by 5,500 EUR/m2 x 20 m2"     # the English: unchanged
    a.purchase_price = None
    need = RED.compute(a, 2025)
    assert need["missing"] == ["purchase_price"] and need["missing_msg"] == [None]
