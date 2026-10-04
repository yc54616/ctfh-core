from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
from dataclasses import asdict
from pathlib import Path

from .types import TaskSpec, WorkerEnvironment, WorkerOwnerTag


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w") as f:
        json.dump(payload, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _pg_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class LocalWorkerEnvironmentProvider:
    """Attempt-private bubblewrap environment."""

    def __init__(self, root: Path, *, codex_home: Path | None = None, home: Path | None = None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.home = (Path.home() if home is None else home).resolve()
        source = Path(os.environ.get("CODEX_HOME", str(self.home / ".codex"))) if codex_home is None else codex_home
        self.codex_home = source.resolve()
        self._live: dict[str, WorkerEnvironment] = {}
        self._procs: dict[int, asyncio.subprocess.Process] = {}

    def _env_root(self, owner: WorkerOwnerTag) -> Path:
        return self.root / owner.label().replace(":", "_")

    @staticmethod
    def _marker_path(root: Path) -> Path:
        return root / ".ctfh-owner.json"

    def _write_marker(self, env: WorkerEnvironment, *, spawn_state: str = "idle") -> None:
        _atomic_json(
            self._marker_path(env.root),
            {
                "owner": asdict(env.owner),
                "owner_label": env.owner.label(),
                "pgids": sorted(env.processes),
                "spawn_state": spawn_state,
            },
        )

    def _create_sync(self, task: TaskSpec, owner: WorkerOwnerTag) -> WorkerEnvironment:
        root = self._env_root(owner)
        if root.exists():
            shutil.rmtree(root)
        workspace = root / "workspace"
        root.mkdir(parents=True, exist_ok=True)
        env = WorkerEnvironment(owner=owner, root=root, workspace=workspace)
        self._write_marker(env, spawn_state="provisioning")
        shutil.copytree(task.public_dir, workspace)
        self._write_marker(env, spawn_state="ready")
        return env

    async def create(self, task: TaskSpec, owner: WorkerOwnerTag, cancel: CancelToken) -> WorkerEnvironment:
        # Synchronous and bounded in the v0 local provider: no background copy
        # can outlive cancellation and later publish an untracked workspace.
        if cancel.cancelled():
            raise asyncio.CancelledError
        env = self._create_sync(task, owner)
        if cancel.cancelled():
            await self.destroy(owner)
            raise asyncio.CancelledError
        self._live[owner.label()] = env
        return env

    def _sandbox_command(self, env: WorkerEnvironment, argv: list[str]) -> list[str]:
        sandbox_home = self.home
        sandbox_codex = sandbox_home / ".codex"
        cmd = [
            "/usr/bin/bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--new-session",
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/sbin", "/sbin",
            "--ro-bind", "/etc", "/etc",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--dir", "/home",
            "--dir", str(sandbox_home),
            "--ro-bind", str(self.codex_home), str(sandbox_codex),
        ]
        for relative in (".nvm", ".local"):
            source = self.home / relative
            if source.exists():
                cmd += ["--ro-bind", str(source), str(sandbox_home / relative)]
        cmd += [
            "--bind", str(env.workspace), "/workspace",
            "--chdir", "/workspace",
            "--clearenv",
            "--setenv", "HOME", str(sandbox_home),
            "--setenv", "CODEX_HOME", str(sandbox_codex),
            "--setenv", "PATH", os.environ.get("PATH", "/usr/bin:/bin"),
            "--setenv", "USER", os.environ.get("USER", self.home.name),
            "--setenv", "LOGNAME", os.environ.get("LOGNAME", self.home.name),
            "--setenv", "LANG", os.environ.get("LANG", "C.UTF-8"),
        ]
        for name in ("OPENAI_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            value = os.environ.get(name)
            if value:
                cmd += ["--setenv", name, value]
        return cmd + argv

    async def spawn(self, env: WorkerEnvironment, argv: list[str], *, stdout, stderr) -> asyncio.subprocess.Process:
        # A host crash in the spawn->marker window still kills the sandbox
        # through bwrap --die-with-parent.
        self._write_marker(env, spawn_state="spawning")
        proc = await asyncio.create_subprocess_exec(
            *self._sandbox_command(env, argv),
            cwd="/",
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        env.processes.add(proc.pid)
        self._procs[proc.pid] = proc
        self._write_marker(env, spawn_state="running")
        return proc

    async def _kill_pgid(self, pgid: int, proc: asyncio.subprocess.Process | None, *, grace_s: float) -> None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=grace_s)
            except TimeoutError:
                pass
        if _pg_alive(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=2.0)
            except TimeoutError as exc:
                raise RuntimeError(f"worker process group {pgid} did not exit") from exc
        deadline = time.monotonic() + 2.0
        while _pg_alive(pgid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if _pg_alive(pgid):
            raise RuntimeError(f"worker process group {pgid} still alive after SIGKILL")

    async def kill(self, env: WorkerEnvironment, *, grace_s: float = 1.0) -> None:
        pgids = list(env.processes)
        for pgid in pgids:
            await self._kill_pgid(pgid, self._procs.get(pgid), grace_s=grace_s)
        for pgid in pgids:
            self._procs.pop(pgid, None)
            env.processes.discard(pgid)
        if env.root.exists():
            self._write_marker(env, spawn_state="stopped")

    async def destroy(self, owner: WorkerOwnerTag) -> None:
        label = owner.label()
        env = self._live.pop(label, None)
        if env is not None:
            await self.kill(env)
        root = self._env_root(owner)
        await asyncio.to_thread(shutil.rmtree, root, True)

    async def sweep_workers(self, live_owner_labels: set[str]) -> list[str]:
        removed: list[str] = []
        for root in self.root.iterdir():
            if not root.is_dir():
                continue
            marker = self._marker_path(root)
            if not marker.is_file():
                raise RuntimeError(f"unowned worker directory: {root}")
            try:
                data = json.loads(marker.read_text())
            except Exception as exc:
                raise RuntimeError(f"corrupt worker owner marker: {marker}") from exc
            label = str(data.get("owner_label"))
            if label in live_owner_labels:
                continue
            for raw in data.get("pgids", []):
                await self._kill_pgid(int(raw), None, grace_s=0.0)
            await asyncio.to_thread(shutil.rmtree, root, True)
            removed.append(label)
        return removed
