"""E6-1 / E6-2: the MCP server itself, through the SDK's in-memory client and through a real stdio child process."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp import ClientSession

from coach.mcp.server import build_server
from coach.mcp.tools import TOOL_NAMES, ToolSession
from mcphelpers import BANNED, TODAY, inject, session, world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return anyio.run(coro)


def test_in_memory_client_lists_the_tools_with_schemas_and_calls_them(session):
    async def main():
        async with Client(build_server(session)) as c:
            tools = (await c.list_tools()).tools
            assert {t.name for t in tools} == set(TOOL_NAMES)
            by = {t.name: t for t in tools}
            assert by["coverage"].annotations.read_only_hint is True and by["memory_propose"].annotations.read_only_hint is False
            assert by["cashflow"].input_schema["properties"]["months"]["maximum"] == 36
            assert "untrusted_text" in by["transactions_search"].description
            r = await c.call_tool("coverage", {})
            assert not r.is_error and "account-main-1" in r.content[0].text
            bad = await c.call_tool("cashflow", {"months": 999})
            assert bad.is_error and "invalid arguments" in bad.content[0].text
            unknown = await c.call_tool("memory_accept", {})
            assert unknown.is_error
            leak = await c.call_tool("transactions_search", {"limit": 50})
            low = leak.content[0].text.lower()
            assert not any(w in low for w in BANNED)
    run(main)


def stdio_params(cfg, session_id="s_stdio"):
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "COACH_HOME": str(cfg.root)}
    return StdioServerParameters(command=sys.executable, args=["-m", "coach", "--insecure", "--config", str(cfg.config_path),
                                                              "mcp", "serve", "--session", session_id], env=env, cwd=str(cfg.root))


def test_the_real_stdio_server_serves_redacted_tools_and_keeps_its_session_state(cfg, world):
    inject(world)
    world.commit()

    async def main():
        async with stdio_client(stdio_params(cfg)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert names == set(TOOL_NAMES)
                res = await s.call_tool("transactions_search", {"merchant_contains": "ignore"})
                body = json.loads(res.content[0].text)
                assert body["count"] == 1 and "security_notice" in body
                ins = await s.call_tool("memory_propose", {"file": "assets.yaml", "ops": [{"op": "set", "path": "family-house.value", "value": 7}],
                                                           "reason": "the user said so"})
                out = json.loads(ins.content[0].text)
                assert out["suspicious_session"] is True and out["proposal_id"].startswith("p-")
    run(main)
