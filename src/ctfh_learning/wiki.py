from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .evidence import VerifiedTraceEvidence
from .util import (
    LearningError,
    atomic_json,
    canonical_json,
    require_sha256,
    sha256_bytes,
)


WIKI_EVENT_SCHEMA = "ctfh.learning-wiki-event.v1"
_ALLOWED_KINDS = {
    "observation",
    "hypothesis",
    "evaluation",
    "promotion",
    "rejection",
    "note",
}


def _events_dir(root: Path) -> Path:
    return root.resolve() / "wiki" / "events"


def _lock_path(root: Path) -> Path:
    return root.resolve() / "wiki" / ".lock"


def _event_payload_hash(payload: dict[str, object]) -> str:
    return sha256_bytes(canonical_json(payload))


def replay_wiki(root: Path) -> tuple[dict[str, object], ...]:
    events_dir = _events_dir(root)
    if not events_dir.exists():
        return ()

    files = sorted(events_dir.glob("*.json"))
    previous: str | None = None
    result: list[dict[str, object]] = []
    seen_hashes: set[str] = set()

    for expected_seq, path in enumerate(files):
        if path.stat().st_mode & 0o222:
            raise LearningError(f"wiki event is not sealed read-only: {path}")
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LearningError(f"invalid wiki event: {path}") from exc
        if not isinstance(event, dict):
            raise LearningError(f"wiki event must be a JSON object: {path}")

        event_hash = require_sha256(event.get("event_hash"), "event_hash")
        payload = {k: v for k, v in event.items() if k != "event_hash"}
        if payload.get("schema") != WIKI_EVENT_SCHEMA:
            raise LearningError(f"unsupported wiki event schema: {path}")
        seq = payload.get("seq")
        if seq != expected_seq:
            raise LearningError(
                f"wiki sequence gap/fork: expected {expected_seq}, got {seq}"
            )
        if payload.get("prev") != previous:
            raise LearningError(
                f"wiki prev mismatch at seq {expected_seq}"
            )
        kind = payload.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise LearningError(f"unsupported wiki event kind: {kind!r}")
        if _event_payload_hash(payload) != event_hash:
            raise LearningError(f"wiki event hash mismatch: {path}")
        if event_hash in seen_hashes:
            raise LearningError(f"duplicate wiki event hash: {event_hash}")
        seen_hashes.add(event_hash)

        expected_prefix = f"{expected_seq:06d}-{event_hash[:12]}"
        if path.stem != expected_prefix:
            raise LearningError(
                f"wiki event filename mismatch: {path.name}"
            )

        task_ids = payload.get("task_ids")
        source_hashes = payload.get("source_trace_hashes")
        tags = payload.get("tags")
        if not isinstance(task_ids, list) or not all(
            isinstance(v, str) and v for v in task_ids
        ):
            raise LearningError(f"invalid task_ids in wiki event: {path}")
        if not isinstance(source_hashes, list):
            raise LearningError(
                f"invalid source_trace_hashes in wiki event: {path}"
            )
        for value in source_hashes:
            require_sha256(value, "source_trace_hash")
        if not isinstance(tags, list) or not all(
            isinstance(v, str) and v for v in tags
        ):
            raise LearningError(f"invalid tags in wiki event: {path}")

        previous = event_hash
        result.append(event)

    return tuple(result)


def append_wiki_event(
    root: Path,
    *,
    kind: str,
    body: dict[str, object] | str,
    evidence: Iterable[VerifiedTraceEvidence] = (),
    task_ids: Iterable[str] = (),
    source_trace_hashes: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> dict[str, object]:
    if kind not in _ALLOWED_KINDS:
        raise LearningError(f"unsupported wiki event kind: {kind!r}")

    root = root.resolve()
    events_dir = _events_dir(root)
    events_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    evidence = tuple(evidence)
    all_tasks = {
        *(str(task_id) for task_id in task_ids if str(task_id)),
        *(item.task_id for item in evidence),
    }
    all_sources = {
        *(require_sha256(v, "source_trace_hash") for v in source_trace_hashes),
        *(item.manifest_sha256 for item in evidence),
    }
    clean_tags = sorted({str(tag) for tag in tags if str(tag)})

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = replay_wiki(root)
        previous = (
            str(existing[-1]["event_hash"])
            if existing
            else None
        )
        seq = len(existing)
        payload: dict[str, object] = {
            "schema": WIKI_EVENT_SCHEMA,
            "seq": seq,
            "prev": previous,
            "kind": kind,
            "created_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds"),
            "task_ids": sorted(all_tasks),
            "source_trace_hashes": sorted(all_sources),
            "tags": clean_tags,
            "body": body,
        }
        event_hash = _event_payload_hash(payload)
        event: dict[str, object] = {
            **payload,
            "event_hash": event_hash,
        }
        path = events_dir / f"{seq:06d}-{event_hash[:12]}.json"
        if path.exists():
            raise LearningError(f"wiki event path already exists: {path}")
        atomic_json(path, event, read_only=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return event
