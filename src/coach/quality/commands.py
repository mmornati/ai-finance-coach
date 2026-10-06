"""CLI of E12: ``coach eval fixtures|gold|classify|models|coach|runs``, ``coach usage``, ``coach logs``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from coach import db as db_mod
from coach.quality import (classify_eval as CE, coachcheck as CC, fixtures as F, gold as G, logs as L, models as M, runs as RUNS,
                           usage as U)


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2, default=str)


def _con(a, cfg, migrate: bool = True):
    return db_mod.connect(cfg, insecure=a.insecure, migrate=migrate)


# ---------------------------------------------------------------- eval fixtures synth

def cmd_fixtures_synth(a, cfg):
    out = Path(a.out).expanduser()
    for label, base in (("data_dir", cfg.data_dir), ("the memory folder", cfg.memory_dir), ("the backup folder", cfg.backup_dir)):
        b = base.resolve()
        if out.resolve() == b or b in out.resolve().parents:
            sys.exit(f"error: refusing to write the fixture inside {label} ({base}): it belongs in the repository (tests/fixtures/)")
    con = _con(a, cfg, migrate=False)
    try:
        fixture, rep = F.synthesize(con, cfg, a.bank, a.n, a.seed)
    except (F.SynthError, F.SynthLeak) as e:
        sys.exit(f"error: {e}")
    finally:
        con.close()
    F.write(fixture, out)
    print(f"wrote {out}: {rep['kept']} invented transaction(s) of the {a.bank} shape ({rep['pending']} pending), seed {a.seed}")
    print("  types: " + ", ".join(f"{k} {v}" for k, v in rep["types"].items()))
    print(f"  not kept: {rep['dropped']['unstable']} whose type changed once invented, {rep['skipped_real']['other']} real 'other' line(s) skipped")
    print(f"  real-token check PASSED: {rep['terms_checked']} real term(s) (household terms, every merchant key / description word, long digit "
          f"strings) checked against the output, {rep['leaks']} finding(s)")
    print("  also check it with the repository test: `uv run pytest tests/test_egress_policy.py -k real_data`")


# ---------------------------------------------------------------- eval gold

def cmd_gold_bootstrap(a, cfg):
    con = _con(a, cfg)
    r = G.bootstrap(con, cfg, dry_run=a.dry_run)
    if a.json:
        print(_j(r))
        return
    print(("DRY RUN: nothing written. " if a.dry_run else "") + "what you already decided, by origin (merchant_label = a label of yours for a merchant, "
          "annotation = a memory annotation with a category, override = a per-transaction category, split = the dominant part of a split):")
    for k in G.BOOTSTRAP_ORIGINS:
        print(f"  {k:<16} wanted {r['wanted'].get(k, 0):>5}   added {r['added'].get(k, 0):>5}   updated {r['updated'].get(k, 0):>5}   "
              f"removed {r['removed'].get(k, 0):>5}")
    print(f"labels given by hand kept untouched: {r['manual_kept']}")
    if r["gold"]:
        print(f"gold set now: {r['gold']['total']} transactions, {r['gold']['categories']} categories, by origin {r['gold']['by_origin']}")


def cmd_gold_sample(a, cfg):
    con = _con(a, cfg)
    try:
        items = G.sample(con, cfg, a.n, a.strategy, a.seed)
    except G.GoldError as e:
        sys.exit(f"error: {e}")
    if a.json:
        print(_j(items))
        return
    print(f"{len(items)} transaction(s) to label ({a.strategy}, seed {a.seed}; none is in the gold set, none was decided by you):")
    for it in items:
        print(f"  {it['tx_key']:<46} {it['date']} {it['amount']:>10.2f}  {it['source']:<8}{it['category']:<28}{it['description'][:44]}")
    print("label them with `coach eval gold label` (a terminal) or on the Gold set page of the web app")


def cmd_gold_label(a, cfg):
    if not G.tty():
        sys.exit("error: `coach eval gold label` needs a terminal (it is a human task: there is no --yes)")
    con = _con(a, cfg)
    if a.tx:
        items = []
        for ref in a.tx:
            rows = con.execute("""SELECT t.tx_key, t.booking_date, t.amount, t.description FROM transactions t WHERE t.tx_key=?""", (ref,)).fetchall()
            if not rows:
                sys.exit(f"error: no transaction {ref!r}")
            from coach.classify import rules as R
            r = next(iter(R.categorised(con, memory_dir=cfg.memory_dir, use_splits=False, only_tx_keys={ref}, include_excluded=True)), None)
            items.append({"tx_key": rows[0][0], "date": rows[0][1], "amount": rows[0][2], "description": rows[0][3] or "",
                          "category": r["category"] if r else "other.uncategorized", "source": r["source"] if r else "none", "confidence": None})
    else:
        items = G.sample(con, cfg, a.n, a.strategy, a.seed)
    if not items:
        print("nothing to label: every transaction an automatic step decided is already in the gold set")
        return
    print(f"{len(items)} transaction(s). For each one: a = the current label is right, f = fix it, s = skip, q = quit. Only the gold set is written.")
    res = G.label_loop(con, cfg, items)
    c = G.counts(con)
    print(f"\naccepted {res['accepted']}, fixed {res['fixed']}, skipped {res['skipped']}, left {res['left']}; gold set: {c['total']} transactions")


def cmd_gold_list(a, cfg):
    con = _con(a, cfg)
    rows = [r for r in G.gold_rows(con) if not a.origin or r["origin"] == a.origin]
    if a.json:
        print(_j({"counts": G.counts(con), "items": rows[:a.limit]}))
        return
    c = G.counts(con)
    print(f"gold set: {c['total']} transactions, {c['categories']} categories; by origin {c['by_origin']}; by labeler {c['by_labeled_by']}")
    for r in rows[:a.limit]:
        print(f"  {r['tx_key']:<46} {r['category']:<30} {r['labeled_by']:<11}{r['origin']:<15}{r['labeled_at'][:10]}")


def cmd_gold_remove(a, cfg):
    con = _con(a, cfg)
    print("removed" if G.remove_gold(con, a.tx) else "not in the gold set")


# ---------------------------------------------------------------- eval classify / models / coach / runs

def cmd_eval_classify(a, cfg):
    con = _con(a, cfg)
    res = CE.run(con, cfg, store=not a.no_store)
    if a.json:
        print(_j({k: v for k, v in res.items() if k != "summary"} | {"summary": res["summary"]}))
    else:
        print(CE.format_report(res, a.top))


def cmd_eval_models(a, cfg):
    specs = [x.strip() for x in a.models.split(",") if x.strip()]
    if not specs:
        sys.exit("error: --models needs at least one model (haiku,sonnet,ollama:llama3.1)")
    con = _con(a, cfg)
    try:
        res = M.run(con, cfg, specs, sample=a.sample, seed=a.seed, batch=a.batch, workers=a.workers, dry_run=a.dry_run,
                    show_payload=a.show_payload)
    except (M.ModelsError, ValueError) as e:
        sys.exit(f"error: {e}")
    except Exception as e:                                                          # noqa: BLE001  (an egress refusal: the message names the setting)
        from coach import egress
        if isinstance(e, egress.EgressDenied):
            sys.exit(f"error: {e}")
        raise
    if a.json:
        print(_j(res))


def cmd_eval_coach(a, cfg):
    con = _con(a, cfg)
    if a.offline:
        res = CC.run_offline(con, cfg, skill=a.skill, limit=a.limit, since_days=a.days, store=not a.no_store)
        print(_j(res) if a.json else CC.format_report(res))
        return
    path = Path(a.questions).expanduser() if a.questions else CC.default_suite_path(cfg)
    try:
        qs = CC.load_questions(path)
    except (OSError, ValueError) as e:
        sys.exit(f"error: {e}")
    if a.skill:
        qs = [q for q in qs if q["skill"] == a.skill]
        if not qs:
            sys.exit(f"error: no question of the suite uses the skill {a.skill!r}")
    try:
        res = CC.run_live(con, cfg, qs, insecure=a.insecure, dry_run=a.dry_run)
    except CC.CoachEvalError as e:
        sys.exit(f"error: {e}")
    if res.get("status") == "done":
        print("\n" + CC.format_report(res))
    if a.json and res.get("status") != "dry-run":
        print(_j(res))


def cmd_eval_runs(a, cfg):
    con = _con(a, cfg)
    if a.show:
        r = RUNS.get(con, a.show)
        if not r:
            sys.exit(f"error: no eval run #{a.show}")
        print(_j(r))
        return
    rows = RUNS.listing(con, a.kind, a.limit)
    if a.json:
        print(_j(rows))
        return
    if not rows:
        print("no evaluation run yet: `coach eval classify`, `coach eval models`, `coach eval coach`")
    for r in rows:
        s = r["summary"] or {}
        head = ", ".join(f"{k} {s[k]:.1%}" if isinstance(s.get(k), float) and k.endswith(("accuracy_tx", "accuracy_money", "rate", "ratio")) else f"{k} {s[k]}"
                         for k in ("accuracy_tx", "accuracy_money", "pass_rate", "traced_ratio", "cost_usd") if s.get(k) is not None)
        print(f"#{r['id']:<4}{r['ts'][:16]}  {r['kind']:<9}{(r['label'] or ''):<14}n={r['n']:<5}{head}")


# ---------------------------------------------------------------- usage / logs

def cmd_usage(a, cfg):
    con = _con(a, cfg)
    d = U.summary(con, cfg, a.days)
    print(_j(d) if a.json else U.format_report(d))


def cmd_logs(a, cfg):
    log_dir = cfg.log_dir
    if a.runs or not (a.tail or a.run):
        runs = L.runs_summary(L.read_events(log_dir, a.run))
        if a.json:
            print(_j(runs[-a.limit:]))
            return
        if not runs:
            print(f"no run logged yet in {log_dir / L.LOG_NAME} (`coach schedule run` writes it)")
        for r in runs[-a.limit:]:
            print(f"{r['run']}  {r['started'] or '?'}  {r['outcome']:<8}" + (f"{r['duration_s']}s  " if r["duration_s"] is not None else "")
                  + f"{len(r['steps'])} step(s)" + (f"  FAILED: {', '.join(r['failed'])}" if r["failed"] else "")
                  + (f"  warnings: {', '.join(r['warned'])}" if r["warned"] else ""))
        return
    events = list(L.read_events(log_dir, a.run))
    if a.tail:
        events = events[-a.tail:]
    if a.json:
        print(_j(events))
        return
    for ev in events:
        print(L.format_event(ev))


# ---------------------------------------------------------------- registration

def register(sub, add):
    ep = sub.add_parser("eval", help="quality: parser fixtures, the gold set, classification accuracy, model and coach evaluations",
                        description="quality evaluations (E12): see docs/quality.md")
    esub = ep.add_subparsers(dest="eval_cmd", required=True, metavar="SUBCOMMAND")

    fx = esub.add_parser("fixtures", help="parser fixtures", description="parser fixtures (E12-1)")
    fsub = fx.add_subparsers(dest="fixtures_cmd", required=True, metavar="SUBCOMMAND")
    s = add(fsub, "synth", cmd_fixtures_synth,
            "write fully INVENTED descriptors of the shape of one bank's real ones (reads the local database, writes no real token)")
    s.add_argument("--bank", required=True, choices=list(F.BANKS)); s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=12); s.add_argument("--out", required=True, metavar="FILE", help="e.g. tests/fixtures/fortuneo.json")

    gp = esub.add_parser("gold", help="the gold set: transactions whose category you confirmed", description="the gold set (E12-2)")
    gsub = gp.add_subparsers(dest="gold_cmd", required=True, metavar="SUBCOMMAND")
    s = add(gsub, "bootstrap", cmd_gold_bootstrap, "add what you already decided (labels, annotations, overrides, splits), marked by origin")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--json", action="store_true")
    s = add(gsub, "sample", cmd_gold_sample, "propose transactions to label next (never one already in the gold set)")
    s.add_argument("--n", type=int, default=300); s.add_argument("--strategy", choices=list(G.STRATEGIES), default="money")
    s.add_argument("--seed", type=int, default=7); s.add_argument("--json", action="store_true")
    s = add(gsub, "label", cmd_gold_label, "label transactions interactively (a terminal): explain() and the current label, then accept / fix / skip")
    s.add_argument("tx", nargs="*", help="tx_keys to label (default: a sample)")
    s.add_argument("--n", type=int, default=20); s.add_argument("--strategy", choices=list(G.STRATEGIES), default="money"); s.add_argument("--seed", type=int, default=7)
    s = add(gsub, "list", cmd_gold_list, "the gold set and its counts")
    s.add_argument("--origin", choices=list(G.ORIGINS)); s.add_argument("--limit", type=int, default=50); s.add_argument("--json", action="store_true")
    s = add(gsub, "remove", cmd_gold_remove, "take one transaction out of the gold set"); s.add_argument("tx")

    s = add(esub, "classify", cmd_eval_classify,
            "accuracy of the categorisation on the gold set: by transaction and by money, per source and category (offline, deterministic)")
    s.add_argument("--json", action="store_true"); s.add_argument("--top", type=int, default=12, help="categories shown")
    s.add_argument("--no-store", action="store_true", help="do not record the run in eval_runs")
    s = add(esub, "models", cmd_eval_models,
            "compare models on the gold merchants (a shadow run: no label is written); --dry-run prints the exact payload size and the cost")
    s.add_argument("--models", required=True, help="comma list: haiku,sonnet,ollama:<model>")
    s.add_argument("--sample", type=int, default=100, help="gold merchants to ask (0 = all)"); s.add_argument("--seed", type=int, default=7)
    s.add_argument("--batch", type=int, default=40); s.add_argument("--workers", type=int, default=2)
    s.add_argument("--dry-run", action="store_true", help="build the exact requests, print sizes and an estimate, call nothing")
    s.add_argument("--show-payload", action="store_true", help="with --dry-run: print the first request's dynamic part"); s.add_argument("--json", action="store_true")
    s = add(esub, "coach", cmd_eval_coach, "score the coach's answers: stored ones (--offline) or the curated question suite through the runner (--run)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--offline", action="store_true", help="score the stored answers / insights (no model)")
    g.add_argument("--run", action="store_true", help="ask the suite evals/coach_questions.yaml (a terminal and a typed confirmation)")
    s.add_argument("--skill"); s.add_argument("--limit", type=int, default=200); s.add_argument("--days", type=int, help="offline: only the last N days")
    s.add_argument("--questions", metavar="FILE", help="a question suite (default evals/coach_questions.yaml)")
    s.add_argument("--dry-run", action="store_true", help="with --run: print the questions, the sizes and the cost bound; call no model")
    s.add_argument("--no-store", action="store_true"); s.add_argument("--json", action="store_true")
    s = add(esub, "runs", cmd_eval_runs, "the stored evaluation runs, to compare over time")
    s.add_argument("--kind", choices=list(RUNS.KINDS)); s.add_argument("--limit", type=int, default=15)
    s.add_argument("--show", type=int, metavar="ID"); s.add_argument("--json", action="store_true")

    s = add(sub, "usage", cmd_usage, "LLM usage: calls, tokens, notional cost and duration per job / model, per day, with where the calls went")
    s.add_argument("--days", type=int, default=30); s.add_argument("--json", action="store_true")
    s = add(sub, "logs", cmd_logs, "the structured logs of the scheduled runs (data_dir/logs/runs.jsonl): runs, steps, durations, counts")
    s.add_argument("--tail", type=int, metavar="N", help="the last N events"); s.add_argument("--run", metavar="ID", help="the events of one run (a prefix of its id)")
    s.add_argument("--runs", action="store_true", help="one line per run (the default)"); s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
