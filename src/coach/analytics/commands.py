"""CLI handlers of the analytics commands (E4). Thin: each builds a Dataset, calls a pure function of
:mod:`coach.analytics` and prints text or ``--json`` (the result's ``to_dict()``)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from coach.analytics import api
from coach.analytics.common import Scope, money_str, parse_money


def _fmt(c) -> str:
    return "-" if c is None else f"{c / 100:,.2f}"


def _pc(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _j(res) -> str:
    return json.dumps(res.to_dict() if hasattr(res, "to_dict") else res, ensure_ascii=False, indent=2)


def _dataset(a, cfg):
    from coach.db import connect
    con = connect(cfg, insecure=a.insecure)
    today = dt.date.fromisoformat(a.as_of) if getattr(a, "as_of", None) else None
    return con, api.build_dataset(con, cfg, today)


def _scope(a, ds=None) -> Scope:
    accounts = None
    if getattr(a, "account", None):
        acc = ds.resolve_account(a.account) if ds else None
        if ds and acc is None:
            sys.exit(f"error: unknown account {a.account!r} (see `coach coverage`)")
        accounts = [acc.uid] if acc else [a.account]
    return Scope.make(getattr(a, "owner", None) and [a.owner], getattr(a, "purpose", None) and [a.purpose], accounts)


def _coverage_lines(cov) -> None:
    if cov.notes:
        for n in cov.notes:
            print(f"  note: {n}")


# ---------------------------------------------------------------- coverage / averages / cashflow

def cmd_coverage(a, cfg):
    con, ds = _dataset(a, cfg)
    rows = ds.coverage.table()
    if a.json:
        print(_j([r.to_dict() for r in rows]))
        return
    print(f"account coverage as of {ds.today} (a month counts when the account has data for all its days)")
    print(f"{'account':<26}{'bank':<22}{'owner':<9}{'purpose':<9}{'first':<12}{'last':<12}{'tx':>5}{'full months':>13}")
    for r in rows:
        print(f"{r.label[:25]:<26}{(r.bank or '')[:21]:<22}{(r.owner or '-'):<9}{(r.purpose or '-'):<9}"
              f"{str(r.first or '-'):<12}{str(r.last or '-'):<12}{r.n_tx:>5}{r.n_months:>13}")
    if ds.foreign:
        print(f"{len(ds.foreign)} non-EUR transaction(s) are left out of every figure")


def cmd_averages(a, cfg):
    from coach.analytics.averages import category_averages
    con, ds = _dataset(a, cfg)
    r = category_averages(ds, _scope(a, ds), a.window)
    if a.json:
        print(_j(r))
        return
    hh = r.household_months
    print(f"monthly averages, coverage-aware (window {r.window_months} months; one-offs and capital excluded)")
    if hh:
        print(f"household: {_fmt(r.household_monthly_avg_c)} EUR/month over {len(hh)} months {hh[0]} -> {hh[-1]} "
              f"(with one-offs {_fmt(r.household_monthly_avg_with_one_offs_c)})")
    else:
        print("household: no month fully covered by every account that carries spending")
    print(f"{'category':<34}{'EUR/month':>11}{'months':>8}  accounts")
    for c in [c for c in r.categories if not c.net_refund][: a.top]:
        print(f"{c.category:<34}{_fmt(c.monthly_avg_c):>11}{c.n_months:>8}  {', '.join(c.accounts)}"
              + ("   (low confidence)" if c.low_confidence else ""))
    back = [c for c in r.categories if c.net_refund]
    if back:
        print("net refunds (more money back than spent in the window: not spending, not in the lines above):")
        for c in back:
            print(f"  {c.category:<32}{_fmt(-c.monthly_avg_c):>11} EUR/month back  over {c.n_months} months")
    if r.unavailable:
        print(f"{len(r.unavailable)} categories have no commonly covered month: " + ", ".join(u["category"] for u in r.unavailable[:6]))
    if r.excluded:
        print("excluded from averages (one_off / exclude_from_averages):")
        for e in r.excluded[:15]:
            print(f"  {e.date} {_fmt(e.amount_c):>11}  {e.category:<26}{e.event or ''}  {','.join(e.tags)}")
    if r.capital:
        print("capital items (shown apart, not in the averages):")
        for e in r.capital[:15]:
            print(f"  {e.date} {_fmt(e.amount_c):>11}  {e.category:<26}{e.event or ''}")
    _coverage_lines(r.coverage)


def cmd_cashflow(a, cfg):
    from coach.analytics.cashflow import cashflow
    con, ds = _dataset(a, cfg)
    r = cashflow(ds, _scope(a, ds), months=a.months, include_current=a.current)
    if a.json:
        print(_j(r))
        return

    def show(title, block):
        print(f"\n{title}  [{', '.join(block.accounts)}]")
        print(f"{'month':<9}{'income':>11}{'spending':>11}{'saved':>10}{'net':>11}{'sav.rate':>9}  complete")
        for m in block.months:
            print(f"{m.month:<9}{_fmt(m.income_c):>11}{_fmt(m.spending_c):>11}{_fmt(m.saved_c):>10}{_fmt(m.net_c):>11}"
                  f"{_pc(m.savings_rate):>9}  {'yes' if m.complete else 'NO: ' + ', '.join(m.missing_accounts)}")
        t = block.totals_complete
        if t:
            print(f"{'complete':<9}{_fmt(t.income_c):>11}{_fmt(t.spending_c):>11}{_fmt(t.saved_c):>10}{_fmt(t.net_c):>11}"
                  f"{_pc(t.savings_rate):>9}  ({t.n_months} months; refunds netted {_fmt(t.refunds_c)}, one-offs inside {_fmt(t.one_off_spending_c)})")
            if t.debt_service_c:
                print(f"         debt service {_fmt(t.debt_service_c)}" + (f", of which principal ~{_fmt(t.loan_principal_c)} -> savings rate "
                      f"incl. principal {_pc(t.savings_rate_incl_principal)}" if t.loan_principal_c is not None
                      else " (principal not estimable: rate / outstanding capital missing in the loans)"))
            if t.drawn_unconnected_c or t.sent_unconnected_c:
                print(f"         internal transfers with an unconnected account: drawn {_fmt(t.drawn_unconnected_c)}, sent {_fmt(t.sent_unconnected_c)}")
    show("household", r.household)
    if a.by in ("purpose", "all"):
        for k, b in r.by_purpose.items():
            show(f"purpose={k}", b)
    if a.by in ("owner", "all"):
        for k, b in r.by_owner.items():
            show(f"owner={k}", b)
    print("\nspending excludes transfer.* ; refunds are negative spending ; 'saved' = savings/investment tagged transfers "
          "(not spending) ; net = income - spending")
    _coverage_lines(r.coverage)


# ---------------------------------------------------------------- recurring / price changes

def cmd_recurring(a, cfg):
    from coach.analytics import pricechanges, recurring
    con, ds = _dataset(a, cfg)
    scope = _scope(a, ds)
    res = recurring.detect_recurring(ds, scope)
    what = a.what
    if what == "refresh" or getattr(a, "refresh", False):
        _no_asof_write(a)
        if not scope.is_all:
            sys.exit("error: the stored series always cover every account: drop --owner/--purpose/--account to refresh")
        s = recurring.refresh_recurring(con, ds)
        if what == "refresh":
            print(f"recurring series: {s.series} (created {s.created}, updated {s.updated}, removed {s.removed}, "
                  f"unchanged {s.unchanged})")
            return
    if what == "changes":
        since = dt.date.fromisoformat(a.since) if a.since else None
        dismissed = frozenset(r[0] for r in con.execute("SELECT id FROM price_change_dismissals"))
        pc = pricechanges.price_changes(ds, scope, res, since=since, only_confirmed=a.confirmed, dismissed=dismissed,
                                        include_dismissed=a.all)
        if a.json:
            print(_j(pc))
            return
        print(f"price changes of recurring payments: {pc.counts['increase']} increase(s), {pc.counts['decrease']} decrease(s)"
              f" ({pc.counts['unconfirmed']} not yet confirmed by a second payment)")
        for c in pc.changes:
            print(f"  {c.id} {c.date} {c.entity[:30]:<30} {_fmt(c.old_c):>9} -> {_fmt(c.new_c):>9} {c.pct * 100:+6.1f}%  "
                  f"{c.effect:<10} ~{_fmt(c.yearly_impact_c)}/yr {'' if c.confirmed else '(unconfirmed)'}"
                  f"{' (variable)' if c.variable else ''}  [{c.account_label}]")
        return
    if what == "dismiss-change":
        _no_asof_write(a)
        if not a.id:
            sys.exit("error: give the price change id (see `coach recurring changes`)")
        ids = {c.id for c in pricechanges.price_changes(ds, scope, res).changes}
        if a.id not in ids:
            sys.exit(f"error: no current price change {a.id!r}")
        con.execute("INSERT OR REPLACE INTO price_change_dismissals(id, dismissed_at, note) VALUES (?,?,?)",
                    (a.id, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), a.note))
        con.commit()
        print(f"dismissed {a.id}")
        return
    if what == "missing":
        miss = recurring.missing_contracts(res)
        if a.json:
            print(_j([x.to_dict() for x in miss]))
            return
        print(f"{len(miss)} recurring payment(s) look like a contract or subscription but match no memory contract / liability:")
        for x in miss:
            print(f"  {x.entity[:34]:<34} {x.category:<28} {_fmt(abs(x.expected_amount_c)):>9}/{x.cadence:<9} "
                  f"{_fmt(x.yearly_cost_c):>9}/yr  [{x.account_label}]")
        return
    series = res.series if a.all else [x for x in res.series if x.status == "active"]
    if a.json:
        d = res.to_dict()
        d["series"] = [x.to_dict() for x in series]
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(f"recurring series as of {ds.today}: {res.counts['active']} active, {res.counts['ended']} ended; "
          f"active by cadence: {res.by_cadence}")
    print(f"{'id':<15}{'st':<4}{'kind':<9}{'cadence':<10}{'entity':<30}{'amount':>10}{'per year':>10}{'n':>4}  next         links")
    for x in series:
        links = ",".join(f"{l.kind[0]}:{l.id}" for l in x.links) or ("(no contract)" if x.missing_contract else "")
        print(f"{x.id:<15}{x.status[:3]:<4}{x.kind:<9}{x.cadence:<10}{x.entity[:29]:<30}{_fmt(x.expected_amount_c):>10}"
              f"{_fmt(x.yearly_cost_c):>10}{x.n_occurrences:>4}  {str(x.next_expected or '-'):<12} {links}"
              + ("  (low confidence)" if x.confidence_label == "low" else ""))
    _coverage_lines(res.coverage)


# ---------------------------------------------------------------- anomalies

def _no_asof_write(a) -> None:
    if getattr(a, "as_of", None):
        sys.exit("error: an --as-of run is read-only (it would store results computed for another date)")


def cmd_anomalies(a, cfg):
    from coach.analytics import anomalies as an
    con, ds = _dataset(a, cfg)
    what = a.what
    if what in ("dismiss", "undismiss"):
        _no_asof_write(a)
        if not a.id:
            sys.exit("error: give the anomaly id (see `coach anomalies`)")
        ok = (an.dismiss_anomaly(con, a.id, a.note, ds=ds) if what == "dismiss" else an.undismiss_anomaly(con, a.id))
        if not ok:
            sys.exit(f"error: no anomaly {a.id!r} (see `coach anomalies`)")
        print(f"{what}ed {a.id}")
        return
    stored_ids = {r[0] for r in con.execute("SELECT id FROM anomalies WHERE dismissed_at IS NOT NULL")}
    res = an.detect_anomalies(ds, _scope(a, ds), dismissed=frozenset(stored_ids), include_dismissed=a.all)
    if what == "refresh":
        _no_asof_write(a)
        s = an.refresh_anomalies(con, ds)
        print(f"anomalies: {s.found} found, {s.new} new, {s.open} open, {s.dismissed} dismissed")
        return
    if a.json:
        print(_j(res))
        return
    print(f"anomalies as of {ds.today}: {len(res.anomalies)} shown ({res.dismissed} dismissed"
          f"{'' if a.all else ', hidden: --all shows them'}); " + ", ".join(f"{k}={v}" for k, v in sorted(res.counts.items())
                                                                           if not k.startswith("severity_")))
    for x in res.anomalies:
        print(f"  [{x.severity:<6}] {x.id}  {x.type:<18} {x.message}" + ("  (dismissed)" if x.dismissed else ""))
    if res.anomalies:
        print("dismiss with `coach anomalies dismiss ID [--note TEXT]`")


# ---------------------------------------------------------------- forecast

def cmd_forecast(a, cfg):
    from coach.analytics.forecast import forecast
    con, ds = _dataset(a, cfg)
    scope = _scope(a, ds)
    r = forecast(ds, a.days, scope, points=a.json or a.points)
    if a.json:
        print(_j(r))
        return

    def line(f):
        ms = {m.days: m for m in f.milestones}
        cells = "".join(f"{_fmt(ms[n].balance_c):>12}{'(' + _fmt(ms[n].low_c) + ')':>13}" if n in ms else f"{'-':>12}{'':>13}"
                        for n in (30, 60, 90))
        mn = f"{_fmt(f.min_balance_c)} on {f.min_date}" if f.min_balance_c is not None else "-"
        print(f"{f.label[:24]:<25}{_fmt(f.start_balance_c):>11}{cells}  min {mn}  {','.join(f.flags)}")

    print(f"cash-flow forecast from {ds.today} (expected balance and, in brackets, the low end of the ~80% band)")
    print(f"{'account':<25}{'start':>11}{'30 days':>12}{'':>13}{'60 days':>12}{'':>13}{'90 days':>12}{'':>13}")
    line(r.household)
    for f in r.accounts:
        if f.start_balance_c is not None and (f.start_balance_c or f.events or f.variable_monthly_c):
            line(f)
    if r.household.first_negative:
        print(f"household balance projected negative from {r.household.first_negative}")
    for f in r.accounts:
        if f.first_negative:
            print(f"  {f.label}: projected negative from {f.first_negative}")
    for t in r.assumptions:
        print(f"  assumption: {t}")
    if a.events:
        print("expected events:")
        for e in r.household.events:
            print(f"  {e.date} {_fmt(e.amount_c):>10}  {e.account_label[:20]:<20} {e.label[:30]} ({e.source}{', overdue' if e.overdue else ''})")


# ---------------------------------------------------------------- budgets

def _store(cfg, a):
    from coach.memory.commands import _store as s
    return s(cfg, a=a)


def cmd_budget(a, cfg):
    from coach.analytics import budgets
    what = a.what
    if what == "suggest":
        con, ds = _dataset(a, cfg)
        r = budgets.suggest_budgets(ds, a.months, _scope(a, ds), a.top)
        if a.json:
            print(_j(r))
            return
        print(f"suggested monthly budgets: median of the last {a.months or ds.settings.budget_suggest_months} covered months, rounded")
        print(f"{'category':<34}{'suggest':>9}{'median':>9}{'mean':>9}{'months':>7}  current")
        for s in r.suggestions:
            print(f"{s.category:<34}{_fmt(s.suggested_c):>9}{_fmt(s.median_c):>9}{_fmt(s.mean_c):>9}{s.n_months:>7}  "
                  f"{_fmt(s.existing_c) if s.existing_c else ''}{'  (low confidence)' if s.low_confidence else ''}")
        print("apply one with `coach budget set CATEGORY AMOUNT` (previewed before it is written)")
        return
    if what == "list":
        store = _store(cfg, a)
        items, problems = store.budgets_checked()
        for w in problems:
            print(f"warning: budget {w} -- left out; run `coach memory check`", file=sys.stderr)
        if a.json:
            rows = []
            for b in items:
                d_ = b.model_dump(mode="json", exclude_none=True)
                d_["monthly"] = money_str(parse_money(b.monthly))
                rows.append(d_)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return
        if not items and not problems:
            print("no budgets yet (memory/budgets.yaml): `coach budget suggest`, then `coach budget set`")
        for b in items:
            print(f"{b.id:<28}{b.category or 'group:' + b.group:<30}{b.monthly:>9.2f}/month"
                  + (" rollover" if b.rollover else "") + (f" owner={b.owner}" if b.owner else "")
                  + (f" account={b.account}" if b.account else "") + (f" from {b.start}" if b.start else ""))
        return
    if what == "status":
        con, ds = _dataset(a, cfg)
        r = budgets.budget_status(ds)
        if a.json:
            print(_j(r))
            return
        for w in r.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"budget status {r.month} (as of {r.as_of}): over={r.counts['over']} at_risk={r.counts['at_risk']} ok={r.counts['ok']}")
        print(f"{'budget':<28}{'available':>10}{'spent':>10}{'left':>10}{'used':>7}{'projected':>11}  status")
        for p in r.budgets:
            print(f"{p.id[:27]:<28}{_fmt(p.available_c):>10}{_fmt(p.spent_c):>10}{_fmt(p.remaining_c):>10}"
                  f"{_pc(p.percent_used):>7}{_fmt(p.projected_c):>11}  {p.status}"
                  + (f"  (+{_fmt(p.carry_c)} carried)" if p.carry_c else "") + (f"  [{', '.join(p.flags)}]" if p.flags else ""))
        if r.unbudgeted:
            print("biggest unbudgeted spending this month: " + ", ".join(f"{u['category']} {u['spent']}" for u in r.unbudgeted[:5]))
        return
    if what == "set":
        _budget_set(a, cfg)


def _check_owner_account(a, cfg, goal: bool = False) -> None:
    """Refuse an owner or account that does not exist (checked when writing, not months later when a status is empty)."""
    from coach.db import connect
    acct = getattr(a, "account", None)
    owner = getattr(a, "owner", None)
    if not (acct or owner):
        return
    con = connect(cfg, insecure=a.insecure)
    rows = con.execute("SELECT uid, label, name, owner FROM accounts").fetchall()
    if acct and not any(acct in (r[0], r[1], r[2]) for r in rows):
        sys.exit(f"error: unknown account {acct!r} (see `coach coverage`)")
    if owner and owner != "joint" and not any(owner == r[3] for r in rows) \
            and owner not in {m.id for m in _store(cfg, a).members()}:
        sys.exit(f"error: unknown owner {owner!r}: 'joint', an account owner or a household member id")


def _confirm(a, what: str) -> bool:
    from coach.memory.commands import _is_tty
    if not _is_tty():                       # --yes only skips the typed prompt for a human at a terminal (stdin and stdout)
        print(f"nothing written: not an interactive terminal; run this yourself in a terminal to {what}, or use --propose to queue it")
        return False
    if a.yes:
        return True
    return input(f"{what}? [y/N] ").strip().lower() in ("y", "yes")


def _budget_set(a, cfg):
    from coach.analytics import budgets
    from coach.classify.rules import CATEGORIES, TAXONOMY
    from coach.memory import proposals as prop_mod
    from coach.memory.store import MemoryStoreError, ValidationFailed
    store = _store(cfg, a)
    target = a.target
    if target.startswith("group:") or (target in TAXONOMY and "." not in target):
        group = target.removeprefix("group:")
        if group not in TAXONOMY:
            sys.exit(f"error: unknown category group {group!r}")
        key = {"group": group}
    else:
        if target not in CATEGORIES:
            sys.exit(f"error: unknown category {target!r} (see `coach taxonomy list`)")
        key = {"category": target}
    try:
        amount = float(money_str(parse_money(a.amount)))
    except Exception:
        sys.exit(f"error: bad amount {a.amount!r}")
    if amount <= 0:
        sys.exit("error: the monthly amount must be positive")
    items = store.budgets()
    same = next((b for b in items if (b.category == key.get("category") and b.group == key.get("group"))
                 and b.owner == a.owner and b.account == a.account), None)
    ids = {b.id for b in items}
    bid = a.id or (same.id if same else budgets.budget_id_for(target, ids))
    clash = next((b for b in items if b.id == bid), None)
    if clash is not None and a.id and (clash.category, clash.group) != (key.get("category"), key.get("group")):
        if not a.replace:
            sys.exit(f"error: budget {bid!r} already exists for {clash.category or 'group:' + clash.group}; "
                     f"use another --id, or --replace to retarget it to {target}")
    _check_owner_account(a, cfg)
    amount = int(amount) if float(amount).is_integer() else amount
    fields: dict = {**key, "monthly": amount}
    replacing = clash is not None and a.replace and (clash.category, clash.group) != (key.get("category"), key.get("group"))
    if bid in ids and not replacing:
        fields = {"monthly": amount}                                       # an update touches only what is given
    if a.rollover is not None:
        fields["rollover"] = a.rollover
    if a.owner:
        fields["owner"] = a.owner
    if a.account:
        fields["account"] = a.account
    if a.start:
        fields["start"] = dt.date.fromisoformat(a.start)
    elif a.rollover and not (same and same.start) and not any(b.id == bid and b.start for b in items):
        fields["start"] = dt.date.today().replace(day=1)               # a rollover starts counting this month
    if a.note:
        fields["note"] = a.note
    ops = budgets.budget_ops(ids, store.exists("budgets.yaml"), bid, fields)
    if replacing:
        ops = [{"op": "remove", "path": f"budgets[{bid}]"}] + budgets.budget_ops(ids - {bid}, True, bid, fields)
    try:
        preview = store.edit("budgets.yaml", ops, action="set-budget", reason=a.reason, dry_run=True)
    except (MemoryStoreError, ValidationFailed) as e:
        sys.exit(f"error: {e}")
    if not preview.changed:
        print("no change (the budget is already like that)")
        return
    print(preview.diff.rstrip("\n"))
    if a.dry_run:
        print("(dry run: nothing written, nothing queued)")
        return
    if a.propose:
        try:
            p = prop_mod.create(store, "budgets.yaml", ops, a.reason or f"budget {bid}: {amount:.2f} EUR/month",
                                a.source or "coach-llm")
        except MemoryStoreError as e:
            sys.exit(f"error: {e}")
        print(f"\nnothing was written: queued as proposal {p.id}. The user applies it with `coach memory accept {p.id}`.")
        return
    if not _confirm(a, f"write budget {bid} ({amount:.2f} EUR/month)"):
        return
    res = store.edit("budgets.yaml", ops, action="set-budget", reason=a.reason, detail=bid)
    print("\nwritten" + (f" as change {res.change_id}" if res.change_id else " (history is off)"))


# ---------------------------------------------------------------- calendar

def cmd_calendar(a, cfg):
    from coach.analytics import upcoming
    con, ds = _dataset(a, cfg)
    r = upcoming.calendar_items(ds, a.days, include_transfers=a.transfers)
    if a.ics:
        Path(a.ics).write_text(upcoming.to_ics(r), newline="")
        print(f"wrote {len(r.items)} event(s) to {a.ics}")
    if a.json:
        print(_j(r))
        return
    if a.ics:
        return
    print(f"next {r.days} days from {r.as_of}: " + ", ".join(f"{k}={v}" for k, v in r.counts.items() if v))
    for i in r.items:
        print(f"  {i.date} (+{i.days_until:>3}d) {i.source:<10}{i.kind:<16}{_fmt(i.amount_c):>10}  {i.title[:50]}"
              + (f"  - {i.note}" if i.note else ""))


# ---------------------------------------------------------------- goals

def cmd_goals(a, cfg):
    from coach.analytics import goals
    what = a.what
    if what in ("list", "status"):
        con, ds = _dataset(a, cfg)
        r = goals.goal_progress(ds)
        if a.json:
            print(_j(r))
            return
        for w in r.warnings:
            print(f"warning: {w}", file=sys.stderr)
        if not r.goals and not r.warnings:
            print("no goals yet (memory/goals.yaml): `coach goals set ID --target AMOUNT --tag savings`")
        for g in r.goals:
            print(f"{g.id:<24}{g.status:<10}{_fmt(g.current_c):>11} / {_fmt(g.target_c):<11}{_pc(g.percent):>7}"
                  f"  pace {_fmt(g.pace_c)}/mo ({g.pace_basis})  projected {g.projected_date or '-'}"
                  f"  target {g.target_date or '-'}" + (f"  need {_fmt(g.required_monthly_c)}/mo" if g.required_monthly_c else "")
                  + (f"  [{', '.join(g.flags)}]" if g.flags else ""))
        return
    if what == "set":
        _goal_set(a, cfg)


def _goal_set(a, cfg):
    from coach.analytics import goals
    from coach.memory import proposals as prop_mod
    from coach.memory.store import MemoryStoreError, ValidationFailed
    store = _store(cfg, a)
    ids = {g.id for g in store.goals()}
    fields: dict = {}
    def amount_of(text, what):
        try:
            v = float(money_str(parse_money(text)))
        except Exception:
            sys.exit(f"error: bad {what} {text!r}: give an amount in EUR such as 3000 or 49.90")
        if v != v or v in (float("inf"), float("-inf")) or v < 0:
            sys.exit(f"error: bad {what} {text!r}")
        return int(v) if v.is_integer() else v
    if a.target is not None:
        fields["target_amount"] = amount_of(a.target, "target")
    elif a.id not in ids:
        sys.exit("error: a new goal needs --target")
    for flag, key in (("asset", "asset"), ("account", "account"), ("tag", "tag")):
        if getattr(a, flag):
            fields[key] = getattr(a, flag)
    if a.date:
        fields["target_date"] = dt.date.fromisoformat(a.date)
    if a.monthly is not None:
        fields["monthly_contribution"] = amount_of(a.monthly, "monthly contribution")
    if a.baseline is not None:
        fields["baseline"] = amount_of(a.baseline, "baseline")
    if a.start:
        fields["start"] = dt.date.fromisoformat(a.start)
    elif a.tag and a.id not in ids:
        fields["start"] = dt.date.today()                              # a tag goal counts what is saved FROM NOW ON
    if a.title:
        fields["title"] = a.title
    _check_owner_account(a, cfg, goal=True)
    ops = goals.goal_ops(ids, store.exists("goals.yaml"), a.id, fields)
    try:
        preview = store.edit("goals.yaml", ops, action="set-goal", reason=a.reason, dry_run=True)
    except (MemoryStoreError, ValidationFailed) as e:
        sys.exit(f"error: {e}")
    if not preview.changed:
        print("no change")
        return
    print(preview.diff.rstrip("\n"))
    if a.dry_run:
        print("(dry run: nothing written, nothing queued)")
        return
    if a.propose:
        try:
            p = prop_mod.create(store, "goals.yaml", ops, a.reason or f"goal {a.id}", a.source or "coach-llm")
        except MemoryStoreError as e:
            sys.exit(f"error: {e}")
        print(f"\nnothing was written: queued as proposal {p.id}. The user applies it with `coach memory accept {p.id}`.")
        return
    if not _confirm(a, f"write goal {a.id}"):
        return
    res = store.edit("goals.yaml", ops, action="set-goal", reason=a.reason, detail=a.id)
    print("\nwritten" + (f" as change {res.change_id}" if res.change_id else " (history is off)"))


# ---------------------------------------------------------------- year in review

def review_markdown(r) -> str:
    L = [f"# Year in review {r.year}" + (" (partial)" if r.partial_year else ""), ""]
    t = r.flow.totals_all
    if t:
        L += [f"- Income: {_fmt(t.income_c)} EUR", f"- Spending (one-offs included): {_fmt(t.spending_c)} EUR "
              f"(one-offs {_fmt(t.one_off_spending_c)}, without them {_fmt(t.spending_ex_one_offs_c)})",
              f"- Saved (savings/investment transfers): {_fmt(t.saved_c)} EUR", f"- Net: {_fmt(t.net_c)} EUR, savings rate {_pc(t.savings_rate)}",
              f"- Months listed: {r.months_listed}, complete: {r.months_complete}", ""]
    if r.by_event:
        L += ["## By event", ""] + [f"- {e.event}: {_fmt(e.spending_c)} EUR spent ({e.n_tx} transactions)" for e in r.by_event] + [""]
    L += ["## Top merchants", ""] + [f"- {e.entity}: {_fmt(e.spending_c)} EUR ({e.n_tx})" + (f", of which one-off {_fmt(e.one_off_c)}" if e.one_off_c else "")
                                   for e in r.top_entities] + [""]
    L += [f"## Biggest category changes vs {r.prev_year} (monthly average)", ""]
    for c in r.increases:
        L.append(f"- up: {c.category} {_fmt(c.avg_prev_c)} -> {_fmt(c.avg_year_c)} ({_fmt(c.delta_c)}/month"
                 + (f", {c.pct * 100:+.0f}%" if c.pct is not None else "") + (", partial" if c.partial else "") + ")")
    for c in r.decreases:
        L.append(f"- down: {c.category} {_fmt(c.avg_prev_c)} -> {_fmt(c.avg_year_c)} ({_fmt(c.delta_c)}/month"
                 + (f", {c.pct * 100:+.0f}%" if c.pct is not None else "") + (", partial" if c.partial else "") + ")")
    if r.coverage.notes:
        L += ["", "Notes:"] + [f"- {n}" for n in r.coverage.notes]
    return "\n".join(L) + "\n"


def cmd_review(a, cfg):
    from coach.analytics.review import year_review
    con, ds = _dataset(a, cfg)
    r = year_review(ds, int(a.year) if a.year else None, _scope(a, ds))
    if a.json:
        print(_j(r))
        return
    print(review_markdown(r), end="")


# ---------------------------------------------------------------- refresh (the scheduled step)

def cmd_analytics_refresh(a, cfg):
    from coach.db import connect
    _no_asof_write(a)
    con = connect(cfg, insecure=a.insecure)
    today = dt.date.fromisoformat(a.as_of) if getattr(a, "as_of", None) else None
    out = api.refresh_all(con, cfg, today)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    r, an, b = out["recurring"], out["anomalies"], out["budgets"]
    print(f"recurring: {r['series']} series (created {r['created']}, updated {r['updated']}, removed {r['removed']}); "
          f"anomalies: {an['open']} open, {an['new']} new; budgets: {b['set']} set"
          + (f", over={b.get('over', 0)} at_risk={b.get('at_risk', 0)}" if b["set"] else ""))
