"""Concurrent worker behavior and external solve reconciliation."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.cli import main
from ctfh_core.live import run_live_contest
from ctfh_core.live_agent import AgentTurn
from ctfh_core.live_store import LiveStore


def report(challenges=None):
    return {"outcome": "progress", "note": "site checked", "work": [],
            "challenges": challenges or [], "submissions": []}


class WaitingWorkers:
    def __init__(self, state: Path, *, site_monitor: bool):
        self.state = state
        self.site_monitor = site_monitor
        self.started: dict[str, asyncio.Event] = {
            "a": asyncio.Event(), "b": asyncio.Event(),
        }
        self.monitor_done = asyncio.Event()
        self.cancelled: list[str] = []

    async def run(self, prompt, thread_id, on_thread_id):
        role = re.search(r"Turn role: (\w+)", prompt).group(1)
        if role == "discovery":
            on_thread_id("site-control")
            return AgentTurn(report([
                {"key": key, "title": key.upper(), "url": f"https://ctf.example/challenge/{key}",
                 "state": "open", "site_evidence": ""} for key in ("a", "b")
            ]), ())
        if role == "monitor":
            self.assert_thread(thread_id, "site-control")
            await self.started["a"].wait()
            await self.started["b"].wait()
            turn_id = int(re.search(r"--turn (\d+)", prompt).group(1))
            directory = self.state / "browser-receipts" / str(turn_id)
            directory.mkdir(parents=True)
            import time
            (directory / "site.json").write_text(json.dumps({
                "turn_id": turn_id, "url": "https://ctf.example/challenges",
                "text": "Solved on site: A", "observed_at": time.time(),
            }))
            self.monitor_done.set()
            return AgentTurn(report([{
                "key": "a", "title": "A", "url": "https://ctf.example/challenge/a",
                "state": "solved", "site_evidence": "Solved on site: A",
            }]), ())
        key = re.search(r'"assigned_challenge": \{"challenge_key": "([^"]+)"', prompt).group(1)
        self.assert_thread(thread_id, None)
        on_thread_id(f"worker-{key}")
        self.started[key].set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.append(key)
            raise

    @staticmethod
    def assert_thread(actual, expected):
        if actual != expected:
            raise AssertionError(f"thread mismatch: {actual!r} != {expected!r}")


class ParallelContestTests(unittest.TestCase):
    def options(self, state, runner, **overrides):
        value = dict(state_dir=state, site_url="https://ctf.example/challenges",
                     account_label="me", auth_entry=None, model=None,
                     cyber_access_program=None, codex_binary="must-not-run",
                     max_turns=4 if runner.site_monitor else 3,
                     idle_seconds=0, scan_seconds=0.05 if runner.site_monitor else 30,
                     turn_timeout=5, stop_when_solved=True, runner=runner)
        value.update(overrides)
        return value

    def test_each_solver_uses_full_access_with_plain_codex(self):
        launches = []

        class DirectRunner:
            def __init__(self, *args, **kwargs):
                self.full_access = kwargs["full_access"]
                self.reasoning_effort = kwargs["reasoning_effort"]

            async def run(self, prompt, thread_id, on_thread_id):
                role = re.search(r"Turn role: (\w+)", prompt).group(1)
                launches.append((role, self.full_access, self.reasoning_effort))
                if role == "discovery":
                    return AgentTurn(report([{
                        "key": "p1", "title": "P1", "url": "https://ctf.example/p1",
                        "state": "open", "site_evidence": "",
                    }]), ())
                self.assert_solver_prompt(prompt)
                return AgentTurn({"outcome": "idle", "note": "checkpoint", "work": [],
                                  "challenges": [], "submissions": []}, ())

            @staticmethod
            def assert_solver_prompt(prompt):
                assert "writeup.md" in prompt and "flag.txt" in prompt

        with tempfile.TemporaryDirectory() as temp:
            with patch("ctfh_core.live_parallel.CodexExecRunner", DirectRunner), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(run_live_contest(
                    state_dir=Path(temp), site_url="https://ctf.example/challenges",
                    account_label=None, auth_entry=None, model=None,
                    cyber_access_program=None, codex_binary="codex",
                    max_turns=2, idle_seconds=0, turn_timeout=5,
                    scan_seconds=60, full_access=False, reasoning_effort="xhigh",
                ))
        self.assertEqual(launches, [("discovery", False, "xhigh"),
                                    ("solver", True, "xhigh")])
        self.assertEqual(result["challenge_count"], 1)

    def test_saved_tui_settings_apply_to_the_next_solver_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            launches = []

            class DirectRunner:
                def __init__(self, *args, **kwargs):
                    self.model = kwargs["model"]
                    self.effort = kwargs["reasoning_effort"]

                async def run(self, prompt, thread_id, on_thread_id):
                    role = re.search(r"Turn role: (\w+)", prompt).group(1)
                    if role == "discovery":
                        return AgentTurn(report([{
                            "key": "p1", "title": "P1", "url": "https://ctf.example/p1",
                            "state": "open", "site_evidence": "",
                        }]), ())
                    launches.append((self.model, self.effort))
                    if len(launches) == 1:
                        with LiveStore(state, create=False) as settings:
                            settings.set_model_settings("gpt-6-sol", "xhigh")
                    return AgentTurn({"outcome": "idle", "note": "checkpoint", "work": [],
                                      "challenges": [], "submissions": []}, ())

            with patch("ctfh_core.live_parallel.CodexExecRunner", DirectRunner), \
                 contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(run_live_contest(
                    state_dir=state, site_url="https://ctf.example/challenges",
                    account_label=None, auth_entry=None, model="gpt-6-astra",
                    reasoning_effort="high", cyber_access_program=None,
                    codex_binary="codex", max_turns=3, idle_seconds=0,
                    turn_timeout=5, scan_seconds=60,
                ))
            self.assertEqual(launches, [("gpt-6-astra", "high"),
                                        ("gpt-6-sol", "xhigh")])

    def test_discover_submit_and_save_artifacts_end_to_end(self):
        class SolvingRunner:
            def __init__(self, state):
                self.state = state

            async def run(self, prompt, thread_id, on_thread_id):
                role = re.search(r"Turn role: (\w+)", prompt).group(1)
                on_thread_id("site-control" if role == "discovery" else "solver-p1")
                if role == "discovery":
                    return AgentTurn(report([{
                        "key": "p1", "title": "P1", "url": "https://ctf.example/p1",
                        "state": "open", "site_evidence": "",
                    }]), ())
                turn_id = int(re.search(r"--turn (\d+)", prompt).group(1))
                candidate = b"CTF{accepted}"
                digest = hashlib.sha256(candidate).hexdigest()
                with LiveStore(self.state, create=False) as store:
                    self.assert_new(store.begin_submission("p1", digest, turn_id=turn_id))
                    workspace = store.workspace("p1")
                workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
                (workspace / "flag.txt").write_bytes(candidate)
                (workspace / "writeup.md").write_text("# P1\n\nReproduce and submit the accepted flag.\n")
                os.chmod(workspace / "flag.txt", 0o600)
                os.chmod(workspace / "writeup.md", 0o600)
                receipt_dir = self.state / "browser-receipts" / str(turn_id)
                receipt_dir.mkdir(parents=True)
                (receipt_dir / "accepted.json").write_text(json.dumps({
                    "turn_id": turn_id, "url": "https://ctf.example/p1",
                    "text": "Correct!", "observed_at": time.time(),
                }))
                result = report([{
                    "key": "p1", "title": "P1", "url": "https://ctf.example/p1",
                    "state": "solved", "site_evidence": "Correct!",
                }])
                result["submissions"] = [{
                    "challenge_key": "p1", "flag_sha256": digest,
                    "verdict": "accepted", "site_evidence": "Correct!",
                    "observed_url": "https://ctf.example/p1",
                }]
                return AgentTurn(result, ())

            @staticmethod
            def assert_new(value):
                assert value == "new"

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(run_live_contest(
                    state_dir=state, site_url="https://ctf.example/challenges",
                    account_label=None, auth_entry=None, model=None,
                    cyber_access_program=None, codex_binary="codex",
                    max_turns=2, idle_seconds=0, turn_timeout=5,
                    stop_when_solved=True, scan_seconds=60,
                    runner=SolvingRunner(state),
                ))
            self.assertTrue(result["all_solved"], result)
            self.assertEqual(result["submission_counts"], {"accepted": 1})
            with LiveStore(state, create=False) as store:
                self.assertEqual(store.snapshot()["challenges"][0]["state"], "solved")
                self.assertEqual((store.workspace("p1") / "flag.txt").read_bytes(), b"CTF{accepted}")

    def test_discovery_ignores_unassigned_work_notes_but_records_challenges(self):
        class DiscoveryRunner:
            async def run(self, prompt, thread_id, on_thread_id):
                result = report([{
                    "key": "A", "title": "A", "url": "https://ctf.example/challenges",
                    "state": "unknown", "site_evidence": "",
                }])
                result["work"] = [{
                    "challenge_key": "A", "note": "list note", "next_action": "inspect",
                    "retry_after_seconds": 0,
                }]
                return AgentTurn(result, ())

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(run_live_contest(
                    state_dir=state, site_url="https://ctf.example/challenges",
                    account_label=None, auth_entry=None, model=None,
                    cyber_access_program=None, codex_binary="must-not-run",
                    max_turns=1, idle_seconds=0, turn_timeout=5,
                    runner=DiscoveryRunner(),
                ))
            self.assertEqual(result["challenge_count"], 1)
            with LiveStore(state, create=False) as store:
                self.assertEqual(store.snapshot()["challenges"][0]["note"], "")

    def test_saved_shift_engine_resumes_through_direct_codex_without_resetting_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example/challenges", "me", None)
                with store.conn:
                    store.conn.execute("UPDATE contest SET engine='codex-shift', thread_id='saved-native-thread' WHERE id=1")
            calls = []

            class DirectRunner:
                def __init__(self, *args, **kwargs):
                    calls.append(("launch", kwargs["binary"], kwargs["full_access"]))

                async def run(self, prompt, thread_id, on_thread_id):
                    calls.append(("resume", thread_id))
                    on_thread_id(thread_id)
                    return AgentTurn(report(), ())

            with patch("ctfh_core.live_parallel.CodexExecRunner", DirectRunner), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(run_live_contest(
                    state_dir=state, site_url=None, account_label=None, auth_entry=None,
                    model=None, cyber_access_program=None, codex_binary="codex-secondary",
                    max_turns=1, idle_seconds=0, turn_timeout=5, full_access=True,
                ))
            self.assertEqual(result["engine"], "codex")
            self.assertEqual(calls, [("launch", "codex-secondary", True), ("resume", "saved-native-thread")])
            with LiveStore(state, create=False) as store:
                self.assertEqual(store.contest()["thread_id"], "saved-native-thread")

    def test_manual_solve_stops_only_matching_worker_and_fences_submission(self):
        async def check(state):
            runner = WaitingWorkers(state, site_monitor=False)
            with contextlib.redirect_stdout(io.StringIO()):
                controller = asyncio.create_task(run_live_contest(**self.options(state, runner)))
                await asyncio.wait_for(asyncio.gather(
                    runner.started["a"].wait(), runner.started["b"].wait()), 3)
                with LiveStore(state, create=False) as store:
                    ids = dict(store.conn.execute(
                        "SELECT challenge_key, id FROM turns WHERE role='solver'"
                    ).fetchall())
                    self.assertEqual(set(ids), {"a", "b"})
                    self.assertNotEqual(store.thread_for("a"), store.thread_for("b"))
                    self.assertNotEqual(store.browser_session_for("a"), store.browser_session_for("b"))
                self.assertEqual(main(["live", "mark-solved", "--state", str(state),
                                       "--challenge", "a"]), 0)
                await asyncio.wait_for(self._cancelled(runner, "a"), 3)
                self.assertNotIn("b", runner.cancelled)
                with LiveStore(state, create=False) as store:
                    self.assertEqual(store.summary()["running_challenges"], ["b"])
                    with self.assertRaises(ValueError):
                        store.begin_submission("a", "f" * 64, turn_id=ids["a"])
                    self.assertEqual(store.begin_submission("a", "f" * 64), "solved")
                    self.assertTrue(store.mark_solved("b"))
                result = await asyncio.wait_for(controller, 3)
                self.assertEqual(result["solved_count"], 2)
                self.assertEqual(result["solved_by_user"], 2)
                self.assertEqual(set(runner.cancelled), {"a", "b"})
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(check(Path(temp)))

    def test_site_monitor_marks_external_solve_and_leaves_other_worker_running(self):
        async def check(state):
            runner = WaitingWorkers(state, site_monitor=True)
            with contextlib.redirect_stdout(io.StringIO()):
                controller = asyncio.create_task(run_live_contest(**self.options(state, runner)))
                await asyncio.wait_for(runner.monitor_done.wait(), 3)
                await asyncio.wait_for(self._cancelled(runner, "a"), 3)
                self.assertNotIn("b", runner.cancelled)
                with LiveStore(state, create=False) as store:
                    self.assertEqual(store.summary()["running_challenges"], ["b"])
                    row = store.conn.execute(
                        "SELECT state, solved_source FROM challenges WHERE challenge_key='a'"
                    ).fetchone()
                    self.assertEqual(tuple(row), ("solved", "site"))
                    self.assertTrue(store.mark_solved("b"))
                result = await asyncio.wait_for(controller, 3)
                self.assertEqual(result["solved_count"], 2)
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(check(Path(temp)))

    def test_user_solve_closes_pending_work_without_fabricating_flag_verdict(self):
        with tempfile.TemporaryDirectory() as temp:
            with LiveStore(Path(temp)) as store:
                store.ensure_contest("https://ctf.example/challenges", None, None)
                discovery = store.begin_turn(role="discovery")
                store.finish_turn(discovery, report([{
                    "key": "a", "title": "A", "url": "https://ctf.example/a",
                    "state": "open", "site_evidence": "",
                }]))
                worker = store.begin_turn("a", role="solver")
                digest = "a" * 64
                self.assertEqual(store.begin_submission("a", digest, turn_id=worker), "new")
                self.assertTrue(store.mark_solved("a", source="user"))
                self.assertFalse(store.mark_solved("a", source="user"))
                self.assertTrue(store.all_solved())
                self.assertEqual(store.summary()["pending_submissions"], 0)
                self.assertEqual(store.begin_submission("a", digest), "solved")
                with self.assertRaises(RuntimeError):
                    store.finish_turn(worker, report([{
                        "key": "a", "title": "A", "url": "https://ctf.example/a",
                        "state": "open", "site_evidence": "",
                    }]))
                row = store.conn.execute(
                    "SELECT verdict, resolved_by_solve FROM submissions WHERE challenge_key='a'"
                ).fetchone()
                self.assertEqual(tuple(row), ("submitting", 1))

    def test_invalid_batch_does_not_partly_mark_solved(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
                turn = store.begin_turn(role="discovery")
                store.finish_turn(turn, report([{
                    "key": "a", "title": "A", "url": "https://ctf.example/a",
                    "state": "open", "site_evidence": "",
                }]))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["live", "mark-solved", "--state", str(state),
                             "--challenge", "a", "--challenge", "missing"])
            self.assertEqual(code, 2)
            with LiveStore(state, create=False) as store:
                self.assertEqual(store.snapshot()["challenges"][0]["state"], "open")

    def test_all_discovered_problems_start_even_past_prompt_summary_limit(self):
        class BulkRunner:
            def __init__(self):
                self.site_monitor = False
                self.started = set()
                self.all_started = asyncio.Event()

            async def run(self, prompt, thread_id, on_thread_id):
                role = re.search(r"Turn role: (\w+)", prompt).group(1)
                if role == "discovery":
                    on_thread_id("bulk-control")
                    return AgentTurn(report([{
                        "key": f"p{i:03}", "title": f"P{i:03}",
                        "url": f"https://ctf.example/p{i:03}",
                        "state": "open", "site_evidence": "",
                    } for i in range(105)]), ())
                key = re.search(
                    r'"assigned_challenge": \{"challenge_key": "([^"]+)"', prompt
                ).group(1)
                on_thread_id("bulk-" + key)
                self.started.add(key)
                if len(self.started) == 105:
                    self.all_started.set()
                await asyncio.Future()

        async def check(state):
            runner = BulkRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                controller = asyncio.create_task(run_live_contest(
                    **self.options(state, runner, max_turns=106, scan_seconds=60)
                ))
                await asyncio.wait_for(runner.all_started.wait(), 10)
                self.assertEqual(len(runner.started), 105)
                with LiveStore(state, create=False) as store:
                    self.assertEqual(len(store.summary()["running_challenges"]), 105)
                    for key in sorted(runner.started):
                        store.mark_solved(key)
                result = await asyncio.wait_for(controller, 10)
                self.assertEqual(result["solved_count"], 105)
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(check(Path(temp)))

    @staticmethod
    async def _cancelled(runner, key):
        while key not in runner.cancelled:
            await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
