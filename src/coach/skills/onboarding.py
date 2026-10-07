"""onboarding_status (E7-11): what the coach still does not know about the household, as an ordered checklist.

A read-only view over the memory and the data: nothing is written here. The onboarding interview (the Claude Code skill, the
``coach onboarding`` command, the web page) walks the steps in order; every answer becomes a memory PROPOSAL (Claude) or a
validated, previewed ``coach memory`` write (CLI / web, source ``cli`` / ``ui``), reusing the open-questions store, and the
interview ends with ``coach memory check``.

Steps (``done`` / ``partial`` / ``todo``)
    household    members (adults, children, birth years), country, the privacy declarations (employers, places, schools)
    accounts     owner and purpose of every account
    loans        the fields of each liability that mortgage-check and the forecast need; loan-like payments without a file
    contracts    recurring payments that look like contracts and have no contract file; empty fields of the contracts on file
    preferences  profile.md and preferences.md (language, tone, goals) have content
    budgets      at least one budget, and how many suggestions the data supports
    questions    open questions waiting for an answer
"""
from __future__ import annotations

from typing import Optional

from coach.analytics.dataset import Dataset
from coach.analytics.recurring import RecurringResult, detect_recurring

MORTGAGE_FIELDS = ("lender", "principal", "start_date", "end_date", "rate.nominal", "monthly_payment", "outstanding",
                   "outstanding_as_of", "insurance.monthly")
LOAN_FIELDS = ("lender", "start_date", "end_date", "monthly_payment", "outstanding", "outstanding_as_of")
LEASE_FIELDS = ("lender", "start_date", "end_date", "monthly_payment", "residual_value", "mileage_limit_km", "excess_km_fee")   # E9-6
CONTRACT_FIELDS = ("provider", "billing.amount", "renewal", "notice_period_days")
LOAN_CATEGORIES = ("housing.mortgage", "housing.rental_property_loan", "transport.car_loan_lease", "debt.personal_loan",
                   "debt.loan_repayment", "debt.bnpl")


def _get(model, dotted: str):
    cur = model
    for p in dotted.split("."):
        cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


def _status(todo: int, total: int) -> str:
    return "done" if todo == 0 else ("todo" if todo >= total else "partial")


def _text_len(store, rel: str) -> int:
    try:
        return len([ln for ln in store.read_text(rel).splitlines() if ln.strip() and not ln.strip().startswith(("#", "<!--", "_"))])
    except Exception:                                                         # noqa: BLE001
        return 0


def onboarding_status(ds: Dataset, store, con, cfg, recurring: Optional[RecurringResult] = None) -> dict:
    rec = recurring or detect_recurring(ds)
    mem = ds.memory
    steps: list[dict] = []          # each "heading" is English; the web shows labels.onboardingStep.<id> (web/src/locales/<lang>/server.json)
    actions: list[dict] = []

    # -- household
    members = mem.members
    adults = [m for m in members if m.role == "adult"]
    kids = [m for m in members if m.role == "child"]
    no_birth = [m.id for m in members if m.birth_year is None]
    try:
        hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
    except Exception:                                                         # noqa: BLE001
        hh = {}
    country = hh.get("country")
    salary = any(x.category == "income.salary" for x in rec.series)
    missing_h = []
    if not adults:
        missing_h.append("members: no adult declared")
        actions.append({"step": "household", "do": "add each member (the id is what the coach sees, the name stays local)",
                        "command": 'uv run coach memory member add --id <id> --name "<name>" --role adult --birth-year <yyyy> --alias "<holder spelling>"'})
    if no_birth:
        missing_h.append("birth year: " + ", ".join(no_birth))
    if not country:
        missing_h.append("country (FR or IT; FR is assumed): tax and cancellation rules depend on it")
        actions.append({"step": "household", "do": "declare the country", "command": "uv run coach memory set household.yaml country FR --new-field"})
    if salary and not hh.get("employers"):
        missing_h.append("employer not declared (it is masked in everything a model sees only when declared)")
        actions.append({"step": "household", "do": "declare the employer(s), places and schools (privacy declarations)",
                        "command": 'uv run coach memory set household.yaml employers \'["<employer>"]\' --new-field'})
    if not hh.get("places"):
        missing_h.append("towns / places not declared")
    if kids and not hh.get("schools"):
        missing_h.append("schools / nurseries not declared")
    steps.append({"id": "household", "heading": "Household and privacy declarations", "status": _status(len(missing_h), 4 if members else 1),
                  "have": {"adults": len(adults), "children": len(kids), "country": country, "employers": len(hh.get("employers") or []),
                           "places": len(hh.get("places") or []), "schools": len(hh.get("schools") or [])},
                  "missing": missing_h})

    # -- accounts
    all_rows = sorted(ds.accounts.values(), key=lambda a: a.uid)
    unset = [{"account": a.uid, "missing": [n for n, v in (("owner", a.owner), ("purpose", a.purpose)) if not v]}
             for a in all_rows if not a.owner or not a.purpose]
    if unset:
        actions.append({"step": "accounts", "do": "say whose each account is and what it is for",
                        "command": "uv run coach accounts set <uid> --owner <member-id|joint> --purpose main|cards|rental|kids|savings"})
    steps.append({"id": "accounts", "heading": "Accounts: owner and purpose", "status": _status(len(unset), max(1, len(all_rows))),
                  "have": {"accounts": len(all_rows)}, "missing": unset})

    # -- loans
    loan_rows = []
    linked = {l.id for x in rec.series for l in x.links if l.kind == "liability"}
    for rel, lb in mem.liabilities:
        fields = MORTGAGE_FIELDS if lb.kind == "mortgage" else LEASE_FIELDS if lb.kind in ("loa", "lld") else LOAN_FIELDS
        miss = [f for f in fields if _get(lb, f) is None]
        if not lb.payment_match:
            miss.append("payment_match")
        loan_rows.append({"id": lb.id, "kind": lb.kind, "missing": miss, "matched_in_bank_data": lb.id in linked})
    orphans = [x.id for x in rec.series if x.status == "active" and x.category in LOAN_CATEGORIES
               and not any(l.kind == "liability" for l in x.links)]
    todo_loans = sum(1 for r in loan_rows if r["missing"]) + len(orphans)
    if loan_rows or orphans:
        actions.append({"step": "loans", "do": "fill the empty fields from the loan contract or the latest statement; let the coach suggest "
                        "the ones the bank payments allow it to infer (a suggestion, never written silently)",
                        "command": "uv run coach loans edit <liability-id> --set rate.nominal=<value> --reason \"<source>\"   "
                                   "(guided: uv run coach loans add; suggestions: uv run coach loans infer <id> --propose; "
                                   "or: uv run coach memory doc add <file> --kind loan --for <id>, then doc extract: dry run first)"})
    steps.append({"id": "loans", "heading": "Loans and mortgage (fields mortgage-check needs)", "status": _status(todo_loans, max(1, len(loan_rows) + len(orphans))),
                  "have": {"liabilities": len(loan_rows)}, "liabilities": loan_rows, "loan_payments_without_file": orphans[:10]})

    # -- contracts
    missing_c = [x.id for x in rec.series if x.missing_contract]
    thin = []
    for rel, c in mem.contracts:
        miss = [f for f in CONTRACT_FIELDS if _get(c, f) is None]
        if miss:
            thin.append({"id": c.id, "missing": miss})
    if missing_c or thin:
        actions.append({"step": "contracts", "do": "create a contract file per recurring cost (or extract it from the contract PDF: dry run first)",
                        "command": "uv run coach memory new contract <id> --kind insurance_car|energy|telecom|...   "
                                   "(or: uv run coach memory doc add <file> --kind contract)"})
    steps.append({"id": "contracts", "heading": "Contracts behind the recurring payments", "status": _status(len(missing_c) + len(thin), max(1, len(missing_c) + len(mem.contracts))),
                  "have": {"contracts": len(mem.contracts)}, "recurring_without_contract": missing_c[:15], "contracts_with_empty_fields": thin[:15]})

    # -- preferences
    prof, pref = _text_len(store, "profile.md"), _text_len(store, "preferences.md")
    miss_p = []
    if prof < 3:
        miss_p.append("profile.md is empty: household background, goals, situation")
    if pref < 4:
        miss_p.append("preferences.md has few coach rules: language, tone, topics to avoid")
    if miss_p:
        actions.append({"step": "preferences", "do": "write the coach rules (language, tone, goals)",
                        "command": 'uv run coach memory append preferences.md "- Language: <fr|en|it>\\n- Tone: <...>"'})
    steps.append({"id": "preferences", "heading": "Preferences and goals", "status": _status(len(miss_p), 2),
                  "have": {"profile_lines": prof, "preference_lines": pref, "goals": len(mem.goals)}, "missing": miss_p})

    # -- budgets
    from coach.analytics.budgets import suggest_budgets
    sug = suggest_budgets(ds, limit=50)
    miss_b = [] if mem.budgets else ["no budget yet"]
    if miss_b and sug.suggestions:
        actions.append({"step": "budgets", "do": "start from the suggested budgets (the median of recent months)",
                        "command": "uv run coach budget suggest   then   uv run coach budget set <category> <amount> --propose"})
    steps.append({"id": "budgets", "heading": "Budgets", "status": _status(len(miss_b), 1),
                  "have": {"budgets": len(mem.budgets), "suggestions_available": len(sug.suggestions)}, "missing": miss_b})

    # -- questions
    qs = [q for q in store.questions() if q.status == "open"]
    if qs:
        actions.append({"step": "questions", "do": "answer the open questions, biggest stake first (household-questions skill)",
                        "command": "uv run coach questions list --open"})
    else:
        actions.append({"step": "questions", "do": "refresh the question list from the data",
                        "command": "uv run coach questions generate --dry-run"})
    steps.append({"id": "questions", "heading": "Open questions", "status": "done" if not qs else "partial", "have": {"open": len(qs)}})

    done = sum(1 for s in steps if s["status"] == "done")
    nxt = next((s["id"] for s in steps if s["status"] != "done"), None)
    return {"as_of": ds.today, "progress": {"done": done, "total": len(steps), "next_step": nxt}, "steps": steps,
            "next_actions": actions,
            "how": ["answers become memory PROPOSALS (memory_propose / questions_propose) that the user accepts themselves, or "
                    "validated `coach memory` writes with a preview",
                    "finish with `uv run coach memory check` (0 errors)"],
            "note": "in the default coarse privacy mode some memory ids are pseudonyms (liability-1): the user applies the exact "
                    "id from their own memory"}
