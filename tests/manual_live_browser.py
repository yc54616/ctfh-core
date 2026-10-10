"""Finite browser check for the local CTF fixture; run by hand, not pytest."""

from __future__ import annotations

import base64
import re
import subprocess
import uuid
from pathlib import Path

from fixtures.live_ctf_site import FixtureServer


def main() -> None:
    server = FixtureServer(("127.0.0.1", 0))
    from threading import Thread

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    session = "ctfh-fixture-" + uuid.uuid4().hex[:10]

    def chrome(*args: str) -> str:
        proc = subprocess.run(
            ["rtk", "proxy", "chrome-use", "--session", session, *args],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=40,
        )
        if proc.returncode:
            raise RuntimeError(f"chrome-use {args[0]} failed: {proc.stderr[-400:]}")
        return proc.stdout

    try:
        chrome("open", base + "/a/")
        chrome("fill", "input[name=username]", "demo")
        chrome("fill", "input[name=password]", "demo-pass")
        chrome("click", "button[type=submit]", "--observe")
        listing = chrome("get", "text", "--main")
        if "Welcome" not in listing or "Attachment" not in listing:
            raise AssertionError("login did not reveal challenge list")

        chrome("open", base + "/a/challenge/welcome")
        chrome("fill", "input[name=flag]", "CTFH{hello_live_browser}")
        chrome("click", "button[type=submit]", "--observe")
        if "Correct!" not in chrome("get", "text", "--main"):
            raise AssertionError("first flag was not accepted")

        chrome("open", base + "/a/attachment/hint.txt")
        hint = chrome("read")
        match = re.search(r"[A-Za-z0-9+/]{16,}={0,2}", hint)
        if match is None:
            raise AssertionError("attachment Base64 was not visible")
        flag = base64.b64decode(match.group()).decode()
        chrome("open", base + "/a/challenge/attachment")
        chrome("fill", "input[name=flag]", flag)
        chrome("click", "button[type=submit]", "--observe")
        if "Correct!" not in chrome("get", "text", "--main"):
            raise AssertionError("attachment flag was not accepted")

        chrome("open", base + "/b/scoreboard")
        board = chrome("get", "text", "--main")
        if "Solved: 2 / 2" not in board:
            raise AssertionError("alternate UI skin did not show both solves")
        print("browser fixture: login, two flags, alternate scoreboard PASS")
    finally:
        try:
            chrome("close")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
