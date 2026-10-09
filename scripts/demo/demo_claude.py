"""The DEMO's scripted `claude`: no model, no network. It stands in for `claude -p` when the demo home's coach backend is
"claude-code" (scripts/demo/run_demo.sh puts a wrapper named `claude` first on PATH).

It reads the question, picks the closest scenario of scenarios.json, starts the REAL finance MCP server named in --mcp-config,
calls the scenario's tools on the demo data and streams an answer whose figures and evidence refs are filled in from what those
tools returned (`{{tool:path.to.value}}`). So the demo shows the real tool calls, the real redaction and real numbers; only the
wording is pre-written. Never install it on a real home.
"""
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
prompt = sys.stdin.read()
cfgjson = json.load(open(args[args.index("--mcp-config") + 1]))
scenarios = json.loads((HERE / "scenarios.json").read_text())


def last_month_named(month: int) -> str:
    t = dt.date.today()
    y = t.year if t.month > month else t.year - 1
    return f"{y}-{month:02d}"


def pick() -> dict:
    q = prompt.lower()
    # the prompt holds the instructions and the question; the question is at the end
    tail = q[-1500:]
    best, score = scenarios["default"], 0
    for sc in scenarios["scenarios"]:
        s = sum(1 for k in sc["keywords"] if k in tail)
        if s > score:
            best, score = sc, s
    return best


def subst(v):
    if isinstance(v, str):
        return v.replace("$JULY", last_month_named(7)).replace("$LAST_MONTH", last_month_named(dt.date.today().month - 1 or 12))
    if isinstance(v, dict):
        return {k: subst(x) for k, x in v.items()}
    if isinstance(v, list):
        return [subst(x) for x in v]
    return v


def emit(o):
    print(json.dumps(o), flush=True)


def lookup(seen: dict, expr: str) -> str:
    tool, _, path = expr.partition(":")
    cur = seen[tool]
    for part in path.split("."):
        if isinstance(cur, dict) and isinstance(cur.get(part), dict) and set(cur[part]) == {"untrusted_text"}:
            cur = cur[part]["untrusted_text"]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    if isinstance(cur, dict) and "untrusted_text" in cur:
        cur = cur["untrusted_text"]
    return str(cur)


async def main():
    sc = subst(pick())
    srv = cfgjson["mcpServers"]["finance"]
    params = StdioServerParameters(command=srv["command"], args=srv["args"], env=srv["env"])
    emit({"type": "system", "subtype": "init", "session_id": "demo", "tools": [f"mcp__finance__{s['tool']}" for s in sc["steps"]],
          "mcp_servers": [{"name": "finance", "status": "connected"}], "model": "claude-sonnet-demo", "plugins": [], "skills": []})
    seen = {}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for i, step in enumerate(sc["steps"]):
                tid = f"toolu_demo_{i}"
                emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "mcp__finance__" + step["tool"],
                                                                    "input": step.get("args", {})}]}})
                time.sleep(0.5)
                res = await s.call_tool(step["tool"], step.get("args", {}))
                text = res.content[0].text
                seen[step.get("as", step["tool"])] = json.loads(text)
                emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": False,
                                                               "content": [{"type": "text", "text": text}]}]}})
    final = re.sub(r"\{\{([^}]+)\}\}", lambda m: lookup(seen, m.group(1).strip()), "\n".join(sc["answer"]))
    for piece in re.findall(r"\S+\s*|\s+", final):
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})
        time.sleep(0.025)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": final, "total_cost_usd": 0.0,
          "usage": {"input_tokens": 18400, "output_tokens": 420, "cache_read_input_tokens": 12000, "cache_creation_input_tokens": 0}})


anyio.run(main)
