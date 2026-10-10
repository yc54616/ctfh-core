"""Terminal dashboard for a live contest. The browser remains real Chrome."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static

from .live import run_live_contest
from .live_config import native_launch
from .live_events import EVENT_LIMIT, public_text
from .live_start import check_local_commands, open_contest_browser, prepare_contest
from .live_store import LiveStore


_EVENT_LABELS = {
    "turn.started": "작업 시작", "turn.finished": "작업 기록",
    "turn.failed": "작업 실패", "turn.interrupted": "작업 중단",
    "worker.stopped": "중단 확인", "challenge.solved": "문제 완료",
    "submission.started": "제출 시작", "submission.verdict": "제출 결과",
    "agent.message": "AI 진행", "agent.progress": "AI 진행",
    "plan.updated": "계획", "checkpoint": "작업 메모",
    "tool.started": "도구 실행", "tool.completed": "도구 결과",
    "model.changed": "모델 변경",
    "approval.requested": "확인 필요", "approval.answered": "확인 완료",
}
_SUMMARY_KINDS = {
    "challenge.solved", "submission.started", "submission.verdict",
    "turn.failed", "turn.interrupted", "worker.stopped", "approval.requested",
}


def _compact_tokens(count: int | None) -> str:
    if count is None:
        return "—"
    for scale, suffix in ((1_000_000, "M"), (1_000, "K")):
        if count >= scale:
            return f"{count / scale:.1f}".rstrip("0").rstrip(".") + suffix
    return str(count)


class ModelSettings(ModalScreen[tuple[str | None, str | None] | None]):
    """Edit per-contest Codex settings while preserving existing threads."""

    CSS = """
    ModelSettings { align: center middle; background: #070d16 80%; }
    #settings-dialog { width: 62; max-width: 94%; height: auto; padding: 1 2;
                       border: round #4b8bac; background: #142238; }
    #settings-title { height: 2; color: #75d7ff; text-style: bold; }
    #settings-note { height: 3; color: #b8d7f2; margin-top: 1; }
    #settings-buttons { height: 3; align-horizontal: right; }
    #settings-buttons Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "닫기")]

    def __init__(self, model: str | None, reasoning_effort: str | None) -> None:
        super().__init__()
        self.current_model = model
        self.current_effort = reasoning_effort

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("Codex 모델 · 추론 설정", id="settings-title")
            yield Static("모델 (비우면 계정 기본값)")
            yield Input(value=self.current_model or "", placeholder="계정 기본값",
                        id="settings-model")
            yield Static("추론 강도")
            yield Select(
                [("계정 기본값", "")] + [(value, value) for value in
                                     ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")],
                value=self.current_effort or "", allow_blank=False, id="settings-effort",
            )
            yield Static("저장 후 새로 시작하는 턴부터 적용됩니다. 진행 중인 턴은 그대로 둡니다.",
                         id="settings-note")
            with Horizontal(id="settings-buttons"):
                yield Button("취소", id="settings-cancel")
                yield Button("저장", variant="primary", id="settings-save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save":
            model = self.query_one("#settings-model", Input).value.strip() or None
            effort = self.query_one("#settings-effort", Select).value
            self.dismiss((model, effort or None))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ContestDashboard(App[dict]):
    """Observe the SQLite journal and control only this terminal's controller."""

    TITLE = "CTFH  /  LIVE CONTEST"
    BINDINGS = [
        Binding("q", "quit_contest", "중단", priority=True),
        Binding("d", "mark_done", "선택 문제 완료"),
        Binding("r", "resume_login", "로그인 후 재개"),
        Binding("s", "settings", "모델·추론"),
    ]
    CSS = """
    Screen { background: #0b111b; color: #dbe7f6; }
    Header { background: #16243a; color: #edf6ff; }
    #banner { height: 3; padding: 0 2; background: #142238; color: #b8d7f2; }
    #body { height: 1fr; }
    #challenge-pane { width: 48%; min-width: 38; border: round #2b4b6b; }
    #challenge-heading, #detail-heading, #detail-event-heading, #event-heading {
        height: 1; padding: 0 1; background: #19304a; color: #75d7ff;
        text-style: bold;
    }
    #challenges { height: 1fr; }
    #detail-pane { width: 1fr; border: round #2b4b6b; }
    #detail { height: 34%; min-height: 7; max-height: 11; padding: 1 2; overflow-y: auto; }
    #detail-events { height: 1fr; padding: 0 1; }
    #event-pane { height: 7; min-height: 5; border: round #2b4b6b; }
    #events { height: 1fr; padding: 0 1; }
    Footer { background: #16243a; }
    """

    def __init__(self, *, state: Path, site_url: str, codex_binary: str,
                 full_access: bool, engine: str, max_workers: int | None,
                 max_turns: int | None, auto_start: bool = True) -> None:
        super().__init__()
        self.state = state
        self.site_url = site_url
        self.codex_binary = codex_binary
        self.full_access = full_access
        self.engine = engine
        self.max_workers = max_workers
        self.max_turns = max_turns
        self.auto_start = auto_start
        self.store: LiveStore | None = None
        self.refresh_timer: Timer | None = None
        self.controller: asyncio.Task | None = None
        self.failure: Exception | None = None
        self.result: dict | None = None
        self.selected_key: str | None = None
        self.detail_key: str | None = None
        self.detail_cursor = 0
        self.summary_cursor = 0
        self.updating_rows = False
        self.row_signature: tuple = ()
        self.rows: list[dict] = []
        self.phase = "대기"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("연결 준비 중", id="banner")
        with Horizontal(id="body"):
            with Vertical(id="challenge-pane"):
                yield Static("문제 목록", id="challenge-heading")
                yield DataTable(id="challenges", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail-pane"):
                yield Static("선택한 문제", id="detail-heading")
                yield Static("문제를 선택하면 작업 내용이 표시됩니다.", id="detail")
                yield Static("문제 상세 진행", id="detail-event-heading")
                yield RichLog(id="detail-events", wrap=True, highlight=False,
                              markup=False, max_lines=300)
        with Vertical(id="event-pane"):
            yield Static("대회 주요 결과", id="event-heading")
            yield RichLog(id="events", wrap=True, highlight=False, markup=False, max_lines=80)
        yield Footer()

    def on_mount(self) -> None:
        self.store = LiveStore(self.state, create=False)
        self._fit_height(self.size.height)
        table = self.query_one("#challenges", DataTable)
        table.add_columns("#", "상태", "토큰", "문제")
        self._refresh()
        self.refresh_timer = self.set_interval(0.5, self._refresh)
        if self.auto_start:
            self._launch(False)

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self._fit_height(event.size.height)

    def _fit_height(self, height: int) -> None:
        if not any(self.query("#detail")):
            return
        compact = height <= 28
        detail = self.query_one("#detail", Static)
        detail.styles.height = 5 if compact else "34%"
        detail.styles.min_height = 4 if compact else 7
        results = self.query_one("#event-pane", Vertical)
        results.styles.height = 5 if compact else 7
        results.styles.min_height = 4 if compact else 5

    async def on_unmount(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()
        if self.controller is not None and not self.controller.done():
            self.controller.cancel()
            await asyncio.gather(self.controller, return_exceptions=True)
        if self.store is not None:
            self.store.close()
            self.store = None

    def _launch(self, resume_handoff: bool) -> None:
        if self.controller is not None and not self.controller.done():
            return
        self.phase = "Chrome 연결 확인"
        self.controller = asyncio.create_task(self._run_controller(resume_handoff))
        self._refresh()

    async def _run_controller(self, resume_handoff: bool) -> None:
        assert self.store is not None
        try:
            check_local_commands(self.engine, self.codex_binary)
            await asyncio.to_thread(
                open_contest_browser, self.store.browser_session_for(None), self.site_url
            )
            self.phase = "발견 및 풀이 중"
            self._refresh()
            self.result = await run_live_contest(
                state_dir=self.state, site_url=None, account_label=None, auth_entry=None,
                model=None, cyber_access_program=None, codex_binary=self.codex_binary,
                engine=None, full_access=self.full_access, max_turns=self.max_turns,
                idle_seconds=60, turn_timeout=3600, resume_handoff=resume_handoff,
                max_workers=self.max_workers, quiet=True, stop_when_solved=True,
            )
            self.phase = "로그인 대기" if self.result["state"] == "needs_human" else "실행 종료"
        except asyncio.CancelledError:
            self.phase = "중단"
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            self.failure = exc
            self.phase = "오류 · 종료 후 메시지 확인"
        finally:
            if self.is_mounted and self.store is not None:
                self._refresh()

    def _refresh(self) -> None:
        if self.store is None or not any(self.query("#banner")):
            return
        summary = self.store.summary()
        running = len(summary["running_challenges"])
        note = public_text(summary["last_note"], 120)
        total = int(summary["challenge_count"])
        solved = int(summary["solved_count"])
        filled = round(10 * solved / total) if total else 0
        progress = "━" * filled + "╺" * (10 - filled)
        token_row = self.store.conn.execute(
            "SELECT SUM(input_tokens + output_tokens) AS total FROM turns "
            "WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL"
        ).fetchone()
        contest_tokens = _compact_tokens(token_row["total"])
        agent_label = self.codex_binary
        if summary["model"]:
            agent_label += f" · {summary['model']}"
        if summary["reasoning_effort"]:
            agent_label += f" · {summary['reasoning_effort']}"
        banner = (
            f"{summary['site_origin']}   │   {self.phase}   │   "
            f"완료 {solved}/{total} {progress}   │   "
            f"작업 중 {running}   │   토큰 {contest_tokens}   │   {agent_label}"
        )
        if self.phase == "로그인 대기":
            banner += "\nChrome에서 로그인한 뒤 R로 재개하세요. " + note
        elif self.failure:
            banner += "\n" + public_text(str(self.failure), 160)
        elif note and self.phase == "실행 종료":
            banner += "\n" + note
        self.query_one("#banner", Static).update(Text(banner))
        rows = [dict(row) for row in self.store.conn.execute(
            "SELECT c.challenge_key, c.title, c.url, c.state, "
            "COALESCE(w.note, '') AS note, COALESCE(w.next_action, '') AS next_action, "
            "COALESCE(w.attempts, 0) AS attempts, "
            "u.input_tokens, u.cached_input_tokens, u.output_tokens, "
            "EXISTS(SELECT 1 FROM turns t WHERE t.challenge_key=c.challenge_key "
            "AND t.state='running') AS running "
            "FROM challenges c LEFT JOIN challenge_work w ON w.challenge_key=c.challenge_key "
            "LEFT JOIN (SELECT challenge_key, SUM(input_tokens) AS input_tokens, "
            "SUM(cached_input_tokens) AS cached_input_tokens, "
            "SUM(output_tokens) AS output_tokens FROM turns "
            "WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "GROUP BY challenge_key) u ON u.challenge_key=c.challenge_key "
            "ORDER BY c.rowid"
        )]
        signature = tuple((r["challenge_key"], r["title"], r["state"], r["attempts"],
                           r["running"], r["input_tokens"], r["output_tokens"])
                          for r in rows)
        self.rows = rows
        if signature != self.row_signature:
            table = self.query_one("#challenges", DataTable)
            previous = self.selected_key
            self.updating_rows = True
            try:
                table.clear()
                for number, row in enumerate(rows, 1):
                    status = (Text("✓ 완료", style="bold #7de2b4")
                              if row["state"] == "solved" else
                              Text("● 풀이 중", style="bold #74d5ff")
                              if row["running"] else Text("○ 대기", style="#9bb2c9"))
                    tokens = (None if row["input_tokens"] is None else
                              row["input_tokens"] + row["output_tokens"])
                    table.add_row(str(number), status, _compact_tokens(tokens),
                                  public_text(row["title"], 100), key=row["challenge_key"])
                keys = [r["challenge_key"] for r in rows]
                self.selected_key = previous if previous in keys else (keys[0] if keys else None)
                if self.selected_key:
                    table.move_cursor(row=keys.index(self.selected_key))
            finally:
                self.updating_rows = False
            self.row_signature = signature
        self._detail()
        self._detail_events()
        self._summary_events()

    def _detail(self) -> None:
        row = next((row for row in self.rows if row["challenge_key"] == self.selected_key), None)
        if row is None:
            text = "문제를 수집하는 중입니다. 발견되면 목록에 나타납니다."
        else:
            tokens = (f"입력 {row['input_tokens']:,} (캐시 {row['cached_input_tokens']:,})"
                      f" · 출력 {row['output_tokens']:,}"
                      if row["input_tokens"] is not None else "아직 집계되지 않음")
            text = (
                f"{public_text(row['title'], 150)}\n"
                f"키 {public_text(row['challenge_key'], 80)}  ·  "
                f"{'완료' if row['state'] == 'solved' else '풀이 중' if row['running'] else '대기'}"
                f"  ·  시도 {row['attempts']}\n"
                f"토큰 {tokens}\n"
                f"주소 {public_text(row['url'], 180)}\n\n"
                f"메모 {public_text(row['note'], 400) or '아직 기록이 없습니다.'}\n"
                f"다음 {public_text(row['next_action'], 250) or '대기 중'}"
            )
        self.query_one("#detail", Static).update(Text(text))

    def _detail_events(self) -> None:
        assert self.store is not None
        high_water = self.store.latest_event_id()
        if self.selected_key != self.detail_key:
            self.detail_key = self.selected_key
            self.query_one("#detail-events", RichLog).clear()
            label = next((row["title"] for row in self.rows
                          if row["challenge_key"] == self.selected_key), "문제 상세 진행")
            self.query_one("#detail-event-heading", Static).update(Text(
                f"{public_text(label, 100)} · 상세 진행"
            ))
            records = (self.store.events(limit=200, challenge=self.selected_key,
                                         through=high_water) if self.selected_key else [])
        elif self.selected_key:
            records = self.store.events(after=self.detail_cursor, through=high_water,
                                        challenge=self.selected_key, limit=EVENT_LIMIT)
        else:
            records = []
        for event in records:
            self._write_event(event, "#detail-events")
        self.detail_cursor = high_water

    def _summary_events(self) -> None:
        assert self.store is not None
        high_water = self.store.latest_event_id()
        records = self.store.events(after=self.summary_cursor, through=high_water,
                                    limit=EVENT_LIMIT)
        for event in records:
            if event["kind"] in _SUMMARY_KINDS:
                self._write_event(event, "#events")
        self.summary_cursor = high_water

    def _write_event(self, event: dict, log_id: str) -> None:
        stamp = datetime.fromtimestamp(event["created_at"]).strftime("%H:%M:%S")
        target = public_text(event["challenge_key"] or event["role"], 40)
        kind = str(event["kind"])
        style = ("bold #7de2b4" if kind == "challenge.solved" else
                 "bold #ffbd79" if kind in {"turn.failed", "submission.verdict"} else
                 "bold #74d5ff" if kind in {"agent.message", "agent.progress"} else
                 "#a8c5dd")
        line = Text(f"{stamp}  ", style="#8ba5bd")
        line.append(f"{target:<14.14} ", style="#d9e9f7")
        line.append(f"{_EVENT_LABELS.get(kind, kind):<8.8} ", style=style)
        line.append(public_text(event["message"], 1000 if log_id == "#detail-events" else 180))
        self.query_one(log_id, RichLog).write(line)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select_problem(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._select_problem(str(event.row_key.value))

    def _select_problem(self, key: str) -> None:
        if (self.updating_rows or key == self.selected_key or
                not any(row["challenge_key"] == key for row in self.rows)):
            return
        self.selected_key = key
        self._detail()
        self._detail_events()

    def action_mark_done(self) -> None:
        if self.store is None or self.selected_key is None:
            self.notify("완료할 문제를 선택하세요.")
            return
        self.store.mark_solved(self.selected_key)
        self.notify("선택한 문제를 완료 처리했습니다. 해당 작업을 중단합니다.")
        self._refresh()

    def action_resume_login(self) -> None:
        if self.phase != "로그인 대기":
            self.notify("로그인 대기 상태에서만 재개할 수 있습니다.")
            return
        self._launch(True)

    def action_settings(self) -> None:
        if self.store is None:
            return
        contest = self.store.contest()
        self.push_screen(ModelSettings(contest["model"], contest["reasoning_effort"]),
                         self._settings_saved)

    def _settings_saved(self, settings: tuple[str | None, str | None] | None) -> None:
        if settings is None or self.store is None:
            return
        try:
            self.store.set_model_settings(*settings)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify("Codex 설정 저장 · 새 턴부터 적용")
        self._refresh()

    async def action_quit_contest(self) -> None:
        if self.controller is not None and not self.controller.done():
            self.controller.cancel()
            await asyncio.gather(self.controller, return_exceptions=True)
        result = self.result or (self.store.summary() if self.store is not None else {"state": "idle"})
        self.exit(result)


async def run_terminal_dashboard(*, root: Path, site: str | None,
                                 account: str | None, auth_entry: str | None,
                                 codex_binary: str | None, max_workers: int | None,
                                 max_turns: int | None, model: str | None = None,
                                 reasoning_effort: str | None = None) -> dict:
    state, contest, engine = prepare_contest(
        root, site, account, auth_entry, model=model, reasoning_effort=reasoning_effort,
    )
    native = native_launch(root, codex_binary)
    app = ContestDashboard(
        state=state, site_url=str(contest["site_url"]), codex_binary=native.binary,
        full_access=native.full_access, engine=engine, max_workers=max_workers,
        max_turns=max_turns,
    )
    result = await app.run_async()
    if app.failure is not None:
        raise app.failure
    return result or {"state": "idle"}
