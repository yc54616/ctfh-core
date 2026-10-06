from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


class LearningError(ValueError):
    pass


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(
    path: Path,
    payload: dict[str, object],
    *,
    read_only: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    data = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ).encode() + b"\n"
    with temp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    if read_only:
        path.chmod(0o444)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _record(kind: bytes, rel: str, data: bytes = b"") -> bytes:
    rel_b = rel.encode()
    return (
        len(kind).to_bytes(1, "big")
        + kind
        + len(rel_b).to_bytes(4, "big")
        + rel_b
        + len(data).to_bytes(8, "big")
        + data
    )


def tree_snapshot_digest(root: Path) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise LearningError(f"not a directory: {root}")

    h = hashlib.sha256()

    def walk(physical: Path, logical: Path, stack: tuple[Path, ...]) -> None:
        try:
            entries = sorted(os.scandir(physical), key=lambda entry: entry.name)
        except OSError as exc:
            raise LearningError(f"cannot read directory: {physical}") from exc

        for entry in entries:
            lexical = logical / entry.name
            rel = lexical.as_posix()
            path = Path(entry.path)

            if entry.is_symlink():
                try:
                    resolved = path.resolve(strict=True)
                except OSError as exc:
                    raise LearningError(f"broken symlink: {path}") from exc
                if resolved.is_dir():
                    real = resolved.resolve()
                    if real in stack:
                        raise LearningError(f"symlink cycle: {path}")
                    h.update(_record(b"D", rel))
                    walk(real, lexical, (*stack, real))
                    continue
                if resolved.is_file():
                    try:
                        data = resolved.read_bytes()
                    except OSError as exc:
                        raise LearningError(f"cannot read file: {resolved}") from exc
                    h.update(_record(b"F", rel, data))
                    continue
                raise LearningError(f"unsupported symlink target: {path}")

            if entry.is_dir(follow_symlinks=False):
                real = path.resolve()
                if real in stack:
                    raise LearningError(f"directory cycle: {path}")
                h.update(_record(b"D", rel))
                walk(real, lexical, (*stack, real))
                continue

            if entry.is_file(follow_symlinks=False):
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    raise LearningError(f"cannot read file: {path}") from exc
                h.update(_record(b"F", rel, data))
                continue

            raise LearningError(f"unsupported filesystem entry: {path}")

    walk(root, Path("."), (root,))
    return h.hexdigest()


def copy_snapshot(source: Path, destination: Path) -> str:
    source = source.resolve()
    digest = tree_snapshot_digest(source)
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.copytree(source, destination, symlinks=False)
    copied = tree_snapshot_digest(destination)
    if copied != digest:
        raise LearningError("copied snapshot digest mismatch")
    return digest


def make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def require_sha256(value: object, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise LearningError(f"{label} must be a lowercase sha256 hex digest")
    return text
