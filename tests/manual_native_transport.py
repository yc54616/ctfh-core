"""Opt-in real Codex/browser transport check; does not solve or submit a CTF.

Open the official Codex non-interactive documentation in the named chrome-use
session before running this script. It reads only that tab's title twice and
checks native conversation continuity across two CLI processes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfh_core.live_agent import CodexExecRunner  # noqa: E402


async def check(state: Path, session: str, timeout: float) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    thread_file = state / "thread-id.txt"
    if thread_file.exists():
        raise ValueError("use a fresh --state directory for this two-process check")
    marker = "transport-" + secrets.token_hex(8)
    command = (
        "rtk proxy chrome-use --session " + shlex.quote(session) + " get title"
    )
    base_prompt = (
        "This is a read-only browser transport integration test. The named "
        "Chrome session is already on the official Codex non-interactive "
        "documentation. Execute exactly this shell command once: " + command +
        ". Do not navigate, edit files, or use other tools. Return the required "
        "JSON object with outcome idle, empty challenges and submissions arrays, "
        "and the observed title in note. "
    )

    def save_thread(thread_id: str) -> None:
        if thread_file.exists() and thread_file.read_text() != thread_id:
            raise AssertionError("native resume switched the thread")
        thread_file.write_text(thread_id, encoding="utf-8")
        os.chmod(thread_file, 0o600)

    observation_counts = []
    for index in range(2):
        thread_id = thread_file.read_text() if index else None
        prompt = base_prompt + (
            f"Remember this test marker and include it in note: {marker}"
            if index == 0 else
            "Also include the exact test marker from the preceding turn in note."
        )
        runner = CodexExecRunner(ROOT, state, timeout_seconds=timeout)
        turn = await runner.run(prompt, thread_id, save_thread)
        if not any("Non-interactive mode" in value for value in turn.browser_observations):
            raise AssertionError("native browser output was not recognized")
        if marker not in str(turn.report.get("note", "")):
            raise AssertionError("native conversation marker was not retained")
        observation_counts.append(len(turn.browser_observations))
        print(json.dumps({"turn": index + 1, "browser_observations": observation_counts[-1]}), flush=True)
    result = {
        "result": "PASS",
        "scope": "read-only browser title and native thread resume only",
        "thread_id": thread_file.read_text(),
        "browser_observations_per_turn": observation_counts,
    }
    (state / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--session", default="ctfh-transport-check")
    parser.add_argument("--turn-timeout", type=float, default=180)
    args = parser.parse_args()
    asyncio.run(check(args.state.resolve(), args.session, args.turn_timeout))
