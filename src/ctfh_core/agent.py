from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from .digest import tree_snapshot_digest
from .environment import CancelToken, LocalWorkerEnvironmentProvider
from .types import AgentCapabilities, AttemptRequest, AttemptResult, WorkerEnvironment


class AgentAdapter(Protocol):
    name: str
    binary: str

    def capabilities(self) -> AgentCapabilities: ...
    def version(self) -> str: ...

    def materialize(
        self,
        env: WorkerEnvironment,
        env_provider: LocalWorkerEnvironmentProvider,
        runlock_base: dict[str, object],
    ) -> dict[str, object]: ...

    async def run(
        self,
        req: AttemptRequest,
        env: WorkerEnvironment,
        env_provider: LocalWorkerEnvironmentProvider,
        attempt_output_dir: Path,
        cancel: CancelToken,
    ) -> AttemptResult: ...


def _make_attempt_tree_writable(root: Path) -> None:
    """Normalize copied snapshot modes for owner cleanup.

    Read-only semantics are enforced by the bwrap ro-bind. The host-side
    attempt copy must remain owner-writable so cancellation/recovery can
    always delete it, even when the promoted source is 0555/0444.
    """
    if not root.is_dir():
        return
    root.chmod(0o755)
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            executable = bool(path.stat().st_mode & 0o111)
            path.chmod(0o755 if executable else 0o644)


def _materialize_frozen_knowledge(
    env: WorkerEnvironment,
    env_provider: LocalWorkerEnvironmentProvider,
    runlock_base: dict[str, object],
) -> str | None:
    memory = runlock_base.get("memory")
    if not isinstance(memory, dict):
        raise ValueError("RunLock missing memory configuration")
    mode = str(memory.get("mode", "off"))
    raw_digest = memory.get("knowledge_snapshot_digest")
    expected_digest = str(raw_digest) if raw_digest is not None else None

    destination = env_provider._knowledge_runtime_dir(env.root)
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)

    if mode == "off":
        if expected_digest is not None:
            raise ValueError("memory=off cannot carry a knowledge digest")
        return None
    if mode != "frozen":
        raise ValueError(f"unsupported memory mode: {mode}")
    if expected_digest is None:
        raise ValueError("memory=frozen requires a knowledge digest")

    source = env_provider.knowledge_snapshot_dir
    if source is None or not source.is_dir():
        raise RuntimeError(
            "memory=frozen requires an available knowledge snapshot directory"
        )
    source_digest = tree_snapshot_digest(source)
    if source_digest != expected_digest:
        raise RuntimeError(
            "knowledge source changed after RunLock resolution"
        )

    shutil.copytree(source, destination, symlinks=False)
    _make_attempt_tree_writable(destination)
    materialized_digest = tree_snapshot_digest(destination)
    if materialized_digest != source_digest:
        raise RuntimeError("materialized knowledge digest mismatch")
    return materialized_digest


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

    @staticmethod
    def materialized_config_text() -> str:
        return """web_search = "disabled"

[features]
memories = false
hooks = false
plugins = false
recommended_plugins = false
remote_plugin = false
plugin_sharing = false
skill_mcp_dependency_install = false
daemon_auto_start = false
"""

    @classmethod
    def materialized_config_digest(cls) -> str:
        return hashlib.sha256(cls.materialized_config_text().encode()).hexdigest()

    def materialize(
        self,
        env: WorkerEnvironment,
        env_provider: LocalWorkerEnvironmentProvider,
        runlock_base: dict[str, object],
    ) -> dict[str, object]:
        skills_config = runlock_base.get("skills")
        if not isinstance(skills_config, dict):
            raise ValueError("RunLock missing skills configuration")
        skill_mode = str(skills_config.get("mode", "off"))
        raw_digest = skills_config.get("digest")
        expected_skill_digest = (
            str(raw_digest) if raw_digest is not None else None
        )
        if skill_mode not in {"host-pinned", "off"}:
            raise ValueError(f"unsupported skill mode: {skill_mode}")

        source_home = env_provider.codex_home
        runtime_home = env.root / "codex-home"
        runtime_home.mkdir(parents=True, exist_ok=True)
        config_path = runtime_home / "config.toml"
        config_path.write_text(self.materialized_config_text())
        destination = runtime_home / "skills"

        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)

        skill_digest: str | None = None
        if skill_mode == "host-pinned":
            source = source_home / "skills"
            if source.is_dir():
                source_digest = tree_snapshot_digest(source)
                if (
                    expected_skill_digest is not None
                    and source_digest != expected_skill_digest
                ):
                    raise RuntimeError(
                        "skill source changed after RunLock resolution"
                    )
                shutil.copytree(source, destination, symlinks=False)
                _make_attempt_tree_writable(destination)
                skill_digest = tree_snapshot_digest(destination)
                if skill_digest != source_digest:
                    raise RuntimeError("materialized skill digest mismatch")
            else:
                destination.mkdir(parents=True, exist_ok=True)
                if expected_skill_digest is not None:
                    raise RuntimeError(
                        "RunLock expected a skill set but source is missing"
                    )
        else:
            destination.mkdir(parents=True, exist_ok=True)

        knowledge_digest = _materialize_frozen_knowledge(
            env, env_provider, runlock_base
        )
        manifest: dict[str, object] = {
            "adapter": self.name,
            "native_config_policy": "ctfh-minimal-v1",
            "native_config_digest": self.materialized_config_digest(),
            "skill_mode": skill_mode,
            "skill_set_digest": skill_digest,
            "knowledge_snapshot_digest": knowledge_digest,
        }
        (env.root / "native-materialization.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        return manifest

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


class ClaudeCLIAdapter:
    name = "claude-cli"
    native_config_policy = "claude-safe-mode-v1"
    tools = ("Bash", "Edit", "Read", "Write", "Glob", "Grep")

    def __init__(self, binary: str = "claude"):
        self.binary = binary

    @classmethod
    def native_config_digest(cls) -> str:
        value = {
            "policy": cls.native_config_policy,
            "safe_mode": True,
            "permission_mode": "bypassPermissions",
            "permission_prompts": "none",
            "no_session_persistence": True,
            "no_chrome": True,
            "strict_mcp_config": True,
            "tools": list(cls.tools),
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            usage_visibility="claude-stream-json",
            budget_control="max-budget-usd",
            rate_control="launch_only",
            trace_coverage="claude-stream-json-events",
        )

    def version(self) -> str:
        cp = subprocess.run(
            [self.binary, "--version"],
            text=True,
            capture_output=True,
            check=True,
        )
        return cp.stdout.strip() or cp.stderr.strip()

    def materialize(
        self,
        env: WorkerEnvironment,
        env_provider: LocalWorkerEnvironmentProvider,
        runlock_base: dict[str, object],
    ) -> dict[str, object]:
        skills_config = runlock_base.get("skills", {"mode": "off"})
        if not isinstance(skills_config, dict):
            raise ValueError("RunLock skills configuration must be a mapping")
        if str(skills_config.get("mode", "off")) != "off":
            raise ValueError("Claude safe-mode adapter requires skills=off")
        runtime_home = env.root / "claude-home"
        runtime_home.mkdir(parents=True, exist_ok=True)
        knowledge_digest = _materialize_frozen_knowledge(
            env, env_provider, runlock_base
        )
        manifest: dict[str, object] = {
            "adapter": self.name,
            "native_config_policy": self.native_config_policy,
            "native_config_digest": self.native_config_digest(),
            "skill_mode": "off",
            "knowledge_snapshot_digest": knowledge_digest,
        }
        (env.root / "claude-native-materialization.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        return manifest

    def command(self, req: AttemptRequest) -> list[str]:
        return [
            self.binary,
            "-p",
            "--safe-mode",
            "--model",
            req.model,
            "--effort",
            req.reasoning_effort,
            "--permission-mode",
            "bypassPermissions",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--no-chrome",
            "--strict-mcp-config",
            "--tools",
            ",".join(self.tools),
            "--output-format",
            "stream-json",
            "--verbose",
            req.prompt,
        ]

    @staticmethod
    def _final_message(trace_path: Path) -> str:
        final = ""
        with trace_path.open("r", encoding="utf-8", errors="replace") as trace:
            for raw in trace:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    value = event.get("result")
                    if isinstance(value, str):
                        final = value
        return final

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
        final_message_path = attempt_output_dir / "final_message.txt"
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
                            value = candidate_path.read_text(
                                errors="replace"
                            ).strip()
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
                    value = candidate_path.read_text(
                        errors="replace"
                    ).strip()
                    if value:
                        first_seen = time.monotonic() - started
                watcher.cancel()

        finished = time.monotonic()
        final_message = self._final_message(trace_path)
        final_message_path.write_text(final_message)
        candidate = (
            candidate_path.read_text(errors="replace").strip()
            if candidate_path.is_file()
            else None
        )
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


def make_agent_adapter(name: str) -> AgentAdapter:
    if name == "codex":
        return CodexCLIAdapter()
    if name == "claude":
        return ClaudeCLIAdapter()
    raise ValueError(f"unsupported agent adapter: {name}")


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
