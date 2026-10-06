"""Every command or flag that a user-facing message tells the user to run must parse with the REAL argparse parser."""
import re
from pathlib import Path

import pytest

from coach.cli import build_parser

SRC = Path(__file__).resolve().parents[1] / "src" / "coach"
SUGGESTION = re.compile(r"`(?:uv run )?coach ([a-z][^`\n]*)`")


def subcommands(parser):
    for a in parser._actions:
        if a.__class__.__name__ == "_SubParsersAction":
            return a.choices
    return {}


def check(tokens):
    """Walk the real parser tree; return the problem found in the suggestion, or None."""
    parser = build_parser()
    cur = parser
    i = 0
    while i < len(tokens) and tokens[i] in subcommands(cur):
        cur = subcommands(cur)[tokens[i]]
        i += 1
    for t in tokens[i:]:
        t = t.rstrip(",.;:)")
        if t.startswith("--"):
            opt = t.split("=")[0]
            if opt not in cur._option_string_actions:
                return f"{opt} is not an option of `coach {' '.join(tokens[:i])}`"
    if i == 0:
        return f"{tokens[0]} is not a command"
    return None


def suggestions():
    out = []
    for p in sorted(SRC.rglob("*.py")):
        for n, ln in enumerate(p.read_text().splitlines(), 1):
            for m in SUGGESTION.finditer(ln):
                out.append((f"{p.relative_to(SRC)}:{n}", m.group(1).split()))
    return out


def test_every_suggested_command_in_the_source_parses():
    found = suggestions()
    assert len(found) > 20
    bad = [(where, " ".join(t), problem) for where, t in found if (problem := check(t))]
    assert not bad, bad


@pytest.mark.parametrize("argv", [
    ["classify", "run", "--abandon-unrecorded"],
    ["classify", "run", "--claim-legacy-batches", "label"],
    ["classify", "run", "--claim-legacy-batches", "compare", "--abandon-unrecorded", "--dry-run", "--no-knn"],
    ["classify", "run", "--refresh", "--include-ruled", "--knn-threshold", "0.9", "--batch", "10"],
    ["classify", "review", "--json"], ["classify", "review", "--accept", "KEY1", "KEY2"],
    ["classify", "compare", "--sample", "5"], ["taxonomy", "merge-package"],
    ["taxonomy", "rename", "a.b", "c.d"], ["taxonomy", "add", "a.b", "desc"],
    ["merchants", "merge", "K1", "K2", "--name", "N"], ["split", "TX", "a.b:1", "c.d:rest"],
    ["db", "migrate"]])
def test_recovery_and_e2_commands_parse_with_the_real_parser(argv):
    args = build_parser().parse_args(argv)
    assert args.fn


def test_the_batch_recovery_flags_belong_to_classify_run_only():
    p = build_parser()
    a = p.parse_args(["classify", "run", "--abandon-unrecorded", "--claim-legacy-batches", "label"])
    assert a.abandon_unrecorded is True and a.claim_legacy_batches == "label"
    for argv in (["import", "--abandon-unrecorded"], ["classify", "review", "--abandon-unrecorded"],
                 ["classify", "run", "--claim-legacy-batches", "other"]):
        with pytest.raises(SystemExit):
            p.parse_args(argv)
