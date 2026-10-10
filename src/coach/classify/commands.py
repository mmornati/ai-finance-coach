"""CLI handlers for classification (ported from classify.py)."""
from __future__ import annotations

import json
import random
import sys

from coach.analytics.report import build_report
from coach.classify import corrections, knn as knn_mod
from coach.classify import backends, rules as rules_mod
from coach.classify.backends import default_model, get_backend
from coach import egress
from coach.classify.candidates import USER_ENTITY_KEYS_SQL, enrich_candidates, household_names, known_places, search_descriptor, llm_candidates, memory_decided_keys
from coach.classify.llm import LabelRunError, abandon_unrecorded, claim_legacy_batches, collect_pending_batches, dry_run_payload, label_keys, prepare_jobs, web_label, web_request
from coach.classify.normalize import normalize_all
from coach.classify.rules import CATEGORIES, needs_llm_sql, rule_category
from coach.db import connect, now_iso
from coach.ingest.fingerprint import remap_warnings
from concurrent.futures import ThreadPoolExecutor


def cmd_normalize(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    n = normalize_all(con, cfg.memory_dir)
    print(f"normalised {n} transactions")
    for t, cnt in con.execute("SELECT tx_type, COUNT(*) FROM tx_enriched GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {t:<22}{cnt}")
    for w in remap_warnings(con, cfg.memory_dir):
        print(f"warning: {w}")
    raw = con.execute("SELECT COUNT(DISTINCT merchant_raw) FROM tx_enriched").fetchone()[0]
    keys = con.execute("SELECT COUNT(DISTINCT merchant_key) FROM tx_enriched").fetchone()[0]
    print(f"distinct raw merchant strings {raw} → normalised keys {keys}")
    return n


def _upsert_label(con, key, name, category, confidence, hint, source, model):
    con.execute("""INSERT INTO merchants VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(merchant_key) DO UPDATE SET merchant_name=excluded.merchant_name,
                   category=excluded.category, confidence=excluded.confidence,
                   recurring_hint=excluded.recurring_hint, source=excluded.source, model=excluded.model,
                   updated_at=excluded.updated_at WHERE merchants.source <> 'user'""",
                (key, name, category, confidence, hint, source, model, now_iso()))


def cmd_run(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    dry = getattr(a, "dry_run", False)
    backend = None if dry else get_backend(cfg)
    model = a.model or (cfg.llm_model if dry else default_model(cfg, backend))
    allow = cfg.llm_allowlist
    for w in rules_mod.stale_copy_warnings():
        print(f"warning: {w}")
    use_knn = cfg.knn_enabled and not getattr(a, "no_knn", False)
    thr = getattr(a, "knn_threshold", None) or cfg.knn_threshold

    def store(batch_keys, pairs, mdl=None):
        for k, r in pairs:
            _upsert_label(con, k, r["merchant"], r["category"], r["confidence"], int(r["recurring_hint"]), "llm",
                          mdl or model)

    if backend is not None and getattr(a, "claim_legacy_batches", None):
        print(f"{claim_legacy_batches(con, backend, a.claim_legacy_batches)} pending batch(es) now count as "
              f"{a.claim_legacy_batches!r}")
    if backend is not None and getattr(a, "abandon_unrecorded", False):
        try:
            gone = abandon_unrecorded(con, backend)
        except backends.BatchUnresolved as e:
            print(f"{e}")
            return 0
        for bid, why in gone:
            print(f"  abandoned batch {bid}: {why}")
        print(f"abandoned {len(gone)} batch(es); you may pay for their requests again")
    if backend is not None and hasattr(backend, "wait"):      # batches left pending by an earlier run come first
        try:
            n_old = collect_pending_batches(con, backend, {"label": store})
        except (backends.BatchPending, backends.BatchUnresolved) as e:
            print(f"{e}; nothing new is submitted meanwhile")
            return 0
        if n_old:
            print(f"collected {n_old} labels from an earlier batch")
        for keys_, why in n_old.failed:
            print(f"  not stored: {len(keys_)} merchant(s): {why}")
    memory_keys = memory_decided_keys(con, cfg)
    full_index = knn_mod.build_index(con, allow)
    index = full_index if use_knn else None
    if not dry:                    # similarity labels follow their sources (also when new ones are switched off)
        r = knn_mod.refresh_knn_labels(con, full_index, thr)
        if r["changed"] or r["dropped"]:
            print(f"similarity labels refreshed: {r['changed']} changed, {r['dropped']} dropped (asked again)")
    cands, withheld = llm_candidates(con, refresh=a.refresh, include_ruled=a.include_ruled,
                                     memory_keys=memory_keys, allow=allow, memory_dir=cfg.memory_dir)
    keys = [c["key"] for c in cands]
    if withheld:
        print(f"{len(withheld)} merchant(s) held back from the LLM because they may be people "
              "(label them with `coach classify correct`; see `coach classify review`)")
    labelled_knn = 0
    if index is not None:
        merchant_like = {c["key"] for c in cands if c["types"] <= knn_mod.MERCHANT_TYPES}
        rest = []
        for k in keys:
            hit = knn_mod.auto_label(index, k, thr) if index.items and k in merchant_like else None
            if hit and not dry:
                cat, sim, near = hit
                _upsert_label(con, k, k.title(), cat, round(sim, 3), None, "knn", f"knn:{near}"[:60])
                labelled_knn += 1
            elif hit:
                labelled_knn += 1            # dry run: would be labelled by similarity, not sent
            else:
                rest.append(k)
        keys = rest
        if labelled_knn:
            con.commit()
            print(f"{labelled_knn} merchants {'would be ' if dry else ''}labelled by similarity to labelled ones "
                  "(no LLM, source 'knn')")
    if a.limit:
        keys = keys[: a.limit]
    names = household_names(con)[0]
    places = known_places(con, cfg.memory_dir)         # the towns cut from every descriptor before it is sent
    if dry:
        jobs = prepare_jobs(con, keys, model, a.batch, index, cfg.knn_examples, names, allow, places=places)
        print(f"DRY RUN: {len(keys)} merchants in {len(jobs)} request(s); nothing is sent, nothing is written.")
        print(json.dumps(dry_run_payload(jobs), ensure_ascii=False, indent=2))
        return 0
    print(f"{len(keys)} merchants to label with {backend.name}:{model}")
    if not keys:
        return labelled_knn
    try:
        results, cost = label_keys(con, keys, model, a.batch, a.workers, backend=backend, index=index,
                                   knn_k=cfg.knn_examples, names=names, on_batch=store, allow=allow, places=places)
    except (backends.BatchPending, backends.BatchUnresolved) as e:
        print(f"{e}")
        return labelled_knn
    except LabelRunError as e:
        con.commit()
        print(f"labelled {len(e.results)}/{len(keys)} merchants before the failure; cost ${e.cost:.3f}")
        for keys_, why in e.failed:                    # every failure, at the end, where it is seen
            print(f"  FAILED {len(keys_)} merchant(s): {why}")
        raise
    con.commit()
    unknown = getattr(label_keys, "unknown_cost", False)
    print(f"labelled {len(results)}/{len(keys)} merchants; "
          + ("cost unknown for this model; " if unknown else "") + f"reported cost ${cost:.3f} "
          f"({'notional on a subscription' if backend.name == 'claude-code' else 'estimate'})")
    if results:
        from coach.quality.classify_eval import after_label_run          # E12-2: the gold set is re-scored after a labelling run
        after_label_run(con, cfg)
    return len(results) + labelled_knn


def cmd_enrich(a, cfg):
    """Web-search the costliest low-confidence / uncategorised merchants (descriptors + city only, redacted).

    E11-1 audit: this is the one path where a model searches the web with merchant descriptors, so it is OFF unless
    ``[privacy] web_enrich = true`` (and never under ``local_only`` / ``offline``), person-like merchants are never searched (only SHOPS:
    see ``enrich_candidates``), every descriptor goes through the redaction layer, and ``--dry-run`` prints the exact request."""
    dry = getattr(a, "dry_run", False)
    ok, code, why = egress.evaluate("llm.claude-code", {"web_search": True}, cfg=cfg)
    if not ok and not dry:
        try:
            egress.require("llm.claude-code", {"web_search": True, "purpose": "classify.enrich"}, cfg=cfg)     # journals the refusal
        except egress.EgressDenied:
            pass
        sys.exit(f"error: `classify enrich` is disabled: {why}")
    con = connect(cfg, insecure=a.insecure)
    keys = [r[0] for r in con.execute("""
        SELECT m.merchant_key FROM merchants m JOIN tx_enriched e USING(merchant_key)
        JOIN transactions t USING(tx_key)
        WHERE m.source='llm' AND (m.confidence < ? OR m.category='other.uncategorized')
          AND e.tx_type IN ('card','card_refund')
        GROUP BY 1 ORDER BY ABS(SUM(t.amount)) DESC LIMIT ?""", (a.max_conf, a.limit))]
    keys = [k for k in keys if not rule_category(k)]
    keys, held = enrich_candidates(con, keys, cfg.llm_allowlist, cfg.memory_dir)
    places = known_places(con, cfg.memory_dir)
    if held:
        print(f"{len(held)} merchant(s) NOT searched because they may be people (a name never goes into a web search)")
    names = household_names(con)[0]
    from coach.classify.candidates import prompt_leaks
    _static, _, _ = web_request([], names)
    leaks = prompt_leaks(_static, places, names)
    if leaks:                                   # an instruction or example that names the household's own people / towns: never sent
        sys.exit(f"error: the web-search prompt contains household names or places ({len(leaks)}): refusing to send it")
    if dry:
        items = [{"id": i, "descriptor": search_descriptor(k, places)} for i, k in enumerate(keys[:a.batch])]
        static, dynamic, _ = web_request(items, names)
        print(f"DRY RUN: {len(keys)} merchants would be web-searched in {-(-len(keys) // a.batch) if keys else 0} request(s); "
              f"enrichment is currently {'ALLOWED' if ok else 'DISABLED: ' + why}. Nothing is sent. First request:")
        print(static + dynamic)
        return
    backend = get_backend(cfg)
    if not backend.supports_web_search:
        sys.exit(f"error: `classify enrich` needs the claude-code backend (WebSearch tool); llm.backend is {backend.name!r}")
    model = a.model or default_model(cfg, backend)
    print(f"web-enriching {len(keys)} merchants with {model}")
    batches = [keys[i:i + a.batch] for i in range(0, len(keys), a.batch)]

    def work(b):
        items = [{"id": i, "descriptor": search_descriptor(k, places)} for i, k in enumerate(b)]
        return b, web_label(items, model, backend, names)

    cost, improved = 0.0, 0
    with ThreadPoolExecutor(a.workers) as ex:
        for b, (res, c) in ex.map(work, batches):
            cost += c
            for r in res:
                if not 0 <= r["id"] < len(b):
                    continue
                k = b[r["id"]]
                old = con.execute("SELECT category, confidence FROM merchants WHERE merchant_key=?", (k,)).fetchone()
                if r["confidence"] <= old[1]:
                    continue
                improved += 1
                con.execute("""UPDATE merchants SET merchant_name=?, category=?, confidence=?, recurring_hint=?,
                               source='llm_web', model=?, updated_at=? WHERE merchant_key=? AND source <> 'user'""",
                            (r["merchant"], r["category"], r["confidence"], int(r["recurring_hint"]),
                             model, now_iso(), k))
                flag = "" if old[0] == r["category"] else f"  (was {old[0]})"
                print(f"  {k:<34} → {r['category']:<30} {r['confidence']:.2f} {r['merchant']}{flag}\n"
                      f"      {r['evidence'][:110]}")
    con.commit()
    print(f"improved {improved}/{len(keys)}; reported cost ${cost:.3f}")


def review_rows(con, cfg, max_conf: float = 0.7, limit: int = 40) -> list[dict]:
    """The review queue, ordered by money at stake (sum of absolute amounts), across all banks: LLM labels below
    `max_conf`, merchants labelled 'other.uncategorized', merchants not labelled yet, and merchants held back from
    the LLM because they may be people. Merchants decided by the user, a rule or a memory annotation are left out."""
    cands, withheld = llm_candidates(con, include_ruled=False, allow=cfg.llm_allowlist, memory_dir=cfg.memory_dir)
    held = {w["key"] for w in withheld}
    in_memory = memory_decided_keys(con, cfg)
    rows = con.execute(f"""
        SELECT e.merchant_key, MAX(m.merchant_name), MAX(m.category), MAX(m.confidence), MAX(m.source), COUNT(*),
               ROUND(SUM(t.amount),2), ROUND(SUM(ABS(t.amount)),2), MAX(e.merchant_raw),
               GROUP_CONCAT(DISTINCT COALESCE(a.bank,'')), GROUP_CONCAT(DISTINCT COALESCE(a.label, a.name, a.uid)),
               GROUP_CONCAT(DISTINCT e.tx_type)
        FROM tx_enriched e JOIN transactions t USING(tx_key) LEFT JOIN merchants m ON m.merchant_key=e.merchant_key
        LEFT JOIN accounts a ON a.uid=t.account_uid
        WHERE e.merchant_key <> '' AND {needs_llm_sql()}
          AND (m.merchant_key IS NULL OR m.category='other.uncategorized'
               OR (m.source LIKE 'llm%' AND m.confidence < ?))
          AND (m.source IS NULL OR m.source <> 'user')
          AND e.merchant_key NOT IN """ + USER_ENTITY_KEYS_SQL + """
        GROUP BY 1 ORDER BY 8 DESC""", (max_conf,)).fetchall()
    out = []
    for k, name, cat, conf, src, n, total, stake, raw, banks, accts, types in rows:
        if rule_category(k) or k in in_memory:
            continue
        reason = ("held_back_person_like" if k in held else "not_labelled" if src is None else
                  "uncategorized" if cat == "other.uncategorized" else "low_confidence")
        out.append(dict(key=k, name=name, category=cat, confidence=conf, source=src, reason=reason, n=n,
                        total=total, at_stake=stake, raw_example=raw,
                        banks=sorted(b for b in set((banks or "").split(",")) if b),
                        accounts=sorted(x for x in set((accts or "").split(",")) if x),
                        types=sorted(set((types or "").split(",")))))
    return out[:limit]


def cmd_review(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    if getattr(a, "accept", None):
        queue = {r["key"]: r for r in review_rows(con, cfg, a.max_conf, 10**9)}
        bad = 0
        for k in a.accept:
            row = con.execute("SELECT category, source, model FROM merchants WHERE merchant_key=?", (k,)).fetchone()
            if row and row[1] == "knn":                          # said first, as before: it is not an LLM label
                print(f"  {k}: this is a similarity label derived from {str(row[2]).removeprefix('knn:')!r}, not an LLM label; "
                      f"check it and set it yourself with `coach classify correct`")
                bad += 1
                continue
            if k not in queue:
                why = ("already your label" if row and row[1] == "user" else
                       "decided by a rule or a memory annotation, or confident enough not to be reviewed"
                       if row else "unknown merchant key")
                print(f"  {k}: not in the review queue ({why}); nothing changed")
                bad += 1
                continue
            conf = queue[k]["confidence"]
            try:
                cat = corrections.confirm_label(con, k)         # the one implementation (the web page uses it too)
            except corrections.CorrectionError as e:
                hint = {"knn": f"this is a similarity label derived from {str(row[2]).removeprefix('knn:')!r}, not an LLM label; "
                               "check it and set it yourself with `coach classify correct`" if row else "",
                        "unlabelled": "no label to accept (not labelled yet): use `coach classify correct`",
                        "uncategorized": "the label is 'other.uncategorized', pick a category with `coach classify correct`"
                        }.get(e.code, str(e))
                print(f"  {k}: {hint}")
                bad += 1
                continue
            print(f"  {k}: confirming {cat} ({row[1]}, confidence {conf}) as your label")
        con.commit()
        if bad:
            sys.exit(1)
        return
    rows = review_rows(con, cfg, a.max_conf, a.limit)
    if getattr(a, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return rows
    unc = con.execute("""SELECT COUNT(*), ROUND(SUM(amount),0) FROM transactions t JOIN tx_enriched e USING(tx_key)
                         LEFT JOIN merchants m USING(merchant_key) WHERE m.category='other.uncategorized'""").fetchone()
    print(f"{len(rows)} merchants to review (conf < {a.max_conf}, uncategorized or not labelled); "
          f"uncategorized tx: {unc[0]} ({unc[1]} EUR)\n")
    for r in rows:
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else " -- "
        print(f"{conf}  {(r['category'] or '(none)'):<32} {(r['name'] or r['key'])[:28]:<28} n={r['n']:<3} "
              f"{r['at_stake']:>8} EUR  [{', '.join(r['banks'])[:22]}] {r['reason']}  key='{r['key']}'  "
              f"raw='{r['raw_example']}'")
    return rows


def cmd_correct(a, cfg):
    from coach.classify.corrections import CorrectionError, correct_merchant
    if a.category not in CATEGORIES:
        sys.exit(f"Unknown category {a.category}. Valid: {', '.join(CATEGORIES)}")
    con = connect(cfg, insecure=a.insecure)
    try:
        rows = correct_merchant(con, a.key, a.category, a.name)
    except CorrectionError as e:
        sys.exit(str(e))
    for k, name in rows:
        print(f"  {k} → {a.category} ({name})")


def cmd_compare(a, cfg):
    """Second opinion from another model on a sample of labelled merchants. The answers go to `merchant_eval`
    ONLY (never to the primary labels). A timed-out batch is resumed by running the same command again, and
    merchants already evaluated by this model are not asked (or paid) twice."""
    con = connect(cfg, insecure=a.insecure)
    backend = get_backend(cfg)
    model = a.model or default_model(cfg, backend)
    names = household_names(con)[0]

    def store_eval(batch_keys, pairs, mdl=None):
        for k, r in pairs:
            con.execute("INSERT OR REPLACE INTO merchant_eval VALUES (?,?,?,?,?)",
                        (k, mdl or model, r["category"], r["confidence"], now_iso()))

    if hasattr(backend, "wait"):
        try:
            n_old = collect_pending_batches(con, backend, {"compare": store_eval})
        except (backends.BatchPending, backends.BatchUnresolved) as e:
            print(f"{e}")
            return
        if n_old:
            print(f"collected {n_old} opinions from an earlier batch")
        for keys_, why in n_old.failed:
            print(f"  not stored: {len(keys_)} merchant(s): {why}")
    rows = con.execute("SELECT merchant_key, category, model FROM merchants WHERE source='llm'").fetchall()
    random.seed(a.seed)
    sample = random.sample(rows, min(a.sample, len(rows)))
    done = {r[0] for r in con.execute("SELECT merchant_key FROM merchant_eval WHERE model=?", (model,))}
    todo = [r[0] for r in sample if r[0] not in done]
    todo, held = enrich_candidates(con, todo, cfg.llm_allowlist, cfg.memory_dir)      # E11-1: a person-like key is never sent, also as a second opinion
    if held:
        print(f"{len(held)} merchant(s) not sent for a second opinion because they may be people")
    print(f"second opinion from {backend.name}:{model} on {len(sample)} merchants ({len(todo)} not asked yet)")
    cost = 0.0
    if todo:
        try:
            _, cost = label_keys(con, todo, model, a.batch, a.workers, backend=backend, names=names,
                                 on_batch=store_eval, purpose="compare", places=known_places(con, cfg.memory_dir))
        except (backends.BatchPending, backends.BatchUnresolved) as e:
            print(f"{e}")
            return
        except LabelRunError as e:
            for keys_, why in e.failed:
                print(f"  FAILED {len(keys_)} merchant(s): {why}")
            raise
    con.commit()
    second = {k: c for k, c in con.execute("SELECT merchant_key, category FROM merchant_eval WHERE model=?", (model,))}
    first = {k: c for k, c, _ in sample}
    common = [k for k, _, _ in sample if k in second]
    if not common:
        print("no opinions yet")
        return
    leaf = sum(first[k] == second[k] for k in common)
    group = sum(first[k].split(".")[0] == second[k].split(".")[0] for k in common)
    print(f"\nagreement: leaf {leaf}/{len(common)} ({leaf / len(common):.0%}), "
          f"group {group}/{len(common)} ({group / len(common):.0%}); cost ${cost:.3f}\n")
    for k in common:
        if first[k] != second[k]:
            print(f"  {k:<36} {first[k]:<30} vs {second[k]}")


def cmd_report(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    r = build_report(con, memory_dir=cfg.memory_dir)
    for w in remap_warnings(con, cfg.memory_dir):
        print(f"warning: {w}")
    cov, conf, avg = r["coverage"], r["llm_confidence"], r["averages"]
    print("coverage by source:", cov["by_source"])
    print(f"uncategorized: {cov['uncategorized']}/{cov['total']}")
    srcs = dict(con.execute("SELECT source, COUNT(*) FROM merchants GROUP BY 1"))
    total_m = sum(srcs.values())
    knn_n = srcs.get("knn", 0)
    print(f"merchant labels by source: {srcs}; similarity (kNN) hit rate {knn_n}/{total_m}"
          + (f" = {knn_n / total_m:.0%}" if total_m else ""))
    print(f"llm merchant confidence: high≥0.8={conf['high']} mid={conf['mid']} low<0.6={conf['low']}\n")

    if getattr(a, "legacy", False):
        _legacy_averages(avg)
    else:
        _coverage_averages(con, cfg)
    if con.execute("SELECT 1 FROM merchant_aliases LIMIT 1").fetchone():
        print("\ntop merchants, variants grouped by canonical entity (spending, refunds netted):")
        for name, total, n in r["by_entity"][:15]:
            print(f"  {name:<30}{total:>10.0f}  n={n}")
    print("\nrecurring hints (LLM) with ≥3 payments:")
    for name, cat, n, avg_amt in r["recurring_hints"]:
        print(f"  {name:<28}{cat:<34} n={n:<4} avg={avg_amt}")


def _legacy_averages(avg: dict) -> None:
    """The report as it was before E4 (`classify report --legacy`): a 12-month total divided by 12 whatever the
    history of the accounts carrying each category."""
    full = avg["full_months"]
    if full:
        print(f"average monthly spending over {len(full)} full months ({full[0]} → {full[-1]}): "
              f"{avg['avg_monthly']:.0f} EUR  (with one-offs it would be {avg['avg_monthly_with_one_offs']:.0f})")
    else:
        print("average monthly spending: not enough data (need at least 3 months)")
    print("excluded from averages (memory tags):")
    for t in avg["excluded"]:
        print(f"  {t['date']} {t['amount']:>10.2f}  {t['category']:<22} {t['event'] or ''}  {sorted(t['tags'])}")

    if avg["last12"]:
        last12 = avg["last12"]
        print(f"\nmonthly average by category, {last12[0]} → {last12[-1]} (one-offs excluded, refunds netted):")
        for c, per_month, total in avg["by_category"][:25]:
            print(f"  {c:<34}{per_month:>9.0f}/month  {total:>9.0f} total")


def _coverage_averages(con, cfg) -> None:
    """Coverage-aware averages (E4): each category over the months fully covered by the accounts that carry it."""
    from coach.analytics import api
    from coach.analytics.averages import category_averages
    ds = api.build_dataset(con, cfg)
    r = category_averages(ds)
    hh = r.household_months
    if hh:
        print(f"average monthly spending over {len(hh)} months fully covered by every account carrying spending "
              f"({hh[0]} -> {hh[-1]}): {r.household_monthly_avg_c / 100:.0f} EUR  "
              f"(with one-offs it would be {r.household_monthly_avg_with_one_offs_c / 100:.0f})")
    else:
        print("average monthly spending: no month is fully covered by every account that carries spending")
    print("excluded from averages (memory tags):")
    for t in r.excluded:
        print(f"  {t.date} {t.amount_c / 100:>10.2f}  {t.category:<22} {t.event or ''}  {t.tags}")
    if r.categories:
        print("\nmonthly average by category over the months covered by the accounts that carry it "
              "(one-offs excluded, refunds netted; `coach averages` for the accounts, `coach coverage` for the base):")
        for c in [c for c in r.categories if not c.net_refund][:25]:
            print(f"  {c.category:<34}{c.monthly_avg_c / 100:>9.0f}/month  {c.total_c / 100:>9.0f} total  "
                  f"over {c.n_months} months" + ("  (few months)" if c.low_confidence else ""))
        for c in [c for c in r.categories if c.net_refund]:
            print(f"  net refund: {c.category:<26}{-c.monthly_avg_c / 100:>9.0f}/month back  {-c.total_c / 100:>9.0f} total "
                  f"over {c.n_months} months (more money back than spent: not spending)")
