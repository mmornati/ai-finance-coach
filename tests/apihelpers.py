"""Shared fixtures of the web API tests: a synthetic household, an app, and a logged-in test client.

The session is obtained exactly the way a browser gets it: a one-time login token is exchanged for the cookie. Nothing in
the tests talks to a real port (TestClient calls the ASGI app directly)."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from coach.api.app import create_app
from helpers import add_tx
from memhelpers import make_world, monthly

TODAY = dt.date(2026, 10, 4)
PORT = 8123
HOST = f"http://127.0.0.1:{PORT}"
CALLS: set = set()               # (method, path, status) of every request the tests made: the coverage guard reads it


def missing_endpoints(paths: dict, calls) -> list[str]:
    """The operations of the OpenAPI `paths` that no call of `calls` answered successfully (status < 400)."""
    import re
    ok = [(m, p) for m, p, status in calls if status < 400]
    missing = []
    for path, ops in paths.items():
        rx = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method in ops:
            if not any(m == method.upper() and rx.match(p) for m, p in ok):
                missing.append(f"{method.upper()} {path}")
    return missing


def api(path: str) -> str:
    return "/api/v1" + path


class RecClient(TestClient):
    def request(self, method, url, *a, **kw):
        r = super().request(method, url, *a, **kw)
        CALLS.add((method.upper(), str(url).split("?")[0], r.status_code))
        return r


def login(app, client, base=HOST) -> TestClient:
    """The browser flow: a one-time token (printed by `coach ui`) is exchanged once for the HttpOnly cookie."""
    token = app.state.security.tokens.issue()
    r = client.post(api("/session/exchange"), json={"token": token})
    assert r.status_code == 200, r.text
    return client


def make_client(app, *, base_url=HOST, logged_in=True) -> TestClient:
    c = RecClient(app, base_url=base_url)
    return login(app, c) if logged_in else c


def static_dir(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>coach</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (static / "sw.js").write_text("// sw")
    return static


class Ctx:
    def __init__(self, app, client, cfg):
        self.app, self.client, self.cfg = app, client, cfg
        self.state = app.state.coach
        self.csrf = client.get(api("/session")).json()["csrf_token"]

    def get(self, path, **params):
        return self.client.get(api(path), params=params)

    def post(self, path, body=None, **params):
        return self.client.post(api(path), json={} if body is None else body, params=params, headers={"X-CSRF-Token": self.csrf})

    def put(self, path, body, **params):
        return self.client.put(api(path), json=body, params=params, headers={"X-CSRF-Token": self.csrf})

    def patch(self, path, body):
        return self.client.patch(api(path), json=body, headers={"X-CSRF-Token": self.csrf})

    def sql(self, q, *a):
        with self.state.read() as con:
            return con.execute(q, a).fetchall()


@pytest.fixture
def world(cfg, tmp_path):
    con = make_world(cfg)
    monthly(con, "fo", "pu", "PRICEUP", [-9.99] * 6 + [-12.99] * 3, start=(2026, 1), day=7)
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('PRICEUP','Priceup','subscriptions.software_cloud',0.95,0,'llm','m','t')")
    add_tx(con, "fo", "big1", "2026-10-01", -1200.0, "BIG NEW SHOP", "card")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('BIG NEW SHOP','Big shop','shopping.electronics',0.9,0,'llm','m','t')")
    add_tx(con, "ce", "salary1", "2026-09-28", 3200.0, "VIR SEPA EMPLOYEUR SALAIRE", "transfer_in")
    con.execute("INSERT INTO balances VALUES ('fo','2026-10-04T08:00:00+00:00','CLBD',1500.0,'EUR','2026-10-04')")
    con.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',4200.0,'EUR','2026-10-04')")
    con.commit()
    con.close()
    return cfg


@pytest.fixture
def ctx(world, tmp_path):
    app = create_app(world, insecure=True, port=PORT, static_dir=static_dir(tmp_path), inline_jobs=True)
    app.state.coach.clock = lambda: TODAY
    return Ctx(app, make_client(app), world)
