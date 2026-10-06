"""Taxonomy management (E2-3): list / add / rename categories.

taxonomy.yaml and rules.yaml are edited as TEXT (comments and layout survive). A rename updates every stored
reference in one transaction (merchants, per-transaction overrides, model comparisons, canonical merchants,
splits) and rewrites rules.yaml; memory/ is never touched: the annotations that reference the old id are REPORTED.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

from coach.classify import rules as rules_mod
from coach.db import connect

ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
# ids the code itself relies on (spending/income/transfer logic, fallbacks): cannot be renamed
RESERVED = {"transfer.internal", "other.uncategorized", "transfer.to_people", "transfer.from_people"}
DB_REFS = (("merchants", "category"), ("tx_overrides", "category"), ("merchant_eval", "category"),
           ("merchant_entities", "category"), ("tx_splits", "category"))


class TaxonomyError(Exception):
    pass


def _scalar(text: str) -> str:
    out = yaml.safe_dump(text, default_flow_style=True, width=10_000).strip()
    return out[:-4].rstrip() if out.endswith("\n...") else (out[:-3].rstrip() if out.endswith("...") else out)


def _guard(path: Path) -> Path:
    """The packaged defaults inside the installed package are never written, whatever calls this."""
    if rules_mod.HERE.resolve() in Path(path).resolve().parents:
        raise TaxonomyError(f"refusing to write {path}: it is part of the installed package")
    return path


def _replace(src: Path, dst: Path) -> None:
    """The ONLY way files are swapped in: both ends are checked against the installed package."""
    os.replace(_guard(src), _guard(dst))


def _write(path: Path, text: str) -> None:
    _guard(path).write_text(text)


def _atomic_write(path: Path, text: str) -> None:
    _guard(path)
    tmp = path.with_name(path.name + ".tmp")
    _write(tmp, text)
    _replace(tmp, path)


def _header_idx(lines: list[str], group: str) -> int | None:
    pat = re.compile(rf"^{re.escape(group)}:\s*(?:#.*)?$")      # a trailing comment on the header is allowed
    return next((i for i, ln in enumerate(lines) if pat.match(ln)), None)


def _insert_leaf(lines: list[str], group: str, leaf: str, desc: str) -> list[str]:
    hdr = _header_idx(lines, group)
    entry = f"  {leaf}: {_scalar(desc)}"
    if hdr is None:     # new group: before 'other:' so that it stays last
        at = _header_idx(lines, "other")
        at = len(lines) if at is None else at
        return lines[:at] + [f"{group}:", entry] + lines[at:]
    end = hdr + 1
    while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip() and end + 1 < len(lines)
                                and lines[end + 1].startswith("  ")):
        end += 1
    return lines[:end] + [entry] + lines[end:]


def _remove_leaf(lines: list[str], group: str, leaf: str) -> list[str]:
    hdr = _header_idx(lines, group)
    if hdr is None:
        raise TaxonomyError(f"group {group!r} not found in {rules_mod.taxonomy_file()}")
    i = hdr + 1
    while i < len(lines) and lines[i].startswith("  "):
        if re.match(rf"^  {re.escape(leaf)}:", lines[i]):
            del lines[i]
            break
        i += 1
    if not (hdr + 1 < len(lines) and lines[hdr + 1].startswith("  ")):   # group became empty
        del lines[hdr]
    return lines


def _validated(text: str, expected: set[str]) -> str:
    """The text we are about to write must parse and contain exactly the expected ids: no leaf may vanish and no
    group may be duplicated, whatever the layout of the file."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise TaxonomyError(f"refusing to write an unparsable taxonomy: {e}")
    ids = {f"{g}.{leaf}" for g, leaves in (data or {}).items() if isinstance(leaves, dict) for leaf in leaves}
    groups = [ln for ln in text.splitlines() if re.match(r"^[a-z_][\w]*:\s*(?:#.*)?$", ln)]
    if ids != expected or len(groups) != len(set(g.split(":")[0] for g in groups)):
        raise TaxonomyError("refusing to write: the edited taxonomy does not have the expected categories "
                            f"(missing {sorted(expected - ids)[:3]}, unexpected {sorted(ids - expected)[:3]})")
    return text


def _no_unresolved_journal() -> None:
    if journal_path().exists():
        raise TaxonomyError(f"an interrupted rename is waiting to be resolved ({journal_path()}): open its database "
                            "with any coach command (it completes or forgets the rename), then retry")


def add_leaf(cid: str, desc: str) -> None:
    _no_unresolved_journal()
    if not ID_RE.match(cid):
        raise TaxonomyError(f"{cid!r} is not a valid id: use group.leaf, lowercase letters, digits and _")
    if cid in rules_mod.CATEGORIES:
        raise TaxonomyError(f"{cid} already exists")
    if not desc.strip():
        raise TaxonomyError("give a description")
    group, leaf = cid.split(".")
    path = rules_mod.ensure_user_file("taxonomy")
    lines = path.read_text().splitlines()
    text = "\n".join(_insert_leaf(lines, group, leaf, desc)) + "\n"
    _atomic_write(path, _validated(text, set(rules_mod.CATEGORIES) | {cid}))
    rules_mod.reload_taxonomy()


def memory_references(memory_dir, cid: str) -> list[str]:
    """Where memory/ mentions `cid`: annotation ids first, then any other file line (never edited here)."""
    out: list[str] = []
    if not memory_dir or not Path(memory_dir).exists():
        return out
    for a in rules_mod.load_annotations(Path(memory_dir)):
        if a.get("category") == cid or cid in ((a.get("match") or {}).get("category_in") or []):
            out.append(f"categorization.yaml: annotation {a.get('id', '?')!r}")
    pat = re.compile(rf"(?<![\w.]){re.escape(cid)}(?![\w.])")
    for p in sorted(Path(memory_dir).rglob("*")):
        if p.is_file() and p.suffix in (".yaml", ".yml", ".md"):
            for n, ln in enumerate(p.read_text().splitlines(), 1):
                if pat.search(ln):
                    out.append(f"{p.relative_to(memory_dir)}:{n}: {ln.strip()[:100]}")
    return out


def rename_leaf(con, old: str, new: str, memory_dir=None) -> dict:
    """Rename a leaf everywhere. The database part runs in its own SAVEPOINT (a caller's open transaction is neither
    committed nor rolled back by this function; if the caller had none, the rename is committed atomically). The
    YAML files are written to temp files first and swapped in only after the database commit; if the swap fails the
    database rename is undone."""
    if old not in rules_mod.CATEGORIES:
        raise TaxonomyError(f"unknown category {old}")
    if old in RESERVED:
        raise TaxonomyError(f"{old} is used by the pipeline itself and cannot be renamed")
    if not ID_RE.match(new):
        raise TaxonomyError(f"{new!r} is not a valid id: use group.leaf, lowercase letters, digits and _")
    if new in rules_mod.CATEGORIES:
        raise TaxonomyError(f"{new} already exists (merging two categories is not supported)")
    og, ol = old.split(".")
    ng, nl = new.split(".")
    if og in ("income", "transfer", "other") and ng != og or ng in ("income", "transfer", "other") and ng != og:
        raise TaxonomyError("moving a category into or out of the income/transfer/other groups changes how it is "
                            "counted in the reports: add a new category there instead")
    _no_unresolved_journal()
    if con.in_transaction:
        raise TaxonomyError("commit or roll back the open transaction first: the taxonomy files are swapped only "
                            "after the database change is committed, which must not happen inside someone else's "
                            "transaction")
    tpath = rules_mod.ensure_user_file("taxonomy")
    rpath_cur = rules_mod.rules_file()
    pat = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w.])")
    needs_rules = bool(pat.search(rpath_cur.read_text()))
    rpath = rules_mod.ensure_user_file("rules") if needs_rules else rpath_cur   # copy rules.yaml only if it changes
    ttext, rtext = tpath.read_text(), rpath.read_text()
    desc = rules_mod.CATEGORIES[old]
    new_t = _validated("\n".join(_insert_leaf(_remove_leaf(ttext.splitlines(), og, ol), ng, nl, desc)) + "\n",
                       (set(rules_mod.CATEGORIES) - {old}) | {new})
    new_r, n_rules = pat.subn(new, rtext)
    tmp_t, tmp_r = tpath.with_name(tpath.name + ".new"), rpath.with_name(rpath.name + ".new")
    _write(tmp_t, new_t)
    if n_rules:                        # (never next to the packaged rules.yaml: it is not rewritten)
        _write(tmp_r, new_r)
    counts = {}
    import uuid
    token = uuid.uuid4().hex
    jp = journal_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    # written BEFORE the commit: after a crash the next start knows what to complete or to forget
    _write(jp, json.dumps({"token": token, "db_id": db_identity(con), "db_path": _db_file(con),
                              "old": old, "new": new, "tpath": str(tpath), "rpath": str(rpath),
                              "tmp_t": str(tmp_t), "tmp_r": str(tmp_r), "n_rules": n_rules}))
    con.execute("SAVEPOINT rename_leaf")
    try:
        for table, col in DB_REFS:
            counts[table] = con.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old)).rowcount
        con.execute("INSERT INTO taxonomy_renames VALUES (?,?,?,datetime('now'))", (token, old, new))
        con.execute("RELEASE rename_leaf")          # commits when this function opened the transaction
    except Exception:
        con.execute("ROLLBACK TO rename_leaf")
        con.execute("RELEASE rename_leaf")
        tmp_t.unlink(missing_ok=True)
        tmp_r.unlink(missing_ok=True)
        jp.unlink(missing_ok=True)
        raise
    try:
        _replace(tmp_t, tpath)
        if n_rules:
            _replace(tmp_r, rpath)
        else:
            tmp_r.unlink(missing_ok=True)
        jp.unlink(missing_ok=True)
    except Exception:
        con.execute("SAVEPOINT rename_undo")             # first the database back to the old ids...
        for table, col in DB_REFS:
            con.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (old, new))
        con.execute("RELEASE rename_undo")
        con.execute("DELETE FROM taxonomy_renames WHERE token=?", (token,))
        con.commit()
        for path, text in [(tpath, ttext)] + ([(rpath, rtext)] if n_rules else []):   # ...then the files we changed
            try:
                _write(path, text)
            except (OSError, TaxonomyError):
                pass
        jp.unlink(missing_ok=True)
        raise
    finally:
        rules_mod.reload_taxonomy()
    return {"db": counts, "rules_yaml": n_rules, "memory": memory_references(memory_dir, old)}


# ---------------------------------------------------------------- interrupted swap: journal (N7)

def journal_path() -> Path:
    env = os.environ.get("COACH_TAXONOMY_FILE")
    d = Path(env).parent if env else rules_mod.user_config_dir()
    return _guard(d / ".taxonomy-journal.json")


def _db_file(con) -> str:
    row = con.execute("PRAGMA database_list").fetchone()
    return str(row[2]) if row and row[2] else ""


def db_identity(con) -> str:
    """A random id stored in the database itself (table `meta`): a journal belongs to ONE database."""
    import uuid
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = con.execute("SELECT value FROM meta WHERE key='db_id'").fetchone()
    if row:
        return row[0]
    new = uuid.uuid4().hex
    con.execute("INSERT OR IGNORE INTO meta VALUES ('db_id', ?)", (new,))
    con.commit()
    return con.execute("SELECT value FROM meta WHERE key='db_id'").fetchone()[0]


def _existing_db_id(con) -> str | None:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='db_id'").fetchone()
    except Exception:                                       # noqa: BLE001  (no meta table: never had a journal)
        return None
    return row[0] if row else None


def _quarantine(jp: Path, why: str) -> None:
    import time
    dest = jp.with_name(jp.name + f".bad-{int(time.time())}")
    try:
        jp.rename(dest)
    except OSError:
        pass
    print(f"coach: warning: the taxonomy rename journal {jp} is unusable ({why}); moved to {dest.name}. "
          "Check config/taxonomy.yaml and your category ids by hand (`coach taxonomy list`).", file=sys.stderr)


def apply_rename_to_files(old: str, new: str, tpath: Path, rpath: Path) -> dict:
    """Re-apply a rename to the CURRENT files, idempotently: later edits (add, merge-package, other renames) are kept,
    and a rename that is already in the files is not repeated."""
    out = {"taxonomy": "missing", "rules": "unchanged"}
    if tpath.exists():
        text = tpath.read_text()
        data = yaml.safe_load(text) or {}
        ids = {f"{g}.{leaf}": d for g, ls in data.items() if isinstance(ls, dict) for leaf, d in ls.items()}
        if old in ids and new not in ids:
            og, ol = old.split(".")
            ng, nl = new.split(".")
            lines = _insert_leaf(_remove_leaf(text.splitlines(), og, ol), ng, nl, ids[old])
            _atomic_write(tpath, _validated("\n".join(lines) + "\n", (set(ids) - {old}) | {new}))
            out["taxonomy"] = "renamed"
        elif new in ids and old not in ids:
            out["taxonomy"] = "already"
        else:
            out["taxonomy"] = "conflict"
    if rpath.exists() and rpath.resolve() != (rules_mod.HERE / "rules.yaml").resolve():
        text = rpath.read_text()
        pat = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w.])")
        new_text, n = pat.subn(new, text)
        if n:
            _atomic_write(rpath, new_text)
            out["rules"] = f"{n} reference(s) renamed"
    return out


def regenerate_identity_of_file(path, key, rebind_journal: bool) -> bool:
    """Give the database file a new identity (a copy: `restore`; a newly encrypted file: `db encrypt`). With
    `rebind_journal`, a pending rename journal of the OLD identity at the same path follows the new one. Returns
    False when the file has no identity yet or cannot be opened."""
    import uuid
    from coach import db as db_mod
    try:
        con = db_mod._open(Path(path), key)
    except Exception:                                      # noqa: BLE001
        return False
    try:
        old = _existing_db_id(con)
        if old is None:
            return False
        new = uuid.uuid4().hex
        con.execute("UPDATE meta SET value=? WHERE key='db_id'", (new,))
        con.commit()
    finally:
        con.close()
    if rebind_journal:
        try:
            jp = journal_path()
            if jp.exists():
                j = json.loads(jp.read_text())
                if j.get("db_id") == old and Path(j.get("db_path", "")).resolve() == Path(path).resolve():
                    j["db_id"] = new
                    _write(jp, json.dumps(j))
        except Exception:                                  # noqa: BLE001
            pass
    return True


JOURNAL_KEYS = ("token", "db_id", "old", "new", "tpath", "rpath", "tmp_t", "tmp_r")


def recover_journal(con) -> str | None:
    """A rename that was interrupted between the database commit and the file swap. The journal is tied to the
    database it was written for (an id stored in that database): another database with the same config root leaves
    it alone. If the rename is committed in this database, it is re-applied to the CURRENT files (idempotently, so
    later edits are kept); if it never committed, the leftovers are forgotten. A damaged journal is quarantined with a
    warning; recovery never blocks a command."""
    jp = journal_path()
    if not jp.exists():
        return None
    try:
        try:
            j = json.loads(jp.read_text())
        except (OSError, ValueError) as e:
            _quarantine(jp, f"not readable: {type(e).__name__}")
            return None
        if not isinstance(j, dict) or any(not isinstance(j.get(k), str) or not j.get(k) for k in JOURNAL_KEYS):
            _quarantine(jp, "missing fields")
            return None
        mine = _existing_db_id(con)
        here = _db_file(con)
        same_file = bool(here) and bool(j.get("db_path")) and Path(here).resolve() == Path(j["db_path"]).resolve()
        if mine is None or mine != j["db_id"] or not same_file:      # a copy of the database is not the database
            print(f"coach: an unfinished taxonomy rename {j['old']} -> {j['new']} belongs to another database "
                  f"({j.get('db_path') or 'unknown'}); it is left untouched: open that database to resolve it",
                  file=sys.stderr)
            return None
        committed = con.execute("SELECT 1 FROM taxonomy_renames WHERE token=?", (j["token"],)).fetchone() is not None
        if committed:
            res = apply_rename_to_files(j["old"], j["new"], Path(j["tpath"]), Path(j["rpath"]))
            if res["taxonomy"] == "conflict":
                _quarantine(jp, f"{j['old']} and {j['new']} are both (or neither) in the taxonomy file")
                return None
            if res["taxonomy"] == "missing":                      # nothing to complete: keep the evidence
                _quarantine(jp, f"the taxonomy file {j['tpath']} no longer exists")
                return None
            msg = (f"completed the interrupted rename {j['old']} -> {j['new']}" if res["taxonomy"] == "renamed"
                   or res["rules"] != "unchanged" else None)
        else:
            msg = f"discarded the unfinished rename {j['old']} -> {j['new']} (it was never committed)"
        for k in ("tmp_t", "tmp_r"):
            Path(j[k]).unlink(missing_ok=True)
        jp.unlink(missing_ok=True)
        rules_mod.reload_taxonomy()
        if msg:
            print(f"coach: {msg}", file=sys.stderr)
        return msg or "already applied"
    except Exception as e:                                   # noqa: BLE001
        print(f"coach: warning: could not process the taxonomy rename journal ({type(e).__name__}: {str(e)[:80]})",
              file=sys.stderr)
        return None


# ---------------------------------------------------------------- merge-package (N6)

def merge_package() -> dict:
    """Bring a user copy up to date with the package WITHOUT overwriting the user's edits: leaves that are new in the
    package since the copy was made (and that the user does not have) are added; packaged type rules and merchant
    rules the user copy lacks are appended (only when their category exists). Renamed or deleted leaves are not
    resurrected (the copy records which ids the package had when it was made)."""
    _no_unresolved_journal()
    tpath, rpath = rules_mod.taxonomy_file(), rules_mod.rules_file()
    pkg_t = yaml.safe_load((rules_mod.HERE / "taxonomy.yaml").read_text())
    pkg_ids = {f"{g}.{leaf}": d for g, ls in pkg_t.items() for leaf, d in ls.items()}
    report = {"leaves": [], "type_rules": [], "merchant_rules": [], "skipped": []}
    if tpath != rules_mod.HERE / "taxonomy.yaml":
        text = tpath.read_text()
        first = [ln for ln in text.splitlines()[:3] if ln.startswith(rules_mod.IDS_HEADER)]
        base = set(first[0][len(rules_mod.IDS_HEADER):].strip().split(",")) if first else None
        have = set(rules_mod.CATEGORIES)
        if base is None:
            report["skipped"].append("taxonomy: this copy does not record which ids the package had, so only the "
                                     "categories your rules need are added")
        for cid, d in pkg_ids.items():
            if cid not in have and (cid not in base if base is not None else cid in rules_mod.unknown_references()):
                add_leaf(cid, d)
                report["leaves"].append(cid)
        # remember the package state this copy is now in sync with
        lines = tpath.read_text().splitlines()
        lines = [ln for ln in lines if not ln.startswith((rules_mod.HASH_HEADER, rules_mod.IDS_HEADER))]
        head = [f"{rules_mod.HASH_HEADER}{rules_mod._pkg_hash('taxonomy.yaml')}",
                rules_mod.IDS_HEADER + ",".join(sorted(pkg_ids))]
        _atomic_write(tpath, "\n".join(head + lines) + "\n")
        rules_mod.reload_taxonomy()
    if rpath != rules_mod.HERE / "rules.yaml":
        pkg_r = yaml.safe_load((rules_mod.HERE / "rules.yaml").read_text())
        usr = yaml.safe_load(rpath.read_text())
        text = rpath.read_text().rstrip("\n") + "\n"
        base_line = [ln for ln in text.splitlines()[:4] if ln.startswith(rules_mod.RULES_HEADER)]
        base = set(base_line[0][len(rules_mod.RULES_HEADER):].strip().split(",")) if base_line else None
        add = []
        if base is None:
            report["skipped"].append("rules: this copy does not record which rules the package had when it was made, "
                                     "so none is added (a rule you deleted or edited must not come back)")
        else:
            # only rules that are NEW in the package since the copy was made; what existed then is never touched
            for k, v in (pkg_r.get("type_rules") or {}).items():
                if f"type:{k}" in base or k in (usr.get("type_rules") or {}):
                    continue
                if v not in rules_mod.CATEGORIES:
                    report["skipped"].append(f"type rule {k}: category {v} not in your taxonomy")
                    continue
                lines = text.splitlines()
                i = next(n for n, ln in enumerate(lines) if ln.startswith("type_rules:")) + 1
                while i < len(lines) and lines[i].startswith("  "):
                    i += 1
                lines.insert(i, f"  {k}: {v}")
                text = "\n".join(lines) + "\n"
                report["type_rules"].append(k)
            for pur, d in (pkg_r.get("type_rules_by_account_purpose") or {}).items():
                for k, v in d.items():
                    if f"purpose:{pur}:{k}" in base or k in ((usr.get("type_rules_by_account_purpose") or {}).get(pur) or {}):
                        continue
                    if v not in rules_mod.CATEGORIES:
                        report["skipped"].append(f"purpose rule {pur}/{k}: category {v} not in your taxonomy")
                        continue
                    lines = text.splitlines()
                    h = next((n for n, ln in enumerate(lines) if ln.startswith("type_rules_by_account_purpose:")), None)
                    if h is None:       # no such section yet: add it just before merchant_rules
                        m_i = next(n for n, ln in enumerate(lines) if ln.startswith("merchant_rules:"))
                        lines[m_i:m_i] = ["type_rules_by_account_purpose:", f"  {pur}:", f"    {k}: {v}", ""]
                    else:
                        end = h + 1
                        while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
                            end += 1
                        while end - 1 > h and not lines[end - 1].strip():      # the section ends at its last real line
                            end -= 1
                        p_i = next((n for n in range(h + 1, end) if lines[n].strip() == f"{pur}:"), None)
                        if p_i is None:
                            lines[end:end] = [f"  {pur}:", f"    {k}: {v}"]
                        else:
                            q = p_i + 1
                            while q < end and lines[q].startswith("    "):
                                q += 1
                            lines.insert(q, f"    {k}: {v}")
                    text = "\n".join(lines) + "\n"
                    report.setdefault("purpose_rules", []).append(f"{pur}/{k}")
            have = {(m["match"], m.get("direction")) for m in usr.get("merchant_rules") or []}
            for m in pkg_r.get("merchant_rules") or []:
                if rules_mod.rule_id(m) in base or (m["match"], m.get("direction")) in have:
                    continue
                if m["category"] not in rules_mod.CATEGORIES:
                    report["skipped"].append(f"merchant rule {m['match'][:30]}: category {m['category']} not in your taxonomy")
                    continue
                add.append(m)
        if add:
            body = yaml.safe_dump(add, default_flow_style=False, sort_keys=False, width=10_000)
            text += "".join("  " + ln if ln.strip() else ln for ln in body.splitlines(True))
            report["merchant_rules"] = [m["match"] for m in add]
        lines = [ln for ln in text.splitlines() if not ln.startswith((rules_mod.HASH_HEADER, rules_mod.RULES_HEADER))]
        head = [f"{rules_mod.HASH_HEADER}{rules_mod._pkg_hash('rules.yaml')}",
                rules_mod.RULES_HEADER + ",".join(rules_mod.package_rule_ids())]
        new = "\n".join(head + lines) + "\n"
        merged = yaml.safe_load(new)
        n_pur = sum(len(v) for v in (usr.get("type_rules_by_account_purpose") or {}).values()) + len(report.get("purpose_rules", []))
        if sum(len(v) for v in (merged.get("type_rules_by_account_purpose") or {}).values()) != n_pur or \
                len(merged["merchant_rules"]) != len(usr.get("merchant_rules") or []) + len(add) or \
                set(merged["type_rules"]) != set(usr.get("type_rules") or {}) | set(report["type_rules"]):
            raise TaxonomyError("refusing to write rules.yaml: the merge did not give the expected rules")
        _atomic_write(rpath, new)
        rules_mod.reload_taxonomy()
    return report


def usage_counts(con) -> dict[str, int]:
    out: dict[str, int] = {}
    for (c,) in con.execute("SELECT category FROM merchants"):
        out[c] = out.get(c, 0) + 1
    return out


# ---------------------------------------------------------------- CLI

def cmd_taxonomy(a, cfg):
    sub = a.taxonomy_cmd
    if sub == "list":
        con = connect(cfg, insecure=a.insecure)
        used = usage_counts(con)
        for w in rules_mod.stale_copy_warnings():
            print(f"warning: {w}")
        packaged = yaml.safe_load((rules_mod.HERE / "taxonomy.yaml").read_text())
        pk = {f"{g}.{leaf}" for g, leaves in packaged.items() for leaf in leaves}
        if rules_mod.taxonomy_file() != rules_mod.HERE / "taxonomy.yaml":
            print(f"(using your copy {rules_mod.taxonomy_file()})")
            if missing := sorted(pk - set(rules_mod.CATEGORIES)):
                print(f"note: not in your copy (renamed by you, or new in a later version): {', '.join(missing)}")
        for g, leaves in rules_mod.TAXONOMY.items():
            print(f"{g}")
            for leaf, d in leaves.items():
                n = used.get(f"{g}.{leaf}", 0)
                print(f"  {g}.{leaf:<28} {d}" + (f"   [{n} merchants]" if n else ""))
        return
    try:
        if sub == "add":
            add_leaf(a.id, a.description)
            print(f"added {a.id}: {a.description}")
        elif sub == "merge-package":
            r = merge_package()
            print(f"added leaves: {', '.join(r['leaves']) or 'none'}; type rules: {', '.join(r['type_rules']) or 'none'}; "
                  f"merchant rules: {len(r['merchant_rules'])}")
            for sk in r["skipped"]:
                print(f"  skipped: {sk}")
        elif sub == "rename":
            con = connect(cfg, insecure=a.insecure)
            res = rename_leaf(con, a.old, a.new, cfg.memory_dir)
            print(f"renamed {a.old} → {a.new}")
            for t, n in res["db"].items():
                print(f"  {t:<18} {n} row(s) updated")
            print(f"  rules.yaml         {res['rules_yaml']} reference(s) updated")
            if res["memory"]:
                print(f"\nmemory/ still mentions {a.old} (not edited: update these by hand):")
                for r in res["memory"]:
                    print(f"  {r}")
            else:
                print("memory/ does not reference it.")
    except TaxonomyError as e:
        sys.exit(f"error: {e}")
