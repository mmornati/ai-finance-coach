"""``coach coach ask | digest | tools`` (E6-3, E6-6)."""
from __future__ import annotations

import argparse
import json
import sys

from coach import compliance, db as db_mod
from coach.agent import digest as digest_mod, prompt as P, service
from coach.agent.runner import CoachUnavailable, run_agent
from coach.mcp.tools import ToolSession


def _spec_of(a):
    """The PromptSpec and the request text of `coach coach ask` (a plain question, or --skill NAME with its parameters)."""
    skill = getattr(a, "skill", None)
    if not skill:
        if not a.question:
            sys.exit("error: give a question, or --skill NAME (see `coach coach skills`)")
        return P.ASK, a.question
    spec = P.SKILL_SPECS.get(skill)
    if spec is None:
        extra = (" It needs web search, which only the interactive Claude Code skill has (.claude/skills/find-cheaper)."
                 if skill in P.SKILLS else "")
        sys.exit(f"error: no runnable skill {skill!r}.{extra} Known: {', '.join(P.SKILL_SPECS)}")
    return spec, P.skill_request(a.question, month=a.month, year=a.year, country=a.country)


def cmd_skills(a, cfg):
    for sid, info in P.SKILLS.items():
        spec = info["spec"]
        where = f"coach coach ask --skill {sid}" if spec else "Claude Code only (web search)"
        print(f"{sid:<22}{info['story']:<7}{where}")
        if spec and a.verbose:
            print(f"    tools: {', '.join(spec.tools or ())}")


def cmd_ask(a, cfg):
    spec, question = _spec_of(a)
    if getattr(a, "dry_run", False):
        from coach.agent.runner import describe_payload
        pl = describe_payload(cfg, spec, insecure=a.insecure)
        print(f"# DRY RUN: nothing is sent. skill/prompt: {spec.id}; backend {pl['backend']}, model {pl['model']}")
        print("# --- system prompt ---\n" + pl["system_prompt"])
        print("# --- user prompt ---\n" + P.user_message(spec, question))
        print("# --- tools the model may call ---\n" + ", ".join(t["name"] for t in pl["tools"]))
        return
    streamed = []

    def emit(ev, d):
        if ev == "delta":
            streamed.append(1)
            sys.stdout.write(d["text"])
            sys.stdout.flush()
        elif ev == "init":
            print(f"[claude init: {json.dumps(d)}]", file=sys.stderr)
        elif ev == "tool_call":
            print(f"\n[tool {d['n']}/{d['max']}: {d['name']}]", file=sys.stderr)
        elif ev in ("notice", "error"):
            print(f"\n[{ev}] {d['message']}", file=sys.stderr)
        elif ev == "proposal":
            print(f"\n[proposal {d['id']}: review it, then {d.get('command')}]", file=sys.stderr)
    print(f"[{compliance.label(short=True)}]", file=sys.stderr)          # E11-5: the answer below is written by a model
    try:
        res = run_agent(cfg, spec, question, emit=emit, insecure=a.insecure)
    except CoachUnavailable as e:
        sys.exit(f"error: {e}")
    if not streamed and res.text:
        sys.stdout.write(res.text)
    print()
    if res.text:
        head, foot = compliance.cli_block(res.text)
        print(f"\n{head}")
        if foot:
            print(foot)
    con = db_mod.connect(cfg, insecure=a.insecure)
    try:
        fin = service.finalize(con, spec, res, question=question)
    finally:
        con.close()
    u = res.usage
    print(f"\n-- {res.backend} {res.model}: {len(res.tool_calls)} tool call(s), {u.tokens_in if u else 0} in / "
          f"{u.tokens_out if u else 0} out tokens" + (f", about ${u.cost_usd:.4f}" if u and u.cost_usd else "")
          + (f"; finished: {res.finish_reason}" if res.finish_reason != "stop" else "")
          + (f"; {len(res.unverified_numbers)} number(s) not traced to a tool" if res.unverified_numbers else "")
          + ("; SUSPICIOUS text was seen in your data" if res.suspicious else "")
          + (f"; stored as {fin['insight_id']}" if fin["insight_id"] else ""), file=sys.stderr)
    if res.finish_reason in ("error", "unsafe_tools"):
        sys.exit(f"error: {res.error or res.finish_reason}")


def cmd_digest(a, cfg):
    kind = "weekly" if a.weekly else "monthly"
    r = digest_mod.run_digest(cfg, kind, insecure=a.insecure, dry=a.dry_run, force=a.force)
    if r["status"] == "dry-run":
        print(f"\n# dry run: nothing was sent to any model. prompt {r['prompt_chars']} characters, "
              f"{len(r['tools'])} tools, {len(r['outputs'])} tool outputs ({sum(o['chars'] for o in r['outputs'])} characters).")
    elif r["status"] == "skipped":
        print(f"skipped: {r['reason']}")
    elif r["status"] == "done":
        print(f"{compliance.label(short=True)}: {kind} digest stored as {r['insight_id']} ({r['tool_calls']} tool call(s))"
              + (f"; {len(r['unverified_numbers'])} number(s) not traced to a tool" if r["unverified_numbers"] else "")
              + ("; SUSPICIOUS text was seen in your data" if r["suspicious"] else ""))
    else:
        sys.exit(f"error: the {kind} digest did not complete: {r['reason']}")


def cmd_tool(a, cfg):
    """Run ONE finance tool exactly as the model would see it (redacted, privacy-checked) - for checking a skill's numbers."""
    try:
        args = json.loads(a.args) if a.args else {}
    except ValueError as e:
        sys.exit(f"error: --args must be a JSON object ({e})")
    s = ToolSession(cfg, insecure=a.insecure)
    try:
        if a.name not in s.specs:
            sys.exit(f"error: unknown tool {a.name!r}; see `coach coach tools`")
        if s.specs[a.name].writes:
            sys.exit(f"error: {a.name} writes (a proposal or an insight): it is only available to the model, not from this command")
        r = s.call(a.name, args)
        print(json.dumps(r.payload, ensure_ascii=False, indent=2) if r.ok or a.raw_errors else r.text)
        if not r.ok:
            sys.exit(1)
    finally:
        s.close()


def cmd_tools(a, cfg):
    s = ToolSession(cfg, insecure=a.insecure)
    try:
        for t in s.listing():
            line = f"{t['name']:<25}{'WRITES ' + t['writes'] if t['writes'] else 'read-only'}"
            if a.sizes and t["writes"] is None:
                from coach.skills.tools import SAMPLE_ARGS
                args = {"tx_ref": next(iter(s.data()[3]), "h_0000000000")} if t["name"] == "explain_transaction" else SAMPLE_ARGS.get(t["name"], {})
                r = s.call(t["name"], args)
                line += f"  {len(r.text):>7} chars  {'ok' if r.ok else 'REFUSED: ' + json.loads(r.text).get('error', '')[:60]}"
            print(line)
        if a.sizes:
            print(f"\nsession suspicious: {s.suspicious}")
    finally:
        s.close()


def register(sub, add):
    cp = sub.add_parser("coach", help="the LLM coach: ask, scheduled digests, the tools it sees",
                        description="the LLM coach (E6): numbers come from code, words from the model")
    csub = cp.add_subparsers(dest="coach_cmd", required=True, metavar="SUBCOMMAND")
    s = add(csub, "ask", cmd_ask, "ask the coach a question, or run a skill (--skill monthly-review ...)")
    s.add_argument("question", nargs="?", help="the question (optional with --skill: it then refines the request)")
    s.add_argument("--skill", help="run a skill prompt: " + ", ".join(P.SKILL_SPECS))
    s.add_argument("--month", help="YYYY-MM (monthly-review, explain-spike)")
    s.add_argument("--year", help="income year (tax-helper)")
    s.add_argument("--country", choices=["FR", "IT"], help="FR or IT (tax-helper, contract-check, mortgage-check)")
    s.add_argument("--dry-run", action="store_true", help="print the exact prompts and the tools; call no model")
    s = add(csub, "skills", cmd_skills, "list the coach skills and how to run them")
    s.add_argument("--verbose", action="store_true")
    s = add(csub, "tool", cmd_tool, "run one READ-ONLY finance tool as the model sees it (redacted), e.g. tax_candidates")
    s.add_argument("name")
    s.add_argument("--args", help="JSON object of arguments")
    s.add_argument("--raw-errors", action="store_true", help=argparse.SUPPRESS)
    s = add(csub, "digest", cmd_digest, "weekly digest or monthly review through the backend (opt-in in the scheduler)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--weekly", action="store_true")
    g.add_argument("--monthly", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="print the exact prompt and the redacted tool outputs; call no model")
    s.add_argument("--force", action="store_true", help="run even if nothing is new / one already exists")
    s = add(csub, "tools", cmd_tools, "list the finance tools; --sizes runs each one (redacted, privacy-checked) and shows its size")
    s.add_argument("--sizes", action="store_true")
