"""CSV mapping profiles: TOML files under ``config/import_profiles/`` (see the shipped examples)."""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
COLUMN_KEYS = ("date", "value_date", "description", "amount", "debit", "credit", "counterparty", "reference",
               "currency", "fee")


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    name: str = "(inline)"
    description: str = ""
    encoding: str = "auto"            # auto | utf-8 | utf-8-sig | latin-1 | cp1252
    delimiter: str = "auto"           # auto | ; | , | \t | |
    skip_rows: int = 0                # lines to ignore before the header (or first data row)
    skip_footer: int = 0              # trailing lines to ignore (totals)
    header_contains: list[str] = field(default_factory=list)   # locate the header line by its content
    has_header: bool = True
    date_format: list[str] = field(default_factory=lambda: ["%d/%m/%Y"])
    decimal: str = "auto"             # "," | "." | auto
    thousands: str = ""
    currency: str = "EUR"             # used when the file has no currency column
    negate: bool = False              # amounts are positive for money OUT
    columns: dict[str, object] = field(default_factory=dict)   # key -> header name | 0-based index | [names]
    filter_column: str | None = None
    filter_keep: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if "date" not in self.columns:
            raise ProfileError("profile needs columns.date")
        if "description" not in self.columns:
            raise ProfileError("profile needs columns.description")
        if "amount" not in self.columns and not ({"debit", "credit"} & set(self.columns)):
            raise ProfileError("profile needs columns.amount, or columns.debit / columns.credit")
        if self.decimal not in (",", ".", "auto"):
            raise ProfileError("format.decimal must be ',' or '.' (or 'auto')")
        for k in self.columns:
            if k not in COLUMN_KEYS:
                raise ProfileError(f"unknown column key {k!r}; allowed: {', '.join(COLUMN_KEYS)}")


def from_dict(d: dict, name: str) -> Profile:
    fmt, cols, flt = d.get("format", {}), d.get("columns", {}), d.get("filter", {})
    dfmt = fmt.get("date_format", ["%d/%m/%Y"])
    p = Profile(
        name=d.get("name", name), description=d.get("description", ""),
        encoding=fmt.get("encoding", "auto"), delimiter=fmt.get("delimiter", "auto"),
        skip_rows=int(fmt.get("skip_rows", 0)), skip_footer=int(fmt.get("skip_footer", 0)),
        header_contains=list(fmt.get("header_contains", [])), has_header=bool(fmt.get("has_header", True)),
        date_format=[dfmt] if isinstance(dfmt, str) else list(dfmt),
        decimal=fmt.get("decimal", "auto"), thousands=fmt.get("thousands", ""),
        currency=fmt.get("currency", "EUR"), negate=bool(fmt.get("negate", False)),
        columns=dict(cols), filter_column=flt.get("column"), filter_keep=list(flt.get("keep", [])))
    p.validate()
    return p


def profile_path(profiles_dir: Path, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise ProfileError(f"invalid profile name {name!r} (letters, digits, '-', '_', '.')")
    return Path(profiles_dir) / f"{name}.toml"


def load_profile(profiles_dir: Path, name: str) -> Profile:
    path = profile_path(profiles_dir, name)
    if not path.exists():
        raise ProfileError(f"profile {name!r} not found in {profiles_dir} (see --list-profiles)")
    try:
        return from_dict(tomllib.loads(path.read_text(encoding="utf-8")), name)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{path}: {e}") from e


def list_profiles(profiles_dir: Path) -> list[tuple[str, str]]:
    out = []
    for p in sorted(Path(profiles_dir).glob("*.toml")) if Path(profiles_dir).is_dir() else []:
        try:
            d = tomllib.loads(p.read_text(encoding="utf-8"))
            out.append((p.stem, d.get("description", "")))
        except tomllib.TOMLDecodeError:
            out.append((p.stem, "(invalid TOML)"))
    return out


def _v(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def to_toml(p: Profile) -> str:
    lines = [f"name = {_v(p.name)}", f"description = {_v(p.description)}", "", "[format]",
             f"encoding = {_v(p.encoding)}", f"delimiter = {_v(p.delimiter)}", f"skip_rows = {p.skip_rows}",
             f"skip_footer = {p.skip_footer}", f"has_header = {_v(p.has_header)}",
             f"header_contains = {_v(p.header_contains)}", f"date_format = {_v(p.date_format)}",
             f"decimal = {_v(p.decimal)}", f"thousands = {_v(p.thousands)}", f"currency = {_v(p.currency)}",
             f"negate = {_v(p.negate)}", "", "[columns]"]
    lines += [f"{k} = {_v(v)}" for k, v in p.columns.items()]
    if p.filter_column:
        lines += ["", "[filter]", f"column = {_v(p.filter_column)}", f"keep = {_v(p.filter_keep)}"]
    return "\n".join(lines) + "\n"


def save_profile(profiles_dir: Path, p: Profile, overwrite: bool = False) -> Path:
    path = profile_path(profiles_dir, p.name)
    if path.exists() and not overwrite:
        raise ProfileError(f"{path} already exists (use --overwrite-profile)")
    p.validate()
    Path(profiles_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(to_toml(p), encoding="utf-8")
    return path
