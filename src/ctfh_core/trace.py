from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

TRACE_MANIFEST_SCHEMA = "ctfh.trace-manifest.v1"
CTFH_JSONL_RECORD_VERSION = 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _jsonl_record_count(path: Path) -> int | None:
    try:
        count = 0
        with path.open("rb") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
    with temp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _descriptor(
    attempt_output: Path,
    path: Path,
    *,
    stream_id: str,
    kind: str,
    format_name: str,
    producer: str,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    relative = path.resolve().relative_to(attempt_output.resolve())
    item: dict[str, Any] = {
        "stream_id": stream_id,
        "kind": kind,
        "format": format_name,
        "producer": producer,
        "path": relative.as_posix(),
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "coverage": coverage,
    }
    if "jsonl" in format_name:
        item["records"] = _jsonl_record_count(path)
    return item


def _agent_coverage(declaration: str) -> dict[str, Any]:
    return {
        "declared": declaration,
        "completeness": "native-agent-defined",
        "captures": [
            "all bytes emitted by the native agent CLI to its configured trace stream",
        ],
        "does_not_guarantee": [
            "hidden model reasoning",
            "tool or observation data the native agent does not emit",
            "semantic completeness beyond the native CLI contract",
        ],
        "ordering": "file-order",
    }


def _session_coverage(
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if metadata is None:
        return {
            "completeness": "pty-output-accounting-unknown",
            "captures": ["retained PTY transcript bytes"],
            "does_not_guarantee": [
                "whether the transcript was quota-truncated",
                "stdin bytes unless echoed by the terminal program",
                "separate stdout and stderr channels",
                "per-byte timestamps",
            ],
            "ordering": "byte-order",
            "captured_bytes": None,
            "observed_bytes": None,
            "capture_limit_bytes": None,
            "truncated": None,
        }

    truncated = bool(metadata.get("transcript_truncated", False))
    captured = metadata.get("transcript_bytes")
    total = metadata.get("total_bytes")
    limit = metadata.get("max_transcript_bytes")
    return {
        "completeness": (
            "pty-output-prefix-truncated" if truncated else "pty-output"
        ),
        "captures": [
            (
                "the bounded prefix of PTY output retained by the session "
                "service"
                if truncated
                else "all PTY output observed by the session reader"
            ),
        ],
        "does_not_guarantee": [
            "stdin bytes unless echoed by the terminal program",
            "separate stdout and stderr channels",
            "per-byte timestamps",
            *(
                ["PTY bytes after the configured transcript quota"]
                if truncated
                else []
            ),
        ],
        "ordering": "byte-order",
        "captured_bytes": captured,
        "observed_bytes": total,
        "capture_limit_bytes": limit,
        "truncated": truncated,
    }


def _browser_coverage() -> dict[str, Any]:
    return {
        "completeness": "ctfh-recorded-browser-operations",
        "captures": [
            "allowlisted CTFH browser controller operations",
            "redacted operation arguments",
            "operation duration",
            "result byte count and SHA-256",
        ],
        "does_not_guarantee": [
            "raw browser result bodies in the trace record",
            "raw typed text",
            "raw eval expressions",
            "full CDP event stream",
        ],
        "ordering": "file-order",
    }


def _browser_stderr_coverage() -> dict[str, Any]:
    return {
        "completeness": "service-stderr",
        "captures": ["stderr emitted by the attempt-owned GhostChrome serve process"],
        "does_not_guarantee": ["Chrome child-process stderr emitted outside GhostChrome"],
        "ordering": "byte-order",
    }


def trace_manifest_reference(
    manifest: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": payload["schema"],
        "path": str(manifest),
        "sha256": _sha256_file(manifest),
        "size": manifest.stat().st_size,
        "stream_count": len(payload.get("streams", [])),
    }


def seal_trace_manifest(
    attempt_output: Path,
    *,
    task_id: str,
    attempt_id: str,
    runlock_base_hash: str,
    adapter_name: str,
    agent_trace_coverage: str,
    timed_out: bool,
) -> tuple[Path, dict[str, Any]]:
    attempt_output = attempt_output.resolve()
    streams: list[dict[str, Any]] = []

    agent_trace = attempt_output / "trace.jsonl"
    if agent_trace.is_file():
        streams.append(
            _descriptor(
                attempt_output,
                agent_trace,
                stream_id="agent-native",
                kind="agent.native.events",
                format_name="native-jsonl",
                producer=adapter_name,
                coverage=_agent_coverage(agent_trace_coverage),
            )
        )

    sessions_dir = attempt_output / "services" / "sessions"
    if sessions_dir.is_dir():
        session_metadata_by_id: dict[str, dict[str, Any]] = {}
        metadata_path = sessions_dir / "metadata.json"
        max_transcript_bytes: int | None = None
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("invalid session trace metadata") from exc
            if not isinstance(metadata, dict):
                raise ValueError("session trace metadata must be an object")
            max_value = metadata.get("max_transcript_bytes")
            if isinstance(max_value, int) and max_value >= 0:
                max_transcript_bytes = max_value
            raw_sessions = metadata.get("sessions", [])
            if not isinstance(raw_sessions, list):
                raise ValueError("session trace metadata sessions must be a list")
            for raw in raw_sessions:
                if not isinstance(raw, dict):
                    raise ValueError("session trace metadata entry must be an object")
                session_id = raw.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("session trace metadata missing session_id")
                session_metadata_by_id[session_id] = {
                    **raw,
                    "max_transcript_bytes": max_transcript_bytes,
                }
            streams.append(
                _descriptor(
                    attempt_output,
                    metadata_path,
                    stream_id="session-capture-metadata",
                    kind="environment.pty.metadata",
                    format_name="json",
                    producer="worker-environment.session-service",
                    coverage={
                        "completeness": "session-capture-accounting",
                        "ordering": "not-applicable",
                    },
                )
            )

        for index, path in enumerate(sorted(sessions_dir.glob("*.log"))):
            if not path.is_file():
                continue
            streams.append(
                _descriptor(
                    attempt_output,
                    path,
                    stream_id=f"session-{index}",
                    kind="environment.pty.transcript",
                    format_name="pty-bytes",
                    producer="worker-environment.session-service",
                    coverage=_session_coverage(
                        session_metadata_by_id.get(path.stem)
                    ),
                )
            )

    browser_dir = attempt_output / "services" / "browser"
    browser_trace = browser_dir / "trace.jsonl"
    if browser_trace.is_file():
        streams.append(
            _descriptor(
                attempt_output,
                browser_trace,
                stream_id="browser-operations",
                kind="environment.browser.operations",
                format_name="ctfh-jsonl-v1",
                producer="worker-environment.ghostchrome-service",
                coverage=_browser_coverage(),
            )
        )
    browser_stderr = browser_dir / "serve.stderr.log"
    if browser_stderr.is_file():
        streams.append(
            _descriptor(
                attempt_output,
                browser_stderr,
                stream_id="browser-service-stderr",
                kind="environment.browser.service-stderr",
                format_name="text-bytes",
                producer="worker-environment.ghostchrome-service",
                coverage=_browser_stderr_coverage(),
            )
        )

    payload: dict[str, Any] = {
        "schema": TRACE_MANIFEST_SCHEMA,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "runlock_base_hash": runlock_base_hash,
        "producer_adapter": adapter_name,
        "timed_out": timed_out,
        "coverage": {
            "status": "partial",
            "reason": (
                "native-agent semantic coverage is producer-dependent; "
                "service streams declare their own capture boundaries"
            ),
        },
        "streams": streams,
    }
    manifest = attempt_output / "trace-manifest.json"
    _atomic_json(manifest, payload)

    # Evidence is immutable after sealing. Candidate/final-message files remain
    # ordinary attempt outputs and are intentionally not changed here.
    for item in streams:
        path = attempt_output / str(item["path"])
        path.chmod(0o444)
    manifest.chmod(0o444)
    return manifest, payload
