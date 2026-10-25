"""``coach eval classify`` (E12-2, method v2): how good is the categorisation, measured on the gold set. Offline and deterministic: nothing here
calls a model or the network, so it can run in the scheduler and after every change.

Two metrics, ALWAYS reported separately (never one mixed headline):

* **Classifier accuracy on merchant-level truth** (memory OFF). Only the gold rows that say "this merchant is this category" (``merchant_truth``:
  a label given by hand, a merchant label of yours that replaced a model label, a memory annotation WITHOUT a context condition). The prediction
  is what the automatic chain says without your own decision (a merchant label of yours is left out) and without memory annotations. Reported by
  transaction and by money, for the LLM alone, at leaf level, group level and "equivalent" level (``taxonomy_equivalence.yaml``: housing.mortgage ~
  debt.loan_repayment ...). For ``category_in`` annotations a separate line says how often the classifier's category is inside the allowed base set.
* **Pipeline with memory** (what you see): the final category of EVERY gold transaction, memory annotations, overrides and splits applied. It should
  be ~100 %: it is a regression test of memory and rules; the failures are listed (an annotation shadowed or broken).

Gold rows that depend on context (an annotation with category_in / weekdays / amount / date bounds / tx_keys, an override, a split) are
``context_rule``: the classifier is not expected to know them, so they never count against it.

Tautology: a classifier prediction from the very layer the gold label is (a user label against a ``user`` prediction) agrees by construction: left out
and reported apart. No counterfactual: for a merchant you labelled yourself, nothing else would have answered: left out and counted apart.

Money: "by money" weights each transaction by its absolute amount (integer cents).

Runs stored before v2 mixed both questions in one figure: they are marked ``method_v1`` (deprecated) when read.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from typing import Optional

from coach.classify import rules as R
from coach.quality import gold as G
from coach.quality import runs

SOURCES = ("rule", "type_rule", "user", "memory", "llm", "llm_web", "knn", "entity", "split", "override", "transfer_link", "none")
LLM_SOURCES = ("llm", "llm_web")
UNCATEGORIZED = "other.uncategorized"
DROP_WARN = 0.02
METHOD = "v2"                      # an accuracy that falls by more than this since the previous run is worth a warning


def cents(amount) -> int:
    return int(round(abs(float(amount or 0)) * 100))


def money(c: int) -> str:
    return f"{c / 100:.2f}"


def _ratio(a, b) -> Optional[float]:
    return round(a / b, 4) if b else None


# ---------------------------------------------------------------- the math (pure: rows in, numbers out)

def load_equivalence(cfg=None) -> frozenset:
    """Unordered pairs of equivalent categories: your copy (config/taxonomy_equivalence.yaml) if there is one, else the packaged default."""
    import yaml
    from pathlib import Path
    from coach.classify.rules import HERE, user_config_dir
    path = HERE / "taxonomy_equivalence.yaml"
    try:
        mine = user_config_dir() / "taxonomy_equivalence.yaml"
        path = mine if mine.exists() else path
    except Exception:                                                              # noqa: BLE001
        pass
    try:
        data = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return frozenset()
    return frozenset(frozenset(p) for p in data.get("equivalent", []) if isinstance(p, list) and len(p) == 2)


def metrics(rows: list[dict], equiv: frozenset = frozenset()) -> dict:
    """rows: {gold, pred, amount, source}. Accuracy by transaction and by money, group accuracy, coverage, per-category precision / recall / F1,
    the most frequent confusions."""
    n = len(rows)
    ok = [r for r in rows if r["gold"] == r["pred"]]
    eq = [r for r in rows if r["gold"] == r["pred"] or frozenset((r["gold"], r["pred"])) in equiv]
    eq_c = sum(cents(r["amount"]) for r in eq)
    tot_c = sum(cents(r["amount"]) for r in rows)
    ok_c = sum(cents(r["amount"]) for r in ok)
    covered = [r for r in rows if r["pred"] != UNCATEGORIZED]
    cov_c = sum(cents(r["amount"]) for r in covered)
    grp = sum(1 for r in rows if r["gold"].split(".")[0] == r["pred"].split(".")[0])
    cats = sorted({r["gold"] for r in rows} | {r["pred"] for r in rows})
    per = []
    for c in cats:
        tp = sum(1 for r in rows if r["gold"] == c and r["pred"] == c)
        fp = sum(1 for r in rows if r["pred"] == c and r["gold"] != c)
        fn = sum(1 for r in rows if r["gold"] == c and r["pred"] != c)
        support = tp + fn
        per.append({"category": c, "support": support, "predicted": tp + fp, "tp": tp, "fp": fp, "fn": fn,
                    "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                    "f1": _ratio(2 * tp, 2 * tp + fp + fn),
                    "money": money(sum(cents(r["amount"]) for r in rows if r["gold"] == c))})
    per.sort(key=lambda p: (-p["support"], p["category"]))
    scored = [p for p in per if p["support"] > 0]
    macro = round(sum(p["f1"] for p in scored if p["f1"] is not None) / len(scored), 4) if scored else None
    weighted = round(sum((p["f1"] or 0) * p["support"] for p in scored) / sum(p["support"] for p in scored), 4) if scored else None
    pairs: dict[tuple, list] = {}
    for r in rows:
        if r["gold"] != r["pred"]:
            e = pairs.setdefault((r["gold"], r["pred"]), [0, 0])
            e[0] += 1
            e[1] += cents(r["amount"])
    conf = [{"gold": g, "predicted": p, "n": v[0], "money": money(v[1])}
            for (g, p), v in sorted(pairs.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:10]]
    return {"n": n, "correct": len(ok), "accuracy_tx": _ratio(len(ok), n), "money": money(tot_c), "money_correct": money(ok_c),
            "accuracy_money": _ratio(ok_c, tot_c), "equiv_accuracy_tx": _ratio(len(eq), n), "equiv_accuracy_money": _ratio(eq_c, tot_c),
            "group_accuracy_tx": _ratio(grp, n), "coverage_tx": _ratio(len(covered), n),
            "coverage_money": _ratio(cov_c, tot_c), "macro_f1": macro, "weighted_f1": weighted, "categories": per, "confusions": conf}


def short(m: dict) -> dict:
    """The headline numbers of a metrics() result (no per-category table)."""
    return {k: m[k] for k in ("n", "correct", "accuracy_tx", "money", "money_correct", "accuracy_money", "equiv_accuracy_tx", "equiv_accuracy_money",
                              "group_accuracy_tx", "coverage_tx", "coverage_money", "macro_f1")}


def evaluate_rows(rows: list[dict], equiv: frozenset = frozenset(), by_kind: bool = False) -> dict:
    """The CLASSIFIER metric over rows {tx_key, gold, pred, source, amount, origin, label_source, [kind], [no_counterfactual]}. The tautological
    rows and the ones with no counterfactual are reported apart."""
    taut = [r for r in rows if r["source"] == r["label_source"]]
    nocf = [r for r in rows if r.get("no_counterfactual") and r["source"] != r["label_source"]]
    real = [r for r in rows if r["source"] != r["label_source"] and not r.get("no_counterfactual")]
    by_source = {s: short(metrics([r for r in real if r["source"] == s], equiv)) for s in SOURCES if any(r["source"] == s for r in real)}
    llm = [r for r in real if r["source"] in LLM_SOURCES]
    out = {
        "headline": metrics(real, equiv),
        "llm_only": short(metrics(llm, equiv)) if llm else None,
        "by_source": by_source,
        "by_origin": {o: short(metrics([r for r in real if r["origin"] == o], equiv)) for o in G.ORIGINS if any(r["origin"] == o for r in real)},
        "tautological": {
            "n": len(taut), "agree": sum(1 for r in taut if r["gold"] == r["pred"]),
            "disagree": sum(1 for r in taut if r["gold"] != r["pred"]),
            "by_source": dict(Counter(r["source"] for r in taut)),
            "note": "gold label and prediction come from the same layer: left out of every figure above (a disagreement means the user's "
                    "decision changed since the label was recorded)"},
        "no_counterfactual": {
            "n": len(nocf), "money": money(sum(cents(r["amount"]) for r in nocf)),
            "note": "merchants you labelled yourself, for which nothing else (no rule, no entity) would have said anything: the model's "
                    "own label was replaced by yours, so there is nothing to score; left out of every figure"}}
    if by_kind:
        out["by_kind"] = {k: short(metrics([r for r in real if r.get("kind") == k], equiv)) for k in sorted({r.get("kind") for r in real if r.get("kind")})}
    return out


# ---------------------------------------------------------------- predictions on the real data

def predict(con, cfg, golds: list[dict]) -> tuple[list[dict], int]:
    """(rows, missing): one row per gold label whose transaction still exists. Each row carries BOTH predictions: ``pred`` / ``source`` = the
    automatic chain without memory and without the user's own decision the label comes from (the classifier), ``final`` / ``final_source`` = what
    the app shows (memory, overrides, splits applied), and what the row is good for (``truth``: merchant_truth | context_rule, ``kind``)."""
    rules = R.load_rules()
    annotations = R.load_annotations(cfg.memory_dir)
    anns = G.annotation_index(cfg)
    pre: dict[frozenset, set] = {}
    for g in golds:
        ign = frozenset({"user"}) if g["origin"] == "merchant_label" else frozenset({"override"}) if g["origin"] == "override" else frozenset()
        pre.setdefault(ign, set()).add(g["tx_key"])
    got: dict[str, tuple] = {}
    for ignore, keys in pre.items():
        for r in R.resolved_rows(con, rules, include_excluded=True, use_splits=False, only_tx_keys=keys, ignore=ignore):
            got[r["tx_key"]] = (r["category"], r["source"], r["amount"])
    final: dict[str, tuple] = {}
    keys_all = {g["tx_key"] for g in golds}
    for r in R.categorised(con, rules, annotations, memory_dir=cfg.memory_dir, include_excluded=True, use_splits=False, only_tx_keys=keys_all):
        final[r["tx_key"]] = (r["category"], r["source"], r.get("annotation"))
    parts: dict[str, list] = {}
    for tx_key, amount, cat in con.execute("SELECT tx_key, amount, category FROM tx_splits ORDER BY id"):
        if tx_key in keys_all:
            parts.setdefault(tx_key, []).append((abs(amount), cat))
    rows, missing = [], 0
    for g in golds:
        p = got.get(g["tx_key"])
        if p is None:
            missing += 1
            continue
        truth, kind, ann = G.truth_of(g, anns)
        f = final.get(g["tx_key"], (UNCATEGORIZED, "none", None))
        fcat = max(parts[g["tx_key"]], key=lambda x: x[0])[1] if g["tx_key"] in parts else f[0]
        # a merchant label of the user REPLACED the model's label (the table keeps one row per merchant): without it the chain may have nothing
        # to say, which is not an error of the pipeline but the absence of a counterfactual
        nocf = g["origin"] == "merchant_label" and p[1] == "none"
        rows.append({"tx_key": g["tx_key"], "gold": g["category"], "pred": p[0], "source": p[1], "amount": p[2], "origin": g["origin"],
                     "labeled_by": g["labeled_by"], "label_source": G.label_source(g["labeled_by"], g["origin"]), "no_counterfactual": nocf,
                     "truth": truth, "kind": kind, "annotation": (ann or {}).get("id"), "category_in": list(((ann or {}).get("match") or {}).get("category_in") or []),
                     "final": fcat, "final_source": f[1]})
    return rows, missing


def evaluate_all(rows: list[dict], equiv: frozenset = frozenset()) -> dict:
    """The two metrics, apart: the classifier on merchant_truth rows (memory off) and the pipeline with memory on every row."""
    mt = [r for r in rows if r["truth"] == "merchant_truth"]
    cls = evaluate_rows(mt, equiv, by_kind=True)
    cin = [r for r in rows if r["truth"] == "context_rule" and r["category_in"]]
    agree = [r for r in cin if r["pred"] in r["category_in"]]
    base = {"n": len(cin), "agree": len(agree), "ratio": _ratio(len(agree), len(cin)),
            "money_ratio": _ratio(sum(cents(r["amount"]) for r in agree), sum(cents(r["amount"]) for r in cin)),
            "note": "category_in annotations: the classifier (memory off) is not scored against them, but its category should be one of the allowed base "
                    "categories; this is how often it is"}
    pr = [{**r, "pred": r["final"], "source": r["final_source"]} for r in rows]
    bad = [r for r in pr if r["gold"] != r["pred"]]
    kinds = sorted({r["kind"] for r in pr})
    pipe = {"headline": metrics(pr, equiv), "by_origin": {o: short(metrics([r for r in pr if r["origin"] == o], equiv)) for o in G.ORIGINS
                                                        if any(r["origin"] == o for r in pr)},
            "by_kind": {k: short(metrics([r for r in pr if r["kind"] == k], equiv)) for k in kinds},
            "failed": len(bad), "failed_money": money(sum(cents(r["amount"]) for r in bad)),
            "failures_by_kind": dict(Counter(r["kind"] for r in bad)),
            "failures": [{"tx_key": r["tx_key"], "gold": r["gold"], "final": r["pred"], "source": r["source"], "origin": r["origin"],
                          "annotation": r["annotation"]} for r in sorted(bad, key=lambda r: -cents(r["amount"]))[:25]],
            "note": "every gold transaction with memory annotations, overrides and splits applied: it should be ~100 %. A failure means a memory "
                    "annotation is shadowed (an earlier one wins) or broken, or an override / split no longer says what the label says"}
    # per origin / annotation kind, by transaction and by money, for the CLASSIFIER too (context rules are listed with the share the classifier gets)
    kind_rows = {k: {"n": sum(1 for r in rows if r["kind"] == k), "truth": sorted({r["truth"] for r in rows if r["kind"] == k})[0],
                     "money": money(sum(cents(r["amount"]) for r in rows if r["kind"] == k))} for k in kinds}
    return {"truth": {"merchant_truth": len(mt), "context_rule": len(rows) - len(mt), "kinds": kind_rows}, "classifier": cls, "base_category": base,
            "pipeline": pipe}


# ---------------------------------------------------------------- what a result depends on

def fingerprint(con, cfg) -> tuple[str, dict]:
    """A hash of everything a result depends on: taxonomy, rules, the label prompt, the model settings, the equivalence map, the memory
    annotations and the gold set. Two runs with the same hash must give the same numbers."""
    from coach.classify.llm import label_prompt

    def sha(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]
    parts = {
        "taxonomy": sha(json.dumps(R.TAXONOMY, sort_keys=True, default=str)),
        "rules": sha(json.dumps(R.load_rules(), sort_keys=True, default=str)),
        "prompt": sha(label_prompt([], [])[0]),
        "model": sha(f"{cfg.llm_backend}|{cfg.llm_model}|{cfg.llm_anthropic_model}|{cfg.llm_ollama_model}|{cfg.knn_enabled}|{cfg.knn_threshold}"),
        "equivalence": sha(json.dumps(sorted(sorted(p) for p in load_equivalence(cfg)))),
        "annotations": sha(json.dumps(R.load_annotations(cfg.memory_dir), sort_keys=True, default=str)),
        "gold": sha(json.dumps([(r["tx_key"], r["category"]) for r in G.gold_rows(con)])),
    }
    return sha(json.dumps(parts, sort_keys=True)), parts


def changed_inputs(old: Optional[dict], new: dict) -> list[str]:
    return [k for k in new if (old or {}).get(k) != new[k] and not (k not in (old or {}) and k in ("equivalence", "annotations"))] if old else []


# ---------------------------------------------------------------- the run

def run(con, cfg, *, store: bool = True, label: str = "offline") -> dict:
    t0 = time.monotonic()
    golds = G.gold_rows(con)
    equiv = load_equivalence(cfg)
    rows, missing = predict(con, cfg, golds)
    res = evaluate_all(rows, equiv)
    cls, pipe = res["classifier"], res["pipeline"]
    res["headline"] = cls["headline"]                       # the classifier's: the headline of this result (compat); the pipeline is apart
    for k in ("llm_only", "by_source", "by_origin", "tautological", "no_counterfactual"):
        res[k] = cls[k]
    fp, parts = fingerprint(con, cfg)
    prev = runs.previous(con, "classify")
    res["gold"] = {**G.counts(con), "missing_transactions": missing, "evaluated": cls["headline"]["n"], "pipeline_evaluated": pipe["headline"]["n"],
                   "merchant_truth": res["truth"]["merchant_truth"], "context_rule": res["truth"]["context_rule"],
                   "tautological": cls["tautological"]["n"], "no_counterfactual": cls["no_counterfactual"]["n"]}
    res["method"] = METHOD
    res["fingerprint"] = fp
    res["inputs"] = parts
    res["changed_since_previous"] = changed_inputs((prev or {}).get("result", {}).get("inputs"), parts) if prev else None
    res["previous"] = {"id": prev["id"], "ts": prev["ts"], **(prev["summary"] or {})} if prev else None
    h, lo, pl, bc = cls["headline"], cls["llm_only"] or {}, pipe["headline"], res["base_category"]
    summary = {"method": METHOD, "n": h["n"], "accuracy_tx": h["accuracy_tx"], "accuracy_money": h["accuracy_money"],
               "equiv_accuracy_tx": h["equiv_accuracy_tx"], "equiv_accuracy_money": h["equiv_accuracy_money"],
               "group_accuracy_tx": h["group_accuracy_tx"], "coverage_tx": h["coverage_tx"], "macro_f1": h["macro_f1"],
               "llm_accuracy_tx": lo.get("accuracy_tx"), "llm_accuracy_money": lo.get("accuracy_money"), "llm_n": lo.get("n", 0),
               "pipeline_n": pl["n"], "pipeline_accuracy_tx": pl["accuracy_tx"], "pipeline_accuracy_money": pl["accuracy_money"],
               "pipeline_failed": pipe["failed"], "base_category_n": bc["n"], "base_category_ratio": bc["ratio"],
               "gold_total": res["gold"]["total"]}
    res["summary"] = summary
    res["warnings"] = regressions(summary, prev["summary"] if prev else None)
    if store:
        res["run_id"] = runs.record(con, "classify", label=label, n=h["n"], fingerprint=fp, duration_s=round(time.monotonic() - t0, 3),
                                    summary=summary, result=res)
    return res


def regressions(now: dict, before: Optional[dict]) -> list[str]:
    """Warnings when the numbers got worse since the previous run. Only comparable with a run of the same method (v1 mixed both questions)."""
    out = []
    if not before or before.get("method") != METHOD:
        return out
    for key, name, thr in (("accuracy_tx", "classifier accuracy by transaction", DROP_WARN), ("accuracy_money", "classifier accuracy by money", DROP_WARN),
                           ("llm_accuracy_tx", "LLM accuracy by transaction", DROP_WARN),
                           ("pipeline_accuracy_tx", "pipeline-with-memory accuracy (a memory or rules regression)", 0.01)):
        a, b = now.get(key), before.get(key)
        if a is not None and b is not None and b - a > thr:
            out.append(f"{name} fell from {b:.1%} to {a:.1%} since the previous run")
    return out


# ---------------------------------------------------------------- text

def pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def format_report(res: dict, top_categories: int = 12) -> str:
    g = res["gold"]
    L = [f"Gold set: {g['total']} transactions ({', '.join(f'{k} {v}' for k, v in g['by_origin'].items()) or 'empty'}); "
         f"{g['merchant_truth']} merchant-level truth, {g['context_rule']} context rules; {g['missing_transactions']} without a transaction"]
    if not g["total"]:
        L.append("nothing to evaluate: `coach eval gold bootstrap`, then `coach eval gold label` (see docs/quality.md)")
        return "\n".join(L)
    cls, h = res["classifier"], res["classifier"]["headline"]
    L.append("\n== CLASSIFIER accuracy on merchant-level truth (memory off) ==")
    L.append(f"{h['n']} evaluated ({g['tautological']} tautological and {g['no_counterfactual']} with no counterfactual left out)")
    if h["n"]:
        L.append(f"by transaction {pct(h['accuracy_tx'])} ({h['correct']}/{h['n']}), by money {pct(h['accuracy_money'])} ({h['money_correct']}/{h['money']} EUR); "
                 f"equivalent categories {pct(h['equiv_accuracy_tx'])} / {pct(h['equiv_accuracy_money'])}; group level {pct(h['group_accuracy_tx'])}; "
                 f"coverage {pct(h['coverage_tx'])}; macro F1 {pct(h['macro_f1'])}")
        lo = cls["llm_only"]
        L.append("LLM only (llm + llm_web): " + (f"{pct(lo['accuracy_tx'])} by transaction, {pct(lo['accuracy_money'])} by money, equivalent "
                                                 f"{pct(lo['equiv_accuracy_tx'])}, on {lo['n']} transactions" if lo else "no gold transaction with an LLM prediction yet"))
        L.append("by prediction source")
        for s, m in cls["by_source"].items():
            L.append(f"  {s:<14} n={m['n']:<5} tx {pct(m['accuracy_tx']):>7}  money {pct(m['accuracy_money']):>7}  coverage {pct(m['coverage_tx']):>7}")
        L.append("by gold origin / annotation kind")
        for o, m in {**cls["by_origin"], **cls.get("by_kind", {})}.items():
            L.append(f"  {o:<28} n={m['n']:<5} tx {pct(m['accuracy_tx']):>7}  money {pct(m['accuracy_money']):>7}")
        L.append(f"per category (top {top_categories} by support)   precision  recall  F1")
        for c in h["categories"][:top_categories]:
            L.append(f"  {c['category']:<32} n={c['support']:<4} {pct(c['precision']):>8} {pct(c['recall']):>8} {pct(c['f1']):>7}")
        if h["confusions"]:
            L.append("most frequent confusions (gold -> predicted)")
            for c in h["confusions"][:8]:
                L.append(f"  {c['n']:>3}x  {c['gold']} -> {c['predicted']}  ({c['money']} EUR)")
    bc = res["base_category"]
    if bc["n"]:
        L.append(f"base category agreement (category_in annotations, classifier memory off): {bc['agree']}/{bc['n']} = {pct(bc['ratio'])} "
                 f"(by money {pct(bc['money_ratio'])}); these rows are not scored as classifier errors")
    p, ph = res["pipeline"], res["pipeline"]["headline"]
    L.append("\n== PIPELINE with memory (what you see) ==")
    L.append(f"{ph['n']} gold transactions: {pct(ph['accuracy_tx'])} by transaction, {pct(ph['accuracy_money'])} by money; {p['failed']} failed ({p['failed_money']} EUR)"
             + (f", by kind {p['failures_by_kind']}" if p["failed"] else ""))
    for f in p["failures"][:8]:
        L.append(f"  FAIL {f['tx_key']}: gold {f['gold']}, shown {f['final']} (source {f['source']}, {f['origin']}"
                 + (f", annotation {f['annotation']}" if f["annotation"] else "") + ")")
    nc, t = cls["no_counterfactual"], cls["tautological"]
    if nc["n"]:
        L.append(f"\nno counterfactual (left out of the classifier): {nc['n']} transactions ({nc['money']} EUR) of merchants you labelled yourself")
    if t["n"]:
        L.append(f"tautological (left out of the classifier): {t['n']} rows, {t['agree']} agree, {t['disagree']} disagree (by source {t['by_source']})")
    if res.get("changed_since_previous"):
        L.append(f"inputs changed since run #{res['previous']['id']}: {', '.join(res['changed_since_previous'])}")
    for w in res.get("warnings", []):
        L.append(f"WARNING {w}")
    if res.get("run_id"):
        L.append(f"\nstored as eval run #{res['run_id']} (method {res['method']}; `coach eval runs`)")
    return "\n".join(L)


def after_label_run(con, cfg, out=print) -> None:
    """E12-2 "run on every model / prompt change": a `classify run` that labelled something re-scores the gold set (offline, no model) and
    prints one line. Warn-only: a failure here never fails the run."""
    try:
        if not G.counts(con)["total"]:
            return
        res = run(con, cfg, label="classify-run")
        sm = res["summary"]
        out(f"gold-set check: classifier {pct(sm['accuracy_tx'])} by transaction, {pct(sm['accuracy_money'])} by money on {sm['n']} merchant-level truth row(s); "
            f"pipeline with memory {pct(sm['pipeline_accuracy_tx'])} (eval run #{res['run_id']}"
            + (f"; inputs changed since the last run: {', '.join(res['changed_since_previous'])}" if res.get("changed_since_previous") else "") + ")")
        for w in res["warnings"]:
            out(f"WARNING {w}")
    except Exception:                                                              # noqa: BLE001
        return
