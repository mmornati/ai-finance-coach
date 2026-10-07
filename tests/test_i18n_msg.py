"""Server text for the web app's translations (i18n step 4): the message helper, and the codes the web must know.

Synthetic values only. The web side of the convention is tested in web/src/i18n/server.test.ts."""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from coach.i18n_msg import MessageError, server_msg

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "locales"


def _bundle(lang: str, ns: str) -> dict:
    return json.loads((LOCALES / lang / f"{ns}.json").read_text(encoding="utf-8"))


def _has(bundle: dict, key: str) -> bool:
    """`a.b.c` (or its plural forms `a.b.c_one` / `_other`) is a string of the bundle."""
    *path, last = key.split(".")
    node = bundle
    for p in path:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return False
    return isinstance(node, dict) and (isinstance(node.get(last), str) or isinstance(node.get(f"{last}_other"), str))


# ---------------------------------------------------------------- the helper

def test_a_message_carries_the_code_raw_params_and_the_english_text():
    m = server_msg("forecast.lowest", "Lowest expected -12.34 EUR on 2026-10-05.", count=2, low_date=dt.date(2026, 10, 5),
                   start_month="2026-09", low_amount="-12.34", change_pct=0.125, top_category="food.groceries", top_group="food",
                   who="adult-1", n=3)
    assert m == {"code": "forecast.lowest", "text": "Lowest expected -12.34 EUR on 2026-10-05.",
                 "params": {"count": 2, "low_date": "2026-10-05", "start_month": "2026-09", "low_amount": "-12.34", "change_pct": 0.125,
                            "top_category": "food.groceries", "top_group": "food", "who": "adult-1", "n": 3}}
    assert json.loads(json.dumps(m)) == m                                           # plain JSON, nothing formatted


def test_dates_months_and_decimals_are_normalised():
    m = server_msg("a.b", "x", end_date=dt.datetime(2026, 1, 2, 10, 30), first_month=dt.date(2026, 3, 9), cap_amount=Decimal("10.50"),
                   gone_date=None, gap_num=Decimal("2.40"), n_num=2.4, k_num=3)
    assert m["params"] == {"end_date": "2026-01-02", "first_month": "2026-03", "cap_amount": "10.50", "gone_date": None, "gap_num": "2.40",
                           "n_num": 2.4, "k_num": 3}


@pytest.mark.parametrize("params", [
    {"count": "2"}, {"count": True}, {"due_date": "05/10/2026"}, {"due_month": "2026-13"}, {"rent_amount": 1234},
    {"rent_amount": 12.5}, {"rent_amount": "12,50"}, {"rise_pct": "12%"}, {"gap_num": "2,4"}, {"gap_num": True}, {"gap_num": "two"}, {"top_category": "groceries"}, {"top_group": "food.groceries"},
    {"label": ["a"]}, {"label": True}, {"BadName": "x"},
])
def test_a_param_of_the_wrong_type_is_refused(params):
    with pytest.raises(MessageError):
        server_msg("area.name", "English", **params)


@pytest.mark.parametrize("code", ["", "nodot", "Area.name", "area.", "area.na-me", "area.name.", "area..name"])
def test_a_code_is_area_dot_name(code):
    with pytest.raises(MessageError):
        server_msg(code, "English")


def test_the_english_text_is_required():
    with pytest.raises(MessageError):
        server_msg("area.name", "")


# ---------------------------------------------------------------- what the web must know

def test_every_literal_code_built_in_the_package_exists_in_the_english_server_namespace():
    en = _bundle("en", "server")
    used = set()
    for p in (ROOT / "src" / "coach").rglob("*.py"):
        # server_msg(...), server_msg_or_none(...) and the coverage notes' analytics.common.note(...)
        used |= set(re.findall(r"\b(?:server_msg|server_msg_or_none|note)\(\s*\"([a-zA-Z0-9_.]+)\"", p.read_text(encoding="utf-8")))
    assert "balances.oneBalance" in used and "balances.mixedTypes" in used     # the guard sees the real calls
    assert "coverage.nonEur" in used and "coverage.incompleteMonths" in used
    missing = sorted(c for c in used if not _has(en, c))
    assert not missing, f"add these codes to web/src/locales/en/server.json (and fr, it): {missing}"


def _label_families() -> dict[str, set]:
    from coach.alerts.messages import KIND_LABEL
    from coach.api.views import BALANCE_TYPE
    from coach.setup.wizard import TITLES
    from coach.subs.inventory import GROUP_LABEL
    from coach.ingest.accounts import PURPOSES
    onboarding = set(re.findall(r"\"id\": \"([a-z_]+)\", \"heading\"", (ROOT / "src/coach/skills/onboarding.py").read_text(encoding="utf-8")))
    assert len(onboarding) == 7
    return {"alertKind": set(KIND_LABEL), "subsGroup": set(GROUP_LABEL), "balanceType": set(BALANCE_TYPE), "setupStep": set(TITLES),
            "onboardingStep": onboarding, "accountPurpose": set(PURPOSES)}


@pytest.mark.parametrize("lang", ["en", "fr", "it"])
def test_every_code_of_a_label_map_has_a_web_label(lang):
    """The API sends the code (an alert kind, a subscription group, a balance type, a setup / onboarding step, a purpose) next to its
    English label; the web translates `labels.<family>.<code>`. A new code needs its label in every language."""
    labels = _bundle(lang, "server")["labels"]
    for family, codes in _label_families().items():
        missing = sorted(codes - set(labels[family]))
        assert not missing, f"{lang} labels.{family}: {missing}"
    assert {"household", "accountUnresolved"} <= set(labels["forecast"])


def test_the_english_web_labels_match_the_server_labels():
    """The English label of the web is the server's own label: the two cannot drift."""
    from coach.alerts.messages import KIND_LABEL
    from coach.api.views import BALANCE_TYPE
    from coach.setup.wizard import TITLES
    from coach.subs.inventory import GROUP_LABEL
    labels = _bundle("en", "server")["labels"]
    for family, mapping in (("alertKind", KIND_LABEL), ("subsGroup", GROUP_LABEL), ("balanceType", BALANCE_TYPE), ("setupStep", TITLES)):
        assert {k: labels[family][k] for k in mapping} == mapping, family


def test_the_net_worth_generic_names_have_a_web_label():
    """`loans.networth.GENERIC_NAME` (what the coach reads) has its web counterpart in the item form's kind options (common namespace)."""
    from coach.loans.networth import GENERIC_NAME
    for lang in ("en", "fr", "it"):
        opts = _bundle(lang, "common")["itemForm"]["option"]
        known = set(opts["assetKind"]) | set(opts["liabilityKind"])
        assert not sorted(set(GENERIC_NAME) - known), lang


def test_the_forecast_flags_are_codes():
    """A forecast flag is a code, or `code:value` (one number): the web translates `labels.forecastFlag.<code>`."""
    src = (ROOT / "src/coach/analytics/forecast.py").read_text(encoding="utf-8")
    flags = set(re.findall(r"(?:fl|flags|hh_flags)\.append\(\"([a-z_]+)\"\)", src)) | set(re.findall(r"f\"([a-z_]+):\{", src))
    assert "accounts_without_balance" in flags and "at_risk" in flags
    labels = _bundle("en", "server")["labels"]["forecastFlag"]
    assert not sorted(f for f in flags if f not in labels and f"{f}_other" not in labels)


def test_the_category_taxonomy_has_a_name_in_every_language():
    """Every group and leaf of classify/taxonomy.yaml has a name in the web's `taxonomy` namespace (the id stays the truth)."""
    import yaml
    tax = yaml.safe_load((ROOT / "src/coach/classify/taxonomy.yaml").read_text(encoding="utf-8"))
    for lang in ("en", "fr", "it"):
        b = _bundle(lang, "taxonomy")
        assert set(b["group"]) == set(tax), lang
        for g, leaves in tax.items():
            assert set(b["cat"][g]) == set(leaves), (lang, g)
