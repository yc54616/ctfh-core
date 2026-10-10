"""Real browser + full contest controller check with a deterministic fixture agent.

Uses only a localhost test website and public dummy credentials. No model is
connected. This tests orchestration/recovery, not autonomous solve capability.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfh_core.live import run_live_contest
from ctfh_core.live_agent import AgentTurn, AgentRunError
from ctfh_core.live_browser import capture_browser
from ctfh_core.live_store import LiveStore
from fixtures.live_ctf_site import FixtureServer, FLAGS


class FixtureAgent:
    def __init__(self, state: Path, base: str, crash_after_submit: bool):
        self.state, self.base = state, base
        self.crash_after_submit = crash_after_submit

    async def run(self, prompt, thread_id, on_thread_id):
        on_thread_id(thread_id or "fixture-core-session")
        with LiveStore(self.state) as store:
            snapshot = store.snapshot()
            turn = int(store.conn.execute("SELECT id FROM turns WHERE state='running'").fetchone()[0])

        async def browser(*args):
            command = ["rtk", "proxy", "env", f"PYTHONPATH={ROOT / 'src'}", sys.executable,
                       "-m", "ctfh_core", "live", "browser", "--state", str(self.state),
                       "--turn", str(turn), "--", *args]
            proc = await asyncio.to_thread(subprocess.run, command, capture_output=True,
                                           text=True, timeout=125)
            if proc.returncode:
                raise RuntimeError("fixture browser helper failed: " + proc.stderr[-300:])
            return proc.stdout

        value = {"outcome": "progress", "note": "Fixture checkpoint", "challenges": [],
                 "submissions": [], "work": []}
        if not snapshot["challenges"]:
            await browser("open", self.base + "/a/")
            await browser("fill", "input[name=username]", "demo")
            await browser("fill", "input[name=password]", "demo-pass")
            await browser("click", "button[type=submit]", "--observe")
            listing = await browser("read")
            assert "Welcome" in listing and "Attachment" in listing
            for key, name, skin in (("a-welcome", "welcome", "a"), ("b-attachment", "attachment", "b")):
                value["challenges"].append({"key": key, "title": name,
                    "url": f"{self.base}/{skin}/challenge/{name}", "state": "open", "site_evidence": ""})
            return AgentTurn(value, ())

        focus = snapshot["assigned_challenge"]
        assert focus is not None
        key, page = focus["challenge_key"], focus["url"]
        await browser("open", page)
        text = await browser("read")
        pending = [item for item in snapshot["pending_submissions"] if item["challenge_key"] == key]
        if pending:
            assert "Already solved" in text
            value["submissions"] = [{"challenge_key": key, "flag_sha256": pending[0]["flag_sha256"],
                "verdict": "accepted", "site_evidence": "Already solved", "observed_url": page}]
            value["note"] = "Recovered accepted submission without replaying it"
            return AgentTurn(value, ())

        if key == "a-welcome":
            flag = re.search(r"CTFH\{[^}]+\}", text).group()
        elif not snapshot["rejected_candidates"]:
            flag = "CTFH{fixture_wrong}"
        else:
            await browser("open", self.base + "/b/attachment/hint.txt")
            hint = await browser("read")
            encoded = re.search(r"[A-Za-z0-9+/]{16,}={0,2}", hint).group()
            flag = base64.b64decode(encoded).decode()
            await browser("open", page)
        digest = hashlib.sha256(flag.encode()).hexdigest()
        with LiveStore(self.state) as store:
            assert store.begin_submission(key, digest) == "new"
        await browser("fill", "input[name=flag]", flag)
        await browser("click", "button[type=submit]", "--observe")
        result = await browser("read")
        if self.crash_after_submit and key == "a-welcome":
            raise RuntimeError("fixture: report lost after site accepted submission")
        correct = "Correct!" in result
        assert correct or "Incorrect flag" in result
        name = "welcome" if key == "a-welcome" else "attachment"
        skin = "a" if key == "a-welcome" else "b"
        value["submissions"] = [{"challenge_key": key, "flag_sha256": digest,
            "verdict": "accepted" if correct else "rejected",
            "site_evidence": "Correct!" if correct else "Incorrect flag",
            "observed_url": f"{self.base}/{skin}/submit/{name}"}]
        value["work"] = [{"challenge_key": key, "note": "Site accepted" if correct else "Wrong candidate rejected",
            "next_action": "Next challenge" if correct else "Read and decode the attachment", "retry_after_seconds": 0}]
        return AgentTurn(value, ())


async def check(state: Path):
    if (state / "contest.sqlite3").exists():
        raise ValueError("use a fresh state directory")
    server = FixtureServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    options = dict(state_dir=state, site_url=base + "/a/", account_label="fixture",
                   auth_entry=None, model=None, cyber_access_program=None,
                   codex_binary="must-not-run", idle_seconds=0, turn_timeout=120,
                   stop_when_solved=True, parallel=False,
                   agent_command=(sys.executable, str(Path(__file__).resolve()), "--connection"))
    session = None
    try:
        try:
            await run_live_contest(**options, max_turns=2)
        except AgentRunError as exc:
            if exc.kind != "agent_failed" or not (state / "fixture-crash-once").exists():
                raise
        else:
            raise AssertionError("expected simulated lost report")
        with LiveStore(state) as store:
            session = str(store.contest()["browser_session"])
            assert store.summary()["pending_submissions"] == 1
            assert store.summary()["state"] == "failed"
        assert not list((state / "browser-receipts").glob("*/*.json"))
        options["site_url"] = None
        # Reconnect using only persisted state, including the same connection command.
        options["agent_command"] = None
        result = await run_live_contest(**options, max_turns=6)
        assert result["solved_count"] == 2 and result["all_solved"]
        assert result["submission_counts"] == {"accepted": 2, "rejected": 1}
        assert result["pending_submissions"] == 0
        assert server.state.submission_counts == {"welcome": 1, "attachment": 2}
        with LiveStore(state) as store:
            assert store.begin_submission("a-welcome", "f" * 64) == "solved"
            verify_turn = store.begin_turn()
        await asyncio.to_thread(capture_browser, state, verify_turn, ["open", base + "/b/scoreboard"])
        board = await asyncio.to_thread(capture_browser, state, verify_turn, ["read"])
        assert "Solved: 2 / 2" in board
        with LiveStore(state) as store:
            store.finish_turn(verify_turn, {"outcome": "idle", "note": "Scoreboard checked",
                                           "challenges": [], "submissions": [], "work": []})
        from ctfh_core.live_browser import clear_receipts
        clear_receipts(state)
        assert not any(flag.encode() in (state / "contest.sqlite3").read_bytes() for flag in FLAGS.values())
        evidence = {"result": "PASS", "scope": "external JSONL fixture connection, real Chrome, no model",
                    "solved": 2, "accepted": 2, "rejected": 1, "replayed_submissions": 0,
                    "restart_recovered": True, "alternate_ui": True, "connection_protocol": "ctfh.turn.v1"}
        (state / "integration-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence), flush=True)
    finally:
        if session is None and (state / "contest.sqlite3").exists():
            with LiveStore(state) as store:
                session = str(store.contest()["browser_session"])
        if session:
            subprocess.run(["rtk", "proxy", "chrome-use", "--session", session, "close"],
                           capture_output=True, timeout=30)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def connection():
    request = json.loads(sys.stdin.readline())
    assert request["protocol"] == "ctfh.turn.v1"
    state = Path(request["state_dir"])
    parsed = urlsplit(request["snapshot"]["site_url"])
    assert parsed.hostname == "127.0.0.1", "fixture connection is localhost-only"
    assert request["snapshot"]["turn_id"] is not None
    agent = FixtureAgent(state, f"{parsed.scheme}://{parsed.netloc}",
                         not (state / "fixture-crash-once").exists())
    def started(thread_id):
        print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    try:
        turn = await agent.run(request["prompt"], request["thread_id"], started)
    except RuntimeError as exc:
        if str(exc) != "fixture: report lost after site accepted submission":
            raise
        (state / "fixture-crash-once").write_text("simulated lost report\n")
        print(json.dumps({"type": "turn.failed", "kind": "agent_failed"}), flush=True)
        return
    print(json.dumps({"type": "turn.report", "report": turn.report}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--connection", action="store_true")
    args = parser.parse_args()
    if args.connection:
        asyncio.run(connection())
    elif args.state:
        asyncio.run(check(args.state.resolve()))
    else:
        parser.error("--state is required for the integration check")
