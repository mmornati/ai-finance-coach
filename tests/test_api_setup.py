"""E13-2: the web side of the first-run wizard: two READ-ONLY endpoints (the wizard's steps and `coach doctor` as data). The steps themselves run in a
terminal with a typed consent; the API has no endpoint that starts one, and nothing it returns is a secret."""
import json

from apihelpers import api, ctx, world  # noqa: F401


def test_the_wizard_status_lists_the_seven_steps_read_only(ctx):
    r = ctx.get("/setup/wizard")
    assert r.status_code == 200
    d = r.json()
    assert [s["id"] for s in d["steps"]] == ["init", "enablebanking", "connect", "sync", "classify", "onboarding", "schedule"]
    assert set(d["progress"]) == {"done", "total", "next_step"} and d["progress"]["total"] == 7 and isinstance(d["container"], bool)
    assert all(s["status"] in ("done", "partial", "todo", "skipped", "blocked") and s["command"] and s["title"] for s in d["steps"])
    assert {s["id"] for s in d["steps"] if s["optional"]} == {"classify", "onboarding", "schedule"}


def test_the_commands_follow_where_the_server_runs(ctx, monkeypatch):
    from coach import home as H
    monkeypatch.setattr(H, "on_container_filesystem", lambda *a, **k: False)
    monkeypatch.delenv("COACH_IN_CONTAINER", raising=False)
    d = ctx.get("/setup/wizard").json()
    assert d["container"] is False and d["command"] == "uv run coach setup" and d["commands"]["enablebanking"] == "uv run coach setup enablebanking"
    assert all(not s["command"].startswith("docker") for s in d["steps"])
    monkeypatch.setattr(H, "on_container_filesystem", lambda *a, **k: True)
    d = ctx.get("/setup/wizard").json()
    assert d["container"] is True and d["command"] == "docker compose run --rm coach setup"
    assert '-v "$PWD/eb.pem:/tmp/eb.pem:ro"' in d["commands"]["enablebanking"] and d["commands"]["enablebanking"].endswith("coach setup enablebanking")
    assert all(s["command"].startswith("docker compose") for s in d["steps"])
    assert not any("uv run" in s["command"] for s in d["steps"])


def test_the_wizard_status_follows_the_data(ctx):
    d = ctx.get("/setup/wizard").json()
    by = {s["id"]: s for s in d["steps"]}
    assert by["connect"]["status"] in ("done", "blocked") and by["sync"]["status"] in ("done", "blocked")
    if by["connect"]["status"] == "done":
        assert "bank account" in by["connect"]["detail"]


def test_there_is_no_way_to_start_a_step_from_the_api(ctx):
    for method in ("post", "put", "patch"):
        r = getattr(ctx, method)("/setup/wizard", {})
        assert r.status_code in (404, 405), (method, r.status_code)
    r = ctx.client.delete(api("/setup/wizard"), headers={"X-CSRF-Token": ctx.csrf})
    assert r.status_code in (404, 405)
    paths = ctx.app.openapi()["paths"]
    assert set(paths["/api/v1/setup/wizard"]) == {"get"} and set(paths["/api/v1/setup/doctor"]) == {"get"}
    assert not [p for p in paths if p.startswith("/api/v1/setup/") and p not in ("/api/v1/setup/wizard", "/api/v1/setup/doctor")]


def test_doctor_as_data_has_checks_and_next_steps_and_no_secret(ctx):
    r = ctx.get("/setup/doctor")
    assert r.status_code == 200
    d = r.json()
    assert {"checks", "next_steps", "ok"} <= set(d) and any(c["id"] == "python" and c["level"] == "ok" for c in d["checks"])
    assert all({"id", "level", "title", "detail", "hint"} <= set(c) for c in d["checks"])
    text = json.dumps(d)
    assert "test-db-key" not in text and "BEGIN" not in text


def test_the_endpoints_need_a_session(ctx):
    from apihelpers import make_client
    anon = make_client(ctx.app, logged_in=False)
    assert anon.get("/api/v1/setup/wizard").status_code == 401
    assert anon.get("/api/v1/setup/doctor").status_code == 401
