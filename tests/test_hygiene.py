"""E13-4: repository hygiene. Two layers: structural rules in the tree (keys, home folders, valid IBANs, e-mail addresses) and the real-data terms of a
LOCAL file that is never part of the tree. Everything below uses INVENTED terms: no real name, place, merchant or amount is written in this file, and the
package carries no hash, salt or list of real terms."""
import json
import os
import re
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from coach import db as db_mod, hygiene as H, release as R

ROOT = Path(__file__).resolve().parents[1]


def tree(tmp_path, files, gitignore=""):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / ".gitignore").write_text(gitignore)
    return tmp_path


def rels(root):
    return sorted(p.relative_to(root).as_posix() for p in H.publishable_files(root))


def index(*pairs):
    return H.TermIndex([H.Term(i, c, t) for i, (c, t) in enumerate(pairs, 1)])


# ---------------------------------------------------------------- the real repository

def test_no_publishable_file_of_the_repository_breaks_a_structural_rule():
    """Runs everywhere (CI has no local term file): keys, home folders, valid IBANs, e-mail addresses."""
    files = H.publishable_files(ROOT)
    assert len(files) > 300
    hits = H.scan(ROOT, files)
    assert hits == [], "structural hits in publishable files:\n" + "\n".join(map(str, hits))


def test_with_the_owners_local_term_file_the_repository_is_clean_too():
    """Only on the machine that has the file (`coach dev hygiene --build-terms`): the real-data rule over every publishable file."""
    path = H.terms_path(ROOT)
    if H.terms_file_problem(path):
        pytest.skip("no usable local term file here (CI, a fresh clone): the owner's release-check runs this rule")
    idx = H.TermIndex(H.load_terms(path))
    hits = H.scan(ROOT, H.publishable_files(ROOT), idx)
    assert hits == [], "real-data hits (replace with invented values):\n" + "\n".join(map(str, hits))


def test_the_package_ships_no_hash_salt_or_real_term():
    """A hash of a name is reversible with a dictionary: the guard keeps its terms on the owner's machine only."""
    src = (ROOT / "src" / "coach" / "hygiene.py").read_text()
    assert not re.search(r"\b[0-9a-f]{40,}\b", src) and "SALT" not in src and "REAL_DATA_HASHES" not in src and "sha256" not in src
    assert not hasattr(H, "REAL_DATA_HASHES") and not hasattr(H, "SALT") and not hasattr(H, "token_hash")


def test_the_local_term_file_can_never_be_published():
    pub = {p.relative_to(ROOT).as_posix() for p in H.publishable_files(ROOT)}
    assert "hygiene-terms.txt" not in pub
    assert any(ln.strip() == "hygiene-terms*" for ln in (ROOT / ".gitignore").read_text().splitlines())


def test_the_personal_paths_of_the_real_repository_are_not_publishable():
    pub = {p.relative_to(ROOT).as_posix() for p in H.publishable_files(ROOT)}
    for private in (".claude/settings.json", "config.toml", "config/taxonomy.yaml", "memory/household.yaml", "memory/profile.md", "memory/open-questions.yaml",
                    "memory/liabilities/mortgage.yaml", "data/finance.db", "backups/x.tar.enc", "src/coach/api/static/index.html", "web/node_modules/x/index.js",
                    "secrets/db_key", "coach-home/data/finance.db", "hygiene-terms.txt"):
        assert private not in pub, private
    for shipped in ("memory/README.md", "memory/liabilities/_template.yaml", "memory/contracts/_template.yaml", "config.example.toml", "config/import_profiles/revolut-csv.toml",
                    "src/coach/templates/memory/household.yaml", "README.md", "docs/security.md", ".claude/skills/analytics-overview/SKILL.md", "docs/research/market-validation.md",
                    "web/src/pages/Setup.tsx", ".gitignore", "Dockerfile"):
        assert shipped in pub, shipped
    assert not [p for p in pub if p.startswith(("memory/", "data/", "backups/")) and p not in (
        "memory/README.md", "memory/liabilities/_template.yaml", "memory/contracts/_template.yaml")]


def test_everything_the_old_per_directory_guard_read_is_still_read():
    pub = {p.relative_to(ROOT).as_posix() for p in H.publishable_files(ROOT)}
    for rel in ("README.md", "CLAUDE.md", "config.example.toml", "tests/test_egress_policy.py", "src/coach/cli.py", "docs/BACKLOG.md", "evals/coach_questions.yaml",
                "web/src/main.tsx", "src/coach/classify/rules.yaml", "src/coach/classify/parsers/common.py"):
        assert rel in pub, rel


# ---------------------------------------------------------------- .gitignore semantics

def test_gitignore_last_match_wins_negation_and_parent_exclusion(tmp_path):
    gi = "memory/*\n!memory/README.md\n!memory/liabilities/\nmemory/liabilities/*\n!memory/liabilities/_template.yaml\n**/data/\n*.pem\n/secrets/\nconfig.toml\n*.bak\n"
    root = tree(tmp_path, {
        "memory/README.md": "x", "memory/profile.md": "x", "memory/liabilities/_template.yaml": "x", "memory/liabilities/mortgage.yaml": "x",
        "a/data/x.txt": "x", "data/x.txt": "x", "k.pem": "x", "sub/k.pem": "x", "secrets/db_key": "x", "src/secrets/ok.py": "x",
        "config.toml": "x", "docs/config.toml.md": "x", "t.py.bak": "x", "keep.py": "x"}, gi)
    assert rels(root) == [".gitignore", "docs/config.toml.md", "keep.py", "memory/README.md", "memory/liabilities/_template.yaml", "src/secrets/ok.py"]


def test_an_excluded_folder_cannot_be_reincluded_from_inside(tmp_path):
    root = tree(tmp_path, {"build/keep.txt": "x", "other/keep.txt": "x"}, "build/\n!build/keep.txt\n")
    assert rels(root) == [".gitignore", "other/keep.txt"]


def test_tool_folders_and_symlinks_are_never_scanned(tmp_path):
    root = tree(tmp_path, {".venv/lib/x.py": "x", "web/node_modules/p/i.js": "x", "__pycache__/a.py": "x", "ok.py": "x"})
    (tmp_path / "link.py").symlink_to(tmp_path / "ok.py")
    assert rels(root) == [".gitignore", "ok.py"]


def test_the_git_file_of_a_worktree_or_submodule_is_never_scanned(tmp_path):
    # In a git worktree (and in a submodule) ".git" is a file pointing at the main repository, by absolute path: git never publishes it.
    root = tree(tmp_path, {".git": "gitdir: " + HOME_DIR + "/.git/worktrees/w1\n", "sub/.git": "gitdir: ../.git/modules/sub\n", "ok.py": "x"})
    assert rels(root) == [".gitignore", "ok.py"]
    assert H.scan(root, H.publishable_files(root), None) == []


# ---------------------------------------------------------------- the structural rules

# The trigger strings are assembled at run time: this file is itself scanned, and must not contain a real-looking secret.
PEM = "-----BEGIN " + "PRIVATE KEY-----"
HOME_DIR = "/Us" + "ers/jdoe2/Projects/x"
LIN_DIR = "/ho" + "me/maria2/data"


@pytest.mark.parametrize("line,rule", [
    ("-----BEGIN " + "RSA PRIVATE KEY-----", "private-key"), (PEM, "private-key"),
    ("key = sk-ant-" + "api03-" + "a1B2c3D4" * 4, "api-key"),
    ("path = " + HOME_DIR, "home-folder"), ("cd " + LIN_DIR, "home-folder"),
    ("mail someone.real@" + "gmail.com now", "email"),
    ("IBAN FR76 3000 4000 " + "0312 3456 7890 143", "iban"),
])
def test_each_structural_rule_fires(line, rule):
    assert [h.rule for h in H.scan_text("x.md", line + "\n")] == [rule]


@pytest.mark.parametrize("line", [
    "path = /Us" + "ers/you/Projects/x", "/ho" + "me/runner/work", "/Us" + "ers/<name>/x", "a@example.org", "noreply@anthropic.com", "x@y.org",
    "IBAN FR7630006000011234567890189", "GB82 WEST 1234 5698 7654 32", "FR0030006000011234567890189",
    "sk-" + "ant-test-key-should-not-pass", "-----BEGIN " + "CERTIFICATE-----", "pkg@1.2.3 is a version",
])
def test_invented_and_example_values_pass(line):
    assert H.scan_text("x.md", line + "\n") == []


def test_iban_validator():
    assert H.iban_valid("FR7630006000011234567890189") and H.iban_valid("GB82 WEST 1234 5698 7654 32")
    assert not H.iban_valid("FR0030006000011234567890189") and not H.iban_valid("FR76")
    assert not H.iban_valid("FR76000000000000000000")


def test_binary_and_lock_files_are_skipped_and_undecodable_files_do_not_crash(tmp_path):
    root = tree(tmp_path, {"pnpm-lock.yaml": "foo@1.2.3 " + PEM + "\n", "x.png": PEM})
    (root / "blob.dat").write_bytes(b"\xff\xfe\x00\x01" + PEM.encode())
    assert H.scan(root) == []


# ---------------------------------------------------------------- the local terms: matching

def test_a_term_is_found_whatever_the_case_accents_punctuation_or_spacing():
    idx = index(("name", "Zorbax Vilé"), ("merchant", "QUINCAILLERIE ZEPHYRA LOZE"))
    for text in ("ZORBAX VILE", "zorbax  vile", "Zorbax-Vilé's", "zörbax_vile!", "x (ZORBAX\tVILE) y"):
        assert [t.n for t in idx.find(text)] == [1], text
    assert [t.n for t in idx.find("paid quincaillerie-zephyra loze 12/03")] == [2]
    assert idx.find("zorbaxvile") == [] and idx.find("zorbax") == [] and idx.find("vile zorbax") == []          # whole words, in order


def test_an_identifier_is_also_found_through_spacing_inside_it():
    idx = index(("id", "ZQ12 9987 3341"), ("account", "XK41-0073-5521"))
    assert [t.n for t in idx.find("iban FR76 ZQ12 9987 3341 000")] == [1]
    assert [t.n for t in idx.find("ref zq1299873341")] == [1]
    assert [t.n for t in idx.find("xk41 0073 5521")] == [2]


def test_amounts_are_found_in_every_common_written_form_and_as_cents():
    idx = index(("amount", "1234.56"), ("amount", "88.40"), ("amount", "30000"))
    for text in ("1234.56", "1 234,56", "1 234,56 EUR", "1,234.56", "1.234,56", "1234,56", "-1234.56", "amount=1234.56;", "total 123456 cents"):
        assert [t.n for t in idx.find(text)] == [1], text
    assert [t.n for t in idx.find("88,40 EUR")] == [2] and [t.n for t in idx.find("8840")] == [2]
    assert [t.n for t in idx.find("capital 30 000 EUR")] == [3] and [t.n for t in idx.find("30000.00")] == [3]
    for text in ("1234.57", "12.34, 56", "version 1.2.3456", "88.41", "30, 000", "(1234, 56)"):
        assert idx.find(text) == [], text


def test_numbers_inside_versions_and_lists_are_not_mistaken_for_amounts():
    idx = index(("amount", "44.05"), ("amount", "17.23"))
    assert idx.find('"jsdom": "^30.1.1"') == [] and idx.find("DISCRETIONARY = (30, 100)") == []
    assert [t.n for t in idx.find("lines = [(17.23, 5), (44.05, 6)]")] == [1, 2] or [t.n for t in idx.find("lines = [(17.23, 5), (44.05, 6)]")] == [2, 1]


def test_a_hit_reports_file_line_category_and_number_and_never_the_term(tmp_path):
    root = tree(tmp_path, {"docs/a.md": "nothing\nI live in Zorbaxville.\n", "b.py": "x = 'ZORBAXVILLE' + 'Quorvex Lane'\n"})
    idx = index(("place", "Zorbaxville"), ("name", "Quorvex Lane"))
    hits = H.scan(root, index=idx)
    assert sorted(str(h) for h in hits) == ["b.py:1  [real-data:name#2]", "b.py:1  [real-data:place#1]", "docs/a.md:2  [real-data:place#1]"]
    assert not re.search(r"zorbax|quorvex", " ".join(str(h) for h in hits), re.I)


def test_without_an_index_only_the_structural_rules_run(tmp_path):
    root = tree(tmp_path, {"a.md": "Zorbaxville\n"})
    assert H.scan(root) == []


def test_scan_names_covers_the_members_of_an_archive():
    idx = index(("merchant", "Zephyra Hardware Loze"))
    hits = H.scan_names([("pkg-1.0/a.txt", "fine"), ("pkg-1.0/b.txt", "line\nbought at ZEPHYRA HARDWARE LOZE\n")], idx)
    assert [str(h) for h in hits] == ["pkg-1.0/b.txt:2  [real-data:merchant#1]"]


# ---------------------------------------------------------------- the local term file

def test_the_term_file_is_written_0600_and_loads_back(tmp_path):
    path = tmp_path / "t" / "hygiene-terms.txt"
    n = H.write_terms(path, {"name": {"Zorbax Vile", "Quorvex"}, "amount": {"1234.56"}, "merchant": {"ZEPHYRA HARDWARE LOZE"}})
    assert n == 4 and stat.S_IMODE(path.stat().st_mode) == 0o600
    terms = H.load_terms(path)
    assert [(t.n, t.category) for t in terms] == [(1, "name"), (2, "name"), (3, "merchant"), (4, "amount")]
    assert path.read_text().startswith("# LOCAL ONLY")
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_missing_empty_or_loose_term_file_is_reported_with_its_reason(tmp_path):
    p = tmp_path / "terms.txt"
    assert H.terms_file_problem(p) == "missing"
    p.write_text("# only comments\n")
    os.chmod(p, 0o600)
    assert H.terms_file_problem(p) == "empty"
    p.write_text("name\tZorbax\n")
    os.chmod(p, 0o644)
    assert "readable by other users" in H.terms_file_problem(p)
    os.chmod(p, 0o600)
    assert H.terms_file_problem(p) is None
    assert H.load_terms(tmp_path / "nope.txt") == []


def test_the_term_file_default_is_the_per_user_home_outside_the_repository(tmp_path, monkeypatch):
    default = H.terms_path(ROOT)
    assert default == Path.home() / ".ai-finance-coach" / "hygiene-terms.txt"
    assert not H.inside_repository(default) and H.terms_path() == default
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(tmp_path / "elsewhere.txt"))
    assert H.terms_path(ROOT) == tmp_path / "elsewhere.txt"


def test_a_term_file_inside_the_repository_is_refused_not_used(tmp_path, monkeypatch):
    inside = ROOT / "hygiene-terms.txt"
    assert H.inside_repository(inside) and H.inside_repository(ROOT / "sub" / "t.txt") and not H.inside_repository(tmp_path / "t.txt")
    assert "inside the repository" in H.terms_file_problem(inside)
    scanned = tmp_path / "proj"
    scanned.mkdir()
    in_scanned = scanned / "t.txt"
    in_scanned.write_text("name\tZorbax\n")
    os.chmod(in_scanned, 0o600)
    assert "inside the repository" in H.terms_file_problem(in_scanned, scanned) and H.terms_file_problem(in_scanned) is None
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(inside))
    cfg = type("C", (), {"root": tmp_path})()
    with pytest.raises(SystemExit) as e:
        R.cmd_hygiene(Namespace(root=None, build_terms=True, ci=False), cfg)
    assert "refusing to write" in str(e.value) and not inside.exists()


def test_the_permission_template_denies_the_term_file_and_claude_md_says_never_read_it():
    deny = json.loads((ROOT / "docs" / "claude-settings.example.json").read_text())["permissions"]["deny"]
    assert "Read(**/hygiene-terms*)" in deny and "Bash(*hygiene-terms*)" in deny
    assert "hygiene-terms" in (ROOT / "CLAUDE.md").read_text()


# ---------------------------------------------------------------- building the terms from a database and a memory folder

@pytest.fixture
def world(cfg, db_key):
    """An invented household: two members, an employer, a town, a school, a loan, a savings plan, accounts, person transfers and merchants."""
    from helpers import add_bank, add_tx
    mem = cfg.memory_dir
    (mem / "liabilities").mkdir(parents=True, exist_ok=True)
    (mem / "household.yaml").write_text(
        "members:\n  - id: zorbax\n    name: Quillon Zorbax\n    role: adult\n    aliases: [Q ZORBAX, ZORBAX QUILLON]\n  - id: pia\n    name: Pia Zorbax\n    role: child\n"
        "employers: [Yarrowtech]\nplaces: [Quenby-sur-Loze]\nschools: [Ecole des Vents]\ncountry: FR\ncontact:\n  address: |-\n    4 impasse des Quetsches\n    99999 Quenby\n  email: q@example.org\n")
    (mem / "assets.yaml").write_text("assets:\n  - id: plan\n    kind: employee_savings_plan\n    balance: 54321\n    as_of: 2026-01-01\n  - id: small\n    kind: other\n    balance: 7000\n")
    (mem / "liabilities" / "home.yaml").write_text("id: home\nkind: mortgage\nlender: Banque Quorvex\nmonthly_payment: 1357.91\nprincipal: 250000\nrate: {type: fixed, nominal: 2.5}\nstart_date: 2020-01-01\n")
    (mem / "liabilities" / "_template.yaml").write_text("id: tpl\nmonthly_payment: 9999.99\n")
    con = db_mod.connect(cfg, create=True)
    add_bank(con, "sess12345678", "Bank X", "FR", [("acct98765432", "FR7600000000000000000000099", "Compte Quenby principal")])
    for i in range(4):
        add_tx(con, "acct98765432", f"r{i}", f"2026-0{i + 1}-05", -41.37, "PRLV SEPA QUORVEX ASSURANCE REF 1", "direct_debit")
    add_tx(con, "acct98765432", "p1", "2026-02-01", -200.0, "VIR M ZEPHYRA OLIVIER", "person_transfer_out")
    con.execute("UPDATE transactions SET counterparty='M ZEPHYRA OLIVIER' WHERE tx_key='p1'")
    for i in range(3):
        add_tx(con, "acct98765432", f"c{i}", f"2026-0{i + 1}-09", -100.0, "CARTE QUINCAILLERIE ZEPHYRA LOZE", "card")
    con.execute("UPDATE tx_enriched SET merchant_key='QUINCAILLERIE ZEPHYRA LOZE' WHERE tx_key LIKE 'c%'")
    con.execute("UPDATE tx_enriched SET merchant_key='NETFLIXXX' WHERE tx_key='r0'")
    con.execute("UPDATE tx_enriched SET merchant_key='SUPERMARCHE ZEPHYRA' WHERE tx_key='r1'")
    con.commit()
    yield cfg, con
    con.close()


def test_build_terms_derives_every_category_from_the_database_and_memory(world):
    cfg, con = world
    terms = H.build_terms(con, cfg.memory_dir, eb_app_id="00000000-aaaa-bbbb-cccc-dddddddddddd", extra_machine=["quillbox"])
    assert {"Quillon Zorbax", "Zorbax", "Quillon", "Q ZORBAX", "Pia Zorbax"} <= terms["name"]
    assert terms["employer"] == {"Yarrowtech"} and terms["place"] == {"Quenby-sur-Loze"} and terms["school"] == {"Ecole des Vents"}
    assert "4 impasse des Quetsches" in terms["contact"] and "q@example.org" not in terms["contact"] or "q@example.org" in terms["contact"]
    assert "Compte Quenby principal" in terms["account"]
    assert {"acct9876", "sess1234", "00000000"} <= terms["id"] and any(t.endswith("0000000099") for t in terms["id"])
    assert "M ZEPHYRA OLIVIER" in terms["counterparty"] and "ZEPHYRA" in terms["counterparty"]
    assert "QUINCAILLERIE ZEPHYRA LOZE" in terms["merchant"] and "Banque Quorvex" in terms["merchant"]
    assert terms["machine"] == {"quillbox"}


def test_build_terms_amounts_are_exact_recurring_and_memory_figures_but_not_round_ones(world):
    cfg, con = world
    amounts = H.build_terms(con, cfg.memory_dir)["amount"]
    assert "41.37" in amounts                                    # a figure with cents that comes back
    assert "1357.91" in amounts and "54321" in amounts and "250000" in amounts      # memory figures: with cents, or round and large
    assert "100.00" not in amounts and "7000" not in amounts      # a round amount, a small round balance
    assert "9999.99" not in amounts                               # a template file is not the household's


def test_build_terms_leaves_out_what_is_not_identifying(world):
    cfg, con = world
    merchants = H.build_terms(con, cfg.memory_dir)["merchant"]
    assert "NETFLIXXX" not in merchants and "SUPERMARCHE ZEPHYRA" in merchants    # a single-word brand is left out, a multi-word local shop is kept
    assert H.public_vocabulary() and "virement" in H.public_vocabulary()


def test_build_terms_on_an_empty_installation_is_empty_not_an_error(cfg, db_key):
    con = db_mod.connect(cfg, create=True)
    try:
        terms = H.build_terms(con, cfg.memory_dir)
    finally:
        con.close()
    assert not any(terms.get(c) for c in ("name", "employer", "place", "merchant", "amount", "counterparty"))


def test_the_command_writes_the_file_and_prints_counts_never_a_term(world, tmp_path, capsys, monkeypatch):
    cfg, con = world
    con.close()
    dest = tmp_path / "out" / "hygiene-terms.txt"
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(dest))
    R.cmd_hygiene(Namespace(root=None, build_terms=True, ci=False), cfg)
    out = capsys.readouterr().out
    assert "wrote" in out and "mode 0600" in out and re.search(r"name\s+\d+", out)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    text = dest.read_text()
    assert "Zorbax" in text and not re.search(r"zorbax|quenby|zephyra|yarrowtech|quorvex|41\.37", out, re.I)
    assert H.terms_file_problem(dest) is None
    con2 = db_mod.connect(cfg)                                   # the fixture closes it again at teardown
    con2.close()


def test_the_built_terms_find_the_household_in_a_text_but_not_a_stranger(world, tmp_path, monkeypatch):
    cfg, con = world
    dest = tmp_path / "terms.txt"
    H.write_terms(dest, H.build_terms(con, cfg.memory_dir))
    idx = H.TermIndex(H.load_terms(dest))
    assert idx.find("Dear Quillon Zorbax, from Yarrowtech in Quenby sur Loze") and idx.find("paid 41,37 EUR") and idx.find("loan 1 357,91")
    assert idx.find("Dear Alice Example, from Acme in Springfield, paid 12.50 EUR") == []
