"""The alternatives store (E8-4): cheaper offers found for a recurring service, each with its source URL and the date the price
was SEEN. Written by the user (CLI / web app, method ``manual``) or by the ``find-cheaper`` skill through the MCP tool
``alternatives_record`` (source ``coach-llm``, method ``find-cheaper``). Table ``alternatives`` (migration 0014).

* A quote older than 30 days is "outdated, re-check": it is still listed but never the "current best" and never presented as a
  current price.
* ``savings`` are computed by code (:func:`coach.skills.savings.savings_estimate`), never by a model.
* Validation (the same for every writer): price > 0, retrieved_at a date not in the future, a source URL that is https (required
  from the coach path), provider / offer present, bounded text.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import secrets as _secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from coach.i18n_msg import server_msg
from coach.skills.money import cents
from coach.skills.savings import savings_estimate

MAX_AGE_DAYS = 30
METHODS = ("find-cheaper", "manual")
SOURCES = ("cli", "ui", "coach-llm")
ID_PREFIX = "alt_"
COLS = ("id", "contract_id", "series_id", "provider", "offer_name", "monthly_price_c", "switching_costs_c", "features", "source_url",
        "retrieved_at", "method", "notes", "source", "created_at")


class AlternativeError(ValueError):
    pass


@dataclass
class Alternative:
    id: str
    contract_id: Optional[str]
    series_id: Optional[str]
    provider: str
    offer_name: str
    monthly_price_c: int
    switching_costs_c: int
    features: Optional[str]
    source_url: Optional[str]
    retrieved_at: dt.date
    method: str
    notes: Optional[str]
    source: str
    created_at: str


def _row(r) -> Alternative:
    d = dict(zip(COLS, r))
    d["retrieved_at"] = dt.date.fromisoformat(d["retrieved_at"])
    return Alternative(**d)


def check_url(url: Optional[str], *, required: bool = False) -> Optional[str]:
    """An https page on a real host: no user-info, no port games, no localhost / IP literal / .local / .internal."""
    if url is None or not str(url).strip():
        if required:
            raise AlternativeError("a source URL (https) is required: say where the price was seen")
        return None
    url = str(url).strip()
    if len(url) > 500 or any(c.isspace() or ord(c) < 32 for c in url):
        raise AlternativeError("the source URL is not valid")
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
    except ValueError:
        raise AlternativeError("the source URL is not valid") from None
    if u.scheme != "https":
        raise AlternativeError("the source URL must start with https://")
    if not host or u.username or u.password or "." not in host or host.endswith((".local", ".internal", ".localhost", ".lan", ".home")):
        raise AlternativeError("the source URL must be a public https page")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise AlternativeError("the source URL must use a host name, not an IP address")
    return url


def _text(v: Optional[str], what: str, limit: int, required: bool = False) -> Optional[str]:
    v = " ".join(str(v or "").split())
    if not v:
        if required:
            raise AlternativeError(f"{what} is required")
        return None
    if len(v) > limit:
        raise AlternativeError(f"{what} is longer than {limit} characters")
    return v


def validate(*, provider, offer_name, monthly_price, retrieved_at, source_url=None, features=None, notes=None, switching_costs=0,
             method="manual", source="cli", today: dt.date, require_url: bool = False) -> dict:
    """-> the cleaned fields (price in cents). Raises :class:`AlternativeError`."""
    if method not in METHODS:
        raise AlternativeError(f"method must be one of {', '.join(METHODS)}")
    if source not in SOURCES:
        raise AlternativeError(f"source must be one of {', '.join(SOURCES)}")
    try:
        price_c = cents(monthly_price)
        sw_c = cents(switching_costs or 0)
    except Exception:                                                     # noqa: BLE001
        raise AlternativeError("the price must be a number") from None
    if price_c <= 0:
        raise AlternativeError("the monthly price must be greater than 0")
    if price_c > 10_000_000:
        raise AlternativeError("the monthly price is implausibly large")
    if sw_c < 0 or sw_c > 10_000_000:
        raise AlternativeError("switching costs must be between 0 and 100000")
    if isinstance(retrieved_at, str):
        try:
            retrieved_at = dt.date.fromisoformat(retrieved_at)
        except ValueError:
            raise AlternativeError("retrieved_at must be a date YYYY-MM-DD") from None
    if not isinstance(retrieved_at, dt.date):
        raise AlternativeError("retrieved_at (the date the price was seen) is required")
    if retrieved_at > today:
        raise AlternativeError("retrieved_at is in the future")
    if (today - retrieved_at).days > 3650:
        raise AlternativeError("retrieved_at is more than ten years ago")
    return {"provider": _text(provider, "provider", 80, True), "offer_name": _text(offer_name, "offer name", 120, True),
            "monthly_price_c": price_c, "switching_costs_c": sw_c, "features": _text(features, "features", 400),
            "source_url": check_url(source_url, required=require_url), "retrieved_at": retrieved_at, "method": method,
            "notes": _text(notes, "notes", 500), "source": source}


def add(con, *, contract_id: Optional[str] = None, series_id: Optional[str] = None, today: dt.date, require_url: Optional[bool] = None,
        **fields) -> Alternative:
    if not (contract_id or series_id):
        raise AlternativeError("say which subscription the offer is an alternative to (a contract id or a series id)")
    if require_url is None:
        require_url = fields.get("source") == "coach-llm"
    v = validate(today=today, require_url=require_url, **fields)
    aid = ID_PREFIX + _secrets.token_hex(5)
    con.execute("INSERT INTO alternatives(id, contract_id, series_id, provider, offer_name, monthly_price_c, switching_costs_c, "
                "features, source_url, retrieved_at, method, notes, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, contract_id, series_id, v["provider"], v["offer_name"], v["monthly_price_c"], v["switching_costs_c"], v["features"],
                 v["source_url"], v["retrieved_at"].isoformat(), v["method"], v["notes"], v["source"],
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return get(con, aid)


def get(con, aid: str) -> Optional[Alternative]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM alternatives WHERE id=?", (aid,)).fetchone()
    return _row(r) if r else None


def load(con, *, contract_id: Optional[str] = None, series_id: Optional[str] = None) -> list[Alternative]:
    q, a = f"SELECT {', '.join(COLS)} FROM alternatives", []
    conds = []
    if contract_id:
        conds.append("contract_id=?")
        a.append(contract_id)
    if series_id:
        conds.append("series_id=?")
        a.append(series_id)
    if conds:
        q += " WHERE " + " OR ".join(conds)
    return [_row(r) for r in con.execute(q + " ORDER BY retrieved_at DESC, id", a)]


def remove(con, aid: str) -> bool:
    n = con.execute("DELETE FROM alternatives WHERE id=?", (aid,)).rowcount
    con.commit()
    return n > 0


def count_by_source(con, source: str) -> int:
    return con.execute("SELECT COUNT(*) FROM alternatives WHERE source=?", (source,)).fetchone()[0]


def assess(alt: Alternative, current_monthly_c: Optional[int], today: dt.date, months: int = 12) -> dict:
    """One alternative against the current monthly cost: age, staleness and the savings computed by code."""
    from coach.analytics.common import money_str
    age = (today - alt.retrieved_at).days
    stale = age > MAX_AGE_DAYS
    out = {"id": alt.id, "provider": alt.provider, "offer": alt.offer_name, "monthly_price": money_str(alt.monthly_price_c),
           "switching_costs": money_str(alt.switching_costs_c), "features": alt.features, "source_url": alt.source_url,
           "retrieved_at": alt.retrieved_at, "age_days": age, "method": alt.method, "notes": alt.notes, "source": alt.source,
           "status": "outdated" if stale else "current",
           "label": "outdated, re-check" if stale else f"seen {age} day(s) ago", "stale": stale, "savings": None,
           "_net_c": None}
    if current_monthly_c is not None and current_monthly_c > 0:
        est = savings_estimate(current_monthly_c / 100, alt.monthly_price_c / 100, alt.switching_costs_c / 100, months)
        out["savings"] = {"monthly": money_str(est.monthly_saving_c), "yearly": money_str(est.yearly_saving_c),
                          f"net_{months}m": money_str(est.net_saving_c), "break_even_months": est.break_even_months,
                          "verdict": est.verdict, "computed_by": "code (savings_estimate)",
                          "stale_warning": "price older than 30 days: re-check before relying on it" if stale else None}
        out["_net_c"] = est.net_saving_c
    return out


def summarize(alts: list[Alternative], current_monthly_c: Optional[int], today: dt.date) -> dict:
    """The alternatives of one subscription: every item assessed, plus the CURRENT BEST (a fresh quote with the highest net saving
    over 12 months; an outdated quote is never the best)."""
    items = [assess(a, current_monthly_c, today) for a in alts]
    fresh = [i for i in items if not i["stale"] and i["_net_c"] is not None and i["_net_c"] > 0]
    best = max(fresh, key=lambda i: (i["_net_c"], i["id"]), default=None)
    for i in items:
        i.pop("_net_c")
        if i["savings"] and i["savings"]["stale_warning"]:        # the web's message (assess itself stays English: the MCP tool returns it)
            i["savings"]["stale_warning_msg"] = STALE_WARNING_MSG
    best_pub = None
    if best is not None:
        best_pub = next(i for i in items if i["id"] == best["id"])
    return {"count": len(items), "current": sum(1 for i in items if not i["stale"]), "outdated": sum(1 for i in items if i["stale"]),
            "best": best_pub, "items": items, "note": SUMMARY_NOTE["text"], "note_msg": SUMMARY_NOTE}


STALE_WARNING_MSG = server_msg("subs.alternatives.staleWarning", "price older than 30 days: re-check before relying on it")
SUMMARY_NOTE = server_msg("subs.alternatives.note", "quotes older than 30 days are outdated and excluded from the best; prices change: "
                                                    "verify on the provider's page before acting")
