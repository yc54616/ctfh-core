"""Single-screen contest launch, observation, manual solves, and login handoff."""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .live import run_live_contest
from .live_events import EVENT_LIMIT, public_text
from .live_store import LiveRunLock, LiveStore, validate_site_url
from .live_config import native_launch


class ConsoleInput:
    """Cancelable terminal input; no input() executor thread left waiting at exit."""

    def __init__(self):
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.fd = sys.stdin.fileno()
        self.loop = asyncio.get_running_loop()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""
        self.closed = False
        self.loop.add_reader(self.fd, self._ready)

    def _ready(self):
        try:
            chunk = os.read(self.fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            self.close()
            self.queue.put_nowait(None)
            return
        self.buffer += self.decoder.decode(chunk)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.queue.put_nowait(line.rstrip("\r"))

    async def read_line(self) -> str | None:
        if self.closed and self.queue.empty():
            return None
        return await self.queue.get()

    def discard_pending(self):
        self.buffer = ""
        while not self.queue.empty():
            self.queue.get_nowait()

    def close(self):
        self.loop.remove_reader(self.fd)
        self.closed = True


def _contained_state(base: Path, value: str) -> Path:
    state = (base / value).resolve()
    if state == base or not state.is_relative_to(base):
        raise ValueError("마지막 대회 경로가 올바르지 않습니다. 사이트 주소를 다시 지정하세요.")
    return state


def last_state(base: Path) -> Path | None:
    path = base / "last.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        value = data["state"]
        if not isinstance(value, str):
            raise ValueError
        state = _contained_state(base, value)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("마지막 대회 기록을 읽을 수 없습니다. 사이트 주소를 다시 지정하세요.") from exc
    if not (state / "contest.sqlite3").is_file():
        raise ValueError("마지막 대회 상태가 없습니다. 사이트 주소를 다시 지정하세요.")
    return state


def choose_state(base: Path, site: str | None, account: str | None) -> Path | None:
    if site is None:
        state = last_state(base)
        if state is None:
            return None
        if account is not None:
            raise ValueError("계정을 바꾸려면 사이트 주소와 --account를 함께 지정하세요.")
        return state
    site = validate_site_url(site)
    # Reuse pre-existing advanced-command states as well as shortcut states.
    matches = []
    for database in base.glob("*/contest.sqlite3"):
        if not database.resolve().is_relative_to(base):
            continue
        try:
            conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                row = conn.execute("SELECT site_url, account_label, updated_at FROM contest WHERE id=1").fetchone()
            finally:
                conn.close()
            if row and row[0] == site and (account is None or row[1] == account):
                matches.append((row[2], database.parent))
        except sqlite3.Error:
            continue
    if matches:
        return max(matches, key=lambda value: value[0])[1]
    digest = hashlib.sha256(f"{site}\0{account or 'default'}".encode()).hexdigest()[:12]
    return base / ("contest-" + digest)


def remember_state(base: Path, state: Path):
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    payload = json.dumps({"state": str(state.relative_to(base))})
    with tempfile.NamedTemporaryFile(mode="w", prefix=".last-", dir=base, delete=False) as file:
        temporary = Path(file.name)
        file.write(payload)
    try:
        os.replace(temporary, base / "last.json")
    finally:
        temporary.unlink(missing_ok=True)


def prepare_contest(root: Path, site: str | None = None, account: str | None = None,
                    auth_entry: str | None = None, *, state: Path | None = None,
                    model: str | None = None, reasoning_effort: str | None = None):
    """Prepare a saved contest without starting a model."""
    base = root.resolve() / ".ctfh-live"
    state = state or choose_state(base, site, account)
    if state is None:
        raise ValueError("CTF 사이트 주소를 입력하세요.")
    with LiveRunLock(state), LiveStore(state) as store:
        contest = store.ensure_contest(site, account, auth_entry, model=model,
                                      reasoning_effort=reasoning_effort)
        if not contest["agent_command"]:
            contest = store.select_engine("codex")
        engine = store.summary()["engine"]
    remember_state(base, state)
    return state, contest, engine


def check_local_commands(engine: str, binary: str = "codex"):
    if engine != "external" and shutil.which(binary) is None:
        raise ValueError(f"{binary} 실행 파일을 찾지 못했습니다. Codex 설치와 로그인을 확인하세요.")
    if shutil.which("chrome-use") is None:
        raise ValueError("chrome-use 실행 파일을 찾지 못했습니다. 브라우저 연결을 확인하세요.")


def open_contest_browser(session: str, site_url: str) -> None:
    """Connect the discovery session to the user's existing Chrome before model work."""
    try:
        result = subprocess.run(
            ["rtk", "proxy", "chrome-use", "--session", session, "open", site_url],
            capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Chrome 연결을 시작하지 못했습니다. chrome-use 확장 연결을 확인하세요.") from exc
    if result.returncode:
        raise ValueError("Chrome에서 CTF 사이트를 열지 못했습니다. chrome-use 확장 연결을 확인하세요.")


_LABELS = {
    "turn.started": "시작", "turn.finished": "기록", "turn.failed": "실패",
    "turn.interrupted": "중단", "worker.stopped": "중단 확인",
    "challenge.solved": "완료", "submission.started": "제출 준비",
    "submission.verdict": "제출 결과", "agent.message": "진행", "agent.progress": "진행",
    "plan.updated": "계획", "checkpoint": "작업 메모", "tool.started": "실행",
    "tool.completed": "실행 결과", "model.changed": "모델 변경",
}


class ConsoleView:
    def __init__(self, store: LiveStore):
        self.store = store
        self.cursor = store.latest_event_id()
        self.keys: list[str] = []

    def refresh(self):
        rows = self.store.conn.execute("SELECT challenge_key, title, state FROM challenges ORDER BY rowid").fetchall()
        for row in rows:
            if row["challenge_key"] not in self.keys:
                self.keys.append(row["challenge_key"])
                self._problem(row)
        return rows

    def _problem(self, row):
        number = self.keys.index(row["challenge_key"]) + 1
        status = "완료" if row["state"] == "solved" else "미해결"
        print(f"  [{number}] {public_text(row['title'], 150)} — {status}", flush=True)

    def drain(self):
        self.refresh()
        high_water = self.store.latest_event_id()
        for event in self.store.events(after=self.cursor, through=high_water, limit=EVENT_LIMIT):
            key = event["challenge_key"]
            target = f"문제 {self.keys.index(key) + 1}" if key in self.keys else "사이트"
            stamp = datetime.fromtimestamp(event["created_at"]).strftime("%H:%M:%S")
            print(f"{stamp} [{target}] {_LABELS.get(event['kind'], '진행')} · {event['message']}", flush=True)
        self.cursor = high_water

    def command(self, line: str) -> bool:
        """Return False to stop. A solve command is explicit user input."""
        words = line.strip().split()
        if not words:
            return True
        if words[0] in {"종료", "/quit", "quit", "exit"}:
            return False
        if words[0] in {"목록", "상태", "/list", "/status"}:
            self.refresh()
            for row in self.store.conn.execute("SELECT challenge_key, title, state FROM challenges ORDER BY rowid"):
                self._problem(row)
            return True
        if words[0] in {"완료", "/done", "done"} and len(words) > 1:
            self.refresh()
            keys = []
            for token in words[1:]:
                if token.isdecimal():
                    index = int(token) - 1
                    key = self.keys[index] if 0 <= index < len(self.keys) else None
                else:
                    key = token if token in self.keys else None
                if key is None:
                    print("문제를 찾지 못했습니다. '목록'으로 번호를 확인하세요.", flush=True)
                    return True
                keys.append(key)
            for key in dict.fromkeys(keys):
                self.store.mark_solved(key)
            return True
        print("입력: 목록 | 완료 번호 (예: 완료 2) | 종료", flush=True)
        return True


async def start_contest(*, root: Path, site: str | None = None, account: str | None = None,
                        auth_entry: str | None = None, max_workers: int | None = None,
                        max_turns: int | None = None, runner=None, console=None,
                        interactive: bool | None = None, codex_binary: str | None = None,
                        model: str | None = None, reasoning_effort: str | None = None) -> dict:
    root = root.resolve()
    base = root / ".ctfh-live"
    own_console = console is None and (sys.stdin.isatty() if interactive is None else interactive)
    if own_console:
        console = ConsoleInput()
    controller = reader = None
    view = None
    try:
        state = choose_state(base, site, account)
        if state is None:
            if console is None:
                raise ValueError("사이트 주소를 지정하세요: ./ctfh2 https://사이트주소")
            print("CTF 사이트 주소: ", end="", flush=True)
            site = await console.read_line()
            if site is None or not site.strip():
                raise ValueError("사이트 주소가 필요합니다.")
            site = site.strip()
            state = choose_state(base, site, account)
        state, contest, engine = prepare_contest(
            root, site, account, auth_entry, state=state,
            model=model, reasoning_effort=reasoning_effort,
        )
        native = native_launch(root, codex_binary)
        with LiveStore(state, create=False) as store:
            view = ConsoleView(store)
            print(f"CTF 시작 · {public_text(contest['site_url'])} · {public_text(contest['account_label'])}", flush=True)
            print(f"Codex 실행기: {public_text(native.command)}", flush=True)
            print("진행은 이 화면에 표시됩니다. 입력: 목록 | 완료 번호 | 종료", flush=True)
            view.refresh()
            if runner is None:
                check_local_commands(engine, native.binary)
                await asyncio.to_thread(
                    open_contest_browser, store.browser_session_for(None), str(contest["site_url"])
                )
                print("Chrome 연결 확인 · 현재 브라우저에서 사이트를 열었습니다.", flush=True)
            handoff = store.contest()["state"] == "needs_human"
            resume_handoff = False
            while True:
                if handoff:
                    note = public_text(store.contest()["last_note"])
                    print(f"로그인이 필요합니다. {note}", flush=True)
                    if console is None:
                        print("브라우저에서 로그인한 뒤 ./ctfh2 를 다시 실행하세요.", flush=True)
                        return store.summary()
                    if reader is not None:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                        reader = None
                    console.discard_pending()
                    print("브라우저에서 로그인한 뒤 Enter를 누르세요. 종료하려면 '종료'를 입력하세요.", flush=True)
                    line = await console.read_line()
                    if line is None or not view.command(line):
                        return store.summary()
                    if line.strip():
                        continue
                    resume_handoff = True
                    handoff = False
                print("연결을 확인하고 문제를 진행합니다…", flush=True)
                controller = asyncio.create_task(run_live_contest(
                    state_dir=state, site_url=None, account_label=None, auth_entry=None,
                    model=None, cyber_access_program=None, codex_binary=native.binary, engine=None,
                    full_access=native.full_access,
                    max_turns=max_turns, idle_seconds=60, turn_timeout=3600,
                    resume_handoff=resume_handoff, max_workers=max_workers,
                    stop_when_solved=True,
                    runner=runner, quiet=True,
                ))
                resume_handoff = False
                if console is not None and reader is None:
                    reader = asyncio.create_task(console.read_line())
                while True:
                    tasks = [controller] + ([reader] if reader is not None else [])
                    done, _ = await asyncio.wait(tasks, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
                    view.drain()
                    if reader is not None and reader in done:
                        line = reader.result()
                        reader = None
                        if line is None or not view.command(line):
                            controller.cancel()
                            await asyncio.gather(controller, return_exceptions=True)
                            view.drain()
                            print("중단했습니다. ./ctfh2 로 이어갈 수 있습니다.", flush=True)
                            return store.summary()
                        reader = asyncio.create_task(console.read_line())
                    if controller in done:
                        result = await controller
                        view.drain()
                        if result["state"] == "needs_human":
                            handoff = True
                            break
                        print(f"실행 종료 · 완료 {result['solved_count']}/{result['challenge_count']} · ./ctfh2 로 재개", flush=True)
                        return result
    finally:
        # The controller must release its workers/runtime before the input fd is closed.
        for task in (reader, controller):
            if task is not None and not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*(task for task in (reader, controller) if task is not None), return_exceptions=True)
        if own_console:
            console.close()
