from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


RULE_VERSION = 1
ATOM_KINDS = {"intent", "invariant", "requirement", "decision"}
AUTHORITIES = {"principal", "agent"}
POLARITIES = {"require", "forbid"}
VERIFY_KINDS = {"command", "manual"}
OP_TYPES = {"ADD", "SUPERSEDE", "CANCEL"}


class IntentGateError(RuntimeError):
    pass


class ApprovalRequired(IntentGateError):
    def __init__(self, event_hash: str):
        super().__init__(f"principal approval required for event {event_hash}")
        self.event_hash = event_hash


@dataclass(frozen=True)
class ResolvedContract:
    active_atoms: tuple[dict[str, Any], ...]
    all_atoms: dict[str, dict[str, Any]]
    statuses: dict[str, str]
    ledger_head: str
    contract_hash: str
    event_count: int

    def active_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(a["id"]): a for a in self.active_atoms}


@dataclass(frozen=True)
class GateReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _collapse_ws(value: str) -> str:
    return " ".join(_nfc(value).split())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _event_hash(event: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(_normalize_generic(event)))


def _normalize_generic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _normalize_generic(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, list):
        return [_normalize_generic(v) for v in value]
    if isinstance(value, str):
        return _nfc(value)
    return value


def _require_exact_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    if not isinstance(value, dict):
        raise IntentGateError(f"{label} must be a mapping")
    keys = set(value)
    missing = sorted(required - keys)
    extra = sorted(keys - required - set(optional))
    if missing:
        raise IntentGateError(f"{label} missing required fields: {', '.join(missing)}")
    if extra:
        raise IntentGateError(f"{label} has unknown fields: {', '.join(extra)}")


def _validate_json_value(value: Any, *, label: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if type(value) is float:
        if not (float("-inf") < value < float("inf")):
            raise IntentGateError(f"{label} contains non-finite float")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, label=f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise IntentGateError(f"{label} contains a non-string mapping key")
            _validate_json_value(item, label=f"{label}.{key}")
        return
    raise IntentGateError(f"{label} contains non-JSON value: {type(value).__name__}")


def _normalize_path_prefix(value: str) -> str:
    value = _nfc(value).strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:].lstrip()
    value = value.strip()
    if value.startswith("/"):
        raise IntentGateError(
            f"intent scope/task paths must be project-relative, got {value!r}"
        )
    value = value.strip("/")
    if value in {"", "."}:
        return ""
    parts = [p for p in value.split("/") if p not in {"", "."}]
    if any(p == ".." for p in parts):
        raise IntentGateError(f"scope path may not contain '..': {value!r}")
    return "/".join(parts)


def _normalize_scope(raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        raise IntentGateError("atom.scope must be a mapping")
    _require_exact_keys(raw, required={"paths"}, label="atom.scope")
    paths = raw["paths"]
    if not isinstance(paths, list):
        raise IntentGateError("atom.scope.paths must be a list")
    if any(not isinstance(path, str) for path in paths):
        raise IntentGateError("atom.scope.paths values must be strings")
    normalized = {_normalize_path_prefix(path) for path in paths}
    # The empty prefix represents project-global scope. If it appears alongside
    # narrower paths, global wins; silently dropping it would narrow a binding
    # obligation.
    if "" in normalized:
        return []
    return sorted(normalized)


def _scope_overlap(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return True
    for left in a:
        for right in b:
            if left == right:
                return True
            if left and right.startswith(left + "/"):
                return True
            if right and left.startswith(right + "/"):
                return True
    return False


def _scope_applies(scope: list[str], paths: list[str]) -> bool:
    if not paths or not scope:
        return True
    normalized = [_normalize_path_prefix(p) for p in paths]
    if "" in normalized:
        return True
    return _scope_overlap(scope, normalized)


def _validate_verification(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise IntentGateError("atom.verification must be a mapping")
    _require_exact_keys(
        raw,
        required={"kind", "ref"},
        label="atom.verification",
    )
    if not isinstance(raw["kind"], str) or not isinstance(raw["ref"], str):
        raise IntentGateError("atom.verification kind/ref must be strings")
    kind = _nfc(raw["kind"].strip())
    ref = _nfc(raw["ref"].strip())
    if kind not in VERIFY_KINDS:
        raise IntentGateError(f"unsupported verification kind: {kind!r}")
    if not ref:
        raise IntentGateError("verification.ref is required")
    return {"kind": kind, "ref": ref}


def normalize_atom(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise IntentGateError("atom must be a mapping")
    _require_exact_keys(
        raw,
        required={
            "id",
            "key",
            "kind",
            "authority",
            "scope",
            "polarity",
            "statement",
            "verification",
        },
        optional={"rationale", "source", "restores", "body_ref", "body_hash"},
        label="atom",
    )
    for field in ("id", "key", "kind", "authority", "polarity", "statement"):
        if not isinstance(raw[field], str):
            raise IntentGateError(f"atom.{field} must be a string")

    atom_id = _nfc(raw["id"].strip())
    key = _nfc(raw["key"].strip())
    kind = _nfc(raw["kind"].strip())
    authority = _nfc(raw["authority"].strip())
    polarity = _nfc(raw["polarity"].strip())
    statement = _collapse_ws(raw["statement"])
    if not atom_id:
        raise IntentGateError("atom.id is required")
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]*-[0-9]{4,}", atom_id):
        raise IntentGateError(f"invalid atom id: {atom_id!r}")
    if not key:
        raise IntentGateError(f"{atom_id}: key is required")
    if kind not in ATOM_KINDS:
        raise IntentGateError(f"{atom_id}: unsupported kind {kind!r}")
    if authority not in AUTHORITIES:
        raise IntentGateError(f"{atom_id}: unsupported authority {authority!r}")
    if polarity not in POLARITIES:
        raise IntentGateError(f"{atom_id}: unsupported polarity {polarity!r}")
    if not statement:
        raise IntentGateError(f"{atom_id}: statement is required")

    verification = _validate_verification(raw["verification"])
    atom: dict[str, Any] = {
        "id": atom_id,
        "key": key,
        "kind": kind,
        "authority": authority,
        "scope": {"paths": _normalize_scope(raw["scope"])},
        "polarity": polarity,
        "statement": statement,
        "verification": verification,
    }

    if "rationale" in raw:
        if not isinstance(raw["rationale"], str):
            raise IntentGateError(f"{atom_id}: rationale must be a string")
        rationale = _collapse_ws(raw["rationale"])
        if rationale:
            atom["rationale"] = rationale

    if "source" in raw:
        _validate_json_value(raw["source"], label=f"{atom_id}.source")
        atom["source"] = _normalize_generic(raw["source"])

    if "restores" in raw:
        if not isinstance(raw["restores"], str):
            raise IntentGateError(f"{atom_id}: restores must be a string")
        restores = _nfc(raw["restores"].strip())
        if restores:
            atom["restores"] = restores

    body_ref = raw.get("body_ref")
    body_hash = raw.get("body_hash")
    if kind == "decision":
        if not isinstance(body_ref, str) or not isinstance(body_hash, str):
            raise IntentGateError(
                f"{atom_id}: decision requires string body_ref and body_hash"
            )
        body_ref = _normalize_path_prefix(body_ref)
        body_hash = body_hash.strip().lower()
        if (
            not body_ref.startswith(".intent/decisions/")
            or body_ref == ".intent/decisions"
            or not re.fullmatch(r"[0-9a-f]{64}", body_hash)
        ):
            raise IntentGateError(
                f"{atom_id}: decision body_ref must be under .intent/decisions/ "
                "and body_hash must be sha256"
            )
        atom["body_ref"] = body_ref
        atom["body_hash"] = body_hash
    elif body_ref is not None or body_hash is not None:
        raise IntentGateError(
            f"{atom_id}: body_ref/body_hash only valid for decision atoms"
        )
    return atom


def _binding_projection(atom: dict[str, Any]) -> dict[str, Any]:
    out = {
        "key": atom["key"],
        "kind": atom["kind"],
        "authority": atom["authority"],
        "scope": {"paths": sorted(atom["scope"]["paths"])},
        "polarity": atom["polarity"],
        "statement": _collapse_ws(atom["statement"]),
        "verification": {
            "kind": atom["verification"]["kind"],
            "ref": atom["verification"]["ref"],
        },
    }
    if atom["kind"] == "decision":
        out["body_hash"] = atom["body_hash"]
    return out


def _contract_hash(active_atoms: Iterable[dict[str, Any]]) -> str:
    projections = [_binding_projection(a) for a in active_atoms]
    projections.sort(
        key=lambda a: (
            a["kind"],
            a["key"],
            _sha256_bytes(_canonical_json(a)),
        )
    )
    return _sha256_bytes(_canonical_json(projections))


def _atom_semantic_hash(atom: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(_binding_projection(atom)))


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntentGateError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise IntentGateError(f"{path}: expected mapping")
    return data


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _ledger_lock(root: Path):
    """Serialize accepted-ledger mutations on this host."""
    lock_path = root / ".intent" / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _event_files(root: Path) -> list[Path]:
    events_dir = root / ".intent" / "events"
    if not events_dir.exists():
        raise IntentGateError(f"missing event directory: {events_dir}")
    files: list[tuple[int, Path]] = []
    for path in events_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix != ".yaml":
            if path.suffix == ".yml":
                raise IntentGateError(f"event files must use .yaml: {path.name}")
            continue
        match = re.fullmatch(r"([0-9]+)-.+[.]yaml", path.name)
        if match is None:
            raise IntentGateError(f"invalid event filename: {path.name}")
        files.append((int(match.group(1)), path))
    files.sort(key=lambda item: item[0])
    return [path for _, path in files]


def _load_approvals(root: Path) -> dict[str, dict[str, Any]]:
    approvals: dict[str, dict[str, Any]] = {}
    directory = root / ".intent" / "approvals"
    if not directory.exists():
        return approvals
    for path in sorted(directory.glob("*.yaml")):
        data = _load_yaml(path)
        _require_exact_keys(
            data,
            required={"version", "event_hash", "approver", "channel"},
            label=f"approval {path.name}",
        )
        if type(data["version"]) is not int or data["version"] != 1:
            raise IntentGateError(f"{path}: unsupported approval version")
        event_hash = data["event_hash"]
        approver = data["approver"]
        channel = data["channel"]
        if not isinstance(event_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", event_hash
        ):
            raise IntentGateError(f"{path}: invalid approval event_hash")
        if not isinstance(approver, str) or not approver.strip():
            raise IntentGateError(f"{path}: approver is required")
        if not isinstance(channel, str) or not channel.strip():
            raise IntentGateError(f"{path}: approval channel is required")
        if event_hash in approvals:
            raise IntentGateError(f"duplicate approval for event {event_hash}")
        approvals[event_hash] = data
    return approvals


def _decision_body_ok(root: Path, atom: dict[str, Any]) -> None:
    if atom["kind"] != "decision":
        return
    decision_root = (root / ".intent" / "decisions").resolve()
    body = (root / atom["body_ref"]).resolve()
    try:
        body.relative_to(decision_root)
    except ValueError as exc:
        raise IntentGateError(
            f"{atom['id']}: decision body must remain under .intent/decisions/"
        ) from exc
    if not body.is_file():
        raise IntentGateError(f"{atom['id']}: missing decision body {atom['body_ref']}")
    actual = _sha256_file(body)
    if actual != atom["body_hash"]:
        raise IntentGateError(
            f"{atom['id']}: decision body hash mismatch "
            f"(expected {atom['body_hash']}, got {actual})"
        )


def _validate_active_collisions(active: dict[str, dict[str, Any]]) -> None:
    values = list(active.values())
    for i, left in enumerate(values):
        for right in values[i + 1 :]:
            if left["key"] != right["key"]:
                continue
            if _scope_overlap(left["scope"]["paths"], right["scope"]["paths"]):
                raise IntentGateError(
                    "active key/scope collision: "
                    f"{left['id']} and {right['id']} share key {left['key']!r}"
                )


def _event_required_principal(
    ops: list[dict[str, Any]],
    active: dict[str, dict[str, Any]],
) -> bool:
    for op in ops:
        typ = op["type"]
        if typ == "ADD":
            if op["atom"]["authority"] == "principal":
                return True
        elif typ == "SUPERSEDE":
            for atom_id in op["old_ids"]:
                if atom_id not in active:
                    raise IntentGateError(
                        f"SUPERSEDE target is not active: {atom_id}"
                    )
                if active[atom_id]["authority"] == "principal":
                    return True
            if any(a["authority"] == "principal" for a in op["new_atoms"]):
                return True
        elif typ == "CANCEL":
            for atom_id in op["ids"]:
                if atom_id not in active:
                    raise IntentGateError(f"CANCEL target is not active: {atom_id}")
                if active[atom_id]["authority"] == "principal":
                    return True
    return False


def _validate_event_shape(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise IntentGateError("event must be a mapping")
    _require_exact_keys(
        event,
        required={"v", "seq", "prev", "actor", "ops", "note"},
        label="event",
    )
    version = event["v"]
    seq = event["seq"]
    prev = event["prev"]
    actor = event["actor"]
    note = event["note"]
    raw_ops = event["ops"]

    if type(version) is not int or version != RULE_VERSION:
        raise IntentGateError(f"unsupported event rule version: {version!r}")
    if type(seq) is not int or seq < 0:
        raise IntentGateError("event.seq must be a non-negative integer")
    if seq == 0:
        if prev is not None:
            raise IntentGateError("genesis event prev must be null")
    elif not isinstance(prev, str) or not re.fullmatch(r"[0-9a-f]{64}", prev):
        raise IntentGateError("non-genesis event prev must be a sha256 hash")
    if not isinstance(actor, str) or actor not in AUTHORITIES:
        raise IntentGateError(f"unsupported event actor: {actor!r}")
    if not isinstance(note, str):
        raise IntentGateError("event.note must be a string")
    note = _nfc(note.strip())
    if not isinstance(raw_ops, list) or not raw_ops:
        raise IntentGateError("event.ops must be a non-empty list")

    ops: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_ops):
        if not isinstance(raw, dict):
            raise IntentGateError(f"event.ops[{index}] must be a mapping")
        typ = raw.get("type")
        if not isinstance(typ, str) or typ not in OP_TYPES:
            raise IntentGateError(f"unsupported operation: {typ!r}")

        if typ == "ADD":
            _require_exact_keys(
                raw,
                required={"type", "atom"},
                label=f"event.ops[{index}]",
            )
            ops.append({"type": typ, "atom": normalize_atom(raw["atom"])})
        elif typ == "SUPERSEDE":
            _require_exact_keys(
                raw,
                required={"type", "old_ids", "new_atoms"},
                label=f"event.ops[{index}]",
            )
            old_ids = raw["old_ids"]
            new_atoms_raw = raw["new_atoms"]
            if not isinstance(old_ids, list) or any(
                not isinstance(x, str) for x in old_ids
            ):
                raise IntentGateError("SUPERSEDE.old_ids must be a string list")
            if not isinstance(new_atoms_raw, list):
                raise IntentGateError("SUPERSEDE.new_atoms must be a list")
            new_atoms = [normalize_atom(x) for x in new_atoms_raw]
            if len(old_ids) != 1 or len(new_atoms) != 1:
                raise IntentGateError(
                    "v0 SUPERSEDE is exactly 1:1; split/merge is deferred"
                )
            ops.append(
                {
                    "type": typ,
                    "old_ids": list(old_ids),
                    "new_atoms": new_atoms,
                }
            )
        else:
            _require_exact_keys(
                raw,
                required={"type", "ids"},
                label=f"event.ops[{index}]",
            )
            ids = raw["ids"]
            if (
                not isinstance(ids, list)
                or not ids
                or any(not isinstance(x, str) for x in ids)
                or len(ids) != len(set(ids))
            ):
                raise IntentGateError("CANCEL requires unique string ids")
            ops.append({"type": typ, "ids": list(ids)})

    return {
        "v": version,
        "seq": seq,
        "prev": prev,
        "actor": actor,
        "ops": ops,
        "note": note,
    }


def _apply_event(
    *,
    root: Path,
    event: dict[str, Any],
    event_hash: str,
    active: dict[str, dict[str, Any]],
    all_atoms: dict[str, dict[str, Any]],
    statuses: dict[str, str],
    approvals: dict[str, dict[str, Any]],
) -> None:
    ops = event["ops"]

    # Validate all references against the pre-transition state. v0 also
    # forbids reusing a new ID or touching the same active atom twice inside one
    # atomic event.
    new_ids: set[str] = set()
    touched_ids: set[str] = set()
    for op in ops:
        typ = op["type"]
        if typ == "ADD":
            atom = op["atom"]
            atom_id = atom["id"]
            if atom_id in all_atoms or atom_id in new_ids:
                raise IntentGateError(f"atom id already used in/before event: {atom_id}")
            new_ids.add(atom_id)
        elif typ == "SUPERSEDE":
            for atom_id in op["old_ids"]:
                if atom_id not in active:
                    raise IntentGateError(
                        f"SUPERSEDE target is not active: {atom_id}"
                    )
                if atom_id in touched_ids:
                    raise IntentGateError(
                        f"atom targeted more than once in one event: {atom_id}"
                    )
                touched_ids.add(atom_id)
            for atom in op["new_atoms"]:
                atom_id = atom["id"]
                if atom_id in all_atoms or atom_id in new_ids:
                    raise IntentGateError(
                        f"atom id already used in/before event: {atom_id}"
                    )
                new_ids.add(atom_id)
        elif typ == "CANCEL":
            for atom_id in op["ids"]:
                if atom_id not in active:
                    raise IntentGateError(f"CANCEL target is not active: {atom_id}")
                if atom_id in touched_ids:
                    raise IntentGateError(
                        f"atom targeted more than once in one event: {atom_id}"
                    )
                touched_ids.add(atom_id)

    requires_principal = _event_required_principal(ops, active)
    if requires_principal:
        if event["actor"] != "principal":
            raise IntentGateError(
                f"event {event['seq']} modifies principal authority but actor is "
                f"{event['actor']!r}"
            )
        approval = approvals.get(event_hash)
        if approval is None:
            raise IntentGateError(
                f"event {event['seq']} requires principal approval bound to {event_hash}"
            )
    elif event["actor"] == "agent":
        # Agent events can only create/replace/cancel agent-authority atoms.
        pass

    new_active = dict(active)
    new_all = dict(all_atoms)
    new_statuses = dict(statuses)

    for op in ops:
        typ = op["type"]
        if typ == "ADD":
            atom = op["atom"]
            if event["actor"] == "agent" and atom["authority"] != "agent":
                raise IntentGateError("agent event cannot create principal atom")
            # An exact active duplicate is a no-op and must not become history.
            for existing in new_active.values():
                if _atom_semantic_hash(existing) == _atom_semantic_hash(atom):
                    raise IntentGateError(
                        f"ADD is semantic duplicate of active atom {existing['id']}"
                    )
            new_all[atom["id"]] = atom
            new_active[atom["id"]] = atom
            new_statuses[atom["id"]] = "active"

        elif typ == "SUPERSEDE":
            old_atoms = [new_active[x] for x in op["old_ids"]]
            new_atoms = op["new_atoms"]
            old_atom = old_atoms[0]
            new_atom = new_atoms[0]
            if old_atom["key"] != new_atom["key"]:
                raise IntentGateError(
                    "v0 SUPERSEDE must preserve the atom key"
                )
            if old_atom["scope"]["paths"] != new_atom["scope"]["paths"]:
                raise IntentGateError(
                    "v0 SUPERSEDE must preserve exact scope; "
                    "partial-scope replacement is deferred"
                )
            if event["actor"] == "agent":
                if any(a["authority"] != "agent" for a in old_atoms):
                    raise IntentGateError("agent event cannot supersede principal atom")
                if any(a["authority"] != "agent" for a in op["new_atoms"]):
                    raise IntentGateError("agent event cannot create principal atom")
            for atom_id in op["old_ids"]:
                new_active.pop(atom_id)
                new_statuses[atom_id] = "superseded"
            for atom in op["new_atoms"]:
                new_all[atom["id"]] = atom
                new_active[atom["id"]] = atom
                new_statuses[atom["id"]] = "active"

        elif typ == "CANCEL":
            cancelled = [new_active[x] for x in op["ids"]]
            if event["actor"] == "agent" and any(
                a["authority"] != "agent" for a in cancelled
            ):
                raise IntentGateError("agent event cannot cancel principal atom")
            for atom_id in op["ids"]:
                new_active.pop(atom_id)
                new_statuses[atom_id] = "cancelled"

    _validate_active_collisions(new_active)
    for atom in new_active.values():
        _decision_body_ok(root, atom)

    active.clear()
    active.update(new_active)
    all_atoms.clear()
    all_atoms.update(new_all)
    statuses.clear()
    statuses.update(new_statuses)


def resolve(root: Path, *, write_cache: bool = False) -> ResolvedContract:
    root = root.resolve()
    files = _event_files(root)
    if not files:
        raise IntentGateError("intent ledger has no events")
    approvals = _load_approvals(root)
    active: dict[str, dict[str, Any]] = {}
    all_atoms: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    previous_hash: str | None = None
    seen_seq: set[int] = set()

    for expected_seq, path in enumerate(files):
        raw = _load_yaml(path)
        event = _validate_event_shape(raw)
        seq = event["seq"]
        if seq in seen_seq:
            raise IntentGateError(f"duplicate event seq: {seq}")
        seen_seq.add(seq)
        if seq != expected_seq:
            raise IntentGateError(
                f"event sequence gap/fork: expected {expected_seq}, got {seq}"
            )
        prefix = path.name.split("-", 1)[0]
        if prefix.isdigit() and int(prefix) != seq:
            raise IntentGateError(
                f"event filename seq mismatch: {path.name} vs seq={seq}"
            )
        if seq == 0:
            if event["prev"] is not None:
                raise IntentGateError("genesis prev must be null")
        elif event["prev"] != previous_hash:
            raise IntentGateError(
                f"event {seq} prev mismatch: expected {previous_hash}, "
                f"got {event['prev']}"
            )
        canonical_event = {
            "v": event["v"],
            "seq": event["seq"],
            "prev": event["prev"],
            "actor": event["actor"],
            "ops": event["ops"],
            "note": event["note"],
        }
        current_hash = _event_hash(canonical_event)
        _apply_event(
            root=root,
            event=canonical_event,
            event_hash=current_hash,
            active=active,
            all_atoms=all_atoms,
            statuses=statuses,
            approvals=approvals,
        )
        previous_hash = current_hash

    assert previous_hash is not None
    atoms = tuple(
        sorted(
            active.values(),
            key=lambda a: (a["kind"], a["key"], a["id"]),
        )
    )
    result = ResolvedContract(
        active_atoms=atoms,
        all_atoms=all_atoms,
        statuses=statuses,
        ledger_head=previous_hash,
        contract_hash=_contract_hash(atoms),
        event_count=len(files),
    )
    if write_cache:
        write_active_cache(root, result)
    return result


def active_cache_payload(result: ResolvedContract) -> dict[str, Any]:
    return {
        "version": 1,
        "ledger_head": result.ledger_head,
        "contract_hash": result.contract_hash,
        "event_count": result.event_count,
        "atoms": list(result.active_atoms),
    }


def write_active_cache(root: Path, result: ResolvedContract) -> Path:
    path = root / ".intent" / "generated" / "active-contract.json"
    content = json.dumps(
        active_cache_payload(result),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write(path, content)
    return path


def check_active_cache(root: Path, result: ResolvedContract) -> None:
    path = root / ".intent" / "generated" / "active-contract.json"
    if not path.is_file():
        raise IntentGateError("active-contract cache missing; run intent resolve --write")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntentGateError(f"invalid active-contract cache: {exc}") from exc
    if cached != active_cache_payload(result):
        raise IntentGateError("active-contract cache does not match ledger replay")


def _event_requires_principal_for_candidate(
    ops: list[dict[str, Any]],
    active: dict[str, dict[str, Any]],
) -> bool:
    return _event_required_principal(ops, active)


def build_candidate_event(
    *,
    root: Path,
    proposal: dict[str, Any],
    actor: str,
) -> tuple[dict[str, Any], str, bool]:
    current = resolve(root)
    if actor not in AUTHORITIES:
        raise IntentGateError(f"invalid actor: {actor}")
    base = str(proposal.get("base", "")).strip().lower()
    if base != current.ledger_head:
        raise IntentGateError(
            f"stale proposal base: expected {current.ledger_head}, got {base or '<missing>'}"
        )
    raw_ops = proposal.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise IntentGateError("proposal.ops must be a non-empty list")
    event = _validate_event_shape(
        {
            "v": RULE_VERSION,
            "seq": current.event_count,
            "prev": current.ledger_head,
            "actor": actor,
            "ops": raw_ops,
            "note": str(proposal.get("note", "")).strip(),
        }
    )
    event_hash = _event_hash(event)

    # Validate by applying to a copy.
    active = current.active_by_id()
    all_atoms = dict(current.all_atoms)
    statuses = dict(current.statuses)
    requires_principal = _event_requires_principal_for_candidate(event["ops"], active)
    approvals: dict[str, dict[str, Any]] = {}
    if requires_principal:
        if actor != "principal":
            raise IntentGateError(
                "agent actor cannot create, supersede, or cancel principal authority"
            )
        approvals[event_hash] = {
            "event_hash": event_hash,
            "approver": "__candidate_validation__",
        }
    _apply_event(
        root=root,
        event=event,
        event_hash=event_hash,
        active=active,
        all_atoms=all_atoms,
        statuses=statuses,
        approvals=approvals,
    )
    return event, event_hash, requires_principal


def append_event(
    *,
    root: Path,
    proposal_path: Path,
    actor: str,
    approver: str | None = None,
    confirmed: bool = False,
    approved_event_hash: str | None = None,
) -> Path:
    root = root.resolve()

    # Acceptance is one serialized critical section. Candidate construction is
    # repeated under the lock so a concurrent append cannot reuse a stale seq
    # or prev head.
    with _ledger_lock(root):
        check_history_immutability(root)
        proposal = _load_yaml(proposal_path)
        event, event_hash, requires_principal = build_candidate_event(
            root=root,
            proposal=proposal,
            actor=actor,
        )
        seq = event["seq"]
        slug = re.sub(
            r"[^a-z0-9-]+",
            "-",
            proposal_path.stem.lower().replace("_", "-"),
        ).strip("-") or "change"
        event_path = root / ".intent" / "events" / f"{seq:04d}-{slug}.yaml"
        if event_path.exists():
            raise IntentGateError(f"event path already exists: {event_path}")

        approval_path: Path | None = None
        if requires_principal:
            if not confirmed or not approver:
                raise ApprovalRequired(event_hash)
            if approved_event_hash != event_hash:
                raise IntentGateError(
                    "principal confirmation does not match the exact event hash; "
                    "proposal or ledger changed after approval"
                )
            approval_path = (
                root
                / ".intent"
                / "approvals"
                / f"{seq:04d}-{event_hash[:12]}.yaml"
            )

        cache_path = root / ".intent" / "generated" / "active-contract.json"
        old_cache = cache_path.read_bytes() if cache_path.is_file() else None
        approval_created = False
        try:
            # For a principal event, publish its approval first and the event
            # last. A crash between the two leaves only an inert orphan
            # approval rather than an authoritative event that cannot replay.
            #
            # A previous interrupted append may already have published the
            # matching approval. Reuse that exact record, but never overwrite
            # or later roll back an approval we did not create.
            if approval_path is not None:
                approval = {
                    "version": 1,
                    "event_hash": event_hash,
                    "approver": approver,
                    "channel": "interactive-confirmation",
                }
                if approval_path.exists():
                    existing = _load_yaml(approval_path)
                    if existing != approval:
                        raise IntentGateError(
                            f"conflicting approval already exists: {approval_path}"
                        )
                else:
                    _atomic_write(
                        approval_path,
                        yaml.safe_dump(
                            approval,
                            sort_keys=False,
                            allow_unicode=True,
                        ),
                    )
                    approval_created = True

            _atomic_write(
                event_path,
                yaml.safe_dump(event, sort_keys=False, allow_unicode=True),
            )

            result = resolve(root, write_cache=True)
            if result.ledger_head != event_hash:
                raise IntentGateError(
                    "appended event hash does not match ledger head"
                )
            return event_path
        except BaseException:
            if event_path.exists():
                event_path.unlink()
                _fsync_dir(event_path.parent)
            if approval_created and approval_path is not None and approval_path.exists():
                approval_path.unlink()
                _fsync_dir(approval_path.parent)
            if old_cache is None:
                if cache_path.exists():
                    cache_path.unlink()
                    _fsync_dir(cache_path.parent)
            else:
                _atomic_write(cache_path, old_cache.decode("utf-8"))
            raise


def brief(
    root: Path,
    *,
    paths: Iterable[str] = (),
) -> str:
    result = resolve(root)
    wanted = [_normalize_path_prefix(p) for p in paths]
    selected: list[dict[str, Any]] = []
    for atom in result.active_atoms:
        always = (
            atom["kind"] in {"intent", "invariant"}
            or atom["authority"] == "principal"
        )
        applies = _scope_applies(atom["scope"]["paths"], wanted)
        if always or applies:
            selected.append(atom)

    def render_group(title: str, kinds: set[str]) -> list[str]:
        items = [a for a in selected if a["kind"] in kinds]
        if not items:
            return []
        lines = [title]
        for atom in items:
            scope = atom["scope"]["paths"]
            suffix = f" [scope: {', '.join(scope)}]" if scope else ""
            lines.append(
                f"- {atom['id']} ({atom['authority']}): "
                f"{atom['statement']}{suffix}"
            )
        lines.append("")
        return lines

    lines = [
        f"CONTRACT_HASH: {result.contract_hash}",
        f"LEDGER_HEAD: {result.ledger_head}",
        "",
    ]
    lines += render_group("INTENT", {"intent"})
    lines += render_group("MUST PRESERVE", {"invariant"})
    lines += render_group("ACTIVE REQUIREMENTS", {"requirement"})
    lines += render_group("ACTIVE DECISIONS", {"decision"})
    return "\n".join(lines).rstrip() + "\n"




def check_history_immutability(root: Path) -> None:
    """Reject edits/deletions to already-tracked accepted history.

    This is a VCS enforcement aid, not a cryptographic trust boundary. New
    untracked event/approval files are allowed. In non-git test fixtures it is
    a no-op.
    """
    root = root.resolve()
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        return
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
    )
    if head.returncode != 0:
        return
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "HEAD",
            "--name-status",
            "--",
            ".intent/events",
            ".intent/approvals",
            ".intent/decisions",
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise IntentGateError(f"cannot inspect intent history: {proc.stderr.strip()}")
    bad: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        if status.startswith(("M", "D", "R", "C", "T", "U")):
            bad.append(line)
    if bad:
        raise IntentGateError(
            "accepted intent history was modified/deleted; append a new event instead: "
            + "; ".join(bad)
        )

    history = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "log",
            "--full-history",
            "-m",
            "--format=",
            "--name-status",
            "--diff-filter=MDRCTU",
            "--",
            ".intent/events",
            ".intent/approvals",
            ".intent/decisions",
        ],
        text=True,
        capture_output=True,
    )
    if history.returncode != 0:
        raise IntentGateError(
            f"cannot inspect accepted intent history: {history.stderr.strip()}"
        )
    historical_mutations = [
        line for line in history.stdout.splitlines() if line.strip()
    ]
    if historical_mutations:
        raise IntentGateError(
            "accepted intent history was rewritten in git history; "
            "intent changes must be append-only: "
            + "; ".join(historical_mutations[:20])
        )


def preflight(
    root: Path,
    *,
    refs: Iterable[str] = (),
    paths: Iterable[str] = (),
    require_cache: bool = False,
) -> GateReport:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        check_history_immutability(root)
        result = resolve(root)
    except IntentGateError as exc:
        return GateReport(False, (str(exc),), ())
    if require_cache:
        try:
            check_active_cache(root, result)
        except IntentGateError as exc:
            errors.append(str(exc))

    active = result.active_by_id()
    normalized_paths = [_normalize_path_prefix(p) for p in paths]
    for ref in refs:
        if ref not in active:
            errors.append(f"reference is not active: {ref}")
            continue
        atom = active[ref]
        if normalized_paths and not _scope_applies(
            atom["scope"]["paths"], normalized_paths
        ):
            errors.append(
                f"reference {ref} does not apply to requested paths "
                f"{normalized_paths}"
            )
    if not refs:
        warnings.append("no explicit atom references supplied")
    return GateReport(not errors, tuple(errors), tuple(warnings))


def _git_commit(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise IntentGateError(
            "convergence requires a git repository with at least one commit"
        )
    commit = proc.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise IntentGateError(f"invalid git commit id: {commit!r}")
    return commit


def _require_clean_git_worktree(root: Path) -> None:
    """Ensure HEAD identifies the exact implementation being converged.

    Non-git fixtures are allowed for unit tests. In a real Git worktree, PASS
    evidence is only meaningful when tracked/untracked non-ignored changes have
    been committed.
    """
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        raise IntentGateError("convergence requires a git worktree")
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise IntentGateError(
            f"cannot inspect implementation worktree: {proc.stderr.strip()}"
        )
    dirty = [line for line in proc.stdout.splitlines() if line.strip()]
    if dirty:
        raise IntentGateError(
            "convergence requires a clean git worktree so HEAD identifies the "
            "exact implementation; commit/stash changes first"
        )


def _canonical_evidence_payload(raw: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(
        raw,
        required={
            "contract_hash",
            "ledger_head",
            "commit",
            "paths",
            "execute_commands",
            "results",
        },
        label="evidence",
    )
    for field in ("contract_hash", "ledger_head", "commit"):
        if not isinstance(raw[field], str):
            raise IntentGateError(f"evidence.{field} must be a string")
    if not isinstance(raw["paths"], list) or any(
        not isinstance(path, str) for path in raw["paths"]
    ):
        raise IntentGateError("evidence.paths must be a string list")
    if type(raw["execute_commands"]) is not bool:
        raise IntentGateError("evidence.execute_commands must be boolean")
    if not isinstance(raw["results"], list):
        raise IntentGateError("evidence.results must be a list")

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw["results"]):
        if not isinstance(item, dict):
            raise IntentGateError(f"evidence.results[{index}] must be a mapping")
        _require_exact_keys(
            item,
            required={"atom_id", "status", "actor", "evidence"},
            label=f"evidence.results[{index}]",
        )
        if any(not isinstance(item[field], str) for field in ("atom_id", "status", "actor")):
            raise IntentGateError(f"evidence.results[{index}] fields must be strings")
        atom_id = _nfc(item["atom_id"].strip())
        if atom_id in seen:
            raise IntentGateError(f"duplicate evidence result for {atom_id}")
        seen.add(atom_id)
        status = item["status"]
        if status not in {"passed", "failed", "deferred", "open", "not_run"}:
            raise IntentGateError(f"invalid evidence status for {atom_id}: {status!r}")
        actor = item["actor"]
        if actor not in AUTHORITIES:
            raise IntentGateError(f"invalid evidence actor for {atom_id}: {actor!r}")
        _validate_json_value(item["evidence"], label=f"evidence.results[{index}].evidence")
        results.append(
            {
                "atom_id": atom_id,
                "status": status,
                "actor": actor,
                "evidence": _normalize_generic(item["evidence"]),
            }
        )

    normalized_paths = [_normalize_path_prefix(path) for path in raw["paths"]]
    # Root path means full-project evaluation; keep one canonical root marker.
    if "" in normalized_paths:
        normalized_paths = [""]
    else:
        normalized_paths = sorted(set(normalized_paths))

    return {
        "contract_hash": raw["contract_hash"].strip().lower(),
        "ledger_head": raw["ledger_head"].strip().lower(),
        "commit": raw["commit"].strip().lower(),
        "paths": normalized_paths,
        "execute_commands": raw["execute_commands"],
        "results": sorted(results, key=lambda item: item["atom_id"]),
    }


def evidence_hash(raw: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(_canonical_evidence_payload(raw)))


def _load_evidence_approvals(root: Path) -> dict[str, dict[str, Any]]:
    directory = root / ".intent" / "evidence-approvals"
    if not directory.exists():
        return {}
    approvals: dict[str, dict[str, Any]] = {}
    for path in directory.glob("*.yaml"):
        data = _load_yaml(path)
        _require_exact_keys(
            data,
            required={"version", "evidence_hash", "approver", "channel"},
            label=f"evidence approval {path.name}",
        )
        if type(data["version"]) is not int or data["version"] != 1:
            raise IntentGateError(f"{path}: invalid evidence approval version")
        digest = data["evidence_hash"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise IntentGateError(f"{path}: invalid evidence_hash")
        if not isinstance(data["approver"], str) or not data["approver"].strip():
            raise IntentGateError(f"{path}: approver is required")
        if not isinstance(data["channel"], str) or not data["channel"].strip():
            raise IntentGateError(f"{path}: channel is required")
        if digest in approvals:
            raise IntentGateError(f"duplicate evidence approval for {digest}")
        approvals[digest] = data
    return approvals


def approve_evidence(
    root: Path,
    *,
    evidence_path: Path,
    approver: str,
    channel: str = "interactive-confirmation",
    approved_evidence_hash: str,
) -> Path:
    root = root.resolve()
    raw = _load_yaml(evidence_path)
    payload = _canonical_evidence_payload(raw)
    digest = _sha256_bytes(_canonical_json(payload))
    if digest != approved_evidence_hash:
        raise IntentGateError(
            "principal confirmation does not match the exact evidence hash"
        )
    approval = {
        "version": 1,
        "evidence_hash": digest,
        "approver": approver,
        "channel": channel,
    }
    path = root / ".intent" / "evidence-approvals" / f"{digest}.yaml"
    if path.exists():
        existing = _load_yaml(path)
        if existing != approval:
            raise IntentGateError(f"conflicting evidence approval already exists: {path}")
        return path
    _atomic_write(path, yaml.safe_dump(approval, sort_keys=False, allow_unicode=True))
    return path


def converge(
    root: Path,
    *,
    evidence_path: Path | None = None,
    paths: Iterable[str] = (),
    execute_commands: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    check_history_immutability(root)
    _require_clean_git_worktree(root)
    result = resolve(root)
    initial_contract_hash = result.contract_hash
    initial_ledger_head = result.ledger_head
    current_commit = _git_commit(root)

    normalized_paths = [_normalize_path_prefix(p) for p in paths]
    if "" in normalized_paths:
        normalized_paths = [""]
    else:
        normalized_paths = sorted(set(normalized_paths))

    raw_evidence: dict[str, Any] | None = None
    evidence: dict[str, dict[str, Any]] = {}
    evidence_digest: str | None = None
    if evidence_path is not None:
        loaded = _load_yaml(evidence_path)
        raw_evidence = _canonical_evidence_payload(loaded)
        if raw_evidence["contract_hash"] != result.contract_hash:
            raise IntentGateError("evidence contract_hash is stale")
        if raw_evidence["ledger_head"] != result.ledger_head:
            raise IntentGateError("evidence ledger_head is stale")
        if raw_evidence["commit"] != current_commit:
            raise IntentGateError("evidence commit is stale")
        if raw_evidence["paths"] != normalized_paths:
            raise IntentGateError("evidence paths do not match this convergence run")
        if raw_evidence["execute_commands"] != execute_commands:
            raise IntentGateError(
                "evidence execute_commands mode does not match this convergence run"
            )
        evidence_digest = _sha256_bytes(_canonical_json(raw_evidence))
        evidence = {
            item["atom_id"]: item for item in raw_evidence["results"]
        }

    rows: list[dict[str, Any]] = []
    blocked = False
    principal_manual_pass = False

    for atom in result.active_atoms:
        if atom["kind"] == "decision":
            continue
        if normalized_paths and not (
            atom["kind"] in {"intent", "invariant"}
            or atom["authority"] == "principal"
            or _scope_applies(atom["scope"]["paths"], normalized_paths)
        ):
            continue

        verification = atom["verification"]
        row: dict[str, Any] = {
            "atom_id": atom["id"],
            "key": atom["key"],
            "authority": atom["authority"],
            "verification": verification,
        }

        if verification["kind"] == "command":
            if execute_commands:
                proc = subprocess.run(
                    ["/usr/bin/bash", "-lc", verification["ref"]],
                    cwd=root,
                    text=True,
                    capture_output=True,
                )
                row.update(
                    {
                        "status": "passed" if proc.returncode == 0 else "failed",
                        "exit_code": proc.returncode,
                        "stdout": proc.stdout[-4000:],
                        "stderr": proc.stderr[-4000:],
                    }
                )
            else:
                row.update(
                    {
                        "status": "not_run",
                        "error": "command verification disabled for this run",
                    }
                )
        else:
            supplied = evidence.get(atom["id"])
            if supplied is None:
                row.update({"status": "not_run", "error": "manual evidence missing"})
            else:
                status = supplied["status"]
                actor = supplied["actor"]
                evidence_value = supplied["evidence"]
                if status == "passed" and not evidence_value:
                    status = "failed"
                    row["error"] = "passing manual evidence must be non-empty"
                if status == "passed" and atom["authority"] == "principal":
                    if actor != "principal":
                        status = "failed"
                        row["error"] = (
                            "principal manual verification requires principal-authored evidence"
                        )
                    else:
                        principal_manual_pass = True
                row["status"] = status
                row["actor"] = actor
                row["evidence"] = evidence_value

        if row["status"] != "passed":
            blocked = True
        rows.append(row)

    if principal_manual_pass:
        if evidence_digest is None:
            raise IntentGateError(
                "principal manual PASS requires hash-bound evidence approval"
            )
        approvals = _load_evidence_approvals(root)
        if evidence_digest not in approvals:
            raise IntentGateError(
                "principal manual PASS lacks approval bound to evidence hash "
                f"{evidence_digest}"
            )

    # Verification commands are required to be observational. Revalidate the
    # exact implementation/contract snapshot after running them.
    _require_clean_git_worktree(root)
    final_commit = _git_commit(root)
    if final_commit != current_commit:
        raise IntentGateError("git commit changed during convergence")
    final_contract = resolve(root)
    if (
        final_contract.contract_hash != initial_contract_hash
        or final_contract.ledger_head != initial_ledger_head
    ):
        raise IntentGateError("intent contract changed during convergence")

    return {
        "version": 1,
        "contract_hash": result.contract_hash,
        "ledger_head": result.ledger_head,
        "commit": current_commit,
        "paths": normalized_paths,
        "execute_commands": execute_commands,
        "evidence_hash": evidence_digest,
        "status": "BLOCKED" if blocked else "PASS",
        "results": rows,
    }
