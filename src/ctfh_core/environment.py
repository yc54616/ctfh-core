from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
import pty
import shutil
import signal
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .trace import CTFH_JSONL_RECORD_VERSION
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


_CODEX_STATIC_FILES = (
    "auth.json",
    "AGENTS.md",
    "RTK.md",
)

# Native runtime state, plugins, MCP servers, hooks, browser state, memories and
# prior sessions are attempt-local or absent unless a later admitted capability
# materializes them explicitly.
_CODEX_STATIC_DIRS: tuple[str, ...] = ()


@dataclass
class _BrowserRuntime:
    process: asyncio.subprocess.Process
    ws_url: str
    home: Path
    stderr_path: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _InteractiveSession:
    session_id: str
    owner_label: str
    process: asyncio.subprocess.Process
    master_fd: int | None
    transcript_path: Path
    buffer: bytearray = field(default_factory=bytearray)
    base_offset: int = 0
    total_bytes: int = 0
    transcript_bytes: int = 0
    transcript_truncated: bool = False
    exit_code: int | None = None
    reader_task: asyncio.Task[None] | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class LocalWorkerEnvironmentProvider:
    """Attempt-private bubblewrap environment."""

    def __init__(
        self,
        root: Path,
        *,
        codex_home: Path | None = None,
        home: Path | None = None,
        max_sessions_per_attempt: int = 4,
        max_session_transcript_bytes: int = 4 * 1024 * 1024,
        max_session_write_bytes: int = 64 * 1024,
        enable_interactive_session_service: bool = False,
        enable_browser_service: bool = False,
        browser_allowed_domains: tuple[str, ...] = (),
        knowledge_snapshot_dir: Path | None = None,
    ):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.home = (Path.home() if home is None else home).resolve()
        source = Path(os.environ.get("CODEX_HOME", str(self.home / ".codex"))) if codex_home is None else codex_home
        self.codex_home = source.resolve()
        self.claude_home = (self.home / ".claude").resolve()
        self.resolver_path = Path("/etc/resolv.conf").resolve()
        self.max_sessions_per_attempt = max_sessions_per_attempt
        self.max_session_transcript_bytes = max_session_transcript_bytes
        self.max_session_write_bytes = max_session_write_bytes
        self.enable_interactive_session_service = enable_interactive_session_service
        self.enable_browser_service = enable_browser_service
        self.browser_allowed_domains = tuple(browser_allowed_domains)
        self.knowledge_snapshot_dir = (
            None
            if knowledge_snapshot_dir is None
            else knowledge_snapshot_dir.resolve()
        )
        self._live: dict[str, WorkerEnvironment] = {}
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self._sessions: dict[str, dict[str, _InteractiveSession]] = {}
        self._session_open_locks: dict[str, asyncio.Lock] = {}
        self._session_servers: dict[str, asyncio.AbstractServer] = {}
        self._session_rpc_tasks: dict[str, set[asyncio.Task[object]]] = {}
        self._browser_servers: dict[str, asyncio.AbstractServer] = {}
        self._browser_rpc_tasks: dict[str, set[asyncio.Task[object]]] = {}
        self._browser_start_locks: dict[str, asyncio.Lock] = {}
        self._browsers: dict[str, _BrowserRuntime] = {}
        self._kill_tasks: dict[str, asyncio.Task[None]] = {}

    def _env_root(self, owner: WorkerOwnerTag) -> Path:
        return self.root / owner.label().replace(":", "_")

    @staticmethod
    def _marker_path(root: Path) -> Path:
        return root / ".ctfh-owner.json"

    @staticmethod
    def _codex_runtime_home(root: Path) -> Path:
        return root / "codex-home"

    @staticmethod
    def _claude_runtime_home(root: Path) -> Path:
        return root / "claude-home"

    @staticmethod
    def _knowledge_runtime_dir(root: Path) -> Path:
        return root / "knowledge"

    @staticmethod
    def _services_root(root: Path) -> Path:
        return root / "services"

    def _session_socket_dir(self, root: Path) -> Path:
        digest = hashlib.sha256(str(root).encode()).hexdigest()[:20]
        return Path("/tmp") / f"ctfh-session-{os.getuid()}" / digest

    def _session_socket_path(self, root: Path) -> Path:
        return self._session_socket_dir(root) / "session.sock"

    def _browser_socket_dir(self, root: Path) -> Path:
        digest = hashlib.sha256(str(root).encode()).hexdigest()[:20]
        return Path("/tmp") / f"ctfh-browser-{os.getuid()}" / digest

    def _browser_socket_path(self, root: Path) -> Path:
        return self._browser_socket_dir(root) / "browser.sock"

    def _prepare_session_service_files(self, root: Path) -> None:
        if not self.enable_interactive_session_service:
            return
        services = self._services_root(root)
        bindir = services / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).with_name("session_cli.py")
        destination = bindir / "ctfh-session"
        shutil.copy2(source, destination)
        destination.chmod(0o755)

    def _prepare_browser_service_files(self, root: Path) -> None:
        if not self.enable_browser_service:
            return
        services = self._services_root(root)
        bindir = services / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).with_name("browser_cli.py")
        destination = bindir / "ctfh-browser"
        shutil.copy2(source, destination)
        destination.chmod(0o755)

    def _prepare_codex_runtime_home(self, root: Path) -> Path:
        runtime = self._codex_runtime_home(root)
        runtime.mkdir(parents=True, exist_ok=True)
        # Bubblewrap overlays the immutable user configuration onto these mount
        # points. Runtime DBs, app-server state, IPC and logs remain attempt-local
        # and writable in the surrounding directory.
        for name in _CODEX_STATIC_FILES:
            source = self.codex_home / name
            if source.is_file():
                dest = runtime / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.touch(exist_ok=True)
        for name in _CODEX_STATIC_DIRS:
            source = self.codex_home / name
            if source.is_dir():
                (runtime / name).mkdir(parents=True, exist_ok=True)
        return runtime

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

    @staticmethod
    def _remove_owned_tree_sync(root: Path) -> None:
        """Remove an attempt-owned tree without following symlinks.

        The worker may chmod files/directories arbitrarily. Normalize modes on
        the owned tree itself before rmtree so cleanup cannot silently leave a
        0555/000 subtree behind.
        """
        if not root.exists() and not root.is_symlink():
            return
        if root.is_symlink():
            root.unlink()
            return

        for dirpath, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            directory = Path(dirpath)
            try:
                directory.chmod(0o700)
            except FileNotFoundError:
                continue
            for name in filenames:
                path = directory / name
                if path.is_symlink():
                    continue
                try:
                    path.chmod(0o600)
                except FileNotFoundError:
                    pass
            for name in dirnames:
                path = directory / name
                if path.is_symlink():
                    continue
                try:
                    path.chmod(0o700)
                except FileNotFoundError:
                    pass

        shutil.rmtree(root)
        if root.exists() or root.is_symlink():
            raise RuntimeError(f"failed to remove owned worker tree: {root}")

    def _create_sync(self, task: TaskSpec, owner: WorkerOwnerTag) -> WorkerEnvironment:
        root = self._env_root(owner)
        if root.exists() or root.is_symlink():
            self._remove_owned_tree_sync(root)
        workspace = root / "workspace"
        root.mkdir(parents=True, exist_ok=True)
        self._prepare_codex_runtime_home(root)
        self._prepare_session_service_files(root)
        self._prepare_browser_service_files(root)
        env = WorkerEnvironment(owner=owner, root=root, workspace=workspace)
        self._write_marker(env, spawn_state="provisioning")
        shutil.copytree(task.public_dir, workspace)
        self._write_marker(env, spawn_state="ready")
        return env

    async def create(self, task: TaskSpec, owner: WorkerOwnerTag, cancel: CancelToken) -> WorkerEnvironment:
        if cancel.cancelled():
            raise asyncio.CancelledError

        # Provision off the event loop, but shield it so cancellation cannot leave
        # a background copy that later publishes an untracked workspace. The
        # create operation is always driven to a terminal state before cleanup.
        provision = asyncio.create_task(
            asyncio.to_thread(self._create_sync, task, owner)
        )
        try:
            env = await asyncio.shield(provision)
        except asyncio.CancelledError:
            cancel.cancel()
            try:
                await asyncio.shield(provision)
            except Exception:
                pass
            await self.destroy_cancellation_safe(owner)
            raise
        except Exception:
            await self.destroy_cancellation_safe(owner)
            raise

        if cancel.cancelled():
            await self.destroy_cancellation_safe(owner)
            raise asyncio.CancelledError
        self._live[owner.label()] = env
        try:
            if self.enable_interactive_session_service:
                await self._start_session_server(env)
            if self.enable_browser_service:
                await self._start_browser_server(env)
        except BaseException:
            await self.destroy_cancellation_safe(owner)
            raise
        return env

    def _sandbox_command(
        self,
        env: WorkerEnvironment,
        argv: list[str],
        *,
        chdir: str = "/workspace",
    ) -> list[str]:
        sandbox_home = self.home
        sandbox_codex = sandbox_home / ".codex"
        sandbox_claude = sandbox_home / ".claude"
        runtime_codex = self._codex_runtime_home(env.root)
        runtime_claude = self._claude_runtime_home(env.root)
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
        ]
        if self.resolver_path.is_file() and not self.resolver_path.is_relative_to("/etc"):
            parent = self.resolver_path.parent
            current = Path("/")
            for part in parent.parts[1:]:
                current /= part
                cmd += ["--dir", str(current)]
            cmd += ["--ro-bind", str(self.resolver_path), str(self.resolver_path)]
        cmd += [
            "--dir", "/home",
            "--dir", str(sandbox_home),
            "--bind", str(runtime_codex), str(sandbox_codex),
        ]
        if runtime_claude.is_dir():
            cmd += ["--bind", str(runtime_claude), str(sandbox_claude)]
            credentials = self.claude_home / ".credentials.json"
            if credentials.is_file():
                cmd += [
                    "--ro-bind",
                    str(credentials),
                    str(sandbox_claude / ".credentials.json"),
                ]
        materialized_config = runtime_codex / "config.toml"
        if materialized_config.is_file():
            cmd += [
                "--ro-bind",
                str(materialized_config),
                str(sandbox_codex / "config.toml"),
            ]
        materialized_skills = runtime_codex / "skills"
        if materialized_skills.is_dir():
            cmd += [
                "--ro-bind",
                str(materialized_skills),
                str(sandbox_codex / "skills"),
            ]
        knowledge_dir = self._knowledge_runtime_dir(env.root)
        if knowledge_dir.is_dir():
            cmd += [
                "--ro-bind",
                str(knowledge_dir),
                "/ctfh-knowledge",
            ]
        services_root = self._services_root(env.root)
        if (
            self.enable_interactive_session_service or self.enable_browser_service
        ) and services_root.is_dir():
            cmd += ["--ro-bind", str(services_root), "/ctfh-services"]
        if self.enable_interactive_session_service:
            socket_dir = self._session_socket_dir(env.root)
            if socket_dir.is_dir():
                cmd += ["--ro-bind", str(socket_dir), "/ctfh-session-rpc"]
        if self.enable_browser_service:
            browser_socket_dir = self._browser_socket_dir(env.root)
            if browser_socket_dir.is_dir():
                cmd += [
                    "--ro-bind",
                    str(browser_socket_dir),
                    "/ctfh-browser-rpc",
                ]
        for name in _CODEX_STATIC_FILES:
            source = self.codex_home / name
            if source.is_file():
                cmd += ["--ro-bind", str(source), str(sandbox_codex / name)]
        for name in _CODEX_STATIC_DIRS:
            source = self.codex_home / name
            if source.is_dir():
                cmd += ["--ro-bind", str(source), str(sandbox_codex / name)]
        for relative in (".nvm", ".local"):
            source = self.home / relative
            if source.exists():
                cmd += ["--ro-bind", str(source), str(sandbox_home / relative)]
        cmd += [
            "--bind", str(env.workspace), "/workspace",
            "--chdir", chdir,
            "--clearenv",
            "--setenv", "HOME", str(sandbox_home),
            "--setenv", "CODEX_HOME", str(sandbox_codex),
            "--setenv", "PATH", (
                "/ctfh-services/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
                if (
                    self.enable_interactive_session_service
                    or self.enable_browser_service
                )
                else os.environ.get("PATH", "/usr/bin:/bin")
            ),
            "--setenv", "USER", os.environ.get("USER", self.home.name),
            "--setenv", "LOGNAME", os.environ.get("LOGNAME", self.home.name),
            "--setenv", "LANG", os.environ.get("LANG", "C.UTF-8"),
        ]
        if runtime_claude.is_dir():
            cmd += ["--setenv", "CLAUDE_CONFIG_DIR", str(sandbox_claude)]
        if knowledge_dir.is_dir():
            cmd += ["--setenv", "CTFH_KNOWLEDGE_DIR", "/ctfh-knowledge"]
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"):
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

    async def _start_browser_server(self, env: WorkerEnvironment) -> None:
        label = env.owner.label()
        socket_dir = self._browser_socket_dir(env.root)
        socket_dir.mkdir(parents=True, exist_ok=True)
        socket_dir.chmod(0o700)
        socket_path = self._browser_socket_path(env.root)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(
            lambda reader, writer: self._handle_browser_rpc(env, reader, writer),
            path=str(socket_path),
        )
        socket_path.chmod(0o600)
        self._browser_servers[label] = server
        self._browser_rpc_tasks.setdefault(label, set())

    async def _stop_browser_server(
        self, env: WorkerEnvironment
    ) -> asyncio.AbstractServer | None:
        label = env.owner.label()
        server = self._browser_servers.pop(label, None)
        if server is not None:
            server.close()
        socket_dir = self._browser_socket_dir(env.root)
        await asyncio.to_thread(shutil.rmtree, socket_dir, True)
        return server

    async def _settle_browser_rpc_tasks(self, env: WorkerEnvironment) -> None:
        label = env.owner.label()
        current = asyncio.current_task()
        pending = [
            task
            for task in self._browser_rpc_tasks.get(label, set())
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._browser_rpc_tasks.pop(label, None)

    def _browser_process_env(self, home: Path) -> dict[str, str]:
        child_env = os.environ.copy()
        config = home / ".config"
        cache = home / ".cache"
        config.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        child_env.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(config),
                "XDG_CACHE_HOME": str(cache),
            }
        )
        child_env.pop("GHOSTCHROME_SESSION", None)
        child_env.pop("PLAYWRIGHT_CLI_SESSION", None)
        return child_env

    def _ghostchrome_common_args(self) -> list[str]:
        found = shutil.which("ghostchrome")
        if found is None:
            raise FileNotFoundError("ghostchrome")
        argv = [
            found,
            "--headless",
            "--profile",
            "agent",
            "--timeout",
            "20",
            "--output-max-size",
            str(1024 * 1024),
        ]
        if self.browser_allowed_domains:
            argv += ["--allowed-domains", ",".join(self.browser_allowed_domains)]
        return argv

    async def _create_process_with_cancellation_barrier(
        self,
        *argv: str,
        cwd: str,
        env: dict[str, str],
        stdin=None,
        stdout=None,
        stderr=None,
        cleanup_grace_s: float = 0.2,
    ) -> asyncio.subprocess.Process:
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env=env,
            )
        )
        try:
            return await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            proc: asyncio.subprocess.Process | None = None
            try:
                proc = await asyncio.shield(spawn_task)
            except BaseException:
                # Outer cancellation wins, but the shielded spawn task has
                # reached a terminal state so it cannot publish a late child.
                pass
            if proc is not None:
                await self._kill_pgid(
                    proc.pid,
                    proc,
                    grace_s=cleanup_grace_s,
                )
            raise

    async def _ensure_browser_runtime(
        self, env: WorkerEnvironment
    ) -> _BrowserRuntime:
        label = env.owner.label()
        lock = self._browser_start_locks.setdefault(
            label,
            asyncio.Lock(),
        )
        async with lock:
            return await self._ensure_browser_runtime_locked(env)

    async def _ensure_browser_runtime_locked(
        self, env: WorkerEnvironment
    ) -> _BrowserRuntime:
        label = env.owner.label()
        current = self._browsers.get(label)
        if current is not None and current.process.returncode is None:
            return current
        if current is not None:
            self._procs.pop(current.process.pid, None)
            env.processes.discard(current.process.pid)
            self._browsers.pop(label, None)

        browser_root = env.root / "browser"
        browser_root.mkdir(parents=True, exist_ok=True)
        home = browser_root / "home"
        home.mkdir(parents=True, exist_ok=True)
        stderr_path = browser_root / "serve.stderr.log"
        process_env = self._browser_process_env(home)
        argv = self._ghostchrome_common_args() + ["serve", "--port", "0"]

        self._write_marker(env, spawn_state="spawning-browser")
        with stderr_path.open("ab") as stderr:
            proc = await self._create_process_with_cancellation_barrier(
                *argv,
                cwd=str(browser_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                env=process_env,
                cleanup_grace_s=0.2,
            )
        env.processes.add(proc.pid)
        self._procs[proc.pid] = proc
        self._write_marker(env, spawn_state="starting-browser")

        try:
            if proc.stdout is None:
                raise RuntimeError("ghostchrome serve stdout is unavailable")
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
            ws_url = line.decode(errors="replace").strip()
            if not ws_url.startswith("ws://"):
                raise RuntimeError(
                    "ghostchrome serve did not return a WebSocket endpoint"
                )
        except BaseException as exc:
            await self._kill_pgid(proc.pid, proc, grace_s=0.2)
            self._procs.pop(proc.pid, None)
            env.processes.discard(proc.pid)
            self._write_marker(env, spawn_state="ready")
            if isinstance(exc, asyncio.CancelledError):
                raise
            detail = stderr_path.read_text(errors="replace")[-2000:]
            raise RuntimeError(
                f"ghostchrome serve startup failed: {detail}"
            ) from exc

        runtime = _BrowserRuntime(
            process=proc,
            ws_url=ws_url,
            home=home,
            stderr_path=stderr_path,
        )
        self._browsers[label] = runtime
        self._write_marker(env, spawn_state="running-browser")
        return runtime

    async def _stop_browser_runtime(
        self, env: WorkerEnvironment, *, grace_s: float = 0.5
    ) -> None:
        runtime = self._browsers.pop(env.owner.label(), None)
        if runtime is None:
            return
        await self._kill_pgid(
            runtime.process.pid,
            runtime.process,
            grace_s=grace_s,
        )
        self._procs.pop(runtime.process.pid, None)
        env.processes.discard(runtime.process.pid)
        if env.root.exists():
            self._write_marker(
                env,
                spawn_state="running" if env.processes else "ready",
            )

    @staticmethod
    async def _read_limited_stream(
        stream: asyncio.StreamReader | None,
        *,
        limit: int,
        label: str,
    ) -> bytes:
        if stream is None:
            return b""
        data = bytearray()
        while True:
            remaining = limit - len(data)
            read_size = min(65536, remaining + 1)
            chunk = await stream.read(read_size)
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > limit:
                raise RuntimeError(
                    f"{label} exceeded configured limit of {limit} bytes"
                )

    @staticmethod
    def _browser_trace_args(op: str, args: dict[str, object]) -> dict[str, object]:
        safe = dict(args)
        if op == "type" and "text" in safe:
            text = str(safe["text"])
            safe["text"] = f"<redacted:{len(text)} chars>"
        if op == "navigate" and "url" in safe:
            parsed = urlsplit(str(safe["url"]))
            safe["url"] = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            )
        if op == "eval" and "expr" in safe:
            expr = str(safe["expr"])
            safe["expr"] = {
                "sha256": hashlib.sha256(expr.encode()).hexdigest(),
                "length": len(expr),
            }
        return safe

    @staticmethod
    def _validate_browser_ref(value: object) -> str:
        ref = str(value)
        if not ref.startswith("@") or not ref[1:].isdigit():
            raise ValueError("browser ref must look like @12")
        return ref

    def _browser_controller_args(
        self,
        runtime: _BrowserRuntime,
        op: str,
        args: dict[str, object],
    ) -> list[str]:
        argv = self._ghostchrome_common_args()
        argv += ["--connect", runtime.ws_url, "--format", "json"]

        if op == "navigate":
            url = str(args.get("url", ""))
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https", "about", "data"}:
                raise ValueError("unsupported browser URL scheme")
            wait = str(args.get("wait", "load"))
            if wait not in {"load", "stable", "idle", "none"}:
                raise ValueError("invalid browser wait strategy")
            return argv + ["navigate", url, "--wait", wait]

        if op == "extract":
            level = str(args.get("level", "content"))
            if level not in {"skeleton", "content", "full"}:
                raise ValueError("invalid extraction level")
            command = argv + ["extract", "--level", level, "--diff", "off"]
            selector = args.get("selector")
            if selector:
                command += ["--selector", str(selector)]
            if bool(args.get("content_boundary", False)):
                command += ["--content-boundary"]
            return command

        if op == "click":
            ref = self._validate_browser_ref(args.get("ref"))
            snapshot = str(args.get("snapshot", "diff"))
            if snapshot not in {"none", "diff", "full"}:
                raise ValueError("invalid click snapshot mode")
            return argv + ["click", ref, "--snapshot", snapshot]

        if op == "type":
            ref = self._validate_browser_ref(args.get("ref"))
            text = str(args.get("text", ""))
            snapshot = str(args.get("snapshot", "diff"))
            if snapshot not in {"none", "diff", "full"}:
                raise ValueError("invalid type snapshot mode")
            command = argv + ["type", ref, text, "--snapshot", snapshot]
            if bool(args.get("submit", False)):
                command += ["--submit"]
            return command

        if op == "eval":
            expr = str(args.get("expr", ""))
            if not expr:
                raise ValueError("eval expression is required")
            return argv + ["eval", expr]

        if op == "url":
            return argv + ["eval", "location.href"]

        if op in {"back", "forward", "errors", "tabs"}:
            return argv + [op]

        raise ValueError(f"unsupported browser operation: {op}")

    async def browser_call(
        self,
        env: WorkerEnvironment,
        op: str,
        args: dict[str, object] | None = None,
    ) -> dict[str, object]:
        args = {} if args is None else dict(args)
        if op == "close":
            await self._stop_browser_runtime(env)
            return {"operation": op, "closed": True}

        runtime = await self._ensure_browser_runtime(env)
        if op == "init":
            return {
                "operation": op,
                "ready": True,
                "backend": "ghostchrome-serve-v0.7.1",
            }

        async with runtime.lock:
            argv = self._browser_controller_args(runtime, op, args)
            browser_root = env.root / "browser"
            process_env = self._browser_process_env(runtime.home)
            started = time.monotonic()
            self._write_marker(env, spawn_state="browser-call")
            proc = await self._create_process_with_cancellation_barrier(
                *argv,
                cwd=str(browser_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=process_env,
                cleanup_grace_s=0.1,
            )
            env.processes.add(proc.pid)
            self._procs[proc.pid] = proc
            self._write_marker(env, spawn_state="browser-call-running")
            stdout_task = asyncio.create_task(
                self._read_limited_stream(
                    proc.stdout,
                    limit=1024 * 1024,
                    label="browser stdout",
                )
            )
            stderr_task = asyncio.create_task(
                self._read_limited_stream(
                    proc.stderr,
                    limit=256 * 1024,
                    label="browser stderr",
                )
            )
            wait_task = asyncio.create_task(proc.wait())
            io_tasks = (stdout_task, stderr_task, wait_task)
            try:
                stdout, stderr, _ = await asyncio.wait_for(
                    asyncio.gather(*io_tasks),
                    timeout=30.0,
                )
            except asyncio.CancelledError:
                await self._kill_pgid(proc.pid, proc, grace_s=0.1)
                for task in io_tasks:
                    task.cancel()
                await asyncio.gather(*io_tasks, return_exceptions=True)
                raise
            except TimeoutError:
                await self._kill_pgid(proc.pid, proc, grace_s=0.1)
                for task in io_tasks:
                    task.cancel()
                await asyncio.gather(*io_tasks, return_exceptions=True)
                raise RuntimeError(f"browser operation timed out: {op}")
            except Exception:
                await self._kill_pgid(proc.pid, proc, grace_s=0.1)
                for task in io_tasks:
                    task.cancel()
                await asyncio.gather(*io_tasks, return_exceptions=True)
                raise
            finally:
                self._procs.pop(proc.pid, None)
                env.processes.discard(proc.pid)
                if env.root.exists():
                    self._write_marker(env, spawn_state="running-browser")

            output_text = stdout.decode(errors="replace").strip()
            error_text = stderr.decode(errors="replace").strip()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ghostchrome {op} failed rc={proc.returncode}: "
                    f"{error_text[-1000:]} {output_text[-1000:]}"
                )
            if len(stdout) > 1024 * 1024:
                raise RuntimeError("browser output exceeded 1 MiB limit")
            try:
                result: object = json.loads(output_text) if output_text else None
            except json.JSONDecodeError:
                result = output_text

            duration_s = time.monotonic() - started
            trace_path = browser_root / "trace.jsonl"
            record = {
                "v": CTFH_JSONL_RECORD_VERSION,
                "kind": "browser.operation",
                "op": op,
                "args": self._browser_trace_args(op, args),
                "duration_s": duration_s,
                "result_bytes": len(stdout),
                "result_sha256": hashlib.sha256(stdout).hexdigest(),
            }
            with trace_path.open("a", encoding="utf-8") as trace:
                trace.write(json.dumps(record, sort_keys=True) + "\n")
            return {
                "operation": op,
                "duration_s": duration_s,
                "result": result,
            }

    @staticmethod
    def _abort_rpc_writer(writer: asyncio.StreamWriter) -> None:
        transport = getattr(writer, "transport", None)
        abort = getattr(transport, "abort", None)
        if callable(abort):
            abort()
            return
        writer.close()

    @staticmethod
    async def _finish_rpc_writer(
        writer: asyncio.StreamWriter,
        response: dict[str, object] | None,
    ) -> None:
        cancelled = False
        force_abort = False
        if response is not None:
            try:
                writer.write(
                    (json.dumps(response, sort_keys=True) + "\n").encode()
                )
                await asyncio.wait_for(writer.drain(), timeout=0.25)
            except asyncio.CancelledError:
                cancelled = True
                force_abort = True
            except (
                asyncio.TimeoutError,
                BrokenPipeError,
                ConnectionError,
                OSError,
            ):
                force_abort = True

        if force_abort:
            LocalWorkerEnvironmentProvider._abort_rpc_writer(writer)
        else:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.25)
            except asyncio.CancelledError:
                # A task cancelled while close() is flushing may otherwise
                # leave the transport half-closed until GC. Abort immediately
                # so RPC teardown cannot escape the environment kill barrier.
                LocalWorkerEnvironmentProvider._abort_rpc_writer(writer)
                raise
            except asyncio.TimeoutError:
                # close() may retain buffered bytes waiting for a peer that
                # never reads. Abort the transport so server.wait_closed()
                # cannot hold the environment kill barrier indefinitely.
                LocalWorkerEnvironmentProvider._abort_rpc_writer(writer)
            except (
                BrokenPipeError,
                ConnectionError,
                OSError,
            ):
                pass

        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _wait_rpc_server_closed(
        server: asyncio.AbstractServer | None,
        *,
        label: str,
    ) -> None:
        if server is None:
            return
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=1.0)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"{label} RPC listener did not close after handlers stopped"
            ) from exc

    async def _handle_browser_rpc(
        self,
        env: WorkerEnvironment,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        label = env.owner.label()
        task = asyncio.current_task()
        if task is not None:
            self._browser_rpc_tasks.setdefault(label, set()).add(task)
        response: dict[str, object] | None = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw or len(raw) > 1024 * 1024:
                raise ValueError("invalid browser RPC request")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("browser RPC request must be an object")
            op = str(request.get("op", ""))
            if op not in {
                "init",
                "navigate",
                "extract",
                "click",
                "type",
                "eval",
                "url",
                "back",
                "forward",
                "errors",
                "tabs",
                "close",
            }:
                raise ValueError(f"unsupported browser RPC op: {op}")
            args = request.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("browser RPC args must be an object")
            value = await self.browser_call(env, op, args)
            response = {"ok": True, **value}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            response = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            await self._finish_rpc_writer(writer, response)
            if task is not None:
                self._browser_rpc_tasks.get(label, set()).discard(task)

    async def _start_session_server(self, env: WorkerEnvironment) -> None:
        label = env.owner.label()
        socket_dir = self._session_socket_dir(env.root)
        socket_dir.mkdir(parents=True, exist_ok=True)
        socket_dir.chmod(0o700)
        socket_path = self._session_socket_path(env.root)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(
            lambda reader, writer: self._handle_session_rpc(env, reader, writer),
            path=str(socket_path),
        )
        socket_path.chmod(0o600)
        self._session_servers[label] = server
        self._session_rpc_tasks.setdefault(label, set())

    async def _stop_session_server(
        self, env: WorkerEnvironment
    ) -> asyncio.AbstractServer | None:
        label = env.owner.label()
        server = self._session_servers.pop(label, None)
        if server is not None:
            server.close()
        socket_dir = self._session_socket_dir(env.root)
        await asyncio.to_thread(shutil.rmtree, socket_dir, True)
        return server

    async def _settle_session_rpc_tasks(
        self,
        env: WorkerEnvironment,
    ) -> None:
        label = env.owner.label()
        current = asyncio.current_task()
        pending = [
            task
            for task in self._session_rpc_tasks.get(label, set())
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._session_rpc_tasks.pop(label, None)

    @staticmethod
    def _jsonable_session_result(value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return {
                "base64": base64.b64encode(value).decode(),
                "text": value.decode(errors="replace"),
            }
        if isinstance(value, dict):
            return {
                str(k): LocalWorkerEnvironmentProvider._jsonable_session_result(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                LocalWorkerEnvironmentProvider._jsonable_session_result(v)
                for v in value
            ]
        return value

    async def _handle_session_rpc(
        self,
        env: WorkerEnvironment,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        label = env.owner.label()
        task = asyncio.current_task()
        if task is not None:
            self._session_rpc_tasks.setdefault(label, set()).add(task)
        response: dict[str, object] | None = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw or len(raw) > 1024 * 1024:
                raise ValueError("invalid session RPC request")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("session RPC request must be an object")
            op = str(request.get("op", ""))
            if op == "open":
                argv = request.get("argv")
                if not isinstance(argv, list) or not all(
                    isinstance(v, str) for v in argv
                ):
                    raise ValueError("open.argv must be a string list")
                value: object = {
                    "session_id": await self.session_open(
                        env, argv, cwd=request.get("cwd")
                    )
                }
            elif op == "send":
                value = {
                    "written": await self.session_send(
                        env,
                        str(request["session_id"]),
                        str(request.get("data", "")),
                    )
                }
            elif op == "read":
                wait_ms = min(int(request.get("wait_ms", 0)), 5000)
                value = await self.session_read(
                    env,
                    str(request["session_id"]),
                    since_offset=int(request.get("since_offset", 0)),
                    max_bytes=min(
                        int(request.get("max_bytes", 65536)), 1024 * 1024
                    ),
                    wait_ms=wait_ms,
                    until_quiescent=bool(
                        request.get("until_quiescent", False)
                    ),
                )
            elif op == "signal":
                await self.session_signal(
                    env,
                    str(request["session_id"]),
                    int(request["signal"]),
                )
                value = {}
            elif op == "close":
                value = {
                    "exit_code": await self.session_close(
                        env, str(request["session_id"])
                    )
                }
            elif op == "list":
                value = {"sessions": self.session_list(env)}
            else:
                raise ValueError(f"unknown session RPC op: {op}")
            payload = self._jsonable_session_result(value)
            assert isinstance(payload, dict)
            response = {"ok": True, **payload}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            response = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            await self._finish_rpc_writer(writer, response)
            if task is not None:
                self._session_rpc_tasks.get(label, set()).discard(task)

    def _session_table(self, env: WorkerEnvironment) -> dict[str, _InteractiveSession]:
        return self._sessions.setdefault(env.owner.label(), {})

    @staticmethod
    def _sandbox_cwd(env: WorkerEnvironment, cwd: str | None) -> str:
        if cwd is None or cwd in {"", ".", "/workspace"}:
            return "/workspace"
        raw = Path(cwd)
        if raw.is_absolute():
            try:
                rel = raw.relative_to("/workspace")
            except ValueError as exc:
                raise ValueError("session cwd must stay under /workspace") from exc
        else:
            rel = raw
        host = (env.workspace / rel).resolve()
        try:
            relative = host.relative_to(env.workspace.resolve())
        except ValueError as exc:
            raise ValueError("session cwd escapes workspace") from exc
        if not host.is_dir():
            raise FileNotFoundError(host)
        return "/workspace" if str(relative) == "." else "/workspace/" + relative.as_posix()

    async def _pty_read_once(self, fd: int) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()

        def readable() -> None:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno == errno.EIO:
                    data = b""
                else:
                    if not future.done():
                        future.set_exception(exc)
                    loop.remove_reader(fd)
                    return
            if not future.done():
                future.set_result(data)
            loop.remove_reader(fd)

        loop.add_reader(fd, readable)
        try:
            return await future
        finally:
            loop.remove_reader(fd)

    @staticmethod
    def _close_session_master(session: _InteractiveSession) -> None:
        fd = session.master_fd
        if fd is None:
            return
        session.master_fd = None
        try:
            os.close(fd)
        except OSError:
            pass

    async def _session_reader(self, session: _InteractiveSession) -> None:
        fd = session.master_fd
        if fd is None:
            session.exit_code = session.process.returncode
            return
        try:
            while True:
                chunk = await self._pty_read_once(fd)
                if not chunk:
                    break
                session.transcript_path.parent.mkdir(parents=True, exist_ok=True)
                remaining = max(
                    0,
                    self.max_session_transcript_bytes
                    - session.transcript_bytes,
                )
                if remaining:
                    captured = chunk[:remaining]
                    with session.transcript_path.open("ab") as transcript:
                        transcript.write(captured)
                    session.transcript_bytes += len(captured)
                if len(chunk) > remaining:
                    session.transcript_truncated = True
                async with session.changed:
                    session.total_bytes += len(chunk)
                    session.buffer.extend(chunk)
                    overflow = len(session.buffer) - self.max_session_transcript_bytes
                    if overflow > 0:
                        del session.buffer[:overflow]
                        session.base_offset += overflow
                    session.changed.notify_all()
        finally:
            try:
                session.exit_code = await session.process.wait()
            except Exception:
                session.exit_code = session.process.returncode
            self._close_session_master(session)
            async with session.changed:
                session.changed.notify_all()

    async def session_open(
        self,
        env: WorkerEnvironment,
        argv: list[str],
        *,
        cwd: str | None = None,
    ) -> str:
        if not argv:
            raise ValueError("session argv must not be empty")
        label = env.owner.label()
        lock = self._session_open_locks.setdefault(
            label,
            asyncio.Lock(),
        )
        async with lock:
            return await self._session_open_locked(
                env,
                argv,
                cwd=cwd,
            )

    async def _session_open_locked(
        self,
        env: WorkerEnvironment,
        argv: list[str],
        *,
        cwd: str | None = None,
    ) -> str:
        sessions = self._session_table(env)
        # This is an attempt-lifetime limit, not merely a concurrent-live limit.
        # Together with the per-session transcript cap it bounds retained PTY
        # memory/disk by max_sessions_per_attempt * max_session_transcript_bytes.
        if len(sessions) >= self.max_sessions_per_attempt:
            raise RuntimeError("interactive session limit reached")

        sandbox_cwd = self._sandbox_cwd(env, cwd)
        session_id = "session-" + uuid.uuid4().hex[:12]
        transcript = env.root / "sessions" / f"{session_id}.log"
        master_fd, slave_fd = pty.openpty()
        os.set_blocking(master_fd, False)
        master_owned = True

        def close_master_if_owned() -> None:
            nonlocal master_owned
            if not master_owned:
                return
            master_owned = False
            try:
                os.close(master_fd)
            except OSError:
                pass

        def close_slave() -> None:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        self._write_marker(env, spawn_state="spawning-session")
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *self._sandbox_command(env, argv, chdir=sandbox_cwd),
                cwd="/",
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        )
        proc: asyncio.subprocess.Process | None = None
        try:
            try:
                proc = await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                # Creation is a cancellation barrier: find out whether a child
                # was created, then reap it before propagating cancellation.
                try:
                    proc = await asyncio.shield(spawn_task)
                except BaseException:
                    proc = None
                if proc is not None:
                    await self._kill_pgid(
                        proc.pid,
                        proc,
                        grace_s=0.1,
                    )
                close_master_if_owned()
                raise
            except BaseException:
                close_master_if_owned()
                raise
        finally:
            close_slave()

        assert proc is not None
        env.processes.add(proc.pid)
        self._procs[proc.pid] = proc
        session = _InteractiveSession(
            session_id=session_id,
            owner_label=env.owner.label(),
            process=proc,
            master_fd=master_fd,
            transcript_path=transcript,
        )
        master_owned = False
        sessions[session_id] = session
        session.reader_task = asyncio.create_task(
            self._session_reader(session)
        )
        self._write_marker(env, spawn_state="running")
        return session_id

    def _get_session(self, env: WorkerEnvironment, session_id: str) -> _InteractiveSession:
        try:
            return self._session_table(env)[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc

    async def session_send(
        self,
        env: WorkerEnvironment,
        session_id: str,
        data: str | bytes,
    ) -> int:
        session = self._get_session(env, session_id)
        if session.process.returncode is not None or session.exit_code is not None:
            raise RuntimeError("session is not running")
        fd = session.master_fd
        if fd is None:
            raise RuntimeError("session PTY is closed")
        payload = data.encode() if isinstance(data, str) else data
        if len(payload) > self.max_session_write_bytes:
            raise ValueError("session write exceeds configured limit")
        total = 0
        while total < len(payload):
            try:
                written = os.write(fd, payload[total:])
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            total += written
        return total

    async def session_read(
        self,
        env: WorkerEnvironment,
        session_id: str,
        *,
        since_offset: int = 0,
        max_bytes: int = 65536,
        wait_ms: int = 0,
        until_quiescent: bool = False,
    ) -> dict[str, object]:
        if since_offset < 0 or max_bytes <= 0 or wait_ms < 0:
            raise ValueError("invalid session read bounds")
        session = self._get_session(env, session_id)
        deadline = asyncio.get_running_loop().time() + (wait_ms / 1000.0) if wait_ms else None

        async def has_new_data() -> bool:
            return session.total_bytes > since_offset or session.exit_code is not None

        if not await has_new_data() and wait_ms:
            async with session.changed:
                try:
                    await asyncio.wait_for(session.changed.wait(), timeout=wait_ms / 1000.0)
                except TimeoutError:
                    pass

        if until_quiescent and session.exit_code is None:
            quiet_s = 0.1
            hard_deadline = deadline or (asyncio.get_running_loop().time() + 1.0)
            last_total = session.total_bytes
            while asyncio.get_running_loop().time() < hard_deadline:
                remaining = hard_deadline - asyncio.get_running_loop().time()
                async with session.changed:
                    try:
                        await asyncio.wait_for(session.changed.wait(), timeout=min(quiet_s, remaining))
                    except TimeoutError:
                        if session.total_bytes == last_total:
                            break
                    if session.exit_code is not None:
                        break
                    if session.total_bytes == last_total:
                        break
                    last_total = session.total_bytes

        start = max(since_offset, session.base_offset)
        end_available = session.total_bytes
        count = min(max_bytes, max(0, end_available - start))
        buffer_start = start - session.base_offset
        data = bytes(session.buffer[buffer_start:buffer_start + count])
        return {
            "session_id": session_id,
            "data": data,
            "start_offset": start,
            "next_offset": start + len(data),
            "total_bytes": session.total_bytes,
            "truncated": since_offset < session.base_offset,
            "eof": session.exit_code is not None and start + len(data) >= session.total_bytes,
            "exit_code": session.exit_code,
            "transcript_path": session.transcript_path,
            "transcript_bytes": session.transcript_bytes,
            "transcript_truncated": session.transcript_truncated,
        }

    async def session_signal(
        self,
        env: WorkerEnvironment,
        session_id: str,
        sig: int,
    ) -> None:
        session = self._get_session(env, session_id)
        if session.exit_code is not None:
            return
        os.killpg(session.process.pid, sig)

    async def session_close(
        self,
        env: WorkerEnvironment,
        session_id: str,
        *,
        grace_s: float = 0.5,
    ) -> int | None:
        session = self._get_session(env, session_id)
        if session.process.returncode is None:
            await self._kill_pgid(
                session.process.pid,
                session.process,
                grace_s=grace_s,
            )
        if session.reader_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(session.reader_task),
                    timeout=1.0,
                )
            except (TimeoutError, asyncio.CancelledError):
                session.reader_task.cancel()
                try:
                    await session.reader_task
                except asyncio.CancelledError:
                    pass
        self._close_session_master(session)
        session.exit_code = session.process.returncode
        self._procs.pop(session.process.pid, None)
        env.processes.discard(session.process.pid)
        self._write_marker(env, spawn_state="running" if env.processes else "ready")
        return session.exit_code

    def session_list(self, env: WorkerEnvironment) -> list[dict[str, object]]:
        return [
            {
                "session_id": s.session_id,
                "running": s.exit_code is None and s.process.returncode is None,
                "exit_code": s.exit_code if s.exit_code is not None else s.process.returncode,
                "total_bytes": s.total_bytes,
                "transcript_path": s.transcript_path,
                "transcript_bytes": s.transcript_bytes,
                "transcript_truncated": s.transcript_truncated,
            }
            for s in self._session_table(env).values()
        ]

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

    async def kill(
        self,
        env: WorkerEnvironment,
        *,
        grace_s: float = 1.0,
    ) -> None:
        label = env.owner.label()
        teardown = self._kill_tasks.get(label)
        if teardown is None or teardown.done():
            teardown = asyncio.create_task(
                self._kill_once(env, grace_s=grace_s)
            )
            self._kill_tasks[label] = teardown

        was_cancelled = False
        while not teardown.done():
            try:
                await asyncio.shield(teardown)
            except asyncio.CancelledError:
                # Teardown is a cancellation barrier: caller cancellation is
                # deferred until the shared owner cleanup reaches terminal.
                was_cancelled = True
                continue

        # Propagate cleanup failure before the deferred caller cancellation.
        teardown.result()
        if was_cancelled:
            raise asyncio.CancelledError

    async def _kill_once(
        self,
        env: WorkerEnvironment,
        *,
        grace_s: float = 1.0,
    ) -> None:
        browser_server: asyncio.AbstractServer | None = None
        session_server: asyncio.AbstractServer | None = None
        if self.enable_browser_service:
            browser_server = await self._stop_browser_server(env)
        if self.enable_interactive_session_service:
            session_server = await self._stop_session_server(env)

        # Close listeners first, then cancel/settle all accepted handlers.
        # Only after handlers can no longer create resources do we await the
        # listener close barrier and snapshot owned process groups.
        if self.enable_interactive_session_service:
            await self._settle_session_rpc_tasks(env)
        if self.enable_browser_service:
            await self._settle_browser_rpc_tasks(env)
        listener_errors: list[Exception] = []
        for server, label in (
            (session_server, "session"),
            (browser_server, "browser"),
        ):
            try:
                await self._wait_rpc_server_closed(server, label=label)
            except Exception as exc:
                # Listener shutdown must never prevent owned process cleanup.
                # Report the listener failure only after the kill domain is
                # confirmed empty.
                listener_errors.append(exc)

        pgids = list(env.processes)
        for pgid in pgids:
            await self._kill_pgid(pgid, self._procs.get(pgid), grace_s=grace_s)
        for session in list(self._session_table(env).values()):
            if session.reader_task is not None and not session.reader_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(session.reader_task),
                        timeout=1.0,
                    )
                except (TimeoutError, asyncio.CancelledError):
                    session.reader_task.cancel()
                    try:
                        await session.reader_task
                    except asyncio.CancelledError:
                        pass
            self._close_session_master(session)
            session.exit_code = session.process.returncode
        for pgid in pgids:
            self._procs.pop(pgid, None)
            env.processes.discard(pgid)
        if self.enable_browser_service:
            self._browsers.pop(env.owner.label(), None)
        if env.root.exists():
            self._write_marker(env, spawn_state="stopped")
        if listener_errors:
            raise RuntimeError(
                "RPC listener shutdown failed after process cleanup"
            ) from listener_errors[0]

    def _publish_service_traces_sync(
        self,
        env: WorkerEnvironment,
        attempt_output: Path,
    ) -> list[Path]:
        published: list[Path] = []
        services_out = attempt_output / "services"

        sessions = env.root / "sessions"
        if sessions.is_dir():
            destination = services_out / "sessions"
            destination.mkdir(parents=True, exist_ok=True)
            for source in sorted(sessions.glob("*.log")):
                if not source.is_file():
                    continue
                target = destination / source.name
                shutil.copy2(source, target)
                published.append(target)

            session_metadata = {
                "schema": "ctfh.session-trace-metadata.v1",
                "max_transcript_bytes": self.max_session_transcript_bytes,
                "sessions": [
                    {
                        "session_id": session.session_id,
                        "total_bytes": session.total_bytes,
                        "transcript_bytes": session.transcript_bytes,
                        "transcript_truncated": session.transcript_truncated,
                    }
                    for session in sorted(
                        self._session_table(env).values(),
                        key=lambda item: item.session_id,
                    )
                ],
            }
            metadata_path = destination / "metadata.json"
            _atomic_json(metadata_path, session_metadata)
            metadata_path.chmod(0o444)
            published.append(metadata_path)

        browser = env.root / "browser"
        if browser.is_dir():
            destination = services_out / "browser"
            destination.mkdir(parents=True, exist_ok=True)
            for name in ("trace.jsonl", "serve.stderr.log"):
                source = browser / name
                if not source.is_file():
                    continue
                target = destination / name
                shutil.copy2(source, target)
                published.append(target)

        return published

    async def publish_service_traces(
        self,
        env: WorkerEnvironment,
        attempt_output: Path,
    ) -> list[Path]:
        return await asyncio.to_thread(
            self._publish_service_traces_sync,
            env,
            attempt_output,
        )

    async def destroy_cancellation_safe(
        self,
        owner: WorkerOwnerTag,
    ) -> None:
        destroy_task = asyncio.create_task(self.destroy(owner))
        try:
            await asyncio.shield(destroy_task)
        except asyncio.CancelledError:
            await asyncio.shield(destroy_task)
            raise

    async def destroy(self, owner: WorkerOwnerTag) -> None:
        label = owner.label()
        env = self._live.pop(label, None)
        if env is not None:
            await self.kill(env)
        self._sessions.pop(label, None)
        self._session_open_locks.pop(label, None)
        self._browsers.pop(label, None)
        self._browser_rpc_tasks.pop(label, None)
        self._browser_start_locks.pop(label, None)
        root = self._env_root(owner)
        await asyncio.to_thread(self._remove_owned_tree_sync, root)

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
            await asyncio.to_thread(
                shutil.rmtree, self._session_socket_dir(root), True
            )
            await asyncio.to_thread(
                shutil.rmtree, self._browser_socket_dir(root), True
            )
            await asyncio.to_thread(
                self._remove_owned_tree_sync,
                root,
            )
            removed.append(label)
        return removed
