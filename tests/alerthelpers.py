"""Synthetic household for the E10 tests (alerts, channels, digest). Every name, merchant, address and amount is invented.

On top of the E8 subscriptions world (STREAMBOX unused and still paid, a CLOUDBOX price rise, TELCOCO with a contract number ...):
    consent of Fortuneo   valid until NOW + 2 days  (d3, high);  consent of the Caisse d'Epargne  NOW + 10 days (d14, medium)
    account 'fo'          three failed syncs in a row after an ok one
    SENTINELS             strings that must never appear in an external message, in either detail mode
"""
from __future__ import annotations

import datetime as dt

from helpers import add_tx
from subshelpers import build_subs_world

TODAY = dt.date(2026, 10, 4)
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.timezone.utc)

HOUSEHOLD = """\
members:
  - id: anna
    name: Anna Rossi
    role: adult
    birth_year: 1984
    aliases: ["MME ANNA ROSSI", "M OU MME ROSSI ANNA"]
  - id: luca
    name: Luca Rossi
    role: adult
  - id: mia
    name: Mia Rossi
    role: child
    birth_year: 2012
employers: ["Zephyrtech Industries"]
places: ["Montpellier", "Sète"]
schools: ["Lycée Pasteur"]
contact:
  address: "12 rue des Lilas, 34000 Montpellier"
  email: "anna.rossi@example.org"
  phone: "+33 6 12 34 56 78"
"""

# what an external message must never hold
SENTINELS = ["Anna", "Luca", "Mia", "Rossi", "Zephyrtech", "Montpellier", "Sète", "Lycée Pasteur", "TC-778899",
             "FR7600000000000000000001", "FR7600000000000000000002", "FR7630006000011234567890189", "M OU MME ROSSI ANNA",
             "anna.rossi@example.org", "+33 6 12 34 56 78", "rue des Lilas", "BIANCHI", "TELCOCO", "TelcoCo", "STREAMBOX", "StreamBox",
             "CLOUDBOX", "Cloudbox", "Fortuneo", "Revolut", "HOMEBANK", "Homebank", "Caisse d'Epargne"]


def iso(days=0, hours=0) -> str:
    return (NOW + dt.timedelta(days=days, hours=hours)).isoformat()


def build_alert_world(cfg, *, consents: bool = True, failing_sync: bool = True):
    con = build_subs_world(cfg)
    (cfg.memory_dir / "household.yaml").write_text(HOUSEHOLD)
    add_tx(con, "ce", "salary1", "2026-09-28", 3200.0, "VIR SEPA ZEPHYRTECH INDUSTRIES SALAIRE", "transfer_in")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('VIR SEPA ZEPHYRTECH INDUSTRIES SALAIRE','Zephyrtech Industries',"
                "'income.salary',0.95,0,'llm','m','t')")
    con.execute("INSERT INTO balances VALUES ('fo','2026-10-04T08:00:00+00:00','CLBD',1500.0,'EUR','2026-10-04')")
    con.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',4200.0,'EUR','2026-10-04')")
    if consents:
        con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s1'", (iso(2),))
        con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s2'", (iso(10),))
    if failing_sync:
        con.execute("INSERT INTO sync_log VALUES ('fo','2026-09-30T06:00:00+00:00',1,3,1,'ok')")
        for d in (1, 2, 3):
            con.execute("INSERT INTO sync_log VALUES ('fo',?,0,0,0,'HTTP 429')", (f"2026-10-0{d}T06:00:00+00:00",))
    con.commit()
    return con


# ---------------------------------------------------------------- fakes for the engine and channel tests

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from coach.alerts import channels as ch_mod, signals  # noqa: E402
from coach.alerts.settings import AlertSettings  # noqa: E402

SECRETS = {"smtp_password": "pw-SMTP-1", "telegram_bot_token": "123456:TOKEN-xyz", "ntfy_token": "tk_ntfy_abc"}
ALL_ON = {"ntfy": {"enabled": True, "url": "https://ntfy.example.net/coach-9f3a"},
          "email": {"enabled": True, "host": "smtp.example.net", "port": 587, "tls": "starttls", "username": "mailer",
                    "from": "coach@example.net", "to": "owner@example.net"},
          "telegram": {"enabled": True, "chat_id": "424242"},
          "macos": {"enabled": True}}


class FakeSMTP:
    def __init__(self, net, host, port, tls):
        self.net, self.info = net, (host, port, tls)
        self.logged = None

    def login(self, user, pw):
        self.logged = (user, pw)

    def send_message(self, msg):
        if self.net.fail:
            raise RuntimeError("boom pw-SMTP-1")
        self.net.mails.append({"smtp": self.info, "login": self.logged, "from": msg["From"], "to": msg["To"], "subject": msg["Subject"], "message_id": msg["Message-ID"],
                               "body": msg.get_content()})

    def quit(self):
        pass


class Net:
    """Every outside transport, faked: nothing here touches a network or runs a command."""

    def __init__(self, secrets=None):
        self.posts, self.mails, self.macos, self.fail = [], [], [], False
        self.secrets = SECRETS if secrets is None else secrets

    def http_post(self, url, data, headers, timeout=10):
        if self.fail:
            raise OSError("connection refused token=" + SECRETS["telegram_bot_token"])
        self.posts.append({"url": url, "body": data.decode(), "headers": dict(headers)})
        return 200

    def smtp(self, host, port, tls):
        return FakeSMTP(self, host, port, tls)

    def run(self, cmd, **kw):
        self.macos.append(cmd)
        return SimpleNamespace(returncode=0 if not self.fail else 1, stdout="", stderr="")

    def transports(self):
        return ch_mod.Transports(http_post=self.http_post, smtp=self.smtp, macos_runner=self.run, secret=self.secrets.get)

    def total(self):
        return len(self.posts) + len(self.mails) + len(self.macos)


def settings(**raw) -> AlertSettings:
    raw.setdefault("digest_only_kinds", [])          # the shipped default holds unusual_charge and price_increase back; most tests want them sent
    return AlertSettings.from_dict(raw)


def cand(kind="unusual_charge", key="k1", severity="medium", title="Possible duplicate charge", amount_c=15234, **payload):
    return signals.Candidate(kind, key, severity, title, "body text", {"amount_c": amount_c, **payload})


def deliveries(con):
    return con.execute("SELECT channel, what, n_events, ok, error FROM alert_deliveries ORDER BY id").fetchall()


def event(con, c):
    from coach.alerts import store
    return store.get(con, c.id)


def retime_consents(cfg) -> None:
    """Tests that run the scheduler use the REAL clock: move the consents to 2 and 10 days from the real now, so they never depend on today's date."""
    from coach import db as dbm
    now = dt.datetime.now(dt.timezone.utc)
    con = dbm.connect(cfg, insecure=True)
    con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s1'", ((now + dt.timedelta(days=2)).isoformat(),))
    con.execute("UPDATE sessions SET valid_until=? WHERE session_id='s2'", ((now + dt.timedelta(days=10)).isoformat(),))
    con.commit()
    con.close()
