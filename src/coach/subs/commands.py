"""CLI of the subscriptions & contracts optimizer (E8): ``coach subs ...``.

    list [--json]                      the canonical inventory (E8-1)
    show <ref>                         one service in full: cost, price history, contract, usage, cancellation rules, alternatives, decision
    draft-contracts [--dry-run|--write] contract drafts for the contract-like series without a contract file
    usage <ref> --frequency ...        record how you use a service (E8-2); usage-questions [--add]
    alternatives add|list|remove       the alternatives store (E8-4)
    letter <contract> [--lang --channel] a cancellation letter / e-mail text, generated locally (E8-5); contact show|set
    decide <ref> <decision> ...        record a decision (E8-6); decisions list|confirm|reject|remove; savings

Every write to the memory is validated and PREVIEWED; it is applied after a typed yes in a terminal (or ``--yes`` for you, the
user). Nothing here sends a letter, calls a website or contacts a provider.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Optional

from coach import db as db_mod
from coach.analytics.common import _plain
from coach.memory import proposals as P, questions as q_mod
from coach.memory.schemas import USAGE_FREQUENCIES
from coach.memory.store import MemoryStore, MemoryStoreError
from coach.subs import alternatives as A, decisions as DEC, draft as DR, letters as L, service as S, usage as U


def _today() -> dt.date:
    return dt.date.today()


def _is_tty() -> bool:
    from coach.memory.commands import _is_tty as t
    return t()


def _store(cfg, source: str = "cli") -> MemoryStore:
    return MemoryStore(cfg.memory_dir, history=cfg.memory_history, source=source)


def _date(v: Optional[str], what: str) -> Optional[dt.date]:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        sys.exit(f"error: {what} must be a date YYYY-MM-DD")


def _json(obj) -> str:
    return json.dumps(_plain(obj), ensure_ascii=False, indent=2, default=str)


def _bundle(a, cfg, **kw):
    con = db_mod.connect(cfg, insecure=a.insecure)
    return con, S.load_bundle(con, cfg, _today(), **kw)


def _row(b, ref):
    try:
        return S.resolve(b.inv, ref)
    except S.RefError as e:
        sys.exit(f"error: {e}")


def _confirm(prompt: str, yes: bool) -> bool:
    if not _is_tty():                       # --yes only skips the typed prompt for a human at a terminal
        sys.exit("error: this change needs a person at a terminal: run this yourself in a terminal (--yes then skips the prompt), or use --propose")
    if yes:
        return True
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _apply(store, rel, ops, action, reason, *, yes=False, dry_run=False) -> bool:
    """Preview an edit and write it after a typed yes. Returns True if written."""
    try:
        res = store.edit(rel, ops, action=action, reason=reason, source="cli", dry_run=True)
    except MemoryStoreError as e:
        sys.exit(f"error: {e}")
    if not res.changed:
        print(f"  {rel}: nothing to change")
        return False
    print(res.diff.rstrip("\n"))
    for i in res.issues:
        if i.level != "error":
            print(f"  warning: {i.message}")
    if dry_run:
        print("  (dry run: nothing written)")
        return False
    if not _confirm("  write this change?", yes):
        print("  skipped")
        return False
    done = store.edit(rel, ops, action=action, reason=reason, source="cli")
    print("  written" + (f" (change {done.change_id}; `coach memory revert {done.change_id}` undoes it)" if done.change_id else ""))
    return True


# ---------------------------------------------------------------- list / show

def _money(v) -> str:
    return "?" if v is None else f"{v:>8}"


def cmd_list(a, cfg):
    con, b = _bundle(a, cfg, include_ended=a.all)
    con.close()
    inv = b.inv
    rows = [r for r in inv["rows"] if (not a.group or r["group"] == a.group) and (not a.missing_contract or r["contract"]["status"] == "missing")]
    if a.json:
        print(_json({**inv, "rows": rows}))
        return
    t = inv["totals"]
    print(f"{t['services']} recurring costs: {t['monthly']} EUR/month, {t['yearly']} EUR/year  ({inv['as_of']}, country {inv['country']})")
    print(f"contracts: {t['without_contract']} without a file, {t['expired_contracts']} with dates all in the past; "
          f"usage unknown on {t['usage_unknown']} discretionary service(s); {t['outdated_alternatives']} outdated alternative quote(s)")
    for g, v in inv["groups"].items():
        print(f"  {v['label']:<22}{v['count']:>3}  {v['monthly']:>9}/month  {v['yearly']:>10}/year" + (f"  ({v['without_contract']} without contract)" if v["without_contract"] else ""))
    cur = None
    for r in rows:
        if r["group"] != cur:
            cur = r["group"]
            print(f"\n== {r['group_label']}")
        flags = []
        if r["status"] == "ended":
            flags.append("ended")
        flags.append({"on_file": "contract", "missing": "NO contract", "expired": "contract dates past"}[r["contract"]["status"]])
        if r["usage"]["recorded"]:
            flags.append(f"usage {r['usage']['frequency']}")
        flags += [s["kind"] for s in r["usage"]["signals"]]
        if r["alternatives"]["best"]:
            flags.append(f"best alt saves {r['alternatives']['best']['savings']['yearly']}/yr")
        if r["decision"]:
            flags.append(f"{r['decision']['decision']} ({r['decision']['status']})")
        print(f"{r['ref']:<20}{r['name'][:26]:<27}{_money(r['monthly'])}/mo {_money(r['yearly'])}/yr  next {r['next_charge'] or '-':<11} "
              + "; ".join(flags))
    s = inv["savings"]
    print(f"\nrealised savings: {s['realised_monthly']} EUR/month ({s['realised_since_decisions']} since the decisions; {s['verified']} verified, "
          f"{s['pending']} pending, {s['contradicted']} contradicted)")
    print("`coach subs show <ref>` for one service; `coach subs draft-contracts --dry-run` for the missing contract files.")


def cmd_show(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    con.close()
    r = _row(b, a.ref)
    if a.json:
        print(_json(r))
        return
    print(f"{r['name']}  [{r['ref']}]  {r['group_label']}  kind {r['kind']}")
    print(f"cost: {r['monthly'] or '?'} EUR/month, {r['yearly'] or '?'} EUR/year ({r['cost_source']}); {r['cadence'] or '?'}; next charge {r['next_charge'] or '-'}")
    for h in r["price_history"]:
        print(f"  price from {h['from']}: {h['amount']}")
    c = r["contract"]
    print(f"contract: {c['status']}" + (f" ({c['id']}; renewal {c['renewal'] or '?'}, commitment end {c['commitment_end'] or '?'}, notice {c['notice_period_days'] or '?'} d)" if c["id"] else ""))
    u = r["usage"]
    print(f"usage: {u['frequency']}" + (f", last used {u['last_used']}" if u["last_used"] else "") + ("" if u["recorded"] else f"  ({u['note_not_measurable']})"))
    for sg in u["signals"]:
        print(f"  reminder: {sg['kind']} ({sg['measurable']})")
    ci = r["cancellation"]
    verdict = {True: "YES, now", False: "NOT yet", None: "cannot tell from the file"}[ci["can_cancel_now"]]
    print(f"can cancel: {verdict}; earliest effective date {ci['earliest_effective_date'] or '-'}; notice {ci['notice_period_days'] or 'see contract'} day(s)")
    print(f"  method: {ci['method']}")
    if ci["early_termination_cost"]:
        e = ci["early_termination_cost"]
        print(f"  early exit costs: {e['amount'] if e['amount'] is not None else '?'} EUR ({e['basis']}); free from {e['free_exit_date']}")
    for x in ci["conditions"]:
        print(f"  - {x}")
    if ci["unknown"]:
        print(f"  unknown: {', '.join(ci['unknown'])}")
    for x in ci["legal_basis"]:
        print(f"  basis: {x['name']} - {x['law']} (reviewed {x['last_reviewed']})")
    print(f"  {ci['verify']}")
    al = r["alternatives"]
    print(f"alternatives: {al['count']} ({al['outdated']} outdated)")
    for i in al["items"]:
        sv = i["savings"]
        print(f"  {i['id']}  {i['provider']} / {i['offer']}  {i['monthly_price']}/mo  {i['label']}  source {i['source_url'] or '-'}  seen {i['retrieved_at']}"
              + (f"  saves {sv['yearly']}/yr, net 12m {sv['net_12m']}" if sv else ""))
    if r["decision"]:
        d = r["decision"]
        print(f"decision: {d['decision']} on {d['decided_on']} ({d['before_monthly']} -> {d['after_monthly']} a month): {d['status']} - {d['reason']}")


# ---------------------------------------------------------------- draft-contracts

def cmd_draft(a, cfg):
    con, b = _bundle(a, cfg)
    store = _store(cfg)
    drafts = DR.drafts(b.ds, b.rec, {c.id for _r, c in store.contracts()}, set(a.series) if a.series else None, a.limit)
    today = _today()
    if not drafts:
        con.close()
        print("no contract-like recurring series without a contract file")
        return
    print(f"{len(drafts)} contract draft(s); by kind: " + ", ".join(f"{k} {v}" for k, v in DR.by_kind(drafts).items()))
    for d in drafts:
        print(f"  {d.series_id}  {d.contract_id:<24}{d.kind:<17}{d.value['billing']['amount'] or '?'} {d.value['billing']['period'] or '?'}  match {d.value['merchant_match'] or '-'}"
              f"  missing: {', '.join(d.missing)}")
        for w in d.warnings:
            print(f"      warning: {w}")
    if a.dry_run:
        con.close()
        print("(dry run: nothing created)")
        return
    if a.write:
        n = 0
        for d in drafts:
            print(f"\n== {d.rel}")
            if _apply(store, d.rel, DR.ops_for(d), "draft-contract", f"drafted from recurring series {d.series_id}", yes=a.yes):
                n += 1
                q = DR.question_for(d, today)
                if q.key not in {x.key for x in store.questions()}:
                    q_mod.add_many(store, [q], source="cli")
        con.close()
        print(f"\n{n} contract file(s) written, with an open question for the missing fields. Fill them: `coach memory set <id> <field> <value>`.")
        return
    created = []
    pending = {p.file for p in P.listing(store, "pending")}
    for d in drafts:
        if d.rel in pending:
            print(f"  {d.contract_id}: already proposed (pending): not proposed again")
            continue
        try:
            p = P.create(store, d.rel, DR.ops_for(d, iso=True), f"contract draft from recurring series {d.series_id}", source="cli")
        except (P.ProposalError, MemoryStoreError) as e:
            print(f"  {d.contract_id}: not proposed ({e})")
            continue
        created.append(p.id)
    qs = [DR.question_for(d, today) for d in drafts]
    existing = {x.key for x in store.questions()}
    qs = [q for q in qs if q.key not in existing]
    if qs and created:                                  # the questions only go with newly proposed drafts
        from coach.memory import questions as Q
        ops = [{"op": "append", "path": "questions", "value": x} for x in Q.questions_to_doc(qs)["questions"]]
        if not store.exists(Q.YAML_FILE):
            ops = [{"op": "create", "value": {"questions": []}}] + ops
        try:
            created.append(P.create(store, Q.YAML_FILE, ops, "questions for the fields missing from the drafted contracts", source="cli").id)
        except (P.ProposalError, MemoryStoreError) as e:
            print(f"  questions not proposed ({e})")
    con.close()
    print(f"\n{len(created)} proposal(s) created: {', '.join(created)}\nReview them with `uv run coach memory proposals` (or the web app, Memory > Proposals) and accept them yourself.")


# ---------------------------------------------------------------- usage

def cmd_usage(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    con.close()
    r = _row(b, a.ref)
    store = _store(cfg)
    if not r["contract_id"]:
        sys.exit(f"error: {r['ref']} has no contract file to hold the usage: `coach subs draft-contracts --series {r['ref']} --write` first")
    rel = S.contract_file(store, r["contract_id"])
    try:
        ops = S.usage_ops(a.frequency, _date(a.last_used, "--last-used"), a.note)
    except ValueError as e:
        sys.exit(f"error: {e}")
    _apply(store, rel, ops, "usage", "usage recorded by the user", yes=a.yes, dry_run=a.dry_run)


def cmd_usage_questions(a, cfg):
    con, b = _bundle(a, cfg)
    store = _store(cfg)
    qs, skipped = U.usage_questions(con, cfg, store, b.rec, _today(), limit=a.limit)
    con.close()
    for q in qs:
        print(f"  {q.id}  {q.question}")
    print(f"{len(qs)} usage question(s) to ask ({skipped} already asked, open, answered or dismissed)")
    if qs and a.add:
        q_mod.add_many(store, qs, source="cli")
        print("added to the open questions: `coach questions list --open`")
    elif qs:
        print("`coach subs usage-questions --add` adds them to the open questions.")


# ---------------------------------------------------------------- alternatives

def cmd_alt_add(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    r = _row(b, a.ref)
    try:
        alt = A.add(con, contract_id=r["contract_id"], series_id=r["series_id"], today=_today(), provider=a.provider, offer_name=a.offer,
                    monthly_price=a.price, features=a.features, source_url=a.url, retrieved_at=a.date or _today().isoformat(),
                    method=a.method, notes=a.notes, switching_costs=a.switching_costs, source="cli")
    except A.AlternativeError as e:
        con.close()
        sys.exit(f"error: {e}")
    con.close()
    print(f"stored {alt.id}: {alt.provider} / {alt.offer_name} {alt.monthly_price_c / 100:.2f} EUR/month, seen {alt.retrieved_at}")


def cmd_alt_list(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    con.close()
    rows = [r for r in b.inv["rows"] if r["alternatives"]["count"] and (not a.ref or r["ref"] == _row(b, a.ref)["ref"])]
    if a.json:
        print(_json({r["ref"]: r["alternatives"] for r in rows}))
        return
    if not rows:
        print("no alternatives stored (`coach subs alternatives add`, or the find-cheaper skill)")
    for r in rows:
        al = r["alternatives"]
        print(f"{r['name']} [{r['ref']}] pays {r['monthly']}/month; best current: "
              + (f"{al['best']['provider']} {al['best']['offer']} saves {al['best']['savings']['yearly']}/year" if al["best"] else "none (no fresh cheaper quote)"))
        for i in al["items"]:
            sv = i["savings"] or {}
            print(f"   {i['id']}  {i['provider']} / {i['offer']}  {i['monthly_price']}/mo  [{i['label']}]  {i['method']}/{i['source']}  {i['source_url'] or 'no url'}"
                  + (f"  saves {sv['yearly']}/yr, net 12m {sv['net_12m']}, break-even {sv['break_even_months']} mo" if sv else ""))


def cmd_alt_remove(a, cfg):
    con = db_mod.connect(cfg, insecure=a.insecure)
    ok = A.remove(con, a.id)
    con.close()
    print("removed" if ok else f"no alternative {a.id}")


# ---------------------------------------------------------------- letter / contact

def cmd_letter(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    con.close()
    store = _store(cfg)
    ref = a.contract
    cid = next((c.id for _r, c in store.contracts() if c.id == ref), None)
    if cid is None:
        row = _row(b, ref)
        cid = row["contract_id"]
        if cid is None:
            sys.exit(f"error: {row['ref']} has no contract file: `coach subs draft-contracts --series {row['ref']} --write` first")
    human = _is_tty()
    try:
        out = S.render_letter(store, b.ds, cid, lang=a.lang, channel=a.channel, holder=a.holder, today=_today(), private=human)
    except (S.RefError, ValueError) as e:
        sys.exit(f"error: {e}")
    if not human:                                          # an agent-driven shell is not a terminal: no name, address or contract number
        out["notes"].insert(0, "not run in a terminal: your name, address, e-mail and contract number are left as [placeholders]; run this "
                               "command yourself in a terminal to fill them")
    if a.json:
        print(_json(out))
        return
    if a.out:
        from pathlib import Path
        Path(a.out).write_text(out["text"], encoding="utf-8")
        print(f"letter written to {a.out}")
    else:
        print(out["text"])
    print("---")
    if out["placeholders"]:
        print("to complete: " + "; ".join(out["placeholders"]))
    for n in out["notes"]:
        print(f"* {n}")
    print(f"legal basis: " + "; ".join(f"{x['name']} ({x['source']})" for x in out["legal_basis"]))
    print(f"[{out['pdf_note']}]")


def cmd_contact(a, cfg):
    store = _store(cfg)
    if a.contact_cmd == "show":
        c = S.contact_of(store)
        if not _is_tty():
            print("contact (local only, never sent to a model): " + (f"set ({', '.join(c)}); the values are shown only in a terminal" if c else "not set"))
            return
        print("contact (local only, never sent to a model): " + (", ".join(f"{k}={v!r}" for k, v in c.items()) if c else "not set"))
        return
    val = {k: v for k, v in (("address", a.address), ("email", a.email), ("phone", a.phone)) if v}
    if not val:
        sys.exit("error: give --address, --email or --phone")
    if "address" in val:
        val["address"] = val["address"].replace("\\n", "\n")
    cur = S.contact_of(store)
    ops = []
    if not store.exists("household.yaml"):
        ops.append({"op": "create", "value": {"members": []}})
    ops.append({"op": "set", "path": "contact", "value": {**cur, **val}})
    _apply(store, "household.yaml", ops, "set-contact", "contact details for cancellation letters (local only)", yes=a.yes, dry_run=a.dry_run)


# ---------------------------------------------------------------- decisions / savings

def cmd_decide(a, cfg):
    con, b = _bundle(a, cfg, include_ended=True)
    r = _row(b, a.ref)
    before = a.before if a.before is not None else (r["monthly"] if r["monthly"] is not None else None)
    after = a.after
    today = _today()
    try:
        preview = {"ref": r["ref"], "name": r["name"], "decision": a.decision, "before": before, "after": after if after is not None else
                   ("0" if a.decision == "cancelled" else before if a.decision == "kept" else None), "decided_on": a.date or today.isoformat(),
                   "effective_on": a.effective}
        print("decision: " + ", ".join(f"{k}={v}" for k, v in preview.items() if v is not None))
        if a.dry_run:
            DEC.check(decision=a.decision, today=today, contract_id=r["contract_id"], series_id=r["series_id"], name=r["name"],
                      decided_on=a.date, effective_on=a.effective, before=before, after=after, note=a.note, source="cli")
            print("(dry run: valid, nothing stored)")
            con.close()
            return
        if not _confirm("  record this decision?", a.yes):
            print("  skipped")
            con.close()
            return
        d = DEC.add(con, decision=a.decision, today=today, contract_id=r["contract_id"], series_id=r["series_id"], name=r["name"],
                    decided_on=a.date, effective_on=a.effective, before=before, after=after, note=a.note, source="cli")
    except DEC.DecisionError as e:
        con.close()
        sys.exit(f"error: {e}")
    con.close()
    print(f"recorded {d.id}: monthly saving {d.monthly_saving_c / 100:.2f} EUR; the bank data will confirm it (`coach subs savings`)")


def cmd_decisions(a, cfg):
    con = db_mod.connect(cfg, insecure=a.insecure)
    try:
        if a.dec_cmd == "list":
            b = S.load_bundle(con, cfg, _today(), include_ended=True)
            rows = DEC.savings([d for d in b.decisions], b.rec, b.ds.today)["decisions"]
            proposed = [d for d in b.decisions if d.state == "proposed"]
            if a.json:
                print(_json({"decisions": rows, "proposed": [{"id": d.id, "decision": d.decision, "name": d.name, "source": d.source} for d in proposed]}))
                return
            for r in rows:
                print(f"{r['id']}  {r['decision']:<12}{r['name'] or r['contract'] or r['series']:<24}{r['before_monthly']:>8} -> {r['after_monthly']:>8}  {r['decided_on']}  {r['status']}: {r['reason']}")
            for d in proposed:
                print(f"{d.id}  PROPOSED by {d.source}: {d.decision} {d.name or d.contract_id or d.series_id} {d.before_c / 100:.2f} -> {d.after_c / 100:.2f}; "
                      f"confirm it yourself with `coach subs decisions confirm {d.id}`")
            if not rows and not proposed:
                print("no decisions recorded (`coach subs decide <ref> cancelled|renegotiated|switched|downgraded|kept`)")
        elif a.dec_cmd in ("confirm", "reject"):
            if not _is_tty():
                sys.exit(f"error: `{a.dec_cmd}` is a decision for you: run it yourself in a terminal")
            d = DEC.get(con, a.id)
            if d is None:
                sys.exit(f"error: no decision {a.id}")
            print(f"{d.id}: {d.decision} {d.name or d.contract_id or d.series_id}, {d.before_c / 100:.2f} -> {d.after_c / 100:.2f} a month (proposed by {d.source}) {d.note or ''}")
            if input(f"  {a.dec_cmd} it? [y/N] ").strip().lower() not in ("y", "yes"):
                print("  skipped")
                return
            (DEC.confirm if a.dec_cmd == "confirm" else DEC.reject)(con, a.id)
            print("  done")
        elif a.dec_cmd == "remove":
            print("removed" if DEC.remove(con, a.id) else f"no decision {a.id}")
    except DEC.DecisionError as e:
        sys.exit(f"error: {e}")
    finally:
        con.close()


def cmd_savings(a, cfg):
    con = db_mod.connect(cfg, insecure=a.insecure)
    b = S.load_bundle(con, cfg, _today(), include_ended=True)
    con.close()
    s = DEC.savings(b.decisions, b.rec, b.ds.today)
    if a.json:
        print(_json(s))
        return
    print(f"realised savings (verified by the bank data): {s['realised_monthly']} EUR/month, {s['realised_yearly_run_rate']} EUR/year run rate, "
          f"{s['realised_since_decisions']} EUR since the decisions")
    print(f"verified {s['verified']}, pending {s['pending']} (claimed {s['claimed_monthly_unverified']} EUR/month), contradicted {s['contradicted']}")
    for r in s["decisions"]:
        print(f"  {r['id']}  {r['decision']:<12}{(r['name'] or '')[:22]:<23}saves {r['monthly_saving']:>7}/month, {r['months_counted']} month(s) = {r['since_decision']:>8}  {r['status']}: {r['reason']}")
    print(s["note"])


# ---------------------------------------------------------------- registration

def register(sub, add):
    sp = sub.add_parser("subs", help="subscriptions & contracts optimizer: inventory, usage, cancellation, alternatives, letters, savings (E8)",
                        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ss = sp.add_subparsers(dest="subs_cmd", required=True, metavar="SUBCOMMAND")
    s = add(ss, "list", cmd_list, "every recurring cost and contract in one list, with yearly cost, contract status, usage, cancellation")
    s.add_argument("--json", action="store_true")
    s.add_argument("--all", action="store_true", help="include ended series")
    s.add_argument("--group", choices=["streaming_media", "software_cloud", "telecom", "memberships", "other_subscriptions", "insurance", "energy_utilities"])
    s.add_argument("--missing-contract", action="store_true", help="only services without a contract file")
    s = add(ss, "show", cmd_show, "one service in full (price history, contract, usage, cancellation rules, alternatives, decision)")
    s.add_argument("ref", help="rec_... id, contract:<id>, a contract id or a unique part of the name")
    s.add_argument("--json", action="store_true")
    s = add(ss, "draft-contracts", cmd_draft, "contract drafts for the contract-like series without a contract file (proposals by default)")
    s.add_argument("--dry-run", action="store_true", help="show the drafts, create nothing")
    s.add_argument("--write", action="store_true", help="write the contract files directly after a preview (source cli) instead of proposals")
    s.add_argument("--yes", action="store_true", help="with --write: skip the typed confirmation (you read the preview)")
    s.add_argument("--series", nargs="+", metavar="REC_ID", help="only these series")
    s.add_argument("--limit", type=int)
    s = add(ss, "usage", cmd_usage, "record how you use a service (frequency, last used, note) in its contract file")
    s.add_argument("ref")
    s.add_argument("--frequency", required=True, choices=list(USAGE_FREQUENCIES))
    s.add_argument("--last-used", metavar="YYYY-MM-DD")
    s.add_argument("--note")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true")
    s = add(ss, "usage-questions", cmd_usage_questions, "the usage questions still to ask (never duplicates); --add stores them")
    s.add_argument("--add", action="store_true")
    s.add_argument("--limit", type=int, default=10)
    alt = ss.add_parser("alternatives", help="the alternatives store: add | list | remove (E8-4)", description="cheaper offers with source and date")
    asub = alt.add_subparsers(dest="alt_cmd", required=True, metavar="SUBCOMMAND")
    s = add(asub, "add", cmd_alt_add, "store an alternative offer (price, source URL https, date seen)")
    s.add_argument("ref")
    s.add_argument("--provider", required=True)
    s.add_argument("--offer", required=True, help="offer name")
    s.add_argument("--price", required=True, type=float, help="monthly price in EUR")
    s.add_argument("--url", help="https page the price was read on")
    s.add_argument("--date", metavar="YYYY-MM-DD", help="when the price was seen (default today)")
    s.add_argument("--features")
    s.add_argument("--switching-costs", type=float, default=0)
    s.add_argument("--method", choices=list(A.METHODS), default="manual")
    s.add_argument("--notes")
    s = add(asub, "list", cmd_alt_list, "alternatives per service, with savings computed by code and the staleness")
    s.add_argument("ref", nargs="?")
    s.add_argument("--json", action="store_true")
    s = add(asub, "remove", cmd_alt_remove, "delete one stored alternative")
    s.add_argument("id")
    s = add(ss, "letter", cmd_letter, "a cancellation letter / e-mail text for a contract, generated locally (nothing is sent)")
    s.add_argument("contract", help="contract id (or a service ref that has a contract file)")
    s.add_argument("--lang", choices=list(L.LANGS), help="default: from the household country")
    s.add_argument("--channel", choices=list(L.CHANNELS), default="lrar", help="lrar = registered letter, email, online = text for the cancellation form")
    s.add_argument("--holder", help="household member id or name to sign with (default: the first adult)")
    s.add_argument("--out", metavar="FILE", help="write the text to a file")
    s.add_argument("--json", action="store_true")
    ct = ss.add_parser("contact", help="your postal address / e-mail for letters (household.yaml, local only)", description="contact block")
    csub = ct.add_subparsers(dest="contact_cmd", required=True, metavar="SUBCOMMAND")
    add(csub, "show", cmd_contact, "show the stored contact block")
    s = add(csub, "set", cmd_contact, "set address / e-mail / phone (previewed; never sent to a model)")
    s.add_argument("--address", help="postal address; use \\n for a line break")
    s.add_argument("--email")
    s.add_argument("--phone")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true")
    s = add(ss, "decide", cmd_decide, "record what you decided: cancelled, renegotiated, switched, downgraded or kept")
    s.add_argument("ref")
    s.add_argument("decision", choices=list(DEC.KINDS))
    s.add_argument("--before", type=float, help="monthly cost before (default: the current monthly cost)")
    s.add_argument("--after", type=float, help="monthly cost after (0 for cancelled)")
    s.add_argument("--date", metavar="YYYY-MM-DD", help="decision date (default today)")
    s.add_argument("--effective", metavar="YYYY-MM-DD", help="when it takes effect (default: the decision date)")
    s.add_argument("--note")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true")
    ds_ = ss.add_parser("decisions", help="list | confirm | reject | remove the recorded decisions", description="decisions")
    dsub = ds_.add_subparsers(dest="dec_cmd", required=True, metavar="SUBCOMMAND")
    s = add(dsub, "list", cmd_decisions, "decisions with their verification status")
    s.add_argument("--json", action="store_true")
    s = add(dsub, "confirm", cmd_decisions, "confirm a decision the coach PROPOSED (terminal only)")
    s.add_argument("id")
    s = add(dsub, "reject", cmd_decisions, "reject a decision the coach proposed (terminal only)")
    s.add_argument("id")
    s = add(dsub, "remove", cmd_decisions, "delete a decision")
    s.add_argument("id")
    s = add(ss, "savings", cmd_savings, "realised savings: verified by the bank data, pending, contradicted")
    s.add_argument("--json", action="store_true")
