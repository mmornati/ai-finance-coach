"""Glue of the rental package (E15): the per-property overview that the CLI, the API and the MCP tool share, and the insight cards that
feed the alert engine (kinds ``scheme_end``, ``scheme_check``, ``rent_missing``)."""
from __future__ import annotations

import hashlib

from coach import disclaimers
from coach.analytics.common import last_closed_month, money_str
from coach.i18n_msg import server_msg
from coach.rental import cashflow as CF, indicators as IND, model as M, scheme as SC


def overview(ds, prop, *, months: int = 12) -> dict:
    """One property in one dict (money in cents, rendered by :func:`coach.rental.render.plain`)."""
    cm = CF.monthly(ds, prop, months)
    pnl_year = int(last_closed_month(ds.today)[:4])
    rent_row = next((r for r in reversed(cm["months"]) if r.rent_c > 0), None)
    sch = SC.status(ds, prop, ds.settings.rental_reminder_months, rent_row.rent_c if rent_row else None)
    return {"id": prop.id, "kind": "rental property",
            "links": {"account": prop.account_link, "accounts": len(prop.accounts), "loan": prop.loan_link, "loans": len(prop.loans),
                      "notes": prop.notes, "notes_msg": prop.notes_msg},
            "cashflow": cm, "current_month": CF.current_month(ds, prop, ds.settings.rental_rent_grace_days),
            "pnl": CF.year_pnl(ds, prop, pnl_year),
            "scheme": sch}


def summary_row(ds, prop) -> dict:
    """The line of the properties list."""
    rows, rent = CF.all_rows(ds, prop)
    last = rows[-1] if rows else None
    sch = SC.status(ds, prop, ds.settings.rental_reminder_months)
    eq = IND.equity(ds, prop)
    return {"id": prop.id, "account_link": prop.account_link, "loan_link": prop.loan_link, "n_loans": len(prop.loans),
            "expected_rent_c": rent.expected_c, "rent_source": rent.source,
            "last_month": ({"month": last.month, "rent_c": last.rent_c, "net_c": last.net_c, "effort_c": last.effort_c, "complete": last.complete,
                            "rent_status": last.rent_status} if last else None),
            "vacancy": CF.vacancy(rows), "scheme": {k: sch.get(k) for k in ("declared", "scheme", "state", "end_date", "effective_end_date",
                                                                            "months_left", "decision_needed")},
            "net_equity_c": eq.get("net_equity_c"), "missing": [m["field"] for m in sch["missing"]]}


def _iid(*p) -> str:
    return "ins_" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:10]


def cards(ds) -> list[dict]:
    """Insight cards (the shape of the insights feed): the commitment end while the extension decision is open, a rent that did not arrive, a
    rent or a tenant income that exceeds the limit the owner declared. Titles and bodies are LOCAL (they may name the property)."""
    out: list[dict] = []
    if getattr(ds, "member", None):                    # a person's view holds only their transactions: a property is a whole-household fact
        return out
    today = ds.today
    rem = ds.settings.rental_reminder_months
    for prop in M.properties(ds):
        who = prop.id
        rows, rent = CF.all_rows(ds, prop)
        rent_row = next((r for r in reversed(rows) if r.rent_c > 0), None)
        sch = SC.status(ds, prop, rem, rent_row.rent_c if rent_row else None)
        if sch.get("decision_needed") and sch.get("end_date"):
            end_iso = sch["end_date"].isoformat()
            ml = sch["months_left"]
            thr = min((m for m in sorted({rem, *SC.REMINDER_STEPS}) if ml <= m), default=rem)
            ended = sch["days_left"] < 0
            sev = "high" if ended or ml <= 3 else "medium" if ml <= 6 else "low"
            when = (f"ended on {end_iso}" if ended else f"ends on {end_iso} (about {ml} month(s) left)")
            title = f"{who}: the scheme commitment {when}"
            core = ("No decision about the extension is recorded. Decide whether to extend (if the scheme allows it), keep renting "
                    "under the ordinary rules or sell, check the consequences in the scheme's rules or with an adviser, then record "
                    "the decision (`coach rental extension`).")
            out.append({"id": _iid("rental-end", prop.id, end_iso, "ended" if ended else thr), "kind": "rental", "subtype": "scheme_end",
                        "severity": sev, "title": title,
                        "title_msg": (server_msg("rentalAlert.schemeEnd.titleEnded", title, property=who, end_date=end_iso) if ended
                                      else server_msg("rentalAlert.schemeEnd.title", title, property=who, end_date=end_iso, count=int(ml))),
                        # the English body ends with the disclaimer (CLI, alerts); the message does not: the web adds the text of
                        # `disclaimer` in its language (GET /meta/disclaimers), the wording living only in coach/disclaimers.py
                        "body": core + " " + disclaimers.get("tax_short"), "body_msg": server_msg("rentalAlert.schemeEnd.body", core),
                        "disclaimer": "tax_short",
                        "amount": None, "date": end_iso, "subject": who, "evidence": [prop.id], "persist": "ui"})
        for r in [x for x in rows[-3:] if x.rent_status == "missing"]:
            exp = f" (about {money_str(r.expected_rent_c)} EUR expected)" if r.expected_rent_c else ""
            title = f"{who}: no rent seen for {r.month}"
            body = (f"The account data cover {r.month} and hold no rent{exp}. If the property was empty, record the period in "
                    "`vacancies` (it is then not reported again); otherwise check with the tenant or the property manager.")
            out.append({"id": _iid("rental-rent", prop.id, r.month), "kind": "rental", "subtype": "rent_missing", "severity": "medium",
                        "title": title, "title_msg": server_msg("rentalAlert.rentMissing.title", title, property=who, rent_month=r.month),
                        "body": body,
                        "body_msg": (server_msg("rentalAlert.rentMissing.bodyExpected", body, rent_month=r.month,
                                                expected_amount=money_str(r.expected_rent_c)) if r.expected_rent_c
                                     else server_msg("rentalAlert.rentMissing.body", body, rent_month=r.month)),
                        "amount": money_str(r.expected_rent_c) if r.expected_rent_c else None, "date": f"{r.month}-28", "subject": who,
                        "evidence": [prop.id], "persist": "ui"})
        cur = CF.current_month(ds, prop, ds.settings.rental_rent_grace_days)
        if cur["status"] == "late":
            title = f"{who}: this month's rent has not arrived"
            grace = ds.settings.rental_rent_grace_days
            body = f"No rent has been seen in {cur['month']} and the usual day (plus {grace} days) has passed."
            out.append({"id": _iid("rental-rent", prop.id, cur["month"]), "kind": "rental", "subtype": "rent_missing", "severity": "low",
                        "title": title, "title_msg": server_msg("rentalAlert.rentLate.title", title, property=who),
                        "body": body, "body_msg": server_msg("rentalAlert.rentLate.body", body, rent_month=cur["month"], count=int(grace)),
                        "amount": money_str(cur["expected_c"]) if cur["expected_c"] else None, "date": today.isoformat(), "subject": who,
                        "evidence": [prop.id], "persist": "ui"})
        rc = sch["rent_cap"]
        if rc["status"] == "above_cap":
            title = f"{who}: the rent is above the cap you declared"
            body = (f"Rent {money_str(rc['rent_c'])} EUR against a cap of {money_str(rc['cap_c'])} EUR ({rc['rent_basis']}). Check the "
                    "scheme's rules and the lease: a rent above the cap can put the advantage at risk.")
            rp = {"rent_amount": money_str(rc["rent_c"]), "cap_amount": money_str(rc["cap_c"])}
            observed = (rc.get("rent_basis") or "").startswith("the last rent received")
            out.append({"id": _iid("rental-cap", prop.id, rc["rent_c"], rc["cap_c"]), "kind": "rental", "subtype": "rent_cap", "severity": "medium",
                        "title": title, "title_msg": server_msg("rentalAlert.rentCap.title", title, property=who),
                        "body": body,
                        "body_msg": (server_msg("rentalAlert.rentCap.bodyObserved", body, **rp) if observed
                                     else server_msg("rentalAlert.rentCap.bodyDeclared", body, **rp)),
                        "amount": money_str(rc["gap_c"]), "date": today.isoformat(), "subject": who, "evidence": [prop.id], "persist": "ui"})
        ti = sch["tenant_income"]
        if ti["status"] == "above_limit":
            title = f"{who}: the tenant income is above the limit you declared"
            body = (f"Tenant income {money_str(ti['tenant_income_c'])} EUR against a limit of {money_str(ti['limit_c'])} EUR. Check the "
                    "scheme's rules for the tenant household's size.")
            out.append({"id": _iid("rental-income", prop.id, ti["tenant_income_c"], ti["limit_c"]), "kind": "rental", "subtype": "tenant_income",
                        "severity": "medium", "title": title, "title_msg": server_msg("rentalAlert.tenantIncome.title", title, property=who),
                        "body": body,
                        "body_msg": server_msg("rentalAlert.tenantIncome.body", body, income_amount=money_str(ti["tenant_income_c"]),
                                               limit_amount=money_str(ti["limit_c"])),
                        "amount": money_str(-ti["headroom_c"]),
                        "date": today.isoformat(), "subject": who, "evidence": [prop.id], "persist": "ui"})
    return out


def facts_for_questions(asset, liabilities: list, rental_accounts: int, rental_assets_without_account: int) -> list[str]:
    """The fields to ask for a rental asset, from the memory alone (the same list the tool reports, minus the by-account loan link)."""
    miss = []
    if not asset.account and not (rental_accounts == 1 and rental_assets_without_account == 1):
        miss.append("account")
    linked = bool(asset.loan) or any((lb.asset or "").strip().lower() == asset.id.lower() for lb in liabilities) or \
        (bool(asset.account) and any(lb.kind == "mortgage" and lb.debited_account == asset.account for lb in liabilities))
    if not linked:
        miss.append("loan")
    for f, v in (("value", asset.value), ("rent_monthly", asset.rent_monthly), ("purchase_price", asset.purchase_price),
                 ("purchase_date", asset.purchase_date)):
        if v is None:
            miss.append(f)
    com = asset.commitment
    if asset.scheme or com or asset.pinel_commitment_years:
        years = (com.years if com else None) or asset.pinel_commitment_years
        for f, ok in (("commitment.start_date", bool(com and com.start_date)), ("commitment.years", bool(years)),
                      ("commitment.rent_cap_monthly (or rent_cap_m2 and surface_m2)", bool(com and (com.rent_cap_monthly is not None or (com.rent_cap_m2 is not None and com.surface_m2)))),
                      ("commitment.tenant_income_limit", bool(com and com.tenant_income_limit is not None)),
                      ("commitment.reduction_rate_pct", bool(com and com.reduction_rate_pct is not None))):
            if not ok:
                miss.append(f)
    return miss
