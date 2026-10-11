"""The AI suggestion of the review queue (E2-9 in the web app) and the web-search descriptor (E2-7): town stripping by shape, the country
hint. Synthetic data only; the model is always a fake backend."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from apihelpers import ctx, world  # noqa: F401
from coach.classify import backends
from coach.classify.backends import LLMBackend
from coach.classify.candidates import country_hint, search_descriptor, strip_trailing_town
from helpers import add_tx

WHICH = "coach.api.coachjobs.shutil.which"


# ---------------------------------------------------------------- the web-search descriptor

@pytest.mark.parametrize("text, bare", [
    ("ZORBAX LA HULPA", "ZORBAX"),                         # a town that starts with an article
    ("QUINC ZORB LA POINTE TROU", "QUINC ZORB"),           # ... and a truncated two-word one
    ("TRATTORIA BLU CHALON SUR SA", "TRATTORIA BLU"),      # a town joined by SUR, truncated
    ("MARE BLU SAN VITO LO", "MARE BLU"),                  # SAN starts the town
    ("FERMOLO BRAVIX LE", "FERMOLO"),                      # 'BRAVIX-LE-...' cut by the bank after its link
    ("WORK IN PROGRESS KELM AM RHEIN", "WORK IN PROGRESS"),
    ("MAISON DE LA PRESSE", "MAISON DE LA PRESSE"),         # a link right after another link: a shop's own name
    ("BRASSERIE DU PORT", "BRASSERIE DU PORT"),             # a link after the first word only: kept
    ("LE ZORBAX", "LE ZORBAX"),
    ("ZORBAX", "ZORBAX"),
])
def test_an_unknown_town_is_cut_by_its_shape(text, bare):
    assert strip_trailing_town(text) == bare


def test_the_search_descriptor_cuts_the_known_town_then_the_shaped_one():
    assert search_descriptor("ZORBAX GADGET QUIMPRE", [("QUIMPRE",)]) == "ZORBAX GADGET"
    assert search_descriptor("ZORBAX GADGET LA HULPA", []) == "ZORBAX GADGET"


def test_the_country_hint_comes_from_a_foreign_currency_or_a_legal_form():
    assert country_hint("SPRL ZORBAX") == "Belgium"
    assert country_hint("ZORBAX GMBH") == "Germany or Austria"
    assert country_hint("ZORBAX", "PLN") == "Poland"
    assert country_hint("SARL ZORBAX", "GBP") == "the United Kingdom"            # the currency of the payment wins
    assert country_hint("ZORBAX GADGET") is None and country_hint("ZORBAX SA") is None


def test_the_web_prompt_no_longer_forces_france_and_carries_the_hint():
    from coach.classify.llm import web_request
    static, dynamic, _ = web_request([{"id": 0, "descriptor": "ZORBAX", "country_hint": "Belgium"}])
    assert "country_hint" in static and "search the name\nalone first" in static
    assert '"country_hint": "Belgium"' in dynamic


# ---------------------------------------------------------------- the review queue's AI suggestion

class Fake(LLMBackend):
    name = "claude-code"

    def __init__(self, web: bool = False, answer=None):
        self.web = web
        self.calls = []
        self.answer = answer

    @property
    def supports_web_search(self):
        return self.web

    def complete(self, static, dynamic, schema, model, purpose="label", items=0, web_search=False):
        self.calls.append(SimpleNamespace(static=static, dynamic=dynamic, purpose=purpose, web=web_search))
        res = self.answer if self.answer is not None else [{"id": 0, "merchant": "Fresh Market", "category": "food.groceries",
                                                            "confidence": 0.91, "recurring_hint": False, "evidence": "  a grocer  "}]
        return {"results": res}, backends.Usage("claude-code", model, purpose, items, cost_usd=0.01)


def _set(cfg, **kw):
    for k, v in kw.items():
        object.__setattr__(cfg, k, v)


@pytest.fixture
def ai(ctx, monkeypatch):
    monkeypatch.setattr(WHICH, lambda _name: "/usr/bin/claude")
    fake = Fake()
    ctx.state.suggest_jobs.backend = fake
    return ctx, fake


def test_status_says_web_search_is_off_by_default(ai):
    ctx, _ = ai
    s = ctx.get("/review/ai").json()
    assert s["label"] == {"allowed": True, "code": "ok", "why": ""}
    assert s["web"]["allowed"] is False and s["web"]["code"] == "web_enrich_off"


def test_a_suggestion_is_a_proposal_with_the_ai_label_and_its_usage_logged(ai):
    ctx, fake = ai
    before = ctx.sql("SELECT category, confidence, source FROM merchants WHERE merchant_key='FRESH MARKET'")
    r = ctx.post("/review/suggest", {"key": "FRESH MARKET"})
    assert r.status_code == 200
    job = ctx.get(f"/review/suggest/{r.json()['id']}").json()
    assert job["state"] == "done" and job["mode"] == "label" and job["ai_generated"] is True and job["ai_label_short"]
    s = job["suggestion"]
    assert s["category"] == "food.groceries" and s["confidence"] == 0.91 and s["evidence"] is None
    assert len(fake.calls) == 1 and fake.calls[0].web is False and fake.calls[0].purpose == "label"
    assert ctx.sql("SELECT category, confidence, source FROM merchants WHERE merchant_key='FRESH MARKET'") == before   # nothing stored
    assert ctx.sql("SELECT purpose, items FROM llm_usage") == [("label", 1)]


def test_web_search_is_used_when_allowed_and_the_merchant_is_a_shop(ai):
    ctx, fake = ai
    fake.web = True
    _set(ctx.state.cfg, privacy_web_enrich=True)
    assert ctx.get("/review/ai").json()["web"]["allowed"] is True
    job = ctx.post("/review/suggest", {"key": "FRESH MARKET"}).json()
    assert job["state"] == "done" and job["mode"] == "web" and job["suggestion"]["evidence"] == "a grocer"
    call = fake.calls[0]
    assert call.web is True and call.purpose == "enrich" and '"descriptor": "FRESH MARKET"' in call.dynamic
    assert ctx.sql("SELECT purpose FROM llm_usage") == [("enrich",)]


def test_an_unusable_answer_fails_and_an_invented_category_is_never_proposed(ai):
    ctx, fake = ai
    fake.answer = []
    assert ctx.post("/review/suggest", {"key": "FRESH MARKET"}).json()["error"]["code"] == "unusable_answer"
    fake.answer = [{"id": 0, "merchant": "X", "category": "made.up", "confidence": 0.99, "recurring_hint": False}]
    s = ctx.post("/review/suggest", {"key": "FRESH MARKET"}).json()["suggestion"]
    assert s["category"] == "other.uncategorized" and s["confidence"] <= 0.3


def test_person_like_merchants_and_keys_outside_the_queue_are_never_sent(ai):
    ctx, fake = ai
    with ctx.state.write() as con:
        add_tx(con, "fo", "p1", "2026-09-20", -40.0, "MARIE DUPONT", "transfer_out")
    queue = {i["key"]: i for i in ctx.get("/review").json()["items"]}
    assert queue["MARIE DUPONT"]["reason"] == "held_back_person_like"
    r = ctx.post("/review/suggest", {"key": "MARIE DUPONT"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "person_like"
    assert ctx.post("/review/suggest", {"key": "NO SUCH KEY"}).status_code == 404
    assert ctx.get("/review/suggest/s_nope").status_code == 404
    assert fake.calls == []


def test_a_refused_backend_is_reported_not_called(ai, monkeypatch):
    ctx, fake = ai
    monkeypatch.setattr(WHICH, lambda _name: None)
    assert ctx.get("/review/ai").json()["label"]["allowed"] is False
    job = ctx.post("/review/suggest", {"key": "FRESH MARKET"}).json()
    assert job["state"] == "failed" and job["error"]["code"] == "backend_unavailable" and fake.calls == []
