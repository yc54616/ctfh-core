"""Small local CTF website for manual browser end-to-end checks.

Run only on localhost. Credentials are deliberately public test data:
demo / demo-pass.
"""

from __future__ import annotations

import argparse
import base64
import html
import secrets
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


FLAGS = {
    "welcome": "CTFH{hello_live_browser}",
    "attachment": "CTFH{decoded_attachment}",
}


class FixtureState:
    def __init__(self) -> None:
        self.sessions: dict[str, set[str]] = {}
        self.submission_counts: dict[str, int] = {}
        self.lock = threading.Lock()


def _page(skin: str, title: str, body: str) -> bytes:
    alt = skin == "b"
    nav = (
        f'<a href="/{skin}/challenges">' + ("Tasks" if alt else "Challenges") + "</a> | "
        f'<a href="/{skin}/scoreboard">' + ("Results" if alt else "Scoreboard") + "</a>"
    )
    layout = "<main class='cards'>" if alt else "<main id='main'>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px sans-serif;max-width:760px;margin:2rem auto}"
        "main{padding:1rem;border:1px solid #aaa}label{display:block;margin:.5rem 0}"
        ".notice{padding:.7rem;background:#def}.cards{display:grid;gap:1rem}</style>"
        f"</head><body><nav>{nav}</nav>{layout}{body}</main></body></html>"
    ).encode()


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    @property
    def state(self) -> FixtureState:
        return self.server.state

    def _send(self, status: int, body: bytes, *, content_type: str = "text/html", cookie: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, *, cookie: str = "") -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _parts(self) -> tuple[str, list[str]]:
        parts = [p for p in urlsplit(self.path).path.split("/") if p]
        skin = parts[0] if parts and parts[0] in {"a", "b"} else "a"
        if parts and parts[0] in {"a", "b"}:
            parts = parts[1:]
        return skin, parts

    def _session(self) -> tuple[str | None, set[str]]:
        raw = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None, set()
        sid = cookie["ctfh_fixture"].value if "ctfh_fixture" in cookie else None
        if sid is None:
            return None, set()
        with self.state.lock:
            solved = self.state.sessions.get(sid)
            return (sid, set(solved)) if solved is not None else (None, set())

    def _form(self) -> dict[str, str]:
        size = min(int(self.headers.get("Content-Length", "0")), 4096)
        data = parse_qs(self.rfile.read(size).decode("utf-8", errors="replace"))
        return {key: values[0] for key, values in data.items() if values}

    def do_GET(self) -> None:
        skin, parts = self._parts()
        sid, solved = self._session()
        if parts == ["health"]:
            self._send(200, b"ok", content_type="text/plain")
            return
        if parts == ["login"] or (sid is None and parts != ["login"]):
            labels = ("User", "Secret", "Enter") if skin == "b" else ("Username", "Password", "Log in")
            body = (
                f"<h1>{'Member access' if skin == 'b' else 'CTF login'}</h1>"
                f"<form method='post' action='/{skin}/login'>"
                f"<label>{labels[0]} <input name='username' autocomplete='username'></label>"
                f"<label>{labels[1]} <input name='password' type='password' autocomplete='current-password'></label>"
                f"<button type='submit'>{labels[2]}</button></form>"
            )
            self._send(200, _page(skin, "Login", body))
            return
        if not parts or parts == ["challenges"]:
            rows = []
            for key, title in (("welcome", "Welcome"), ("attachment", "Attachment")):
                marker = "Solved" if key in solved else "Open"
                rows.append(f"<li><a href='/{skin}/challenge/{key}'>{title}</a> — {marker}</li>")
            self._send(200, _page(skin, "Challenges", "<h1>Challenges</h1><ul>" + "".join(rows) + "</ul>"))
            return
        if parts == ["scoreboard"]:
            names = ", ".join(sorted(solved)) or "none"
            body = f"<h1>Scoreboard</h1><p>Solved: {len(solved)} / 2</p><p>{html.escape(names)}</p>"
            self._send(200, _page(skin, "Scoreboard", body))
            return
        if len(parts) == 2 and parts[0] == "attachment" and parts[1] == "hint.txt":
            clue = base64.b64encode(FLAGS["attachment"].encode()).decode()
            self._send(200, f"Decode this Base64 value: {clue}\n".encode(), content_type="text/plain")
            return
        if len(parts) == 2 and parts[0] == "challenge" and parts[1] in FLAGS:
            key = parts[1]
            description = (
                "Submit the string CTFH{hello_live_browser}."
                if key == "welcome" else
                f"Download <a href='/{skin}/attachment/hint.txt'>hint.txt</a> and decode its Base64 value."
            )
            solved_note = "<p class='notice'>Already solved</p>" if key in solved else ""
            body = (
                f"<h1>{html.escape(key.title())}</h1><p>{description}</p>{solved_note}"
                f"<form method='post' action='/{skin}/submit/{key}'>"
                "<label>Flag <input name='flag'></label>"
                f"<button type='submit'>{'Check answer' if skin == 'b' else 'Submit flag'}</button></form>"
            )
            self._send(200, _page(skin, key.title(), body))
            return
        self._send(404, _page(skin, "Not found", "<h1>Not found</h1>"))

    def do_POST(self) -> None:
        skin, parts = self._parts()
        form = self._form()
        if parts == ["login"]:
            if form.get("username") != "demo" or form.get("password") != "demo-pass":
                self._send(401, _page(skin, "Login failed", "<p>Invalid credentials</p>"))
                return
            sid = secrets.token_urlsafe(24)
            with self.state.lock:
                self.state.sessions[sid] = set()
            self._redirect(f"/{skin}/challenges", cookie=f"ctfh_fixture={sid}; HttpOnly; SameSite=Lax; Path=/")
            return
        sid, solved = self._session()
        if sid is None:
            self._redirect(f"/{skin}/login")
            return
        if len(parts) == 2 and parts[0] == "submit" and parts[1] in FLAGS:
            key = parts[1]
            with self.state.lock:
                self.state.submission_counts[key] = self.state.submission_counts.get(key, 0) + 1
            correct = form.get("flag") == FLAGS[key]
            if correct:
                with self.state.lock:
                    self.state.sessions[sid].add(key)
            result = "Correct!" if correct else "Incorrect flag"
            body = (
                f"<h1>{html.escape(key.title())}</h1><p class='notice'>{result}</p>"
                f"<a href='/{skin}/challenges'>Back to challenges</a>"
            )
            self._send(200, _page(skin, "Flag result", body))
            return
        self._send(404, _page(skin, "Not found", "<h1>Not found</h1>"))


class FixtureServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]):
        super().__init__(address, Handler)
        self.state = FixtureState()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=48176)
    args = parser.parse_args()
    server = FixtureServer(("127.0.0.1", args.port))
    print(f"fixture CTF on http://127.0.0.1:{server.server_port}/a/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
