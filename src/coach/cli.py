"""The ``coach`` command line interface."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from coach import __version__, backup as backup_mod, config as config_mod, db as db_mod
from coach import schedule as schedule_mod, secrets
from coach.classify import commands as cc, entities as entities_mod, splits as splits_mod, taxonomy as taxonomy_mod
from coach.ingest import accounts as accounts_mod, commands as ic
from coach.memory import commands as mc
from coach.analytics import commands as ac
from coach.ingest.client import ApiError

DESCRIPTION = "AI finance coach: bank ingestion, classification, analytics, encrypted storage."

EPILOG = """all commands:
  check, banks, connect, reconnect, finish, sessions, consents, accounts [set|merge], sync, stats, health,
  import, transfers [match|link|unlink], normalize,
  coverage, averages, cashflow, recurring [list|changes|missing|refresh], anomalies [list|refresh|dismiss|undismiss],
  forecast, budget suggest|set|list|status, calendar, goals list|status|set, review year, analytics refresh,
  classify run|enrich|review|correct|compare|report, taxonomy list|add|rename, merchants list|group|merge|split|rename|category,
  split, explain,
  memory show|set|new|append|annotate|history|diff|revert|propose|proposals|accept|reject|check|context|totals|migrate-questions|
         member add|doc add|doc list|doc extract,
  questions list|answer|dismiss|reopen|add|generate,
  alerts check|list|show|ack|snooze|restore|mute-kind|unmute-kind|channels|test-channel|digest,
  household show|assign|unassign|why|log|undo|rule|kids|pocket|budget|allocation|allocate, users list|add|set-role|disable|enable|remove|prefs|audit,
  loans list|show|schedule|payments|alerts|infer|add|edit|odometer|lease|scenario, networth,
  rental list|show|cashflow|pnl|scheme|tax|indicators|flows|add|edit|extension|vacancy|market-rate,
  db migrate|status|encrypt|import-prototype,
  schedule install|uninstall|status|run|loop, ui,
  init, doctor, setup [enablebanking], dev release-check,
  eval fixtures synth|gold bootstrap|sample|label|list|remove|classify|models|coach|runs, usage, logs,
  mcp serve, coach ask|digest|tools,
  backup, restore, config show|import-env|set-secret,
  privacy status|report, security audit, export [--plain|--decrypt], wipe [--dry-run]
(`coach COMMAND --help` for details; global flags: --insecure, --config)"""


# ---------------------------------------------------------------- db / config / backup handlers

def cmd_db_migrate(a, cfg):
    con = db_mod.connect(cfg, insecure=a.insecure, migrate=False, create=a.create)
    key = None if db_mod.is_plaintext(cfg.db_path) else secrets.get_secret("db_key", required=False)
    applied = db_mod.apply_migrations(con, key=key)   # the safety copy of an encrypted DB needs its key
    if applied:
        for m in applied:
            print(f"applied {m.version:04d}_{m.name}")
    else:
        print("database is up to date")


def _warn_leftovers(path):
    left = db_mod.plaintext_leftovers(path)
    if left:
        print("WARNING: unencrypted copies of the data are lying next to the database; delete them once "
              "you have verified the encrypted DB:")
        for p in left:
            print(f"  {p}")


def cmd_db_status(a, cfg):
    path = cfg.db_path
    state = db_mod.is_plaintext(path)
    print(f"database: {path}")
    print("encryption: " + {True: "NONE (plaintext)", False: "SQLCipher", None: "(file does not exist yet)"}[state])
    if state is None:
        return
    con = db_mod.connect(cfg, insecure=a.insecure, migrate=False)
    st = db_mod.status(con)
    print("applied migrations:")
    for v, n, at in st["applied"]:
        print(f"  {v:04d}_{n}  ({at})")
    if not st["applied"]:
        print("  (none)")
    print("pending migrations:" + ("" if st["pending"] else " none"))
    for v, n in st["pending"]:
        print(f"  {v:04d}_{n}")
    if st["unknown"]:
        print(f"warning: database has migrations unknown to this version: {st['unknown']}")
    print("rows per table:")
    for t, n in db_mod.table_counts(con).items():
        print(f"  {t:<24}{n}")
    _warn_leftovers(path)


def cmd_db_encrypt(a, cfg):
    path, bak = db_mod.encrypt_database(cfg)
    print(f"encrypted database written to {path} (SQLCipher; key = secret 'db_key')")
    print(f"row counts verified table by table.\nplaintext copy kept as {bak}")
    print("WARNING: the .plaintext.bak file still holds all your data unencrypted. Check that the app works "
          "with the encrypted DB (e.g. `uv run coach stats`), then delete it securely.")
    _warn_leftovers(path)


def cmd_db_import(a, cfg):
    source = Path(a.source) if a.source else cfg.root / "prototype" / "ingest" / "data" / "finance.db"
    if not a.source and not source.exists():
        sys.exit(f"error: the legacy prototype database {source} no longer exists (it was removed on purpose). "
                 "Pass --source PATH to import another prototype DB, or create an empty database with "
                 "`coach db migrate --create`.")
    dest, result = db_mod.import_prototype(cfg, source, force=a.force)  # warnings go to stderr
    print(f"imported {source} -> {dest} (source untouched), migrations applied")
    for t, (s, d) in result.items():
        print(f"  {t:<24}{s:>8} -> {d:<8} OK")
    print("row counts match for every table.")
    if db_mod.is_plaintext(dest):
        print("note: the copy is plaintext; run `uv run coach db encrypt` once the db_key secret is set.")


def cmd_config_show(a, cfg):
    for k, v, s in config_mod.effective(cfg):
        print(f"{k:<34} {v}" + (f"   [{s}]" if s else ""))
    print("\nsecrets (values are never shown):")
    for name, st in secrets.describe():
        print(f"  {name:<20} {st}")


def cmd_config_import_env(a, cfg):
    dest, changed = config_mod.import_env(cfg, force=a.force)
    if changed:
        print(f"wrote {', '.join(changed)} to {dest}")
        print("You can now delete prototype/ingest/.env (the private key file stays where it is).")
    else:
        print(f"nothing to import: {dest} already has the values (use --force to overwrite)")


def cmd_config_set_secret(a, cfg):
    if a.generate:
        value = secrets.generate()
    else:
        value = getpass.getpass(f"Value for secret '{a.name}': ")
        if not value:
            sys.exit("Empty value, nothing stored")
    secrets.set_secret(a.name, value)
    print(f"stored '{a.name}' in {secrets.store_label()}" + ("" if secrets.backend() == "file" else f" (service '{secrets.SERVICE}')")
          + (" - back it up in your password manager: losing it makes the data unrecoverable." if a.generate else ""))


def cmd_backup(a, cfg):
    path, pruned = backup_mod.create_backup(cfg)
    print(f"backup written: {path} ({path.stat().st_size} bytes)")
    for p in pruned:
        print(f"  pruned old backup {p.name}")


def cmd_restore(a, cfg):
    files = backup_mod.restore_backup(Path(a.archive), Path(a.to), force=a.force)
    print(f"restored {len(files)} file(s) into {a.to}")
    # a restored database is a COPY: it gets its own identity, so journals of the original never apply to it
    from coach.classify.taxonomy import regenerate_identity_of_file
    for f in files:
        if f.suffix == ".db" and regenerate_identity_of_file(f, secrets.get_secret("db_key", required=False),
                                                              rebind_journal=False):
            print(f"  new database identity for {f.name}")


def _cmd_ui(a, cfg):
    from coach.api.server import cmd_ui           # fastapi / uvicorn are imported only when the web app is started
    cmd_ui(a, cfg)


def cmd_schedule(a, cfg):
    s = a.sched_cmd
    if s == "install":
        schedule_mod.install(cfg, a.agents_dir, dry_run=a.dry_run, load=not a.no_load,
                             do_preflight=not a.skip_preflight)
    elif s == "uninstall":
        schedule_mod.uninstall(a.agents_dir, dry_run=a.dry_run)
    elif s == "status":
        schedule_mod.status(cfg, a.agents_dir)
    elif s == "run":
        code = schedule_mod.run_daily(cfg, insecure=a.insecure)
        sys.exit(code)
    elif s == "loop":
        sys.exit(schedule_mod.loop(cfg, insecure=a.insecure, run_now=a.run_now))


def cmd_mcp_serve(a, cfg):
    from coach import egress
    from coach.mcp.server import serve
    try:
        egress.allow("mcp.finance", {"purpose": "stdio-session"}, cfg=cfg)        # E11-4: refused under [privacy] local_only
    except egress.EgressDenied as e:
        sys.exit(f"error: {e}")
    only = [x for x in (getattr(a, "only", None) or "").split(",") if x] or None
    try:
        serve(cfg, insecure=a.insecure, session_id=getattr(a, "session", None), only=only)
    except RuntimeError as e:
        sys.exit(f"error: {e}")


# ---------------------------------------------------------------- parser

class NoAbbrevParser(argparse.ArgumentParser):
    """allow_abbrev is off on the root parser and (add_subparsers builds each subparser with this same class) on every subparser:
    `--ye` must never be read as `--yes`."""

    def __init__(self, *a, **kw):
        kw["allow_abbrev"] = False
        super().__init__(*a, **kw)


def build_parser() -> argparse.ArgumentParser:
    common = NoAbbrevParser(add_help=False)
    common.add_argument("--insecure", action="store_true", default=argparse.SUPPRESS,
                        help="allow opening an unencrypted (plaintext) database")
    common.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS, help="path to config.toml")

    p = NoAbbrevParser(prog="coach", description=DESCRIPTION, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--insecure", action="store_true", help="allow opening an unencrypted (plaintext) database")
    p.add_argument("--config", metavar="PATH", help="path to config.toml (default: $COACH_CONFIG or ./config.toml)")
    p.add_argument("--version", action="version", version=f"coach {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(parent, name, fn, help_, **kw):
        sp = parent.add_parser(name, help=help_, description=help_, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    add(sub, "check", ic.cmd_check, "validate Enable Banking app id + key (no bank needed)")
    s = add(sub, "banks", ic.cmd_banks, "list banks available in a country")
    s.add_argument("--country", default="IT"); s.add_argument("--q")
    s = add(sub, "connect", ic.cmd_connect, "start a bank connection (opens the bank SCA in the browser)")
    s.add_argument("--bank", required=True); s.add_argument("--country", default="IT")
    s.add_argument("--days", type=int, default=180); s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-server", action="store_true",
                   help="do not start the local HTTPS callback server: copy-paste the redirect URL to `coach finish`")
    s.add_argument("--timeout", type=float, metavar="SECONDS",
                   help="how long to wait for the bank redirect (default: [callback] timeout_seconds, 600)")
    s.add_argument("--replace", action="store_true",
                   help="start a new consent although this bank already has an active one (the old session is "
                        "retired when the new one completes)")
    s = add(sub, "reconnect", ic.cmd_reconnect,
            "renew a consent: new authorisation for the same bank, old session retired, history kept")
    s.add_argument("target", help="bank name (e.g. Fortuneo) or session id (see `coach consents`)")
    s.add_argument("--days", type=int, default=180); s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-server", action="store_true"); s.add_argument("--timeout", type=float, metavar="SECONDS")
    s = add(sub, "finish", ic.cmd_finish, "complete a connection from the redirect URL (or just the code)")
    s.add_argument("code_or_url", nargs="?")
    s.add_argument("--replay", metavar="STATE",
                   help="store the bank's session response saved by a failed completion (no bank call)")
    s = add(sub, "sessions", ic.cmd_sessions, "list bank sessions and their live status (asks the bank)")
    s.add_argument("--all", action="store_true", help="include sessions replaced by a reconnect")
    s = add(sub, "consents", ic.cmd_consents,
            "consent days left and status per bank (ok / expiring <=14d / urgent <=3d / expired / revoked)")
    s.add_argument("--refresh", action="store_true", help="ask the bank for the live status (network)")
    s.add_argument("--all", action="store_true", help="include replaced sessions"); s.add_argument("--json", action="store_true")
    ap = add(sub, "accounts", ic.cmd_accounts, "list accounts (bank, label, owner, purpose); `accounts set` edits them")
    asub = ap.add_subparsers(dest="accounts_cmd", metavar="SUBCOMMAND")
    s = add(asub, "set", ic.cmd_accounts_set, "set label / owner / purpose / analytics exclusion of an account")
    s.add_argument("uid", help="account uid, bank uid, label or unique uid prefix")
    s.add_argument("--label"); s.add_argument("--owner", help="joint or a household member name")
    s.add_argument("--purpose", choices=list(accounts_mod.PURPOSES))
    s.add_argument("--resolve", action="store_true", help="clear 'needs review' (keep it as its own account)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--exclude", action="store_true", help="leave this account out of analytics")
    g.add_argument("--include", action="store_true", help="put it back into analytics")
    s = add(asub, "merge", ic.cmd_accounts_merge,
            "fold account NEW into OLD (OLD keeps uid/label/history; use after an ambiguous reconnect)")
    s.add_argument("old"); s.add_argument("new")
    s = add(sub, "health", ic.cmd_health, "connector health per bank/account; exit 1 if anything is red")
    s.add_argument("--json", action="store_true"); s.add_argument("--refresh", action="store_true",
                                                                help="refresh live consent status first (network)")
    s = add(sub, "import", ic.cmd_import, "import a CSV / OFX / QFX / CAMT.053 statement into an account")
    s.add_argument("file", nargs="?"); s.add_argument("--account", help="uid | label | new:Label")
    s.add_argument("--profile", help="CSV mapping profile name (config/import_profiles/NAME.toml)")
    s.add_argument("--dry-run", action="store_true", help="show what would be inserted, write nothing")
    s.add_argument("--list-profiles", action="store_true")
    s.add_argument("--bank", help="bank name for a new manual account"); s.add_argument("--iban", help="IBAN of a new account")
    s.add_argument("--currency", help="import only this currency's rows")
    s.add_argument("--force", action="store_true",
                   help="new:Label although the file's IBAN already belongs to another account")
    g = s.add_argument_group("inline CSV mapping (overrides the profile; --save-profile NAME keeps it)")
    g.add_argument("--date-col"); g.add_argument("--value-date-col"); g.add_argument("--desc-col", nargs="+")
    g.add_argument("--amount-col"); g.add_argument("--debit-col"); g.add_argument("--credit-col")
    g.add_argument("--counterparty-col"); g.add_argument("--reference-col")
    g.add_argument("--delimiter"); g.add_argument("--encoding"); g.add_argument("--skip-rows", type=int)
    g.add_argument("--date-format", nargs="+", help="strptime formats, e.g. %%d/%%m/%%Y")
    g.add_argument("--decimal", choices=[",", ".", "auto"]); g.add_argument("--header-contains", nargs="+")
    g.add_argument("--currency-default", help="currency when the file has no currency column")
    g.add_argument("--save-profile", metavar="NAME"); g.add_argument("--overwrite-profile", action="store_true")
    tp = add(sub, "transfers", ic.cmd_transfers, "internal transfers between your own accounts")
    tp.add_argument("--unmatched", action="store_true", help="list ambiguous candidates instead of the links")
    tp.add_argument("--proposals", action="store_true",
                    help="list unambiguous pairs that are not linked (low confidence, or auto_link is off)")
    tp.add_argument("--window", type=int, help="days between legs (default: [transfers] window_days)")
    tsub = tp.add_subparsers(dest="transfers_cmd", metavar="SUBCOMMAND")
    s = add(tsub, "match", ic.cmd_transfers_match, "detect and store unambiguous transfer pairs")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--window", type=int)
    s = add(tsub, "link", ic.cmd_transfers_link, "link a debit and a credit by hand")
    s.add_argument("out_tx"); s.add_argument("in_tx"); s.add_argument("--allow-amount-mismatch", action="store_true")
    s = add(tsub, "unlink", ic.cmd_transfers_unlink, "remove a link (id or either leg's tx_key)")
    s.add_argument("ref")
    s = add(sub, "sync", ic.cmd_sync, "pull new transactions and balances")
    s.add_argument("--account"); s.add_argument("--full", action="store_true")
    s.add_argument("--force", action="store_true", help="ignore the per-account daily limit")
    add(sub, "stats", ic.cmd_stats, "per-account totals, balances and last syncs")
    add(sub, "normalize", cc.cmd_normalize, "parse bank descriptions into tx_enriched")

    cl = sub.add_parser("classify", help="categorise transactions", description="categorise transactions")
    csub = cl.add_subparsers(dest="classify_cmd", required=True, metavar="SUBCOMMAND")
    s = add(csub, "run", cc.cmd_run, "label unknown merchants with the LLM")
    s.add_argument("--model"); s.add_argument("--batch", type=int, default=50)
    s.add_argument("--workers", type=int, default=4); s.add_argument("--limit", type=int)
    s.add_argument("--refresh", action="store_true"); s.add_argument("--include-ruled", action="store_true")
    s.add_argument("--dry-run", action="store_true",
                   help="print the exact redacted requests (items, examples, hints) without calling any backend")
    s.add_argument("--no-knn", action="store_true", help="do not label near-duplicates by similarity (always ask the LLM)")
    s.add_argument("--knn-threshold", type=float, help="similarity needed for an automatic label (default [classify])")
    s.add_argument("--abandon-unrecorded", action="store_true",
                   help="forget batches that cannot be collected (id never recorded, no request mapping, unknown "
                        "purpose); needs the provider reachable; you may pay for them twice")
    s.add_argument("--claim-legacy-batches", choices=["label", "compare"],
                   help="say what pending batches of unknown purpose are for, so they can be collected")
    s = add(csub, "enrich", cc.cmd_enrich, "web-search costly low-confidence merchants")
    s.add_argument("--model"); s.add_argument("--max-conf", type=float, default=0.7)
    s.add_argument("--limit", type=int, default=40); s.add_argument("--batch", type=int, default=8)
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--dry-run", action="store_true", help="print the exact redacted web-search request; nothing is sent")
    s = add(csub, "review", cc.cmd_review, "review queue: low-confidence / uncategorized merchants by money at stake")
    s.add_argument("--max-conf", type=float, default=0.7); s.add_argument("--limit", type=int, default=40)
    s.add_argument("--json", action="store_true", help="machine-readable output (for the future API)")
    s.add_argument("--accept", nargs="+", metavar="KEY", help="confirm the current label of these merchant keys as yours")
    s = add(csub, "correct", cc.cmd_correct, "save a user correction for a merchant key (or regex)")
    s.add_argument("key"); s.add_argument("category"); s.add_argument("--name")
    s = add(csub, "compare", cc.cmd_compare, "second-opinion evaluation with another model")
    s.add_argument("--model"); s.add_argument("--sample", type=int, default=80)
    s.add_argument("--seed", type=int, default=7); s.add_argument("--batch", type=int, default=40)
    s.add_argument("--workers", type=int, default=2)
    s = add(csub, "report", cc.cmd_report, "coverage and monthly spending report (averages are coverage-aware, see `coach coverage`)")
    s.add_argument("--legacy", action="store_true", help="print the averages the way they were computed before E4 (12-month total / 12)")

    tp2 = sub.add_parser("taxonomy", help="category taxonomy: list, add, rename", description="category taxonomy")
    tsub2 = tp2.add_subparsers(dest="taxonomy_cmd", required=True, metavar="SUBCOMMAND")
    add(tsub2, "list", taxonomy_mod.cmd_taxonomy, "all categories with the number of merchants using them")
    s = add(tsub2, "add", taxonomy_mod.cmd_taxonomy, "add a leaf: group.leaf \"description\"")
    s.add_argument("id"); s.add_argument("description")
    add(tsub2, "merge-package", taxonomy_mod.cmd_taxonomy,
        "add what is new in the packaged taxonomy / rules to your copies (never overwrites your edits)")
    s = add(tsub2, "rename", taxonomy_mod.cmd_taxonomy,
            "rename a leaf; updates merchants, overrides, rules.yaml and lists the memory/ files that mention it")
    s.add_argument("old"); s.add_argument("new")

    mp = sub.add_parser("merchants", help="canonical merchants (chains, variants)", description="canonical merchants")
    msub = mp.add_subparsers(dest="merchants_cmd", required=True, metavar="SUBCOMMAND")
    s = add(msub, "list", entities_mod.cmd_merchants, "canonical merchants with their keys and spending")
    s.add_argument("--proposals", action="store_true", help="also show similar names that are not grouped")
    s.add_argument("--verbose", "-v", action="store_true")
    s = add(msub, "group", entities_mod.cmd_merchants, "group keys whose merchant names normalise identically")
    s.add_argument("--dry-run", action="store_true")
    s = add(msub, "merge", entities_mod.cmd_merchants, "merge merchant keys / entities into one entity")
    s.add_argument("refs", nargs="+"); s.add_argument("--name")
    s = add(msub, "split", entities_mod.cmd_merchants, "take a merchant key out of its entity")
    s.add_argument("key")
    s = add(msub, "rename", entities_mod.cmd_merchants, "rename an entity"); s.add_argument("entity"); s.add_argument("name")
    s = add(msub, "category", entities_mod.cmd_merchants, "set an entity's default category ('none' clears it)")
    s.add_argument("entity"); s.add_argument("category")

    s = add(sub, "split", splits_mod.cmd_split,
            "split a transaction over categories: coach split TX_KEY food.groceries:70 housing.furniture:rest")
    s.add_argument("tx"); s.add_argument("parts", nargs="*", help="CATEGORY:AMOUNT (or CATEGORY:rest), optional :note")
    s.add_argument("--clear", action="store_true", help="remove the split")

    s = add(sub, "explain", mc.cmd_explain, "why does a transaction have this category: the full decision chain")
    s.add_argument("ref", help="tx_key, or a fragment of the key / bank description / merchant key")
    s.add_argument("--json", action="store_true")

    mp3 = sub.add_parser("memory", help="household memory: view, edit, check, history, proposals, documents",
                         description="household memory (memory/): every write is validated and recorded in a local "
                                     "change history; the coach can only PROPOSE changes")
    mem = mp3.add_subparsers(dest="memory_cmd", required=True, metavar="SUBCOMMAND")
    s = add(mem, "show", mc.cmd_show, "print a memory file or one item (annotation, asset, liability... by id)")
    s.add_argument("ref", help="file (e.g. assets.yaml, liabilities/mortgage.yaml) or an item id"); s.add_argument("--json", action="store_true")
    s = add(mem, "set", mc.cmd_set, "set a field (validated, recorded): memory set livrets-a balance 1200")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("ref", help="file or item id"); s.add_argument("path", help="dotted path, e.g. rate.nominal or tags[0]")
    s.add_argument("value", nargs="?", default=None, help="YAML value (12, 1.5, true, 2026-01-31, null, [a, b]); quote to force text")
    s.add_argument("--unset", action="store_true", help="remove the field instead")
    s.add_argument("--reason", help="why (kept in the history)")
    s.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
    s.add_argument("--new-field", action="store_true", help="allow a field the schema does not know")
    s.add_argument("--drop-comments", action="store_true", help="allow an edit that removes comments of the file")
    s = add(mem, "new", mc.cmd_new, "create a liability or contract file: memory new liability car-loan --kind loa")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("what", choices=["liability", "contract"]); s.add_argument("id")
    s.add_argument("--kind", required=True, help="liability: mortgage|car_loan|loa|lld|consumer_loan|bnpl; "
                                                 "contract: energy|telecom|insurance_home|insurance_car|health|streaming|software|other")
    s.add_argument("--reason")
    s = add(mem, "append", mc.cmd_append, "append text to a Markdown file (profile.md, preferences.md, events.md), recorded")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("file"); s.add_argument("text"); s.add_argument("--reason"); s.add_argument("--dry-run", action="store_true")
    s = add(mem, "annotate", mc.cmd_annotate, "add a categorization annotation, with a preview of the transactions it matches")
    s.add_argument("--id"); s.add_argument("--merchant-key", metavar="REGEX"); s.add_argument("--description", metavar="REGEX")
    s.add_argument("--tx-key", action="append", metavar="KEY", help="repeatable")
    s.add_argument("--date-from"); s.add_argument("--date-to")
    s.add_argument("--amount-min", type=float); s.add_argument("--amount-max", type=float)
    s.add_argument("--category-in", help="comma list: only transactions the pipeline puts in these categories")
    s.add_argument("--weekdays", help="comma list: mon,tue,...")
    s.add_argument("--category"); s.add_argument("--tags", help="comma list, e.g. one_off,capital")
    s.add_argument("--event", help="id of an event of events.md"); s.add_argument("--note")
    s.add_argument("--reason"); s.add_argument("--dry-run", action="store_true", help="preview only")
    s.add_argument("--allow-empty", action="store_true", help="write even if it matches nothing")
    s.add_argument("--propose", action="store_true", help="queue a proposal instead of writing")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s = add(mem, "history", mc.cmd_history, "recorded changes of the memory folder (newest first)")
    s.add_argument("file", nargs="?"); s.add_argument("--limit", type=int, default=30); s.add_argument("--json", action="store_true")
    s = add(mem, "diff", mc.cmd_diff, "unrecorded (hand) edits, or the patch of a change id / history of a file")
    s.add_argument("ref", nargs="?", help="change id or file")
    s = add(mem, "revert", mc.cmd_revert, "undo one recorded change with a new change")
    s.add_argument("change_id")
    s = add(mem, "propose", mc.cmd_propose, "queue a change for approval (what the coach does); writes nothing")
    s.add_argument("ref"); s.add_argument("path", nargs="?"); s.add_argument("value", nargs="?")
    s.add_argument("--reason", required=True); s.add_argument("--source", default="coach-llm")
    s.add_argument("--append-text", metavar="TEXT", help="Markdown file (profile.md, preferences.md, events.md): propose appending this text")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--unset", action="store_true"); g.add_argument("--append", action="store_true", help="append VALUE to the list at PATH")
    g.add_argument("--remove", action="store_true", help="remove the list item at PATH")
    s = add(mem, "proposals", mc.cmd_proposals, "pending proposals with their field-by-field diff")
    s.add_argument("--json", action="store_true"); s.add_argument("--all", action="store_true", help="include accepted / rejected")
    s.add_argument("--id")
    s = add(mem, "accept", mc.cmd_accept,
            "apply a proposal after showing its FRESH diff; interactive terminal only; refuses a tampered or stale one")
    s.add_argument("id")
    s.add_argument("--force", action="store_true", help="apply a stale proposal (the target changed since) after reading the diff")
    s.add_argument("--source", default="cli", help="who accepts (recorded in the history)")
    s.add_argument("--confirm-field", action="append", metavar="PATH",
                   help="explicitly confirm a field extracted from a suspicious document (repeatable)")
    s = add(mem, "reject", mc.cmd_reject, "close a proposal without applying it"); s.add_argument("id"); s.add_argument("--note")
    s = add(mem, "check", mc.cmd_check, "validate memory: schemas, annotations vs transactions, stale facts; exit 1 on errors")
    s.add_argument("--json", action="store_true"); s.add_argument("--no-db", action="store_true", help="skip the checks that need the database")
    s = add(mem, "context", mc.cmd_context, "privacy-aware summary of the memory for an LLM")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true"); g.add_argument("--md", action="store_true", help="Markdown (default)")
    s.add_argument("--max-tokens", type=int); s.add_argument("--names", action="store_true",
                                                          help="keep the real names (local use only: never send this to an LLM)")
    s.add_argument("--no-db", action="store_true")
    s.add_argument("--coarse", action="store_true",
                   help="only structured facts: no profile / event / note text; employers and places declared in "
                        "household.yaml are replaced by placeholders")
    s = add(mem, "totals", mc.cmd_totals, "manual assets / liabilities totals (net worth input)"); s.add_argument("--json", action="store_true")
    s = add(mem, "purge-history", mc.cmd_purge_history,
            "delete history older than a date (rewrites the private history; the files are untouched)")
    s.add_argument("--before", required=True, metavar="YYYY-MM-DD")
    s = add(mem, "migrate-questions", mc.cmd_migrate_questions,
            "one-time: convert open-questions.md into open-questions.yaml (dry run unless --write)")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("--write", action="store_true")
    mm = add(mem, "member", mc.cmd_member_add, "household members")
    msub2 = mm.add_subparsers(dest="member_cmd", required=True, metavar="SUBCOMMAND")
    s = add(msub2, "add", mc.cmd_member_add, "add a member to household.yaml")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("--id", required=True); s.add_argument("--name", required=True)
    s.add_argument("--role", required=True, choices=["adult", "child"]); s.add_argument("--birth-year", type=int)
    s.add_argument("--alias", action="append", help="holder-name spelling seen in bank data (repeatable)")
    dd = add(mem, "doc", mc.cmd_doc_list, "documents attached to the memory")
    dsub2 = dd.add_subparsers(dest="doc_cmd", required=True, metavar="SUBCOMMAND")
    s = add(dsub2, "add", mc.cmd_doc_add, "copy a file into memory/documents/ (private, hashed name)")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("file"); s.add_argument("--kind", required=True, choices=["loan", "contract", "insurance", "statement", "other"])
    s.add_argument("--for", dest="for_id", metavar="ID", help="liability / contract / asset id it belongs to")
    s = add(dsub2, "list", mc.cmd_doc_list, "list documents"); s.add_argument("--json", action="store_true")
    s = add(dsub2, "extract", mc.cmd_doc_extract,
            "extract facts into a PROPOSAL: shows the redacted payload (dry run); --send calls the LLM")
    s.add_argument("doc"); s.add_argument("--into", nargs=2, required=True, metavar=("KIND", "ID"),
                                          help="liability|contract|asset and its id")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="default: show exactly what would be sent")
    g.add_argument("--send", action="store_true", help="send the redacted text to the configured LLM backend")
    s.add_argument("--model"); s.add_argument("--create", action="store_true", help="target does not exist yet: propose creating it")
    s.add_argument("--target-kind", help="kind of the new liability / contract (with --create)")

    qp = sub.add_parser("questions", help="open questions the coach asks you", description="open questions (E3-3)")
    qsub = qp.add_subparsers(dest="questions_cmd", required=True, metavar="SUBCOMMAND")
    s = add(qsub, "list", mc.cmd_q_list, "list questions, biggest stake first")
    s.add_argument("--open", action="store_true"); s.add_argument("--status", choices=["open", "answered", "dismissed"])
    s.add_argument("--json", action="store_true")
    s = add(qsub, "answer", mc.cmd_q_answer, "record the answer (does NOT edit other files)")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("id"); s.add_argument("text")
    s = add(qsub, "dismiss", mc.cmd_q_dismiss, "never ask this again"); s.add_argument("id"); s.add_argument("--reason")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s = add(qsub, "reopen", mc.cmd_q_reopen, "reopen an answered or dismissed question"); s.add_argument("id")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s = add(qsub, "add", mc.cmd_q_add, "add a question by hand")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("text"); s.add_argument("--topic", default="General")
    s.add_argument("--target", metavar="FILE[:FIELD]"); s.add_argument("--stake", type=float)
    s.add_argument("--evidence", action="append", metavar="KEY=VALUE")
    s = add(qsub, "regenerate-view", mc.cmd_q_regen, "rewrite open-questions.md from the yaml (saves a hand-edited copy first)")
    s = add(qsub, "generate", mc.cmd_q_generate, "derive questions from the data and the memory (stable ids, never repeated)")
    s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--max-merchants", type=int, default=15)

    # ---- analytics (E4)
    def common_flags(sp, json_=True, scope=True):
        if json_:
            sp.add_argument("--json", action="store_true", help="machine-readable output (amounts are strings)")
        sp.add_argument("--as-of", metavar="YYYY-MM-DD", help="compute as if today were this date (reproducible)")
        if scope:
            sp.add_argument("--owner", help="only the accounts of this owner (joint or a member id)")
            sp.add_argument("--purpose", help="only the accounts with this purpose (main, cards, rental, kids, savings)")
            sp.add_argument("--account", help="only this account (uid or label)")

    s = add(sub, "coverage", ac.cmd_coverage, "per-account data coverage (first/last date, full months): the base of every average")
    s.add_argument("--json", action="store_true"); s.add_argument("--as-of", metavar="YYYY-MM-DD")
    s = add(sub, "averages", ac.cmd_averages, "monthly spending per category, coverage-aware (one-offs excluded)")
    common_flags(s); s.add_argument("--window", type=int, help="max covered months to average (default [analytics] average_window_months)")
    s.add_argument("--top", type=int, default=30)
    s = add(sub, "cashflow", ac.cmd_cashflow, "income vs spending vs saved and savings rate per month")
    common_flags(s); s.add_argument("--months", type=int, default=6)
    s.add_argument("--by", choices=["purpose", "owner", "all"], help="also print the breakdown per account purpose / owner")
    s.add_argument("--current", action="store_true", help="include the current (partial) month")
    s = add(sub, "recurring", ac.cmd_recurring, "recurring payments: list | changes (price changes) | missing (no contract) | refresh")
    s.add_argument("what", nargs="?", default="list", choices=["list", "changes", "missing", "refresh", "dismiss-change"])
    s.add_argument("id", nargs="?", help="dismiss-change: the price change id (see `coach recurring changes`)")
    common_flags(s); s.add_argument("--all", action="store_true", help="include ended series (changes: include dismissed ones)")
    s.add_argument("--refresh", action="store_true", help="also store the result in the recurring_series table (reads never write otherwise)")
    s.add_argument("--note")
    s.add_argument("--since", metavar="YYYY-MM-DD", help="changes: only price changes from this date")
    s.add_argument("--confirmed", action="store_true", help="changes: only those confirmed by a second payment")
    s = add(sub, "anomalies", ac.cmd_anomalies, "unusual spending: list | refresh | dismiss ID | undismiss ID")
    s.add_argument("what", nargs="?", default="list", choices=["list", "refresh", "dismiss", "undismiss"])
    s.add_argument("id", nargs="?"); common_flags(s)
    s.add_argument("--all", action="store_true", help="include dismissed anomalies"); s.add_argument("--note")
    s = add(sub, "forecast", ac.cmd_forecast, "cash-flow forecast (30/60/90 days) per account and for the household")
    common_flags(s); s.add_argument("--days", type=int, default=90)
    s.add_argument("--points", action="store_true"); s.add_argument("--events", action="store_true", help="list the expected events")
    bp = sub.add_parser("budget", help="budgets: suggest | set | list | status", description="budgets (E4-7)")
    bsub = bp.add_subparsers(dest="budget_cmd", required=True, metavar="SUBCOMMAND")
    def budget_cmd(name, help_):
        sp = add(bsub, name, ac.cmd_budget, help_)
        sp.set_defaults(what=name)
        return sp
    s = budget_cmd("suggest", "suggested monthly budgets from the coverage-aware history")
    common_flags(s); s.add_argument("--months", type=int); s.add_argument("--top", type=int, default=15)
    s = budget_cmd("list", "budgets in memory/budgets.yaml"); s.add_argument("--json", action="store_true")
    s = budget_cmd("status", "progress of the current month"); common_flags(s, scope=False)
    s = budget_cmd("set", "create or update a budget (preview first; writes through the memory store)")
    s.add_argument("target", help="category (food.groceries) or group (food / group:food)")
    s.add_argument("amount", help="monthly amount in EUR")
    s.add_argument("--id"); s.add_argument("--rollover", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--owner"); s.add_argument("--account"); s.add_argument("--start", metavar="YYYY-MM-DD"); s.add_argument("--note")
    s.add_argument("--reason"); s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("--yes", action="store_true", help="write without asking"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--propose", action="store_true", help="queue a proposal instead of writing (what the coach/LLM uses)")
    s.add_argument("--replace", action="store_true", help="--id names an existing budget of another target: retarget it")
    s = add(sub, "calendar", ac.cmd_calendar, "upcoming payments, renewals, notice deadlines, consent expiries")
    s.add_argument("--days", type=int); s.add_argument("--json", action="store_true"); s.add_argument("--as-of", metavar="YYYY-MM-DD")
    s.add_argument("--ics", metavar="FILE", help="write an iCalendar file"); s.add_argument("--transfers", action="store_true", help="include transfers between own accounts")
    gp = sub.add_parser("goals", help="savings goals: list | status | set", description="goals (E4-9)")
    gsub = gp.add_subparsers(dest="goals_cmd", required=True, metavar="SUBCOMMAND")
    def goal_cmd(name, help_):
        sp = add(gsub, name, ac.cmd_goals, help_)
        sp.set_defaults(what=name)
        return sp
    for n_, h_ in (("list", "goals with their progress"), ("status", "goals with their progress")):
        s = goal_cmd(n_, h_); s.add_argument("--json", action="store_true"); s.add_argument("--as-of", metavar="YYYY-MM-DD")
    s = goal_cmd("set", "create or update a goal (preview first; writes through the memory store)")
    s.add_argument("id"); s.add_argument("--target"); s.add_argument("--date", metavar="YYYY-MM-DD", help="target date")
    s.add_argument("--asset"); s.add_argument("--account"); s.add_argument("--tag")
    s.add_argument("--monthly", help="planned monthly contribution"); s.add_argument("--baseline"); s.add_argument("--start", metavar="YYYY-MM-DD")
    s.add_argument("--title"); s.add_argument("--reason"); s.add_argument("--source", metavar="NAME", help="who makes this change; recorded in the history (use `coach` when the coach runs it for the user)")
    s.add_argument("--yes", action="store_true"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--propose", action="store_true")
    rp = sub.add_parser("review", help="year in review", description="year in review (E4-10)")
    rsub = rp.add_subparsers(dest="review_cmd", required=True, metavar="SUBCOMMAND")
    s = add(rsub, "year", ac.cmd_review, "totals, events, top merchants and category changes of a year")
    s.add_argument("year", nargs="?"); common_flags(s)
    g_ = s.add_mutually_exclusive_group(); g_.add_argument("--md", action="store_true", help="Markdown (default)")
    ap = sub.add_parser("analytics", help="analytics maintenance", description="analytics maintenance")
    asub = ap.add_subparsers(dest="analytics_cmd", required=True, metavar="SUBCOMMAND")
    s = add(asub, "refresh", ac.cmd_analytics_refresh, "recompute and store the recurring series and the anomalies, check the budgets")
    s.add_argument("--json", action="store_true"); s.add_argument("--as-of", metavar="YYYY-MM-DD")

    dbp = sub.add_parser("db", help="database: migrations, encryption, import",
                         description="database: migrations, encryption, import")
    dsub = dbp.add_subparsers(dest="db_cmd", required=True, metavar="SUBCOMMAND")
    s = add(dsub, "migrate", cmd_db_migrate, "apply pending migrations")
    s.add_argument("--create", action="store_true",
                   help="create the database if it does not exist (encrypted when db_key is set)")
    add(dsub, "status", cmd_db_status, "show encryption state, migrations and row counts")
    add(dsub, "encrypt", cmd_db_encrypt, "convert the plaintext DB to a SQLCipher DB (keeps *.plaintext.bak)")
    s = add(dsub, "import-prototype", cmd_db_import, "copy the prototype DB into the data dir and migrate it")
    s.add_argument("--source", help="prototype DB to copy (no default any more: the shipped prototype data was removed)")
    s.add_argument("--force", action="store_true", help="overwrite an existing destination")

    sp = sub.add_parser("schedule", help="daily launchd job", description="daily launchd job")
    ssub = sp.add_subparsers(dest="sched_cmd", required=True, metavar="SUBCOMMAND")
    for name, h in [("install", "write and load the LaunchAgent"), ("uninstall", "unload and remove it"),
                    ("status", "show whether it is installed/loaded and the last run"),
                    ("run", "the job: sync -> normalize -> classify run"),
                    ("loop", "the same job in a loop at [schedule] time, without launchd (containers, Linux)")]:
        s = add(ssub, name, cmd_schedule, h)
        if name == "loop":
            s.add_argument("--run-now", action="store_true", help="run the job once right away, then keep looping")
        if name in ("install", "uninstall", "status"):
            s.add_argument("--agents-dir", help="LaunchAgents directory (default ~/Library/LaunchAgents "
                                                "or $COACH_LAUNCHAGENTS_DIR)")
        if name in ("install", "uninstall"):
            s.add_argument("--dry-run", action="store_true", help="print plist and launchctl commands only")
        if name == "install":
            s.add_argument("--skip-preflight", action="store_true",
                           help="install even if the DB / key / Enable Banking config are not ready")
            s.add_argument("--no-load", action="store_true", help="write the plist without calling launchctl")

    add(sub, "backup", cmd_backup, "encrypted dated archive of the DB + memory/")
    s = add(sub, "restore", cmd_restore, "decrypt and extract a backup archive")
    s.add_argument("archive"); s.add_argument("--to", required=True, help="target directory")
    s.add_argument("--force", action="store_true", help="overwrite existing files")

    s = add(sub, "ui", lambda a, cfg: _cmd_ui(a, cfg), "serve the local web app (127.0.0.1 only)")
    s.add_argument("--port", type=int, help="port (default [ui] port, 8765)")
    s.add_argument("--host", help="bind address (default [ui] host, 127.0.0.1; anything else needs [ui] allow_remote)")
    s.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    s.add_argument("--dev", action="store_true", help="dev mode behind the Vite proxy (pnpm dev in web/): API only")
    s.add_argument("--login-link", action="store_true", help="print a fresh one-time login link and exit (the server may be running)")
    s.add_argument("--user", metavar="ID", help="with --login-link: the link of this person's login (`coach users add`); without it: the owner's, who sees everything")
    s.add_argument("--rotate-session-key", action="store_true", help="replace the session secret: every browser is signed out")

    mp4 = sub.add_parser("mcp", help="the finance MCP server (E6-1)", description="the finance MCP server")
    msub = mp4.add_subparsers(dest="mcp_cmd", required=True, metavar="SUBCOMMAND")
    s = add(msub, "serve", cmd_mcp_serve, "serve the redacted finance tools over stdio (Claude Code: .mcp.json; web app jobs)")
    s.add_argument("--session", metavar="ID", help="tool-session id (the web app passes its job id)")
    s.add_argument("--only", metavar="TOOLS", help="comma-separated tool names to expose (the web app narrows digests to read-only + add_insight)")

    from coach.agent import commands as agc
    agc.register(sub, add)
    from coach.skills import commands as skc
    skc.register(sub, add)
    from coach.subs import commands as subc
    subc.register(sub, add)
    from coach.loans import commands as loanc
    loanc.register(sub, add)
    from coach.rental import commands as rentalc
    rentalc.register(sub, add)                   # E15: rental property
    from coach.alerts import commands as alertc
    alertc.register(sub, add)
    from coach.household import commands as householdc
    householdc.register(sub, add)          # E14: household ..., users ...
    from coach import export as export_mod, privacy as privacy_mod, security as security_mod, wipe as wipe_mod
    privacy_mod.register(sub, add)         # E11-1 / E11-4
    security_mod.register(sub, add)        # E11-2 / E11-3
    export_mod.register(sub, add)          # E11-6
    wipe_mod.register(sub, add)            # E11-6

    from coach.quality import commands as qualityc
    qualityc.register(sub, add)            # E12: eval fixtures|gold|classify|models|coach|runs, usage, logs

    from coach.setup import doctor as doctor_mod, init as init_mod, wizard as wizard_mod
    init_mod.register(sub, add)            # E13-1: coach init
    doctor_mod.register(sub, add)          # E13-1: coach doctor
    wizard_mod.register(sub, add)          # E13-2: coach setup [enablebanking]
    from coach import release as release_mod
    release_mod.register(sub, add)         # E13-4: coach dev release-check

    cp = sub.add_parser("config", help="show / migrate configuration", description="show / migrate configuration")
    csub2 = cp.add_subparsers(dest="config_cmd", required=True, metavar="SUBCOMMAND")
    add(csub2, "show", cmd_config_show, "effective configuration (secrets masked)")
    s = add(csub2, "import-env", cmd_config_import_env, "one-time: copy prototype/ingest/.env values into config.toml")
    s.add_argument("--force", action="store_true", help="overwrite values already in config.toml")
    s = add(csub2, "set-secret", cmd_config_set_secret, "store a secret in the macOS Keychain")
    s.add_argument("name", choices=list(secrets.SECRETS))
    s.add_argument("--generate", action="store_true", help="generate a random value instead of prompting")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    insecure = getattr(args, "insecure", False)
    try:
        cfg = config_mod.load_config(getattr(args, "config", None))
        args.insecure = insecure
        from coach import egress as _egress
        _egress.activate(cfg, insecure=insecure)           # E11-1: the [privacy] policy every outbound call is checked against
        from coach.classify import rules as _rules
        _rules.set_config_root(cfg.root)
        _rules.check_paths()
        args.fn(args, cfg)
    except (config_mod.ConfigError, secrets.SecretNotFound, secrets.SecretBackendError,
            db_mod.PlaintextDatabaseError, db_mod.WrongKeyError, db_mod.EncryptionError,
            db_mod.DatabaseMissingError, db_mod.MigrationError, backup_mod.BackupError) as e:
        sys.exit(f"error: {e}")
    except db_mod.Error as e:  # sqlite/sqlcipher errors, e.g. "database is locked"
        sys.exit(f"error: database error: {e} (is another coach process, e.g. the scheduled job, running?)")
    except RuntimeError as e:  # e.g. launchctl failures
        sys.exit(f"error: {e}")
    except ApiError as e:
        sys.exit(f"Enable Banking API error: {e}")


if __name__ == "__main__":
    main()
