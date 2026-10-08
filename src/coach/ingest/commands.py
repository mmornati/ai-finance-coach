"""CLI handlers for the ingestion commands (ported from the eb.py prototype)."""
from __future__ import annotations

import json
import sys

from coach.db import connect
from coach.ingest.client import ApiError, EnableBankingClient
from coach.ingest import accounts as accounts_mod, auth, consent as consent_mod
from coach.ingest.accounts import mask_iban
from coach.ingest.sync import sync_all
from coach.ingest import health as health_mod
from coach.ingest.imports import core as import_core, profiles as import_profiles
from coach import transfers as transfers_mod


def cmd_check(a, cfg):
    app = EnableBankingClient.from_config(cfg).call("GET", "/application")
    print(json.dumps({k: app.get(k) for k in
                      ("name", "environment", "active", "countries", "redirect_urls", "services")},
                     indent=2))
    if not app.get("active"):
        print("\n⚠️  Application is not active: link your own accounts in the Enable Banking control panel.")


def cmd_banks(a, cfg):
    res = EnableBankingClient.from_config(cfg).call(
        "GET", "/aspsps", params={"country": a.country, "psu_type": "personal"})
    banks = res.get("aspsps", res) if isinstance(res, dict) else res
    for b in banks:
        if a.q and a.q.lower() not in b["name"].lower():
            continue
        days = (b.get("maximum_consent_validity") or 0) // 86400
        beta = " [beta]" if b.get("beta") else ""
        print(f"{b['name']:<45} {b['country']}  consent≤{days}d{beta}")


def _fail(e: Exception):
    sys.exit(f"error: {e}")


def _no_browser(a) -> bool:
    """A container has no browser to open: the URL is printed for the one on the host."""
    from coach import home as home_mod
    return bool(a.no_browser) or home_mod.in_container()


def cmd_connect(a, cfg):
    # (getattr defaults: programmatic callers that do not pass no_server never open a listening socket)
    cfg.require_redirect()
    client = EnableBankingClient.from_config(cfg)
    con = connect(cfg, insecure=a.insecure)  # open (and validate) the DB BEFORE calling the bank
    try:
        replace = getattr(a, "replace", False)
        auth.check_single_consent(con, a.bank, a.country, replace)
        replaces = None
        if replace and (c := auth.active_session_for(con, a.bank, a.country)):
            replaces = c.session_id
        code = auth.connect_flow(con, client, cfg, a.bank, a.country, a.days, replaces,
                                 no_server=getattr(a, "no_server", True), no_browser=_no_browser(a),
                                 timeout=getattr(a, "timeout", None))
    except auth.ConnectError as e:
        _fail(e)
    if code:
        sys.exit(code)


def cmd_reconnect(a, cfg):
    """Renew a consent: new authorisation for the same ASPSP; the old session is retired when it completes."""
    cfg.require_redirect()
    client = EnableBankingClient.from_config(cfg)
    con = connect(cfg, insecure=a.insecure)
    try:
        c = auth.find_session(con, a.target)
        print(f"Reconnecting {c.bank} ({c.country}), current consent: {c.status}, valid until {c.valid_until}")
        code = auth.connect_flow(con, client, cfg, c.bank, c.country, a.days, replaces=c.session_id,
                                 no_server=a.no_server, no_browser=_no_browser(a), timeout=a.timeout)
    except auth.ConnectError as e:
        _fail(e)
    if code:
        sys.exit(code)


def cmd_finish(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    try:
        if getattr(a, "replay", None):
            info = auth.replay_auth(con, a.replay)
            auth.print_completion(info)
            return
        if not a.code_or_url:
            sys.exit("error: give the redirect URL (or code), or --replay STATE")
        code, state = auth.parse_finish_arg(con, a.code_or_url)
        info = auth.complete_auth(con, EnableBankingClient.from_config(cfg), code, state)
    except auth.ConnectError as e:
        _fail(e)
    except ApiError as e:
        _fail(auth.ConnectError(auth._friendly(e)))
    auth.print_completion(info)


def cmd_sessions(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    client = EnableBankingClient.from_config(cfg)
    consent_mod.refresh_live(con, client)
    for c in consent_mod.list_consents(con, include_replaced=a.all):
        print(f"{c.bank} ({c.country})  session={c.session_id}  valid_until={c.valid_until}  "
              f"status={c.status}  bank_status={c.live_status or '?'}")
        for uid, aname, iban, api_uid in con.execute(
                "SELECT uid, name, iban, api_uid FROM accounts WHERE session_id=?", (c.session_id,)):
            alias = f"  (bank uid {api_uid})" if api_uid and api_uid != uid else ""
            print(f"    {uid}  {aname or ''} {mask_iban(iban)}{alias}")


def cmd_consents(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    if a.refresh:
        consent_mod.refresh_live(con, EnableBankingClient.from_config(cfg))
    cons = consent_mod.list_consents(con, include_replaced=a.all)
    if a.json:
        print(consent_mod.to_json(cons))
    elif not cons:
        print("no bank sessions yet (`coach connect`)")
    else:
        print(consent_mod.format_table(cons))
        for lvl, c in consent_mod.warnings(con):
            print(f"! {consent_mod.describe(c)}")
        if not a.refresh:
            print("(stored data; add --refresh to ask the bank for the live status)")
    if any(c.status in consent_mod.DEAD for c in cons):
        sys.exit(1)


def cmd_accounts(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    rows = accounts_mod.list_accounts(con)
    print(accounts_mod.format_accounts(rows) if rows else "no accounts yet")


def cmd_accounts_merge(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    try:
        uid = accounts_mod.merge_accounts(con, a.old, a.new)
    except accounts_mod.AccountError as e:
        _fail(e)
    print(f"merged {a.new} into {uid}")
    print(accounts_mod.format_accounts([r for r in accounts_mod.list_accounts(con) if r["uid"] == uid]))


def cmd_accounts_set(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    from coach.household import people as people_mod
    from coach.memory.store import MemoryStore
    try:
        uid = accounts_mod.set_account(con, a.uid, label=a.label, owner=a.owner, purpose=a.purpose,
                                       exclude=True if a.exclude else (False if a.include else None),
                                       resolve=a.resolve, people=people_mod.load(MemoryStore(cfg.memory_dir, history=False)))
    except accounts_mod.AccountError as e:
        _fail(e)
    print(accounts_mod.format_accounts([r for r in accounts_mod.list_accounts(con) if r["uid"] == uid]))


def cmd_sync(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    if not a.account and not con.execute("SELECT 1 FROM accounts WHERE source='api'").fetchone():
        sys.exit("No bank accounts yet: run connect (or finish) first")
    try:
        res = sync_all(con, EnableBankingClient.from_config(cfg), a.account, a.full, a.force, cfg.sync_daily_limit,
                       memory_dir=cfg.memory_dir)
    except accounts_mod.AccountError as e:
        _fail(e)
    try:                                                       # E9-4: a snapshot of the net worth after each sync (warn only)
        from coach.loans import service as loans_service
        loans_service.record_networth(con, cfg)
    except Exception as e:                                     # noqa: BLE001 - a failed snapshot never fails a sync
        print(f"WARNING net worth snapshot could not run: {type(e).__name__}: {str(e)[:120]}")
    if any(r["status"] == "failed" for r in res):
        sys.exit(1)


def cmd_stats(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    rows = con.execute("""
      SELECT a.uid, COALESCE(a.label, a.name), COALESCE(a.bank, s.aspsp_name, 'import'), COUNT(t.tx_key),
             MIN(t.booking_date), MAX(t.booking_date),
             ROUND(SUM(CASE WHEN t.amount<0 THEN t.amount END),2),
             ROUND(SUM(CASE WHEN t.amount>0 THEN t.amount END),2),
             SUM(t.tx_key IS NOT NULL AND t.entry_reference IS NULL)
      FROM accounts a LEFT JOIN sessions s ON s.session_id=a.session_id
      LEFT JOIN transactions t ON t.account_uid=a.uid GROUP BY a.uid""").fetchall()
    for uid, name, bank, n, dmin, dmax, out_, in_, noref in rows:
        print(f"{bank} / {name or uid}: {n} booked tx  {dmin} → {dmax}  out={out_} in={in_}"
              f"  without entry_reference={noref}")
        for bt, amt, cur, ref in con.execute(
                "SELECT balance_type, amount, currency, reference_date FROM balances "
                "WHERE account_uid=? AND fetched_at=(SELECT MAX(fetched_at) FROM balances WHERE account_uid=?)",
                (uid, uid)):
            print(f"    balance {bt}: {amt} {cur} ({ref})")
    print("\nLast syncs:")
    for r in con.execute("SELECT * FROM sync_log ORDER BY ran_at DESC LIMIT 8"):
        print("  ", r)


# ---------------------------------------------------------------- health (E1-11)

def cmd_health(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    if a.refresh:
        consent_mod.refresh_live(con, EnableBankingClient.from_config(cfg))
    rep = health_mod.health(con, cfg.sync_daily_limit, cfg.health_stale_days)
    from coach.memory.commands import memory_summary_line
    mem = memory_summary_line(cfg, con)           # counts only; never changes the exit code
    if a.json:
        import json as _json
        from coach.i18n_msg import strip_msgs
        d = strip_msgs(rep.to_dict())     # the *_msg siblings are the web's only
        d["memory"] = mem
        print(_json.dumps(d, indent=2))
    else:
        print(health_mod.format_report(rep))
        if mem.get("failed"):
            print(f"\nmemory: check could not run ({mem['failed']})")
        else:
            flag = "!" if mem["errors"] else " "
            print(f"\n[{flag}] memory: {mem['errors']} error(s), {mem['warnings']} warning(s), {mem['info']} info"
                  + ("  (`coach memory check` for details)" if mem["errors"] or mem["warnings"] else ""))
    if not rep.ok:
        sys.exit(1)


# ---------------------------------------------------------------- file import (E1-10)

def _inline_profile(a, base):
    """Profile from --profile and/or inline mapping flags (flags win)."""
    p = base or import_profiles.Profile()
    cols = dict(p.columns)
    for key, attr in (("date", "date_col"), ("value_date", "value_date_col"), ("amount", "amount_col"),
                      ("debit", "debit_col"), ("credit", "credit_col"), ("counterparty", "counterparty_col"),
                      ("reference", "reference_col")):
        v = getattr(a, attr, None)
        if v is not None:
            cols[key] = int(v) if v.isdigit() else v
    if a.desc_col:
        cols["description"] = [int(x) if x.isdigit() else x for x in a.desc_col]
        if len(cols["description"]) == 1:
            cols["description"] = cols["description"][0]
    p.columns = cols
    if a.delimiter:
        p.delimiter = a.delimiter
    if a.encoding:
        p.encoding = a.encoding
    if a.skip_rows is not None:
        p.skip_rows = a.skip_rows
    if a.date_format:
        p.date_format = a.date_format
    if a.decimal:
        p.decimal = a.decimal
    if a.currency_default:
        p.currency = a.currency_default.upper()
    if a.header_contains:
        p.header_contains = a.header_contains
    if a.save_profile:
        p.name = a.save_profile
    return p


def cmd_import(a, cfg):
    if a.list_profiles:
        for name, desc in import_profiles.list_profiles(cfg.import_profiles_dir):
            print(f"{name:<28} {desc}")
        return
    if not a.file or not a.account:
        sys.exit("error: usage: coach import FILE --account <uid|label|new:Label> [--profile NAME] [--dry-run]")
    try:
        profile = None
        inline = any(getattr(a, k) is not None for k in (
            "date_col", "desc_col", "amount_col", "debit_col", "credit_col", "delimiter", "encoding", "skip_rows",
            "date_format", "decimal", "header_contains", "currency_default", "save_profile"))
        if a.profile:
            profile = import_profiles.load_profile(cfg.import_profiles_dir, a.profile)
        if inline:
            profile = _inline_profile(a, profile)
            profile.validate()
        con = connect(cfg, insecure=a.insecure)
        rep = import_core.import_file(con, a.file, a.account, profile, dry_run=a.dry_run, bank=a.bank,
                                      currency_filter=a.currency, new_iban=a.iban, force=a.force)
        if a.save_profile and not a.dry_run:
            path = import_profiles.save_profile(cfg.import_profiles_dir, profile, overwrite=a.overwrite_profile)
            print(f"saved mapping profile {path}")
    except (import_core.ImportFailed, import_profiles.ProfileError) as e:
        _fail(e)
    print(import_core.format_report(rep))


# ---------------------------------------------------------------- transfers (E1-12)

def cmd_transfers(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    if a.unmatched or a.proposals:
        res = transfers_mod.find_pairs(con, a.window or cfg.transfer_window_days, **transfers_mod.opts(cfg))
        print(transfers_mod.format_proposals(res) if a.proposals else transfers_mod.format_ambiguous(res))
    else:
        print(transfers_mod.format_links(transfers_mod.list_links(con)))


def cmd_transfers_match(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    res = transfers_mod.match_transfers(con, a.window or cfg.transfer_window_days, dry_run=a.dry_run,
                                        **transfers_mod.opts(cfg))
    verb = "would link" if a.dry_run else "linked"
    print(f"{verb} {len(res.linked)} confident transfer(s); {len(res.proposals)} proposal(s) below "
          f"{cfg.transfer_min_confidence:g} (`coach transfers --proposals`); {len(res.ambiguous)} ambiguous "
          "(`coach transfers --unmatched`)")
    for p in res.linked:
        print(f"  {p['confidence']:.2f}  OUT {p['debit'].short()}\n        IN  {p['credit'].short()}")


def cmd_transfers_link(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    try:
        lid = transfers_mod.link_transfer(con, a.out_tx, a.in_tx, a.allow_amount_mismatch)
    except transfers_mod.TransferError as e:
        _fail(e)
    print(f"linked as #{lid}")


def cmd_transfers_unlink(a, cfg):
    con = connect(cfg, insecure=a.insecure)
    try:
        r = transfers_mod.unlink_transfer(con, a.ref)
    except transfers_mod.TransferError as e:
        _fail(e)
    print(f"unlinked #{r['id']} (the pair will not be proposed again)")
