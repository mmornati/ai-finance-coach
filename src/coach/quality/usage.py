"""LLM usage (E12-4): what each model call cost, where it went and how it adds up.

The data is the ``llm_usage`` table (one row per call: backend, model, purpose, tokens, cost, duration), written by every path that calls a
model (classify run / compare / enrich, the coach, document extraction, the evaluations). The cost of a ``claude-code`` call is NOTIONAL:
it is what the same tokens would cost at API prices, not something the subscription bills; an ``anthropic-api`` cost is an estimate from
the price table (``coach.classify.backends.PRICES``); ``ollama`` is local and free. A call whose model has no known price has no cost
(NULL, shown as unknown, never as 0).

The destinations come from the egress journal (``coach privacy report``): which host the calls went to and how many bytes (never a payload).
A budget threshold ``[usage] monthly_warn_usd`` raises the ``llm_usage_high`` alert in the LOCAL feed only (it is never sent to a channel).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

JOBS = {"label": "classify run", "compare": "classify compare", "enrich": "classify enrich", "doc_extract": "memory doc extract",
        "eval": "eval models"}


def job_of(purpose: Optional[str]) -> str:
    p = purpose or "?"
    if p in JOBS:
        return JOBS[p]
    if p == "eval:coach" or p.startswith("eval"):
        return "eval coach" if "coach" in p else "eval models"
    if p.startswith("coach:"):
        sid = p.split(":", 1)[1]
        return "coach ask" if sid == "ask" else "coach digest" if sid.startswith("digest") else f"coach skill {sid}"
    return p


def _since(now: dt.datetime, days: int) -> str:
    return (now - dt.timedelta(days=days)).isoformat(timespec="seconds")


def _now(now: Optional[dt.datetime]) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)


def month_status(con, cfg, now: Optional[dt.datetime] = None) -> dict:
    """The month so far against [usage] monthly_warn_usd. level: None (under, or no threshold), 'medium' (>= 100 %), 'high' (>= 200 %)."""
    now = _now(now)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    include = bool(getattr(cfg, "usage_include_notional", True))
    cost = 0.0
    unknown = 0
    for backend, c in con.execute("SELECT backend, cost_usd FROM llm_usage WHERE ts >= ?", (first,)):
        if c is None:
            unknown += 1
        elif include or backend != "claude-code":
            cost += c
    thr = float(getattr(cfg, "usage_monthly_warn_usd", 0) or 0)
    ratio = round(cost / thr, 3) if thr > 0 else None
    level = None if ratio is None or ratio < 1 else "high" if ratio >= 2 else "medium"
    return {"month": f"{now:%Y-%m}", "cost_usd": round(cost, 4), "threshold_usd": thr or None, "ratio": ratio, "level": level,
            "unknown_cost_calls": unknown, "include_notional": include}


def summary(con, cfg, days: int = 30, now: Optional[dt.datetime] = None) -> dict:
    now = _now(now)
    since = _since(now, days)
    rows = con.execute("""SELECT purpose, backend, model, COUNT(*), COALESCE(SUM(items),0), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),
                                 COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_write_tokens),0), SUM(cost_usd),
                                 SUM(cost_usd IS NULL), COALESCE(SUM(duration_s),0), MAX(ts)
                          FROM llm_usage WHERE ts >= ? GROUP BY purpose, backend, model ORDER BY SUM(COALESCE(cost_usd,0)) DESC, 4 DESC""",
                       (since,)).fetchall()
    lines = []
    for p, b, m, n, items, tin, tout, cr, cw, cost, unk, dur, last in rows:
        lines.append({"job": job_of(p), "purpose": p, "backend": b, "model": m, "calls": n, "items": items, "tokens_in": tin,
                      "tokens_out": tout, "cache_read_tokens": cr, "cache_write_tokens": cw, "cost_usd": None if cost is None else round(cost, 4),
                      "cost_unknown_calls": int(unk or 0), "notional": b == "claude-code", "duration_s": round(dur, 1),
                      "avg_duration_s": round(dur / n, 2) if n else 0, "last": last})
    by_job: dict[str, dict] = {}
    for ln in lines:
        j = by_job.setdefault(ln["job"], {"job": ln["job"], "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "duration_s": 0.0})
        j["calls"] += ln["calls"]
        j["tokens_in"] += ln["tokens_in"]
        j["tokens_out"] += ln["tokens_out"]
        j["cost_usd"] = round(j["cost_usd"] + (ln["cost_usd"] or 0), 4)
        j["duration_s"] = round(j["duration_s"] + ln["duration_s"], 1)
    days_rows = con.execute("""SELECT SUBSTR(ts,1,10), COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost_usd),0)
                               FROM llm_usage WHERE ts >= ? GROUP BY 1 ORDER BY 1""", (since,)).fetchall()
    by_day = {d: {"date": d, "calls": n, "tokens_in": a, "tokens_out": b, "cost_usd": round(c, 4)} for d, n, a, b, c in days_rows}
    first = (now - dt.timedelta(days=days - 1)).date()
    series = [by_day.get((first + dt.timedelta(days=i)).isoformat(), {"date": (first + dt.timedelta(days=i)).isoformat(), "calls": 0,
                                                                       "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
              for i in range(days)]
    total_cost = round(sum(ln["cost_usd"] or 0 for ln in lines), 4)
    notional = round(sum(ln["cost_usd"] or 0 for ln in lines if ln["notional"]), 4)
    from coach import egress
    dest = egress.journal_summary(con, days)
    destinations = [{"kind": r["kind"], "host": r["host"], "purpose": r["purpose"], "calls": r["count"], "bytes": r["bytes"],
                     "denied": r["denied"], "web": r["web"], "last": r["last"]}
                    for r in dest["rows"] if r["kind"].startswith("llm.") or r["purpose"].startswith(("eval", "coach"))]
    return {"days": days, "since": since, "lines": lines, "by_job": sorted(by_job.values(), key=lambda j: -j["cost_usd"]),
            "by_day": series, "destinations": destinations, "journal_available": dest["available"],
            "totals": {"calls": sum(ln["calls"] for ln in lines), "tokens_in": sum(ln["tokens_in"] for ln in lines),
                       "tokens_out": sum(ln["tokens_out"] for ln in lines), "cache_read_tokens": sum(ln["cache_read_tokens"] for ln in lines),
                       "cache_write_tokens": sum(ln["cache_write_tokens"] for ln in lines), "cost_usd": total_cost, "notional_cost_usd": notional,
                       "estimated_cost_usd": round(total_cost - notional, 4),
                       "unknown_cost_calls": sum(ln["cost_unknown_calls"] for ln in lines),
                       "duration_s": round(sum(ln["duration_s"] for ln in lines), 1)},
            "month": month_status(con, cfg, now),
            "note": "claude-code costs are NOTIONAL (API prices for the tokens a subscription call used: nothing is billed for them); "
                    "anthropic-api costs are estimates from the price table; ollama is local and free; a model without a known price has "
                    "no cost (unknown calls are counted apart)."}


def candidate(con, cfg, now: Optional[dt.datetime] = None):
    """The `llm_usage_high` alert candidate of the current month, or None. One event per month; it escalates at twice the threshold."""
    ms = month_status(con, cfg, now)
    if not ms["level"]:
        return None
    from coach.alerts.signals import Candidate
    from coach.i18n_msg import server_msg
    thr = ms["threshold_usd"]
    title = f"LLM usage this month: {ms['cost_usd']:.2f} USD, over the {thr:.2f} USD threshold"
    body = ("Open the Usage page or run `coach usage` to see which job costs the most. Subscription calls count at their notional "
            "API price" + ("" if ms["include_notional"] else " (not counted: [usage] include_notional = false)")
            + ". This alert is shown here only; it is never sent to a channel.")
    # USD, not EUR: the costs are params as they are (two decimals), not *_amount
    return Candidate("llm_usage_high", f"llm-usage:{ms['month']}", ms["level"], title, body,
                     {"month": ms["month"], "cost_usd": ms["cost_usd"], "threshold_usd": thr, "ratio": ms["ratio"]},
                     title_msg=server_msg("alert.llmUsage.title", title, cost_usd=f"{ms['cost_usd']:.2f}", threshold_usd=f"{thr:.2f}"),
                     body_msg=(server_msg("alert.llmUsage.body", body) if ms["include_notional"]
                               else server_msg("alert.llmUsage.bodyNotionalOff", body)))


def format_report(d: dict) -> str:
    t, m = d["totals"], d["month"]
    L = [f"LLM usage, last {d['days']} days: {t['calls']} call(s), {t['tokens_in']} tokens in / {t['tokens_out']} out (cache read {t['cache_read_tokens']}, written {t['cache_write_tokens']}), "
         f"{t['cost_usd']:.4f} USD ({t['notional_cost_usd']:.4f} notional on a subscription, {t['estimated_cost_usd']:.4f} estimated at API "
         f"prices), {t['duration_s']:.0f} s"
         + (f"; {t['unknown_cost_calls']} call(s) with an unknown price" if t["unknown_cost_calls"] else "")]
    if not d["lines"]:
        return L[0] + "\nno call recorded in this period"
    L.append(f"\n{'job':<30}{'backend':<14}{'model':<22}{'calls':>6}{'in':>9}{'out':>8}{'cache r/w':>13}{'cost USD':>10}{'avg s':>7}")
    for ln in d["lines"]:
        cost = "unknown" if ln["cost_usd"] is None else f"{ln['cost_usd']:.4f}"
        L.append(f"{ln['job']:<30}{ln['backend']:<14}{(ln['model'] or '?'):<22}{ln['calls']:>6}{ln['tokens_in']:>9}{ln['tokens_out']:>8}"
                 f"{str(ln['cache_read_tokens']) + '/' + str(ln['cache_write_tokens']):>13}{cost:>10}{ln['avg_duration_s']:>7}")
    L.append("\nper day (calls / cost USD)")
    L.append("  " + "  ".join(f"{x['date'][5:]}:{x['calls']}/{x['cost_usd']:.2f}" for x in d["by_day"] if x["calls"]))
    if d["destinations"]:
        L.append("\nwhere the calls went (egress journal: host, never a payload)")
        for x in d["destinations"]:
            L.append(f"  {x['kind']:<18}{x['host']:<34}{x['purpose']:<18}{x['calls']:>4} call(s) {x['bytes']:>9} bytes"
                     + (f"  {x['denied']} denied" if x["denied"] else ""))
    elif not d["journal_available"]:
        L.append("\n(the egress journal is not available: migrations pending)")
    if m["threshold_usd"]:
        L.append(f"\nthis month ({m['month']}): {m['cost_usd']:.2f} USD of the {m['threshold_usd']:.2f} USD threshold ({m['ratio']:.0%})"
                 + (f"  WARNING: threshold passed ({m['level']})" if m["level"] else ""))
    else:
        L.append(f"\nthis month ({m['month']}): {m['cost_usd']:.2f} USD; no threshold ([usage] monthly_warn_usd = 0)")
    L.append(d["note"])
    return "\n".join(L)
