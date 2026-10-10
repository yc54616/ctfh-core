"""Browser receipts independent of any model provider's event format."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .live_store import LiveStore


@dataclass(frozen=True)
class BrowserReceipt:
    turn_id: int
    url: str
    text: str
    observed_at: float


def _directory(state_dir: Path, turn_id: int) -> Path:
    if turn_id < 1:
        raise ValueError("positive turn ID required")
    return state_dir / "browser-receipts" / str(turn_id)


def clear_receipts(state_dir: Path, turn_id: int | None = None) -> None:
    path = state_dir / "browser-receipts" if turn_id is None else _directory(state_dir, turn_id)
    if path.exists():
        shutil.rmtree(path)


def load_receipts(state_dir: Path, turn_id: int) -> tuple[BrowserReceipt, ...]:
    result = []
    for path in sorted(_directory(state_dir, turn_id).glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        receipt = BrowserReceipt(**value)
        if receipt.turn_id != turn_id:
            raise ValueError("browser receipt belongs to another turn")
        result.append(receipt)
    return tuple(result)


def capture_browser(state_dir: Path, turn_id: int, args: list[str]) -> str:
    """Execute chrome-use once, then capture its real URL and output privately."""
    if not args or any(
        arg.split("=", 1)[0] in {
            "--session-name", "--session", "--cdp", "--profile", "--browser",
            "--executable-path", "--user-data-dir",
        } for arg in args
    ):
        raise ValueError("browser command required; session/profile overrides are not allowed")
    with LiveStore(state_dir, create=False) as store:
        store.require_running_turn(turn_id)
        session = str(store.snapshot(turn_id)["browser_session"])
    prefix = ["rtk", "proxy", "chrome-use", "--session", session]
    proc = subprocess.run(prefix + args, capture_output=True, text=True, timeout=90)
    if proc.returncode:
        # Do not copy command arguments or browser errors containing secrets.
        raise RuntimeError(f"chrome-use command failed (exit {proc.returncode})")
    text = proc.stdout
    # Session handoff/close need no page receipt and may prevent a subsequent read.
    if args[0] == "close" or args[:2] == ["session", "handoff"]:
        return text
    url_result = subprocess.run(prefix + ["get", "url"], capture_output=True, text=True, timeout=30)
    urls = []
    for line in url_result.stdout.splitlines():
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        match = re.fullmatch(r"(?:✓\s*)?(https?://\S+)", clean)
        if match:
            urls.append(match.group(1))
    if url_result.returncode or not urls:
        return text  # Navigation/auth diagnostics are useful, but cannot prove a verdict.
    if len(text) > 200_000:
        text = text[:100_000] + "\n[receipt truncated]\n" + text[-100_000:]
    receipt = BrowserReceipt(turn_id, urls[-1], text, time.time())
    with LiveStore(state_dir, create=False) as store:
        store.require_running_turn(turn_id)
        directory = _directory(store.state_dir, turn_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if len(list(directory.glob("*.json"))) >= 512:
            raise ValueError("browser receipt limit reached; finish the current turn")
        path = directory / (uuid.uuid4().hex + ".json")
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            json.dump(asdict(receipt), handle, ensure_ascii=False)
    return proc.stdout


def verify_verdicts(store: LiveStore, report: dict[str, object], receipts: tuple[BrowserReceipt, ...]) -> None:
    """Require actual, fresh browser evidence on the contest's own origin."""
    origin = urlsplit(str(store.contest()["site_url"]))
    for name in ("challenges", "submissions"):
        values = report.get(name)
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise ValueError(f"report {name} must contain objects")

    def matches(evidence: object, url: object = None, after: float = 0) -> bool:
        if not isinstance(evidence, str) or not evidence.strip():
            return False
        for receipt in receipts:
            parsed = urlsplit(receipt.url)
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                continue
            if url is not None and receipt.url != url:
                continue
            if receipt.observed_at >= after and evidence in receipt.text:
                return True
        return False

    for challenge in report.get("challenges", []):
        if challenge.get("state") == "solved" and not matches(challenge.get("site_evidence")):
            raise ValueError("solved challenge needs a current contest browser receipt")
    for submission in report.get("submissions", []):
        if submission.get("verdict") == "unknown":
            continue
        row = store.conn.execute(
            "SELECT created_at FROM submissions WHERE challenge_key=? AND flag_sha256=?",
            (submission.get("challenge_key"), submission.get("flag_sha256")),
        ).fetchone()
        if row is None or not matches(
            submission.get("site_evidence"), submission.get("observed_url"), float(row["created_at"])
        ):
            raise ValueError("submission verdict needs a fresh browser receipt after its journal entry")
