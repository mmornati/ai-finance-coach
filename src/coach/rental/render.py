"""JSON-safe rendering of the rental results: an integer in a key ending ``_c`` is cents and becomes a decimal string under the key without
the suffix (``rent_c`` -> ``rent``), dataclasses and dates are flattened. The one rule of ``analytics.common``, for dicts too."""
from __future__ import annotations

import datetime as dt
from dataclasses import fields, is_dataclass

from coach.analytics.common import money_str


def plain(v):
    if is_dataclass(v) and not isinstance(v, type):
        return plain({f.name: getattr(v, f.name) for f in fields(v)})
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            k = str(k)
            if k.endswith("_c") and (x is None or (isinstance(x, int) and not isinstance(x, bool))):
                out[k[:-2]] = money_str(x)
            elif k.endswith("_c") and isinstance(x, (list, tuple)) and all(isinstance(i, int) for i in x):
                out[k[:-2]] = [money_str(i) for i in x]
            elif k.endswith("_c") and isinstance(x, dict):                     # a group of amounts (average_c: {rent_c, costs_c ...})
                out[k[:-2]] = plain(x)
            else:
                out[k] = plain(x)
        return out
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return [plain(x) for x in sorted(v)]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 4)
    return v
