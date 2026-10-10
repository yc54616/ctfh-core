from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.cli import main
from ctfh_core.live import run_live_contest
from ctfh_core.live_agent import AgentTurn
from ctfh_core.live_bridge import JsonLineAgentRunner
from ctfh_core.live_events import capture_events, emit, event_lines, publish_exec_event
from ctfh_core.live_store import LiveStore


def report(**overrides):
    return {"outcome": "progress", "note": "확인 완료", "challenges": [],
            "submissions": [], "work": [], **overrides}


def discover(store):
    turn = store.begin_turn(role="discovery")
    store.finish_turn(turn, report(challenges=[
        {"key": key, "title": key.upper(), "url": "https://ctf.example/" + key,
         "state": "open", "site_evidence": ""} for key in ("a", "b")
    ]))
    return store.begin_turn("a", role="solver"), store.begin_turn("b", role="solver")


class LiveEventStoreTests(unittest.TestCase):
    def test_follow_filters_events_and_advances_without_replaying(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example", None, None)
            first, second = discover(store)
            store.record_event(first, "agent.message", "이전 진행")
            stream = event_lines(store, limit=1, challenge="a", follow=True, as_json=True)
            before = json.loads(next(stream))
            count = 0

            def tick(_):
                nonlocal count
                count += 1
                if count == 1:
                    # Another process may append while the viewer is open.
                    with LiveStore(Path(temp), create=False) as writer:
                        writer.record_event(second, "agent.message", "다른 문제")
                elif count == 2:
                    store.record_event(first, "agent.message", "새 진행")
                else:
                    raise AssertionError("viewer failed to find the new matching event")

            try:
                with patch("ctfh_core.live_events.time.sleep", side_effect=tick):
                    after = json.loads(next(stream))
            finally:
                stream.close()
            self.assertGreater(after["id"], before["id"])
            self.assertEqual(after["message"], "새 진행")
            self.assertEqual(after["challenge_key"], "a")
            self.assertEqual(count, 2)
            self.assertEqual(store.require_running_turn(first)["state"], "running")

    def test_retention_redaction_and_cli_do_not_expose_raw_tool_data(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example", None, None)
            first, _ = discover(store)
            with patch("ctfh_core.live_store.EVENT_LIMIT", 4):
                for i in range(8):
                    store.record_event(first, "agent.message", f"단계 {i}")
            self.assertEqual(len(store.events(limit=5000)), 4)
            with capture_events(lambda kind, text: store.record_event(first, kind, text)):
                publish_exec_event({"type": "item.completed", "item": {
                    "type": "reasoning", "text": "PRIVATE_REASONING",
                }})
                publish_exec_event({"type": "item.completed", "item": {
                    "type": "command_execution",
                    "command": "rtk proxy python3 solver.py --password TOOL_SECRET",
                    "aggregated_output": "RAW_OUTPUT_SECRET", "status": "completed", "exit_code": 0,
                }})
                publish_exec_event({"type": "item.completed", "item": {
                    "type": "agent_message", "phase": "commentary",
                    "text": '\x1b[31m첨부 확인\x1b[0m password="TEXT_SECRET" CTF{FLAG_SECRET}',
                }})
                publish_exec_event({"type": "item.completed", "item": {
                    "type": "agent_message", "text": json.dumps(report(note="FINAL_SECRET")),
                }})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["live", "events", "--state", temp, "--challenge", "a", "--json"])
            self.assertEqual(code, 0)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertIn("python3 · completed · exit 0", [r["message"] for r in rows])
            self.assertIn("첨부 확인", output.getvalue())
            for secret in ("PRIVATE_REASONING", "TOOL_SECRET", "RAW_OUTPUT_SECRET", "TEXT_SECRET",
                           "FLAG_SECRET", "FINAL_SECRET", "\x1b"):
                self.assertNotIn(secret, output.getvalue())
            self.assertEqual(store.require_running_turn(first)["state"], "running")

    def test_rejected_report_rolls_back_verdict_events(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example", None, None)
            first, _ = discover(store)
            store.begin_submission("a", "a" * 64, turn_id=first)
            before = store.latest_event_id()
            invalid = report(submissions=[{
                "challenge_key": "a", "flag_sha256": "a" * 64, "verdict": "accepted",
                "site_evidence": "Correct", "observed_url": "https://ctf.example/a",
            }], work=[{"challenge_key": "a", "note": "bad checkpoint", "next_action": "",
                      "retry_after_seconds": -1}])
            with self.assertRaises(ValueError):
                store.finish_turn(first, invalid, browser_observations=("Correct",))
            self.assertEqual(store.latest_event_id(), before)
            self.assertEqual(store.summary()["solved_count"], 0)

    def test_codex_completed_usage_is_recorded_without_raw_event_data(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example", None, None)
            first, second = discover(store)
            with capture_events(
                lambda kind, message: store.record_event(first, kind, message),
                usage_sink=lambda input_count, cached_count, output_count:
                    store.record_token_usage(first, input_count, cached_count, output_count),
            ):
                publish_exec_event({"type": "turn.completed", "usage": {
                    "input_tokens": 1234, "cached_input_tokens": 1000,
                    "output_tokens": 56, "private": "SHOULD_NOT_BE_SAVED",
                }})
                publish_exec_event({"type": "turn.completed", "usage": {
                    "input_tokens": True, "cached_input_tokens": 0, "output_tokens": 99,
                }})
            self.assertEqual(tuple(store.conn.execute(
                "SELECT input_tokens, cached_input_tokens, output_tokens FROM turns WHERE id=?",
                (first,),
            ).fetchone()), (1234, 1000, 56))
            self.assertIsNone(store.conn.execute(
                "SELECT input_tokens FROM turns WHERE id=?", (second,)
            ).fetchone()[0])
            self.assertEqual(store.events(challenge="a")[-1]["kind"], "turn.started")
            with LiveStore(Path(temp), create=False) as reopened:
                self.assertEqual(reopened.conn.execute(
                    "SELECT input_tokens + output_tokens FROM turns WHERE id=?", (first,)
                ).fetchone()[0], 1290)

    def test_events_missing_state_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "missing"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["live", "events", "--state", str(state)]), 2)
            self.assertFalse(state.exists())


class LiveEventAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_progress_is_visible_before_completion_and_survives_restart(self):
        started = {key: asyncio.Event() for key in ("a", "b")}
        stopped = {key: asyncio.Event() for key in ("a", "b")}

        class Runner:
            async def run(self, prompt, thread_id, on_thread_id):
                role = re.search(r"Turn role: (\w+)", prompt).group(1)
                if role == "discovery":
                    on_thread_id("control")
                    return AgentTurn(report(challenges=[
                        {"key": key, "title": key, "url": "https://ctf.example/" + key,
                         "state": "open", "site_evidence": ""} for key in started
                    ]), ())
                key = re.search(r'"assigned_challenge": \{"challenge_key": "([^"]+)"', prompt).group(1)
                on_thread_id("thread-" + key)
                # Force interleaving; a shared mutable callback would misattribute events.
                await asyncio.sleep(0.02 if key == "a" else 0.01)
                emit("agent.message", key + ": 첨부 파일 분석 중")
                started[key].set()
                try:
                    await asyncio.Future()
                finally:
                    stopped[key].set()

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                controller = asyncio.create_task(run_live_contest(
                    state_dir=state, site_url="https://ctf.example", account_label=None,
                    auth_entry=None, model=None, cyber_access_program=None, codex_binary="unused",
                    max_turns=3, idle_seconds=0, scan_seconds=60, turn_timeout=5,
                    stop_when_solved=True, runner=Runner(),
                ))
                try:
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 3)
                    with LiveStore(state, create=False) as viewer:
                        for key in started:
                            messages = [r for r in viewer.events(challenge=key) if r["kind"] == "agent.message"]
                            self.assertEqual([r["message"] for r in messages], [key + ": 첨부 파일 분석 중"])
                            self.assertEqual(viewer.require_running_turn(messages[0]["turn_id"])["state"], "running")
                        viewer.mark_solved("a")
                        await asyncio.wait_for(stopped["a"].wait(), 3)
                        self.assertFalse(stopped["b"].is_set())
                        viewer.mark_solved("b")
                    await asyncio.wait_for(controller, 3)
                finally:
                    if not controller.done():
                        controller.cancel()
                    await asyncio.gather(controller, return_exceptions=True)
            with LiveStore(state, create=False) as reopened:
                for key in started:
                    kinds = [r["kind"] for r in reopened.events(challenge=key)]
                    self.assertIn("agent.message", kinds)
                    self.assertIn("challenge.solved", kinds)
                    self.assertIn("worker.stopped", kinds)

    async def test_external_connection_public_progress_is_forwarded(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "connection.py"
            script.write_text(
                "import json,sys\njson.loads(sys.stdin.readline())\n"
                "print(json.dumps({'type':'thread.started','thread_id':'external'}),flush=True)\n"
                "print(json.dumps({'type':'progress','message':'파일 구조 확인'}),flush=True)\n"
                f"print(json.dumps({{'type':'turn.report','report':{report()!r}}}),flush=True)\n"
            )
            seen = []
            with capture_events(lambda kind, text: seen.append((kind, text))):
                turn = await JsonLineAgentRunner((sys.executable, str(script)), Path(temp), Path(temp), 3).run(
                    "fixture", None, lambda _: None,
                )
            self.assertEqual(turn.report, report())
            self.assertEqual(seen, [("agent.progress", "파일 구조 확인")])


if __name__ == "__main__":
    unittest.main()
