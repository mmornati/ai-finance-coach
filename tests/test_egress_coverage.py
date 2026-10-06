"""E11-1: every network-capable call site is registered, and goes through the egress gate.

The test reads the SOURCE (AST), so a new `requests.post`, `subprocess.run(["claude", ...])`, `smtplib.SMTP`, `socket`, `webbrowser` or
`anthropic` use in a function that does not call `egress.allow(...)` (or is not exempt in coach.egress.EXEMPT_FUNCTIONS, with a reason) fails
here, and so does a file that is missing from coach.egress.CALL_SITES.
"""
import ast
import re
from pathlib import Path

from coach import egress

SRC = Path(__file__).resolve().parents[1] / "src" / "coach"
ROOTS = {"requests", "httpx", "smtplib", "socket", "subprocess", "webbrowser", "anthropic", "aiohttp", "uvicorn", "Popen", "HTTPServer"}
GATES = {"allow", "gated_client"}


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _files():
    return sorted(p for p in SRC.rglob("*.py") if "migrations" not in p.parts and "__pycache__" not in p.parts)


def _hits(node) -> list[str]:
    """Network-capable references directly in `node` (not inside a nested function / class)."""
    out = []

    def visit(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)) and c is not node:
                # decorators / defaults / bases belong to the enclosing scope
                for d in getattr(c, "decorator_list", []) + getattr(getattr(c, "args", None), "defaults", []) + getattr(c, "bases", []):
                    out.extend(_expr_hits(d))
                continue
            out.extend(_one(c))
            visit(c)
    visit(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):          # the node's own defaults
        out.extend(h for d in node.args.defaults + [d for d in node.args.kw_defaults if d] for h in _expr_hits(d))
    return out


def _expr_hits(e) -> list[str]:
    out = []
    for n in ast.walk(e):
        out.extend(_one(n))
    return out


NET_MODULES = {"requests", "httpx", "smtplib", "socket", "subprocess", "webbrowser", "anthropic", "aiohttp", "uvicorn", "urllib3", "ftplib",
               "telnetlib", "paramiko", "websockets", "websocket", "xmlrpc", "poplib", "imaplib", "nntplib", "socketserver", "asyncore"}


def _one(n) -> list[str]:
    if isinstance(n, ast.Name) and n.id in ROOTS:
        return [n.id]
    if isinstance(n, ast.Attribute) and n.attr in ("Popen", "HTTPServer"):
        return [n.attr]
    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
        v = n.value.id
        if v == "urllib" and n.attr == "request":
            return ["urllib.request"]
        if v == "http" and n.attr in ("client", "server"):
            return [f"http.{n.attr}"]
        if v == "os" and (n.attr in ("system", "popen", "startfile") or n.attr.startswith(("exec", "spawn", "posix_spawn", "fork"))):
            return [f"os.{n.attr}"]
        if v == "asyncio" and n.attr.startswith("create_subprocess"):
            return [f"asyncio.{n.attr}"]
        if v in ("importlib",) and n.attr == "import_module":
            return ["importlib.import_module"]
    if isinstance(n, ast.Name) and n.id in NET_MODULES:
        return [n.id]
    if isinstance(n, ast.Name) and n.id == "__import__":
        return ["__import__"]
    if isinstance(n, ast.Import):
        return [a.name for a in n.names if a.name.split(".")[0] in ROOTS | NET_MODULES or a.name in ("urllib.request", "http.client", "http.server")]
    if isinstance(n, ast.ImportFrom) and n.module:
        mod = n.module.split(".")[0]
        names = {a.name for a in n.names}
        if mod in ROOTS | NET_MODULES or n.module in ("urllib.request", "http.client", "http.server"):
            return [n.module]
        if n.module == "urllib" and "request" in names:
            return ["urllib.request"]
        if n.module == "http" and names & {"client", "server"}:
            return ["http." + sorted(names & {"client", "server"})[0]]
        if n.module == "os" and names & {"system", "popen", "execv", "execvp", "spawnv", "fork"}:
            return ["os." + sorted(names)[0]]
        if n.module == "asyncio" and any(x.startswith("create_subprocess") for x in names):
            return ["asyncio.create_subprocess"]
        if n.module == "importlib" and "import_module" in names:
            return ["importlib.import_module"]
    if isinstance(n, ast.Constant) and n.value == "claude":
        return ['"claude"']
    return []


def _calls_gate(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in GATES:
            if n.func.attr == "gated_client" or (isinstance(n.func.value, ast.Name) and n.func.value.id == "egress"):
                return True
    return False


def _scopes(tree):
    """(qualname, node, enclosing nodes) for every function and class."""
    def walk(n, prefix, parents):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}.{c.name}" if prefix else c.name
                yield q, c, parents
                yield from walk(c, q, parents + [c])
            else:
                yield from walk(c, prefix, parents)
    yield from walk(tree, "", [])


def test_every_file_with_a_network_call_site_is_registered():
    found = {}
    for p in _files():
        tree = ast.parse(p.read_text())
        mod_hits = _expr_hits(tree)
        if mod_hits:
            found[_rel(p)] = sorted(set(mod_hits))
    unregistered = {f: h for f, h in found.items() if f not in egress.CALL_SITES}
    assert not unregistered, ("network / subprocess / browser call sites in files that are not registered in coach.egress.CALL_SITES "
                              f"(register them, and gate them with egress.allow): {unregistered}")
    stale = [f for f in egress.CALL_SITES if f not in found]
    assert not stale, f"CALL_SITES lists files with no call site any more (remove them): {stale}"


def test_every_function_that_reaches_the_outside_calls_the_gate_or_is_exempt():
    bad, used_exempt = [], set()
    for p in _files():
        rel = _rel(p)
        tree = ast.parse(p.read_text())
        for qual, node, parents in _scopes(tree):
            hits = _hits(node)
            if not hits:
                continue
            if _calls_gate(node) or any(_calls_gate(par) for par in parents):
                continue
            key = (rel, qual)
            if key in egress.EXEMPT_FUNCTIONS or (rel, "*") in egress.EXEMPT_FUNCTIONS or any((rel, par.name) in egress.EXEMPT_FUNCTIONS for par in parents):
                used_exempt.add(key if key in egress.EXEMPT_FUNCTIONS else (rel, "*"))
                continue
            bad.append(f"{rel}:{qual} reaches {sorted(set(hits))} without egress.allow()")
    assert not bad, "\n".join(bad)
    stale = [k for k in egress.EXEMPT_FUNCTIONS if k[1] != "*" and k not in used_exempt and not _exempt_matches_parent(k)]
    assert not stale, f"EXEMPT_FUNCTIONS lists functions that no longer reach the outside (remove them): {stale}"


def _exempt_matches_parent(key) -> bool:
    rel, qual = key
    p = SRC / rel
    if not p.exists():
        return False
    tree = ast.parse(p.read_text())
    for q, node, parents in _scopes(tree):
        if q == qual:
            return bool(_hits(node)) or any(_hits(c) for c in ast.walk(node) if isinstance(c, (ast.FunctionDef, ast.ClassDef)))
    return False


def test_gate_kinds_are_registered():
    """Every egress.allow("<kind>") of the source names a kind of the inventory; f-strings only for the llm. / alerts. families."""
    bad = []
    for p in _files():
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("allow", "evaluate") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "egress" and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if a.value not in egress.KINDS:
                        bad.append(f"{_rel(p)}: egress.{n.func.attr}({a.value!r})")
                elif isinstance(a, ast.JoinedStr):
                    head = a.values[0].value if a.values and isinstance(a.values[0], ast.Constant) else ""
                    if head not in ("llm.", "alerts."):
                        bad.append(f"{_rel(p)}: dynamic egress kind {head!r}")
    assert not bad, bad


def test_registry_is_consistent():
    for f, (kinds, why) in egress.CALL_SITES.items():
        assert (SRC / f).exists(), f
        assert why
        for k in kinds:
            assert k in egress.KINDS, (f, k)
    for (f, q), why in egress.EXEMPT_FUNCTIONS.items():
        assert (SRC / f).exists(), f
        assert why.strip(), (f, q)
    for fl in egress.INVENTORY:
        assert fl.destination and fl.data and fl.redaction and fl.opt_in and fl.disable, fl.kind
        assert fl.local_only in ("allowed", "refused", "loopback only") and fl.offline in ("allowed", "refused", "loopback only")
        assert fl.external or fl.local_only != "refused" or fl.kind == "llm.ollama"


def test_the_raw_anthropic_client_is_only_reached_through_the_gate():
    """AnthropicBackend.raw_client builds the SDK client WITHOUT the egress check: only gated_client() may use it."""
    users = []
    for p in _files():
        for m in re.finditer(r"\.raw_client\b", p.read_text()):
            users.append(_rel(p))
    assert set(users) <= {"classify/backends.py"} and len(users) == 1, users


def test_only_wipe_deletes_keychain_items():
    """The Keychain is deleted from exactly one place: `coach wipe` (interactive, typed confirmation); secrets.py only defines the helper."""
    callers = {}
    for p in _files():
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("delete_secret", "delete_password"):
                callers.setdefault(_rel(p), []).append(n.func.attr)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("delete_secret", "delete_password"):
                callers.setdefault(_rel(p), []).append(n.func.id)
    assert set(callers) <= {"secrets.py", "wipe.py"}, callers
    assert callers.get("wipe.py"), "wipe.py is the code path that deletes Keychain items"


def test_the_detector_catches_a_new_unregistered_call_site():
    """Self-check of the test itself: a synthetic function that posts, spawns `claude`, sends mail or uses a raw socket is seen, and the
    gate / exemption logic recognises the safe shapes."""
    bad_src = '''
import requests, subprocess, smtplib, socket
def leak(x):
    return requests.post("https://example.org", json=x)
def spawn(prompt):
    return subprocess.run(["claude", "-p"], input=prompt)
def mail():
    return smtplib.SMTP("smtp.example.org")
def raw():
    return socket.create_connection(("example.org", 80))
def hook():
    import urllib.request
    return urllib.request.urlopen("https://example.org")
'''
    tree = ast.parse(bad_src)
    found = {q: sorted(set(_hits(n))) for q, n, _ in _scopes(tree)}
    assert found["leak"] == ["requests"] and "subprocess" in found["spawn"] and '"claude"' in found["spawn"]
    assert found["mail"] == ["smtplib"] and found["raw"] == ["socket"] and "urllib.request" in found["hook"]
    assert not any(_calls_gate(n) for _, n, _ in _scopes(tree))
    good = ast.parse('''
import requests
from coach import egress
def ok(x):
    egress.allow("enable_banking", {"purpose": "sync"})
    return requests.post("https://example.org", json=x)
def via_client(self):
    return self.gated_client("p").messages.create()
''')
    scopes = {q: (n, p) for q, n, p in _scopes(good)}
    assert _calls_gate(scopes["ok"][0]) and _calls_gate(scopes["via_client"][0])
    assert not _calls_gate(ast.parse("def f():\n    other.allow('x')\n").body[0])           # only egress.allow / gated_client count


def test_the_detector_sees_every_other_way_to_start_a_process_or_open_a_connection():
    src = """
import os, asyncio, importlib
from urllib import request
from http import client
from os import system
from asyncio import create_subprocess_exec
def a(): os.system("x")
def b(): os.popen("x")
def c(): os.execvp("x", [])
def d(): os.spawnl(0, "x")
async def e(): await asyncio.create_subprocess_shell("x")
def f(): return importlib.import_module("requests")
def g(): return __import__("smtplib")
def h(): return request.urlopen("x")
def i(): import http.client
def j(): from http import server
"""
    tree = ast.parse(src)
    assert {x for x in _expr_hits(tree)} >= {"urllib.request", "http.client", "os.system", "asyncio.create_subprocess"}
    found = {q: _hits(n) for q, n, _ in _scopes(tree)}
    for q in "abcdefg":
        assert found[q], q
    assert found["i"] and found["j"] and "http.client" in _expr_hits(tree)
