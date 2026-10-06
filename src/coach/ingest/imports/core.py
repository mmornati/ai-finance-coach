"""Import a statement file into an account with the same dedup guarantees as the API sync.

Dedup = multiset on the content fingerprint (booking date, amount, normalised description) per account:
for each fingerprint the file holds n rows, the account already holds m (from the API, an earlier import,
or an overlapping file): n - m are inserted, so re-importing a file adds 0, overlapping files add only the
difference, and genuine same-day same-amount same-label purchases (two coffees) are kept.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from coach.db import now_iso
from coach.ingest.accounts import AccountError, ensure_hashes, ident_hash, normalize_iban, resolve_account
from coach.ingest.fingerprint import content_key, import_tx_key
from coach.ingest.imports import parsers
from coach.ingest.imports.parsers import ParseError, Parsed, Row
from coach.ingest.imports.profiles import Profile, ProfileError


class ImportFailed(Exception):
    pass


@dataclass
class ImportReport:
    file: str
    format: str
    account_uid: str
    account_created: bool
    dry_run: bool
    rows_total: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0
    rows_skipped: int = 0
    date_min: str | None = None
    date_max: str | None = None
    already_imported: str | None = None
    new_rows: list[Row] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    import_id: int | None = None


def slug(label: str) -> str:
    t = unicodedata.normalize("NFKD", label)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if not t:
        raise ImportFailed(f"cannot derive an account id from label {label!r}")
    return t[:48]


def ensure_account(con, spec: str, bank: str | None = None, currency: str | None = None,
                   iban: str | None = None, create: bool = True, force: bool = False) -> tuple[str, bool]:
    """(stable uid, created). `new:Label` makes (or reuses) the manual account ``imp-<slug>``."""
    if spec.startswith("new:"):
        ensure_hashes(con)               # accounts stored before identification_hash existed have NULL there
        label = spec[4:].strip()
        if not label:
            raise ImportFailed("--account new:LABEL needs a label")
        uid = f"imp-{slug(label)}"
        row = con.execute("SELECT source, label FROM accounts WHERE uid=?", (uid,)).fetchone()
        if row:
            if row[0] != "import":
                raise ImportFailed(f"{uid} exists and is not a manual account")
            return uid, False
        if iban and not force and (dup := con.execute(
                "SELECT uid, COALESCE(label, name, uid) FROM accounts WHERE identification_hash=? AND uid<>?",
                (ident_hash(iban), uid)).fetchone()):
            raise ImportFailed(f"the file's IBAN (…{normalize_iban(iban)[-4:]}) already belongs to account "
                               f"{dup[0]} ({dup[1]}): import there with --account {dup[0]}, or pass --force to "
                               "create a separate manual account anyway")
        if create:
            if con.execute("SELECT 1 FROM accounts WHERE LOWER(label)=LOWER(?)", (label,)).fetchone():
                raise ImportFailed(f"an account is already labelled {label!r}: use --account <uid|label> for it")
            iban = normalize_iban(iban)
            # name stays NULL on purpose: account names feed the household-member detection of the parser
            con.execute("""INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw,
                           bank, label, source, identification_hash) VALUES (?,NULL,NULL,?,?,NULL,'{}',?,?,'import',?)""",
                        (uid, iban, currency, bank or label, label, ident_hash(iban)))
        return uid, True
    try:
        return resolve_account(con, spec), False
    except AccountError as e:
        raise ImportFailed(str(e)) from e


def _check_account(con, uid: str, parsed: Parsed, rows: list[Row], created: bool) -> list[str]:
    warns = []
    acc_iban, acc_cur = con.execute("SELECT iban, currency FROM accounts WHERE uid=?", (uid,)).fetchone() or (None, None)
    if parsed.iban and acc_iban and normalize_iban(parsed.iban) != normalize_iban(acc_iban):
        raise ImportFailed(f"the file is for IBAN …{parsed.iban[-4:]} but account {uid} has …{acc_iban[-4:]}: "
                           "wrong account?")
    currencies = {r.currency for r in rows}
    if acc_cur and acc_cur != "XXX" and currencies - {acc_cur}:
        raise ImportFailed(f"the file has {', '.join(sorted(currencies))} rows but account {uid} is {acc_cur}; "
                           "use --currency to import one currency only")
    if len(currencies) > 1:
        raise ImportFailed(f"the file mixes currencies ({', '.join(sorted(currencies))}): import one at a time "
                           "with --currency, into one account each")
    return warns


def import_file(con, path: Path, account: str, profile: Profile | None = None, fmt: str | None = None,
                dry_run: bool = False, bank: str | None = None, currency_filter: str | None = None,
                new_iban: str | None = None, force: bool = False) -> ImportReport:
    path = Path(path)
    if not path.is_file():
        raise ImportFailed(f"file not found: {path}")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        fmt = fmt or parsers.detect_format(path.name, data)
        if fmt == "ofx":
            parsed = parsers.parse_ofx(data)
        elif fmt == "camt053":
            parsed = parsers.parse_camt053(data)
        elif fmt == "csv":
            if profile is None:
                raise ImportFailed("a CSV import needs --profile NAME or an inline mapping "
                                   "(--date-col/--desc-col/--amount-col ...); see --list-profiles")
            parsed = parsers.parse_csv(data, profile)
        else:
            raise ImportFailed(f"unknown format {fmt!r}")
    except (ParseError, ProfileError) as e:
        raise ImportFailed(str(e)) from e

    rows = parsed.rows
    skipped = 0
    if currency_filter:
        kept = [r for r in rows if r.currency == currency_filter.upper()]
        skipped = len(rows) - len(kept)
        rows = kept
        if not rows:
            raise ImportFailed(f"no {currency_filter.upper()} rows in the file")
    first_cur = rows[0].currency
    uid, created = ensure_account(con, account, bank, first_cur, new_iban or parsed.iban, create=not dry_run,
                                  force=force)
    rep = ImportReport(file=path.name, format=parsed.format, account_uid=uid, account_created=created,
                       dry_run=dry_run, rows_total=len(rows), rows_skipped=skipped,
                       warnings=list(parsed.warnings))
    rep.date_min, rep.date_max = min(r.date for r in rows), max(r.date for r in rows)
    try:
        rep.warnings += _check_account(con, uid, parsed, rows, created)
    except ImportFailed:
        if con.in_transaction:
            con.rollback()
        raise
    prior = con.execute("SELECT imported_at FROM imports WHERE account_uid=? AND file_sha256=?", (uid, sha)).fetchone()
    rep.already_imported = prior[0] if prior else None

    existing: Counter = Counter()
    if not (created and dry_run):
        for d, amt, desc in con.execute(
                "SELECT booking_date, amount, description FROM transactions WHERE account_uid=? "
                "AND booking_date BETWEEN ? AND ?", (uid, rep.date_min, rep.date_max)):
            existing[content_key(d, amt, desc)] += 1
    seen: Counter = Counter()
    todo: list[tuple[Row, str]] = []
    for r in rows:
        k = content_key(r.date, r.amount, r.description)
        seen[k] += 1
        if seen[k] > existing[k]:
            todo.append((r, import_tx_key(uid, k, seen[k])))
    rep.rows_duplicate = len(rows) - len(todo)
    rep.rows_new = len(todo)
    rep.new_rows = [r for r, _ in todo]
    if dry_run:
        return rep

    ts = now_iso()
    try:
        cur = con.execute("INSERT INTO imports(account_uid, file_name, file_sha256, format, profile, imported_at, "
                          "rows_total, rows_new) VALUES (?,?,?,?,?,?,?,0)",
                          (uid, path.name, sha, parsed.format, profile.name if profile and fmt == "csv" else None,
                           ts, len(rows)))
        rep.import_id = cur.lastrowid
        inserted = 0
        for r, key in todo:
            c = con.execute(
                "INSERT OR IGNORE INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, "
                "amount, currency, counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
                (key, uid, r.reference, r.date, r.value_date, r.amount, r.currency, r.counterparty,
                 r.description, json.dumps({"source": "import", "file": path.name, "line": r.line, **r.raw},
                                           default=str), ts, ts))
            if c.rowcount:
                con.execute("INSERT INTO import_rows VALUES (?,?)", (key, rep.import_id))
                inserted += 1
        con.execute("UPDATE imports SET rows_new=? WHERE id=?", (inserted, rep.import_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    rep.rows_new = inserted
    rep.rows_duplicate = len(rows) - inserted
    return rep


def format_report(rep: ImportReport, sample: int = 10) -> str:
    head = "DRY RUN - nothing written. " if rep.dry_run else ""
    lines = [f"{head}{rep.file} ({rep.format}) -> account {rep.account_uid}"
             + (" (new manual account)" if rep.account_created else ""),
             f"  rows read: {rep.rows_total}  new: {rep.rows_new}  already present: {rep.rows_duplicate}"
             + (f"  other currency skipped: {rep.rows_skipped}" if rep.rows_skipped else ""),
             f"  dates: {rep.date_min} -> {rep.date_max}"]
    if rep.already_imported:
        lines.append(f"  note: this exact file was already imported on {rep.already_imported}")
    for w in rep.warnings:
        lines.append(f"  warning: {w}")
    if rep.dry_run and rep.new_rows:
        lines.append(f"  would insert ({min(sample, len(rep.new_rows))} of {len(rep.new_rows)}):")
        for r in rep.new_rows[:sample]:
            lines.append(f"    {r.date} {r.amount:>10.2f} {r.currency}  {r.description[:60]}")
    if not rep.dry_run and rep.rows_new:
        lines.append("  next: `coach normalize` then `coach classify run` to categorise the new rows")
    return "\n".join(lines)
