"""E13-2: the first-run wizard (`coach setup`). Every network / side-effect action is a recording fake: the tests prove WHEN each one is called
(only after the typed consent of its step), that steps detect their own state (idempotent, resumable), and that nothing is sent otherwise."""
import json
import os
import stat
from argparse import Namespace

import pytest

from coach import config as config_mod, db as db_mod
from coach.setup import init as I, wizard as W


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("COACH_LAUNCHAGENTS_DIR", str(tmp_path / "agents"))        # never look at the real ~/Library/LaunchAgents
    I.run_init(tmp_path / "home", interactive=False, container=False, out=lambda *a: None)
    return tmp_path / "home"


def load(home):
    return config_mod.load_config(home / "config.toml", env={})


def configure_eb(home, tmp_path):
    key = tmp_path / "eb.pem"
    key.write_text("-----BEGIN " + "PRIVATE KEY-----\nAAAA\n-----END " + "PRIVATE KEY-----\n")
    os.chmod(key, 0o600)
    text = (home / "config.toml").read_text()
    for k, v in (("app_id", "00000000-1111-2222-3333-444444444444"), ("redirect_url", "https://localhost:8443/callback"), ("private_key_path", str(key))):
        text = config_mod.set_toml_value(text, "enable_banking", k, v)
    (home / "config.toml").write_text(text)


class Rec:
    """Recording fakes of every action of the wizard."""

    def __init__(self, cfg_path):
        self.calls = []
        self.cfg_path = cfg_path

    def __getattr__(self, name):
        raise AttributeError(name)

    def actions(self, **over):
        rec = self

        def mk(name, fn=None):
            def f(*a, **k):
                rec.calls.append((name, a[1:] if a else (), k))
                return fn(*a, **k) if fn else None
            return f
        base = dict(check=mk("check"), banks=mk("banks"), connect=mk("connect"), sync=mk("sync"), normalize=mk("normalize"),
                    classify=mk("classify"), onboarding=mk("onboarding"), schedule_install=mk("schedule_install"),
                    eb_guide=lambda cfg, **k: (rec.calls.append(("eb_guide", (), {})), {"configured": False, "checked": False, "changed": []})[1])
        base.update(over)
        return W.Actions(**base)

    def names(self):
        return [c[0] for c in self.calls]


def ctx_for(home, answers, rec, **over):
    it = iter(answers)
    said = []
    ctx = W.Ctx(cfg=load(home), state=W.State.for_cfg(load(home)), isatty=lambda: True, input_fn=lambda p: next(it), out=said.append,
                actions=rec.actions(**over))
    ctx.said = said
    return ctx


def add_account(cfg):
    from helpers import add_bank
    con = db_mod.connect(cfg)
    add_bank(con, "s1", "Bank A", "FR", [("a1", "FR7630006000011234567890189", "Main")])
    con.close()


def add_transaction(cfg):
    from helpers import add_tx
    con = db_mod.connect(cfg)
    add_tx(con, "a1", "k1", "2026-09-01", -10.0, "CARD PAYMENT SHOP")
    con.close()


def steps(home):
    return {d.id: d for d in W.detect(load(home))}


# ---------------------------------------------------------------- detection

def test_a_fresh_init_leaves_step_one_done_and_the_next_ones_waiting(home):
    s = steps(home)
    assert s["init"].status == "done" and s["enablebanking"].status == "todo"
    assert [s[x].status for x in ("connect", "sync", "classify")] == ["blocked"] * 3 and "Enable Banking" in s["connect"].detail
    assert s["onboarding"].status == "todo" and s["schedule"].status == "todo"
    st = W.status(load(home))
    assert st["progress"] == {"done": 1, "total": 7, "next_step": "enablebanking"} and [x["id"] for x in st["steps"]] == list(W.STEPS)
    assert all(x["command"] for x in st["steps"]) and {x["id"] for x in st["steps"] if x["optional"]} == set(W.OPTIONAL)


def test_without_a_database_the_init_step_is_todo_and_the_rest_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / "config.toml").write_text('data_dir = "data"\nmemory_dir = "memory"\n')
    s = {d.id: d for d in W.detect(config_mod.load_config(tmp_path / "h" / "config.toml", env={}))}
    assert s["init"].status == "todo" and s["connect"].status == "blocked" and s["sync"].status == "blocked"


def test_the_states_follow_the_real_data(home, tmp_path):
    configure_eb(home, tmp_path)
    assert steps(home)["enablebanking"].status == "partial" and steps(home)["connect"].status == "todo"   # configured counts as a way forward
    add_account(load(home))
    s = steps(home)
    assert s["connect"].status == "done" and s["sync"].status == "todo" and s["classify"].status == "blocked"
    add_transaction(load(home))
    assert steps(home)["sync"].status == "done" and steps(home)["classify"].status == "todo"
    con = db_mod.connect(load(home))
    from coach.classify.normalize import normalize_all
    normalize_all(con, load(home).memory_dir)
    con.close()
    assert steps(home)["classify"].status == "partial"


# ---------------------------------------------------------------- consent: nothing is sent without the typed word

def test_empty_answers_stop_the_wizard_and_call_nothing(home):
    rec = Rec(home)
    ctx = ctx_for(home, [""], rec)
    W.run_wizard(ctx)
    assert rec.calls == [] and any("Stopped" in str(s) for s in ctx.said)


def test_unrecognised_answers_never_count_as_yes(home):
    rec = Rec(home)
    ctx = ctx_for(home, ["maybe", "ok", "sure"], rec)
    W.run_wizard(ctx)
    assert rec.calls == [] and any("No valid answer" in str(s) for s in ctx.said)


def test_the_wizard_refuses_without_a_terminal(home):
    rec = Rec(home)
    ctx = ctx_for(home, [], rec)
    ctx.isatty = lambda: False
    with pytest.raises(SystemExit) as e:
        W.run_wizard(ctx)
    assert "terminal" in str(e.value) and rec.calls == []


def test_the_enable_banking_step_runs_the_guide_and_marks_done_only_when_checked(home, tmp_path):
    rec = Rec(home)

    def guide(cfg, **k):
        rec.calls.append(("eb_guide", (), {}))
        configure_eb(home, tmp_path)
        return {"configured": True, "checked": True, "changed": ["app_id"]}
    ctx = ctx_for(home, ["y", "q"], rec, eb_guide=guide)
    W.run_wizard(ctx)
    assert rec.names() == ["eb_guide"] and ctx.state.step("enablebanking")["status"] == "done"
    assert steps(home)["enablebanking"].status == "done"


def test_connect_needs_the_word_connect_and_a_two_letter_country(home, tmp_path):
    configure_eb(home, tmp_path)
    for answers in (["y", "F", ""], ["y", "fr", "Bank A", "yes"], ["y", "fr", "Bank A", "CONNECT?"]):
        rec = Rec(home)
        ctx = ctx_for(home, answers + ["q"], rec)
        W.run_wizard(ctx, only="connect")
        assert "connect" not in rec.names(), answers
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "fr", "Bank A", "connect"], rec)
    W.run_wizard(ctx, only="connect")
    assert rec.calls[-1][0] == "connect" and rec.calls[-1][1][:2] == ("Bank A", "FR")


def test_listing_banks_asks_for_its_own_consent(home, tmp_path):
    configure_eb(home, tmp_path)
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "FR", "?", "no", ], rec)
    W.run_wizard(ctx, only="connect")
    assert rec.names() == []
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "FR", "?", "list", "bank", "Bank A", "connect"], rec)
    W.run_wizard(ctx, only="connect")
    assert rec.names() == ["banks", "connect"]


def test_a_failed_connection_is_reported_and_the_step_stays_todo(home, tmp_path):
    configure_eb(home, tmp_path)
    rec = Rec(home)

    def fail(*a, **k):
        raise SystemExit("error: consent refused")
    ctx = ctx_for(home, ["y", "FR", "Bank A", "connect"], rec, connect=fail)
    W.run_wizard(ctx, only="connect")
    assert any("did not complete" in str(s) for s in ctx.said) and steps(home)["connect"].status == "todo"


def test_sync_needs_the_word_sync(home, tmp_path):
    configure_eb(home, tmp_path)
    add_account(load(home))
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "yes"], rec), only="sync")
    assert rec.names() == []
    rec = Rec(home)

    def do_sync(cfg, insecure=False):
        rec.calls.append(("sync", (), {}))
        add_transaction(cfg)
    ctx = ctx_for(home, ["y", "sync"], rec, sync=do_sync)
    W.run_wizard(ctx, only="sync")
    assert rec.names() == ["sync"] and steps(home)["sync"].status == "done"


def prepared(home, tmp_path, normalized=True):
    configure_eb(home, tmp_path)
    add_account(load(home))
    add_transaction(load(home))


def test_classify_dry_run_always_first_then_nothing_is_sent_without_the_word_send(home, tmp_path):
    prepared(home, tmp_path)
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "1", "yes"], rec)
    W.run_wizard(ctx, only="classify")
    assert rec.names() == ["normalize", "classify"] and rec.calls[1][1][0] is True                 # the dry run, and only it
    assert any("never sent" in str(s) for s in ctx.said) and any("claude-code" in str(s) for s in ctx.said)
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "1", "send"], rec)
    W.run_wizard(ctx, only="classify")
    assert [c[1][0] for c in rec.calls if c[0] == "classify"] == [True, False]                     # dry run, then the real run
    assert ctx.state.choice("classify_backend") == "claude-code"


def test_classify_skip_is_remembered_and_resumable(home, tmp_path):
    prepared(home, tmp_path)
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "3"], rec), only="classify")
    assert [c[1][0] for c in rec.calls if c[0] == "classify"] == [True]
    assert steps(home)["classify"].status == "skipped"
    rec2 = Rec(home)
    W.State.for_cfg(load(home)).mark("enablebanking", "done")
    ctx = ctx_for(home, ["q"], rec2)                                                              # a new run: skipped steps are not asked again
    W.run_wizard(ctx)
    assert any("skipped earlier" in str(s) for s in ctx.said)
    W.State.for_cfg(load(home)).reset()
    assert steps(home)["classify"].status == "todo"


def test_the_local_only_choice_edits_the_privacy_settings_after_a_typed_word(home, tmp_path):
    prepared(home, tmp_path)
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "2", "no"], rec), only="classify")
    assert load(home).privacy_local_only is False and load(home).llm_backend == "claude-code"
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "2", "local"], rec), only="classify")
    cfg = load(home)
    assert cfg.privacy_local_only is True and cfg.llm_backend == "ollama" and cfg.coach_backend == "ollama"
    assert [c[1][0] for c in rec.calls if c[0] == "classify"] == [True]                           # nothing was labelled
    assert "# database, logs" in (home / "config.toml").read_text()                              # comments survive the edit


def test_a_cloud_run_is_refused_under_local_only_without_asking_to_send(home, tmp_path):
    prepared(home, tmp_path)
    text = config_mod.set_toml_bool((home / "config.toml").read_text(), "privacy", "local_only", True)
    (home / "config.toml").write_text(text)
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "1"], rec)
    W.run_wizard(ctx, only="classify")
    assert [c[1][0] for c in rec.calls if c[0] == "classify"] == [True] and any("not allowed" in str(s) for s in ctx.said)


def test_onboarding_needs_start_and_schedule_never_enables_alert_channels(home, tmp_path):
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "go"], rec), only="onboarding")
    assert rec.names() == []
    W.run_wizard(ctx_for(home, ["y", "start"], rec), only="onboarding")
    assert rec.names() == ["onboarding"]
    before = (home / "config.toml").read_text()
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "no"], rec)
    W.run_wizard(ctx, only="schedule")
    assert any("all OFF" in str(s) for s in ctx.said) and rec.names() == [] and (home / "config.toml").read_text() == before


def test_schedule_install_on_macos_needs_the_word_install_and_a_container_gets_the_loop(home, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    monkeypatch.setattr(W.doctor_mod, "in_container", lambda: False)
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "yes"], rec), only="schedule")
    assert rec.names() == []
    W.run_wizard(ctx_for(home, ["y", "install"], rec), only="schedule")
    assert rec.names() == ["schedule_install"]
    monkeypatch.setattr(W.doctor_mod, "in_container", lambda: True)
    rec = Rec(home)
    ctx = ctx_for(home, ["y", "noted"], rec)
    W.run_wizard(ctx, only="schedule")
    assert any("schedule loop" in str(s) for s in ctx.said) and rec.names() == []


# ---------------------------------------------------------------- the state file, idempotence, the command

def test_the_state_file_is_private_and_holds_no_personal_data(home, tmp_path):
    prepared(home, tmp_path)
    rec = Rec(home)
    W.run_wizard(ctx_for(home, ["y", "3"], rec), only="classify")
    p = load(home).data_dir / "setup-state.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert set(data) <= {"version", "steps", "choices", "updated"} and data["steps"]["classify"]["status"] == "skipped"
    text = p.read_text()
    assert "Bank A" not in text and "FR76" not in text and "SHOP" not in text


def test_running_the_wizard_again_when_everything_is_done_asks_and_calls_nothing(home, tmp_path):
    prepared(home, tmp_path)
    con = db_mod.connect(load(home))
    con.execute("INSERT INTO merchants(merchant_key, merchant_name, category, confidence, recurring_hint, source, model, updated_at) "
                "VALUES ('SHOP','Shop','food.groceries',0.9,0,'rule',NULL,'t')")
    con.commit()
    con.close()
    (home / "memory" / "household.yaml").write_text("members:\n  - id: adult-a\n    name: Test Person\n    role: adult\n")
    st = W.State.for_cfg(load(home))
    st.mark("enablebanking", "done")
    st.mark("schedule", "skipped")
    rec = Rec(home)
    said = []
    ctx = W.Ctx(cfg=load(home), state=st, isatty=lambda: True, input_fn=lambda p: pytest.fail(f"asked: {p}"), out=said.append, actions=rec.actions())
    W.run_wizard(ctx)
    text = "\n".join(map(str, said))
    assert text.count("already done") == 6 and "skipped earlier" in text and "Setup finished." in text and rec.calls == []


def test_status_and_reset_commands(home, capsys):
    cfg = load(home)
    W.cmd_setup(Namespace(setup_cmd=None, status=True, json=False, reset=False, step=None, insecure=False), cfg)
    out = capsys.readouterr().out
    assert "1/7 steps done" in out and "[x] init" in out and "[#] connect" in out
    W.cmd_setup(Namespace(setup_cmd=None, status=False, json=True, reset=False, step=None, insecure=False), cfg)
    assert json.loads(capsys.readouterr().out)["progress"]["next_step"] == "enablebanking"
    W.State.for_cfg(cfg).mark("schedule", "skipped")
    W.cmd_setup(Namespace(setup_cmd=None, status=False, json=False, reset=True, step=None, insecure=False), cfg)
    assert "cleared" in capsys.readouterr().out and not (cfg.data_dir / "setup-state.json").exists()


def test_the_commands_are_registered(home):
    from coach.cli import build_parser
    p = build_parser()
    assert p.parse_args(["setup", "--status"]).fn is W.cmd_setup
    a = p.parse_args(["setup", "enablebanking"])
    assert a.fn is W.cmd_setup and a.setup_cmd == "enablebanking"
    assert p.parse_args(["setup", "--step", "sync"]).step == "sync"
    with pytest.raises(SystemExit):
        p.parse_args(["setup", "--step", "nonsense"])


def test_the_default_actions_are_the_real_commands_wired_without_a_yes_flag():
    import inspect
    src = inspect.getsource(W.default_actions)
    assert "--yes" not in src and "yes=True" not in src
    a = W.default_actions()
    assert all(callable(getattr(a, f)) for f in ("check", "banks", "connect", "sync", "normalize", "classify", "onboarding", "schedule_install", "eb_guide"))
