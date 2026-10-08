"""CLI of the skills: ``coach onboarding status|run`` (E7-11) and ``coach contract check`` (E7-6).

``onboarding run`` is an INTERACTIVE interview for the person at the keyboard (it refuses to run without a terminal, so an
agent cannot drive it): every answer becomes a validated memory edit that is PREVIEWED (the diff) and written only after a typed
yes, recorded in the history with the source ``cli``; account owner / purpose go through ``coach accounts set``. It reuses the
open-questions generator and finishes with ``coach memory check``. ``contract check`` prints the cancellability of the
contracts on file (local, with the real provider names, no model involved).
"""
from __future__ import annotations

import datetime as dt
import sys
from typing import Callable, Optional

from coach import db as db_mod
from coach.analytics import api as analytics_api
from coach.analytics.recurring import detect_recurring
from coach.memory import check as check_mod, qgen, questions as q_mod, yamlio
from coach.memory.store import MemoryStore, MemoryStoreError
from coach.skills import cancel as C, onboarding as OB

PURPOSES = ("main", "cards", "rental", "kids", "savings")


def _is_tty() -> bool:
    from coach.memory.commands import _is_tty as t
    return t()


def _store(cfg, a) -> MemoryStore:
    return MemoryStore(cfg.memory_dir, history=cfg.memory_history, source="cli")


def _status(cfg, a, today: Optional[dt.date] = None):
    con = db_mod.connect(cfg, insecure=a.insecure)
    store = MemoryStore(cfg.memory_dir, history=False)
    ds = analytics_api.build_dataset(con, cfg, today)
    return con, store, ds, OB.onboarding_status(ds, store, con, cfg, detect_recurring(ds))


# ---------------------------------------------------------------- status

def cmd_status(a, cfg):
    con, store, ds, st = _status(cfg, a)
    try:
        if a.json:
            import json
            from coach.analytics.common import _plain
            from coach.i18n_msg import strip_msgs
            print(json.dumps(strip_msgs(_plain(st)), ensure_ascii=False, indent=2))
            return
        p = st["progress"]
        print(f"onboarding: {p['done']}/{p['total']} steps done" + (f"; next: {p['next_step']}" if p["next_step"] else ""))
        for s in st["steps"]:
            mark = {"done": "[x]", "partial": "[~]", "todo": "[ ]"}[s["status"]]
            print(f"{mark} {s['id']:<12}{s['heading']}")
            for m in s.get("missing") or []:
                print(f"      - {m if isinstance(m, str) else _acct_line(ds, m)}")
            for r in s.get("liabilities") or []:
                if r["missing"]:
                    print(f"      - {r['id']} ({r['kind']}): {', '.join(r['missing'])}")
            for k, label in (("recurring_without_contract", "recurring payments without a contract file"),
                             ("loan_payments_without_file", "loan-like payments without a liability file")):
                if s.get(k):
                    print(f"      - {len(s[k])} {label}")
            for r in s.get("contracts_with_empty_fields") or []:
                print(f"      - contract {r['id']}: {', '.join(r['missing'])}")
        print("\nnext actions:")
        for act in st["next_actions"]:
            print(f"  [{act['step']}] {act['do']}\n      {act['command']}")
        print("\n`uv run coach onboarding run` walks through them (interactive, every write previewed).")
    finally:
        con.close()


def _acct_line(ds, m) -> str:
    return f"account {ds.label(m['account'])}: {', '.join(m['missing'])} not set"


# ---------------------------------------------------------------- run (interactive)

class Interview:
    def __init__(self, cfg, a, ask: Optional[Callable[[str], str]] = None, out: Callable = print):
        self.cfg, self.a, self.out = cfg, a, out
        self.ask = ask or (lambda prompt: input(prompt))
        self.store = _store(cfg, a)
        self.written: list[str] = []

    def yes(self, prompt: str) -> bool:
        return self.ask(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")

    def apply(self, rel: str, ops: list[dict], action: str, reason: str) -> bool:
        """Preview an edit; write it after a typed yes. Returns True if written."""
        try:
            res = self.store.edit(rel, ops, action=action, reason=reason, source="cli", dry_run=True)
        except MemoryStoreError as e:
            self.out(f"  cannot apply: {e}")
            return False
        if not res.changed:
            self.out("  nothing to change")
            return False
        self.out(res.diff.rstrip("\n"))
        if not self.yes("  write this change?"):
            self.out("  skipped")
            return False
        done = self.store.edit(rel, ops, action=action, reason=reason, source="cli")
        self.out("  written" + (f" (change {done.change_id}; `coach memory revert {done.change_id}` undoes it)" if done.change_id else ""))
        self.written.append(rel)
        return True

    def value(self, text: str):
        return yamlio.to_plain(yamlio.parse_scalar(text.strip()))

    # -- steps
    def household(self, st: dict) -> None:
        step = next(s for s in st["steps"] if s["id"] == "household")
        self.out("\n== Household ==")
        have = step["have"]
        if not have["adults"]:
            self.out("No adult member yet. The id is what the coach sees (use a neutral id such as adult-a); the name stays on this machine.")
            while True:
                mid = self.ask("Member id (blank to stop): ").strip()
                if not mid:
                    break
                name = self.ask("  display name: ").strip()
                role = self.ask("  role (adult/child) [adult]: ").strip() or "adult"
                by = self.ask("  birth year (blank if unknown): ").strip()
                alias = self.ask("  holder-name spelling in bank data (blank for none): ").strip()
                m = {"id": mid, "name": name, "role": role}
                if by.isdigit():
                    m["birth_year"] = int(by)
                if alias:
                    m["aliases"] = [alias]
                ops = [{"op": "append", "path": "members", "value": m}]
                if not self.store.exists("household.yaml"):
                    ops = [{"op": "create", "value": {"members": []}}] + ops
                self.apply("household.yaml", ops, "add-member", "onboarding interview")
        if have["country"] is None:
            c = self.ask("Country for tax and cancellation rules (FR or IT) [FR]: ").strip().upper() or "FR"
            if c in ("FR", "IT"):
                self.apply("household.yaml", [{"op": "set", "path": "country", "value": c}], "set-country", "onboarding interview")
            else:
                self.out("  FR or IT only; skipped")
        for key, label in (("employers", "employer(s)"), ("places", "town(s) you live and work in"), ("schools", "school(s) / nursery(ies)")):
            if have.get(key) == 0 and (key != "schools" or have["children"]):
                raw = self.ask(f"Privacy declaration: {label}, comma separated (blank to skip): ").strip()
                if raw:
                    self.apply("household.yaml", [{"op": "set", "path": key, "value": [x.strip() for x in raw.split(",") if x.strip()]}],
                               f"declare-{key}", "onboarding interview")

    def accounts(self, st: dict, con) -> None:
        step = next(s for s in st["steps"] if s["id"] == "accounts")
        if not step["missing"]:
            return
        from coach.ingest import accounts as acc
        self.out("\n== Accounts ==")
        members = [m.id for m in self.store.members()]
        for m in step["missing"]:
            label = next((r["label"] or r["name"] or r["uid"] for r in acc.list_accounts(con) if r["uid"] == m["account"]), m["account"])
            self.out(f"account {label}: {', '.join(m['missing'])} not set")
            owner = self.ask(f"  owner ('joint' or a member id: {', '.join(members) or 'none yet'}; blank to skip): ").strip() if "owner" in m["missing"] else None
            purpose = self.ask(f"  purpose ({'/'.join(PURPOSES)}; blank to skip): ").strip() if "purpose" in m["missing"] else None
            if not (owner or purpose):
                continue
            if self.yes(f"  set owner={owner or '-'} purpose={purpose or '-'} on {label}?"):
                try:
                    acc.set_account(con, m["account"], owner=owner or None, purpose=purpose or None)
                    self.out("  done")
                except acc.AccountError as e:
                    self.out(f"  refused: {e}")

    def loans(self, st: dict) -> None:
        step = next(s for s in st["steps"] if s["id"] == "loans")
        rows = [r for r in step["liabilities"] if r["missing"]]
        if not rows:
            return
        self.out("\n== Loans (fields mortgage-check needs) ==")
        rels = {m.id: rel for rel, m in self.store.liabilities()}
        for r in rows:
            self.out(f"{r['id']} ({r['kind']}): missing {', '.join(r['missing'])}")
            ops = []
            for f in r["missing"]:
                if f == "payment_match":
                    continue
                raw = self.ask(f"  {f} (blank to skip; dates YYYY-MM-DD, amounts in EUR, rates in %): ").strip()
                if raw:
                    ops.append({"op": "set", "path": f, "value": self.value(raw)})
            if ops and r["id"] in rels:
                self.apply(rels[r["id"]], ops, "onboarding-loan", "onboarding interview: values given by the user")

    def preferences(self, st: dict) -> None:
        step = next(s for s in st["steps"] if s["id"] == "preferences")
        if not step["missing"]:
            return
        self.out("\n== Preferences ==")
        lines = []
        for key, q in (("Language", "language of the reports (fr/en/it)"), ("Tone", "tone (short, direct, detailed...)"),
                       ("Goals", "savings goals or things you care about"), ("Never", "topics to avoid")):
            v = self.ask(f"  {q} (blank to skip): ").strip()
            if v:
                lines.append(f"- {key}: {v}")
        if lines:
            self.apply("preferences.md", [{"op": "append_text", "value": "\n".join(lines) + "\n"}], "append", "onboarding interview")

    def questions(self, con) -> None:
        self.out("\n== Open questions ==")
        res = qgen.generate(self.store, con, self.cfg)
        if not res.new:
            self.out("no new questions from the data")
            return
        self.out(f"{len(res.new)} new question(s) could be added from the data ({', '.join(f'{k} {v}' for k, v in res.by_type.items())})")
        if self.yes("  add them to the open questions?"):
            q_mod.add_many(self.store, res.new, source="cli")
            self.out("  added; answer them with `uv run coach questions list --open` or the Memory page")

    def run(self, con, today: Optional[dt.date] = None) -> dict:
        ds = analytics_api.build_dataset(con, self.cfg, today)
        st = OB.onboarding_status(ds, MemoryStore(self.cfg.memory_dir, history=False), con, self.cfg, detect_recurring(ds))
        self.out(f"Onboarding: {st['progress']['done']}/{st['progress']['total']} steps done. Every change is previewed and "
                 "written only after you type y.")
        self.household(st)
        self.accounts(st, con)
        self.loans(st)
        self.preferences(st)
        self.questions(con)
        self.out("\n== Check ==")
        issues = check_mod.run_check(MemoryStore(self.cfg.memory_dir, history=False), con, stale_months=self.cfg.memory_stale_months,
                                     asset_stale_months=self.cfg.memory_asset_stale_months, cfg=self.cfg)
        s = check_mod.summarize(issues)
        self.out(check_mod.format_issues(issues))
        self.out(f"memory check: {s['errors']} error(s), {s['warnings']} warning(s). {len(self.written)} file change(s) written.")
        return s


def cmd_run(a, cfg):
    if not _is_tty():
        sys.exit("error: the onboarding interview is interactive: run it yourself in a terminal "
                 "(`uv run coach onboarding run`). Use `coach onboarding status` for the checklist.")
    con = db_mod.connect(cfg, insecure=a.insecure)
    try:
        s = Interview(cfg, a).run(con)
    finally:
        con.close()
    if s["errors"]:
        sys.exit(1)


# ---------------------------------------------------------------- contract check

def cmd_contract_check(a, cfg):
    store = MemoryStore(cfg.memory_dir, history=False)
    country = (a.country or ((store.load_plain("household.yaml") if store.exists("household.yaml") else {}).get("country")) or "FR").upper()
    today = dt.date.today()
    cs = [(rel, c) for rel, c in store.contracts() if not a.id or c.id == a.id]
    if not cs:
        sys.exit("error: no contract on file" + (f" with id {a.id}" if a.id else "") + " (`coach memory new contract`, or `coach memory doc add`)")
    for rel, c in cs:
        r = C.cancellability_of(c, today, country)
        verdict = {True: "YES, now", False: "NOT yet", None: "cannot tell from the file"}[r["can_cancel_now"]]
        print(f"{c.id} ({c.provider or 'provider ?'}, {c.kind or 'kind ?'}): can cancel: {verdict}")
        if "early_termination_cost" in r:
            e = r["early_termination_cost"]
            print(f"   early exit costs: {e['amount'] if e['amount'] is not None else '?'} EUR ({e['basis']}; {e['remaining_months']} month(s) left); free from {e['free_exit_date']}")
        if r["earliest_effective_date"]:
            print(f"   earliest effective date: {r['earliest_effective_date']}   notice: {r['notice_period_days'] or 'see contract'} day(s)")
        print(f"   method: {r['method']}")
        for x in r["conditions"]:
            print(f"   - {x}")
        if r["unknown"]:
            print(f"   unknown: {', '.join(r['unknown'])}  (fill them: `coach memory set {c.id} <field> <value>`)")
        for k in ("anniversary_route", "contract_notice_deadline"):
            if k in r:
                print(f"   {k}: {r[k]}")
        print("   rules: " + "; ".join(f"{x['name']} ({x['law']})" for x in r["rules"]))
    print("\n" + C.DISCLAIMER)


def register(sub, add):
    op = sub.add_parser("onboarding", help="the onboarding interview: what the coach still does not know (E7-11)",
                        description="onboarding checklist and interactive interview; every write is previewed")
    osub = op.add_subparsers(dest="onboarding_cmd", required=True, metavar="SUBCOMMAND")
    s = add(osub, "status", cmd_status, "the checklist of what is missing and the next actions")
    s.add_argument("--json", action="store_true")
    add(osub, "run", cmd_run, "walk through the missing facts (interactive; every change previewed, written after a typed yes)")
    cp = sub.add_parser("contract", help="contracts on file: can they be cancelled? (FR / IT rules table, E7-6)")
    csub = cp.add_subparsers(dest="contract_cmd", required=True, metavar="SUBCOMMAND")
    s = add(csub, "check", cmd_contract_check, "cancellability of the contracts on file (local; verify with your contract)")
    s.add_argument("id", nargs="?")
    s.add_argument("--country", choices=["FR", "IT"])
