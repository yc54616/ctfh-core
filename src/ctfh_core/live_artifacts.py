"""Private, durable files required for an agent-accepted live solve."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

from .live_store import LiveStore


def verify_accepted_artifacts(store: LiveStore, report: dict[str, object]) -> None:
    """Do not commit an accepted submission without its flag and write-up."""
    for submission in report.get("submissions", []):
        if not isinstance(submission, dict) or submission.get("verdict") != "accepted":
            continue
        key = submission.get("challenge_key")
        digest = submission.get("flag_sha256")
        if not isinstance(key, str) or not isinstance(digest, str):
            raise ValueError("accepted submission needs a challenge key and flag digest")
        workspace = store.workspace(key)
        flag = workspace / "flag.txt"
        writeup = workspace / "writeup.md"
        for path in (flag, writeup):
            if path.is_symlink() or not path.is_file():
                raise ValueError("accepted submission needs private flag.txt and writeup.md files")
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError("accepted submission artifacts must be private (mode 0600)")
        flag_bytes = flag.read_bytes()
        if not flag_bytes or len(flag_bytes) > 4096 or hashlib.sha256(flag_bytes).hexdigest() != digest:
            raise ValueError("saved flag.txt does not match the accepted candidate")
        if not writeup.read_text(encoding="utf-8").strip():
            raise ValueError("accepted submission needs a nonempty writeup.md")
