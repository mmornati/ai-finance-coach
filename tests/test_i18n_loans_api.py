"""i18n step 4f, the web API side: the wealth and loan endpoints send their sentences with aligned `*_msg` siblings whose codes the web
knows in every language (see test_i18n_loans.py). Synthetic data only."""
from __future__ import annotations

from apihelpers import ctx, world  # noqa: F401
from coach.api.routes.wealth import LOAN_FIELD_LABEL
from test_i18n_loans import _check_aligned_and_known


def test_the_wealth_api_sends_aligned_messages_the_web_knows(ctx):
    seen = set()
    nw = ctx.get("/net-worth", history=True, months=24).json()
    assert nw["note_msg"]["code"] in ("netWorth.total", "netWorth.totalPartial")
    seen |= _check_aligned_and_known(nw)
    L = ctx.get("/liabilities").json()
    assert L["note_msg"]["code"] == "netWorth.liabilitiesNote"
    for loan in L["liabilities"]:
        assert [LOAN_FIELD_LABEL[c] for c in loan["missing_codes"]] == loan["missing"]
    seen |= _check_aligned_and_known(L)
    seen |= _check_aligned_and_known(ctx.get("/loans/home-loan").json())
    seen |= _check_aligned_and_known(ctx.get("/net-worth/history").json())
    r = ctx.post("/loans/home-loan/scenario", {"type": "prepay", "amount": 20000, "on": "2026-11-01"}).json()
    seen |= _check_aligned_and_known(r)
    assert r["result"]["disclaimer_key"] == "loan" and all("verdict_code" in o for o in r["result"]["options"])
    assert {"schedule.outstandingStart", "schedule.declaredRolled", "netWorth.historyNote", "scenario.estimate"} <= seen
    assert ctx.get("/meta/disclaimers", lang="fr").json()["texts"]["loan"].startswith("Estimation")
