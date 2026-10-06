"""The finance MCP tools of the subscriptions optimizer (E8): ``subscriptions_inventory`` and ``savings_tracker`` (read-only),
``alternatives_record`` (a validated write of a SOURCED, DATED offer), ``decision_propose`` (a PROPOSED decision: not counted until
the user confirms it) and ``contracts_draft`` (contract draft PROPOSALS).

They sit behind the same choke point as every tool (``ToolSession._publish``): untrusted text wrapped, memory ids pseudonymised, the
final privacy assertion. What they never return: the user's usage notes, decision notes, the contract number, the postal address /
e-mail of the household's ``contact`` block (no tool reads it), and the file names of drafted contracts (they carry the provider).
Provider names go through the household redactor like in every other tool.
"""
from __future__ import annotations


from coach.analytics.common import _plain, money_str
from coach.mcp.tools import DATA_NOTE, DATE, MAX_PROPOSALS, ToolError, ToolSession, ToolSpec, _obj
from coach.skills.tools import _recurring, _real_id, _today
from coach.subs import alternatives as A, decisions as DEC, draft as DR, service as S

MAX_ALTERNATIVES, MAX_DECISIONS = 10, 5


def _bundle(s: ToolSession, include_ended: bool = True) -> S.Bundle:
    reg = s.reg
    key = ("_subs_bundle", include_ended, _today(s))
    memo = getattr(reg, "_subs_memo", None)
    if memo is None:
        memo = reg._subs_memo = {}
    if key not in memo:
        memo[key] = S.load_bundle(s.con, s.cfg, _today(s), ds=reg.ds, rec=_recurring(s), store=s.data()[2], include_ended=include_ended)
    return memo[key]


def _resolve_ref(s: ToolSession, b: S.Bundle, ref: str) -> dict:
    """A model-side ref (``rec_...`` or ``contract:<pseudonym>``) -> the inventory row. A REAL contract id is refused with the same
    neutral message as an unknown one (no oracle on the memory)."""
    if ref.startswith("contract:"):
        real = _real_id(s, ref.split(":", 1)[1])
        ref = f"contract:{real}"
    try:
        return S.resolve(b.inv, ref, allow_name=False)
    except S.RefError:
        raise ToolError("unknown subscription: use a `ref` returned by `subscriptions_inventory` (a `rec_` id, or contract:<id>)") from None


# ---------------------------------------------------------------- read-only

def _row_view(s: ToolSession, r: dict, rules: dict) -> dict:
    """One compact row (the choke point caps a result at 24 000 characters: the rules text is listed once at the top level)."""
    red = s.reg.red
    ci = r["cancellation"]
    al = r["alternatives"]
    best = al["best"]
    dec = r["decision"]
    for x in ci["legal_basis"]:
        rules.setdefault(x["id"], {"name": x["name"], "source": x["source"], "last_reviewed": x["last_reviewed"]})
    ec = ci["early_termination_cost"]
    out = {
        "ref": r["ref"], "name": red.text(r["name"]), "group": r["group"], "kind": r["kind"], "cadence": r["cadence"], "monthly": r["monthly"],
        "yearly": r["yearly"], "status": r["status"], "next_charge": r["next_charge"],
        "contract": {k: v for k, v in {"status": r["contract"]["status"], "id": r["contract_id"], "renewal": r["contract"].get("renewal"),
                                      "commitment_end": r["contract"].get("commitment_end"), "notice_days": r["contract"].get("notice_period_days"),
                                      "keep": r["contract"].get("keep")}.items() if v is not None},
        "usage": {"frequency": r["usage"]["frequency"], "last_used": r["usage"]["last_used"], "recorded": r["usage"]["recorded"],
                  "reminders": [x["kind"] for x in r["usage"]["signals"]]},
        "cancellation": {k: v for k, v in {
            "can_cancel_now": ci["can_cancel_now"], "earliest_effective_date": ci["earliest_effective_date"], "notice_period_days": ci["notice_period_days"],
            "early_termination_cost": None if not ec else {"amount": ec["amount"], "free_exit_date": ec["free_exit_date"]},
            "rules": [x["id"] for x in ci["legal_basis"]], "unknown": ci["unknown"] or None}.items() if v is not None},
    }
    if r["price_changes"]:
        out["price_changes"] = [{"id": p["id"], "date": p["date"], "old": p["old"], "new": p["new"]} for p in r["price_changes"]]
    if al["count"]:
        out["alternatives"] = {"count": al["count"], "outdated": al["outdated"],
                               "best": None if not best else {"provider": best["provider"], "offer": best["offer"], "monthly_price": best["monthly_price"],
                                                              "retrieved_at": best["retrieved_at"], "age_days": best["age_days"],
                                                              "yearly_saving": best["savings"]["yearly"], "net_12m": best["savings"]["net_12m"]}}
    if dec:
        out["decision"] = {"id": dec["id"], "decision": dec["decision"], "status": dec["status"], "monthly_saving": dec["monthly_saving"]}
    if r["draftable"]:
        out["draftable"] = True
    return out


def _subscriptions_inventory(s: ToolSession, a: dict):
    b = _bundle(s, bool(a.get("include_ended")))
    inv = b.inv
    rows = [r for r in inv["rows"] if not a.get("group") or r["group"] == a["group"]]
    limit = int(a.get("limit") or 25)
    rules: dict = {}
    listed = [_row_view(s, r, rules) for r in rows[:limit]]
    out = {"as_of": inv["as_of"], "country": inv["country"], "totals": inv["totals"], "groups": {g: {k: v for k, v in x.items() if k != "label"}
                                                                                                 for g, x in inv["groups"].items()},
           "savings": inv["savings"], "rows": listed, "rows_not_listed": max(0, len(rows) - limit), "rules": rules,
           "note": ("One row per recurring cost. Cancellation is a general summary of consumer-law rules (`rules` lists each with its source and "
                    "review date): always say 'verify with your contract'. Usage is what the household recorded; it is unknown unless "
                    "`usage.recorded`. Alternatives older than 30 days are outdated and never the current best. Never tell the user to cancel: "
                    "list what is worth reviewing."),
           "next": ("Missing contracts (`draftable`): `contracts_draft` (proposals). Usage unknown: `questions_propose`. Cheaper offers: the "
                    "`find-cheaper` skill, then `alternatives_record`. A decision the user tells you about: `decision_propose`.")}
    return _plain(out)


def _savings_tracker(s: ToolSession, a: dict):
    b = _bundle(s)
    red = s.reg.red
    sav = DEC.savings(b.decisions, b.rec, _today(s))
    rows = [{"id": r["id"], "name": red.text(r["name"] or ""), "decision": r["decision"], "decided_on": r["decided_on"], "effective_on": r["effective_on"],
             "before_monthly": r["before_monthly"], "after_monthly": r["after_monthly"], "monthly_saving": r["monthly_saving"],
             "months_counted": r["months_counted"], "since_decision": r["since_decision"], "status": r["status"], "reason": r["reason"],
             "reminder": r["reminder"]} for r in sav["decisions"]]
    out = {k: sav[k] for k in ("as_of", "realised_monthly", "realised_yearly_run_rate", "realised_since_decisions", "verified", "pending",
                               "contradicted", "claimed_monthly_unverified")}
    out.update({"decisions": rows, "proposed_not_yet_confirmed": sum(1 for d in b.decisions if d.state == "proposed"),
                "note": sav["note"] + " Read-only: the user records decisions; you can only propose one with `decision_propose`."})
    return _plain(out)


# ---------------------------------------------------------------- writes

def _mask(s: ToolSession, fields: dict) -> dict:
    guard = s.data()[1]
    masked, hit = guard.mask(fields)
    if hit and guard.violations(masked):
        raise ToolError("the offer text could not be stored (details withheld by the privacy filter): leave out personal details")
    return masked


def _alternatives_record(s: ToolSession, a: dict):
    from coach import db
    if len(getattr(s, "alternatives", [])) >= MAX_ALTERNATIVES:
        raise ToolError(f"at most {MAX_ALTERNATIVES} alternatives per session")
    b = _bundle(s)
    row = _resolve_ref(s, b, a["ref"])
    text = _mask(s, {"provider": a["provider"], "offer_name": a["offer_name"], "features": a.get("features"), "notes": a.get("notes")})
    today = _today(s)
    try:
        v = A.validate(provider=text["provider"], offer_name=text["offer_name"], monthly_price=a["monthly_price"], retrieved_at=a["retrieved_at"],
                       source_url=a["source_url"], features=text["features"], notes=text["notes"], switching_costs=a.get("switching_costs") or 0,
                       method="find-cheaper", source="coach-llm", today=today, require_url=True)
    except A.AlternativeError as e:
        raise ToolError(f"the alternative was refused: {e}") from None
    con = db.connect(s.cfg, insecure=s.insecure, migrate=False)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        dup = [x for x in A.load(con, contract_id=row["contract_id"], series_id=row["series_id"])
               if (x.provider, x.offer_name, x.monthly_price_c, x.retrieved_at) == (v["provider"], v["offer_name"], v["monthly_price_c"], v["retrieved_at"])]
        if dup:
            alt = dup[0]
            status = "already_stored"
        else:
            alt = A.add(con, contract_id=row["contract_id"], series_id=row["series_id"], today=today, provider=v["provider"], offer_name=v["offer_name"],
                        monthly_price=v["monthly_price_c"] / 100, features=v["features"], source_url=v["source_url"], retrieved_at=v["retrieved_at"],
                        method="find-cheaper", notes=v["notes"], switching_costs=v["switching_costs_c"] / 100, source="coach-llm")
            status = "stored"
    finally:
        con.close()
    if not hasattr(s, "alternatives"):
        s.alternatives = []
    s.alternatives.append(alt.id)
    cur_c = None if row["monthly"] is None else round(float(row["monthly"]) * 100)
    assessed = A.assess(alt, cur_c, today)
    assessed.pop("_net_c", None)
    return _plain({"alternative_id": alt.id, "status": status, "subscription": row["ref"], "current_monthly": row["monthly"],
                   "monthly_price": assessed["monthly_price"], "retrieved_at": alt.retrieved_at, "age_days": assessed["age_days"], "label": assessed["label"], "savings": assessed["savings"],
                   "next": ("Stored with its source and date; the savings are computed by code. Say that prices change and that the user must "
                            "verify the offer on the provider's page. Nothing was changed or signed.")})


def _decision_propose(s: ToolSession, a: dict):
    from coach import db
    if len(getattr(s, "decisions_proposed", [])) >= MAX_DECISIONS:
        raise ToolError(f"at most {MAX_DECISIONS} decision proposals per session")
    b = _bundle(s)
    row = _resolve_ref(s, b, a["ref"])
    note = _mask(s, {"note": a.get("note")})["note"]
    before = a.get("before_monthly") if a.get("before_monthly") is not None else row["monthly"]
    try:
        con = db.connect(s.cfg, insecure=s.insecure, migrate=False)
        try:
            con.execute("PRAGMA busy_timeout=8000")
            d = DEC.add(con, decision=a["decision"], today=_today(s), contract_id=row["contract_id"], series_id=row["series_id"], name=row["name"],
                        decided_on=a.get("decided_on"), effective_on=a.get("effective_on"), before=before, after=a.get("after_monthly"),
                        note=note, source="coach-llm")
        finally:
            con.close()
    except DEC.DecisionError as e:
        raise ToolError(f"the decision was refused: {e}") from None
    if not hasattr(s, "decisions_proposed"):
        s.decisions_proposed = []
    s.decisions_proposed.append(d.id)
    return {"decision_id": d.id, "state": "proposed", "decision": d.decision, "monthly_saving": money_str(d.monthly_saving_c),
            "next": ("This is only a PROPOSAL: it does not count until the user confirms it themselves (web app, Subscriptions, or "
                     "`coach subs decisions confirm`). You cannot confirm or reject it. The bank data will then verify it.")}


def _contracts_draft(s: ToolSession, a: dict):
    from coach.memory import proposals as P
    from coach.memory.store import MemoryStore, MemoryStoreError
    ids = list(dict.fromkeys(a["series"]))
    if len(ids) + len(s.proposals) > MAX_PROPOSALS:
        raise ToolError(f"at most {MAX_PROPOSALS} proposals per session: ask the user to review the pending ones first")
    rec = _recurring(s)
    known = {x.id for x in rec.series}
    for i in ids:
        if i not in known:
            raise ToolError(f"unknown series {i}: use a `rec_` id from `subscriptions_inventory` or `recurring`")
    store = MemoryStore(s.cfg.memory_dir, history=False, source="coach-llm")
    pending = {p.file for p in P.listing(store, "pending")}
    drafts = [d for d in DR.drafts(s.reg.ds, rec, {c.id for _r, c in store.contracts()}, set(ids)) if d.rel not in pending]
    if not drafts:
        raise ToolError("nothing to draft: those series already have a contract file (or a pending proposal) or are not contract-like")
    out = []
    for d in drafts:
        try:
            p = P.create(store, d.rel, DR.ops_for(d, iso=True), f"contract draft from recurring series {d.series_id} (the bank payments only: dates and terms to fill)",
                         source="coach-llm", evidence=[{"path": "(contract draft)", "suspicious": True, "snippet": "proposed in a session where tool data contained instruction-like text"}]
                         if s.suspicious else None)
        except (P.ProposalError, MemoryStoreError):
            raise ToolError("a draft could not be created (details withheld)") from None
        s.proposals.append(p.id)
        out.append({"proposal_id": p.id, "series": d.series_id, "kind": d.kind, "missing_fields": len(d.missing)})
    return {"proposals": out, "status": "pending", "suspicious_session": s.suspicious,
            "next": ("Nothing is written: give the proposal ids to the user, who reviews them in the web app (Memory > Proposals) or with "
                     "`uv run coach memory proposals` and accepts them THEMSELVES. You cannot accept, reject or revert proposals.")}


# ---------------------------------------------------------------- specs

REF = {"type": "string", "maxLength": 120, "description": "a `ref` of `subscriptions_inventory`: a `rec_` series id or contract:<id>"}


def specs() -> list[ToolSpec]:
    return [
        ToolSpec("subscriptions_inventory", "The canonical list of every recurring cost and contract: group, monthly / yearly cost, next charge, price changes, "
                 "contract status (on_file / missing / expired), usage as the household recorded it, cancellation rules (can cancel now, "
                 "earliest date, notice, early-termination cost, legal basis with source and review date), stored alternatives (current best, "
                 "outdated ones) and the latest decision. Loans, rent and taxes are not listed." + DATA_NOTE,
                 _obj({"group": {"type": "string", "enum": ["streaming_media", "software_cloud", "telecom", "memberships", "other_subscriptions",
                                                           "insurance", "energy_utilities"]},
                       "include_ended": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 60}}), _subscriptions_inventory),
        ToolSpec("savings_tracker", "The savings the household has actually realised: decisions (cancelled, renegotiated, switched, downgraded, kept) with "
                 "before / after monthly cost, whether the bank data confirms them (verified, pending, contradicted), the realised monthly "
                 "saving and the cumulative since the decisions. Read-only." + DATA_NOTE, _obj({}), _savings_tracker),
        ToolSpec("alternatives_record", "STORE a cheaper alternative found for a recurring service (used by the find-cheaper skill): the `ref` of the "
                 "service, provider, offer name, monthly price (> 0), a short features summary, the https `source_url` the price was read on "
                 "and `retrieved_at` (the date the price was seen, not in the future). Savings are computed by code; a quote older than 30 "
                 "days is shown as outdated. Public offer data only: no names, addresses or account details." + DATA_NOTE,
                 _obj({"ref": REF, "provider": {"type": "string", "minLength": 1, "maxLength": 80}, "offer_name": {"type": "string", "minLength": 1, "maxLength": 120},
                       "monthly_price": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000},
                       "features": {"type": "string", "maxLength": 400}, "source_url": {"type": "string", "maxLength": 500, "pattern": r"^https://"},
                       "retrieved_at": DATE, "switching_costs": {"type": "number", "minimum": 0, "maximum": 100000},
                       "notes": {"type": "string", "maxLength": 300}}, ("ref", "provider", "offer_name", "monthly_price", "source_url", "retrieved_at")),
                 _alternatives_record, writes="alternative"),
        ToolSpec("decision_propose", "PROPOSE that a decision the user told you about is recorded (cancelled, renegotiated, switched, downgraded, kept) with "
                 "the monthly cost before and after. It is only a proposal: it is not counted until the user confirms it themselves." + DATA_NOTE,
                 _obj({"ref": REF, "decision": {"type": "string", "enum": list(DEC.KINDS)}, "before_monthly": {"type": "number", "minimum": 0, "maximum": 100000},
                       "after_monthly": {"type": "number", "minimum": 0, "maximum": 100000}, "decided_on": DATE, "effective_on": DATE,
                       "note": {"type": "string", "maxLength": 200}}, ("ref", "decision")), _decision_propose, writes="decision-proposal"),
        ToolSpec("contracts_draft", "PROPOSE contract files for recurring series that have none: kind, provider, billing, bank-label match and start date "
                 "are filled from the payments, everything else stays empty. Creates sealed memory PROPOSALS the user reviews and accepts "
                 "themselves; nothing is written." + DATA_NOTE,
                 _obj({"series": {"type": "array", "minItems": 1, "maxItems": MAX_PROPOSALS, "items": {"type": "string", "pattern": r"^rec_[0-9a-f]{6,}$"}}},
                      ("series",)), _contracts_draft, writes="proposal"),
    ]
