from __future__ import annotations

import hashlib
import io
import os
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

    def publish_bytes(self, data: bytes) -> dict[str, object]:
        digest = hashlib.sha256(data).hexdigest()
        destination = self.blobs / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temp = self.tmp / f"{uuid.uuid4().hex}.tmp"
            with temp.open("wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)
        return {"sha256": digest, "path": str(destination), "size": len(data)}

    def publish_file(self, path: Path) -> dict[str, object]:
        return self.publish_bytes(path.read_bytes())

    def seal_directory(self, path: Path) -> dict[str, object]:
        """Serialize a completed attempt directory after all writers stopped."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tf:
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    arc = item.relative_to(path)
                    info = tf.gettarinfo(str(item), arcname=str(arc))
                    # Normalize metadata so equivalent attempt contents hash
                    # consistently apart from their actual bytes.
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with item.open("rb") as f:
                        tf.addfile(info, f)
        return self.publish_bytes(buffer.getvalue())
