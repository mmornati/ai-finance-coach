"""Explicit redaction of the skills' outputs.

The skills compute on the real dataset (real merchant names, account uids, transaction keys) and name their free-text and
reference fields with a small fixed vocabulary; :func:`redact` rewrites exactly those fields with the SAME redactor every
other finance tool uses (``analytics.privacy.Redactor``): merchant text goes through ``red.text``, transaction keys become
hashed ``h_`` refs, account uids become pseudonyms. Everything else (numbers, dates, enums, rule text written by this code)
is left alone, and the tool choke point (``mcp.tools.ToolSession._publish``) still wraps untrusted text, pseudonymises memory
ids and runs the final privacy assertion on the result.
"""
from __future__ import annotations

TEXT_KEYS = {"entity", "entities", "merchant", "subject", "provider", "lender", "event", "label", "asset"}
REF_KEYS = {"ref", "tx_key"}                       # a transaction key (or an already-public rec_ / anm_ / chg_ id)
REFS_KEYS = {"evidence", "refs"}                   # lists of those
ACCOUNT_KEYS = {"account"}
ACCOUNTS_KEYS = {"accounts", "missing_accounts"}
PUBLIC_PREFIXES = ("rec_", "anm_", "chg_", "h_")


def _ref(red, v: str) -> str:
    return v if v.startswith(PUBLIC_PREFIXES) else red.tx(v)


def redact(obj, red):
    """A copy of `obj` with entity text, transaction keys and account uids rewritten for a model."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in REF_KEYS and isinstance(v, str):
                out[k] = _ref(red, v)
            elif k in REFS_KEYS and isinstance(v, list):
                out[k] = [_ref(red, x) if isinstance(x, str) else x for x in v]
            elif k in ACCOUNT_KEYS and isinstance(v, str):
                out[k] = red.account.get(v, red.text(v))
            elif k in ACCOUNTS_KEYS and isinstance(v, list):
                out[k] = [red.account.get(x, red.text(x)) if isinstance(x, str) else x for x in v]
            elif k in TEXT_KEYS and isinstance(v, str):
                out[k] = red.text(v)
            elif k in TEXT_KEYS and isinstance(v, list):
                out[k] = [red.text(x) if isinstance(x, str) else redact(x, red) for x in v]
            else:
                out[k] = redact(v, red)
        return out
    if isinstance(obj, list):
        return [redact(x, red) for x in obj]
    return obj
