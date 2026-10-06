"""The weekly digest (E10-3): a deterministic summary rendered LOCALLY as Markdown. Every number comes from code (the same datasets
and functions as the rest of the app); no model writes any of it.

Sections: last week's spending against the usual week, the top categories, the payments of the next 7 days, the open alerts, the
budgets, the savings tracker, and (when ``[coach] schedule_weekly`` produced one in the last 7 days) the coach's own commentary,
clearly labelled as written by the model.

* In the app: stored as an insight (``kind = digest``, skill ``weekly-local``), once per week (``due``).
* Outside the app (opt-in, ``weekly_digest_to_channels``): a TEASER, never the digest text. ``minimal``: "your weekly summary is ready,
  open the app". ``summary``: last week's spending and the usual one rounded, the count of open alerts. Same privacy guard as alerts.
* The link to the app is the localhost address of ``coach ui``. It works on the machine that runs the app, or from another device only
  through your own Tailscale / VPN setup (``[ui] allow_remote``, see docs/ui.md): nothing here exposes the app.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.alerts import channels as ch_mod, engine, messages as msg_mod, store
from coach.alerts.settings import DAYS, EXTERNAL, LOCAL_ONLY_KINDS, AlertSettings
from coach.analytics.averages import spending_txs
from coach.analytics.common import money_str

SKILL = "weekly-local"
USUAL_WEEKS = 8
MIN_USUAL_WEEKS = 3


def variable_txs(ds, rec) -> list:
    """Spending that is not a fixed commitment: the payments of recurring series (subscriptions, insurance, energy, loan instalments ...)
    are left out, except the discretionary groups (food, shopping, leisure ...), whose regular rhythm is still variable spending."""
    disc = tuple(g + "." for g in ds.settings.recurring_discretionary_groups) + tuple(ds.settings.recurring_discretionary_groups)
    fixed = {o.tx_key for x in rec.series if x.kind in ("expense", "saving", "transfer") and not x.category.startswith(disc)
             for o in x.occurrences}
    return [t for t in spending_txs(ds, None) if t.key not in fixed and not t.category.startswith("debt.")
            and t.category not in ("housing.mortgage", "housing.rental_property_loan")]


def _window_sum(txs, start: dt.date, end: dt.date) -> int:
    return -sum(t.amount_c for t in txs if start <= t.date <= end)


def build(con, cfg, ds, s: AlertSettings, *, today: Optional[dt.date] = None) -> dict:
    """The structured digest (``markdown`` included). Read-only."""
    from coach.analytics import budgets as budgets_mod, recurring, upcoming
    from coach.subs import decisions as DEC
    today = today or ds.today
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=6)
    rec = recurring.detect_recurring(ds)
    sp = variable_txs(ds, rec)
    first = min((t.date for t in ds.txs), default=None)
    spent = _window_sum(sp, start, end)
    notes: list[str] = []
    prior = []
    for k in range(1, USUAL_WEEKS + 1):
        e, b = start - dt.timedelta(days=7 * (k - 1) + 1), start - dt.timedelta(days=7 * k)
        if first is not None and b >= first:
            prior.append(_window_sum(sp, b, e))
    usual = round(sum(prior) / len(prior)) if len(prior) >= MIN_USUAL_WEEKS else None
    if usual is None:
        notes.append(f"Not enough history to say what a usual week is (needs {MIN_USUAL_WEEKS} earlier weeks of data).")
    last_tx = max((t.date for t in ds.txs), default=None)
    if last_tx is None or (today - last_tx).days > 3:
        notes.append(f"The latest transaction is from {last_tx or 'never'}: the figures may be incomplete until the next sync.")
    cats: dict[str, int] = {}
    for t in sp:
        if start <= t.date <= end:
            cats[t.category] = cats.get(t.category, 0) - t.amount_c
    top = [{"category": c, "spent": money_str(v)} for c, v in sorted(cats.items(), key=lambda kv: -kv[1])[:5] if v > 0]
    cal = upcoming.calendar_items(ds, days=7, recurring=rec)
    ups = [{"date": i.date.isoformat(), "title": i.title, "amount": money_str(i.amount_c) if i.amount_c is not None else None,
            "certainty": i.certainty} for i in cal.items if i.amount_c is not None and i.amount_c < 0 or i.kind == "deadline"][:12]
    events = [e for e in (store.open_events(con, today) if store.tables_present(con) else []) if e["kind"] not in LOCAL_ONLY_KINDS]
    alerts = {"open": len(events), "high": sum(1 for e in events if e["severity"] == "high"),
              "items": [{"id": e["id"], "kind": e["kind"], "severity": e["severity"], "title": e["title"]} for e in events[:8]]}
    bs = budgets_mod.budget_status(ds, recurring=rec)
    bud = {"set": len(ds.memory.budgets), "counts": bs.counts,
           "problems": [{"target": p.target, "status": p.status, "spent": money_str(p.spent_c), "available": money_str(p.available_c),
                         "projected": money_str(p.projected_c)} for p in bs.budgets if p.status in ("over", "at_risk")]}
    sav = DEC.savings(ds.decisions, rec, today)
    savings = {k: sav[k] for k in ("realised_monthly", "realised_yearly_run_rate", "realised_since_decisions", "verified", "pending",
                                   "contradicted")}
    out = {"as_of": today.isoformat(), "week_start": start.isoformat(), "week_end": end.isoformat(),
           "spent_c": spent, "usual_c": usual,
           "delta_pct": None if not usual else round((spent - usual) * 100 / usual, 1),
           "top_categories": top, "upcoming": ups, "alerts": alerts, "budgets": bud, "savings": savings, "notes": notes,
           "commentary": commentary(con, cfg, today)}
    out["markdown"] = render(out, s, cfg)
    return out


def commentary(con, cfg, today: dt.date) -> Optional[dict]:
    """The coach's weekly digest (E6-6) when ``[coach] schedule_weekly`` is on and one was written in the last 7 days."""
    if not cfg.coach_schedule_weekly:
        return None
    try:
        from coach.agent import insights as I, prompt as P
        last = I.last_digest(con, P.WEEKLY.id)
    except Exception:                                                           # noqa: BLE001
        return None
    if not last:
        return None
    created = dt.datetime.fromisoformat(last["created"]).date()
    if (today - created).days > 7:
        return None
    return {"insight_id": last["id"], "created": last["created"][:10], "body": last["body"]}


def _eur(c: Optional[int]) -> str:
    return "?" if c is None else f"{money_str(c)} EUR"


def app_link(cfg, s: AlertSettings) -> str:
    return s.app_url or f"http://{cfg.ui_host}:{cfg.ui_port}/alerts"


def render(d: dict, s: AlertSettings, cfg) -> str:
    L = [f"# Weekly summary, {d['week_start']} to {d['week_end']}", ""]
    L += ["*Every figure below is computed by the app from your bank data; nothing in this summary is written by a model "
          "(except the labelled coach commentary).*", ""]
    L += ["## Variable spending last week", ""]
    line = f"You spent **{_eur(d['spent_c'])}** of variable spending (recurring payments and loan instalments left out)"
    if d["usual_c"] is not None:
        pct = "" if d["delta_pct"] is None else f" ({'+' if d['delta_pct'] >= 0 else ''}{d['delta_pct']}%)"
        line += f" against a usual week of {_eur(d['usual_c'])}{pct} (mean of up to 8 earlier weeks, same definition)."
    else:
        line += "."
    L += [line, ""]
    if d["top_categories"]:
        L += ["Top categories:", ""] + [f"- {c['category']}: {c['spent']} EUR" for c in d["top_categories"]] + [""]
    L += ["## Next 7 days", ""]
    L += [f"- {u['date']}: {u['title']}" + (f", {u['amount']} EUR" if u["amount"] else "") + (" (assumed)" if u["certainty"] == "assumed" else "")
          for u in d["upcoming"]] or ["Nothing scheduled that the app knows of."]
    L += ["", "## Alerts", ""]
    a = d["alerts"]
    if a["open"]:
        L += [f"{a['open']} open ({a['high']} high):", ""] + [f"- [{i['severity']}] {i['title']}" for i in a["items"]]
    else:
        L += ["No open alert."]
    L += ["", "## Budgets", ""]
    b = d["budgets"]
    if not b["set"]:
        L += ["No budget set (`coach budget set`)."]
    elif not b["problems"]:
        L += [f"{b['set']} budget(s), none over or at risk."]
    else:
        L += [f"- {p['target']}: {'over' if p['status'] == 'over' else 'on track to overrun'} ({p['spent']} of {p['available']} EUR, "
              f"projected {p['projected']} EUR)" for p in b["problems"]]
    L += ["", "## Savings tracker", ""]
    sv = d["savings"]
    L += [f"Realised: {sv['realised_monthly']} EUR a month ({sv['realised_yearly_run_rate']} EUR a year), {sv['realised_since_decisions']} EUR "
          f"since the decisions; {sv['verified']} verified, {sv['pending']} pending, {sv['contradicted']} contradicted by the bank data."]
    if d["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in d["notes"]]
    if d["commentary"]:
        L += ["", f"## Coach commentary (AI-generated, written by the model on {d['commentary']['created']}, insight {d['commentary']['insight_id']})", "",
              d["commentary"]["body"].strip()]
    L += ["", "---", f"Open the app: {app_link(cfg, s)} (this link works on the machine that runs the app; from another device only "
          "through your own Tailscale / VPN set-up)."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- when, store, send

def due(con, s: AlertSettings, today: dt.date) -> tuple[bool, str]:
    """A digest is due when none was made since the most recent ``weekly_digest_day`` (so enabling it mid-week makes one at once)."""
    from coach.agent import insights as I
    period_start = today - dt.timedelta(days=(today.weekday() - DAYS.index(s.weekly_digest_day)) % 7)
    last = I.last_digest(con, SKILL)
    if last is not None and dt.datetime.fromisoformat(last["created"]).astimezone().date() >= period_start:
        return False, f"this week's digest already exists ({last['created'][:10]})"
    return True, ""


def store_digest(con, d: dict) -> str:
    from coach.agent import insights as I
    findings = {k: v for k, v in d.items() if k not in ("markdown", "commentary")}
    # E11-5: the summary is deterministic, but when it carries the coach's commentary a model wrote part of the text: label it AI-generated
    # and run the investment-advice check on it
    return I.add(con, kind="digest", title=f"Weekly summary {d['as_of']}", body=d["markdown"], findings=[findings], skill=SKILL,
                 backend="code", model=None, data_through=d["as_of"], ai_generated=bool(d.get("commentary")))


def teaser(d: dict, detail: str, guard, app_url: str = "") -> Optional[msg_mod.Message]:
    """The external message about a digest: never the digest text."""
    minimal = msg_mod.Message("Coach weekly summary", f"Coach: your weekly summary is ready. {msg_mod.open_the_app(app_url)}", "default",
                              ["bar_chart"], "minimal")
    if detail == "summary":
        sp, us = msg_mod.round_amount(d["spent_c"]), msg_mod.round_amount(d["usual_c"] or 0)
        text = f"Coach weekly summary: about {sp} EUR of variable spending last week" + (f" (usual week about {us} EUR)" if us else "")
        text += f", {d['alerts']['open']} open alert{'s' if d['alerts']['open'] != 1 else ''}. {msg_mod.open_the_app(app_url)}"
        if not msg_mod.violations(text, guard):
            return msg_mod.Message(minimal.title, text, "default", minimal.tags, "summary")
        minimal.note = "the summary was refused by the privacy filter: sent as minimal"
    if guard is not None and msg_mod.violations(minimal.body, guard):
        return None
    return minimal


def local_teaser(d: dict) -> msg_mod.Message:
    a = d["alerts"]
    delta = f", {d['delta_pct']:+}% vs usual" if d["delta_pct"] is not None else ""
    return msg_mod.Message("Coach: weekly summary ready", f"Spent {_eur(d['spent_c'])} last week{delta}. {a['open']} open alert(s).",
                           "default", [], "local")


def deliver(con, cfg, s: AlertSettings, d: dict, insight_id: str, *, now=None, dry_run: bool = False,
            transports: Optional[ch_mod.Transports] = None, tz=None) -> list[dict]:
    """Send the teaser to every enabled channel that has not had this digest. Same gates as alerts (quiet hours, weekly limit)."""
    now = engine._utc(now)
    t = transports or ch_mod.Transports()
    guard = None
    out = []
    for name in s.enabled_channels():
        r = {"channel": name, "status": "nothing", "reason": None, "message": None, "request": None}
        out.append(r)
        probs = ch_mod.problems(s, name, t, cfg)
        if probs:
            r.update(status="not_ready", reason="; ".join(probs))
            continue
        if store.digest_sent(con, name, insight_id):
            continue
        why = engine.gate(con, s, name, now, tz)
        if why:
            r.update(status="deferred", reason=why)
            continue
        if name in EXTERNAL and guard is None:
            guard = msg_mod.external_guard(cfg, con)
        m = local_teaser(d) if name == "macos" else teaser(d, s.external_detail, guard, s.app_url)
        if m is None:
            r.update(status="failed", reason="the message was refused by the privacy filter")
            continue
        r["message"] = {"title": m.title, "body": m.body, "detail": m.detail, "note": m.note}
        r["request"] = ch_mod.render(s, name, m, t)
        if dry_run:
            r["status"] = "would_send"
            continue
        try:
            ch_mod.send(s, name, m, t, cfg)
        except ch_mod.ChannelError as e:
            store.record_delivery(con, name, "digest", 0, False, str(e)[:120], ref=insight_id, now=now)
            r.update(status="failed", reason=str(e)[:120])
            continue
        store.record_delivery(con, name, "digest", 0, True, ref=insight_id, now=now)
        r["status"] = "sent"
    return out


def run_weekly(con, cfg, ds, s: AlertSettings, *, now=None, force: bool = False, transports=None, tz=None) -> dict:
    """The scheduled step: build + store the digest when due, then (opt-in) send the teaser. Returns {status, insight_id, deliveries}."""
    now = engine._utc(now)
    if not s.weekly_digest and not force:
        return {"status": "off"}
    ok, why = (True, "") if force else due(con, s, ds.today)
    if not ok:
        return {"status": "skipped", "reason": why}
    d = build(con, cfg, ds, s, today=ds.today)
    iid = store_digest(con, d)
    out = {"status": "done", "insight_id": iid, "deliveries": []}
    if s.weekly_digest_to_channels:
        out["deliveries"] = deliver(con, cfg, s, d, iid, now=now, transports=transports, tz=tz)
    return out
