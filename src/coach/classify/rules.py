"""Category resolution: override -> internal-transfer link -> type rule -> user memory -> regex rule
-> LLM label -> other.

A transaction that is a leg of a matched internal transfer (``transfer_links``, E1-12) is ``transfer.internal``
whatever its descriptor says: it ranks below a per-transaction user override (the user always wins) and above
every type/merchant rule. Memory annotations are still applied on top of the result.

Memory annotations (``memory/categorization.yaml``) are applied on top of the pipeline result.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
NO_AVERAGE_TAGS = {"one_off", "exclude_from_averages"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class UnsafeConfigPath(RuntimeError):
    """A taxonomy / rules / config path that points into the installed package: those files are defaults, never an
    editable location (an edit would modify the shipped files)."""


def inside_package(path) -> bool:
    p, h = Path(path).resolve(), HERE.resolve()
    return p == h or h in p.parents


def _safe(path: Path, what: str) -> Path:
    if inside_package(path):
        raise UnsafeConfigPath(f"{what} {path} is inside the installed package ({HERE}): point it somewhere else")
    return path


_CONFIG_ROOT: list = [None]


def set_config_root(root) -> None:
    """The project root the user's copies are looked up in (the directory of the --config file), set once by the
    CLI. Without it the root is found from $COACH_HOME / the working directory."""
    _CONFIG_ROOT[0] = Path(root) if root else None
    if "TAXONOMY" in globals():
        reload_taxonomy()


def user_config_dir() -> Path:
    """Where the user's editable copies of taxonomy.yaml / rules.yaml live (`config/` of the project, or
    $COACH_CONFIG_DIR). The packaged files are the defaults and are never edited: a reinstall must not lose renamed
    ids that the database already uses."""
    if env := os.environ.get("COACH_CONFIG_DIR"):
        return _safe(Path(env), "COACH_CONFIG_DIR")
    if _CONFIG_ROOT[0] is not None:
        return _safe(_CONFIG_ROOT[0] / "config", "the config directory")
    from coach.config import find_root
    return _safe(find_root() / "config", "the config directory")


def taxonomy_file() -> Path:
    """$COACH_TAXONOMY_FILE, else the user's copy if it exists, else the packaged default."""
    if env := os.environ.get("COACH_TAXONOMY_FILE"):
        return _safe(Path(env), "COACH_TAXONOMY_FILE")
    u = user_config_dir() / "taxonomy.yaml"
    return u if u.exists() else HERE / "taxonomy.yaml"


def rules_file() -> Path:
    if env := os.environ.get("COACH_RULES_FILE"):
        return _safe(Path(env), "COACH_RULES_FILE")
    u = user_config_dir() / "rules.yaml"
    return u if u.exists() else HERE / "rules.yaml"


HASH_HEADER = "# coach-package-sha256: "
IDS_HEADER = "# coach-package-ids: "
RULES_HEADER = "# coach-package-rules: "


def _pkg_hash(name: str) -> str:
    import hashlib
    return hashlib.sha256((HERE / name).read_bytes()).hexdigest()[:16]


def rule_id(m: dict) -> str:
    import hashlib
    return "rule:" + hashlib.sha1(f"{m['match']}|{m.get('direction') or ''}".encode()).hexdigest()[:10]


def package_rule_ids() -> list[str]:
    """Identity of every packaged rule (type rules by key, merchant rules by a hash of match + direction): recorded
    in a user copy so that `merge-package` knows which rules are new in the package since the copy was made."""
    r = yaml.safe_load((HERE / "rules.yaml").read_text())
    purposes = [f"purpose:{p}:{k}" for p, d in (r.get("type_rules_by_account_purpose") or {}).items() for k in d]
    return [f"type:{k}" for k in (r.get("type_rules") or {})] + purposes + [rule_id(m) for m in r.get("merchant_rules") or []]


def ensure_user_file(which: str) -> Path:
    """The file an edit goes to: the user's copy of taxonomy.yaml or rules.yaml (`which` = 'taxonomy' | 'rules'),
    copied from the packaged default on first use ONLY for the file being edited, so that fixes shipped for the
    other one keep arriving. The copy records the hash of the package file it came from."""
    name, env = (("taxonomy.yaml", "COACH_TAXONOMY_FILE") if which == "taxonomy" else ("rules.yaml", "COACH_RULES_FILE"))
    if os.environ.get(env):
        return _safe(Path(os.environ[env]), env)
    dest = user_config_dir() / name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        ids = ""
        if name == "rules.yaml":         # which rules the package had when the copy was made (for merge-package)
            ids = RULES_HEADER + ",".join(package_rule_ids()) + "\n"
        if name == "taxonomy.yaml":      # which ids the package had when the copy was made (for merge-package)
            ids = IDS_HEADER + ",".join(sorted(f"{g}.{leaf}" for g, ls in yaml.safe_load(
                (HERE / name).read_text()).items() for leaf in ls)) + "\n"
        dest.write_text(f"{HASH_HEADER}{_pkg_hash(name)}\n{ids}" + (HERE / name).read_text())
        reload_taxonomy()
    return dest


def unknown_references() -> list[str]:
    """Categories named by the EFFECTIVE rules (type rules, per-purpose rules, merchant rules) that the effective
    taxonomy does not have: the sign of a user copy that is behind the package (or of a half-done rename)."""
    r = load_rules()
    refs = set((r.get("type_rules") or {}).values())
    for d in (r.get("type_rules_by_account_purpose") or {}).values():
        refs |= set(d.values())
    refs |= {m["category"] for m in r.get("merchant_rules") or []}
    return sorted(c for c in refs if c not in CATEGORIES)


def stale_copy_warnings() -> list[str]:
    """User copies made from an older package version: shipped changes (new categories, rule fixes) are not in them."""
    out = []
    for name, env in (("taxonomy.yaml", "COACH_TAXONOMY_FILE"), ("rules.yaml", "COACH_RULES_FILE")):
        p = user_config_dir() / name
        if os.environ.get(env) or not p.exists():
            continue
        first = p.read_text().split("\n", 1)[0]
        base = first[len(HASH_HEADER):].strip() if first.startswith(HASH_HEADER) else None
        if base != _pkg_hash(name):
            out.append(f"your {p} was made from "
                       + ("an unknown version" if base is None else "an older version")
                       + f" of the packaged {name}: `coach taxonomy merge-package` adds what is new without touching "
                       f"your edits")
    if missing := unknown_references():
        out.append("the rules refer to categories your taxonomy does not have (" + ", ".join(missing[:5])
                   + "): `coach taxonomy merge-package`")
    return out


def check_paths() -> None:
    """Raise UnsafeConfigPath if any configured taxonomy / rules / config location is inside the package."""
    user_config_dir()
    taxonomy_file()
    rules_file()


def _taxonomy_text() -> str:
    try:
        return taxonomy_file().read_text()
    except UnsafeConfigPath:                   # reported by check_paths() at start; keep reading the defaults
        return (HERE / "taxonomy.yaml").read_text()


def _rules_text() -> str:
    try:
        return rules_file().read_text()
    except UnsafeConfigPath:
        return (HERE / "rules.yaml").read_text()


def load_taxonomy() -> dict:
    return yaml.safe_load(_taxonomy_text())


TAXONOMY = load_taxonomy()
CATEGORIES = {f"{g}.{leaf}": desc for g, leaves in TAXONOMY.items() for leaf, desc in leaves.items()}
_RULES_CACHE: dict = {}


def reload_taxonomy() -> None:
    """Re-read taxonomy.yaml / rules.yaml in place (the CATEGORIES / TAXONOMY dicts other modules imported stay
    valid)."""
    TAXONOMY.clear()
    TAXONOMY.update(load_taxonomy())
    CATEGORIES.clear()
    CATEGORIES.update({f"{g}.{leaf}": d for g, leaves in TAXONOMY.items() for leaf, d in leaves.items()})
    _RULES_CACHE.clear()


def load_rules() -> dict:
    if not _RULES_CACHE:
        _RULES_CACHE.update(yaml.safe_load(_rules_text()))
    return _RULES_CACHE


def load_annotations(memory_dir: Path) -> list[dict]:
    """Memory annotations as the classifier uses them: the store's validated, strictly typed annotations (the same
    reader `coach memory check` uses). Never raises: an invalid or unreadable file yields no annotations and a
    loud warning (see :mod:`coach.memory.loader`)."""
    from coach.memory.loader import load_annotations_safe
    return load_annotations_safe(memory_dir)


def needs_llm_sql(rules: dict | None = None) -> str:
    rules = load_rules() if rules is None else rules
    types = ",".join(f"'{t}'" for t in rules["type_rules"])
    return f"tx_type NOT IN ({types})"


def rule_category(key: str, rules: dict | None = None, direction: str | None = None) -> str | None:
    """Category of the first merchant rule matching `key`. A rule with ``direction: in|out`` only applies when the
    caller knows the direction of the money (resolve() does; key listings do not)."""
    rules = load_rules() if rules is None else rules
    for r in rules["merchant_rules"]:
        if r.get("direction") and r["direction"] != direction:
            continue
        if re.search(r["match"], key or "", re.I):
            return r["category"]
    return None


def _annotation_check(a, tx_key, mkey, desc, bdate, amount, category, op_date, aliases, msg: bool):
    """The first failing criterion of annotation `a`, or None when it matches: the English sentence, or (``msg``) the pair
    (sentence, the web's message ``{code, params, text}``). The classifier's hot path asks for the sentence only (no params built)."""
    def out(code, text, **params):
        if not msg:
            return text
        from coach.i18n_msg import MessageError, server_msg
        try:
            return text, server_msg(code, text, **params)
        except MessageError:                          # an odd value in a hand-written file: no message, the web shows the English
            return text, None

    m = a.get("match", {})
    if "category_in" in m and category not in m["category_in"]:
        return out("annotation.categoryIn", f"category_in {m['category_in']} does not include the pre-memory category {category!r}",
                   categories=", ".join(map(str, m["category_in"])), pre_category=category)
    if "weekdays" in m:
        day = WEEKDAYS[datetime.fromisoformat(op_date or bdate).weekday()]
        if day not in m["weekdays"]:
            return out("annotation.weekdays", f"weekdays {m['weekdays']} does not include {day} ({op_date or bdate})",
                       weekdays=", ".join(map(str, m["weekdays"])), weekday=day, payment_date=str(op_date or bdate)[:10])
    if "tx_keys" in m and tx_key not in m["tx_keys"] and not any(k in m["tx_keys"] for k in aliases):
        return out("annotation.txKeys", "tx_keys does not list this transaction")
    if "merchant_key" in m and not re.search(m["merchant_key"], mkey or "", re.I):
        return out("annotation.merchantKey", f"merchant_key /{m['merchant_key']}/ does not match {mkey!r}",
                   pattern=str(m["merchant_key"]), merchant_key=mkey or "")
    if "description" in m and not re.search(m["description"], desc or "", re.I):
        return out("annotation.description", f"description /{m['description']}/ does not match the bank description",
                   pattern=str(m["description"]))
    if "date_from" in m and bdate < str(m["date_from"]):
        return out("annotation.dateFrom", f"date {bdate} is before date_from {m['date_from']}",
                   tx_date=str(bdate)[:10], from_date=str(m["date_from"])[:10])
    if "date_to" in m and bdate > str(m["date_to"]):
        return out("annotation.dateTo", f"date {bdate} is after date_to {m['date_to']}",
                   tx_date=str(bdate)[:10], to_date=str(m["date_to"])[:10])
    if "amount_min" in m and amount < m["amount_min"]:
        return out("annotation.amountMin", f"amount {amount} is below amount_min {m['amount_min']}",
                   tx_amount=f"{amount:.2f}", min_amount=f"{float(m['amount_min']):.2f}")
    if "amount_max" in m and amount > m["amount_max"]:
        return out("annotation.amountMax", f"amount {amount} is above amount_max {m['amount_max']}",
                   tx_amount=f"{amount:.2f}", max_amount=f"{float(m['amount_max']):.2f}")
    return None


def annotation_mismatch(a, tx_key, mkey, desc, bdate, amount, category=None, op_date=None, aliases=()) -> str | None:
    """Why annotation `a` does NOT apply to this transaction (first failing criterion, human readable), or None
    when it matches. `category` is the category the pipeline would assign without memory (for `category_in`);
    weekdays are checked on the real payment date (card op date) when known. `aliases`: older tx_keys of this
    transaction (re-keyed rows), so annotations written against them keep matching."""
    return _annotation_check(a, tx_key, mkey, desc, bdate, amount, category, op_date, aliases, False)


def annotation_mismatch_msg(a, tx_key, mkey, desc, bdate, amount, category=None, op_date=None,
                            aliases=()) -> tuple[str | None, dict | None]:
    """(:func:`annotation_mismatch`, the same as the message the web translates: ``{code, params, text}``, :mod:`coach.i18n_msg`).
    A match is ``(None, annotation.allMatch)``; a value the message cannot carry gives no message (the web shows the English).
    The params carry the user's own patterns and the merchant key as they are (never translated)."""
    r = _annotation_check(a, tx_key, mkey, desc, bdate, amount, category, op_date, aliases, True)
    if r is None:
        from coach.i18n_msg import server_msg
        return None, server_msg("annotation.allMatch", "all criteria match")
    return r


def annotation_for(annotations, tx_key, mkey, desc, bdate, amount, category=None, op_date=None,
                   aliases=()) -> dict | None:
    """First memory annotation matching this transaction (see :func:`annotation_mismatch`)."""
    for a in annotations:
        if annotation_mismatch(a, tx_key, mkey, desc, bdate, amount, category, op_date, aliases) is None:
            return a
    return None


def resolve(con, tx_key, tx_type, mkey, rules: dict | None = None, linked: bool | None = None,
            purpose: str | None = None, amount: float | None = None, ignore: frozenset = frozenset()) -> tuple[str, str]:
    """override -> internal-transfer link -> type rule (account purpose first) -> user merchant label -> regex rule
    (direction-aware when `amount` is known) -> canonical-entity default -> LLM/kNN merchant label -> none.

    `ignore` (E12-2, the gold-set evaluation): layers to leave out, {"override", "user"}: what the automatic chain would have
    said without the user's own decision. Never used by the pipeline itself."""
    rules = load_rules() if rules is None else rules
    o = None if "override" in ignore else con.execute("SELECT category FROM tx_overrides WHERE tx_key=?", (tx_key,)).fetchone()
    if o:
        return o[0], "override"
    if linked is None:
        linked = con.execute("SELECT 1 FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?",
                             (tx_key, tx_key)).fetchone() is not None
    if linked:
        return "transfer.internal", "transfer_link"
    by_purpose = (rules.get("type_rules_by_account_purpose") or {}).get(purpose or "", {})
    if tx_type in by_purpose:
        return by_purpose[tx_type], "type_rule"
    if tx_type in rules["type_rules"]:
        return rules["type_rules"][tx_type], "type_rule"
    m = con.execute("SELECT category, source FROM merchants WHERE merchant_key=?", (mkey,)).fetchone()
    if m and m[1] == "user":
        if "user" not in ignore:
            return m[0], "user"
        m = None                                            # the user's label is left out: as if the merchant had none
    direction = None if amount is None else ("in" if amount > 0 else "out")
    if c := rule_category(mkey, rules, direction):
        return c, "rule"
    e = con.execute("""SELECT e.category, e.category_user FROM merchant_aliases a
                       JOIN merchant_entities e ON e.id=a.entity_id WHERE a.merchant_key=?""", (mkey,)).fetchone()
    if e and e[0] and (not m or e[1]):
        # entity category: a default for variants without a label; when the USER set it, it also beats LLM / kNN
        # labels of its keys (but never a user label or a rule, handled above)
        return e[0], "entity"
    if m:
        return m[0], m[1]
    return "other.uncategorized", "none"


def resolved_rows(con, rules: dict | None = None, include_excluded: bool = False, use_splits: bool = True,
                  only_tx_keys=None, only_merchant_keys=None, ignore: frozenset = frozenset()):
    """Yield one dict per transaction with its category BEFORE memory annotations (``category``/``source``), plus
    what the annotation matcher needs (``op_date``, ``aliases``). Transactions of accounts flagged ``exclude`` or
    ``needs_review`` are left out unless `include_excluded`. Split parts are NOT expanded here (``splits`` key).
    `only_tx_keys` / `only_merchant_keys` restrict the work to those rows (previews of a single change)."""
    rules = load_rules() if rules is None else rules
    rows = con.execute("""SELECT t.tx_key, t.booking_date, t.amount, e.tx_type, e.merchant_key,
                                 COALESCE(m.merchant_name, e.merchant_raw), t.description, e.op_date,
                                 t.account_uid, a.purpose, a.bank
                          FROM transactions t JOIN tx_enriched e USING(tx_key)
                          LEFT JOIN merchants m USING(merchant_key)
                          LEFT JOIN accounts a ON a.uid=t.account_uid""").fetchall()
    excluded = set() if include_excluded else {r[0] for r in con.execute("SELECT uid FROM accounts WHERE exclude=1 OR needs_review=1")}
    linked = {k for r in con.execute("SELECT out_tx_key, in_tx_key FROM transfer_links") for k in r}
    aliases: dict[str, list[str]] = {}
    for old, new in con.execute("SELECT old_key, new_key FROM tx_key_remap"):
        aliases.setdefault(new, []).append(old)
    entity = {k: n for k, n in con.execute("""SELECT a.merchant_key, e.name FROM merchant_aliases a
                                              JOIN merchant_entities e ON e.id=a.entity_id""")}
    splits: dict[str, list] = {}
    if use_splits:
        for k, amt, cat, note in con.execute("SELECT tx_key, amount, category, note FROM tx_splits ORDER BY id"):
            splits.setdefault(k, []).append((amt, cat, note))
    for tx_key, bdate, amount, ttype, mkey, mname, desc, op_date, account_uid, purpose, bank in rows:
        if account_uid in excluded or (only_tx_keys is not None and tx_key not in only_tx_keys) \
                or (only_merchant_keys is not None and mkey not in only_merchant_keys):
            continue
        cat, src = resolve(con, tx_key, ttype, mkey, rules, linked=tx_key in linked, purpose=purpose, amount=amount,
                           ignore=ignore)
        yield dict(tx_key=tx_key, date=bdate, amount=amount, type=ttype, key=mkey, merchant=mname,
                   entity=entity.get(mkey) or mname, category=cat, source=src, desc=desc, op_date=op_date,
                   aliases=tuple(aliases.get(tx_key, ())), account=account_uid, bank=bank, purpose=purpose,
                   splits=splits.get(tx_key))


def mortgage_matchers(memory_dir) -> list:
    """Compiled `payment_match` patterns of the liabilities of kind mortgage (E12): a loan instalment that matches one, on an account that is not
    a rental one, is the household's mortgage and not an unknown loan. Never raises: no memory, no liability or a bad pattern gives nothing."""
    if not memory_dir:
        return []
    try:
        from coach.memory.store import MemoryStore
        out = []
        for _rel, lb in MemoryStore(Path(memory_dir), history=False).liabilities():
            if getattr(lb, "kind", None) == "mortgage" and getattr(lb, "payment_match", None):
                try:
                    out.append(re.compile(lb.payment_match, re.I))
                except re.error:
                    continue
        return out
    except Exception:                                                  # noqa: BLE001
        return []


def categorised(con, rules: dict | None = None, annotations: list[dict] | None = None,
                memory_dir: Path | None = None, include_excluded: bool = False, use_splits: bool = True,
                only_tx_keys=None, only_merchant_keys=None):
    """Yield one dict per transaction with its final category/source/tags (one per part for a split transaction).
    Transactions of accounts flagged ``exclude`` (coach accounts set --exclude) or ``needs_review`` (ambiguous
    after a reconnect) are left out unless `include_excluded`."""
    rules = load_rules() if rules is None else rules
    if annotations is None:
        if memory_dir is None:
            from coach.config import load_config
            memory_dir = load_config().memory_dir
        annotations = load_annotations(memory_dir)
    mortgages = mortgage_matchers(memory_dir)
    for r in resolved_rows(con, rules, include_excluded, use_splits, only_tx_keys, only_merchant_keys):
        cat, src = r["category"], r["source"]
        if mortgages and r["type"] == "loan_payment" and src == "type_rule" and cat == "debt.loan_repayment" and r["purpose"] != "rental" \
                and any(rx.search(r["key"] or "") or rx.search(r["desc"] or "") for rx in mortgages):
            cat = "housing.mortgage"                       # the liability file says this payment is the mortgage
        pre_cat, pre_src = cat, src                       # before the memory annotations (for explanations and previews)
        ann = annotation_for(annotations, r["tx_key"], r["key"], r["desc"], r["date"], r["amount"], cat,
                             r["op_date"], aliases=r["aliases"])
        if ann and ann.get("category"):
            cat, src = ann["category"], "memory"
        base = dict(tx_key=r["tx_key"], date=r["date"], amount=r["amount"], type=r["type"], key=r["key"],
                    merchant=r["merchant"], entity=r["entity"], category=cat, source=src, desc=r["desc"],
                    pre_category=pre_cat, pre_source=pre_src, annotation=ann.get("id") if ann else None,
                    tags=set(ann.get("tags", [])) if ann else set(),
                    event=ann.get("event") if ann else None, account=r["account"], bank=r["bank"])
        parts = r["splits"]
        if parts:
            for i, (amt, scat, note) in enumerate(parts):
                yield {**base, "amount": amt, "category": scat, "source": "split", "split_index": i,
                       "split_of": r["amount"], "note": note, "tags": set(base["tags"])}
        else:
            yield base
