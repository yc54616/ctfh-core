from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .util import LearningError, require_sha256, sha256_file


TRACE_MANIFEST_SCHEMA = "ctfh.trace-manifest.v1"
_ALLOWED_TRACE_KINDS = {
    "agent.native.events",
    "environment.pty.transcript",
    "environment.pty.metadata",
    "environment.browser.operations",
    "environment.browser.service-stderr",
}
_FORBIDDEN_PARTS = {
    "evaluator.json",
    "result.json",
    "state.db",
    "candidate.txt",
}


@dataclass(frozen=True)
class VerifiedTraceEvidence:
    manifest_path: Path
    manifest_sha256: str
    task_id: str
    attempt_id: str
    runlock_base_hash: str
    producer_adapter: str
    streams: tuple[dict[str, object], ...]


def verify_trace_manifest(path: Path) -> VerifiedTraceEvidence:
    manifest = path.resolve(strict=True)
    if not manifest.is_file():
        raise LearningError(f"trace manifest is not a file: {manifest}")
    if manifest.stat().st_mode & 0o222:
        raise LearningError("trace manifest must be sealed read-only")

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningError(f"invalid trace manifest: {manifest}") from exc
    if not isinstance(data, dict):
        raise LearningError("trace manifest must be a JSON object")
    if data.get("schema") != TRACE_MANIFEST_SCHEMA:
        raise LearningError(
            f"unsupported trace manifest schema: {data.get('schema')!r}"
        )

    task_id = data.get("task_id")
    attempt_id = data.get("attempt_id")
    producer_adapter = data.get("producer_adapter")
    if not isinstance(task_id, str) or not task_id:
        raise LearningError("trace manifest missing task_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise LearningError("trace manifest missing attempt_id")
    if not isinstance(producer_adapter, str) or not producer_adapter:
        raise LearningError("trace manifest missing producer_adapter")
    runlock_base_hash = require_sha256(
        data.get("runlock_base_hash"),
        "runlock_base_hash",
    )

    raw_streams = data.get("streams")
    if not isinstance(raw_streams, list) or not raw_streams:
        raise LearningError(
            "trace manifest must contain at least one evidence stream"
        )

    attempt_root = manifest.parent.resolve()
    verified: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(raw_streams):
        if not isinstance(raw, dict):
            raise LearningError(f"stream {index} must be an object")
        kind = raw.get("kind")
        if kind not in _ALLOWED_TRACE_KINDS:
            raise LearningError(
                f"stream {index} has unsupported kind: {kind!r}"
            )
        rel_text = raw.get("path")
        if not isinstance(rel_text, str) or not rel_text:
            raise LearningError(f"stream {index} missing path")
        rel = Path(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            raise LearningError(
                f"stream {index} path escapes attempt: {rel_text}"
            )
        if any(part in _FORBIDDEN_PARTS for part in rel.parts):
            raise LearningError(f"forbidden learning input path: {rel_text}")
        if rel.as_posix() in seen_paths:
            raise LearningError(f"duplicate trace stream path: {rel_text}")
        seen_paths.add(rel.as_posix())

        try:
            candidate = (attempt_root / rel).resolve(strict=True)
        except OSError as exc:
            raise LearningError(f"missing trace stream: {rel_text}") from exc
        try:
            candidate.relative_to(attempt_root)
        except ValueError as exc:
            raise LearningError(
                f"stream path escapes attempt root: {rel_text}"
            ) from exc
        if not candidate.is_file():
            raise LearningError(f"trace stream is not a file: {rel_text}")
        if candidate.stat().st_mode & 0o222:
            raise LearningError(
                f"trace stream is not sealed read-only: {rel_text}"
            )

        expected_digest = require_sha256(
            raw.get("sha256"),
            f"stream {index} sha256",
        )
        actual_digest = sha256_file(candidate)
        if actual_digest != expected_digest:
            raise LearningError(
                f"trace stream digest mismatch: {rel_text}"
            )
        expected_size = raw.get("bytes")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise LearningError(f"stream {index} has invalid byte size")
        if candidate.stat().st_size != expected_size:
            raise LearningError(f"trace stream size mismatch: {rel_text}")

        verified.append(dict(raw))

    return VerifiedTraceEvidence(
        manifest_path=manifest,
        manifest_sha256=sha256_file(manifest),
        task_id=task_id,
        attempt_id=attempt_id,
        runlock_base_hash=runlock_base_hash,
        producer_adapter=producer_adapter,
        streams=tuple(verified),
    )
