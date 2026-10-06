"""E7: the Claude Code skills are text files. These tests check what they promise and what they must never instruct:
the finance MCP tools / coach commands only, proposals only, no direct reads of memory/ or the database, no accept / --yes,
web search only where it is allowed (find-cheaper, mortgage-check) and only with generic, non-personal queries, and that the
docs and the index list every skill and tool. No model is involved."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from coach.agent import prompt as P
from coach.mcp.tools import READ_ONLY, TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".claude" / "skills"
NEW_SKILLS = ("monthly-review", "explain-spike", "subscription-audit", "contract-check", "find-cheaper", "mortgage-check", "what-if",
              "tax-helper", "onboarding-interview")
NEW_TOOLS = ("monthly_review", "explain_spike", "subscription_audit", "cancellability", "savings_estimate", "mortgage_check", "what_if",
             "tax_candidates", "onboarding_status", "questions_propose")
TOOL_OF = {"monthly-review": "monthly_review", "explain-spike": "explain_spike", "subscription-audit": "subscription_audit",
           "contract-check": "cancellability", "find-cheaper": "savings_estimate", "mortgage-check": "mortgage_check",
           "what-if": "what_if", "tax-helper": "tax_candidates", "onboarding-interview": "onboarding_status"}
WEB_SKILLS = ("find-cheaper", "mortgage-check")


def text(skill: str) -> str:
    return (SKILLS_DIR / skill / "SKILL.md").read_text()


def flat(skill: str) -> str:
    return re.sub(r"\s+", " ", text(skill))


def paragraphs(skill: str) -> list[str]:
    body = text(skill).split("---", 2)[2]
    return [re.sub(r"\s+", " ", p) for p in re.split(r"\n\s*\n|\n(?=\s*[-0-9]+[.)]?\s)", body) if p.strip()]


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_frontmatter_and_the_core_safety_rules(skill):
    t = text(skill)
    m = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", t)
    assert m and m.group(1) == skill and len(m.group(2)) > 80
    f = flat(skill)
    assert "untrusted_text" in f and "DATA" in f
    assert "`memory/`" in f and "`data/`" in f and "directly" in f                 # told NOT to read them directly
    assert "--insecure" in f and "Never pass `--insecure`" in f
    assert "Never run `uv run coach memory accept`" in f and "--yes" in f
    assert "not financial advice" in f.lower() or "not legal or financial advice" in f.lower() or "Estimate only" in f or "No investment" in f
    assert TOOL_OF[skill] in f


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_no_skill_instructs_a_forbidden_command_or_a_direct_read(skill):
    t = text(skill)
    for para in paragraphs(skill):
        low = para.lower()
        # every mention of a forbidden thing sits in a prohibition
        for needle in ("memory accept", "--yes", "--force", "--insecure", "settings.json"):
            if needle in low:
                assert re.search(r"\b(never|do not|don't|not)\b", low), (skill, needle, para)
        for cmd in ("memory reject", "memory revert", "purge-history", "proposal_key", "ui-session", "coach ui", "script "):
            assert cmd not in low or re.search(r"\b(never|do not|don't|not)\b", low) or cmd == "script ", (skill, cmd)
    # no instruction to cat / grep / sqlite the memory or the database
    assert not re.search(r"\b(cat|less|head|tail|grep|rg|sqlite3|sed|awk)\s+[^`\n]*(memory/|data/|\.db\b|finance\.db)", t), skill
    assert "memory/*.yaml" not in t and "Read(" not in t
    # nothing tells the model to run a shell script, curl or the network from the skill itself
    assert not re.search(r"\b(curl|wget|nc |ssh |chmod|rm -)\b", t), skill
    # the only write commands are proposals or user-confirmed ones; direct memory writes carry --source coach
    for line in t.splitlines():
        if re.search(r"uv run coach memory (set|append|new|annotate|member add|doc add)\b", line) and "propose" not in line:
            assert "--source coach" in line or "never" in line.lower() or "user" in line.lower(), (skill, line)


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_tool_names_in_a_skill_exist(skill):
    t = text(skill)
    for name in re.findall(r"mcp__finance__(\w+)", t):
        assert name in TOOL_NAMES, (skill, name)
    # every backticked snake_case word that looks like a finance tool is a real tool (or a known field name)
    known_fields = {"can_cancel_now", "earliest_effective_date", "include_rules", "market_rate_pct", "market_rate_date", "quote_date",
                    "alternative_insurance_monthly", "bank_fees", "guarantee_fees", "other_fees", "switching_costs", "current_monthly",
                    "alternative_monthly", "usage_questions_needed", "what_i_need", "no_data_for", "missing_info", "cash_flow",
                    "against_usual", "one_offs", "by_class", "excess_by_class", "top_transactions", "same_month_last_year",
                    "from_date", "start_date", "end_date", "set_category_level", "adjust_category", "cancel_recurring", "add_monthly",
                    "remove_monthly", "one_off", "prepay_loan", "change_income", "monthly_delta", "monthly_target", "drawn_from_savings",
                    "candidates_savings_range_sum", "expected_yearly_savings", "no_contract_on_file", "price_increases", "assurance_vie",
                    "assicurazione_vita", "memory_context", "open_questions", "category_averages", "budget_suggestions", "transactions_search",
                    "explain_transaction", "add_insight", "memory_propose", "legal_basis", "offer_name", "source_url", "retrieved_at", "monthly_price",
                        "draftable", "usage_recorded", "before_monthly", "after_monthly", "inferred_suggestions",
                        "web_search_skills"}      # a field of `coach privacy status --json` (E11-4)
    tools = set(TOOL_NAMES)
    for w in set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", t)):
        assert w in tools or w in known_fields, (skill, w)
    # the skill mentions its own tool by name
    assert f"`{TOOL_OF[skill]}`" in t


def test_web_search_only_in_find_cheaper_and_mortgage_check_and_always_generic_and_dated():
    for skill in NEW_SKILLS + ("analytics-overview", "household-questions", "review-categories", "categorize-transactions"):
        f = flat(skill)
        uses_web = "WebSearch" in f or "WebFetch" in f or "web search" in f.lower()
        if skill in WEB_SKILLS:
            assert uses_web, skill
        elif skill in ("household-questions", "review-categories"):
            pass                                                 # E3 skills: a named business + city may be searched, never a person
        elif skill in ("onboarding-interview", "tax-helper", "contract-check", "subscription-audit", "what-if", "monthly-review",
                       "explain-spike", "analytics-overview", "categorize-transactions"):
            assert "WebSearch" not in f and "WebFetch" not in f, skill
    for skill in WEB_SKILLS:
        f = flat(skill)
        assert "generic" in f and "non-personal" in f and re.search(r"NEVER put", f)
        for banned in ("name", "address", "account", "IBAN"):
            assert banned in f
        assert "SOURCE" in f and "DATE" in f and "30 days" in f and "possibly outdated" in f
        assert "interactive Claude Code" in f
        assert "recommend" in f.lower()
    ch = flat("find-cheaper")
    for needle in ("energie-info.fr", "Portale Offerte", "Que Choisir", "ARCEP", "offre fibre + mobile France prix 2026", "savings_estimate",
                   "never switch", "add_insight", "URLS"):
        assert needle.lower() in ch.lower(), needle
    mc = flat("mortgage-check")
    assert "taux credit immobilier 20 ans octobre 2026" in mc and "Estimate only: consult your bank or a broker" in mc
    assert "what_i_need" in mc and "questions_propose" in mc and "never guessed" in mc


def test_the_runtime_prompts_never_mention_web_search_but_the_skill_files_say_it_is_claude_code_only():
    for sid, spec in P.SKILL_SPECS.items():
        assert "web" not in spec.user.lower().replace("web app", ""), sid
    coach_md = re.sub(r"\s+", " ", (ROOT / "docs" / "coach.md").read_text())
    assert "no web tool" in coach_md


def test_contract_check_documents_the_dry_run_then_review_then_send_flow():
    f = flat("contract-check")
    assert "doc add" in f and "doc extract" in f and "DRY RUN first" in f and "--send" in f
    assert "explicitly agrees" in f and "permission rule asks the user first" in f
    assert "PROPOSAL" in f and "themselves" in f and "cancellability" in f and "verify with your contract" in f.lower()
    for law in ("Hamon", "Chatel", "Lemoine", "Bersani"):
        assert law in text("contract-check") or law in (ROOT / "docs" / "skills.md").read_text()
    assert "never send" in f.lower() or "Never send" in f


def test_the_other_skills_point_to_the_new_ones_and_the_index_lists_all():
    ov = flat("analytics-overview")
    hq = flat("household-questions")
    claude = (ROOT / "CLAUDE.md").read_text()
    for skill in NEW_SKILLS:
        assert f"`{skill}`" in ov, skill
        assert f"`{skill}`" in claude, skill
    for tool in NEW_TOOLS:
        assert f"`{tool}`" in ov or tool in ("questions_propose",), tool
    assert "onboarding-interview" in hq and "questions_propose" in hq and "contract-check" in hq and "mortgage-check" in hq
    assert "ONLY in the interactive skills `find-cheaper` and `mortgage-check`" in claude


def test_docs_skills_md_covers_every_skill_tool_and_limit():
    doc = (ROOT / "docs" / "skills.md").read_text()
    for skill in NEW_SKILLS:
        assert f"`{skill}`" in doc, skill
    for tool in NEW_TOOLS:
        assert f"`{tool}" in doc, tool
    for section in ("Purpose", "Inputs", "Tools", "Safety", "Limits"):
        assert doc.count(f"**{section}") >= 9 or doc.count(f"{section}:") >= 9 or doc.count(f"| {section}") >= 1 or section in doc
    readme = (ROOT / "docs" / "reference.md").read_text()          # E13-4: the command reference (the README is the newcomer's introduction)
    assert "docs/skills.md" in readme and "coach onboarding" in readme and "--skill" in readme


def test_the_user_permission_rules_are_untouched_and_the_skills_work_within_them():
    """The user's own `.claude/settings.json` when it exists (it is git-ignored, E13-4); else the shipped template, which carries the same categories."""
    import json
    mine = ROOT / ".claude" / "settings.json"
    s = json.loads((mine if mine.exists() else ROOT / "docs" / "claude-settings.example.json").read_text())
    deny = "\n".join(s["permissions"]["deny"])
    for needle in ("memory accept", "purge-history", "proposal_key", "ui-session.key", "coach ui", "LoginTokens", "coach.api.security"):
        assert needle in deny
    ask = "\n".join(s["permissions"]["ask"])
    assert "doc extract" in ask and "--send" in ask and "memory revert" in ask and "memory reject" in ask
