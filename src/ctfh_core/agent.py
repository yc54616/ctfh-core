from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from pathlib import Path

from .environment import CancelToken, LocalWorkerEnvironmentProvider
from .types import AgentCapabilities, AttemptRequest, AttemptResult, WorkerEnvironment


class CodexCLIAdapter:
    name = "codex-cli"

    def __init__(self, binary: str = "codex"):
        self.binary = binary

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            usage_visibility="none",
            budget_control="none",
            rate_control="launch_only",
            trace_coverage="codex-json-events",
        )

    def version(self) -> str:
        cp = subprocess.run(
            [self.binary, "-V"],
            text=True,
            capture_output=True,
            check=True,
        )
        return cp.stdout.strip() or cp.stderr.strip()

    def command(self, req: AttemptRequest) -> list[str]:
        return [
            self.binary,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-m",
            req.model,
            "-c",
            f'model_reasoning_effort="{req.reasoning_effort}"',
            "-s",
            req.sandbox,
            "--json",
            "-o",
            ".ctfh-final.txt",
            req.prompt,
        ]

    async def run(
        self,
        req: AttemptRequest,
        env: WorkerEnvironment,
        env_provider: LocalWorkerEnvironmentProvider,
        attempt_output_dir: Path,
        cancel: CancelToken,
    ) -> AttemptResult:
        attempt_output_dir.mkdir(parents=True, exist_ok=True)
        trace_path = attempt_output_dir / "trace.jsonl"
        stderr_path = attempt_output_dir / "stderr.log"
        final_message_path = env.workspace / ".ctfh-final.txt"
        candidate_path = env.workspace / "candidate.txt"

        started = time.monotonic()
        first_seen: float | None = None

        with trace_path.open("wb") as trace, stderr_path.open("wb") as err:
            proc = await env_provider.spawn(
                env,
                self.command(req),
                stdout=trace,
                stderr=err,
            )

            async def watch_candidate() -> None:
                nonlocal first_seen
                while proc.returncode is None:
                    if candidate_path.is_file():
                        try:
                            value = candidate_path.read_text(errors="replace").strip()
                        except OSError:
                            value = ""
                        if value:
                            first_seen = time.monotonic() - started
                            return
                    await asyncio.sleep(0.05)

            watcher = asyncio.create_task(watch_candidate())
            cancel_wait = asyncio.create_task(cancel.wait())
            proc_wait = asyncio.create_task(proc.wait())
            done, _ = await asyncio.wait(
                {proc_wait, cancel_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done and proc.returncode is None:
                await env_provider.kill(env)
            exit_code = await proc_wait
            cancel_wait.cancel()
            if not watcher.done():
                if candidate_path.is_file() and first_seen is None:
                    value = candidate_path.read_text(errors="replace").strip()
                    if value:
                        first_seen = time.monotonic() - started
                watcher.cancel()

        finished = time.monotonic()
        final_message = final_message_path.read_text(errors="replace") if final_message_path.is_file() else ""
        candidate = candidate_path.read_text(errors="replace").strip() if candidate_path.is_file() else None
        return AttemptResult(
            exit_code=exit_code,
            final_message=final_message,
            candidate=candidate,
            candidate_first_seen_s=first_seen,
            started_at_monotonic=started,
            finished_at_monotonic=finished,
            trace_path=trace_path,
            final_message_path=final_message_path,
        )


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
