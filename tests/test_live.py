from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from ctfh_core.live import run_live_contest
from ctfh_core.live_agent import _browser_observation
from ctfh_core.live_agent import CodexExecRunner, CodexRunError
from ctfh_core.live_store import LiveRunLock, LiveStore


class LiveStoreTests(unittest.TestCase):
    def test_cyber_access_program_is_explicit_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with LiveStore(state) as store:
                contest = store.ensure_contest("https://ctf.example", None, None)
                self.assertIsNone(contest["cyber_access_program"])
                self.assertIsNone(contest["reasoning_effort"])
                updated = store.ensure_contest(
                    None, None, None, model="gpt-6-sol",
                    reasoning_effort="xhigh",
                    cyber_access_program="daybreak_blue",
                )
                self.assertEqual(updated["model"], "gpt-6-sol")
                self.assertEqual(updated["reasoning_effort"], "xhigh")
            with LiveStore(state) as store:
                resumed = store.ensure_contest(None, None, None)
                self.assertEqual(resumed["cyber_access_program"], "daybreak_blue")
                self.assertEqual(resumed["reasoning_effort"], "xhigh")

    def test_only_one_controller_can_own_a_contest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with LiveStore(state):
                pass
            with LiveRunLock(state):
                with self.assertRaisesRegex(ValueError, "already owns"):
                    with LiveRunLock(state):
                        pass
            with LiveRunLock(state):
                pass

    def test_submission_is_journaled_before_result_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            digest = "a" * 64
            with LiveStore(state) as store:
                contest = store.ensure_contest("https://ctf.example/challenges", "me", None)
                self.assertTrue(str(contest["browser_session"]).startswith("ctfh-"))
                store.begin_turn()
                store.set_thread_id("thread-1")
                self.assertEqual(store.begin_submission("web-1", digest), "new")
            with LiveStore(state) as store:
                store.ensure_contest(None, None, None)
                self.assertEqual(store.recover_interrupted_turns(), 1)
                self.assertEqual(store.begin_submission("web-1", digest), "submitting")
                self.assertEqual(store.snapshot()["thread_id"], "thread-1")
                turn = store.begin_turn()
                store.finish_turn(turn, {
                    "outcome": "progress",
                    "note": "Site confirmed correct; move to next problem.",
                    "challenges": [{
                        "key": "web-1", "title": "Web 1",
                        "url": "https://ctf.example/challenges/web-1", "state": "open",
                        "site_evidence": "",
                    }],
                    "submissions": [{
                        "challenge_key": "web-1", "flag_sha256": digest,
                        "verdict": "accepted", "site_evidence": "Correct!",
                        "observed_url": "https://ctf.example/challenges/web-1",
                    }],
                }, browser_observations=("Correct!",))
                self.assertEqual(store.begin_submission("web-1", digest), "accepted")
                self.assertEqual(store.snapshot()["challenges"][0]["state"], "solved")
                self.assertEqual(store.summary()["submission_counts"], {"accepted": 1})
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((state / "contest.sqlite3").stat().st_mode), 0o600)

    def test_unjournaled_verdict_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with LiveStore(Path(temp) / "state") as store:
                store.ensure_contest("https://ctf.example", None, None)
                turn = store.begin_turn()
                with self.assertRaisesRegex(ValueError, "journaled"):
                    store.finish_turn(turn, {
                        "outcome": "progress", "note": "",
                        "challenges": [{
                            "key": "x", "title": "X", "url": "https://ctf.example/x",
                            "state": "open", "site_evidence": "",
                        }],
                        "submissions": [{
                            "challenge_key": "x", "flag_sha256": "b" * 64,
                            "verdict": "accepted", "site_evidence": "Correct",
                            "observed_url": "https://ctf.example/x",
                        }],
                    }, browser_observations=("Correct",))
                self.assertEqual(store.snapshot()["challenges"], [])
                self.assertEqual(store.snapshot()["submission_counts"], {})

    def test_handoff_requires_explicit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with LiveStore(Path(temp) / "state") as store:
                store.ensure_contest("https://ctf.example", None, None)
                turn = store.begin_turn()
                store.finish_turn(turn, {
                    "outcome": "needs_human", "note": "Complete hardware-key touch.",
                    "challenges": [], "submissions": [],
                })
                self.assertEqual(store.snapshot()["state"], "needs_human")
                store.resume_handoff()
                self.assertEqual(store.snapshot()["state"], "active")

    def test_verdict_requires_phrase_from_browser_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with LiveStore(Path(temp) / "state") as store:
                store.ensure_contest("https://ctf.example", None, None)
                digest = "c" * 64
                store.begin_submission("crypto-1", digest)
                turn = store.begin_turn()
                report = {
                    "outcome": "progress", "note": "Site response checked.",
                    "challenges": [],
                    "submissions": [{
                        "challenge_key": "crypto-1", "flag_sha256": digest,
                        "verdict": "accepted", "site_evidence": "Correct!",
                        "observed_url": "https://ctf.example/challenges/crypto-1",
                    }],
                }
                with self.assertRaisesRegex(ValueError, "browser evidence"):
                    store.finish_turn(turn, report, browser_observations=("Incorrect",))
                self.assertEqual(store.snapshot()["submission_counts"], {"submitting": 1})
                store.finish_turn(turn, report, browser_observations=("Correct!",))
                self.assertEqual(store.snapshot()["submission_counts"], {"accepted": 1})


class NativeCodexLoopTests(unittest.TestCase):
    def test_missing_cli_is_a_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with self.assertRaises(CodexRunError) as raised:
                asyncio.run(CodexExecRunner(
                    state, state, binary=str(state / "missing-codex"),
                ).run("Transport test", None, lambda _: None))
            self.assertEqual(raised.exception.kind, "launch_failed")
            self.assertEqual(list(state.glob("turn-*.json")), [])

    def test_safety_rejection_is_classified_and_state_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            fake = root / "fake-codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "print(json.dumps({'type':'thread.started','thread_id':'thread-safety'}))\n"
                "print(json.dumps({'type':'error','message':\n"
                "  'This content was flagged for possible cybersecurity risk.'}))\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            with self.assertRaises(CodexRunError) as raised:
                asyncio.run(run_live_contest(
                    state_dir=state, site_url="https://ctf.example",
                    account_label=None, auth_entry=None, model=None,
                    cyber_access_program=None, codex_binary=str(fake),
                    max_turns=1, idle_seconds=0, turn_timeout=10,
                ))
            self.assertEqual(raised.exception.kind, "cyber_safety")
            with LiveStore(state) as store:
                self.assertEqual(store.summary()["last_error_kind"], "cyber_safety")
                self.assertEqual(store.summary()["state"], "failed")
                self.assertEqual(store.snapshot()["thread_id"], "thread-safety")

    def test_only_browser_events_supply_verdict_evidence(self) -> None:
        phrase = "Correct!"
        self.assertIsNone(_browser_observation({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "rtk proxy printf Correct!", "aggregated_output": phrase},
        }))
        self.assertEqual(_browser_observation({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "rtk proxy chrome-use snapshot -i",
                "aggregated_output": phrase,
            },
        }), phrase)

    def test_shell_wrapped_browser_commands_and_failed_observations(self) -> None:
        for command in (
            "/bin/zsh -lc 'rtk proxy chrome-use --session demo read'",
            ["/bin/bash", "-lc", "rtk proxy chrome-use snapshot -i"],
            ["rtk", "proxy", "chrome-use", "get", "title"],
        ):
            with self.subTest(command=command):
                event = {"type": "item.completed", "item": {
                    "type": "command_execution", "command": command,
                    "exit_code": 0, "aggregated_output": "Observed title",
                }}
                self.assertEqual(_browser_observation(event), "Observed title")
                event["item"]["exit_code"] = 1
                self.assertIsNone(_browser_observation(event))
        for command in (
            "rtk proxy chrome-use read; printf Correct",
            "/bin/zsh -lc 'rtk proxy chrome-use read && printf Correct'",
            "rtk proxy chrome-use read\nprintf Correct",
            "rtk proxy chrome-use read | cat",
        ):
            with self.subTest(command=command):
                self.assertIsNone(_browser_observation({
                    "type": "item.completed", "item": {
                        "type": "command_execution", "command": command,
                        "aggregated_output": "Correct",
                    },
                }))

    def test_long_event_stream_retains_late_browser_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            fake = state / "fake-codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json,pathlib,sys\n"
                "a=sys.argv[1:]\n"
                "sys.stdin.read()\n"
                "pathlib.Path(a[a.index('-o')+1]).write_text(json.dumps({\n"
                "'outcome':'idle','note':'','challenges':[],'submissions':[]}))\n"
                "print('[]')\n"
                "print(json.dumps({'type':'thread.started','thread_id':'thread-events'}))\n"
                "for n in range(300):\n"
                " print(json.dumps({'type':'item.completed','item':{\n"
                "  'type':'command_execution',\n"
                "  'command':\"/bin/zsh -lc 'rtk proxy chrome-use read'\",\n"
                "  'exit_code':0,'aggregated_output':f'Observation {n}'}}),\n"
                "  end='\\n' if n < 299 else '')\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            threads = []
            result = asyncio.run(CodexExecRunner(
                state, state, binary=str(fake), timeout_seconds=10,
            ).run("Transport test", None, threads.append))
            self.assertEqual(threads, ["thread-events"])
            self.assertEqual(len(result.browser_observations), 256)
            self.assertEqual(result.browser_observations[-1], "Observation 299")
            self.assertNotIn("Observation 0", result.browser_observations)

    def test_failed_turn_event_overrides_zero_exit_and_early_stdin_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            fake = state / "fake-codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os\n"
                "os.close(0)\n"
                "print(json.dumps({'type':'turn.failed','error':{\n"
                "'message':'This content was flagged for possible cybersecurity risk.'}}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            with self.assertRaises(CodexRunError) as raised:
                asyncio.run(CodexExecRunner(
                    state, state, binary=str(fake), timeout_seconds=10,
                ).run("Transport test " * 100_000, None, lambda _: None))
            self.assertEqual(raised.exception.kind, "cyber_safety")

    def test_native_thread_resumes_and_tool_output_is_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            fake = root / "fake-codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "a = sys.argv[1:]\n"
                "resume = 'resume' in a\n"
                "pathlib.Path(a[a.index('-o')+1]).write_text(json.dumps({\n"
                "  'outcome': 'idle' if resume else 'progress',\n"
                "  'note': 'Nothing new' if resume else 'Found challenge',\n"
                "  'challenges': [] if resume else [{'key':'web-1','title':'Web 1',\n"
                "     'url':'https://ctf.example/web-1','state':'open','site_evidence':''}],\n"
                "  'submissions': []}))\n"
                "print(json.dumps({'type':'thread.started','thread_id':'thread-live-1'}))\n"
                "print(json.dumps({'type':'item.completed','item':{\n"
                "  'type':'command_execution',\n"
                "  'command':'rtk proxy chrome-use snapshot -i',\n"
                "  'aggregated_output':'flag{secret}'}}))\n"
                "pathlib.Path(__file__).with_suffix('.args').write_text(' '.join(a))\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                first = asyncio.run(run_live_contest(
                    state_dir=state, site_url="https://ctf.example",
                    account_label="me", auth_entry=None, model=None,
                    cyber_access_program=None,
                    codex_binary=str(fake), max_turns=1, idle_seconds=0,
                    turn_timeout=10,
                ))
                result = asyncio.run(run_live_contest(
                    state_dir=state, site_url=None,
                    account_label=None, auth_entry=None, model=None,
                    cyber_access_program="standard",
                    codex_binary=str(fake), max_turns=1, idle_seconds=0,
                    turn_timeout=10,
                ))
            self.assertEqual(first["thread_id"], "thread-live-1")
            self.assertEqual(result["thread_id"], "thread-live-1")
            self.assertEqual(result["challenge_count"], 1)
            self.assertEqual(result["state"], "idle")
            args = fake.with_suffix(".args").read_text()
            self.assertIn("exec resume thread-live-1", args)
            self.assertIn("--cyber-access-program standard", args)
            self.assertNotIn("--ephemeral", args)
            self.assertNotIn("--ignore-user-config", args)
            self.assertNotIn("flag{secret}", out.getvalue())
            with LiveStore(state, create=False) as viewer:
                self.assertEqual(
                    [row["message"] for row in viewer.events() if row["kind"] == "tool.completed"],
                    ["chrome-use snapshot", "chrome-use snapshot"],
                )
            for path in state.iterdir():
                if path.is_file():
                    self.assertNotIn(b"flag{secret}", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
