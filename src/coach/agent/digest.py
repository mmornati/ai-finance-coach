"""Scheduled analyses (E6-6): the weekly digest and the monthly review. Opt-in (``[coach] schedule_weekly`` /
``schedule_monthly``, default off), skipped when nothing is new since the last run, one fixed prompt each, the result stored
as an insight. ``--dry-run`` shows what the model WOULD see (the prompt and every tool's redacted output) and calls nothing."""
from __future__ import annotations

import datetime as dt
import json
from typing import Callable, Optional

from coach import db
from coach.agent import insights as I, prompt as P, service
from coach.agent.runner import CoachUnavailable, run_agent
from coach.mcp.tools import READ_ONLY, ToolSession

KINDS = {"weekly": P.WEEKLY, "monthly": P.MONTHLY}
# the argument sets a digest's prompt uses (the dry run shows exactly these outputs)
DRY_CALLS = {"weekly": [("coverage", {}), ("cashflow", {"months": 3}), ("anomalies", {}), ("price_changes", {}),
                        ("budget_status", {}), ("forecast", {"days": 45}), ("calendar", {"days": 14})],
             "monthly": [("coverage", {}), ("cashflow", {"months": 6}), ("category_averages", {}), ("recurring", {}),
                         ("price_changes", {}), ("anomalies", {}), ("budget_status", {}), ("budget_suggestions", {}),
                         ("forecast", {"days": 60})]}


def due(con, kind: str, today: dt.date) -> tuple[bool, str]:
    """(run?, why not). weekly: no digest in the last 7 days; monthly: none yet in this calendar month."""
    last = I.last_digest(con, KINDS[kind].id)
    if last is None:
        return True, ""
    created = dt.datetime.fromisoformat(last["created"]).date()
    if kind == "weekly" and (today - created).days < 7:
        return False, f"the last weekly digest is from {created} (less than 7 days ago)"
    if kind == "monthly" and (created.year, created.month) == (today.year, today.month):
        return False, f"the monthly review for {today:%Y-%m} already exists"
    return True, ""


def dry_run(cfg, kind: str, *, insecure: bool = False, today: Optional[dt.date] = None, out: Callable = print,
            full: bool = True) -> dict:
    """Prints what a real run would send and start, built by the same functions: the claude command line, the MCP config, the
    system and user prompts, the MCP instructions, every tool definition the model gets, and the redacted outputs of the tool
    calls the prompt asks for. Calls no model."""
    import shlex
    from coach.agent.runner import describe_payload
    spec = KINDS[kind]
    pl = describe_payload(cfg, spec, insecure=insecure)
    s = ToolSession(cfg, insecure=insecure, today=today, session_id="dry-run", only=spec.tools)
    try:
        out(f"# backend: {cfg.coach_backend}, model: {pl['model']}, max tool calls: {cfg.coach_max_tool_calls * spec.tool_factor}, "
            f"timeout: {cfg.coach_timeout * spec.timeout_factor}s")
        out("# --- isolation ---")
        out(pl["isolation"])
        if cfg.coach_backend == "claude-code":
            out("# --- claude command (the question goes through stdin) ---")
            out(" ".join(shlex.quote(c) if c != "" else "''" for c in pl["claude_command"]))
            out("# --- MCP config handed to claude ---")
            out(json.dumps(pl["mcp_config"], indent=1))
            out("# --- environment names passed to claude (nothing else) ---")
            out(", ".join(pl["claude_env"]))
        out("# --- system prompt ---")
        out(pl["system_prompt"])
        out("# --- user prompt ---")
        out(pl["user_prompt"])
        out("# --- MCP server instructions ---")
        out(pl["mcp_instructions"])
        out("# --- tools available to the model (name, description, input schema) ---")
        for t in pl["tools"]:
            out(f"- {t['name']}{'  [WRITES: ' + t['writes'] + ']' if t['writes'] else ''}")
            out(f"    {t['description']}")
            out("    " + json.dumps(t["input_schema"], separators=(",", ":")))
        rows = []
        out("# --- what the tools would return (redacted) for the calls the prompt asks for ---")
        for name, args in DRY_CALLS[kind]:
            assert name in READ_ONLY
            r = s.call(name, args)
            rows.append({"tool": name, "args": args, "ok": r.ok, "chars": len(r.text)})
            out(f"## {name} {json.dumps(args)}  ({len(r.text)} characters{'' if r.ok else ', REFUSED'})")
            if full:
                out(r.text)
        sysmsg, user = pl["system_prompt"], pl["user_prompt"]
        return {"prompt_chars": len(sysmsg) + len(user), "system_chars": len(sysmsg), "user_chars": len(user),
                "tools": [t["name"] for t in pl["tools"]], "outputs": rows, "suspicious": s.suspicious,
                "payload_chars": len(json.dumps(pl["tools"])) + len(sysmsg) + len(user)}
    finally:
        s.close()


def run_digest(cfg, kind: str, *, insecure: bool = False, dry: bool = False, force: bool = False,
               today: Optional[dt.date] = None, out: Callable = print, **backend_kw) -> dict:
    """Returns {"status": "done" | "skipped" | "dry-run" | "failed", ...}. Never raises for a backend problem."""
    today = today or dt.date.today()
    spec = KINDS[kind]
    if dry:
        return {"status": "dry-run", **dry_run(cfg, kind, insecure=insecure, today=today, out=out)}
    con = db.connect(cfg, insecure=insecure)
    try:
        s = ToolSession(cfg, con=con, insecure=insecure, today=today)
        fp = s.data_fingerprint()
        last = I.last_digest(con, spec.id)
        if not force:
            ok, why = due(con, kind, today)
            if not ok:
                return {"status": "skipped", "reason": why}
            if last and last.get("data_through") == fp:
                return {"status": "skipped", "reason": f"no new data since the last {kind} digest ({last['created'][:10]})"}
        try:
            res = run_agent(cfg, spec, insecure=insecure, **backend_kw)
        except CoachUnavailable as e:
            return {"status": "failed", "reason": str(e)}
        title = f"{spec.title} {today.isoformat()}"
        fin = service.finalize(con, spec, res, data_through=fp, title=title, today=today)
        ok = res.finish_reason in service.STORABLE and bool(res.text)
        return {"status": "done" if ok else "failed", "finish_reason": res.finish_reason, "error": res.error,
                "insight_id": fin["insight_id"], "tool_calls": len(res.tool_calls), "usage": res.usage, "suspicious": res.suspicious,
                "unverified_numbers": res.unverified_numbers,
                "reason": None if ok else (res.error or f"the run ended with {res.finish_reason}")}
    finally:
        con.close()
