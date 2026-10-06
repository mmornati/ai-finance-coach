"""Coach answer checks (E12-5): are the answers of the coach traceable, labelled, compliant and shaped as the skill asks?

``score_text`` is one pure scorer used twice:

* ``coach eval coach --offline``  over the answers and insights already STORED (``insights``): nothing is called, nothing is written but
  an ``eval_runs`` row; it is what tells you, after a prompt or a model change, that the numbers are still traced to tool results;
* ``coach eval coach --run``      over the curated question suite ``evals/coach_questions.yaml``, asked through the REAL runner
  (same prompts, tools, redaction and egress gate as ``coach coach ask``), and scored the same way. The answers are not stored as
  insights (the feed must not fill with test questions); the usage is recorded (purpose ``eval:coach``) and the result goes to ``eval_runs``.

Checks (each is pass / fail / not applicable):

  numbers_traced   every number of the text was returned by a tool (the ``unverified_numbers`` the runner kept; years and the integers 0-31
                   are not numbers here, as in the runner). The ratio and the unverified numbers are listed.
  evidence_refs    every evidence ref cited in the text (``h_...``, ``rec_...``) is one a tool returned in that session (``evidence``)
  ai_label         a model-written text carries the AI-generated flag (the label shown to the user is derived from it)
  compliance       ``coach.compliance`` finds no ISIN, product name or recommendation
  sections         what the skill's prompt requires (monthly-review: a "Three actions" heading and EXACTLY three numbered actions ...)
  disclaimer       a text about saving or investing ends with the general-advice sentence; the skills that must carry a disclaimer do
  length           at most the number of words the skill's prompt asks for (+10 %: models count loosely), and not empty
  completed        (live runs) the run finished normally and called at least one tool unless the question needs none
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Optional

import yaml

from coach import compliance, disclaimers
from coach.agent import prompt as P
from coach.mcp.guard import _canon, numbers_in_text
from coach.mcp.tools import REF_RE
from coach.quality import gold as G, runs

LENGTH_SLACK = 1.10
PHRASE = "send coach questions"
CHECKS = ("numbers_traced", "evidence_refs", "ai_label", "compliance", "sections", "disclaimer", "length", "completed")
SAVING_RE = re.compile(r"\b(invest\w*|saving\w*|savings|épargn\w*|epargn\w*|placement\w*|risparmi\w*|portfolio|etf)\b", re.I)

# What each prompt asks for (coach.agent.prompt). words: "at most N words"; heading / actions: a heading line followed by numbered items
SKILL_RULES: dict[str, dict] = {
    "ask": {"max_words": 350},
    "digest-weekly": {"max_words": 180},
    "digest-monthly": {"max_words": 300},
    "monthly-review": {"max_words": 250, "heading": r"three actions", "actions": 3},
    "explain-spike": {"max_words": 200},
    "subscription-audit": {"max_words": 300, "advice": True},
    "contract-check": {"verify": True},
    "mortgage-check": {"max_words": 250, "advice": True, "phrases": [r"estimate only"]},
    "what-if": {"max_words": 200, "phrases": [r"estimate"]},
    "tax-helper": {"max_words": 300, "advice": True, "phrases": [r"impots\.gouv\.fr|agenzia delle entrate|verify on"]},
    "onboarding-interview": {"max_words": 250},
}


def words_of(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _norm(s: str) -> str:
    return re.sub(r"[\s.]+$", "", re.sub(r"\s+", " ", (s or "").casefold())).strip()


def has_general_advice(text: str) -> bool:
    t = _norm(text)
    return any(_norm(v) in t for v in disclaimers.all_languages("general_advice").values())


def has_contract_verify(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").casefold())
    return any(re.sub(r"\s+", " ", v.casefold()).split(":")[0][:40] in t for v in disclaimers.all_languages("contract_verify").values()) \
        or "verify with your contract" in t or "vérifiez avec votre contrat" in t or "verifica con il contratto" in t


def actions_after(text: str, heading: str) -> Optional[int]:
    """The number of numbered items after the heading line matching `heading`; None when there is no such heading."""
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if re.search(heading, ln, re.I) and (re.match(r"\s*(#{1,6}|\*\*|__)", ln) or len(ln.split()) <= 4):
            return sum(1 for x in lines[i + 1:] if re.match(r"\s*\d+[.)]\s+\S", x))
    return None


def _check(passed: Optional[bool], detail: Optional[dict] = None) -> dict:
    return {"applicable": passed is not None, "pass": passed, **(detail or {})}


def score_text(item: dict, extra: Optional[dict] = None) -> dict:
    """item: {id, skill, body, title, evidence, unverified_numbers, ai_generated, backend, [finish_reason, tool_calls]}; extra: per-question
    expectations (max_words, no_tools, must_include_any, must_not_include). Returns {id, skill, checks: {name: {...}}, passed, failed}."""
    body = item.get("body") or ""
    skill = item.get("skill") or "ask"
    rules = {**SKILL_RULES.get(skill, {}), **(extra or {})}
    out: dict[str, dict] = {}
    # numbers
    nums = {_canon(d) for d in numbers_in_text(body, strict=False)}
    unv = [u for u in (item.get("unverified_numbers") or []) if isinstance(u, str)]
    bad = sorted(set(unv) & nums) if nums else []
    out["numbers_traced"] = _check(None if not nums else not bad,
                                   {"numbers": len(nums), "unverified": bad, "traced_ratio": round(1 - len(bad) / len(nums), 4) if nums else None})
    # evidence refs
    cited = list(dict.fromkeys(REF_RE.findall(body)))
    ev = {e for e in (item.get("evidence") or []) if isinstance(e, str)}
    invalid = [r for r in cited if r not in ev]
    out["evidence_refs"] = _check(None if not cited else not invalid, {"cited": len(cited), "invalid": invalid})
    # AI label
    model_written = item.get("backend") not in ("local", "code")
    out["ai_label"] = _check(None if not model_written else bool(item.get("ai_generated")), {"backend": item.get("backend")})
    # compliance
    flags = compliance.check(f"{item.get('title') or ''}\n{body}")
    out["compliance"] = _check(not flags, {"codes": compliance.codes(flags)})
    # sections / shape
    problems = []
    if rules.get("heading"):
        n = actions_after(body, rules["heading"])
        if n is None:
            problems.append("the heading is missing")
        elif rules.get("actions") is not None and n != rules["actions"]:
            problems.append(f"{n} numbered action(s), exactly {rules['actions']} required")
    inc = rules.get("must_include_any")
    if inc and not any(re.search(p, body, re.I) for p in inc):
        problems.append("none of the expected phrases is present")
    for pat in rules.get("must_not_include") or []:
        if re.search(pat, body, re.I):
            problems.append("a forbidden phrase is present")
    shaped = bool(rules.get("heading") or inc or rules.get("must_not_include"))
    out["sections"] = _check(None if not shaped else not problems, {"problems": problems})
    # disclaimer
    lang = compliance.detect_lang(body)
    need = []
    if rules.get("advice") or (SAVING_RE.search(body) and skill in ("ask", "digest-weekly", "digest-monthly", "monthly-review")):
        if not has_general_advice(body):
            need.append("general advice sentence")
    if rules.get("verify") and not has_contract_verify(body):
        need.append("contract verification line")
    for pat in rules.get("phrases") or []:
        if not re.search(pat, body, re.I):
            need.append("required wording")
    applicable = bool(rules.get("advice") or rules.get("verify") or rules.get("phrases") or SAVING_RE.search(body))
    out["disclaimer"] = _check(None if not applicable else not need, {"missing": need, "lang": lang})
    # length
    w = words_of(body)
    cap = rules.get("max_words")
    over = bool(cap and w > cap * LENGTH_SLACK)
    out["length"] = _check(w >= 3 and not over, {"words": w, "max_words": cap})
    # a live run
    if "finish_reason" in item:
        calls = item.get("tool_calls") or 0
        ok = item["finish_reason"] == "stop" and (rules.get("no_tools") or calls >= 1)
        out["completed"] = _check(ok, {"finish_reason": item["finish_reason"], "tool_calls": calls})
    else:
        out["completed"] = _check(None)
    failed = [k for k, v in out.items() if v["applicable"] and not v["pass"]]
    return {"id": item.get("id"), "skill": skill, "checks": out, "failed": failed, "passed": not failed,
            "applicable": sum(1 for v in out.values() if v["applicable"])}


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    checks = {}
    for name in CHECKS:
        app = [s for s in scored if s["checks"][name]["applicable"]]
        ok = [s for s in app if s["checks"][name]["pass"]]
        checks[name] = {"applicable": len(app), "passed": len(ok), "rate": round(len(ok) / len(app), 4) if app else None,
                        "failing": [s["id"] for s in app if not s["checks"][name]["pass"]][:20]}
    nums = [s["checks"]["numbers_traced"] for s in scored if s["checks"]["numbers_traced"]["applicable"]]
    total = sum(c["numbers"] for c in nums)
    bad = sum(len(c["unverified"]) for c in nums)
    by_skill: dict[str, dict] = {}
    for s in scored:
        d = by_skill.setdefault(s["skill"], {"n": 0, "passed": 0})
        d["n"] += 1
        d["passed"] += int(s["passed"])
    return {"n": n, "all_passed": sum(1 for s in scored if s["passed"]), "pass_rate": round(sum(1 for s in scored if s["passed"]) / n, 4) if n else None,
            "checks": checks, "numbers": {"total": total, "unverified": bad, "traced_ratio": round(1 - bad / total, 4) if total else None},
            "unverified_listed": sorted({u for c in nums for u in c["unverified"]})[:30],
            "by_skill": {k: {**v, "rate": round(v["passed"] / v["n"], 4)} for k, v in sorted(by_skill.items())}}


# ---------------------------------------------------------------- offline: the stored answers

def stored_items(con, *, skill: Optional[str] = None, limit: int = 200, since_days: Optional[int] = None) -> list[dict]:
    import json
    q = ("SELECT id, created, kind, title, body, evidence, skill, backend, ai_generated, unverified_numbers FROM insights "
         "WHERE ai_generated=1 AND body <> ''")
    args: list = []
    if skill:
        q, args = q + " AND skill=?", args + [skill]
    if since_days:
        import datetime as dt
        q, args = q + " AND created >= ?", args + [(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).isoformat(timespec="seconds")]
    out = []
    for r in con.execute(q + " ORDER BY created DESC LIMIT ?", (*args, limit)):
        def lj(s):
            try:
                v = json.loads(s or "[]")
                return v if isinstance(v, list) else []
            except ValueError:
                return []
        out.append({"id": r[0], "created": r[1], "kind": r[2], "title": r[3], "body": r[4], "evidence": lj(r[5]), "skill": r[6] or "ask",
                    "backend": r[7], "ai_generated": bool(r[8]), "unverified_numbers": lj(r[9])})
    return out


def run_offline(con, cfg, *, skill: Optional[str] = None, limit: int = 200, since_days: Optional[int] = None, store: bool = True) -> dict:
    t0 = time.monotonic()
    items = stored_items(con, skill=skill, limit=limit, since_days=since_days)
    scored = [score_text(i) for i in items]
    res = {"mode": "offline", "skill": skill, **aggregate(scored), "items": [{"id": s["id"], "skill": s["skill"], "failed": s["failed"]}
                                                                           for s in scored if s["failed"]]}
    summary = {"n": res["n"], "pass_rate": res["pass_rate"], "traced_ratio": res["numbers"]["traced_ratio"],
               "unverified": res["numbers"]["unverified"], **{f"{k}_rate": v["rate"] for k, v in res["checks"].items() if v["applicable"]}}
    res["summary"] = summary
    if store:
        res["run_id"] = runs.record(con, "coach", label="offline", n=res["n"], duration_s=round(time.monotonic() - t0, 3), summary=summary,
                                    result=res)
    return res


# ---------------------------------------------------------------- the question suite

def default_suite_path(cfg) -> Path:
    """evals/coach_questions.yaml next to the configuration, else the one of the checkout this package runs from."""
    mine = Path(cfg.root) / "evals" / "coach_questions.yaml"
    return mine if mine.exists() else Path(__file__).resolve().parents[3] / "evals" / "coach_questions.yaml"


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    qs = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(qs, list) or not qs:
        raise ValueError(f"{path}: no `questions:` list")
    out, seen = [], set()
    for q in qs:
        if not isinstance(q, dict) or not q.get("id") or not q.get("question"):
            raise ValueError(f"{path}: every question needs an id and a question")
        if q["id"] in seen:
            raise ValueError(f"{path}: duplicate question id {q['id']!r}")
        seen.add(q["id"])
        skill = q.get("skill") or "ask"
        if skill not in P.PROMPTS:
            raise ValueError(f"{path}: question {q['id']!r}: unknown skill {skill!r} (runnable: {', '.join(P.PROMPTS)})")
        out.append({**q, "skill": skill})
    return out


def request_text(q: dict) -> str:
    skill = q["skill"]
    if skill in P.SKILL_SPECS:
        return P.skill_request(q["question"], month=q.get("month"), year=q.get("year"), country=q.get("country"))
    return q["question"]


def plan(cfg, questions: list[dict]) -> dict:
    rows = []
    for q in questions:
        spec = P.PROMPTS[q["skill"]]
        msg = P.user_message(spec, request_text(q))
        sysm = P.system_prompt(cfg.coach_max_tool_calls * spec.tool_factor)
        budget = float(cfg.coach_max_budget_usd or 0) * spec.tool_factor
        rows.append({"id": q["id"], "skill": q["skill"], "lang": q.get("lang", "en"), "request_bytes": len((sysm + msg).encode("utf-8")),
                     "max_tool_calls": cfg.coach_max_tool_calls * spec.tool_factor, "max_cost_usd": budget or None})
    bound = sum(r["max_cost_usd"] or 0 for r in rows)
    return {"backend": cfg.coach_backend, "model": cfg.coach_model_effective, "questions": rows,
            "bytes": sum(r["request_bytes"] for r in rows), "max_cost_usd": round(bound, 2) if bound else None}


def format_plan(p: dict) -> str:
    L = [f"{len(p['questions'])} question(s) through the coach backend {p['backend']} ({p['model']}):"]
    for r in p["questions"]:
        L.append(f"  {r['id']:<30}{r['skill']:<22}{r['lang']:<4}{r['request_bytes']:>7} bytes  up to {r['max_tool_calls']} tool calls"
                 + (f", about {r['max_cost_usd']:g} USD at most" if r["max_cost_usd"] else ""))
    L.append(f"total: {p['bytes']} bytes of prompts (the finance tools answer with REDACTED, pseudonymised results)"
             + (f"; the per-run budget caps the cost at {p['max_cost_usd']:g} USD" if p["max_cost_usd"] else ""))
    return "\n".join(L)


class CoachEvalError(RuntimeError):
    pass


def run_live(con, cfg, questions: list[dict], *, insecure: bool = False, dry_run: bool = False, input_fn: Callable[[str], str] = input,
             out=print, isatty: Optional[Callable[[], bool]] = None, runner: Optional[Callable] = None, store: bool = True) -> dict:
    isatty = isatty or G.tty
    p = plan(cfg, questions)
    out(format_plan(p))
    if dry_run:
        out("\nDRY RUN: no model was called, nothing was written.")
        return {"status": "dry-run", "plan": p}
    from coach import egress
    ok, code, why = egress.evaluate(f"llm.{cfg.coach_backend}", {"host": egress.host_of(getattr(cfg, "llm_ollama_url", ""))
                                                                  if cfg.coach_backend == "ollama" else ""}, cfg=cfg)
    if not ok:
        raise CoachEvalError(f"the privacy policy refuses the coach backend: {why}")
    if not isatty():
        raise CoachEvalError("a live run needs a terminal: it costs money (or notional quota) and sends the questions and the REDACTED tool "
                             "results to the backend, so it cannot run from a script or an agent. Use --dry-run to see what would be asked.")
    out(f"\nThis asks the model {len(questions)} question(s) (estimated cost bound {p['max_cost_usd'] or 'unknown'} USD).")
    if input_fn(f"Type {PHRASE!r} to continue (anything else aborts): ").strip() != PHRASE:
        out("aborted: nothing was sent.")
        return {"status": "aborted"}
    from coach.agent.runner import CoachUnavailable, run_agent
    from coach.classify.backends import record_usage
    runner = runner or run_agent
    scored, t0, cost = [], time.monotonic(), 0.0
    for q in questions:
        spec = P.PROMPTS[q["skill"]]
        try:
            res = runner(cfg, spec, request_text(q), insecure=insecure)
        except CoachUnavailable as e:
            raise CoachEvalError(str(e)) from e
        if res.usage is not None:
            res.usage.purpose = "eval:coach"
            if res.usage.tokens_in or res.usage.tokens_out or res.usage.cost_usd:
                record_usage(con, res.usage)
                con.commit()
            cost += res.usage.cost_usd or 0.0
        item = {"id": q["id"], "skill": q["skill"], "title": q["id"], "body": res.text, "evidence": res.refs,
                "unverified_numbers": res.unverified_numbers, "ai_generated": True, "backend": res.backend, "finish_reason": res.finish_reason,
                "tool_calls": len(res.tool_calls)}
        s = score_text(item, {k: q[k] for k in ("max_words", "no_tools", "must_include_any", "must_not_include") if k in q})
        s["usage"] = {"tokens_in": res.usage.tokens_in if res.usage else 0, "tokens_out": res.usage.tokens_out if res.usage else 0,
                      "cost_usd": res.usage.cost_usd if res.usage else None, "duration_s": res.usage.duration_s if res.usage else None}
        scored.append(s)
        out(f"  {q['id']:<30}{'PASS' if s['passed'] else 'FAIL ' + ', '.join(s['failed'])}")
    agg = aggregate(scored)
    res = {"mode": "live", **agg, "items": [{"id": s["id"], "skill": s["skill"], "failed": s["failed"], "usage": s["usage"]} for s in scored],
           "backend": cfg.coach_backend, "model": cfg.coach_model_effective, "cost_usd": round(cost, 6)}
    res["summary"] = {"n": agg["n"], "pass_rate": agg["pass_rate"], "traced_ratio": agg["numbers"]["traced_ratio"],
                      "unverified": agg["numbers"]["unverified"], "cost_usd": round(cost, 6)}
    if store:
        res["run_id"] = runs.record(con, "coach", label="live", backend=cfg.coach_backend, model=cfg.coach_model_effective, n=agg["n"],
                                    cost_usd=round(cost, 6), duration_s=round(time.monotonic() - t0, 2), summary=res["summary"], result=res)
    res["status"] = "done"
    return res


# ---------------------------------------------------------------- text

def format_report(res: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.0%}"
    L = [f"coach answer checks ({res['mode']}): {res['n']} answer(s), {res['all_passed']} passed every check ({pct(res['pass_rate'])})"]
    if not res["n"]:
        L.append("nothing to score: no stored answer yet (ask the coach something), or use `--run` for the question suite")
        return "\n".join(L)
    nm = res["numbers"]
    L.append(f"numbers traced to a tool: {pct(nm['traced_ratio'])} ({nm['total']} numbers, {nm['unverified']} unverified)"
             + (f"; unverified: {', '.join(res['unverified_listed'][:12])}" if res["unverified_listed"] else ""))
    for name, c in res["checks"].items():
        if c["applicable"]:
            L.append(f"  {name:<16}{c['passed']}/{c['applicable']} ({pct(c['rate'])})"
                     + (f"   failing: {', '.join(map(str, c['failing'][:6]))}" if c["failing"] else ""))
    L.append("by skill: " + ", ".join(f"{k} {v['passed']}/{v['n']}" for k, v in res["by_skill"].items()))
    if res.get("run_id"):
        L.append(f"stored as eval run #{res['run_id']}")
    return "\n".join(L)
