"""Keyboard dashboard for the ctfh2 checker: the plain board's loop, with per-challenge actions."""

from __future__ import annotations

import threading

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Static

from .native import Checker, window_name

STYLES = {"manual": "bold yellow", "hold": "blue", "unknown": "bold red", "blocked": "red", "needs_fix": "magenta",
          "checking": "cyan", "verified": "cyan", "submitting": "cyan", "accepted": "green",
          "done": "green", "user_solved": "green", "site_solved": "green", "pending": "dim"}


class NotesScreen(ModalScreen):
    """Read-only view of one challenge's work/NOTES.md."""

    CSS = """
    NotesScreen { align: center middle; }
    #notes { width: 90%; height: 90%; border: round $accent; background: $surface; padding: 0 1; }
    """
    BINDINGS = [Binding("escape,q,o", "close", "닫기")]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="notes"):
            yield Static(Text(self.title_text, style="bold"))
            yield Static(Text(self.text))
        yield Footer()

    def action_close(self) -> None:
        self.dismiss()


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; }
    #ask { width: auto; max-width: 90%; height: auto; border: round $warning; background: $surface; padding: 1 2; }
    """
    BINDINGS = [Binding("y", "answer(True)", "예"), Binding("n,escape", "answer(False)", "아니오")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        yield Static(Text(self.question + "  (y/n)"), id="ask")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class CheckerApp(App):
    TITLE = "ctfh2 검사기"
    CSS = """
    #head { height: auto; padding: 0 1; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("enter", "jump", "창으로 이동", priority=True),
        Binding("n", "nudge", "재촉"),
        Binding("r", "restart", "다시 열기"),
        Binding("o", "notes", "노트"),
        Binding("d", "done", "해결 표시"),
        Binding("p", "hold", "보류"),
        Binding("s", "rescan", "지금 재스캔"),
        Binding("plus,equals_sign", "parallel(1)", "창 +1"),
        Binding("minus", "parallel(-1)", "창 -1"),
        Binding("q", "quit", "검사기 종료"),
        Binding("ctrl+c", "quit", "검사기 종료", show=False, priority=True),
    ]

    def __init__(self, checker: Checker):
        super().__init__()
        self.checker = checker
        self.stop, self.wake = threading.Event(), threading.Event()

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("문제", "상태", "토큰", "활동", "메모")
        threading.Thread(target=self.loop, name="checker", daemon=True).start()

    def on_unmount(self) -> None:
        self.stop.set()
        self.wake.set()

    def loop(self) -> None:
        """The checker's ticks run here, off the UI thread; solver windows keep running if this stops."""
        while not self.stop.is_set():
            error = None
            try:
                self.checker.tick()
            except Exception as exc:  # noqa: BLE001 - keep supervising
                error = f"검사기 오류 (다음 주기에 다시 시도): {exc!r}"
            try:
                self.call_from_thread(self.show, self.checker.last_view, error)
            except RuntimeError:  # the app is gone
                return
            self.wake.wait(max(self.checker.opts.tick, 0.5))
            self.wake.clear()

    def show(self, view: tuple[list[str], list[dict]], error: str | None = None) -> None:
        head, rows = view
        text = Text()
        for line in head:
            style = "bold red" if line.startswith("!!") else "bold yellow" if line.startswith("직접 확인") else ""
            text.append(line + "\n", style=style)
        if error:
            text.append(error + "\n", style="bold red")
        text.append("Enter로 간 풀이 창에서 돌아오기: tmux 창 목록 (기본 Ctrl-b w) → checker", style="dim")
        self.query_one("#head", Static).update(text)
        table = self.query_one(DataTable)
        keep = self.selected()
        table.clear()
        for row in rows:
            style = STYLES.get(row["state"], "")
            table.add_row(Text(row["name"], style=style), Text(row["state"], style=style),
                          Text(row["tokens"], justify="right"),
                          Text(row["activity"], style="dim" if "서버 대기" in row["activity"] else ""),
                          Text(row["reason"]), key=row["name"])
        if keep in {row["name"] for row in rows}:
            table.move_cursor(row=table.get_row_index(keep))

    def selected(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # Row actions belong to the table; leave keys to the notes view while it is open.
        if action in ("jump", "nudge", "restart", "notes", "done", "hold", "rescan") and len(self.screen_stack) > 1:
            return False
        return True

    def action_jump(self) -> None:
        name = self.selected()
        if name and not self.checker.tmux.select(window_name(name)):
            self.notify(f"{name}: 열린 창이 없습니다. r로 다시 여세요.", severity="warning")

    def action_nudge(self) -> None:
        self.request("nudge")

    def action_restart(self) -> None:
        self.request("restart")

    def refused(self, action: str, name: str) -> str | None:
        message = self.checker.refusal(action, name)
        if message:  # say no before asking, not after the user confirmed
            self.notify(message, severity="warning")
        return message

    def action_done(self) -> None:
        name = self.selected()
        if name is not None and self.refused("done", name) is None:  # closing a solver is not undone by a key
            self.push_screen(ConfirmScreen(f"{name}: 팀원이 풀었다고 해결로 표시하고 풀이 창을 닫을까요?"),
                             lambda yes: self.request("done", name) if yes else None)

    def action_hold(self) -> None:
        name = self.selected()
        if name is not None and self.refused("hold", name) is None:
            self.push_screen(ConfirmScreen(f"{name}: 풀이 창을 닫고 보류할까요? (r로 재개)"),
                             lambda yes: self.request("hold", name) if yes else None)

    def action_parallel(self, delta: int) -> None:
        def work() -> None:
            message = self.checker.set_parallel(delta)
            self.call_from_thread(self.notify, message)
            self.wake.set()
        threading.Thread(target=work, daemon=True).start()

    def action_rescan(self) -> None:
        self.request("rescan", "")

    def request(self, action: str, name: str | None = None) -> None:
        name = self.selected() if name is None else name
        if name is None:
            return

        def work() -> None:
            try:
                message = self.checker.rescan_now() if action == "rescan" else self.checker.act(action, name)
            except Exception as exc:  # noqa: BLE001
                message = f"{name}: 실패 {exc!r}"
            self.wake.set()
            try:
                self.call_from_thread(self.notify, message)
            except RuntimeError:
                pass

        threading.Thread(target=work, name=f"act-{action}", daemon=True).start()

    def action_notes(self) -> None:
        name = self.selected()
        if name is None:
            return
        notes = self.checker.contest.challenges_dir / name / "work" / "NOTES.md"
        text = notes.read_text(errors="replace") if notes.is_file() else "(work/NOTES.md 없음)"
        self.push_screen(NotesScreen(f"{name} · work/NOTES.md", text))


def run_dashboard(checker: Checker) -> None:
    CheckerApp(checker).run()
