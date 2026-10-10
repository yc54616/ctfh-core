from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from ctfh_core.cli import main
from ctfh_core.live import run_live_contest
from ctfh_core.live_agent import AgentRunError, AgentTurn
from ctfh_core.live_bridge import JsonLineAgentRunner
from ctfh_core.live_browser import BrowserReceipt, capture_browser, load_receipts, verify_verdicts
from ctfh_core.live_store import LiveStore


def report(challenges=None, submissions=None, work=None):
    return {"outcome": "progress", "note": "checkpoint", "challenges": challenges or [],
            "submissions": submissions or [], "work": work or []}


def challenge(key):
    return {"key": key, "title": key, "url": "https://ctf.example/" + key,
            "state": "open", "site_evidence": ""}


class LiveCoreTests(unittest.TestCase):
    def test_checkpoints_fair_scheduling_and_total_over_prompt_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
                store.finish_turn(store.begin_turn(), report([challenge(f"p{i:03}") for i in range(105)]))
                self.assertEqual(store.summary()["challenge_count"], 105)
                self.assertEqual(len(store.snapshot()["challenges"]), 100)
                self.assertEqual(store.next_challenge()["challenge_key"], "p000")
                turn = store.begin_turn("p000")
                self.assertEqual(store.snapshot()["assigned_challenge"]["challenge_key"], "p000")
                store.finish_turn(turn, report(work=[{
                    "challenge_key": "p000", "note": "Tried A; ruled it out.",
                    "next_action": "Inspect B", "retry_after_seconds": 3600,
                }]))
                store.set_thread_id("old-thread")
                store.reset_thread()
                self.assertEqual(store.next_challenge()["challenge_key"], "p001")
                workspace = store.workspace("../../outside")
                self.assertTrue(workspace.is_relative_to(state / "workspaces"))
            with LiveStore(state) as store:
                row = store.conn.execute("SELECT * FROM challenge_work WHERE challenge_key='p000'").fetchone()
                self.assertEqual(row["note"], "Tried A; ruled it out.")
                self.assertEqual(row["attempts"], 1)
                self.assertIsNone(store.contest()["thread_id"])
                store.begin_submission("p000", "a" * 64)
                self.assertEqual(store.next_challenge()["challenge_key"], "p000")
                self.assertEqual(store.next_challenge()["reconcile"], 1)

    def test_concurrent_candidates_have_one_pending_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
            barrier = threading.Barrier(2)
            def submit(digest):
                with LiveStore(state) as store:
                    barrier.wait(timeout=5)
                    return store.begin_submission("one", digest * 64)
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(submit, ["a", "b"]))
            self.assertEqual(sorted(values), ["new", "pending"])

    def test_receipts_bind_verdict_to_fresh_contest_page(self):
        with tempfile.TemporaryDirectory() as temp:
            with LiveStore(Path(temp)) as store:
                store.ensure_contest("https://ctf.example", None, None)
                turn = store.begin_turn()
                store.begin_submission("one", "a" * 64)
                value = report(submissions=[{
                    "challenge_key": "one", "flag_sha256": "a" * 64,
                    "verdict": "accepted", "site_evidence": "Correct!",
                    "observed_url": "https://ctf.example/result/one",
                }])
                for receipt in (
                    BrowserReceipt(turn, "https://ctf.example/result/one", "Correct!", 0),
                    BrowserReceipt(turn, "https://other.example/result/one", "Correct!", time.time()),
                    BrowserReceipt(turn, "https://ctf.example/result/two", "Correct!", time.time()),
                    BrowserReceipt(turn, "https://ctf.example/result/one", "Incorrect", time.time()),
                ):
                    with self.assertRaises(ValueError):
                        verify_verdicts(store, value, (receipt,))
                verify_verdicts(store, value, (BrowserReceipt(
                    turn, "https://ctf.example/result/one", "Correct!", time.time()),))

    def test_browser_capture_rejects_stale_turn_and_session_override(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
                turn = store.begin_turn()
                with patch("ctfh_core.live_browser.subprocess.run", side_effect=[
                    SimpleNamespace(returncode=0, stdout="Correct!"),
                    SimpleNamespace(returncode=0, stdout="chrome-use update available\n✓ https://ctf.example/result\n"),
                ]) as call:
                    self.assertEqual(capture_browser(state, turn, ["read"]), "Correct!")
                    self.assertEqual(call.call_args_list[0].args[0][-1], "read")
                    self.assertEqual(call.call_args_list[0].args[0][3:5],
                                     ["--session", store.browser_session_for(None)])
                self.assertEqual(len(load_receipts(state, turn)), 1)
                with self.assertRaises(ValueError):
                    capture_browser(state, turn, ["--session-name=other", "read"])
                store.finish_turn(turn, report())
                with self.assertRaises(ValueError):
                    capture_browser(state, turn, ["read"])

    def test_init_and_status_do_not_need_codex_or_create_missing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["live", "status", "--state", str(state)]), 2)
                self.assertFalse(state.exists())
                self.assertEqual(main(["live", "init", "https://ctf.example", "--state", str(state)]), 0)
                self.assertEqual(main(["live", "status", "--details", "--state", str(state)]), 0)

    def test_user_connection_persists_and_resumes_without_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "agent.py"
            script.write_text(
                "import json,sys\n"
                "r=json.loads(sys.stdin.readline())\n"
                "assert r['protocol']=='ctfh.turn.v1'\n"
                "assert r['thread_id'] in (None,'external-1')\n"
                "print(json.dumps({'type':'thread.started','thread_id':'external-1'}))\n"
                "print(json.dumps({'type':'turn.report','report':{'outcome':'idle',\n"
                "'note':'resumed' if r['thread_id'] else 'first','challenges':[],\n"
                "'submissions':[],'work':[]}}))\n", encoding="utf-8")
            options = dict(state_dir=root / "state", site_url="https://ctf.example",
                           account_label=None, auth_entry=None, model=None,
                           cyber_access_program=None, codex_binary="must-not-run",
                           max_turns=1, idle_seconds=0, turn_timeout=10)
            with contextlib.redirect_stdout(io.StringIO()):
                first = asyncio.run(run_live_contest(**options, agent_command=(sys.executable, str(script))))
                options["site_url"] = None
                second = asyncio.run(run_live_contest(**options))
            self.assertEqual(first["last_note"], "first")
            self.assertEqual(second["last_note"], "resumed")
            self.assertEqual(second["thread_id"], "external-1")

    def test_connection_failure_does_not_accept_a_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "agent.py"
            script.write_text(
                "import json,sys\nsys.stdin.read()\n"
                "print(json.dumps({'type':'turn.report','report':{}}))\n"
                "print(json.dumps({'type':'turn.failed','kind':'auth'}))\n", encoding="utf-8")
            with self.assertRaises(AgentRunError) as raised:
                asyncio.run(JsonLineAgentRunner((sys.executable, str(script)), root, root, 5).run(
                    "test", None, lambda _: None))
            self.assertEqual(raised.exception.kind, "auth")

    def test_handoff_stops_controller_until_explicit_resume(self):
        class HandoffRunner:
            calls = 0
            async def run(self, prompt, thread_id, on_thread_id):
                self.calls += 1
                value = report()
                value["outcome"] = "needs_human" if self.calls == 1 else "idle"
                if self.calls == 2:
                    self.assert_resumed = "explicitly completed the prior browser handoff" in prompt
                return AgentTurn(value, ())
        with tempfile.TemporaryDirectory() as temp:
            runner = HandoffRunner()
            options = dict(state_dir=Path(temp), site_url="https://ctf.example",
                           account_label=None, auth_entry=None, model=None, cyber_access_program=None,
                           codex_binary="must-not-run", max_turns=1, idle_seconds=0, turn_timeout=5,
                           runner=runner)
            with contextlib.redirect_stdout(io.StringIO()):
                first = asyncio.run(run_live_contest(**options))
                second = asyncio.run(run_live_contest(**options))
                self.assertEqual(first["state"], "needs_human")
                self.assertEqual(second["state"], "needs_human")
                self.assertEqual(runner.calls, 1)
                third = asyncio.run(run_live_contest(**options, resume_handoff=True))
            self.assertEqual(third["state"], "idle")
            self.assertTrue(runner.assert_resumed)

    def test_connection_timeout_is_terminal_and_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            options = dict(state_dir=root / "state", site_url="https://ctf.example",
                           account_label=None, auth_entry=None, model=None, cyber_access_program=None,
                           codex_binary="must-not-run", max_turns=1, idle_seconds=0, turn_timeout=0.2,
                           agent_command=(sys.executable, "-c", "import time; time.sleep(60)"))
            with self.assertRaises(AgentRunError) as raised:
                asyncio.run(run_live_contest(**options))
            self.assertEqual(raised.exception.kind, "timeout")
            with LiveStore(root / "state") as store:
                self.assertEqual(store.summary()["state"], "failed")
                self.assertEqual(store.summary()["last_error_kind"], "timeout")


if __name__ == "__main__":
    unittest.main()
