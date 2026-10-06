from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import uuid
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.tmp = self.root / ".tmp"
        self.blobs = self.root / "sha256"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _destination(self, digest: str) -> Path:
        return self.blobs / digest[:2] / digest

    def _commit_temp(
        self,
        temp: Path,
        *,
        digest: str,
        size: int,
    ) -> dict[str, object]:
        destination = self._destination(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temp.unlink(missing_ok=True)
        else:
            os.replace(temp, destination)
            self._fsync_dir(destination.parent)
        return {
            "sha256": digest,
            "path": str(destination),
            "size": size,
        }

    def publish_bytes(self, data: bytes) -> dict[str, object]:
        digest = hashlib.sha256(data).hexdigest()
        destination = self._destination(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return {
                "sha256": digest,
                "path": str(destination),
                "size": len(data),
            }

        temp = self.tmp / f"{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            return self._commit_temp(
                temp,
                digest=digest,
                size=len(data),
            )
        finally:
            temp.unlink(missing_ok=True)

    def publish_file(self, path: Path) -> dict[str, object]:
        source = path.resolve(strict=True)
        temp = self.tmp / f"{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as src, temp.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            return self._commit_temp(
                temp,
                digest=digest.hexdigest(),
                size=size,
            )
        finally:
            temp.unlink(missing_ok=True)

    def seal_directory(self, path: Path) -> dict[str, object]:
        """Serialize a completed attempt directory without buffering it in RAM."""
        source_root = path.resolve(strict=True)
        temp = self.tmp / f"{uuid.uuid4().hex}.tar.tmp"
        try:
            with tarfile.open(temp, mode="w") as tf:
                for item in sorted(source_root.rglob("*")):
                    if item.is_file():
                        arc = item.relative_to(source_root)
                        info = tf.gettarinfo(
                            str(item),
                            arcname=str(arc),
                        )
                        # Normalize metadata so equivalent attempt contents hash
                        # consistently apart from their actual bytes.
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = 0
                        with item.open("rb") as f:
                            tf.addfile(info, f)
            with temp.open("rb") as f:
                os.fsync(f.fileno())
            digest, size = self._hash_file(temp)
            return self._commit_temp(
                temp,
                digest=digest,
                size=size,
            )
        finally:
            temp.unlink(missing_ok=True)
