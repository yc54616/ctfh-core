from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ctfh_core.cli import main
from ctfh_core.live_agent import AgentRunError, AgentTurn
from ctfh_core.live_events import emit
from ctfh_core.live_start import ConsoleInput, ConsoleView, choose_state, last_state, open_contest_browser, start_contest
from ctfh_core.live_store import LiveStore


def report(**overrides):
    return {"outcome": "idle", "note": "fixture", "challenges": [], "submissions": [], "work": [], **overrides}


class InputFixture:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.reads = 0

    async def read_line(self):
        self.reads += 1
        return await self.queue.get()

    def discard_pending(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    async def waiting(self, count):
        async with asyncio.timeout(3):
            while self.reads < count:
                await asyncio.sleep(0.01)


class IdleRunner:
    def __init__(self):
        self.previous = []

    async def run(self, prompt, thread_id, on_thread_id):
        self.previous.append(thread_id)
        on_thread_id(thread_id or "fixture-native-thread")
        emit("agent.message", "실시간 시험 진행")
        return AgentTurn(report(), ())


class StartRouteTests(unittest.TestCase):
    def test_browser_preflight_uses_saved_session(self):
        with patch("ctfh_core.live_start.subprocess.run") as run:
            run.return_value.returncode = 0
            open_contest_browser("contest-session", "https://ctf.example/challenges")
            self.assertEqual(run.call_args.args[0], [
                "rtk", "proxy", "chrome-use", "--session", "contest-session",
                "open", "https://ctf.example/challenges",
            ])
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, "Chrome"):
                open_contest_browser("contest-session", "https://ctf.example/challenges")

    def test_explicit_start_keeps_previous_live_flow(self):
        with patch("ctfh_core.live_start.start_contest", new_callable=AsyncMock) as start:
            start.return_value = {"state": "idle"}
            self.assertEqual(main(["start", "https://ctf.example", "--max-turns", "1"]), 0)
            self.assertEqual(start.call_args.kwargs["site"], "https://ctf.example")
            self.assertEqual(start.call_args.kwargs["max_turns"], 1)
            self.assertEqual(main(["start"]), 0)
            self.assertIsNone(start.call_args.kwargs["site"])

    def test_start_and_shortcut_binary_arguments_reach_controller(self):
        with patch("ctfh_core.live_start.start_contest", new_callable=AsyncMock) as start:
            start.return_value = {"state": "idle"}
            for argv in (["start", "https://ctf.example", "--codex", "codex", "--secondary"],
                         ["start", "--codex", "codex", "--secondary"],
                         ["start", "--secondary"],
                         ["start", "--codex", "codex --secondary"],
                         ["start", "--codex", "codex-secondary"]):
                self.assertEqual(main(argv), 0)
                self.assertEqual(start.call_args.kwargs["codex_binary"], "codex-secondary")

    def test_state_selection_reuses_existing_progress_and_separates_sites(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / ".ctfh-live"
            old = base / "prior-explicit-name"
            with LiveStore(old) as store:
                store.ensure_contest("https://ctf.example/challenges", "my-account", None)
            self.assertEqual(choose_state(base, "https://ctf.example/challenges", None), old)
            self.assertNotEqual(choose_state(base, "https://ctf.example/challenges", "second-account"), old)
            self.assertNotEqual(choose_state(base, "https://other.example", None), old)
            (base / "last.json").write_text(json.dumps({"state": "../outside"}))
            with self.assertRaises(ValueError):
                last_state(base)

    def test_invalid_solve_batch_does_not_partly_mark_problems(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example", None, None)
            turn = store.begin_turn(role="discovery")
            store.finish_turn(turn, report(challenges=[{
                "key": "a", "title": "A", "url": "https://ctf.example/a", "state": "open", "site_evidence": "",
            }]))
            with contextlib.redirect_stdout(io.StringIO()):
                view = ConsoleView(store)
                self.assertTrue(view.command("완료 1 999"))
            self.assertEqual(store.summary()["solved_count"], 0)


class StartFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_opens_chrome_before_native_turn(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            order = []
            async def run_turn(**_kwargs):
                order.append("model")
                return {"state": "idle", "solved_count": 0, "challenge_count": 0}
            with patch("ctfh_core.live_start.check_local_commands"), \
                    patch("ctfh_core.live_start.open_contest_browser") as browser, \
                    patch("ctfh_core.live_start.run_live_contest", side_effect=run_turn):
                browser.side_effect = lambda *_args: order.append("chrome")
                await start_contest(root=Path(temp), site="https://ctf.example/challenges",
                                    interactive=False, max_turns=1)
            self.assertEqual(order, ["chrome", "model"])
            self.assertEqual(browser.call_args.args[1], "https://ctf.example/challenges")

    async def test_console_passes_explicit_binary_without_editing_default_profile(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp)
            config = '[native]\nbinary="codex-secondary"\ndangerously_bypass_approvals_and_sandbox=true\n'
            (root / "ctfh.toml").write_text(config)
            with patch("ctfh_core.live_start.run_live_contest", new_callable=AsyncMock) as run:
                run.return_value = {"state": "idle", "solved_count": 0, "challenge_count": 0}
                await start_contest(root=root, site="https://ctf.example", codex_binary="codex",
                                    runner=IdleRunner(), interactive=False, max_turns=1)
                self.assertEqual(run.await_args.kwargs["codex_binary"], "codex")
                self.assertFalse(run.await_args.kwargs["full_access"])
            self.assertEqual((root / "ctfh.toml").read_text(), config)

    async def test_auto_engine_inline_events_and_no_argument_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = IdleRunner()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                first = await start_contest(root=root, site="https://ctf.example", runner=runner,
                                            max_turns=1, interactive=False)
                second = await start_contest(root=root, runner=runner, max_turns=1, interactive=False)
            self.assertEqual(first["engine"], "codex")
            self.assertEqual(second["thread_id"], first["thread_id"])
            self.assertEqual(runner.previous, [None, "fixture-native-thread"])
            self.assertIn("실시간 시험 진행", output.getvalue())
            self.assertNotIn('"challenge_count":', output.getvalue())
            state = last_state(root / ".ctfh-live")
            self.assertTrue((state / "contest.sqlite3").is_file())

    async def test_no_argument_first_run_asks_only_for_site(self):
        with tempfile.TemporaryDirectory() as temp:
            console = InputFixture()
            with contextlib.redirect_stdout(io.StringIO()):
                task = asyncio.create_task(start_contest(root=Path(temp), console=console,
                                                        runner=IdleRunner(), max_turns=1))
                try:
                    await console.waiting(1)
                    await console.queue.put("https://ctf.example")
                    result = await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(result["state"], "idle")

    async def test_numbered_manual_solve_stops_only_selected_parallel_worker(self):
        started = {key: asyncio.Event() for key in ("a", "b")}
        stopped = {key: asyncio.Event() for key in started}

        class Runner:
            async def run(self, prompt, thread_id, on_thread_id):
                if "Turn role: discovery" in prompt:
                    on_thread_id("control")
                    return AgentTurn(report(challenges=[{
                        "key": key, "title": key.upper(), "url": "https://ctf.example/" + key,
                        "state": "open", "site_evidence": "",
                    } for key in started]), ())
                key = re.search(r'"assigned_challenge": \{"challenge_key": "([^"]+)"', prompt).group(1)
                on_thread_id("thread-" + key)
                emit("agent.message", key + " 풀이 중")
                started[key].set()
                try:
                    await asyncio.Future()
                finally:
                    stopped[key].set()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            console = InputFixture()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                task = asyncio.create_task(start_contest(root=root, site="https://ctf.example", console=console,
                                                        runner=Runner(), max_turns=3))
                try:
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 3)
                    await console.queue.put("완료 1")
                    await asyncio.wait_for(stopped["a"].wait(), 3)
                    self.assertFalse(stopped["b"].is_set())
                    with LiveStore(last_state(root / ".ctfh-live"), create=False) as store:
                        self.assertEqual(store.summary()["solved_count"], 1)
                    await console.queue.put("완료 2")
                    result = await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(result["solved_count"], 2)
            self.assertIn("[1] A", output.getvalue())
            self.assertIn("[문제 2]", output.getvalue())

    async def test_login_handoff_waits_for_fresh_enter_then_resumes_same_thread(self):
        class LoginRunner:
            def __init__(self):
                self.prompts = []

            async def run(self, prompt, thread_id, on_thread_id):
                self.prompts.append(prompt)
                on_thread_id("login-thread")
                return AgentTurn(report(outcome="needs_human" if len(self.prompts) == 1 else "idle",
                                        note="브라우저 로그인 필요"), ())

        with tempfile.TemporaryDirectory() as temp:
            console, runner = InputFixture(), LoginRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                task = asyncio.create_task(start_contest(root=Path(temp), site="https://ctf.example",
                                                        console=console, runner=runner, max_turns=1))
                try:
                    await console.waiting(2)
                    self.assertEqual(len(runner.prompts), 1)
                    await console.queue.put("")
                    result = await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(result["thread_id"], "login-thread")
            self.assertEqual(result["state"], "idle")
            self.assertIn("The user has explicitly completed", runner.prompts[1])

    async def test_noninteractive_handoff_does_not_auto_resume(self):
        class Runner:
            def __init__(self):
                self.calls = 0

            async def run(self, prompt, thread_id, on_thread_id):
                self.calls += 1
                on_thread_id("login-thread")
                return AgentTurn(report(outcome="needs_human"), ())

        with tempfile.TemporaryDirectory() as temp:
            runner = Runner()
            with contextlib.redirect_stdout(io.StringIO()):
                result = await start_contest(root=Path(temp), site="https://ctf.example", runner=runner,
                                             interactive=False, max_turns=1)
                again = await start_contest(root=Path(temp), runner=runner, interactive=False, max_turns=1)
            self.assertEqual(runner.calls, 1)
            self.assertEqual(result["state"], "needs_human")
            self.assertEqual(again["state"], "needs_human")

    async def test_connection_failure_is_not_retried_with_another_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("ctfh_core.live_start.shutil.which", return_value="/fixture/command"), \
                    patch("ctfh_core.live_start.open_contest_browser"):  # never the user's real Chrome
                with patch("ctfh_core.live_start.run_live_contest", new_callable=AsyncMock) as run:
                    run.side_effect = AgentRunError("fixture failed", kind="connection_failed")
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(AgentRunError):
                            await start_contest(root=Path(temp), site="https://ctf.example", interactive=False)
                    self.assertEqual(run.await_count, 1)
            with LiveStore(last_state(Path(temp) / ".ctfh-live"), create=False) as store:
                self.assertEqual(store.summary()["engine"], "codex")

    async def test_console_input_can_be_cancelled_and_closed_without_a_thread(self):
        read_fd, write_fd = os.pipe()
        try:
            with patch("ctfh_core.live_start.sys.stdin") as stdin:
                stdin.fileno.return_value = read_fd
                console = ConsoleInput()
            try:
                os.write(write_fd, "목록\n완료 2\n".encode())
                self.assertEqual(await asyncio.wait_for(console.read_line(), 1), "목록")
                self.assertEqual(await asyncio.wait_for(console.read_line(), 1), "완료 2")
                waiting = asyncio.create_task(console.read_line())
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
                os.close(write_fd)
                write_fd = None
                self.assertIsNone(await asyncio.wait_for(console.read_line(), 1))
            finally:
                console.close()
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
