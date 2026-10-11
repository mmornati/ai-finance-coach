"""One AI suggestion for one merchant of the review queue (E2-9 in the web app).

The suggestion is a PROPOSAL: nothing is stored but the usage of the call. The user accepts it with the review queue's own "Set" (a
`user` label), or ignores it. The request is the one `classify enrich` (web search) or `classify run` (the classifier) would send for this
merchant, built by the same code: the same redaction, the same person guard, the same town stripping, the same egress gates. A web search
is used when it is allowed (``[privacy] web_enrich``, the claude-code backend) and the merchant is a shop; otherwise the classifier answers
without the web; a merchant that may be a person is never sent at all.

Split in three steps so that a background job holds no database connection while the model works: :func:`plan` (reads the database,
builds the redacted request), :func:`ask` (the call, no database), and the caller records the usage (``backends.record_usage``).
"""
from __future__ import annotations

from dataclasses import dataclass

from coach import egress
from coach.classify.backends import LLMBackend, Usage, default_model, get_backend
from coach.classify.candidates import (enrich_candidates, hold_back, household_names, known_merchants, known_places, prompt_leaks,
                                       search_item, search_places)
from coach.classify.llm import prepare_jobs, validate_results, web_request
from coach.classify.rules import rule_category

EVIDENCE_MAX = 300


class SuggestRefused(Exception):
    """The merchant is not sent, for a reason the page shows: `code` is stable (i18n key), the message is English."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Plan:
    key: str
    mode: str                  # 'web' (the model searches the web) | 'label' (the classifier, no web)
    model: str
    request: dict              # the `complete` keyword arguments: static, dynamic, schema, model, purpose, items, web_search


def web_status(cfg) -> tuple[bool, str, str]:
    """(allowed, code, why) of a web search from the review queue, without calling anything."""
    ok, code, why = egress.evaluate("llm.claude-code", {"web_search": True}, cfg=cfg)
    if not ok:
        return False, code, why
    if cfg.llm_backend != "claude-code":
        return False, "backend_no_web", f"web search needs the claude-code backend; llm.backend is {cfg.llm_backend!r}"
    return True, "ok", ""


def label_status(cfg) -> tuple[bool, str, str]:
    """(allowed, code, why) of a classifier call (no web) with the configured `[llm]` backend."""
    from coach.api.coachjobs import availability
    ok, why = availability(cfg, cfg.llm_backend, cfg.llm_openai_model, section="llm")
    return (True, "ok", "") if ok else (False, "backend_unavailable", why)


def _facts(con, key: str) -> tuple[str | None, set[str]]:
    row = con.execute("SELECT MAX(merchant_raw), GROUP_CONCAT(DISTINCT tx_type) FROM tx_enriched WHERE merchant_key=?", (key,)).fetchone()
    if row is None or row[1] is None:
        raise SuggestRefused("unknown_merchant", "no transaction has this merchant key")
    return row[0], set(row[1].split(",")) - {""}


def plan(con, cfg, key: str, backend: LLMBackend | None = None) -> Plan:
    """The redacted request for `key`, or SuggestRefused. `backend`: the configured one when None (tests inject a fake)."""
    if rule_category(key):
        raise SuggestRefused("decided_by_rule", "a classification rule decides this merchant")
    raw, types = _facts(con, key)
    family, first = household_names(con)
    places = known_places(con, cfg.memory_dir)
    allow = cfg.llm_allowlist
    if hold_back(key, raw, types, family, first, known_merchants(con), allow, places=places):
        raise SuggestRefused("person_like", "this merchant may be a person: it is never sent to an AI; choose its category yourself")
    web_ok, _, _ = web_status(cfg)
    if web_ok and types <= {"card", "card_refund"}:
        ok, _held = enrich_candidates(con, [key], allow, cfg.memory_dir)
        if ok:
            static, _, _ = web_request([], family)
            if prompt_leaks(static, places, family):           # an instruction that names the household: never sent
                raise SuggestRefused("prompt_leak", "the web-search prompt contains household names or places: refusing to send it")
            backend = backend or get_backend(cfg)
            if backend.supports_web_search:
                model = default_model(cfg, backend)
                static, dynamic, schema = web_request([search_item(con, 0, key, search_places(con, cfg.memory_dir))], family)
                return Plan(key, "web", model, dict(static=static, dynamic=dynamic, schema=schema, model=model, purpose="enrich",
                                                    items=1, web_search=True))
    label_ok, _, why = label_status(cfg)
    if not label_ok:
        raise SuggestRefused("backend_unavailable", why)
    backend = backend or get_backend(cfg)
    model = default_model(cfg, backend)
    index = None
    if cfg.knn_enabled:
        from coach.classify import knn as knn_mod
        index = knn_mod.build_index(con, allow)
    (_, job), = prepare_jobs(con, [key], model, 1, index, cfg.knn_examples, family, allow, places=places)
    return Plan(key, "label", model, job)


def ask(p: Plan, backend: LLMBackend) -> tuple[dict | None, Usage]:
    """The call. -> (suggestion or None when the answer was unusable, usage). The suggestion: {key, mode, model, merchant, category,
    confidence, evidence}; `evidence` (web mode) is the model's own note, untrusted text to show as text."""
    data, usage = backend.complete(**p.request)
    raw = data.get("results") if isinstance(data, dict) else None
    ok = validate_results(raw, 1)
    if not ok:
        return None, usage
    r = ok[0]
    evidence = None
    if p.mode == "web":
        first = next((x for x in raw if isinstance(x, dict) and x.get("id") == 0), {})
        ev = first.get("evidence")
        evidence = ev.strip()[:EVIDENCE_MAX] if isinstance(ev, str) and ev.strip() else None
    return {"key": p.key, "mode": p.mode, "model": p.model, "merchant": r["merchant"], "category": r["category"],
            "confidence": round(r["confidence"], 2), "evidence": evidence}, usage
