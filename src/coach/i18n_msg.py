"""Server text the web app can translate: a CODE and raw PARAMETERS next to the English sentence (i18n step 4, docs/i18n.md "Server text").

The API keeps sending its English sentence (the CLI, the MCP finance tools read by the coach, stored rows and the macOS notification use it
as it is). Next to it, a field the web app translates::

    {"note": "3 account(s) left out", "note_msg": {"code": "forecast.accountsLeftOut", "params": {"count": 3}, "text": "3 account(s) left out"}}

* ``code``: ``<area>.<name>``, the key of the sentence in the web namespace ``server`` (``web/src/locales/<lang>/server.json``).
* ``params``: RAW values, never formatted: the web formats them for the reader's language. The TYPE of a param is given by its NAME:

  ========================  ==========================================  ==========================
  name                      value                                       the web shows it with
  ========================  ==========================================  ==========================
  ``count``                 an integer (also picks the plural form)     the number
  ``*_date``                ISO date ``YYYY-MM-DD``                     ``fmtDate``
  ``*_month``               ``YYYY-MM``                                 ``fmtMonth``
  ``*_amount``              decimal string ``"-12.34"`` (EUR)           ``fmtMoney``
  ``*_pct``                 a ratio (``0.12`` = 12 %)                    ``fmtPct``
  ``*_category``            a category id ``group.leaf``                ``catLabel``
  ``*_group``               a category group id                         ``groupLabel``
  anything else             a string or a number, shown as it is        -
  ========================  ==========================================  ==========================

* ``text``: the English sentence, shown when the web does not know the code (an older web app, a code added later).

Lists of sentences use the same field name with the same order: ``"notes": [...]`` and ``"notes_msg": [{...}, ...]``. A FIXED
vocabulary (an alert kind, a balance type, a setup step) needs no message: the code is already a field of the payload, and the web
looks up ``labels.<family>.<code>`` in the same namespace, the English label being the fallback.

:func:`server_msg` checks the shape (a wrong param type is a programming error, raised at once). ``tests/test_i18n_msg.py`` checks that
every code built with a literal in ``src/coach`` exists in the English ``server.json``.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

CODE_RE = re.compile(r"^[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)+$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
AMOUNT_RE = re.compile(r"^-?\d+(\.\d+)?$")
CATEGORY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
GROUP_RE = re.compile(r"^[a-z0-9_]+$")
SUFFIXES = ("_date", "_month", "_amount", "_pct", "_category", "_group")


class MessageError(ValueError):
    """A message that does not follow the convention (a programming error)."""


def _param(name: str, v: Any) -> Any:
    if not PARAM_RE.match(name):
        raise MessageError(f"param name {name!r}: lower snake case")
    if v is None:
        return None
    if name == "count":
        if isinstance(v, bool) or not isinstance(v, int):
            raise MessageError("count: an integer")
        return v
    if name.endswith("_date"):
        if isinstance(v, dt.datetime):
            v = v.date()
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, str) and DATE_RE.match(v):
            return v
        raise MessageError(f"{name}: an ISO date YYYY-MM-DD")
    if name.endswith("_month"):
        if isinstance(v, dt.date):
            return f"{v.year:04d}-{v.month:02d}"
        if isinstance(v, str) and MONTH_RE.match(v):
            return v
        raise MessageError(f"{name}: a month YYYY-MM")
    if name.endswith("_amount"):
        if isinstance(v, Decimal):
            v = str(v)
        if isinstance(v, str) and AMOUNT_RE.match(v):
            return v
        raise MessageError(f"{name}: a decimal string (money_str of cents), not {type(v).__name__}")
    if name.endswith("_pct"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise MessageError(f"{name}: a ratio (0.12 = 12 %)")
        return v
    if name.endswith("_category"):
        if isinstance(v, str) and CATEGORY_RE.match(v):
            return v
        raise MessageError(f"{name}: a category id group.leaf")
    if name.endswith("_group"):
        if isinstance(v, str) and GROUP_RE.match(v):
            return v
        raise MessageError(f"{name}: a category group id")
    if isinstance(v, (str, int, float)) and not isinstance(v, bool):
        return v
    raise MessageError(f"{name}: a string or a number")


def server_msg(code: str, text: str, **params: Any) -> dict:
    """``{"code", "params", "text"}``: a sentence the web translates (``code`` in ``server.json``), ``text`` the English fallback."""
    if not CODE_RE.match(code or ""):
        raise MessageError(f"code {code!r}: <area>.<name>, lower camel case parts")
    if not isinstance(text, str) or not text:
        raise MessageError("text: the English sentence")
    return {"code": code, "params": {k: _param(k, v) for k, v in params.items()}, "text": text}


def is_msg_key(key: object) -> bool:
    """A ``<field>_msg`` sibling (or a list of them): what only the web app reads."""
    return isinstance(key, str) and key.endswith("_msg")


def strip_msgs(obj: Any) -> Any:
    """``obj`` without its ``*_msg`` keys, at any depth. The MCP finance tools (read by a model) and the CLI's ``--json`` keep the English
    sentences only: a message's params can hold raw labels the redaction walks differently, and the model does not need them."""
    if isinstance(obj, dict):
        return {k: strip_msgs(v) for k, v in obj.items() if not is_msg_key(k)}
    if isinstance(obj, list):
        return [strip_msgs(v) for v in obj]
    return obj
