"""Proposals (E6-5): the ONLY way the coach / an LLM changes memory. A proposal is a pending, validated change
stored in ``memory/.proposals/<id>.json``: target file, a list of operations (:mod:`coach.memory.edit`), the reason,
the source (``coach-llm``, ``doc-extract:<doc>``, ``user``...), a rendered diff and, for document extractions, the
source snippets. Creating one never touches the target file; only :func:`accept` (a human action: CLI or UI)
applies it, validated again and recorded in the change history; :func:`reject` just closes it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from coach.memory import edit as edit_mod, yamlio
from coach.memory.store import EditResult, MemoryStore, MemoryStoreError

DIRNAME = ".proposals"
ID_RE = re.compile(r"^p-\d{8}-[0-9a-f]{6}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProposalError(MemoryStoreError):
    pass


@dataclass
class Proposal:
    id: str
    status: str                       # pending | accepted | rejected
    created: str
    file: str
    ops: list[dict]
    reason: str
    source: str
    diff: str = ""
    changes: list[dict] = field(default_factory=list)       # field-by-field view: {path, op, old, new, snippet}
    evidence: list[dict] = field(default_factory=list)      # extra provenance (document snippets...)
    base_sha256: Optional[str] = None
    resolved: Optional[str] = None
    change_id: Optional[str] = None
    note: Optional[str] = None
    seal: Optional[str] = None            # digest of (file, ops, reason, source) taken at creation
    seal_kind: Optional[str] = None       # "hmac-sha256" (key from the secrets store) | "sha256"

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "status", "created", "file", "ops", "reason", "source", "diff",
                                                "changes", "evidence", "base_sha256", "resolved", "change_id", "note",
                                                "seal", "seal_kind")}


def _key() -> Optional[str]:
    try:
        from coach import secrets
        return secrets.get_secret("proposal_key", required=False)
    except Exception:                                                # noqa: BLE001 - Keychain trouble: fall back
        return None


def _mac(payload: bytes) -> tuple[str, str]:
    key = _key()
    if key:
        return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest(), "hmac-sha256"
    return hashlib.sha256(payload).hexdigest(), "sha256"


def _seal(p: "Proposal") -> tuple[str, str]:
    """Digest of EVERYTHING that decides or documents what is applied: id, created, base hash, file, operations, reason,
    source (the proposer) and the evidence. With a `proposal_key` secret (env COACH_PROPOSAL_KEY or Keychain) it is an
    HMAC, so a file edited by something that cannot read the key is detected; without one it is a plain SHA-256
    (accidents and naive edits only: a process that can write the file can also reseal it. A same-user process can
    always edit memory files directly; the mitigations are the history attribution, the TTY gate of `accept` and the
    permissions). The status is NOT trusted: see :func:`effective_status`."""
    payload = json.dumps({"id": p.id, "created": p.created, "base_sha256": p.base_sha256, "file": p.file,
                          "ops": edit_mod.jsonable(p.ops), "reason": p.reason, "source": p.source,
                          "evidence": edit_mod.jsonable(p.evidence)},
                         sort_keys=True, ensure_ascii=False, default=str).encode()
    return _mac(payload)


def is_sealed(p: "Proposal") -> bool:
    """True if every sealed field is exactly what was validated at creation."""
    if not p.seal:
        return False
    digest, kind = _seal(p)
    if kind != p.seal_kind:
        return False                       # the key appeared / disappeared: cannot vouch for it
    return hmac.compare_digest(digest, p.seal)


# ---------------------------------------------------------------- resolution registry (accepted / rejected)

REGISTRY = ".resolved"


def _registry_entries(store: MemoryStore) -> list[dict]:
    """Chained entries {id, status, at, change_id, prev, mac}; an entry whose MAC (or chain link) does not verify is
    ignored. A proposal can never become pending again: its resolution lives here and in the change history, not in
    the (editable) proposal file."""
    path = store.root / DIRNAME / REGISTRY
    if not path.exists():
        return []
    out, prev = [], ""
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
            body = json.dumps({k: e[k] for k in ("id", "status", "at", "change_id", "prev")}, sort_keys=True).encode()
            digest, kind = _mac(body)
            if e["prev"] != prev or kind != e.get("kind") or not hmac.compare_digest(digest, e["mac"]):
                print(f"WARNING: ignoring a registry entry of {store.root / DIRNAME / REGISTRY} that does not verify",
                      file=sys.stderr)
                continue
            out.append(e)
            prev = e["mac"]
        except Exception:                                            # noqa: BLE001
            continue
    return out


def _registry_add(store: MemoryStore, pid: str, status: str, change_id: Optional[str]) -> None:
    prev = (_registry_entries(store) or [{"mac": ""}])[-1]["mac"]
    e = {"id": pid, "status": status, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "change_id": change_id, "prev": prev}
    mac, kind = _mac(json.dumps(e, sort_keys=True).encode())
    path = _dir(store) / REGISTRY
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({**e, "mac": mac, "kind": kind}) + "\n")
    os.chmod(path, 0o600)


def _history_verdict(store: MemoryStore, pid: str) -> Optional[str]:
    """'accepted' / 'rejected' if the change history holds a commit for this proposal id."""
    if not store.repo.exists():
        return None
    p = store.repo.git("log", "--all", "--format=%s", "--fixed-strings", f"--grep={pid}", check=False)
    for ln in p.stdout.splitlines():
        if f"accept {pid}" in ln:
            return "accepted"
        if f"reject {pid}" in ln:
            return "rejected"
    return None


def effective_status(store: MemoryStore, p: "Proposal") -> str:
    """pending | accepted | rejected | resolved, from the change history, the resolved/ folder and the registry; the
    `status` field of the JSON is display only and cannot resurrect a resolved proposal. Never defaults to pending
    for a proposal that has any trace of a resolution."""
    verdict = _history_verdict(store, p.id)
    if verdict:
        return verdict
    for e in reversed(_registry_entries(store)):
        if e["id"] == p.id:
            return e["status"]
    if (store.root / DIRNAME / RESOLVED_DIR / f"{p.id}.json").exists():
        return p.status if p.status in ("accepted", "rejected") else "resolved"
    return "pending"


def _dir(store: MemoryStore) -> Path:
    d = store.root / DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _path(store: MemoryStore, pid: str) -> Path:
    if not ID_RE.match(pid or ""):
        raise ProposalError(f"{pid!r} is not a proposal id (see `coach memory proposals`)")
    return _dir(store) / f"{pid}.json"


def _save(store: MemoryStore, p: Proposal) -> None:
    yamlio.atomic_write(_path(store, p.id), json.dumps(p.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n")


RESOLVED_DIR = "resolved"


def _load(store: MemoryStore, pid: str) -> Proposal:
    path = _path(store, pid)
    if not path.exists():
        path = _dir(store) / RESOLVED_DIR / f"{pid}.json"
    if not path.exists():
        raise ProposalError(f"no proposal {pid!r}")
    data = json.loads(path.read_text())
    if data.get("id") != pid:
        raise ProposalError(f"proposal file {path.name} carries another id ({data.get('id')!r}): refusing it")
    return Proposal(**data)


def _finalize(store: MemoryStore, p: "Proposal") -> None:
    """A resolved proposal LEAVES the pending folder: it is moved to .proposals/resolved/. Being there, or in the change
    history, is what makes it unusable, whatever its (editable) JSON or the registry say."""
    rdir = _dir(store) / RESOLVED_DIR
    rdir.mkdir(exist_ok=True)
    os.chmod(rdir, 0o700)
    yamlio.atomic_write(rdir / f"{p.id}.json", json.dumps(p.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n")
    try:
        _path(store, p.id).unlink()
    except FileNotFoundError:
        pass


def rehydrate(v):
    """JSON has no dates: ISO date strings in a value become dates again so they are written unquoted."""
    if isinstance(v, str) and ISO_DATE_RE.match(v):
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            return v
    if isinstance(v, dict):
        return {k: rehydrate(x) for k, x in v.items()}
    if isinstance(v, list):
        return [rehydrate(x) for x in v]
    return v


def _ops(ops: list[dict]) -> list[dict]:
    out = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in edit_mod.OPS:
            raise ProposalError(f"bad operation {op!r}: op must be one of {', '.join(sorted(edit_mod.OPS))}")
        o = dict(op)
        if "value" in o:
            o["value"] = rehydrate(edit_mod.jsonable(o["value"]))
        out.append(o)
    return out


def _describe(store: MemoryStore, rel: str, ops: list[dict], snippets: dict[str, str]) -> list[dict]:
    """Field-by-field view with the CURRENT value of each field."""
    changes = []
    doc = None
    if store.exists(rel) and store.kind_of(rel) == "yaml":
        try:
            doc = yamlio.loads(store.read_text(rel))
        except yamlio.YamlError:
            doc = None
    lk = store.list_key_of(rel)
    for op in ops:
        old = None
        if doc is not None and op.get("path") and op["op"] in ("set", "unset", "remove"):
            try:
                old = yamlio.to_plain(edit_mod.get_path(doc, op["path"], lk))
            except edit_mod.EditError:
                old = None
        changes.append({"op": op["op"], "path": op.get("path"), "old": edit_mod.jsonable(old),
                        "new": edit_mod.jsonable(op.get("value", op.get("old"))),
                        "snippet": snippets.get(op.get("path") or "")})
    return changes


def create(store: MemoryStore, file: str, ops: list[dict], reason: str, source: str = "coach-llm", *,
           snippets: Optional[dict[str, str]] = None, evidence: Optional[list[dict]] = None,
           now: Optional[dt.datetime] = None) -> Proposal:
    """Validate a change against the current memory (dry run) and queue it. Raises if it would be invalid."""
    if not (reason or "").strip():
        raise ProposalError("a proposal needs a reason (what the user said / what the document says)")
    ops = _ops(ops)
    if not ops:
        raise ProposalError("a proposal needs at least one operation")
    res: EditResult = store.edit(file, ops, action="propose", reason=reason, source=source, dry_run=True,
                                 replace_inline_comments=True)
    if not res.changed:
        raise ProposalError("this proposal changes nothing")
    now = now or dt.datetime.now(dt.timezone.utc)
    pid = f"p-{now.strftime('%Y%m%d')}-{secrets.token_hex(3)}"
    ev = list(evidence or [])
    for path, snip in (snippets or {}).items():
        if not any(e.get("path") == path for e in ev):
            ev.append({"path": path, "snippet": snip})
    p = Proposal(pid, "pending", now.isoformat(timespec="seconds"), file, ops, reason.strip(), source, "", [], ev,
                 hashlib.sha256(store.read_text(file).encode()).hexdigest() if store.exists(file) else None)
    p.seal, p.seal_kind = _seal(p)
    _save(store, p)
    return p


def describe(store: MemoryStore, p: "Proposal") -> dict:
    """What the proposal means RIGHT NOW, recomputed from its operations and the sealed evidence: the stored `diff` /
    `changes` fields of the file are never displayed."""
    snippets = {e["path"]: e.get("snippet") for e in p.evidence if isinstance(e, dict) and e.get("path")}
    changes = _describe(store, p.file, _ops(p.ops), snippets)
    flags = {e["path"]: e for e in p.evidence if isinstance(e, dict) and e.get("path")}
    for c in changes:
        ev = flags.get(c["path"], {})
        c["suspicious"] = bool(ev.get("suspicious"))
        if ev.get("existing") is not None:
            c["conflicts_with"] = ev["existing"]
    try:
        res = store.edit(p.file, _ops(p.ops), action="preview", dry_run=True, replace_inline_comments=True)
        return {"changes": changes, "diff": res.diff, "applicable": True, "error": None}
    except MemoryStoreError as e:
        return {"changes": changes, "diff": "", "applicable": False, "error": str(e)}


def public_dict(store: MemoryStore, p: "Proposal") -> dict:
    d = {k: getattr(p, k) for k in ("id", "created", "file", "ops", "reason", "source", "evidence", "base_sha256",
                                    "change_id", "note", "resolved", "seal_kind")}
    d["status"] = effective_status(store, p)
    d["sealed"] = is_sealed(p)
    d.update(describe(store, p))
    return d


def listing(store: MemoryStore, status: Optional[str] = "pending") -> list[Proposal]:
    """Proposals, oldest first, filtered by their EFFECTIVE status. A malformed / unreadable file is skipped with a
    warning, it never breaks the list."""
    d = store.root / DIRNAME
    if not d.is_dir():
        return []
    out = []
    files = sorted(d.glob("p-*.json")) + (sorted((d / RESOLVED_DIR).glob("p-*.json")) if status in (None, "all", "accepted", "rejected", "resolved") else [])
    for f in files:
        if not ID_RE.match(f.stem):
            continue
        try:
            p = _load(store, f.stem)
            p.status = effective_status(store, p)
            out.append(p)
        except Exception as e:                                       # noqa: BLE001
            print(f"WARNING: skipping unreadable proposal {f.name} ({type(e).__name__})", file=sys.stderr)
    return [p for p in out if status in (None, "all", p.status)]


def get(store: MemoryStore, pid: str) -> Proposal:
    p = _load(store, pid)
    p.status = effective_status(store, p)
    return p


@dataclass
class Preview:
    proposal: Proposal
    result: EditResult           # a FRESH dry run of the operations against the file as it is now
    sealed: bool
    stale: bool                  # the file changed since the proposal was made


def preview(store: MemoryStore, pid: str) -> Preview:
    """What accepting would do RIGHT NOW (recomputed from the operations, never from stored display fields)."""
    p = get(store, pid)
    if p.status != "pending":
        raise ProposalError(f"proposal {pid} is already {p.status}")
    stale = bool(p.base_sha256) and store.exists(p.file) and \
        hashlib.sha256(store.read_text(p.file).encode()).hexdigest() != p.base_sha256
    try:
        res = store.edit(p.file, _ops(p.ops), action="preview", dry_run=True, replace_inline_comments=True)
    except MemoryStoreError as e:
        raise ProposalError(f"cannot apply {pid} to {p.file} as it is now: {e}") from e
    return Preview(p, res, is_sealed(p), bool(stale))


def suspicious_paths(p: "Proposal") -> list[str]:
    return sorted({e["path"] for e in p.evidence if isinstance(e, dict) and e.get("suspicious") and e.get("path")})


def accept(store: MemoryStore, pid: str, *, confirmed: bool = False, force: bool = False,
           accepted_by: str = "cli", confirm_fields=()) -> tuple[Proposal, EditResult]:
    """Apply a pending proposal. This is a HUMAN action: the CLI requires an interactive terminal (stdin and stdout
    TTYs) and a typed confirmation after showing the fresh diff; `confirmed=True` here is for that code path and for
    tests. Refused when
      * the proposal is already accepted or rejected (history / registry, not the editable status field),
      * any sealed field was modified after creation,
      * the target file changed since the proposal was made (stale), unless `force`.
    The change is recorded in the history with `accepted_by` as its source and the proposer in the reason."""
    if not confirmed:
        raise ProposalError("accepting a proposal needs explicit confirmation of its diff (pass confirmed=True)")
    pv = preview(store, pid)
    p = pv.proposal
    if not pv.sealed:
        raise ProposalError(f"proposal {pid} was modified after it was created (or has no seal): refusing to apply it; "
                            "reject it and ask for a new one")
    missing = [x for x in suspicious_paths(p) if x not in set(confirm_fields)]
    if missing:
        raise ProposalError(f"proposal {pid} comes from a document that contains instruction-like text (possible prompt "
                            f"injection): each of these fields needs an explicit --confirm-field: {', '.join(missing)}")
    if pv.stale and not force:
        raise ProposalError(f"{p.file} changed since proposal {pid} was made, so it may overwrite a newer value. "
                            "Review `coach memory proposals`, re-propose, or accept with --force after reading the "
                            "fresh diff")
    comp = None
    if p.file == "open-questions.yaml":                  # the Markdown view is generated from the yaml: keep it in step
        from coach.memory import questions as q_mod
        if q_mod.md_hand_edited(store):
            raise ProposalError("open-questions.md was edited by hand (or is out of date): run `coach questions regenerate-view` first, "
                                "then accept the proposal")
        comp = q_mod._companion(store)
    res = store.edit(p.file, _ops(p.ops), action=f"accept {pid}", source=accepted_by,
                     reason=f"{p.reason} (proposed by {p.source}, accepted by {accepted_by})", replace_inline_comments=True,
                     companion=comp)
    _registry_add(store, pid, "accepted", res.change_id)
    p.status, p.resolved, p.change_id = "accepted", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), res.change_id
    _finalize(store, p)
    return p, res


def reject(store: MemoryStore, pid: str, note: Optional[str] = None) -> Proposal:
    p = get(store, pid)
    if p.status != "pending":
        raise ProposalError(f"proposal {pid} is already {p.status}")
    if store.use_history:
        store.repo.note(f"coach: reject {pid}", source=store.source, reason=note or "proposal rejected")
    _registry_add(store, pid, "rejected", None)
    p.status, p.note = "rejected", note
    p.resolved = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _finalize(store, p)
    return p


def purge_resolved(store: MemoryStore, cutoff: str) -> int:
    """Delete resolved proposal files older than `cutoff` (ISO date); their text holds past values and reasons."""
    n = 0
    d = store.root / DIRNAME / RESOLVED_DIR
    if not d.is_dir():
        return 0
    for f in d.glob("p-*.json"):
        try:
            when = (json.loads(f.read_text()).get("resolved") or json.loads(f.read_text()).get("created") or "")[:10]
        except Exception:                                            # noqa: BLE001
            when = ""
        if when and when < cutoff:
            f.unlink()
            n += 1
    return n
