from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.agent import ClaudeCLIAdapter, CodexCLIAdapter
from ctfh_core.benchmark import run_direct
from ctfh_core.digest import tree_snapshot_digest
from ctfh_core.environment import CancelToken, LocalWorkerEnvironmentProvider
from ctfh_core.kernel import next_host_epoch, run_supervised
from ctfh_core.outcome import LocalOutcomeAuthority
from ctfh_core.runlock import (
    apply_knowledge_prompt,
    build_runlock,
    resolve_knowledge_snapshot,
)
from ctfh_core.store import StateStore
from ctfh_core.target import LocalTargetProvider
from ctfh_core.task_source import LocalTaskSource
from ctfh_core.trace import TRACE_MANIFEST_SCHEMA, seal_trace_manifest
from ctfh_core.types import (
    AttemptState,
    OperationState,
    WorkerOwnerTag,
)


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "benchmarks" / "fixtures" / "smoke"


class V0Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = LocalTaskSource(FIXTURE).load()
        self.task = self.bundle.spec
        self.assertFalse(hasattr(self.task, "evaluator_data"))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_evaluator_secret_is_not_mounted(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        self.assertTrue((env.workspace / "TASK.md").is_file())
        self.assertFalse((env.workspace / "evaluator.json").exists())
        self.assertNotIn(
            self.bundle.evaluator_data["expected_candidate"],
            "\n".join(
                p.read_text(errors="replace")
                for p in env.workspace.rglob("*")
                if p.is_file()
            ),
        )
        await provider.destroy(owner)

    async def test_environment_kills_process_group(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        devnull = open("/dev/null", "wb")
        try:
            proc = await provider.spawn(
                env,
                ["python3", "-c", "import time; time.sleep(60)"],
                stdout=devnull,
                stderr=devnull,
            )
            self.assertIsNone(proc.returncode)
            await provider.kill(env, grace_s=0.05)
            self.assertIsNotNone(proc.returncode)
            self.assertFalse(env.processes)
        finally:
            devnull.close()
            await provider.destroy(owner)

    async def test_codex_runtime_home_materializes_pinned_read_only_skills(self):
        source_home = self.root / "source-home"
        source_codex = source_home / ".codex"
        source_codex.mkdir(parents=True)
        (source_codex / "config.toml").write_text("model = \"example\"\n")
        (source_codex / "auth.json").write_text("{}\n")

        skills = source_codex / "skills"
        demo = skills / "demo"
        demo.mkdir(parents=True)
        (demo / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n"
        )
        external = self.root / "external-skill"
        external.mkdir()
        (external / "SKILL.md").write_text(
            "---\nname: linked\ndescription: linked skill\n---\n"
        )
        (skills / "linked").symlink_to(external, target_is_directory=True)

        (source_codex / "memories").mkdir()
        (source_codex / "memories" / "MEMORY.md").write_text("old memory\n")
        (source_codex / "sessions").mkdir()
        (source_codex / "history.jsonl").write_text("old history\n")

        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            codex_home=source_codex,
            home=source_home,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        runtime = provider._codex_runtime_home(env.root)
        self.assertTrue(runtime.is_dir())
        self.assertFalse((runtime / "config.toml").exists())
        self.assertTrue((runtime / "auth.json").is_file())
        self.assertFalse((runtime / "skills").exists())
        self.assertFalse((runtime / "memories").exists())
        self.assertFalse((runtime / "sessions").exists())
        self.assertFalse((runtime / "history.jsonl").exists())

        expected = tree_snapshot_digest(skills)
        manifest = CodexCLIAdapter().materialize(
            env,
            provider,
            {
                "skills": {"mode": "host-pinned", "digest": expected},
                "memory": {
                    "mode": "off",
                    "knowledge_snapshot_digest": None,
                },
            },
        )
        materialized = runtime / "skills"
        materialized_config = runtime / "config.toml"
        self.assertTrue(materialized_config.is_file())
        self.assertEqual(
            materialized_config.read_text(),
            CodexCLIAdapter.materialized_config_text(),
        )
        self.assertEqual(
            manifest["native_config_digest"],
            CodexCLIAdapter.materialized_config_digest(),
        )
        self.assertTrue((materialized / "demo" / "SKILL.md").is_file())
        self.assertTrue((materialized / "linked" / "SKILL.md").is_file())
        self.assertFalse((materialized / "linked").is_symlink())
        self.assertEqual(manifest["skill_set_digest"], expected)
        self.assertEqual(tree_snapshot_digest(materialized), expected)

        cmd = provider._sandbox_command(env, ["true"])
        joined = "\n".join(cmd)
        self.assertIn(str(runtime), joined)
        self.assertIn(str(source_codex / "auth.json"), joined)
        self.assertIn(str(materialized), joined)
        ro_bind_pairs = [
            (cmd[index + 1], cmd[index + 2])
            for index, token in enumerate(cmd[:-2])
            if token == "--ro-bind"
        ]
        self.assertIn(
            (str(materialized_config), str(source_codex / "config.toml")),
            ro_bind_pairs,
        )
        self.assertIn(
            (str(materialized), str(source_codex / "skills")),
            ro_bind_pairs,
        )
        ro_sources = {source for source, _ in ro_bind_pairs}
        self.assertNotIn(str(source_codex / "config.toml"), ro_sources)
        self.assertNotIn(str(source_codex / "skills"), ro_sources)
        self.assertNotIn(str(source_codex / "memories"), joined)
        self.assertNotIn(str(source_codex / "sessions"), joined)
        self.assertNotIn(str(source_codex / "history.jsonl"), joined)
        if provider.resolver_path.is_file() and not provider.resolver_path.is_relative_to("/etc"):
            self.assertIn(str(provider.resolver_path), joined)

        devnull = open("/dev/null", "wb")
        try:
            proc = await provider.spawn(
                env,
                ["python3", "-c", "from pathlib import Path; Path.home().joinpath('.codex/skills/demo/NEW').write_text('x')"],
                stdout=devnull,
                stderr=devnull,
            )
            self.assertNotEqual(await proc.wait(), 0)
            self.assertFalse((materialized / "demo" / "NEW").exists())

            config_proc = await provider.spawn(
                env,
                ["python3", "-c", "from pathlib import Path; Path.home().joinpath('.codex/config.toml').write_text('changed')"],
                stdout=devnull,
                stderr=devnull,
            )
            self.assertNotEqual(await config_proc.wait(), 0)
            self.assertEqual(
                materialized_config.read_text(),
                CodexCLIAdapter.materialized_config_text(),
            )
        finally:
            devnull.close()
            await provider.destroy(owner)

    async def test_create_cancellation_during_service_start_cleans_owned_environment(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag(
            "r",
            1,
            self.task.task_id,
            "create-service-cancel",
        )
        entered = asyncio.Event()

        async def blocked_start(_env):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(
            provider,
            "_start_browser_server",
            new=blocked_start,
        ):
            create_task = asyncio.create_task(
                provider.create(self.task, owner, CancelToken())
            )
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            create_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(create_task, timeout=2.0)

        self.assertNotIn(owner.label(), provider._live)
        self.assertFalse(provider._env_root(owner).exists())
        self.assertFalse(provider._session_socket_dir(provider._env_root(owner)).exists())
        self.assertFalse(provider._browser_socket_dir(provider._env_root(owner)).exists())

    async def test_interactive_session_round_trip_and_transcript(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            session_id = await provider.session_open(
                env,
                [
                    "python3",
                    "-u",
                    "-c",
                    (
                        "import sys; "
                        "print('READY', flush=True); "
                        "line=sys.stdin.readline(); "
                        "print('ECHO:'+line.strip(), flush=True)"
                    ),
                ],
            )
            first = await provider.session_read(
                env,
                session_id,
                since_offset=0,
                max_bytes=4096,
                wait_ms=2000,
                until_quiescent=True,
            )
            self.assertIn(b"READY", first["data"])
            next_offset = int(first["next_offset"])
            self.assertEqual(await provider.session_send(env, session_id, "hello\n"), 6)
            second = await provider.session_read(
                env,
                session_id,
                since_offset=next_offset,
                max_bytes=4096,
                wait_ms=2000,
                until_quiescent=True,
            )
            self.assertIn(b"ECHO:hello", second["data"])
            await asyncio.sleep(0.05)
            final = await provider.session_read(
                env, session_id, since_offset=int(second["next_offset"]), max_bytes=4096
            )
            transcript = Path(second["transcript_path"])
            self.assertTrue(transcript.is_file())
            self.assertIn(b"ECHO:hello", transcript.read_bytes())
            self.assertTrue(final["eof"] or provider.session_list(env)[0]["exit_code"] == 0)
            await provider.session_close(env, session_id)
        finally:
            await provider.destroy(owner)

    async def test_interactive_session_transcript_is_bounded(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            max_session_transcript_bytes=32,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        attempt_output = self.root / "bounded-transcript-attempt"
        try:
            session_id = await provider.session_open(
                env,
                ["python3", "-u", "-c", "print('X'*128, flush=True)"],
            )
            result = await provider.session_read(
                env,
                session_id,
                since_offset=0,
                max_bytes=1024,
                wait_ms=2000,
                until_quiescent=True,
            )
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["data"]), 32)
            self.assertGreater(int(result["total_bytes"]), len(result["data"]))
            self.assertTrue(result["transcript_truncated"])
            self.assertLessEqual(int(result["transcript_bytes"]), 32)
            transcript = Path(result["transcript_path"])
            self.assertLessEqual(transcript.stat().st_size, 32)

            await provider.session_close(env, session_id)
            await provider.publish_service_traces(env, attempt_output)
            manifest, payload = seal_trace_manifest(
                attempt_output,
                task_id=self.task.task_id,
                attempt_id="bounded-transcript",
                runlock_base_hash="a" * 64,
                adapter_name="codex-cli",
                agent_trace_coverage="codex-json-events",
                timed_out=False,
            )
            self.assertTrue(manifest.is_file())
            transcript_stream = next(
                stream
                for stream in payload["streams"]
                if stream["kind"] == "environment.pty.transcript"
            )
            self.assertTrue(transcript_stream["coverage"]["truncated"])
            self.assertEqual(
                transcript_stream["coverage"]["capture_limit_bytes"],
                32,
            )
            self.assertGreater(
                transcript_stream["coverage"]["observed_bytes"],
                transcript_stream["coverage"]["captured_bytes"],
            )
            self.assertTrue(
                any(
                    stream["kind"] == "environment.pty.metadata"
                    for stream in payload["streams"]
                )
            )
        finally:
            await provider.destroy(owner)

    async def test_interactive_session_limit_is_per_attempt(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            max_sessions_per_attempt=1,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            first = await provider.session_open(
                env, ["python3", "-c", "import time; time.sleep(60)"]
            )
            with self.assertRaisesRegex(RuntimeError, "session limit"):
                await provider.session_open(
                    env, ["python3", "-c", "import time; time.sleep(60)"]
                )
            await provider.session_close(env, first, grace_s=0.05)
        finally:
            await provider.destroy(owner)

    async def test_environment_kill_terminates_interactive_sessions(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        session_id = await provider.session_open(
            env, ["python3", "-c", "import time; time.sleep(60)"]
        )
        self.assertTrue(provider.session_list(env)[0]["running"])
        await provider.kill(env, grace_s=0.05)
        info = provider.session_list(env)[0]
        self.assertFalse(info["running"])
        self.assertFalse(env.processes)
        self.assertIsNotNone(info["exit_code"])
        await provider.destroy(owner)

    async def test_environment_kill_cancels_blocked_session_rpc_before_process_wait(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_interactive_session_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "blocked-send")
        env = await provider.create(self.task, owner, CancelToken())
        session_id = await provider.session_open(
            env,
            ["python3", "-c", "import time; time.sleep(60)"],
        )
        label = owner.label()
        send_task: asyncio.Task[int] | None = None
        try:
            with patch(
                "ctfh_core.environment.os.write",
                side_effect=BlockingIOError,
            ):
                send_task = asyncio.create_task(
                    provider.session_send(env, session_id, b"x")
                )
                provider._session_rpc_tasks[label].add(send_task)
                await asyncio.sleep(0.02)
                await asyncio.wait_for(
                    provider.kill(env, grace_s=0.05),
                    timeout=1.0,
                )
            self.assertTrue(send_task.done())
            self.assertFalse(env.processes)
            self.assertFalse(provider.session_list(env)[0]["running"])
        finally:
            if send_task is not None and not send_task.done():
                send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)
            await provider.destroy(owner)

    async def test_session_close_is_idempotent_after_fd_reuse(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "fd-close")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            session_id = await provider.session_open(
                env,
                ["python3", "-c", "import time; time.sleep(60)"],
            )
            await provider.session_close(env, session_id, grace_s=0.05)
            session = provider._get_session(env, session_id)
            self.assertIsNone(session.master_fd)

            read_fd, write_fd = os.pipe()
            try:
                await provider.session_close(env, session_id, grace_s=0.05)
                os.write(write_fd, b"x")
                self.assertEqual(os.read(read_fd, 1), b"x")
            finally:
                os.close(read_fd)
                os.close(write_fd)
        finally:
            await provider.destroy(owner)

    async def test_session_open_cancellation_reaps_unregistered_child(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "open-cancel")
        env = await provider.create(self.task, owner, CancelToken())
        real_create = asyncio.create_subprocess_exec

        async def delayed_create(*args, **kwargs):
            await asyncio.sleep(0.05)
            return await real_create(*args, **kwargs)

        try:
            with patch(
                "ctfh_core.environment.asyncio.create_subprocess_exec",
                new=delayed_create,
            ):
                open_task = asyncio.create_task(
                    provider.session_open(
                        env,
                        ["python3", "-c", "import time; time.sleep(60)"],
                    )
                )
                await asyncio.sleep(0.01)
                open_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await open_task
            self.assertFalse(env.processes)
            self.assertEqual(provider.session_list(env), [])
        finally:
            await provider.destroy(owner)

    async def test_concurrent_session_open_cannot_bypass_attempt_lifetime_limit(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            max_sessions_per_attempt=1,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "open-race")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            results = await asyncio.gather(
                provider.session_open(
                    env,
                    ["python3", "-c", "import time; time.sleep(60)"],
                ),
                provider.session_open(
                    env,
                    ["python3", "-c", "import time; time.sleep(60)"],
                ),
                return_exceptions=True,
            )
            opened = [value for value in results if isinstance(value, str)]
            rejected = [
                value for value in results if isinstance(value, RuntimeError)
            ]
            self.assertEqual(len(opened), 1)
            self.assertEqual(len(rejected), 1)
            await provider.session_close(env, opened[0], grace_s=0.05)

            with self.assertRaisesRegex(
                RuntimeError,
                "session limit",
            ):
                await provider.session_open(
                    env,
                    ["python3", "-c", "print('second')"],
                )
        finally:
            await provider.destroy(owner)

    async def test_natural_session_completion_releases_master_fd(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            max_sessions_per_attempt=1,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "natural-close")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            session_id = await provider.session_open(
                env,
                ["python3", "-u", "-c", "print('DONE', flush=True)"],
            )
            result = await provider.session_read(
                env,
                session_id,
                wait_ms=2000,
                until_quiescent=True,
            )
            self.assertIn(b"DONE", result["data"])
            for _ in range(50):
                session = provider._get_session(env, session_id)
                if session.exit_code is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(session.exit_code)
            self.assertIsNone(session.master_fd)

            with self.assertRaisesRegex(
                RuntimeError,
                "session limit",
            ):
                await provider.session_open(
                    env,
                    ["python3", "-c", "print('no-reuse')"],
                )
        finally:
            await provider.destroy(owner)

    async def test_environment_kill_cancels_connected_blocked_session_rpc(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_interactive_session_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "rpc-blocked-send")
        env = await provider.create(self.task, owner, CancelToken())
        session_id = await provider.session_open(
            env,
            ["python3", "-c", "import time; time.sleep(60)"],
        )
        reader = writer = None
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(provider._session_socket_path(env.root))
            )
            with patch(
                "ctfh_core.environment.os.write",
                side_effect=BlockingIOError,
            ):
                writer.write(
                    (
                        json.dumps(
                            {
                                "op": "send",
                                "session_id": session_id,
                                "data": "x",
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                await asyncio.sleep(0.03)
                self.assertTrue(
                    any(
                        not task.done()
                        for task in provider._session_rpc_tasks[
                            owner.label()
                        ]
                    )
                )
                await asyncio.wait_for(
                    provider.kill(env, grace_s=0.05),
                    timeout=1.0,
                )
            self.assertFalse(env.processes)
            self.assertFalse(provider._session_rpc_tasks.get(owner.label()))
            self.assertEqual(
                await asyncio.wait_for(reader.read(), timeout=0.5),
                b"",
            )
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
            await provider.destroy(owner)

    async def test_environment_kill_aborts_nonreading_rpc_response_transport(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_interactive_session_service=True,
            max_session_transcript_bytes=3 * 1024 * 1024,
        )
        owner = WorkerOwnerTag(
            "r",
            1,
            self.task.task_id,
            "rpc-response-backpressure",
        )
        env = await provider.create(self.task, owner, CancelToken())
        data_session = await provider.session_open(
            env,
            [
                "python3",
                "-u",
                "-c",
                (
                    "import sys; "
                    "sys.stdout.write('X' * 1500000); "
                    "sys.stdout.flush()"
                ),
            ],
        )
        sleeper = await provider.session_open(
            env,
            ["python3", "-c", "import time; time.sleep(60)"],
        )
        reader = writer = None
        try:
            data = provider._get_session(env, data_session)
            for _ in range(300):
                if data.exit_code is not None and data.total_bytes >= 1_000_000:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(data.exit_code)
            self.assertGreaterEqual(data.total_bytes, 1_000_000)

            reader, writer = await asyncio.open_unix_connection(
                str(provider._session_socket_path(env.root))
            )
            raw_socket = writer.get_extra_info("socket")
            self.assertIsNotNone(raw_socket)
            raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)

            writer.write(
                (
                    json.dumps(
                        {
                            "op": "read",
                            "session_id": data_session,
                            "since_offset": 0,
                            "max_bytes": 1024 * 1024,
                            "wait_ms": 0,
                            "until_quiescent": False,
                        }
                    )
                    + "\n"
                ).encode()
            )
            await writer.drain()
            await asyncio.sleep(0.08)
            self.assertTrue(
                any(
                    not task.done()
                    for task in provider._session_rpc_tasks[owner.label()]
                )
            )

            await asyncio.wait_for(
                provider.kill(env, grace_s=0.05),
                timeout=2.0,
            )
            self.assertFalse(env.processes)
            self.assertFalse(provider._session_rpc_tasks.get(owner.label()))
            self.assertFalse(provider.session_list(env)[1]["running"])

            # Bytes already accepted by the Unix socket may still be readable
            # after the server aborts the transport. The invariant is that no
            # handler/process remains and the peer reaches EOF promptly.
            drained = 0
            async with asyncio.timeout(1.0):
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    drained += len(chunk)
                    self.assertLessEqual(drained, 2 * 1024 * 1024)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
            await provider.destroy(owner)

    async def test_rpc_writer_closes_when_response_drain_is_cancelled(self):
        class BlockingWriter:
            def __init__(self):
                self.closed = False
                self.drain_started = asyncio.Event()

            def write(self, _data: bytes) -> None:
                pass

            async def drain(self) -> None:
                self.drain_started.set()
                await asyncio.Event().wait()

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                pass

        writer = BlockingWriter()
        task = asyncio.create_task(
            LocalWorkerEnvironmentProvider._finish_rpc_writer(
                writer,  # type: ignore[arg-type]
                {"ok": True},
            )
        )
        await asyncio.wait_for(writer.drain_started.wait(), timeout=0.5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(writer.closed)

    async def test_rpc_writer_aborts_when_wait_closed_is_cancelled(self):
        class Transport:
            def __init__(self):
                self.aborted = False

            def abort(self) -> None:
                self.aborted = True

        class BlockingCloseWriter:
            def __init__(self):
                self.transport = Transport()
                self.closed = False
                self.wait_closed_started = asyncio.Event()

            def write(self, _data: bytes) -> None:
                pass

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                self.wait_closed_started.set()
                await asyncio.Event().wait()

        writer = BlockingCloseWriter()
        task = asyncio.create_task(
            LocalWorkerEnvironmentProvider._finish_rpc_writer(
                writer,  # type: ignore[arg-type]
                {"ok": True},
            )
        )
        await asyncio.wait_for(writer.wait_closed_started.wait(), timeout=0.5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(writer.closed)
        self.assertTrue(writer.transport.aborted)

    async def test_session_cli_reaches_provider_over_unix_socket(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_interactive_session_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())

        async def run_cli(*argv: str) -> dict[str, object]:
            output = env.root / ("cli-" + str(time.monotonic_ns()) + ".out")
            errors = output.with_suffix(".err")
            with output.open("wb") as stdout, errors.open("wb") as stderr:
                proc = await provider.spawn(
                    env,
                    ["ctfh-session", *argv],
                    stdout=stdout,
                    stderr=stderr,
                )
                rc = await asyncio.wait_for(proc.wait(), 10)
            self.assertEqual(rc, 0, errors.read_text(errors="replace"))
            return json.loads(output.read_text())

        try:
            opened = await run_cli(
                "open",
                "--",
                "python3",
                "-u",
                "-c",
                (
                    "import sys; "
                    "print('READY', flush=True); "
                    "line=sys.stdin.readline(); "
                    "print('RPC:'+line.strip(), flush=True)"
                ),
            )
            session_id = str(opened["session_id"])
            first = await run_cli(
                "read",
                session_id,
                "--wait-ms",
                "2000",
                "--until-quiescent",
            )
            self.assertIn("READY", first["data"]["text"])
            next_offset = int(first["next_offset"])

            sent = await run_cli("send", session_id, "hello\n")
            self.assertEqual(sent["written"], 6)
            second = await run_cli(
                "read",
                session_id,
                "--since",
                str(next_offset),
                "--wait-ms",
                "2000",
                "--until-quiescent",
            )
            self.assertIn("RPC:hello", second["data"]["text"])
            listed = await run_cli("list")
            self.assertEqual(len(listed["sessions"]), 1)
            closed = await run_cli("close", session_id)
            self.assertEqual(closed["exit_code"], 0)
        finally:
            await provider.destroy(owner)

    def _fake_ghostchrome(self) -> Path:
        fake = self.root / "fake-ghostchrome"
        fake.write_text(
            """#!/usr/bin/python3
import json
import os
import signal
import sys
import time

args = sys.argv[1:]
if "serve" in args:
    count_path = os.environ.get("FAKE_GHOSTCHROME_COUNT")
    if count_path:
        with open(count_path, "a", encoding="utf-8") as count:
            count.write("serve\\n")
    delay = float(os.environ.get("FAKE_GHOSTCHROME_DELAY", "0"))
    if delay:
        time.sleep(delay)
    print("ws://127.0.0.1:39999/devtools/browser/fake", flush=True)
    stopped = False
    def stop(*_):
        global stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        time.sleep(0.05)
    raise SystemExit(0)

stderr_bytes = int(os.environ.get("FAKE_GHOSTCHROME_STDERR_BYTES", "0"))
if stderr_bytes:
    sys.stderr.write("E" * stderr_bytes)
    sys.stderr.flush()
stdout_bytes = int(os.environ.get("FAKE_GHOSTCHROME_STDOUT_BYTES", "0"))
if stdout_bytes:
    sys.stdout.write("O" * stdout_bytes)
    sys.stdout.flush()

ops = {"navigate", "extract", "click", "type", "eval", "url", "back", "forward", "errors", "tabs"}
op = next((item for item in args if item in ops), None)
if op is None:
    print(json.dumps({"error": "unknown op", "argv": args}))
    raise SystemExit(2)
if op == "eval":
    expr = args[args.index("eval") + 1]
    result = "about:blank" if expr == "location.href" else "FAKE_TITLE"
    print(json.dumps({"expression": expr, "result": result}))
elif op == "url":
    print(json.dumps({"url": "about:blank"}))
elif op == "extract":
    print(json.dumps({"tree": "button \\"Go\\" [ref=@1]"}))
else:
    print(json.dumps({"op": op, "ok": True}))
"""
        )
        fake.chmod(0o755)
        return fake

    async def test_browser_service_is_lazy_and_cli_rpc_is_allowlisted(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser")
        env = await provider.create(self.task, owner, CancelToken())
        self.assertFalse(provider._browsers)

        async def run_cli(*argv: str) -> dict[str, object]:
            output = env.root / ("browser-cli-" + str(time.monotonic_ns()) + ".out")
            errors = output.with_suffix(".err")
            with output.open("wb") as stdout, errors.open("wb") as stderr:
                proc = await provider.spawn(
                    env,
                    ["ctfh-browser", *argv],
                    stdout=stdout,
                    stderr=stderr,
                )
                rc = await asyncio.wait_for(proc.wait(), 10)
            self.assertEqual(rc, 0, errors.read_text(errors="replace"))
            return json.loads(output.read_text())

        try:
            with patch("ctfh_core.environment.shutil.which", return_value=str(fake)):
                initialized = await run_cli("init")
                self.assertTrue(initialized["ready"])
                self.assertIn(owner.label(), provider._browsers)

                evaluated = await run_cli("eval", "document.title")
                self.assertEqual(
                    evaluated["result"]["result"],
                    "FAKE_TITLE",
                )

                url = await run_cli("url")
                self.assertEqual(url["result"]["result"], "about:blank")

                with self.assertRaisesRegex(ValueError, "unsupported browser operation"):
                    runtime = provider._browsers[owner.label()]
                    provider._browser_controller_args(
                        runtime,
                        "upload",
                        {"path": "/tmp/secret"},
                    )

                closed = await run_cli("close")
                self.assertTrue(closed["closed"])
                self.assertNotIn(owner.label(), provider._browsers)

                trace = (env.root / "browser" / "trace.jsonl").read_text()
                self.assertNotIn("document.title", trace)
                first_record = json.loads(trace.splitlines()[0])
                self.assertEqual(first_record["v"], 1)
                self.assertEqual(first_record["kind"], "browser.operation")
        finally:
            await provider.destroy(owner)

    async def test_concurrent_browser_init_starts_one_attempt_owned_runtime(self):
        fake = self._fake_ghostchrome()
        count_path = self.root / "ghostchrome-start-count.txt"
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser-race")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            with (
                patch(
                    "ctfh_core.environment.shutil.which",
                    return_value=str(fake),
                ),
                patch.dict(
                    os.environ,
                    {
                        "FAKE_GHOSTCHROME_COUNT": str(count_path),
                        "FAKE_GHOSTCHROME_DELAY": "0.10",
                    },
                    clear=False,
                ),
            ):
                first, second = await asyncio.gather(
                    provider.browser_call(env, "init"),
                    provider.browser_call(env, "init"),
                )
            self.assertTrue(first["ready"])
            self.assertTrue(second["ready"])
            self.assertEqual(
                count_path.read_text().splitlines(),
                ["serve"],
            )
            self.assertEqual(len(provider._browsers), 1)
            self.assertEqual(
                len(
                    [
                        pgid
                        for pgid in env.processes
                        if pgid in provider._procs
                    ]
                ),
                1,
            )
        finally:
            await provider.destroy(owner)

    async def test_browser_startup_cancellation_barrier_kills_spawn_window_process(self):
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser-start-cancel")
        env = await provider.create(self.task, owner, CancelToken())
        real_create = asyncio.create_subprocess_exec
        created = asyncio.Event()
        spawned: dict[str, asyncio.subprocess.Process] = {}

        async def delayed_create(*args, **kwargs):
            proc = await real_create(
                "python3",
                "-c",
                "import time; time.sleep(60)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            spawned["proc"] = proc
            created.set()
            await asyncio.sleep(0.20)
            return proc

        task: asyncio.Task[dict[str, object]] | None = None
        try:
            with (
                patch(
                    "ctfh_core.environment.shutil.which",
                    return_value="/bin/true",
                ),
                patch(
                    "ctfh_core.environment.asyncio.create_subprocess_exec",
                    side_effect=delayed_create,
                ),
            ):
                task = asyncio.create_task(
                    provider.browser_call(env, "init")
                )
                await asyncio.wait_for(created.wait(), timeout=1.0)
                pgid = spawned["proc"].pid
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3.0)
            self.assertIsNotNone(spawned["proc"].returncode)
            self.assertFalse(os.path.exists(f"/proc/{pgid}"))
            self.assertNotIn(pgid, env.processes)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            proc = spawned.get("proc")
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            await provider.destroy(owner)

    async def test_browser_controller_cancellation_barrier_kills_spawn_window_process(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag(
            "r",
            1,
            self.task.task_id,
            "browser-controller-cancel",
        )
        env = await provider.create(self.task, owner, CancelToken())
        real_create = asyncio.create_subprocess_exec
        created = asyncio.Event()
        spawned: dict[str, asyncio.subprocess.Process] = {}
        task: asyncio.Task[dict[str, object]] | None = None
        try:
            with patch(
                "ctfh_core.environment.shutil.which",
                return_value=str(fake),
            ):
                initialized = await provider.browser_call(env, "init")
                self.assertTrue(initialized["ready"])
                serve_pid = provider._browsers[owner.label()].process.pid

                async def delayed_create(*args, **kwargs):
                    proc = await real_create(
                        "python3",
                        "-c",
                        "import time; time.sleep(60)",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=True,
                    )
                    spawned["proc"] = proc
                    created.set()
                    await asyncio.sleep(0.20)
                    return proc

                with patch(
                    "ctfh_core.environment.asyncio.create_subprocess_exec",
                    side_effect=delayed_create,
                ):
                    task = asyncio.create_task(
                        provider.browser_call(env, "url")
                    )
                    await asyncio.wait_for(created.wait(), timeout=1.0)
                    controller_pid = spawned["proc"].pid
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=3.0)

            self.assertIsNotNone(spawned["proc"].returncode)
            self.assertFalse(os.path.exists(f"/proc/{controller_pid}"))
            self.assertNotIn(controller_pid, env.processes)
            self.assertIn(serve_pid, env.processes)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            proc = spawned.get("proc")
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            await provider.destroy(owner)

    async def test_browser_controller_output_is_supervisor_bounded(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser-bounded")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            with patch(
                "ctfh_core.environment.shutil.which",
                return_value=str(fake),
            ):
                await provider.browser_call(env, "init")
                runtime = provider._browsers[owner.label()]
                serve_pgid = runtime.process.pid
                with patch.dict(
                    os.environ,
                    {"FAKE_GHOSTCHROME_STDERR_BYTES": str(300 * 1024)},
                    clear=False,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "browser stderr exceeded configured limit",
                    ):
                        await provider.browser_call(
                            env,
                            "eval",
                            {"expr": "document.title"},
                        )
                self.assertIn(serve_pgid, env.processes)
                self.assertEqual(
                    [
                        pgid
                        for pgid in env.processes
                        if pgid in provider._procs
                    ],
                    [serve_pgid],
                )
        finally:
            await provider.destroy(owner)

    async def test_environment_kill_cancels_connected_browser_start_rpc(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser-rpc-kill")
        env = await provider.create(self.task, owner, CancelToken())
        reader = writer = None
        try:
            with (
                patch(
                    "ctfh_core.environment.shutil.which",
                    return_value=str(fake),
                ),
                patch.dict(
                    os.environ,
                    {"FAKE_GHOSTCHROME_DELAY": "5.0"},
                    clear=False,
                ),
            ):
                reader, writer = await asyncio.open_unix_connection(
                    str(provider._browser_socket_path(env.root))
                )
                writer.write(
                    (
                        json.dumps({"op": "init", "args": {}})
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                await asyncio.sleep(0.05)
                self.assertTrue(
                    any(
                        not task.done()
                        for task in provider._browser_rpc_tasks[
                            owner.label()
                        ]
                    )
                )
                await asyncio.wait_for(
                    provider.kill(env, grace_s=0.05),
                    timeout=1.0,
                )
            self.assertFalse(env.processes)
            self.assertFalse(provider._browser_rpc_tasks.get(owner.label()))
            self.assertFalse(provider._browsers)
            self.assertEqual(
                await asyncio.wait_for(reader.read(), timeout=0.5),
                b"",
            )
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
            await provider.destroy(owner)

    async def test_environment_kill_terminates_browser_runtime(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "browser-kill")
        env = await provider.create(self.task, owner, CancelToken())
        try:
            with patch("ctfh_core.environment.shutil.which", return_value=str(fake)):
                result = await provider.browser_call(env, "init")
                self.assertTrue(result["ready"])
                runtime = provider._browsers[owner.label()]
                pgid = runtime.process.pid
                self.assertTrue(runtime.process.returncode is None)
                await provider.kill(env, grace_s=0.05)
                self.assertFalse(env.processes)
                self.assertNotIn(owner.label(), provider._browsers)
                self.assertFalse(os.path.exists(f"/proc/{pgid}"))
        finally:
            await provider.destroy(owner)

    async def test_service_traces_survive_environment_destroy(self):
        fake = self._fake_ghostchrome()
        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            enable_interactive_session_service=True,
            enable_browser_service=True,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "trace-publish")
        env = await provider.create(self.task, owner, CancelToken())
        attempt_output = self.root / "attempt-output"

        try:
            session_id = await provider.session_open(
                env,
                ["python3", "-u", "-c", "print('TRACE_ME', flush=True)"],
            )
            session_result = await provider.session_read(
                env,
                session_id,
                wait_ms=2000,
                until_quiescent=True,
            )
            self.assertIn(b"TRACE_ME", session_result["data"])

            with patch("ctfh_core.environment.shutil.which", return_value=str(fake)):
                await provider.browser_call(env, "init")
                await provider.browser_call(
                    env,
                    "eval",
                    {"expr": "document.title"},
                )

            await provider.kill(env, grace_s=0.05)
            published = await provider.publish_service_traces(
                env, attempt_output
            )
            self.assertTrue(published)
            await provider.destroy(owner)

            self.assertFalse(env.root.exists())
            session_logs = list(
                (attempt_output / "services" / "sessions").glob("*.log")
            )
            self.assertEqual(len(session_logs), 1)
            self.assertIn(b"TRACE_ME", session_logs[0].read_bytes())

            browser_trace = (
                attempt_output / "services" / "browser" / "trace.jsonl"
            )
            self.assertTrue(browser_trace.is_file())
            trace_text = browser_trace.read_text()
            self.assertIn('"op": "eval"', trace_text)
            self.assertNotIn("document.title", trace_text)
        finally:
            await provider.destroy(owner)

    async def test_run_direct_materialize_failure_cleans_environment(self):
        run_root = self.root / "direct-materialize-failure"
        with patch.object(
            CodexCLIAdapter,
            "materialize",
            side_effect=RuntimeError("materialize boom"),
        ):
            with self.assertRaisesRegex(RuntimeError, "materialize boom"):
                await run_direct(
                    task=self.task,
                    run_root=run_root,
                    model="gpt-6-sol",
                    reasoning_effort="high",
                    evaluator_data=self.bundle.evaluator_data,
                )
        env_root = run_root / "environments"
        self.assertTrue(env_root.is_dir())
        self.assertEqual(
            [path for path in env_root.iterdir() if path.is_dir()],
            [],
        )

    async def test_run_supervised_materialize_failure_cleans_environment(self):
        run_root = self.root / "supervised-materialize-failure"
        with patch.object(
            CodexCLIAdapter,
            "materialize",
            side_effect=RuntimeError("materialize boom"),
        ):
            with self.assertRaisesRegex(RuntimeError, "materialize boom"):
                await run_supervised(
                    task=self.task,
                    run_root=run_root,
                    model="gpt-6-sol",
                    reasoning_effort="high",
                    evaluator_data=self.bundle.evaluator_data,
                    host_state_dir=self.root / "host-materialize-failure",
                )
        env_root = run_root / "environments"
        self.assertTrue(env_root.is_dir())
        self.assertEqual(
            [path for path in env_root.iterdir() if path.is_dir()],
            [],
        )

    async def test_run_direct_outer_cancellation_cleans_environment(self):
        run_root = self.root / "direct-cancel"

        async def hang_run(*args, **kwargs):
            await asyncio.Event().wait()

        with patch.object(CodexCLIAdapter, "run", new=hang_run):
            run_task = asyncio.create_task(
                run_direct(
                    task=self.task,
                    run_root=run_root,
                    model="gpt-6-sol",
                    reasoning_effort="high",
                    evaluator_data=self.bundle.evaluator_data,
                )
            )
            env_root = run_root / "environments"
            for _ in range(150):
                if env_root.is_dir() and any(
                    (path / ".ctfh-owner.json").is_file()
                    for path in env_root.iterdir()
                    if path.is_dir()
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail("direct environment was not created before cancellation")

            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(run_task, timeout=5.0)

        self.assertEqual(
            [path for path in env_root.iterdir() if path.is_dir()],
            [],
        )

    async def test_run_supervised_outer_cancellation_cleans_environment(self):
        run_root = self.root / "supervised-cancel"

        async def hang_run(*args, **kwargs):
            await asyncio.Event().wait()

        with patch.object(CodexCLIAdapter, "run", new=hang_run):
            run_task = asyncio.create_task(
                run_supervised(
                    task=self.task,
                    run_root=run_root,
                    model="gpt-6-sol",
                    reasoning_effort="high",
                    evaluator_data=self.bundle.evaluator_data,
                    host_state_dir=self.root / "host-cancel",
                )
            )
            env_root = run_root / "environments"
            for _ in range(150):
                if env_root.is_dir() and any(
                    (path / ".ctfh-owner.json").is_file()
                    for path in env_root.iterdir()
                    if path.is_dir()
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail(
                    "supervised environment was not created before cancellation"
                )

            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(run_task, timeout=5.0)

        self.assertEqual(
            [path for path in env_root.iterdir() if path.is_dir()],
            [],
        )

    async def test_frozen_knowledge_is_pinned_dereferenced_and_read_only(self):
        source = self.root / "knowledge-source"
        source.mkdir()
        (source / "guide.md").write_text("# Guide\nORBIT-42\n")
        external = self.root / "external-knowledge"
        external.mkdir()
        (external / "linked.txt").write_text("LINKED-VALUE\n")
        (source / "linked").symlink_to(external, target_is_directory=True)

        resolved, memory_mode, digest = resolve_knowledge_snapshot(source)
        self.assertEqual(resolved, source.resolve())
        self.assertEqual(memory_mode, "frozen")
        self.assertEqual(digest, tree_snapshot_digest(source))

        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            knowledge_snapshot_dir=resolved,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "knowledge")
        env = await provider.create(self.task, owner, CancelToken())
        adapter = CodexCLIAdapter()
        effective_prompt = apply_knowledge_prompt("probe", memory_mode)
        lock = build_runlock(
            treatment="knowledge-test",
            task=self.task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt=effective_prompt,
            memory_mode=memory_mode,
            knowledge_snapshot_digest=digest,
        )

        try:
            manifest = adapter.materialize(env, provider, lock.base_config)
            materialized = provider._knowledge_runtime_dir(env.root)
            self.assertEqual(manifest["knowledge_snapshot_digest"], digest)
            self.assertEqual(tree_snapshot_digest(materialized), digest)
            self.assertFalse((materialized / "linked").is_symlink())
            self.assertEqual(
                (materialized / "linked" / "linked.txt").read_text(),
                "LINKED-VALUE\n",
            )

            cmd = provider._sandbox_command(env, ["true"])
            ro_bind_pairs = [
                (cmd[index + 1], cmd[index + 2])
                for index, token in enumerate(cmd[:-2])
                if token == "--ro-bind"
            ]
            self.assertIn(
                (str(materialized), "/ctfh-knowledge"),
                ro_bind_pairs,
            )
            self.assertIn("CTFH_KNOWLEDGE_DIR", cmd)
            self.assertIn("/ctfh-knowledge", cmd)

            output = env.root / "knowledge-read.out"
            errors = env.root / "knowledge-read.err"
            with output.open("wb") as stdout, errors.open("wb") as stderr:
                proc = await provider.spawn(
                    env,
                    [
                        "python3",
                        "-c",
                        (
                            "import os; from pathlib import Path; "
                            "p=Path(os.environ['CTFH_KNOWLEDGE_DIR']); "
                            "print((p/'guide.md').read_text().strip())"
                        ),
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )
                self.assertEqual(await proc.wait(), 0)
            self.assertIn("ORBIT-42", output.read_text())

            with output.open("wb") as stdout, errors.open("wb") as stderr:
                write_proc = await provider.spawn(
                    env,
                    [
                        "python3",
                        "-c",
                        (
                            "import os; from pathlib import Path; "
                            "p=Path(os.environ['CTFH_KNOWLEDGE_DIR']); "
                            "(p/'MUTATE').write_text('x')"
                        ),
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )
                self.assertNotEqual(await write_proc.wait(), 0)
            self.assertFalse((materialized / "MUTATE").exists())
        finally:
            await provider.destroy(owner)

    async def test_read_only_promoted_snapshot_and_locked_workspace_cleanup(self):
        source = self.root / "promoted-readonly"
        source.mkdir()
        note = source / "note.txt"
        note.write_text("PROMOTED\n")
        digest = tree_snapshot_digest(source)
        note.chmod(0o444)
        source.chmod(0o555)

        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            knowledge_snapshot_dir=source,
        )
        owner = WorkerOwnerTag(
            "r",
            1,
            self.task.task_id,
            "readonly-cleanup",
        )
        env = await provider.create(self.task, owner, CancelToken())
        adapter = CodexCLIAdapter()
        lock = build_runlock(
            treatment="knowledge-cleanup-test",
            task=self.task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt=apply_knowledge_prompt("probe", "frozen"),
            memory_mode="frozen",
            knowledge_snapshot_digest=digest,
        )
        try:
            adapter.materialize(env, provider, lock.base_config)
            materialized = provider._knowledge_runtime_dir(env.root)
            self.assertTrue(materialized.stat().st_mode & 0o200)
            self.assertTrue((materialized / "note.txt").stat().st_mode & 0o200)

            locked = env.workspace / "locked"
            locked.mkdir()
            locked_file = locked / "payload"
            locked_file.write_text("x")
            locked_file.chmod(0o000)
            locked.chmod(0o500)

            await provider.destroy(owner)
            self.assertFalse(env.root.exists())
        finally:
            # TemporaryDirectory teardown also needs to clean the source tree.
            source.chmod(0o755)
            note.chmod(0o644)
            if env.root.exists():
                await provider.destroy(owner)

    async def test_frozen_knowledge_source_mutation_fails_closed(self):
        source = self.root / "knowledge-source"
        source.mkdir()
        note = source / "note.txt"
        note.write_text("v1\n")
        _, memory_mode, digest = resolve_knowledge_snapshot(source)

        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            knowledge_snapshot_dir=source,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "knowledge-race")
        env = await provider.create(self.task, owner, CancelToken())
        adapter = CodexCLIAdapter()
        lock = build_runlock(
            treatment="knowledge-test",
            task=self.task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt=apply_knowledge_prompt("probe", memory_mode),
            memory_mode=memory_mode,
            knowledge_snapshot_digest=digest,
        )
        note.write_text("v2\n")
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "knowledge source changed after RunLock resolution",
            ):
                adapter.materialize(env, provider, lock.base_config)
        finally:
            await provider.destroy(owner)

    async def test_claude_uses_same_frozen_knowledge_snapshot(self):
        source = self.root / "knowledge-source"
        source.mkdir()
        (source / "shared.txt").write_text("SHARED-KNOWLEDGE\n")
        resolved, memory_mode, digest = resolve_knowledge_snapshot(source)

        provider = LocalWorkerEnvironmentProvider(
            self.root / "envs",
            knowledge_snapshot_dir=resolved,
        )
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "knowledge-claude")
        env = await provider.create(self.task, owner, CancelToken())
        adapter = ClaudeCLIAdapter()
        lock = build_runlock(
            treatment="knowledge-test",
            task=self.task,
            adapter=adapter,
            model="claude-opus-5-5",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt=apply_knowledge_prompt("probe", memory_mode),
            memory_mode=memory_mode,
            knowledge_snapshot_digest=digest,
        )
        try:
            manifest = adapter.materialize(env, provider, lock.base_config)
            self.assertEqual(manifest["knowledge_snapshot_digest"], digest)
            self.assertEqual(
                (env.root / "knowledge" / "shared.txt").read_text(),
                "SHARED-KNOWLEDGE\n",
            )
        finally:
            await provider.destroy(owner)

    def test_frozen_knowledge_changes_runlock_and_pins_digest(self):
        source = self.root / "knowledge-source"
        source.mkdir()
        (source / "fact.txt").write_text("fact\n")
        _, memory_mode, digest = resolve_knowledge_snapshot(source)
        adapter = CodexCLIAdapter()

        baseline = build_runlock(
            treatment="ctfh-supervised",
            task=self.task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt="same-prompt",
        )
        frozen_prompt = apply_knowledge_prompt("same-prompt", memory_mode)
        frozen = build_runlock(
            treatment="ctfh-supervised",
            task=self.task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=self.task.deadline_seconds,
            prompt=frozen_prompt,
            memory_mode=memory_mode,
            knowledge_snapshot_digest=digest,
        )

        self.assertNotEqual(baseline.base_hash, frozen.base_hash)
        self.assertNotEqual(
            baseline.base_config["prompt_sha256"],
            frozen.base_config["prompt_sha256"],
        )
        self.assertEqual(frozen.base_config["memory"]["mode"], "frozen")
        self.assertEqual(
            frozen.base_config["memory"]["knowledge_snapshot_digest"],
            digest,
        )
        self.assertTrue(frozen.base_config["memory"]["read_only"])
        self.assertTrue(frozen.base_config["memory"]["native_memory_isolated"])
        self.assertEqual(
            frozen.base_config["memory"]["materializer"],
            "snapshot-copy-v1",
        )

    def test_trace_manifest_seals_hashes_and_coverage(self):
        attempt = self.root / "trace-attempt"
        (attempt / "services" / "sessions").mkdir(parents=True)
        (attempt / "services" / "browser").mkdir(parents=True)

        agent_trace = attempt / "trace.jsonl"
        agent_trace.write_text(
            '{"type":"turn.started"}\n{"type":"turn.completed"}\n'
        )
        session_trace = attempt / "services" / "sessions" / "session-a.log"
        session_trace.write_bytes(b"hello\r\n")
        browser_trace = attempt / "services" / "browser" / "trace.jsonl"
        browser_trace.write_text(
            json.dumps(
                {
                    "v": 1,
                    "kind": "browser.operation",
                    "op": "url",
                    "args": {},
                    "duration_s": 0.01,
                    "result_bytes": 10,
                    "result_sha256": "0" * 64,
                },
                sort_keys=True,
            )
            + "\n"
        )
        browser_stderr = (
            attempt / "services" / "browser" / "serve.stderr.log"
        )
        browser_stderr.write_text("Chrome ready\n")

        manifest, payload = seal_trace_manifest(
            attempt,
            task_id=self.task.task_id,
            attempt_id="attempt-test",
            runlock_base_hash="a" * 64,
            adapter_name="codex-cli",
            agent_trace_coverage="codex-json-events",
            timed_out=False,
        )

        self.assertEqual(payload["schema"], TRACE_MANIFEST_SCHEMA)
        self.assertEqual(payload["attempt_id"], "attempt-test")
        self.assertEqual(payload["runlock_base_hash"], "a" * 64)
        self.assertEqual(payload["coverage"]["status"], "partial")
        self.assertEqual(len(payload["streams"]), 4)

        by_id = {
            stream["stream_id"]: stream for stream in payload["streams"]
        }
        self.assertEqual(by_id["agent-native"]["records"], 2)
        self.assertEqual(by_id["browser-operations"]["records"], 1)
        self.assertEqual(
            by_id["agent-native"]["coverage"]["declared"],
            "codex-json-events",
        )
        self.assertEqual(
            by_id["session-0"]["coverage"]["completeness"],
            "pty-output-accounting-unknown",
        )
        self.assertIsNone(
            by_id["session-0"]["coverage"]["truncated"]
        )
        self.assertIn(
            "raw eval expressions",
            by_id["browser-operations"]["coverage"]["does_not_guarantee"],
        )

        self.assertEqual(json.loads(manifest.read_text()), payload)
        for path in (
            agent_trace,
            session_trace,
            browser_trace,
            browser_stderr,
            manifest,
        ):
            self.assertEqual(path.stat().st_mode & 0o222, 0)

    async def test_create_cancel_barrier_removes_workspace(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        token = CancelToken()
        original = provider._create_sync

        def slow_create(task, owner_tag):
            time.sleep(0.08)
            return original(task, owner_tag)

        with patch.object(provider, "_create_sync", side_effect=slow_create):
            create_task = asyncio.create_task(provider.create(self.task, owner, token))
            await asyncio.sleep(0.01)
            token.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await create_task

        self.assertFalse(provider._env_root(owner).exists())

    async def test_orphan_sweep_removes_previous_run_environment(self):
        root = self.root / "envs"
        first = LocalWorkerEnvironmentProvider(root)
        owner = WorkerOwnerTag("old-run", 1, self.task.task_id, "a")
        env = await first.create(self.task, owner, CancelToken())
        self.assertTrue(env.root.exists())

        after_restart = LocalWorkerEnvironmentProvider(root)
        removed = await after_restart.sweep_workers(set())
        self.assertIn(owner.label(), removed)
        self.assertFalse(env.root.exists())

    async def test_local_authority_uses_unmounted_secret(self):
        provider = LocalWorkerEnvironmentProvider(self.root / "envs")
        owner = WorkerOwnerTag("r", 1, self.task.task_id, "a")
        env = await provider.create(self.task, owner, CancelToken())
        target = await LocalTargetProvider().acquire(
            run_id="r",
            task_id=self.task.task_id,
            epoch=1,
        )
        verdict = await LocalOutcomeAuthority(self.bundle.evaluator_data).verify(
            self.task,
            target,
            env,
            "CTFH{ALPHA-731}",
        )
        self.assertTrue(verdict.passed)
        await provider.destroy(owner)


class HostControlTests(unittest.TestCase):
    def test_host_epoch_is_monotonic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "epoch"
            self.assertEqual(next_host_epoch(path), 1)
            self.assertEqual(next_host_epoch(path), 2)


class StoreTests(unittest.TestCase):
    def test_target_epoch_cas_rejects_stale_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            try:
                store.ensure_task("t", 1)
                self.assertEqual(store.advance_target_epoch("t"), 2)
                self.assertFalse(
                    store.commit_solved(
                        "t",
                        target_epoch=1,
                        outcome={"candidate": "stale"},
                    )
                )
                self.assertTrue(
                    store.commit_solved(
                        "t",
                        target_epoch=2,
                        outcome={"candidate": "fresh"},
                    )
                )
            finally:
                store.close()

    def test_external_operation_idempotency_key_deduplicates(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            try:
                first = store.begin_operation("op1", "t", "submit", "same")
                second = store.begin_operation("op2", "t", "submit", "same")
                self.assertEqual(first, "op1")
                self.assertEqual(second, "op1")
                store.set_operation_state("op1", OperationState.RUNNING)
                store.set_operation_state("op1", OperationState.UNKNOWN)
            finally:
                store.close()

    def test_recovery_marks_live_attempt_lost(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            try:
                store.ensure_task("t")
                store.create_attempt("a", "t", "owner")
                store.transition_attempt("a", AttemptState.STARTING)
                store.transition_attempt("a", AttemptState.RUNNING)
                self.assertEqual(store.recover_live_attempts(), 1)
                state = store.conn.execute(
                    "SELECT state FROM attempts WHERE attempt_id='a'"
                ).fetchone()["state"]
                self.assertEqual(state, AttemptState.LOST.value)
            finally:
                store.close()


class RunLockTests(unittest.TestCase):
    def test_treatment_does_not_change_base_hash(self):
        task = LocalTaskSource(FIXTURE).load().spec
        adapter = CodexCLIAdapter()
        a = build_runlock(
            treatment="direct-native",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        b = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        self.assertEqual(a.base_hash, b.base_hash)
        self.assertNotEqual(a.full_hash, b.full_hash)
        self.assertEqual(
            a.base_config["capabilities"]["admitted"],
            ["native-shell", "workspace-files"],
        )
        self.assertEqual(a.base_config["capabilities"]["policy"], "static-narrow-only-v3")
        self.assertEqual(a.base_config["skills"]["mode"], "host-pinned")
        self.assertIsNotNone(a.base_config["skills"]["digest"])
        self.assertTrue(a.base_config["skills"]["read_only"])
        self.assertEqual(
            a.base_config["agent"]["native_config"]["policy"],
            "ctfh-minimal-v1",
        )
        self.assertEqual(
            a.base_config["agent"]["native_config"]["digest"],
            CodexCLIAdapter.materialized_config_digest(),
        )
        self.assertEqual(
            a.base_config["agent"]["native_home"]["kind"],
            "codex",
        )
        self.assertNotIn(
            "config.toml",
            a.base_config["agent"]["native_home"]["files"],
        )
        self.assertEqual(a.base_config["web_search"], "native-config-disabled")
        self.assertEqual(a.base_config["memory"]["mode"], "off")
        self.assertIsNone(a.base_config["memory"]["knowledge_snapshot_digest"])
        self.assertTrue(a.base_config["memory"]["native_memory_isolated"])
        self.assertEqual(
            a.base_config["environment_services"],
            {"browser": "disabled", "interactive-session": "disabled"},
        )
        self.assertEqual(
            a.base_config["trace"]["manifest_schema"],
            TRACE_MANIFEST_SCHEMA,
        )
        self.assertEqual(
            a.base_config["trace"]["agent_coverage"],
            "codex-json-events",
        )
        self.assertEqual(
            a.base_config["trace"]["bulk_path"],
            "attempt-private-files",
        )
        self.assertFalse(
            a.base_config["trace"]["kernel_sqlite_hot_path"]
        )

    def test_claude_runlock_is_treatment_stable_and_safe_mode(self):
        task = LocalTaskSource(FIXTURE).load().spec
        adapter = ClaudeCLIAdapter()
        a = build_runlock(
            treatment="direct-native",
            task=task,
            adapter=adapter,
            model="claude-opus-5-5",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        b = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="claude-opus-5-5",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        self.assertEqual(a.base_hash, b.base_hash)
        self.assertNotEqual(a.full_hash, b.full_hash)
        self.assertEqual(a.base_config["agent"]["adapter"], "claude-cli")
        self.assertEqual(
            a.base_config["agent"]["native_home"]["kind"],
            "claude",
        )
        self.assertEqual(a.base_config["skills"]["mode"], "off")
        self.assertIsNone(a.base_config["skills"]["digest"])
        self.assertEqual(
            a.base_config["agent"]["native_config"]["policy"],
            "claude-safe-mode-v1",
        )
        self.assertEqual(
            a.base_config["agent"]["native_config"]["digest"],
            ClaudeCLIAdapter.native_config_digest(),
        )
        self.assertEqual(a.base_config["web_search"], "tool-list-excluded")
        self.assertIn("claude-safe-mode", a.base_config["permissions"])

        with self.assertRaisesRegex(ValueError, "requires skills=off"):
            build_runlock(
                treatment="invalid",
                task=task,
                adapter=adapter,
                model="claude-opus-5-5",
                reasoning_effort="high",
                sandbox="danger-full-access",
                deadline_seconds=task.deadline_seconds,
                prompt="same-prompt",
                skill_mode="host-pinned",
            )

    def test_browser_treatment_and_policy_are_pinned(self):
        task = LocalTaskSource(FIXTURE).load().spec
        adapter = CodexCLIAdapter()
        baseline = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        browser = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
            admitted_capabilities=("native-shell", "workspace-files", "browser"),
            environment_services={
                "browser": "ghostchrome-serve-cli-v1",
                "interactive-session": "disabled",
            },
            browser_allowed_domains=("example.com",),
        )
        browser_other_domain = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
            admitted_capabilities=("native-shell", "workspace-files", "browser"),
            environment_services={
                "browser": "ghostchrome-serve-cli-v1",
                "interactive-session": "disabled",
            },
            browser_allowed_domains=("ctf.example",),
        )
        self.assertNotEqual(baseline.base_hash, browser.base_hash)
        self.assertNotEqual(browser.base_hash, browser_other_domain.base_hash)
        self.assertIn("browser", browser.base_config["capabilities"]["admitted"])
        self.assertEqual(
            browser.base_config["environment_services"]["browser"],
            "ghostchrome-serve-cli-v1",
        )
        self.assertEqual(
            browser.base_config["browser_policy"]["allowed_domains"],
            ["example.com"],
        )
        self.assertEqual(
            browser.base_config["browser_policy"]["profile"],
            "disposable-attempt-local",
        )
        self.assertFalse(browser.base_config["browser_policy"]["cross_attempt_reuse"])

    def test_interactive_session_treatment_is_pinned(self):
        task = LocalTaskSource(FIXTURE).load().spec
        adapter = CodexCLIAdapter()
        baseline = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        session = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="danger-full-access",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
            admitted_capabilities=(
                "native-shell",
                "workspace-files",
                "interactive-session",
            ),
            environment_services={
                "browser": "disabled",
                "interactive-session": "cli-unix-rpc-v1",
            },
        )
        self.assertNotEqual(baseline.base_hash, session.base_hash)
        self.assertIn(
            "interactive-session",
            session.base_config["capabilities"]["admitted"],
        )
        self.assertEqual(
            session.base_config["environment_services"]["interactive-session"],
            "cli-unix-rpc-v1",
        )


if __name__ == "__main__":
    unittest.main()
