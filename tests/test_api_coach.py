"""E6-3 / E6-7: the coach endpoints (job, SSE stream, cancel, resolve, insights feed). The runner is faked or the backend is a fake
`claude`: no model, no network."""
from __future__ import annotations

import json
import threading

import pytest

from apihelpers import api, ctx, world  # noqa: F401
from coach.agent import insights as I
from coach.agent.runner import CoachUnavailable, RunResult
from coach.classify.backends import Usage

WHICH = "coach.api.coachjobs.shutil.which"


def parse(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev = data = None
        for ln in block.split("\n"):
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                data = json.loads(ln[5:].strip())
        if ev:
            out.append((ev, data))
    return out


def fake_runner(text="Spending rose because of 12.99 EUR.", *, finish="stop", refs=(), suspicious=False, unverified=(), gate=None,
                proposals=()):
    def run(cfg, spec, question, *, emit, cancel, insecure, session_id, **kw):
        emit("status", {"state": "running", "backend": "claude-code", "model": "sonnet", "session_id": session_id})
        emit("tool_call", {"id": "t1", "name": "coverage", "args": "", "n": 1, "max": 12})
        emit("tool_result", {"id": "t1", "name": "coverage", "ok": True, "chars": 10, "suspicious": False})
        if gate:
            gate.wait(10)
            if cancel.is_set():
                return RunResult(finish_reason="cancelled", session_id=session_id, backend="claude-code", model="sonnet")
        for piece in (text[:10], text[10:]):
            emit("delta", {"text": piece})
        return RunResult(text=text, finish_reason=finish, usage=Usage("claude-code", "sonnet", "coach:ask", 1, 100, 20, 5, 0, 0.01, True, 1.5),
                         tool_calls=[{"name": "coverage"}], suspicious=suspicious, refs=list(refs), unverified_numbers=list(unverified),
                         proposals=list(proposals), session_id=session_id, backend="claude-code", model="sonnet")
    return run


@pytest.fixture(autouse=True)
def claude_present(monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")


def test_status_prompts_and_a_streamed_answer_with_usage_and_a_stored_insight(ctx):
    s = ctx.get("/coach/status").json()
    assert s["configured"] and s["backend"] == "claude-code" and s["model"] == "sonnet" and "personal" in s["message"]
    assert s["max_tool_calls"] == 12 and "memory_propose" in s["tools"] and "memory_accept" not in s["tools"] and s["busy"] is False
    assert len(ctx.get("/coach/prompts").json()["prompts"]) >= 3
    ref = ctx.sql("SELECT tx_key FROM transactions LIMIT 1")[0][0]
    from coach.analytics.privacy import stable_hash
    h = stable_hash(ref)
    ctx.state.coach_jobs.runner = fake_runner(f"Spending rose ({h}) by 12.99 EUR.", refs=[h], unverified=["12.99"])
    r = ctx.post("/coach/stream", {"question": "Why was September high?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = parse(r.text)
    kinds = [e for e, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and "tool_call" in kinds and "delta" in kinds
    assert kinds.index("usage") < kinds.index("answer") < kinds.index("done")
    assert "".join(d["text"] for e, d in ev if e == "delta").startswith("Spending rose")
    ans = next(d for e, d in ev if e == "answer")
    assert ans["unverified_numbers"] == ["12.99"] and ans["insight_id"].startswith("cin_")
    assert next(d for e, d in ev if e == "citation")["ref"] == h
    u = next(d for e, d in ev if e == "usage")
    assert u["tokens_in"] == 100 and u["cost_usd"] == 0.01
    row = ctx.sql("SELECT kind, title, status, backend, model, usage_ref, unverified_numbers, question FROM insights")
    assert row[0][:5] == ("answer", "Why was September high?", "read", "claude-code", "sonnet") and row[0][5]
    assert json.loads(row[0][6]) == ["12.99"] and row[0][7] == "Why was September high?"
    assert ctx.sql("SELECT purpose, tokens_in, cost_usd FROM llm_usage")[0] == ("coach:ask", 100, 0.01)


def test_one_job_at_a_time_cancel_and_reconnect(ctx):
    gate = threading.Event()
    ctx.state.coach_jobs.runner = fake_runner(gate=gate)
    r = ctx.post("/coach/jobs", {"question": "slow one"})
    assert r.status_code == 202
    jid = r.json()["id"]
    busy = ctx.post("/coach/stream", {"question": "second"})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "coach_busy" and busy.json()["error"]["details"]["job_id"] == jid
    assert ctx.get("/coach/status").json()["busy"] is True
    assert ctx.get(f"/coach/jobs/{jid}").json()["state"] == "running"
    assert ctx.post(f"/coach/jobs/{jid}/cancel").status_code == 200
    gate.set()
    ev = parse(ctx.client.get(api(f"/coach/jobs/{jid}/stream")).text)       # a page that reconnects replays everything
    kinds = [e for e, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and ev[-1][1]["finish_reason"] == "cancelled" and "notice" in kinds
    after = parse(ctx.client.get(api(f"/coach/jobs/{jid}/stream"), params={"after": 2}).text)
    assert [e for e, _ in after] == kinds[2:]
    assert ctx.get(f"/coach/jobs/{jid}").json()["state"] == "cancelled"
    assert ctx.sql("SELECT COUNT(*) FROM insights")[0][0] == 0               # a cancelled answer is not stored
    assert ctx.get("/coach/jobs/j_nope").status_code == 404 and ctx.post("/coach/jobs/j_nope/cancel").status_code == 404
    ctx.state.coach_jobs.runner = fake_runner()                               # free again
    assert ctx.post("/coach/stream", {"question": "again"}).status_code == 200


def test_not_configured_unavailable_and_failures_are_events_not_crashes(ctx, monkeypatch):
    monkeypatch.setattr(WHICH, lambda name: None)
    assert ctx.get("/coach/status").json()["configured"] is False
    ev = parse(ctx.post("/coach/stream", {"question": "q"}).text)
    assert [e for e, _ in ev] == ["meta", "notice", "done"] and ev[1][1]["code"] == "coach_not_configured"
    monkeypatch.setattr(WHICH, lambda name: "/usr/bin/claude")

    def boom(*a, **k):
        raise CoachUnavailable("model has no tool support")
    ctx.state.coach_jobs.runner = boom
    ev = parse(ctx.post("/coach/stream", {"question": "q"}).text)
    assert ("error", {"code": "coach_unavailable", "message": "model has no tool support"}) in ev and ev[-1][0] == "done"

    def crash(*a, **k):
        raise ValueError("oops")
    ctx.state.coach_jobs.runner = crash
    ev = parse(ctx.post("/coach/stream", {"question": "q"}).text)
    assert any(e == "error" and "ValueError" in d["message"] for e, d in ev)
    ctx.state.coach_jobs.runner = fake_runner(finish="timeout", text="partial")
    ev = parse(ctx.post("/coach/stream", {"question": "q"}).text)
    assert any(e == "notice" and d["code"] == "timeout" for e, d in ev) and ev[-1][1]["finish_reason"] == "timeout"
    assert ctx.client.post(api("/coach/stream"), json={"question": "x"}).status_code == 403            # CSRF
    assert ctx.post("/coach/stream", {"question": ""}).status_code == 422


def test_resolve_maps_evidence_refs_to_transactions_on_the_server(ctx):
    from coach.analytics.privacy import stable_hash
    key = ctx.sql("SELECT tx_key FROM transactions WHERE tx_key LIKE 'gro%' LIMIT 1")[0][0]
    h = stable_hash(key)
    rid = ctx.state.snapshot().recurring().series[0].id
    r = ctx.post("/coach/resolve", {"refs": [h, "h_0000000000", "rec_abc123", "anm_abc123", "chg_abc123", "weird"]  + [rid]}).json()["refs"]
    assert r[rid]["kind"] == "series"
    assert r[h]["tx_key"] == key and r[h]["kind"] == "transaction" and r[h]["amount"].startswith("-")
    assert r["h_0000000000"] is None and r["weird"] is None
    assert r["rec_abc123"] is None and r["anm_abc123"] is None and r["chg_abc123"] is None      # invented ids resolve to nothing
    assert ctx.post("/coach/resolve", {"refs": []}).status_code == 422


def test_coach_insights_in_the_feed_with_dismiss_done_snooze_read_restore(ctx):
    with ctx.state.write() as con:
        a = I.add(con, kind="digest", title="Weekly digest 2026-10-04", body="All quiet.", evidence=["h_0123456789"], skill="digest-weekly",
                  backend="anthropic-api", model="claude-sonnet-5-5", usage_ref=None, session_id="j_1", unverified_numbers=["999.99"],
                  suspicious=True)
        b = I.add(con, kind="finding", title="Second", body="x", evidence=[])
        c = I.add(con, kind="finding", title="Third", body="x", evidence=[])
        d = I.add(con, kind="finding", title="Fourth", body="x", evidence=[])
    feed = ctx.get("/insights").json()
    items = feed["coach"]["items"]
    assert {i["id"] for i in items} == {a, b, c, d} and feed["coach"]["configured"]
    first = next(i for i in items if i["id"] == a)
    assert first["suspicious"] and first["unverified_numbers"] == ["999.99"] and first["evidence"] == ["h_0123456789"] and first["status"] == "new"
    assert ctx.post(f"/insights/{a}/read").json()["status"] == "read"
    assert ctx.post(f"/insights/{b}/dismiss").json()["dismissed"] is True
    assert ctx.post(f"/insights/{c}/done").json()["status"] == "done"
    sn = ctx.post(f"/insights/{d}/snooze", {"days": 3}).json()
    assert sn["snoozed_until"] == "2026-10-07"
    feed = ctx.get("/insights").json()
    assert [i["id"] for i in feed["coach"]["items"]] == [a] and feed["coach"]["hidden"] == 3
    allv = ctx.get("/insights", include_snoozed="true").json()["coach"]["items"]
    assert len(allv) == 4
    assert ctx.post(f"/insights/{b}/restore").json()["status"] == "new"
    assert {i["id"] for i in ctx.get("/insights").json()["coach"]["items"]} == {a, b}
    assert ctx.post("/insights/cin_nope/dismiss").status_code == 404 and ctx.post("/insights/ins_x/read").status_code == 404


def test_end_to_end_with_the_fake_claude_and_the_real_mcp_server(ctx, tmp_path, monkeypatch):
    """The whole path: POST -> job -> fake `claude -p` -> real stdio MCP server -> tools -> SSE -> stored insight."""
    import os
    import stat
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(root / 'tests' / 'fake_claude.py')!r}, run_name='__main__')\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(WHICH, lambda name: str(exe))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(root / "src"))
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"steps": [{"tool": "coverage"}, {"tool": "transactions_search", "args": {"limit": 1}}],
                                                   "final": "The latest payment is $REF0, nothing else."}))
    ctx.state.insecure = True
    ctx.state.cfg.coach_claude_env = ("FAKE_CLAUDE",)
    ev = parse(ctx.post("/coach/stream", {"question": "What was the latest payment?"}).text)
    kinds = [e for e, _ in ev]
    assert kinds.count("tool_call") == 2 and kinds[-1] == "done" and ev[-1][1]["finish_reason"] == "stop", ev
    cit = next(d for e, d in ev if e == "citation")
    assert cit["ref"].startswith("h_")
    assert ctx.post("/coach/resolve", {"refs": [cit["ref"]]}).json()["refs"][cit["ref"]]["kind"] == "transaction"
    row = ctx.sql("SELECT kind, evidence, backend FROM insights")[0]
    assert row[0] == "answer" and json.loads(row[1]) == [cit["ref"]] and row[2] == "claude-code"


def test_the_observed_claude_init_summary_is_kept_in_the_job_log_and_on_disk(ctx):
    def run(cfg, spec, question, *, emit, cancel, insecure, session_id, **kw):
        emit("init", {"model": "m", "tools": 1, "mcp_servers": ["finance"], "plugins": 0, "skills": 0, "agents": ["general-purpose"]})
        return RunResult(text="ok", finish_reason="stop", session_id=session_id, backend="claude-code", model="sonnet")
    ctx.state.coach_jobs.runner = run
    ev = parse(ctx.post("/coach/stream", {"question": "q"}).text)
    jid = ev[0][1]["job_id"]
    assert any(e == "init" for e, _ in ev)
    snap = ctx.get(f"/coach/jobs/{jid}").json()
    assert snap["log"] and snap["log"][0].startswith("init ") and "general-purpose" in snap["log"][0]
