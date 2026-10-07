"""CLI of the alerts and the weekly digest (E10): ``coach alerts ...``.

    check [--dry-run] [--no-send]     evaluate the signals, keep the events, send to the enabled channels (--dry-run: write and send nothing)
    list [--status S] [--kind K] [--all]   the events (open ones by default)
    show ID                           one event in full (local detail)
    ack ID | --all                    acknowledge: no more messages about it (it comes back only if its severity goes up)
    snooze ID|KIND --days N           hold an event, or a whole kind, for N days
    restore ID|KIND                   undo an ack / snooze / suppression (a kind: wake it)
    mute-kind KIND / unmute-kind KIND mute a kind until you unmute it (its events are kept as 'suppressed')
    channels                          which channels are enabled and ready (secrets are never shown)
    test-channel NAME [--dry-run]     print exactly what a message would be; without --dry-run send it (interactive terminal + typed yes,
                                      and the channel must be enabled in config.toml)
    digest [--save] [--send] [--dry-run] [--force]   render the weekly summary (to the screen; --save puts it in the in-app feed;
                                      --send also sends the short teaser to the enabled channels; --dry-run shows what would go)

Acking, snoozing and muting change local state in the database only. Nothing here enables a channel: that is done in config.toml.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Optional

from coach import db as db_mod
from coach.alerts import channels as ch_mod, digest as digest_mod, engine, messages as msg_mod, store
from coach.alerts.settings import CHANNELS, KINDS, warnings_of
from coach.analytics import api as analytics_api
from coach.analytics.common import _plain
from coach.i18n_msg import strip_msgs


def alert_warnings(cfg) -> list[str]:
    return warnings_of(cfg.alert_settings, cfg.ui_allowed_hosts)


def _today() -> dt.date:
    return dt.date.today()


def _is_tty() -> bool:
    from coach.memory.commands import _is_tty as t
    return t()


def _connect(a, cfg):
    return db_mod.connect(cfg, insecure=a.insecure)


def _dataset(con, cfg):
    return analytics_api.build_dataset(con, cfg, _today())


def _fmt_event(e: dict) -> str:
    when = e["created"][:10]
    extra = f" until {e['snoozed_until']}" if e["status"] == "snoozed" else ""
    sent = f" sent: {', '.join(e['channels_sent'])}" if e["channels_sent"] else ""
    res = " (resolved)" if e["resolved"] else ""
    return f"{e['id']}  {e['severity']:<6} {e['status']:<10}{extra} {e['kind']:<22} {when}  {e['title']}{res}{sent}"


def _print_dispatch(rows: list[dict], dry: bool) -> None:
    if not rows:
        print("channels: none enabled (the in-app feed only). Enable a channel in config.toml: see docs/alerts.md")
    for r in rows:
        line = f"channel {r['channel']}: {r['status']}"
        if r["reason"]:
            line += f" ({r['reason']})"
        if r.get("baselined"):
            line += f" [first use of this channel: {r['baselined']} existing alert(s) marked as already sent, not sent as a backlog]"
        if r.get("n") and r["status"] in ("would_send", "sent", "deferred"):
            line += f", {r['n']} event(s)"
        print(line)
        if dry and r.get("request"):
            print("  " + ch_mod.format_render(r["request"]).replace("\n", "\n  "))


# ---------------------------------------------------------------- commands

def cmd_check(a, cfg):
    s = cfg.alert_settings
    if not s.enabled:
        print("alerts are disabled ([alerts] enabled = false): nothing evaluated")
        return
    con = _connect(a, cfg)
    ds = _dataset(con, cfg)
    held = not a.dry_run and not a.no_send and not _is_tty()          # outside a terminal (an agent, a pipe) nothing leaves the machine
    res = engine.run(con, cfg, ds, s=s, dry_run=a.dry_run, send=not a.no_send and not held)
    ev = res["evaluated"]
    if a.json:
        print(json.dumps(_plain(res), ensure_ascii=False, indent=2, default=str))
        return
    print(("DRY RUN: nothing was written or sent. " if a.dry_run else "") + f"{ev['candidates']} signal(s): {len(ev['new'])} new, "
          f"{len(ev['escalated'])} escalated, {len(ev['reopened'])} back, {len(ev['resolved'])} resolved, {len(ev['unchanged'])} unchanged")
    if held:
        print("not interactive (no terminal): events stored, nothing sent to any channel. Sending happens from `coach schedule run` or from a terminal session.")
    kinds: dict = {}
    for e in res["events"]:
        if e["status"] in ("new", "sent", "snoozed"):
            kinds.setdefault(e["kind"], {}).setdefault(e["severity"], 0)
            kinds[e["kind"]][e["severity"]] += 1
    for k, v in sorted(kinds.items()):
        print(f"  {k:<22}" + "  ".join(f"{sev} {n}" for sev, n in sorted(v.items())))
    if not a.no_send and not held:
        _print_dispatch(res["dispatch"], a.dry_run)
    if not a.dry_run:
        c = store.counts(con, ds.today)
        print(f"open alerts: {c['open']} ({c['high']} high); `coach alerts list` to see them")


def cmd_list(a, cfg):
    con = _connect(a, cfg)
    rows = store.listing(con, status=a.status, kind=a.kind, severity=a.severity, include_resolved=a.all, today=_today())
    if not a.all and not a.status:
        rows = [r for r in rows if r["status"] in ("new", "sent", "snoozed")]
    if a.json:
        print(json.dumps(strip_msgs(_plain(rows)), ensure_ascii=False, indent=2, default=str))
        return
    for r in rows:
        print(_fmt_event(r))
    if not rows:
        print("no alerts" + ("" if a.all else " (--all shows acked, suppressed and resolved ones)"))


def _one(con, ref: str) -> dict:
    hits = store.find(con, ref)
    if not hits:
        sys.exit(f"error: no alert {ref!r} (`coach alerts list --all`)")
    if len(hits) > 1:
        sys.exit(f"error: {ref!r} matches {len(hits)} alerts: give more characters of the id")
    return hits[0]


def cmd_show(a, cfg):
    con = _connect(a, cfg)
    e = _one(con, a.id)
    if a.json:
        print(json.dumps(strip_msgs(_plain(e)), ensure_ascii=False, indent=2, default=str))
        return
    print(_fmt_event(e) + f"\n\n{e['body']}\n\npayload (stays on this machine): {json.dumps(strip_msgs(e['payload']), ensure_ascii=False, default=str)}")
    if e["escalations"]:
        print(f"escalated {e['escalations']} time(s)")


def cmd_ack(a, cfg):
    con = _connect(a, cfg)
    if a.all:
        n = 0
        for e in store.open_events(con, _today()):
            n += store.ack(con, e["id"])
        print(f"acknowledged {n} alert(s)")
        return
    if not a.id:
        sys.exit("error: give an alert id, or --all")
    e = _one(con, a.id)
    store.ack(con, e["id"])
    print(f"acknowledged {e['id']}: {e['title']}")


def _is_kind(ref: str) -> bool:
    return ref in KINDS


def cmd_snooze(a, cfg):
    if a.days < 1 or a.days > 180:
        sys.exit("error: --days must be between 1 and 180")
    con = _connect(a, cfg)
    until = _today() + dt.timedelta(days=a.days)
    if _is_kind(a.target):
        n = store.snooze_kind(con, a.target, until)
        print(f"snoozed the kind {a.target} until {until} ({n} open event(s); new ones of this kind are held too)")
        return
    e = _one(con, a.target)
    store.snooze(con, e["id"], until)
    print(f"snoozed {e['id']} until {until}: {e['title']}")


def cmd_restore(a, cfg):
    con = _connect(a, cfg)
    if _is_kind(a.target):
        n = store.snooze_kind(con, a.target, None)
        print(f"woke the kind {a.target} ({n} event(s))")
        return
    e = _one(con, a.target)
    store.restore(con, e["id"])
    print(f"restored {e['id']}: {e['title']}")


def cmd_mute(a, cfg):
    con = _connect(a, cfg)
    try:
        store.set_muted(con, a.kind, a.alerts_cmd == "mute-kind")
    except ValueError as e:
        sys.exit(f"error: {e}")
    print(f"{a.kind}: {'muted (its events are kept as suppressed)' if a.alerts_cmd == 'mute-kind' else 'unmuted'}")


def cmd_channels(a, cfg):
    s = cfg.alert_settings
    rows = ch_mod.status(s, cfg=cfg)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"in-app feed: always on.  external_detail = {s.external_detail}, min_severity = {s.min_severity}, quiet_hours = "
          f"{s.quiet_hours or 'off'}, max_per_week = {s.max_per_week}")
    for w in alert_warnings(cfg):
        print(f"  WARNING: {w}")
    for r in rows:
        state = "READY" if r["ready"] else ("enabled but NOT READY" if r["enabled"] else "off")
        print(f"  {r['channel']:<9}{state:<24}{r['target']}")
        for p in r["problems"]:
            print(f"      ! {p}")
    if not any(r["enabled"] for r in rows):
        print("all external channels are off (default). Enabling one is done in config.toml: see docs/alerts.md")


def cmd_test_channel(a, cfg):
    s = cfg.alert_settings
    if a.name not in CHANNELS:
        sys.exit(f"error: unknown channel {a.name!r} (known: {', '.join(CHANNELS)})")
    c = s.channel(a.name)
    sample = msg_mod.sample_events()
    guard = msg_mod.external_guard(cfg, _connect(a, cfg)) if a.name != "macos" else None
    m = engine.compose(a.name, sample, s, guard, title="Coach (test)")
    if m is None:
        sys.exit("error: the message was refused by the privacy filter")
    if a.name == "macos":
        m.title = "Coach (test)"
    t = ch_mod.Transports()
    probs = ch_mod.problems(s, a.name, t, cfg)
    print(f"# test message for {a.name}, external_detail = {s.external_detail}, built from SAMPLE events (nothing of your data)")
    print(ch_mod.format_render(ch_mod.render(s, a.name, m, t)))
    if m.note:
        print(f"# note: {m.note}")
    if a.dry_run:
        print("# --dry-run: nothing was sent" + ("" if c.enabled else f" (the channel is disabled in config.toml: [alerts.{a.name}] enabled = true)"))
        if c.enabled and probs:
            print("# not ready: " + "; ".join(probs))
        return
    if not c.enabled:
        sys.exit(f"error: channel {a.name} is disabled in config.toml, so nothing is sent. Enable it there yourself "
                 f"([alerts.{a.name}] enabled = true) or use --dry-run to see the message.")
    if probs:
        sys.exit("error: channel not ready: " + "; ".join(probs))
    if not _is_tty():
        sys.exit("error: sending a message outside this machine is a human decision and needs an interactive terminal (stdin and "
                 "stdout must be a TTY). Run it yourself in a terminal. There is deliberately no --yes.")
    if input(f"Send this test message through {a.name} now? [y/N] ").strip().lower() not in ("y", "yes"):
        print("not sent")
        return
    try:
        ch_mod.send(s, a.name, m, t, cfg)
    except ch_mod.ChannelError as e:
        con = _connect(a, cfg)
        store.record_delivery(con, a.name, "test", 0, False, str(e)[:120])
        sys.exit(f"error: the send failed: {e}")
    con = _connect(a, cfg)
    store.record_delivery(con, a.name, "test", 0, True)
    print("sent")


def cmd_digest(a, cfg):
    s = cfg.alert_settings
    con = _connect(a, cfg)
    ds = _dataset(con, cfg)
    d = digest_mod.build(con, cfg, ds, s, today=ds.today)
    if a.json:
        print(json.dumps(_plain({k: v for k, v in d.items() if k != "markdown"}), ensure_ascii=False, indent=2, default=str))
    else:
        print(d["markdown"])
    if a.save:
        ok, why = (True, "") if a.force else digest_mod.due(con, s, ds.today)
        if not ok and not a.dry_run:
            print(f"# not saved: {why} (--force makes another)")
            return
        if a.dry_run:
            print("# --dry-run: not saved")
            iid = "cin_dryrun"
        else:
            iid = digest_mod.store_digest(con, d)
            print(f"# saved to the in-app feed as {iid}")
        if a.send and not a.dry_run and not _is_tty():
            print("# not interactive (no terminal): the teaser was not sent. Sending happens from `coach schedule run` or from a terminal session.")
        elif a.send:
            rows = digest_mod.deliver(con, cfg, s, d, iid, dry_run=a.dry_run)
            _print_dispatch(rows, a.dry_run)
    elif a.send:
        sys.exit("error: --send needs --save (the digest is sent once, with the insight it belongs to)")


def register(sub, add) -> None:
    ap = sub.add_parser("alerts", help="alerts, notification channels and the weekly digest (E10)",
                        description="alerts and the weekly digest; channels are enabled in config.toml, never from here")
    asub = ap.add_subparsers(dest="alerts_cmd", required=True, metavar="SUBCOMMAND")
    s = add(asub, "check", cmd_check, "evaluate the signals, keep the events, send to the enabled channels")
    s.add_argument("--dry-run", action="store_true", help="write nothing and send nothing; show what would happen")
    s.add_argument("--no-send", action="store_true", help="keep the events but send nothing outside the app")
    s.add_argument("--json", action="store_true")
    s = add(asub, "list", cmd_list, "the alerts (open ones by default)")
    s.add_argument("--status", choices=store.STATUSES); s.add_argument("--kind", choices=KINDS)
    s.add_argument("--severity", choices=("low", "medium", "high")); s.add_argument("--all", action="store_true", help="include resolved ones")
    s.add_argument("--json", action="store_true")
    s = add(asub, "show", cmd_show, "one alert in full")
    s.add_argument("id"); s.add_argument("--json", action="store_true")
    s = add(asub, "ack", cmd_ack, "acknowledge an alert (or all open ones)")
    s.add_argument("id", nargs="?"); s.add_argument("--all", action="store_true")
    s = add(asub, "snooze", cmd_snooze, "hold an alert, or a whole kind, for some days")
    s.add_argument("target", help="an alert id (or its first characters) or a kind: " + ", ".join(KINDS))
    s.add_argument("--days", type=int, required=True)
    s = add(asub, "restore", cmd_restore, "undo an ack / snooze / suppression (a kind: wake it)")
    s.add_argument("target")
    for name, hlp in (("mute-kind", "mute a kind until you unmute it"), ("unmute-kind", "unmute a kind")):
        s = add(asub, name, cmd_mute, hlp)
        s.add_argument("kind", choices=KINDS)
    s = add(asub, "channels", cmd_channels, "which channels are enabled and ready")
    s.add_argument("--json", action="store_true")
    s = add(asub, "test-channel", cmd_test_channel, "print exactly what a test message would be; send it only from a terminal, after a typed yes")
    s.add_argument("name", choices=CHANNELS)
    s.add_argument("--dry-run", action="store_true", help="print the message and send nothing")
    s = add(asub, "digest", cmd_digest, "the weekly summary (rendered locally)")
    s.add_argument("--save", action="store_true", help="put it in the in-app feed (once per week unless --force)")
    s.add_argument("--send", action="store_true", help="with --save: send the short teaser to the enabled channels")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--force", action="store_true"); s.add_argument("--json", action="store_true")
