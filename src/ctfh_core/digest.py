from __future__ import annotations

import hashlib
import os
from pathlib import Path


class TreeDigestError(ValueError):
    pass


def tree_snapshot_digest(root: Path) -> str:
    """Hash the dereferenced byte snapshot of a directory tree.

    Symlinked files/directories are followed and hashed under the symlink's
    lexical path so the digest matches a copytree(..., symlinks=False)
    materialization. Symlink cycles and special filesystem nodes are rejected.
    """

    root = root.resolve()
    if not root.is_dir():
        raise TreeDigestError(f"not a directory: {root}")

    h = hashlib.sha256()

    def add_record(kind: bytes, rel: str, data: bytes = b"") -> None:
        rel_b = rel.encode()
        h.update(len(kind).to_bytes(1, "big"))
        h.update(kind)
        h.update(len(rel_b).to_bytes(4, "big"))
        h.update(rel_b)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)

    def walk(physical: Path, logical: Path, stack: tuple[Path, ...]) -> None:
        try:
            entries = sorted(os.scandir(physical), key=lambda entry: entry.name)
        except OSError as exc:
            raise TreeDigestError(f"cannot read directory: {physical}") from exc

        for entry in entries:
            lexical = logical / entry.name
            rel = lexical.as_posix()
            path = Path(entry.path)

            if entry.is_symlink():
                try:
                    resolved = path.resolve(strict=True)
                except OSError as exc:
                    raise TreeDigestError(f"broken symlink: {path}") from exc

                if resolved.is_dir():
                    real = resolved.resolve()
                    if real in stack:
                        raise TreeDigestError(f"symlink cycle: {path}")
                    add_record(b"D", rel)
                    walk(real, lexical, (*stack, real))
                    continue

                if resolved.is_file():
                    try:
                        data = resolved.read_bytes()
                    except OSError as exc:
                        raise TreeDigestError(f"cannot read file: {resolved}") from exc
                    add_record(b"F", rel, data)
                    continue

                raise TreeDigestError(f"unsupported symlink target: {path}")

            if entry.is_dir(follow_symlinks=False):
                real = path.resolve()
                if real in stack:
                    raise TreeDigestError(f"directory cycle: {path}")
                add_record(b"D", rel)
                walk(real, lexical, (*stack, real))
                continue

            if entry.is_file(follow_symlinks=False):
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    raise TreeDigestError(f"cannot read file: {path}") from exc
                add_record(b"F", rel, data)
                continue

            raise TreeDigestError(f"unsupported filesystem entry: {path}")

    walk(root, Path("."), (root,))
    return h.hexdigest()
