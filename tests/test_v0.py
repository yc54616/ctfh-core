from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.agent import CodexCLIAdapter
from ctfh_core.environment import CancelToken, LocalWorkerEnvironmentProvider
from ctfh_core.kernel import next_host_epoch
from ctfh_core.outcome import LocalOutcomeAuthority
from ctfh_core.runlock import build_runlock
from ctfh_core.store import StateStore
from ctfh_core.target import LocalTargetProvider
from ctfh_core.task_source import LocalTaskSource
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
        target = await LocalTargetProvider().acquire(run_id="r", task_id=self.task.task_id)
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
                store.set_target_epoch("t", 2)
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
            sandbox="workspace-write",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        b = build_runlock(
            treatment="ctfh-supervised",
            task=task,
            adapter=adapter,
            model="gpt-6-sol",
            reasoning_effort="high",
            sandbox="workspace-write",
            deadline_seconds=task.deadline_seconds,
            prompt="same-prompt",
        )
        self.assertEqual(a.base_hash, b.base_hash)
        self.assertNotEqual(a.full_hash, b.full_hash)


if __name__ == "__main__":
    unittest.main()
