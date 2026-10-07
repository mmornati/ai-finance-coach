"""What each channel says (E10-1, E10-3), and the PRIVACY GUARD of the external ones.

ntfy, e-mail and Telegram are third-party transports: their messages are built here from a fixed vocabulary and numbers only, never
from the event's own wording (which may name a merchant, a bank or a person).

* ``minimal`` (default): ``Coach: 2 new alerts (1 high). Open the app.`` No amount, no name, no kind.
* ``summary``: one line per event: the KIND (a fixed label), its severity and, for a few kinds, an amount ROUNDED to the nearest
  10 EUR (100 above 1000). Never a merchant, an account, a bank, a person, an IBAN.

Every external text then passes the same :class:`~coach.mcp.guard.PrivacyGuard` the finance tools use (household members, aliases,
employers, places, schools, account labels / uids / IBANs, the contact block, contract numbers, IBAN-, e-mail-, path- and key-like
strings). A ``summary`` that fails it falls back to ``minimal``; a ``minimal`` that fails it (impossible by construction) is not sent.
The local macOS notification may be more detailed (it never leaves the machine) but never shows a full IBAN or a long number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from coach.alerts.settings import RANK

# English labels (the channels, the CLI); the web shows labels.alertKind.<kind> of web/src/locales/<lang>/server.json (tests/test_i18n_msg.py)
KIND_LABEL = {
    "consent": "Bank consent expiring or expired",
    "sync_failing": "Bank sync failing",
    "unusual_charge": "Unusual charge",
    "price_increase": "Price increase",
    "low_balance": "Low balance forecast",
    "budget": "Budget over or at risk",
    "loan_alert": "Loan payment alert",
    "loa_end": "Lease end reminder",
    "unused_subscription": "Unused subscription",
    "contract_notice": "Contract notice deadline",
    "decision_contradicted": "Cancelled service still charged",
    "llm_usage_high": "LLM usage above your monthly threshold",
    "scheme_end": "Rental scheme commitment ending",
    "scheme_check": "Rental scheme condition to check",
    "rent_missing": "Rent not received",
    "kid_budget": "Kid budget limit",              # local feed only (LOCAL_ONLY_KINDS): never sent to a channel
}
AMOUNT_KINDS = ("unusual_charge", "price_increase", "budget", "unused_subscription")   # a rounded amount may be shown for these
LONG_NUMBER = re.compile(r"\b\d[\d .-]{7,}\d\b")


@dataclass
class Message:
    title: str
    body: str
    priority: str = "default"           # default | high
    tags: list = field(default_factory=list)
    detail: str = "minimal"             # minimal | summary | local
    note: Optional[str] = None          # why the text is what it is (a fallback, a masked part)


def plural(n: int, word: str = "alert") -> str:
    return f"{n} new {word}" + ("" if n == 1 else "s")


def round_amount(cents: int) -> int:
    """Whole EUR, to the nearest 10 (100 from 1000 up); never below 10: a figure that cannot identify a payment."""
    eur = abs(int(cents)) / 100
    if eur < 5:
        return 0
    step = 100 if eur >= 1000 else 10
    return max(step, int(round(eur / step)) * step)


def open_the_app(app_url: str = "") -> str:
    return f"Open the app: {app_url}" if app_url else "Open the app."


def minimal_text(events: list[dict], app_url: str = "") -> str:
    n = len(events)
    high = sum(1 for e in events if e["severity"] == "high")
    return f"Coach: {plural(n)}" + (f" ({high} high)" if high else "") + f". {open_the_app(app_url)}"


def summary_text(events: list[dict], app_url: str = "", max_lines: int = 5) -> str:
    head = minimal_text(events, "").rsplit(". Open the app.", 1)[0] + "."
    lines = []
    ordered = sorted(events, key=lambda e: (-RANK[e["severity"]], e["kind"]))
    for e in ordered[:max_lines]:
        line = f"- {KIND_LABEL.get(e['kind'], 'Alert')} ({e['severity']})"
        amt = (e.get("payload") or {}).get("amount_c")
        if e["kind"] in AMOUNT_KINDS and isinstance(amt, int) and round_amount(amt):
            line += f", about {round_amount(amt)} EUR"
        lines.append(line)
    if len(ordered) > max_lines:
        lines.append(f"- and {len(ordered) - max_lines} more")
    return head + "\n" + "\n".join(lines) + "\n" + open_the_app(app_url)


# ---------------------------------------------------------------- the guard

def external_guard(cfg, con):
    """The PrivacyGuard of the finance tools, plus the employer / place terms derived from the data. None when it cannot be built."""
    try:
        from coach.analytics import identity
        from coach.mcp.guard import PrivacyGuard
        from coach.memory.store import MemoryStore
        store = MemoryStore(cfg.memory_dir, history=False, source="alerts")
        try:
            extra = identity.derive_terms(con, store).all_terms()
        except Exception:                                                       # noqa: BLE001
            extra = []
        try:                                                                    # the banks: not people, but they say where the money is
            extra += [r[0] for r in con.execute("SELECT DISTINCT aspsp_name FROM sessions WHERE aspsp_name IS NOT NULL")]
            extra += [r[0] for r in con.execute("SELECT DISTINCT bank FROM accounts WHERE bank IS NOT NULL")]
        except Exception:                                                       # noqa: BLE001
            pass
        return PrivacyGuard(store, con, cfg, extra_terms=extra)
    except Exception:                                                           # noqa: BLE001
        return None


def violations(text: str, guard) -> list[str]:
    if guard is None:
        return ["privacy guard unavailable"]
    return guard.violations(text)


def compose_external(events: list[dict], detail: str, guard, app_url: str = "", title: Optional[str] = None) -> Optional[Message]:
    """The message of ntfy / e-mail / Telegram, or None when even the minimal text is refused."""
    high = any(e["severity"] == "high" for e in events)
    ttl = title or "Coach alerts"
    minimal = Message(ttl, minimal_text(events, app_url), "high" if high else "default", ["bell"], "minimal")
    if detail == "summary":
        text = summary_text(events, app_url)
        bad = violations(text, guard)
        if not bad:
            return Message(ttl, text, minimal.priority, minimal.tags, "summary")
        minimal.note = "the summary was refused by the privacy filter: sent as minimal"
    # the minimal text is fixed vocabulary + two numbers: the guard only fails closed when it could not be built or the URL looks bad
    if guard is not None and violations(minimal.body, guard):
        return None
    return minimal


def mask_local(text: str) -> str:
    from coach.mcp.guard import EMAIL_RE, IBAN_RE
    text = IBAN_RE.sub("[iban]", text)
    text = EMAIL_RE.sub("[e-mail]", text)
    return LONG_NUMBER.sub("[number]", text)


def compose_local(events: list[dict], max_lines: int = 3) -> Message:
    """The macOS notification: more detail than an external message (it stays on this machine), never a full IBAN."""
    high = sum(1 for e in events if e["severity"] == "high")
    title = f"Coach: {plural(len(events))}" + (f" ({high} high)" if high else "")
    lines = [mask_local(e["title"])[:90] for e in sorted(events, key=lambda e: -RANK[e["severity"]])[:max_lines]]
    if len(events) > max_lines:
        lines.append(f"and {len(events) - max_lines} more")
    return Message(title, "\n".join(lines), "high" if high else "default", [], "local")


# ---------------------------------------------------------------- the sample of `coach alerts test-channel`

def sample_events() -> list[dict]:
    """Invented events (no data of the household) so a test message has the exact shape of a real one."""
    return [{"id": "alr_sample000001", "kind": "unusual_charge", "severity": "high", "title": "Sample: unusual charge",
             "body": "", "payload": {"amount_c": 15000}},
            {"id": "alr_sample000002", "kind": "consent", "severity": "medium", "title": "Sample: consent expiring",
             "body": "", "payload": {}}]
