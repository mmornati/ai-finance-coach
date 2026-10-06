"""``coach eval models`` (E12-3): which model labels merchants best, measured on the gold set, WITHOUT changing a single label.

A shadow run takes the merchants that have gold transactions, asks each model to label them with the SAME request the real run builds
(the same candidates, the same person guard, the same redaction, the same prompt), keeps the answers in memory, scores them against the
gold labels and stores the numbers in ``eval_runs``. Nothing is written to ``merchants``, ``tx_overrides`` or ``merchant_eval``.

Two differences from a real run, both on purpose: the merchants being evaluated are NOT shown to the model as the user's examples or as
nearest-neighbour hints (they would hand it the answer), and the Message Batches API is not used (one request after the other, so the
latency of each model is measured).

``--dry-run`` builds the exact requests and prints their size and an estimated cost; no model is called. A real run needs a terminal and a
typed confirmation (it costs money or notional quota, and the redacted descriptors leave this machine for the backend), and every request
goes through the egress gate (``[privacy] local_only`` / ``offline`` refuse it).
"""
from __future__ import annotations

import math
import random
import time
from collections import Counter
from typing import Callable, Optional

from coach import egress
from coach.classify import backends as B
from coach.classify import rules as R
from coach.classify.candidates import hold_back, household_names, known_merchants, known_places
from coach.classify.llm import label_keys, prepare_jobs
from coach.quality import classify_eval as CE, gold as G, runs

PHRASE = "send gold merchants"
TOKENS_PER_CHAR = 1 / 3.6            # a rough estimate of the tokens of a JSON / French text prompt
TOKENS_OUT_PER_ITEM = 55             # one result object of the label schema


class ModelsError(RuntimeError):
    pass


# ---------------------------------------------------------------- what is evaluated

def gold_merchants(con, cfg, sample: int = 0, seed: int = 7) -> dict:
    """The gold merchants a model can be asked about: {"keys": [...], "gold_tx": {key: [(gold, amount)]}, "excluded": {...counts}}."""
    anns = G.annotation_index(cfg)
    # only merchant-level truth: a model cannot know a context rule (an annotation with category_in / dates / amounts, an override, a split)
    gold = {r["tx_key"]: r["category"] for r in G.gold_rows(con) if G.truth_of(r, anns)[0] == "merchant_truth"}
    if not gold:
        return {"keys": [], "gold_tx": {}, "excluded": {"no_gold": 0}, "keys_all": 0}
    type_rule_types = set(R.load_rules()["type_rules"])
    by_key: dict[str, dict] = {}
    for tx_key, key, ttype, raw, amount in con.execute(
            """SELECT t.tx_key, COALESCE(e.merchant_key,''), COALESCE(e.tx_type,''), e.merchant_raw, t.amount
               FROM transactions t JOIN tx_enriched e USING(tx_key)"""):
        if tx_key not in gold:
            continue
        d = by_key.setdefault(key, {"types": set(), "raw": raw, "tx": []})
        d["types"].add(ttype)
        d["tx"].append((gold[tx_key], amount))
    family, first = household_names(con)
    places = known_places(con, cfg.memory_dir)
    known = known_merchants(con)
    excluded = Counter()
    ok: list[str] = []
    for key, d in sorted(by_key.items()):
        if not key:
            excluded["no_merchant_key"] += len(d["tx"])
        elif d["types"] <= type_rule_types:
            excluded["decided_by_a_type_rule"] += len(d["tx"])           # the real run never asks a model about these
        elif hold_back(key, d["raw"], d["types"], family, first, known, cfg.llm_allowlist, places=places):
            excluded["withheld_person_like"] += len(d["tx"])
        else:
            ok.append(key)
    total = len(ok)
    if sample and sample < len(ok):
        ok = sorted(random.Random(seed).sample(ok, sample))
    return {"keys": ok, "gold_tx": {k: by_key[k]["tx"] for k in ok}, "excluded": dict(excluded), "keys_all": total}


def parse_spec(spec: str, cfg) -> tuple[str, str]:
    """('claude-code' | 'anthropic-api' | 'ollama', model) of a --models item: `ollama:<model>`, or a model alias / id (haiku, sonnet, ...)
    asked of the configured [llm] backend (claude-code when that is ollama)."""
    spec = spec.strip()
    if not spec:
        raise ModelsError("empty model name")
    if spec.startswith("ollama:"):
        m = spec.split(":", 1)[1].strip()
        if not m:
            raise ModelsError("ollama: needs a model name (ollama:llama3.1)")
        return "ollama", m
    backend = cfg.llm_backend if cfg.llm_backend != "ollama" else "claude-code"
    return backend, spec


# ---------------------------------------------------------------- the plan (the exact requests, no call)

def plan(con, cfg, specs: list[str], sample: int = 0, seed: int = 7, batch: int = 40) -> dict:
    gm = gold_merchants(con, cfg, sample, seed)
    names = household_names(con)[0]
    out = {"merchants": len(gm["keys"]), "merchants_available": gm["keys_all"], "excluded": gm["excluded"], "models": [], "keys": gm["keys"],
           "gold_tx": gm["gold_tx"]}
    holdout = frozenset(gm["keys"])
    for spec in specs:
        backend, model = parse_spec(spec, cfg)
        jobs = prepare_jobs(con, gm["keys"], model, batch, None, 0, names, cfg.llm_allowlist, holdout)
        for _, j in jobs:
            j["purpose"] = "eval"
        chars = sum(len(j["static"]) + len(j["dynamic"]) for _, j in jobs)
        nbytes = sum(len((j["static"] + j["dynamic"]).encode("utf-8")) for _, j in jobs)
        tin, tout = math.ceil(chars * TOKENS_PER_CHAR), TOKENS_OUT_PER_ITEM * len(gm["keys"])
        mid = B.ALIASES.get(model, model)
        cost = 0.0 if backend == "ollama" else B.estimate_cost(mid, tin, tout)
        kind = f"llm.{backend}"
        host = egress.host_of(cfg.llm_ollama_url) if backend == "ollama" else (
            egress.host_of(cfg.llm_anthropic_base_url or B.AnthropicBackend.OFFICIAL_URL) if backend == "anthropic-api" else "api.anthropic.com (via the claude CLI)")
        ok, code, why = egress.evaluate(kind, {"host": host if backend == "ollama" else "", "purpose": "eval.models"}, cfg=cfg)
        out["models"].append({"spec": spec, "backend": backend, "model": model, "requests": len(jobs), "merchants": len(gm["keys"]),
                              "payload_bytes": nbytes, "payload_chars": chars, "est_tokens_in": tin, "est_tokens_out": tout,
                              "est_cost_usd": cost, "cost_basis": "notional (subscription)" if backend == "claude-code" else
                              "local, free" if backend == "ollama" else "API price estimate", "egress_kind": kind, "host": host,
                              "egress_allowed": ok, "egress_reason": "" if ok else why, "jobs": jobs})
    return out


def format_plan(p: dict, show_payload: bool = False) -> str:
    L = [f"{p['merchants']} gold merchant(s) to re-label"
         + (f" (of {p['merchants_available']}; --sample)" if p["merchants"] < p["merchants_available"] else "")
         + ("; left out: " + ", ".join(f"{v} tx {k.replace('_', ' ')}" for k, v in p["excluded"].items()) if p["excluded"] else "")]
    for m in p["models"]:
        cost = "unknown price" if m["est_cost_usd"] is None else f"{m['est_cost_usd']:.4f} USD ({m['cost_basis']})"
        L.append(f"\n  {m['spec']:<22} backend {m['backend']}: {m['requests']} request(s), {m['payload_bytes']} bytes of redacted payload "
                 f"(about {m['est_tokens_in']} tokens in, {m['est_tokens_out']} out), estimated cost {cost}")
        L.append(f"    destination: {m['host'] or m['egress_kind']}; egress policy: "
                 + ("allowed" if m["egress_allowed"] else f"REFUSED ({m['egress_reason']})"))
        if show_payload and m["jobs"]:
            L.append("    first request, dynamic part (exactly what is sent after the static instructions):\n" + m["jobs"][0][1]["dynamic"])
    L.append("\nThe estimate counts the whole prompt as new input every time (the API's prompt cache would make the repeated instructions "
             "cheaper). The merchants being evaluated are never shown to the model as examples or hints.")
    return "\n".join(L)


# ---------------------------------------------------------------- scoring

def score(gold_tx: dict[str, list], predictions: dict[str, str]) -> dict:
    """gold_tx: key -> [(gold category, amount)]; predictions: key -> category. Metrics over the transactions of the merchants that got a
    prediction (a merchant whose batch failed is counted in `missing_merchants`, not as a wrong answer)."""
    rows = [{"gold": g, "pred": predictions[k], "amount": a, "source": "model"} for k, txs in gold_tx.items() if k in predictions
            for g, a in txs]
    m = CE.metrics(rows, CE.load_equivalence())
    n_all = sum(len(v) for v in gold_tx.values())
    m["merchants_scored"] = sum(1 for k in gold_tx if k in predictions)
    m["missing_merchants"] = sum(1 for k in gold_tx if k not in predictions)
    m["tx_total"] = n_all
    m["tx_coverage"] = CE._ratio(m["n"], n_all)
    return m


# ---------------------------------------------------------------- the run

def tty() -> bool:
    return G.tty()


def run(con, cfg, specs: list[str], *, sample: int = 100, seed: int = 7, batch: int = 40, workers: int = 2, dry_run: bool = False,
        show_payload: bool = False, input_fn: Callable[[str], str] = input, out=print, isatty: Optional[Callable[[], bool]] = None,
        backend_factory: Optional[Callable] = None) -> dict:
    """Plan, then (unless dry_run) confirm and run. `backend_factory(backend_name, model)` is for tests (a fake backend)."""
    isatty = isatty or tty
    p = plan(con, cfg, specs, sample, seed, batch)
    out(format_plan(p, show_payload))
    if dry_run:
        out("\nDRY RUN: no model was called, nothing was written.")
        return {"status": "dry-run", "plan": {k: v for k, v in p.items() if k not in ("keys", "gold_tx")} | {
            "models": [{k: v for k, v in m.items() if k != "jobs"} for m in p["models"]]}}
    if not p["keys"]:
        raise ModelsError("the gold set has no merchant a model can be asked about: `coach eval gold bootstrap` and `coach eval gold label` first")
    refused = [m for m in p["models"] if not m["egress_allowed"]]
    if refused:
        raise ModelsError("the privacy policy refuses: " + "; ".join(f"{m['spec']} ({m['egress_reason']})" for m in refused))
    if not isatty():
        raise ModelsError("a real run needs a terminal: it costs money (or notional quota) and sends the redacted merchant descriptors to the "
                          "backend, so it cannot run from a script or an agent. Use --dry-run to see the payload and the estimate.")
    total = sum(m["est_cost_usd"] or 0 for m in p["models"])
    out(f"\nThis sends {sum(m['payload_bytes'] for m in p['models'])} bytes of redacted merchant data to "
        + ", ".join(sorted({m['host'] or m['egress_kind'] for m in p["models"]})) + f" (estimated {total:.4f} USD).")
    if input_fn(f"Type {PHRASE!r} to continue (anything else aborts): ").strip() != PHRASE:
        out("aborted: nothing was sent.")
        return {"status": "aborted"}
    names = household_names(con)[0]
    holdout = frozenset(p["keys"])
    results = []
    fp, parts = CE.fingerprint(con, cfg)
    base = baseline(con, cfg, p["gold_tx"])
    for m in p["models"]:
        backend = backend_factory(m["backend"], m["model"]) if backend_factory else B.get_backend(cfg, m["backend"])
        if hasattr(backend, "use_batch"):
            backend.use_batch = False                            # one request after the other: the latency is measured per request
        got: dict[str, dict] = {}

        def collect(keys, pairs, mdl=None, got=got):
            for k, r in pairs:
                got[k] = r
        last_id = con.execute("SELECT COALESCE(MAX(id),0) FROM llm_usage").fetchone()[0]
        t0 = time.monotonic()
        failed = []
        cost = 0.0
        try:
            _, cost = label_keys(con, p["keys"], m["model"], batch, workers, backend=backend, index=None, knn_k=0, names=names,
                                 on_batch=collect, allow=cfg.llm_allowlist, purpose="eval", exclude_examples=holdout)
        except Exception as e:                                                      # noqa: BLE001  (LabelRunError: some batches failed)
            failed = [type(e).__name__]
            cost = getattr(e, "cost", 0.0) or 0.0
            if not got and not hasattr(e, "failed"):
                raise
        con.commit()
        wall = time.monotonic() - t0
        urows = con.execute("SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), COALESCE(SUM(duration_s),0) "
                            "FROM llm_usage WHERE id>? AND purpose='eval' AND model=?", (last_id, m["model"] if m["backend"] != "anthropic-api"
                                                                                       else B.AnthropicBackend.model_id(m["model"]))).fetchone()
        sc = score(p["gold_tx"], {k: r["category"] for k, r in got.items()})
        unknown = bool(getattr(label_keys, "unknown_cost", False))
        summary = {"spec": m["spec"], "merchants": len(p["keys"]), "n": sc["n"], "accuracy_tx": sc["accuracy_tx"],
                   "accuracy_money": sc["accuracy_money"], "equiv_accuracy_tx": sc["equiv_accuracy_tx"], "method": CE.METHOD,
                   "group_accuracy_tx": sc["group_accuracy_tx"], "coverage_tx": sc["tx_coverage"],
                   "cost_usd": None if unknown else round(cost, 6), "requests": urows[0], "tokens_in": urows[1], "tokens_out": urows[2],
                   "latency_s": round(wall, 2), "avg_request_s": round(urows[3] / urows[0], 2) if urows[0] else None,
                   "baseline_accuracy_tx": base["accuracy_tx"]}
        rid = runs.record(con, "models", label=m["spec"], backend=m["backend"], model=m["model"], n=sc["n"], fingerprint=fp,
                          cost_usd=summary["cost_usd"], duration_s=round(wall, 2), summary=summary,
                          result={"summary": summary, "metrics": sc, "baseline": base, "inputs": parts, "failed": failed,
                                  "excluded": p["excluded"], "seed": seed})
        results.append({**summary, "run_id": rid, "failed": failed})
    out("\n" + format_results(results, base))
    return {"status": "done", "results": results, "baseline": base}


def baseline(con, cfg, gold_tx: dict) -> dict:
    """The production pipeline on the same transactions (what `coach eval classify` would say about them): the number to beat."""
    keys = set(gold_tx)
    golds = [g for g in G.gold_rows(con)
             if (con.execute("SELECT merchant_key FROM tx_enriched WHERE tx_key=?", (g["tx_key"],)).fetchone() or ("",))[0] in keys]
    rows, _ = CE.predict(con, cfg, golds)
    real = [r for r in rows if r["truth"] == "merchant_truth" and r["source"] != r["label_source"] and not r["no_counterfactual"]]
    m = CE.metrics(real, CE.load_equivalence())
    return {"n": m["n"], "accuracy_tx": m["accuracy_tx"], "accuracy_money": m["accuracy_money"], "equiv_accuracy_tx": m["equiv_accuracy_tx"]}


def format_results(results: list[dict], base: dict) -> str:
    L = ["model comparison on the gold set (shadow run: no label was changed)",
         f"{'model':<22}{'tx acc':>8}{'money acc':>11}{'group':>8}{'covered':>9}{'cost USD':>10}{'latency s':>11}{'req':>5}"]
    for r in results:
        cost = "unknown" if r["cost_usd"] is None else f"{r['cost_usd']:.4f}"
        L.append(f"{r['spec']:<22}{CE.pct(r['accuracy_tx']):>8}{CE.pct(r['accuracy_money']):>11}{CE.pct(r['group_accuracy_tx']):>8}"
                 f"{CE.pct(r['coverage_tx']):>9}{cost:>10}{r['latency_s']:>11}{r['requests']:>5}"
                 + ("  (some requests failed)" if r["failed"] else ""))
    L.append(f"{'classifier today':<22}{CE.pct(base['accuracy_tx']):>8}{CE.pct(base['accuracy_money']):>11}  (memory off, same transactions, on {base['n']})")
    L.append("stored in eval_runs: `coach eval runs --kind models`")
    return "\n".join(L)
