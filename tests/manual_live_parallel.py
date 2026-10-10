"""Real Chrome check: simultaneous workers, site solve, and explicit user solve."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfh_core.live import run_live_contest
from ctfh_core.live_browser import capture_browser
from ctfh_core.live_store import LiveStore
from fixtures.live_ctf_site import FixtureServer, FLAGS


async def connection() -> None:
    request = json.loads(sys.stdin.readline())
    assert request["protocol"] == "ctfh.turn.v1"
    snapshot = request["snapshot"]
    state = Path(request["state_dir"])
    turn_id = int(snapshot["turn_id"])
    role = snapshot["turn_role"]
    base = str(snapshot["site_url"]).split("/a/")[0]
    assert base.startswith("http://localhost:"), "this fixture connection is localhost-only"

    def browser(*args):
        return capture_browser(state, turn_id, list(args))

    def report(challenges=None, *, outcome="progress"):
        return {"outcome": outcome, "note": "local fixture observation", "work": [],
                "challenges": challenges or [], "submissions": []}

    def emit_thread(name):
        print(json.dumps({"type": "thread.started", "thread_id": name}), flush=True)

    if role == "discovery":
        emit_thread("parallel-control")
        browser("open", base + "/a/")
        browser("fill", "input[name=username]", "demo")
        browser("fill", "input[name=password]", "demo-pass")
        browser("click", "button[type=submit]", "--observe")
        listing = browser("read")
        assert "Welcome" in listing and "Attachment" in listing
        problems = [{"key": key, "title": title,
                     "url": base + "/a/challenge/" + route,
                     "state": "open", "site_evidence": ""}
                    for key, title, route in (("a-welcome", "Welcome", "welcome"),
                                              ("b-attachment", "Attachment", "attachment"))]
        print(json.dumps({"type": "turn.report", "report": report(problems)}), flush=True)
        return

    if role == "monitor":
        assert request["thread_id"] == "parallel-control"
        browser("open", base + "/a/challenges")
        listing = browser("read")
        solved = []
        for key, title, route in (("a-welcome", "Welcome", "welcome"),
                                  ("b-attachment", "Attachment", "attachment")):
            match = re.search(rf"{title}[^\n]{{0,100}}Solved", listing)
            if match:
                solved.append({"key": key, "title": title,
                               "url": base + "/a/challenge/" + route,
                               "state": "solved", "site_evidence": match.group()})
        print(json.dumps({"type": "turn.report", "report": report(solved, outcome="idle")}), flush=True)
        return

    focus = snapshot["assigned_challenge"]
    assert role == "solver" and focus is not None
    key = focus["challenge_key"]
    emit_thread("parallel-" + key)
    browser("open", focus["url"])
    page = browser("read")
    assert focus["title"] in page
    (state / ("worker-ready-" + key)).write_text("ready\n")
    # The controller must terminate this process group when this one problem is solved.
    while True:
        await asyncio.sleep(60)


async def check(state: Path) -> None:
    if (state / "contest.sqlite3").exists():
        raise ValueError("use a fresh state directory")
    server = FixtureServer(("127.0.0.1", 0))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://localhost:{server.server_port}"
    controller = None
    sessions = []
    try:
        controller = asyncio.create_task(run_live_contest(
            state_dir=state, site_url=base + "/a/", account_label="fixture-parallel",
            auth_entry=None, model=None, cyber_access_program=None,
            codex_binary="must-not-run", max_turns=None,
            idle_seconds=30, scan_seconds=2, turn_timeout=120,
            stop_when_solved=True,
            agent_command=(sys.executable, str(Path(__file__).resolve()), "--connection"),
        ))

        async def until(predicate, timeout=90):
            end = time.monotonic() + timeout
            while not predicate():
                if controller.done():
                    await controller
                    raise AssertionError("controller ended before expected state")
                if time.monotonic() > end:
                    raise TimeoutError("parallel browser integration timed out")
                await asyncio.sleep(0.25)

        await until(lambda: all((state / ("worker-ready-" + key)).exists()
                                for key in ("a-welcome", "b-attachment")))
        with LiveStore(state, create=False) as store:
            sessions = [store.browser_session_for(key)
                        for key in (None, "a-welcome", "b-attachment")]
            assert store.summary()["running_challenges"] == ["a-welcome", "b-attachment"]
            assert store.thread_for("a-welcome") != store.thread_for("b-attachment")

        # A user submits the dummy flag directly on the site in the same account.
        with server.state.lock:
            assert len(server.state.sessions) == 1
            sid = next(iter(server.state.sessions))
        data = urllib.parse.urlencode({"flag": FLAGS["welcome"]}).encode()
        request = urllib.request.Request(
            base + "/a/submit/welcome", data=data,
            headers={"Cookie": "ctfh_fixture=" + sid},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert "Correct!" in response.read().decode()

        def site_reconciled():
            with LiveStore(state, create=False) as store:
                row = store.conn.execute(
                    "SELECT state, solved_source FROM challenges WHERE challenge_key='a-welcome'"
                ).fetchone()
                return row is not None and tuple(row) == ("solved", "site") and (
                    store.summary()["running_challenges"] == ["b-attachment"]
                )
        await until(site_reconciled)
        assert (state / "worker-ready-b-attachment").exists()

        with LiveStore(state, create=False) as store:
            assert store.mark_solved("b-attachment", source="user")
        result = await asyncio.wait_for(controller, 30)
        assert result["solved_count"] == 2 and result["all_solved"]
        assert result["solved_by_user"] == 1
        assert server.state.submission_counts == {"welcome": 1}
        evidence = {"result": "PASS", "workers_started_together": 2,
                    "site_solve_detected": True, "manual_solve_detected": True,
                    "other_worker_kept_running": True,
                    "browser_sessions": "three independent named sessions",
                    "model": "deterministic local connection"}
        (state / "parallel-integration-result.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        print(json.dumps(evidence), flush=True)
    finally:
        if controller is not None and not controller.done():
            controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
        for session in sessions:
            subprocess.run(["rtk", "proxy", "chrome-use", "--session", session, "close"],
                           capture_output=True, timeout=30)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


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
        parser.error("--state is required")
