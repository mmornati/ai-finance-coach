"""A fake `claude` executable for the tests: validates how it was called, starts the MCP server named in --mcp-config over
stdio (the REAL finance server), calls tools as scripted by $FAKE_CLAUDE (JSON) and prints stream-json like `claude -p`.
No model, no network."""
import json
import os
import sys
import time

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

args = sys.argv[1:]
scn = json.loads(os.environ.get("FAKE_CLAUDE", "{}"))
prompt = sys.stdin.read()
rec = {"argv": args, "stdin": prompt, "cwd": os.getcwd(), "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
       "env_has_db_key": "COACH_DB_KEY" in os.environ, "env_keys": sorted(os.environ)}
mcp_path = args[args.index("--mcp-config") + 1]
cfgjson = json.load(open(mcp_path))
rec["mcp"] = cfgjson
out_path = os.environ.get("FAKE_CLAUDE_OUT")
if out_path:
    json.dump(rec, open(out_path, "w"))


def emit(o):
    print(json.dumps(o), flush=True)


async def main():
    srv = cfgjson["mcpServers"]["finance"]
    params = StdioServerParameters(command=srv["command"], args=srv["args"], env=srv["env"])
    names = [f"mcp__finance__{n}" for n in scn.get("tools", [])]
    if not scn.get("skip_init"):
        emit({"type": "system", "subtype": "init", "session_id": "fake", "tools": scn.get("init_tools", ["mcp__finance__coverage"]),
              "mcp_servers": [{"name": "finance", "status": "connected"}], "model": "fake-model", "plugins": [], "skills": [],
              **scn.get("init_extra", {})})
    if scn.get("weird"):
        emit({"type": "assistant", "message": "not a dict"})
        emit({"type": "assistant", "message": {"content": 5}})
        emit({"type": "assistant", "message": {"content": [7, None, {"type": "text", "text": 5}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": None, "content": 12}]}})
        emit({"type": "stream_event", "event": "x"})
        emit({"type": "result", "usage": "oops", "total_cost_usd": "free"})
    if scn.get("sleep"):
        time.sleep(scn["sleep"])
    if scn.get("exit_code"):
        sys.stderr.write("boom\n")
        sys.exit(scn["exit_code"])
    seen = {}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for i, step in enumerate(scn.get("steps", [])):
                tid = f"toolu_{i}"
                emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "mcp__finance__" + step["tool"]
                                                                    if not step["tool"].startswith("raw:") else step["tool"][4:],
                                                                    "input": step.get("args", {})}]}})
                if step["tool"].startswith("raw:"):
                    time.sleep(1)
                    continue
                res = await s.call_tool(step["tool"], step.get("args", {}))
                text = res.content[0].text
                seen[step["tool"]] = text
                emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": res.isError if hasattr(res, "isError") else res.is_error,
                                                               "content": [{"type": "text", "text": text}]}]}})
    final = scn.get("final", "")
    if "$REF0" in final:
        ref = json.loads(seen["transactions_search"])["transactions"][0]["ref"]
        final = final.replace("$REF0", ref)
    if "$PID" in final:
        final = final.replace("$PID", json.loads(seen["memory_propose"])["proposal_id"])
    for piece in [final[i:i + 12] for i in range(0, len(final), 12)]:
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": final, "total_cost_usd": 0.0123,
          "usage": {"input_tokens": 1200, "output_tokens": 150, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100}})


anyio.run(main)
