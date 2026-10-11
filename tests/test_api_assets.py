"""Rename and delete an asset from the web app: the loans and goals that point to it follow, in one recorded change."""
from __future__ import annotations

from apihelpers import Ctx, ctx, world  # noqa: F401

GOALS = "goals:\n  - id: house-fund\n    target_amount: 1000\n    asset: savings-book\n  - id: second\n    target_amount: 50\n    asset: savings-book\n"


def _ids(ctx: Ctx) -> set[str]:
    return {a["id"] for a in ctx.get("/assets").json()["assets"]}


def test_rename_previews_then_moves_the_loans_and_goals_in_one_change(ctx):
    mem = ctx.cfg.memory_dir
    (mem / "goals.yaml").write_text(GOALS)
    # the loan's `asset` is typed by hand ("family house"): point it to savings-book to follow it
    loan = mem / "liabilities" / "home-loan.yaml"
    loan.write_text(loan.read_text().replace("asset: family house", "asset: Savings-Book"))

    pv = ctx.post("/memory/assets/savings-book/rename", {"new_id": "livret-a"}, dry_run="true")
    assert pv.status_code == 200, pv.text
    d = pv.json()
    assert d["dry_run"] and d["changed"] and d["id"] == "livret-a" and d["old_id"] == "savings-book"
    assert {(r["type"], r["id"]) for r in d["references"]} == {("liability", "home-loan"), ("goal", "house-fund"), ("goal", "second")}
    for f in ("a/assets.yaml", "a/liabilities/home-loan.yaml", "a/goals.yaml"):
        assert f in d["diff"]
    assert "savings-book" in _ids(ctx)                                    # a preview writes nothing

    r = ctx.post("/memory/assets/savings-book/rename", {"new_id": "livret-a"})
    assert r.status_code == 200 and not r.json()["dry_run"]
    assert _ids(ctx) == {"livret-a", "family-house"}
    assert "asset: livret-a" in loan.read_text()
    assert (mem / "goals.yaml").read_text().count("asset: livret-a") == 2
    assert "# Assets not synced" in (mem / "assets.yaml").read_text()   # comments are kept
    ch = ctx.get("/memory/history", limit=1).json()["changes"][0]          # one change for the three files
    assert "rename-asset" in ch["subject"] and set(ch["files"]) >= {"assets.yaml", "goals.yaml", "liabilities/home-loan.yaml"}


def test_rename_guards(ctx):
    assert ctx.post("/memory/assets/nope/rename", {"new_id": "x"}).status_code == 404
    assert ctx.post("/memory/assets/savings-book/rename", {"new_id": "Bad Id"}).status_code in (409, 422)
    taken = ctx.post("/memory/assets/savings-book/rename", {"new_id": "family-house"})
    assert taken.status_code == 409 and "already used" in taken.json()["error"]["message"]
    other_kind = ctx.post("/memory/assets/savings-book/rename", {"new_id": "anna"})       # a member's id: refs stay unambiguous
    assert other_kind.status_code == 409
    assert ctx.post("/memory/assets/savings-book/rename", {"new_id": "savings-book"}).status_code == 409
    assert ctx.put("/memory/assets/flat", {"fields": {"kind": "real_estate_rental"}}).status_code == 200
    rental = ctx.post("/memory/assets/flat/rename", {"new_id": "flat-2"})
    assert rental.status_code == 409 and "property-flat" in rental.json()["error"]["message"]


def test_delete_previews_and_refuses_while_something_points_to_it(ctx):
    (ctx.cfg.memory_dir / "goals.yaml").write_text(GOALS)
    linked = ctx.post("/memory/assets/savings-book/delete", dry_run="true")
    assert linked.status_code == 409 and "goal house-fund" in linked.json()["error"]["message"]
    assert ctx.post("/memory/assets/nope/delete").status_code == 404

    pv = ctx.post("/memory/assets/family-house/delete", dry_run="true").json()
    assert pv["dry_run"] and "-  - id: family-house" in pv["diff"] and "family-house" in _ids(ctx)
    assert ctx.post("/memory/assets/family-house/delete").json()["changed"]
    assert _ids(ctx) == {"savings-book"}
    text = (ctx.cfg.memory_dir / "assets.yaml").read_text()
    assert "# Assets not synced" in text and "# tax-free savings" in text and "# to fill" not in text   # only its own comments go


def test_holder_joint_and_the_new_fields_are_written_and_read_back(ctx):
    assert ctx.put("/memory/assets/pee-anna", {"fields": {"kind": "employee_savings_plan", "holder": "anna", "employer": "Test Corp",
                                                          "opened": "2019-03-01", "balance": 1200, "as_of": "2026-10-01"}}).json()["changed"]
    a = next(x for x in ctx.get("/assets").json()["assets"] if x["id"] == "pee-anna")
    assert a["holder"] == "anna" and a["employer"] == "Test Corp" and a["opened"] == "2019-03-01"
    # a joint asset recorded with `holders` becomes a single holder: the form unsets `holders`
    assert ctx.put("/memory/assets/pee-anna", {"fields": {"holders": ["anna", "luca"]}}).json()["changed"]
    r = ctx.put("/memory/assets/pee-anna", {"fields": {"holder": "luca"}, "unset": ["holders"]}).json()
    a = next(x for x in ctx.get("/assets").json()["assets"] if x["id"] == "pee-anna")
    assert r["changed"] and a["holder"] == "luca" and a["holders"] is None
