"""`coach explain` (E3-7): why does a transaction have this category? The complete decision chain, evaluated step by
step in the order of the classifier (:func:`coach.classify.rules.resolve`), then the memory annotations that are
applied on top, and where to edit each decision.

The chain is *re-evaluated* here (every step independently, so you also see the steps that were never reached) and
checked against the classifier's own answer: ``consistent`` is False if they ever disagree.
"""
from __future__ import annotations

import re
from typing import Optional

from coach.classify import rules as R
from coach.memory.store import MemoryStore, MemoryStoreError


class ExplainError(MemoryStoreError):
    pass


def find_transactions(con, ref: str, limit: int = 12) -> list[tuple]:
    """tx_key exactly, else a fragment of the tx_key / bank description / merchant key (case-insensitive)."""
    q = """SELECT t.tx_key, t.booking_date, t.amount, COALESCE(e.merchant_key,''), SUBSTR(t.description,1,60)
           FROM transactions t LEFT JOIN tx_enriched e USING(tx_key) """
    rows = con.execute(q + "WHERE t.tx_key=?", (ref,)).fetchall()
    if rows:
        return rows
    like = f"%{ref}%"
    return con.execute(q + """WHERE t.tx_key LIKE ? OR UPPER(t.description) LIKE UPPER(?) OR e.merchant_key LIKE UPPER(?)
                              ORDER BY t.booking_date DESC LIMIT ?""", (like, like, like, limit)).fetchall()


def rules_hint() -> str:
    """Where a rule is edited: the user's copy (the packaged file is a default, never edited)."""
    try:
        user = R.user_config_dir() / "rules.yaml"
    except Exception:                                               # noqa: BLE001
        return "config/rules.yaml"
    return f"{user}" + ("" if user.exists() else " (copy the packaged rules.yaml there first)")


def annotation_lines(store: MemoryStore) -> dict[str, int]:
    """annotation id -> line in categorization.yaml (1-based); empty if the file cannot be parsed."""
    try:
        doc = store.load_doc("categorization.yaml")
        return {str(it.get("id")): it.lc.line + 1 for it in (doc.get("annotations") or [])}
    except Exception:                                               # noqa: BLE001
        return {}


def explain(con, store: MemoryStore, tx_key: str, rules: Optional[dict] = None) -> dict:
    rules = R.load_rules() if rules is None else rules
    row = con.execute("""SELECT t.tx_key, t.booking_date, t.amount, t.description, t.counterparty, e.tx_type, e.op_date,
                                e.merchant_raw, e.merchant_key, e.fx_amount, e.fx_currency, a.purpose, a.bank,
                                COALESCE(a.label, a.name, a.uid)
                         FROM transactions t LEFT JOIN tx_enriched e USING(tx_key) LEFT JOIN accounts a ON a.uid=t.account_uid
                         WHERE t.tx_key=?""", (tx_key,)).fetchone()
    if not row:
        raise ExplainError(f"no transaction {tx_key!r}")
    (tx_key, bdate, amount, desc, cpty, ttype, op_date, mraw, mkey, fx, fxc, purpose, bank, acct_label) = row
    meta = con.execute("SELECT parser, counterparty, mandate_ref, creditor_id, reference FROM tx_parse_meta WHERE tx_key=?",
                       (tx_key,)).fetchone()
    parsed = {"parser": meta[0] if meta else None, "tx_type": ttype, "op_date": op_date, "merchant_raw": mraw,
              "merchant_key": mkey, "counterparty": meta[1] if meta else None, "mandate_ref": meta[2] if meta else None,
              "creditor_id": meta[3] if meta else None, "reference": meta[4] if meta else None,
              "fx_amount": fx, "fx_currency": fxc}
    tx = {"tx_key": tx_key, "date": bdate, "amount": amount, "description": desc, "bank": bank, "account": acct_label,
          "account_purpose": purpose}
    mkey = mkey or ""
    steps: list[dict] = []
    direction = "in" if amount > 0 else "out"

    def step(name, applies: bool, detail: str, category=None, edit=None, source=None):
        steps.append({"step": name, "applies": applies, "detail": detail, "category": category, "edit": edit,
                      "source": source})

    # 1. override
    o = con.execute("SELECT category, note FROM tx_overrides WHERE tx_key=?", (tx_key,)).fetchone()
    step("override", bool(o), f"per-transaction override {o[0]}" + (f" ({o[1]})" if o and o[1] else "") if o else
         "no per-transaction override", o[0] if o else None,
         "delete the row of tx_overrides for this tx_key (database)" if o else None, "override")
    # 2. split (not part of resolve(): parts replace the category afterwards)
    parts = con.execute("SELECT amount, category, note FROM tx_splits WHERE tx_key=? ORDER BY id", (tx_key,)).fetchall()
    # 3. transfer link
    lk = con.execute("SELECT out_tx_key, in_tx_key, confidence, method FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?",
                     (tx_key, tx_key)).fetchone()
    if lk:
        other = lk[1] if lk[0] == tx_key else lk[0]
        step("transfer link", True, f"leg of an internal transfer with {other} ({lk[3]}, confidence {lk[2]:.2f})",
             "transfer.internal", f"`coach transfers unlink {tx_key}`", "transfer_link")
    else:
        step("transfer link", False, "not linked to another own account")
    # 4. type rule
    by_p = (rules.get("type_rules_by_account_purpose") or {}).get(purpose or "", {})
    if ttype in by_p:
        step("type rule", True, f"transaction type {ttype!r} on an account with purpose {purpose!r}", by_p[ttype],
             f"type_rules_by_account_purpose.{purpose}.{ttype} in {rules_hint()}, or override it with `coach memory annotate`", "type_rule")
    elif ttype in rules["type_rules"]:
        step("type rule", True, f"transaction type {ttype!r}", rules["type_rules"][ttype],
             f"type_rules.{ttype} in {rules_hint()}, or override it with `coach memory annotate`", "type_rule")
    else:
        step("type rule", False, f"no rule for type {ttype!r}")
    # 5. user label
    m = con.execute("SELECT merchant_name, category, confidence, source, model, updated_at FROM merchants WHERE merchant_key=?",
                    (mkey,)).fetchone()
    if m and m[3] == "user":
        step("user merchant label", True, f"you labelled {mkey!r} as {m[1]} ({m[5]})", m[1],
             f"`coach classify correct \"{mkey}\" <category>`", "user")
    else:
        step("user merchant label", False, "no label of yours for this merchant key")
    # 6. rule
    rule_hit = None
    for r in rules["merchant_rules"]:
        if r.get("direction") and r["direction"] != direction:
            continue
        if re.search(r["match"], mkey, re.I):
            rule_hit = r
            break
    if rule_hit:
        step("rule", True, f"merchant rule /{rule_hit['match']}/" + (f" ({rule_hit['direction']} only)" if rule_hit.get("direction") else ""),
             rule_hit["category"], f"merchant_rules in {rules_hint()}, or override it with `coach memory annotate`", "rule")
    else:
        step("rule", False, "no merchant rule matches the key")
    # 7. entity
    e = con.execute("""SELECT e.name, e.category, e.category_user FROM merchant_aliases a
                       JOIN merchant_entities e ON e.id=a.entity_id WHERE a.merchant_key=?""", (mkey,)).fetchone()
    entity_applies = bool(e and e[1] and (not m or e[2]))
    if e and e[1]:
        why = "" if entity_applies else " (an LLM/kNN label of the key outranks an automatic entity default)"
        step("entity", entity_applies, f"canonical merchant {e[0]!r} has the default category {e[1]}"
             + (" set by you" if e[2] else "") + why, e[1] if entity_applies else None,
             f"`coach merchants category \"{e[0]}\" <category>`", "entity")
    else:
        step("entity", False, "the key is not grouped under a canonical merchant with a category" if not e else
             f"canonical merchant {e[0]!r} has no category")
    # 8. LLM / kNN / web label
    if m and m[3] != "user":
        conf = f"{m[2]:.2f}" if m[2] is not None else "?"
        kind = {"llm": "LLM", "llm_web": "LLM with web evidence", "knn": "similarity (kNN)"}.get(m[3], str(m[3]))
        step(f"{kind} label", True, f"{kind} label {m[1]} (confidence {conf}, model {m[4]}, {m[5]})", m[1],
             f"`coach classify correct \"{mkey}\" <category>`", m[3])
    else:
        step("LLM / kNN label", False, "no automatic label for this merchant key")
    if m and m[3] == "llm_web":
        steps[-1]["detail"] += "; the web evidence note is not stored in the database (only the label)"

    # decider = first applicable (resolve() order), else uncategorized
    decider = next((s for s in steps if s["applies"]), None)
    pre_cat, pre_src = (decider["category"], decider["source"]) if decider else ("other.uncategorized", "none")
    for s in steps:
        s["decides"] = s is decider
    # sanity: the classifier's own answer
    linked = lk is not None
    real_cat, real_src = R.resolve(con, tx_key, ttype, mkey, rules, linked=linked, purpose=purpose, amount=amount)

    # memory annotations (applied on top)
    anns = R.load_annotations(store.root)
    aliases = [r[0] for r in con.execute("SELECT old_key FROM tx_key_remap WHERE new_key=?", (tx_key,))]
    considered, winner = [], None
    lines = annotation_lines(store)
    for a in anns:
        why = R.annotation_mismatch(a, tx_key, mkey, desc, bdate, amount, real_cat, op_date, aliases)
        entry = {"id": a.get("id"), "matched": why is None, "reason": why or "all criteria match",
                 "line": lines.get(str(a.get("id")))}
        if why is None and winner is None:
            winner = a
            entry["winner"] = True
        considered.append(entry)
    final_cat, final_src = real_cat, real_src
    tags, event, edit_hint = [], None, None
    if winner:
        tags, event = list(winner.get("tags", [])), winner.get("event")
        if winner.get("category"):
            final_cat, final_src = winner["category"], "memory"
        line = next((c["line"] for c in considered if c["id"] == winner.get("id")), None)
        edit_hint = (f"memory/categorization.yaml" + (f" line {line}" if line else "") + f" (annotation {winner.get('id')!r}): "
                     f"`coach memory set {winner.get('id')} category <category>`")
    elif decider:
        edit_hint = decider["edit"]
    split = None
    if parts:
        split = [{"amount": a, "category": c, "note": n} for a, c, n in parts]
        final_src = "split"
    consistent = (pre_cat, pre_src) == (real_cat, real_src)
    attribution = None
    try:                                                      # E14-3: whose it is and why (manual > rule > account owner)
        from coach.household import attribution as attr_mod, people as people_mod
        people = people_mod.load(store)
        if people:
            attribution = attr_mod.explain(con, people, tx_key)
    except Exception:                                         # noqa: BLE001 - never blocks an explanation (migration pending, no household)
        attribution = None
    return {
        "transaction": tx, "parsed": parsed, "steps": steps,
        "split": split, "memory": {"annotations": considered, "winner": winner.get("id") if winner else None},
        "final": {"category": final_cat, "source": final_src, "tags": sorted(tags), "event": event,
                  "before_memory": {"category": real_cat, "source": real_src}},
        "edit": edit_hint if not parts else f"`coach split {tx_key} --clear` (the split decides the categories)",
        "consistent": consistent,
        "attribution": attribution,
    }


def format_explanation(x: dict) -> str:
    tx, p, f = x["transaction"], x["parsed"], x["final"]
    L = [f"{tx['tx_key']}", f"  {tx['date']}  {tx['amount']:+.2f} EUR  {tx['bank'] or '?'} / {tx['account'] or '?'}"
         + (f" (purpose {tx['account_purpose']})" if tx["account_purpose"] else ""),
         f"  bank description: {tx['description']}", "", "Parser output"]
    for k in ("parser", "tx_type", "op_date", "merchant_raw", "merchant_key", "counterparty", "mandate_ref",
              "creditor_id", "reference", "fx_amount", "fx_currency"):
        if p.get(k) not in (None, ""):
            L.append(f"  {k:<14} {p[k]}")
    L += ["", "Decision chain (first applicable step decides)"]
    for s in x["steps"]:
        mark = ">> " if s.get("decides") else ("   " if not s["applies"] else " ~ ")
        cat = f" -> {s['category']}" if s["applies"] and s["category"] else ""
        note = "" if s.get("decides") or not s["applies"] else "  (applies but outranked)"
        L.append(f"{mark}{s['step']:<22}{cat}{note}")
        L.append(f"      {s['detail']}")
    if x["split"]:
        L.append("   split: " + ", ".join(f"{s['category']} {s['amount']:+.2f}" for s in x["split"]))
    L += ["", "Memory annotations (categorization.yaml, first match wins)"]
    if not x["memory"]["annotations"]:
        L.append("   (none)")
    for a in x["memory"]["annotations"]:
        mark = ">> " if a.get("winner") else ("   " if not a["matched"] else " ~ ")
        L.append(f"{mark}{a['id']}: {'MATCHES' if a['matched'] else 'no'} - {a['reason']}"
                 + (f"  [line {a['line']}]" if a.get("line") else ""))
    at = x.get("attribution")
    if at:
        L += ["", "Person (E14-3: manual reassignment > attribution rule > owner of the account)",
              f"   belongs to: {at['person'] or 'nobody (the account has no known owner)'}  [{at['source']}] {at['reason']}"]
        for r in at["rules"]:
            L.append(f"   rule {r['id']} -> {r['member']}: " + ("MATCHES" if r["matched"] else f"no ({r['why_not']})"))
        L.append("   change it: `coach household assign TX MEMBER` / `coach household unassign TX`")
    L += ["", f"Final: {f['category']}  (source {f['source']})  tags {f['tags'] or '-'}  event {f['event'] or '-'}"]
    if f["source"] == "memory":
        L.append(f"  before memory: {f['before_memory']['category']} ({f['before_memory']['source']})")
    if x["edit"]:
        L.append(f"To change it: {x['edit']}")
    if not x["consistent"]:
        L.append("WARNING: this chain disagrees with the classifier's own answer (please report)")
    return "\n".join(L)
