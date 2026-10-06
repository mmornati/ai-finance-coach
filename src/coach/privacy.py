"""`coach privacy status | report` (E11-1, E11-4): what may leave this machine, now and in the last 30 days.

* ``status``  the effective privacy mode (standard | local_only | offline) and what every outbound path may do under it. The interactive
  skills that search the web (find-cheaper, mortgage-check) read this FIRST and refuse when ``web_search_skills`` is false.
* ``report``  the egress inventory (every outbound path: destination, data, redaction, opt-in, how to disable) and the summary of the local
  egress journal (calls per path / host / purpose, bytes, refusals) plus the compliance flags recorded by the AI-advice check.

Both are read-only and make no network call.
"""
from __future__ import annotations

import json
import sys

from coach import egress


def status_text(st: dict) -> str:
    mode = {"standard": "STANDARD: cloud LLMs and alert channels follow their own settings",
            "local_only": "LOCAL ONLY: no cloud LLM, no web search, no external alert channel (bank sync still allowed)",
            "offline": "OFFLINE: nothing leaves this machine (no bank sync: import files only; ollama on loopback is the only model)"}[st["mode"]]
    yes = lambda b: "allowed" if b else "REFUSED"                                  # noqa: E731
    lines = [f"privacy mode: {mode}", ""]
    lines.append(f"  bank sync (Enable Banking)        {yes(st['bank_sync'])}")
    lines.append(f"  LLM for classify                  [llm] backend = {st['llm_backends']['classify']}   "
                 f"{yes(st['paths']['llm.' + st['llm_backends']['classify']]['allowed'])}")
    lines.append(f"  LLM for the coach                 [coach] backend = {st['llm_backends']['coach']}   "
                 f"{yes(st['paths']['llm.' + st['llm_backends']['coach']]['allowed'])}")
    lines.append(f"  web search by `classify enrich`   {yes(st['web_search'])}"
                 + ("" if st["web_search"] else f"  ({st['web_search_reason']}: [privacy] web_enrich = {str(st['web_enrich']).lower()})"))
    lines.append(f"  web search by skills (find-cheaper, mortgage-check)   {yes(st['web_search_skills'])}")
    lines.append(f"  external alert channels           {yes(st['external_alert_channels'])}"
                 + (f"   enabled in config: {', '.join(st['alert_channels_enabled'])}" if st["alert_channels_enabled"] else ""))
    lines.append(f"  egress journal                    {'on' if st['journal'] else 'OFF'}")
    for m in st["llm_misconfigured"]:
        lines.append(f"\n  ! {m['purpose']} backend {m['backend']!r} is refused by this mode: set it to \"ollama\" ([llm] backend / [coach] backend)")
    return "\n".join(lines)


def cmd_status(a, cfg) -> None:
    st = egress.status(cfg)
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print(status_text(st))


def _kib(n: int) -> str:
    return f"{n / 1024:.1f} KiB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MiB"


def report_text(st: dict, inv: list[dict], js: dict, comp: dict) -> str:
    out = [status_text(st), "", "== egress inventory: every way data can leave this machine =="]
    for r in inv:
        out.append(f"\n[{r['kind']}] {r['title']}" + ("" if r["external"] else "   (this machine only)"))
        out.append(f"   destination : {r['destination']}")
        out.append(f"   data sent   : {r['data']}")
        out.append(f"   redaction   : {r['redaction']}")
        out.append(f"   opt-in      : {r['opt_in']}")
        out.append(f"   disable     : {r['disable']}")
        out.append(f"   local_only  : {r['local_only']}    offline: {r['offline']}")
    out.append(f"\n== egress journal, last {js['days']} days ==")
    if not js["available"]:
        out.append("   (no journal yet: run `coach db migrate`)")
    elif not js["rows"]:
        out.append("   nothing was sent")
    else:
        out.append(f"   {js['total']} call(s), {_kib(js['bytes'])} sent, {js['denied']} refused by the privacy settings")
        for r in js["rows"]:
            out.append(f"   {r['kind']:<18}{r['host'][:34]:<36}{r['purpose'][:22]:<24}{r['count']:>5} call(s) {_kib(r['bytes']):>10}"
                       + (f"  {r['denied']} refused" if r["denied"] else "") + ("  [web search]" if r["web"] else "") + f"  last {r['last'][:16]}")
    out.append("\n== AI-advice compliance flags, last 30 days ==")
    out.append(f"   {comp['flagged']} text(s) flagged ({', '.join(f'{k}: {v}' for k, v in comp['by_code'].items()) or 'none'})"
               if comp["available"] else "   (not available: run `coach db migrate`)")
    out.append("\nThe journal records host, size, purpose and redaction mode, never a payload, URL path or name.")
    return "\n".join(out)


def compliance_summary(con, days: int = 30) -> dict:
    import datetime as dt
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    try:
        rows = con.execute("SELECT codes FROM compliance_events WHERE ts >= ?", (since,)).fetchall()
    except Exception:                                                              # noqa: BLE001
        return {"available": False, "flagged": 0, "by_code": {}}
    by: dict[str, int] = {}
    for (c,) in rows:
        for k in json.loads(c or "[]"):
            by[k] = by.get(k, 0) + 1
    return {"available": True, "flagged": len(rows), "by_code": by}


def cmd_report(a, cfg) -> None:
    from coach.db import connect
    st = egress.status(cfg)
    inv = egress.inventory_rows()
    try:
        con = connect(cfg, insecure=a.insecure, migrate=False)
    except Exception as e:                                                         # noqa: BLE001  (no database yet / no key)
        print(f"note: the egress journal could not be read ({type(e).__name__})", file=sys.stderr)
        js, comp = {"days": a.days, "rows": [], "total": 0, "bytes": 0, "denied": 0, "available": False}, {"available": False, "flagged": 0, "by_code": {}}
    else:
        try:
            egress.flush()
            js, comp = egress.journal_summary(con, a.days), compliance_summary(con, a.days)
        finally:
            con.close()
    if a.json:
        print(json.dumps({"status": st, "inventory": inv, "journal": js, "compliance": comp}, indent=2))
    else:
        print(report_text(st, inv, js, comp))


def register(sub, add):
    pp = sub.add_parser("privacy", help="privacy mode, egress inventory and journal (E11-1, E11-4)",
                        description="what may leave this machine: the effective mode, the inventory of every outbound path, the egress journal")
    psub = pp.add_subparsers(dest="privacy_cmd", required=True, metavar="SUBCOMMAND")
    s = add(psub, "status", cmd_status, "the effective privacy mode and what each outbound path may do (skills read this first)")
    s.add_argument("--json", action="store_true")
    s = add(psub, "report", cmd_report, "egress inventory + the last 30 days of the egress journal")
    s.add_argument("--json", action="store_true")
    s.add_argument("--days", type=int, default=30)
