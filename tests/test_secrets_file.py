"""E13-1: the `file` secret backend (containers, Linux): one 0600 file per secret in a 0700 folder, never the Keychain.

Everything lives in tmp_path; the fake keyring of conftest proves the Keychain is not touched.
"""
import os
import stat

import pytest

from coach import secrets as sec


@pytest.fixture
def fdir(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(d))
    return d


def put(d, name, value, mode=0o600):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    p = d / name
    p.write_text(value)
    os.chmod(p, mode)
    return p


def test_the_default_backend_is_the_keychain_and_a_bad_value_is_an_error(monkeypatch):
    assert sec.backend() == "keychain" and "Keychain" in sec.store_label()
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "vault")
    with pytest.raises(sec.SecretBackendError):
        sec.backend()


def test_lookup_reads_the_file_and_the_environment_wins(fdir, fake_keyring, monkeypatch):
    put(fdir, "db_key", "from-file\n")
    assert sec.lookup("db_key") == ("from-file", "file") and sec.get_secret("db_key") == "from-file"
    monkeypatch.setenv("COACH_DB_KEY", "from-env")
    assert sec.lookup("db_key") == ("from-env", "env")
    assert fake_keyring.store == {}                                   # the Keychain was never asked


def test_a_missing_or_empty_secret_is_not_found_and_the_message_names_the_file(fdir):
    assert sec.lookup("backup_key") == (None, None)
    put(fdir, "backup_key", "  \n")
    assert sec.lookup("backup_key") == (None, None)
    with pytest.raises(sec.SecretNotFound) as e:
        sec.get_secret("backup_key")
    assert str(fdir / "backup_key") in str(e.value) and "Keychain" not in str(e.value)
    assert sec.get_secret("anthropic_api_key", required=False) is None


def test_a_file_readable_by_others_is_refused_unless_the_read_only_escape_is_set(fdir, monkeypatch):
    put(fdir, "db_key", "v", 0o644)
    with pytest.raises(sec.SecretBackendError) as e:
        sec.lookup("db_key")
    assert "chmod 600" in str(e.value)
    monkeypatch.setenv("COACH_SECRETS_ALLOW_READABLE", "1")             # docker swarm / kubernetes mount secrets 0444
    assert sec.lookup("db_key") == ("v", "file")
    os.chmod(fdir / "db_key", 0o666)                                    # writable by others: never accepted
    with pytest.raises(sec.SecretBackendError):
        sec.lookup("db_key")


def test_set_secret_writes_a_private_file_atomically_and_delete_removes_it(fdir, fake_keyring):
    sec.set_secret("db_key", "s3cret")
    p = fdir / "db_key"
    assert p.read_text().strip() == "s3cret"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE(fdir.stat().st_mode) == 0o700
    assert [x.name for x in fdir.iterdir()] == ["db_key"]               # no temp file left behind
    assert sec.in_keychain("db_key") and not sec.in_keychain("backup_key")
    sec.delete_secret("db_key")
    sec.delete_secret("db_key")                                         # idempotent
    assert not p.exists() and fake_keyring.store == {}


def test_unknown_secret_names_cannot_escape_the_folder(fdir):
    with pytest.raises(KeyError):
        sec.set_secret("../evil", "x")
    with pytest.raises(KeyError):
        sec.lookup("../../etc/passwd")


def test_describe_masks_values_and_the_cli_message_names_the_folder(fdir, capsys):
    put(fdir, "db_key", "TOPSECRETVALUE")
    text = " ".join(f"{n} {s}" for n, s in sec.describe())
    assert "TOPSECRETVALUE" not in text and "set (file)" in text
    from coach.cli import main
    main(["config", "set-secret", "backup_key", "--generate"])
    out = capsys.readouterr().out
    assert str(fdir) in out and "Keychain" not in out and (fdir / "backup_key").is_file()
    assert (fdir / "backup_key").read_text().strip() not in out


def test_a_symlinked_secret_is_followed_like_kubernetes_mounts_it(fdir, tmp_path, monkeypatch):
    real = put(tmp_path / "elsewhere", "payload", "linked-value")
    fdir.mkdir()
    os.chmod(fdir, 0o700)
    os.symlink(real, fdir / "db_key")
    assert sec.lookup("db_key") == ("linked-value", "file")


class FakeStat:
    def __init__(self, mode, uid):
        self.st_mode, self.st_uid = mode, uid


def test_the_folder_must_not_be_writable_by_others(fdir):
    put(fdir, "db_key", "v")
    os.chmod(fdir, 0o755)                                              # readable by others is fine (compose mounts /run/secrets that way)
    assert sec.lookup("db_key") == ("v", "file")
    os.chmod(fdir, 0o777)                                              # writable by others: refused
    with pytest.raises(sec.SecretBackendError) as e:
        sec.lookup("db_key")
    assert "writable by others" in str(e.value)
    os.chmod(fdir, 0o1777)                                             # a sticky tmpfs (Kubernetes) is accepted
    assert sec.lookup("db_key") == ("v", "file")


def test_a_root_owned_0755_folder_as_compose_mounts_it_is_accepted_for_a_non_root_user(fdir, monkeypatch):
    put(fdir, "db_key", "v")
    real_stat = sec._stat

    def fake(p):
        st = real_stat(p)
        if p == fdir:
            return FakeStat(stat.S_IFDIR | 0o755, 0)                  # root-owned, 0755
        return st
    monkeypatch.setattr(sec, "_stat", fake)
    monkeypatch.setattr(sec.os, "getuid", lambda: 10001)              # the image's user
    # the file here is ours (a bind mount of the host's 0600 file with COACH_UID): accepted
    monkeypatch.setattr(sec, "_owner_ok", lambda st: st.st_uid in (0, os.stat(fdir).st_uid))
    assert sec.lookup("db_key") == ("v", "file")


def test_a_folder_or_file_owned_by_a_third_user_is_refused(fdir, monkeypatch):
    put(fdir, "db_key", "v")
    me = os.stat(fdir).st_uid
    monkeypatch.setattr(sec.os, "getuid", lambda: me + 7)              # we are not the owner, and the owner is not root either
    with pytest.raises(sec.SecretBackendError) as e:
        sec.lookup("db_key")
    assert "owned by another user" in str(e.value)


def test_a_root_owned_secret_file_needs_the_documented_read_only_mode(fdir, monkeypatch):
    p = put(fdir, "db_key", "v", 0o444)
    real_stat = sec._stat
    monkeypatch.setattr(sec, "_stat", lambda q: FakeStat(stat.S_IFREG | 0o444, 0) if q == p else real_stat(q))
    with pytest.raises(sec.SecretBackendError) as e:
        sec.lookup("db_key")                                           # 0444: readable by everyone
    assert "mode 0400" in str(e.value) or "COACH_SECRETS_ALLOW_READABLE" in str(e.value)
    monkeypatch.setattr(sec, "_stat", lambda q: FakeStat(stat.S_IFREG | 0o400, 0) if q == p else real_stat(q))
    assert sec.lookup("db_key") == ("v", "file")                      # compose `mode: 0400`
    monkeypatch.setattr(sec, "_stat", lambda q: FakeStat(stat.S_IFREG | 0o444, 0) if q == p else real_stat(q))
    monkeypatch.setenv("COACH_SECRETS_ALLOW_READABLE", "1")           # swarm / Kubernetes 0444
    assert sec.lookup("db_key") == ("v", "file")
    monkeypatch.setattr(sec, "_stat", lambda q: FakeStat(stat.S_IFREG | 0o666, 0) if q == p else real_stat(q))
    with pytest.raises(sec.SecretBackendError):
        sec.lookup("db_key")                                           # writable by others: never


def test_writing_never_follows_a_planted_symlink_and_uses_a_random_temporary_name(fdir, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    fdir.mkdir()
    os.chmod(fdir, 0o700)
    os.symlink(victim, fdir / ".db_key.tmp")                           # the old, predictable temporary name
    sec.set_secret("db_key", "s3cret")
    assert victim.read_text() == "precious" and (fdir / "db_key").read_text().strip() == "s3cret"
    names = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        if str(path).endswith(".tmp"):
            names.append((os.path.basename(str(path)), flags, mode))
        return real_open(path, flags, mode, *a, **k)
    import coach.secrets as m
    m.os.open = spy
    try:
        sec.set_secret("backup_key", "a")
        sec.set_secret("backup_key", "b")
    finally:
        m.os.open = real_open
    assert len(names) == 2 and names[0][0] != names[1][0] and all(n != ".backup_key.tmp" for n, _, _ in names)
    assert all(f & os.O_EXCL and f & os.O_CREAT and (not hasattr(os, "O_NOFOLLOW") or f & os.O_NOFOLLOW) and mode == 0o600 for _, f, mode in names)
    assert [x.name for x in fdir.iterdir() if x.name.endswith(".tmp") and x.name != ".db_key.tmp"] == []


def test_the_secret_is_synced_to_disk_before_the_rename(fdir, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(sec.os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    sec.set_secret("db_key", "x")
    assert len(calls) >= 1
