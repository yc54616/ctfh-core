"""Versioned, model-independent subprocess connection owned by the user."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Callable, Protocol

from .live_agent import AgentRunError, AgentTurn, REPORT_SCHEMA
from .live_store import LiveStore
from .live_events import emit


class TurnRunner(Protocol):
    async def run(self, prompt: str, thread_id: str | None,
                  on_thread_id: Callable[[str], None]) -> AgentTurn: ...


class JsonLineAgentRunner:
    """One JSON request on stdin; thread.started / turn.report / turn.failed on stdout."""

    def __init__(self, argv: tuple[str, ...], root: Path, state_dir: Path,
                 timeout_seconds: float, turn_id: int | None = None) -> None:
        self.argv = argv
        self.root = root
        self.state_dir = state_dir
        self.timeout = timeout_seconds
        self.turn_id = turn_id

    async def run(self, prompt: str, thread_id: str | None,
                  on_thread_id: Callable[[str], None]) -> AgentTurn:
        snapshot = None
        if (self.state_dir / "contest.sqlite3").is_file():
            with LiveStore(self.state_dir, create=False) as store:
                snapshot = store.snapshot(self.turn_id)
        request = json.dumps({
            "protocol": "ctfh.turn.v1", "thread_id": thread_id,
            "state_dir": str(self.state_dir), "prompt": prompt,
            "snapshot": snapshot,
            "report_schema": REPORT_SCHEMA,
        }, ensure_ascii=False).encode() + b"\n"
        proc = None
        report = None
        failure = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv, cwd=self.root, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True, limit=2_000_000,
            )

            async def feed() -> None:
                assert proc is not None and proc.stdin is not None
                try:
                    proc.stdin.write(request)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

            async def receive() -> None:
                nonlocal report, failure
                assert proc is not None and proc.stdout is not None
                while line := await proc.stdout.readline():
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise AgentRunError("agent emitted invalid JSONL", kind="invalid_protocol") from exc
                    if not isinstance(value, dict):
                        raise AgentRunError("agent event must be an object", kind="invalid_protocol")
                    event = value.get("type")
                    if event == "thread.started":
                        tid = value.get("thread_id")
                        if not isinstance(tid, str):
                            raise AgentRunError("agent thread ID must be a string", kind="invalid_protocol")
                        on_thread_id(tid)
                    elif event == "turn.report":
                        if report is not None or not isinstance(value.get("report"), dict):
                            raise AgentRunError("exactly one object report is required", kind="invalid_protocol")
                        report = value["report"]
                    elif event == "turn.failed":
                        kind = value.get("kind")
                        failure = kind if kind in {"auth", "quota", "cyber_safety", "timeout"} else "agent_failed"
                    elif event == "progress":
                        message = value.get("message")
                        emit("agent.progress", message if isinstance(message, str) else "연결기 작업 진행")
                    else:
                        raise AgentRunError("unsupported agent event", kind="invalid_protocol")

            await asyncio.wait_for(asyncio.gather(feed(), receive(), proc.wait()), self.timeout)
            if failure or proc.returncode != 0:
                kind = failure or "agent_failed"
                raise AgentRunError(f"connected agent stopped the turn ({kind})", kind=kind)
            if report is None:
                raise AgentRunError("connected agent produced no report", kind="invalid_report")
            return AgentTurn(report, ())
        except FileNotFoundError as exc:
            raise AgentRunError("agent command could not be started", kind="launch_failed") from exc
        except TimeoutError as exc:
            raise AgentRunError("connected agent timed out", kind="timeout") from exc
        finally:
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await proc.wait()
