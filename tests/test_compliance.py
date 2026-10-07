"""E11-5: AI-generated labels, the investment-advice compliance check (FR / IT / EN), the central disclaimers, the system prompt rules."""
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

from apihelpers import api, ctx, world  # noqa: F401
from coach import compliance as C, disclaimers as D
from coach.agent import insights as I, prompt as P
from coach.agent.runner import RunResult
from coach.agent import commands as agent_commands
from coach.classify.backends import Usage
from coach.db import connect
from test_api_coach import WHICH, fake_runner, parse  # noqa: F401

# ---------------------------------------------------------------- the check: true positives


@pytest.mark.parametrize("text,code", [
    # ISIN (valid check digits)
    ("Look at IE00B4L5Y983 for exposure.", "isin"),
    ("Votre fonds FR0000120271 a baissé.", "isin"),
    ("Il titolo US0378331005 è in rialzo.", "isin"),
    # named products / issuers / tickers
    ("An iShares fund tracks it.", "product_name"),
    ("Pensez à Amundi ou Lyxor.", "product_name"),
    ("Un tracker sur le MSCI World est courant.", "product_name"),
    ("Consider VWCE or CW8 for the long run.", "product_name"),
    ("Hai pensato al Bitcoin?", "product_name"),
    # recommendations: EN
    ("You should invest in an ETF every month.", "recommendation"),
    ("I recommend buying shares of a broad index.", "recommendation"),
    ("Buy a global equity fund and hold it.", "recommendation"),
    ("Put your money in bonds.", "recommendation"),
    # FR
    ("Vous devriez placer cet argent sur un placement à rendement élevé.", "recommendation"),
    ("Investissez dans des actions européennes.", "recommendation"),
    ("Achetez des SCPI pour vos revenus.", "recommendation"),
    ("Je vous conseille d'acheter un fonds indiciel.", "recommendation"),
    ("Il faut placer 200 euros par mois.", "recommendation"),
    # IT
    ("Dovresti investire in fondi a basso costo.", "recommendation"),
    # review round: advice verbs + products, "mettez ... dans", Italian government bonds
    ("Je vous conseille un ETF World.", "recommendation"),
    ("Consider a low-cost index fund.", "recommendation"),
    ("Mettez 200 € par mois dans des actions Air Liquide.", "recommendation"),
    ("Puoi comprare BTP Italia o buoni postali.", "recommendation"),
    ("Hai considerato un PEA investito in azioni?", "recommendation"),
    ("An iShares ETF tracks it.", "product_name"),
    ("Le fonds Amundi MSCI World est populaire.", "product_name"),
    ("Ti consiglio di acquistare azioni di aziende solide.", "recommendation"),
    ("Conviene sottoscrivere un'obbligazione.", "recommendation"),
])
def test_investment_product_recommendations_are_flagged(text, code):
    assert code in C.codes(C.check(text)), text


# ---------------------------------------------------------------- true negatives


@pytest.mark.parametrize("text", [
    "Your grocery spending rose 12% in September (h_0123456789); that is 80 EUR more than usual.",
    "You should buy less coffee out and make it at home.",
    "Buy groceries on promotion and plan your meals.",
    "Achetez moins de café au distributeur.",
    "Vous devriez réduire vos dépenses de restaurant.",
    "Dovresti ridurre le spese per il ristorante.",
    "Compra meno cibo da asporto.",
    "Invest in your education: courses are tax deductible.",
    "Your emergency fund covers three months of spending.",
    "The code ABCDEFGHIJK1 is an order reference.",             # 12 characters but not a valid ISIN
    "FR7630006000011234567890189 is an IBAN, not an ISIN.",
    "Votre abonnement à Netflix coûte 13,49 EUR par mois (rec_abcdef0123).",
    "This is general information, not financial advice.",
    "",
    # review round: regulated savings accounts, issuers as context, tickers inside merchant names, "invest time"
    "Vous devriez placer 200 euros sur un livret A ou un LEP.",
    "Placez votre épargne de précaution sur un LDDS.",
    "Votre PEE (géré par Amundi) est alimenté chaque mois.",
    "META VERIFIED 14.99 EUR on 12 Sept (h_0123456789).",
    "AMZN MKTP 23.40 EUR yesterday.",
    "You should invest time in reviewing your budget.",
    "You should invest in your education.",
    "Je vous conseille de réduire les dépenses de café.",
    "I suggest cancelling the unused gym membership.",
    "Consider cancelling Netflix.",
    "Mettez 200 € de côté chaque mois.",
    "Metti da parte 200 euro al mese.",
])
def test_budgeting_talk_is_not_flagged(text):
    assert C.check(text) == [], text


def test_isin_check_digit():
    assert C.isin_valid("IE00B4L5Y983") and C.isin_valid("US0378331005") and C.isin_valid("FR0000120271")
    assert not C.isin_valid("US0378331006") and not C.isin_valid("IE00B4L5Y98") and not C.isin_valid("1234567890AB")


def test_each_flag_carries_a_short_snippet_and_the_check_never_changes_the_text():
    text = "Blah. You should invest in an ETF every month. Blah."
    flags = C.check(text)
    assert flags and all(len(f.snippet) <= 100 for f in flags)
    assert text == "Blah. You should invest in an ETF every month. Blah."


# ---------------------------------------------------------------- language, banner, label


def test_language_detection_and_banner_in_each_language():
    assert C.detect_lang("Vous devriez placer cet argent dans votre épargne pour les mois à venir.") == "fr"
    assert C.detect_lang("Ti consiglio di acquistare azioni per il tuo risparmio.") == "it"
    assert C.detect_lang("You should invest in an ETF every month for your savings.") == "en"
    assert "AMF" in C.banner(["isin"], "fr") and "Consob" in C.banner(["isin"], "it")
    en = C.banner(["isin"], "en")
    assert "General information only" in en and "AMF / CIF" in en and "Consob" in en
    assert C.banner([], "en") == ""


def test_assess_gives_a_screen_everything():
    a = C.assess("You should invest in an ETF every month for your savings.")
    assert a["ai_generated"] and a["flagged"] and a["lang"] == "en" and "AI-generated" in a["label"] and a["banner"]
    assert a["codes"] and a["flags"][0]["snippet"]
    clean = C.assess("Your spending rose 5% in September.")
    assert clean["ai_generated"] and not clean["flagged"] and clean["banner"] == "" and clean["label"]


def test_cli_block_prints_the_label_and_the_banner():
    head, foot = C.cli_block("Investissez dans des actions européennes pour votre épargne.")
    assert "Généré par IA" in head and "AMF" in foot
    head, foot = C.cli_block("Your spending rose 5%.")
    assert "AI-generated" in head and foot == ""


# ---------------------------------------------------------------- the disclaimers module


@pytest.mark.parametrize("key", sorted(D.TEXTS))
def test_every_disclaimer_exists_in_french_italian_and_english(key):
    t = D.TEXTS[key]
    assert set(t) == {"en", "fr", "it"} and all(t[k].strip() for k in t)
    assert D.get(key, "xx") == t["en"] and D.get(key, "FR") == t["fr"]


def test_skills_tools_letters_and_tax_use_the_central_wording():
    from coach.skills import cancel, loans, savings, tax
    from coach.subs import letters
    assert cancel.DISCLAIMER == D.get("contract") and loans.DISCLAIMER == D.get("loan") and savings.DISCLAIMER == D.get("savings")
    assert tax.DISCLAIMER is D.TAX_BY_COUNTRY and "impots.gouv.fr" in tax.DISCLAIMER["FR"] and "Agenzia delle Entrate" in tax.DISCLAIMER["IT"]
    for lang in ("fr", "it", "en"):
        assert letters.NOTES[lang]["verify"] == D.get("letter_verify", lang)


def test_the_banner_and_label_texts_name_the_regulators():
    assert "AMF" in D.get("investment_banner", "fr") and "CIF" in D.get("investment_banner", "fr") or "AMF" in D.get("investment_banner", "fr")
    assert "Consob" in D.get("investment_banner", "it")
    assert "AMF / CIF" in D.get("investment_banner", "en") and "Consob" in D.get("investment_banner", "en")


# ---------------------------------------------------------------- the system prompt


def test_the_system_prompt_rules_cover_products_identity_and_the_disclaimer_in_three_languages():
    s = P.system_prompt(5)
    for needle in ("not a licensed adviser", "ISIN", "ticker", "buy / invest in", "AMF", "Consob",
                   "This is general information, not financial advice.", "Ceci est une information générale", "Questa è un'informazione generale",
                   "flagged"):
        assert needle in s, needle
    assert D.get("general_advice", "en") in s and D.get("general_advice", "fr") in s and D.get("general_advice", "it") in s


# ---------------------------------------------------------------- stored insights


@pytest.fixture
def con(cfg, db_key):
    c = connect(cfg, create=True)
    yield c
    c.close()


def test_an_llm_insight_is_labelled_and_a_flagged_one_is_recorded(con):
    ok = I.add(con, kind="answer", title="Groceries", body="Your grocery spending rose 12% (h_0123456789).", backend="claude-code", model="sonnet")
    bad = I.add(con, kind="answer", title="Where to put savings", body="You should invest in an ETF every month. IE00B4L5Y983 is popular.",
                backend="claude-code", model="sonnet")
    a, b = I.get(con, ok), I.get(con, bad)
    assert a["ai_generated"] and a["compliance"] == [] and a["compliance_banner"] is None and "AI-generated" in a["ai_label"]
    assert a["ai_label_short"] == "AI-generated"                     # the badge text, in the answer's language (the web copies no wording)
    assert b["ai_generated"] and set(b["compliance"]) >= {"isin", "recommendation"} and "General information only" in b["compliance_banner"]
    ev = con.execute("SELECT source, insight_id, codes FROM compliance_events").fetchall()
    assert len(ev) == 1 and ev[0][0] == "insight" and ev[0][1] == bad and "isin" in json.loads(ev[0][2])
    flags = json.loads(con.execute("SELECT flags FROM compliance_events").fetchone()[0])
    assert flags and all(set(f) == {"code", "snippet"} for f in flags)


def test_deterministic_insights_are_not_labelled_or_checked(con):
    iid = I.add(con, kind="digest", title="Weekly summary", body="You should invest in an ETF.", backend="code", model=None)
    d = I.get(con, iid)
    assert d["ai_generated"] is False and d["ai_label"] is None and d["ai_label_short"] is None and d["compliance"] == []
    iid = I.add(con, kind="finding", title="Loan scenario", body="Buy shares.", backend="local", model="none")
    assert I.get(con, iid)["ai_generated"] is False
    assert con.execute("SELECT COUNT(*) FROM compliance_events").fetchone()[0] == 0


def test_the_migration_labels_and_checks_the_insights_that_already_exist(con):
    con.execute("INSERT INTO insights(id, created, kind, title, body, backend) VALUES ('cin_old1','t','answer','t','Investissez dans des actions.','claude-code')")
    con.execute("INSERT INTO insights(id, created, kind, title, body, backend) VALUES ('cin_old2','t','digest','t','Investissez dans des actions.','code')")
    con.execute("UPDATE insights SET ai_generated=1, compliance='[]'")
    con.execute("UPDATE insights SET ai_generated=0 WHERE backend='code'")
    con.execute("DELETE FROM compliance_events")
    con.commit()
    path = Path(__file__).resolve().parents[1] / "src" / "coach" / "migrations" / "0020_ai_label_compliance.py"
    spec = importlib.util.spec_from_file_location("m20", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run(con)
    mod.run(con)                                                    # idempotent
    assert json.loads(con.execute("SELECT compliance FROM insights WHERE id='cin_old1'").fetchone()[0]) == ["recommendation"]
    assert con.execute("SELECT compliance FROM insights WHERE id='cin_old2'").fetchone()[0] == "[]"
    assert con.execute("SELECT COUNT(*) FROM compliance_events").fetchone()[0] == 1


# ---------------------------------------------------------------- the web app and the CLI


def test_a_flagged_answer_carries_the_banner_in_the_stream_and_in_the_job(ctx, monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")        # the backend must look configured on a machine without `claude` (CI)
    text = "Pour votre épargne, vous devriez placer 200 euros par mois dans un ETF sur le MSCI World."
    ctx.state.coach_jobs.runner = fake_runner(text)
    ev = parse(ctx.post("/coach/stream", {"question": "Où placer mon épargne ?"}).text)
    ans = next(d for e, d in ev if e == "answer")
    assert ans["ai_generated"] is True and ans["compliance"]["flagged"] is True
    assert "AMF" in ans["compliance"]["banner"] and ans["compliance"]["lang"] == "fr" and "IA" in ans["compliance"]["label"]
    iid = ans["insight_id"]
    snap = ctx.get(f"/coach/jobs/{next(iter(ctx.state.coach_jobs.jobs))}").json()
    assert snap["ai_generated"] is True and snap["compliance"]["flagged"]
    assert json.loads(ctx.sql("SELECT compliance FROM insights WHERE id=?", iid)[0][0])
    assert ctx.sql("SELECT COUNT(*) FROM compliance_events")[0][0] >= 1


def test_a_clean_answer_is_labelled_but_has_no_banner(ctx, monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")        # the backend must look configured on a machine without `claude` (CI)
    ctx.state.coach_jobs.runner = fake_runner("Your spending on groceries rose 12% in September.")
    ev = parse(ctx.post("/coach/stream", {"question": "Why?"}).text)
    ans = next(d for e, d in ev if e == "answer")
    assert ans["ai_generated"] is True and ans["compliance"]["flagged"] is False and ans["compliance"]["banner"] == ""
    assert "AI-generated" in ans["compliance"]["label"]


def test_the_insights_feed_shows_the_label_and_the_banner(ctx, monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")        # the backend must look configured on a machine without `claude` (CI)
    ctx.state.coach_jobs.runner = fake_runner("You should invest in an ETF every month for your savings.")
    ctx.post("/coach/stream", {"question": "Where do I put money?"})
    items = ctx.get("/insights").json()["coach"]["items"]
    assert items and items[0]["ai_generated"] is True and "AI-generated" in items[0]["ai_label"]
    assert items[0]["compliance"] and "General information only" in items[0]["compliance_banner"]


def test_cli_ask_prints_the_ai_label_and_the_banner(cfg, db_key, monkeypatch, capsys):
    connect(cfg, create=True).close()
    text = "Vous devriez placer cet argent dans un ETF pour votre épargne."
    res = RunResult(text=text, finish_reason="stop", usage=Usage("claude-code", "sonnet", "coach:ask", 1), backend="claude-code", model="sonnet",
                    session_id="j_1")
    monkeypatch.setattr(agent_commands, "run_agent", lambda *a, **k: res)
    agent_commands.cmd_ask(Namespace(question="Où placer ?", skill=None, month=None, year=None, country=None, dry_run=False, insecure=False), cfg)
    cap = capsys.readouterr()
    assert "[AI-generated]" in cap.err and "Généré par IA" in cap.out and "AMF" in cap.out and text in cap.out
    con = connect(cfg)
    row = con.execute("SELECT ai_generated, compliance FROM insights").fetchone()
    assert row[0] == 1 and json.loads(row[1])
