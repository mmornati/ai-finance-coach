"""Coach skills (E7): the deterministic helpers behind the analysis skills.

Every skill = deterministic Python here (numbers come from code) + new READ-ONLY finance MCP tools (``skills.tools``,
exposed through the same redaction / privacy-guard choke point as every other tool) + a ``PromptSpec`` (``agent.prompt``)
+ a Claude Code skill (``.claude/skills/<name>/SKILL.md``). See ``docs/skills.md``.

Modules
    loans        amortization, renegotiation / rachat / surroga, borrower-insurance delegation (E7-8)
    savings      savings_estimate(current, alternative, switching costs, months) (E7-7)
    cancel       cancellability rules table FR / IT (E7-6)
    review       monthly_review and explain_spike (E7-3, E7-4)
    subaudit     subscription_audit (E7-5)
    whatif       scenario engine on the forecast (E7-9)
    tax          tax_candidates FR / IT (E7-10)
    onboarding   onboarding_status checklist (E7-11)
    tools        the MCP tool specs of the skills
"""
