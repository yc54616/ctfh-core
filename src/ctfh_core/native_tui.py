"""Keyboard dashboard for the ctfh2 checker: the plain board's loop, with per-challenge actions."""

from __future__ import annotations

import threading

from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, OptionList, Static
from textual.widgets.option_list import Option

from .native import Checker, window_name

STYLES = {"needs_user": "bold red", "manual": "bold yellow", "hold": "blue", "unknown": "bold red", "blocked": "red", "needs_fix": "magenta",
          "checking": "cyan", "verified": "cyan", "submitting": "cyan", "accepted": "green",
          "done": "green", "user_solved": "green", "site_solved": "green", "pending": "dim"}


# Table columns: key, header, and how a header click sorts by it (None = the board's own grouping).
COLUMNS = [("group", ""), ("name", "문제"), ("category", "분류"), ("instance", "인스턴스"), ("label", "상태"), ("tokens", "토큰"),
           ("elapsed", "시간"), ("activity", "활동"), ("reason", "메모")]
SORT_KEYS = {"name": lambda row: row["name"], "category": lambda row: (row["category"], row["name"]),
             "instance": lambda row: (not row.get("instance"), not row["queue"].startswith("사용"),
                                      int(row["queue"].split()[-1]) if row["queue"].startswith("대기") else 0, row["name"]),
             "label": lambda row: (row["label"], row["name"]), "tokens": lambda row: row["tokens_n"],
             "elapsed": lambda row: row["elapsed_n"],
             "activity": lambda row: (row["activity"] == "", row["activity"]),
             "reason": lambda row: (row["reason"] == "", row["reason"])}
DESCENDING_FIRST = {"tokens", "elapsed"}  # the biggest spenders first


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


# The challenge menu: action, key, label, what it does. The keys work straight from the table too.
ACTIONS = [("jump", "enter", "창으로 이동", "이 문제의 풀이 창(tmux)으로 간다"),
           ("notes", "o", "노트 보기", "work/NOTES.md를 읽는다"),
           ("nudge", "n", "재촉", "풀이 창에 계속하라고 알린다"),
           ("restart", "r", "다시 열기", "닫힌 풀이 창을 다시 연다 (보류·직접 확인도 재개)"),
           ("pick", "i", "인스턴스 우선 지정/해제", "인스턴스 큐 맨 앞으로 (한도가 차 있으면 하나를 쉬게 하고 연다)"),
           ("instance", "I", "인스턴스 문제 맞음/아님", "자동 판단이 틀렸을 때 고친다"),
           ("hold", "p", "보류", "풀이 창을 닫고 멈춘다 (r로 재개)"),
           ("done", "d", "해결 표시", "팀원이 풀었다고 표시하고 창을 닫는다")]
HELP = """표
  ↑/↓  문제 고르기        Enter  풀이 창으로 이동     Space/m  문제 메뉴
  o    노트 보기          n  재촉     r  다시 열기     p  보류     d  해결 표시
  i    인스턴스 우선 지정/해제: 자리 큐 맨 앞으로. 한도가 차 있으면 지정 안 된 문제 하나가
       노트를 적고 인스턴스를 끈 뒤 2분 안에 자리를 넘긴다 (첨부 없는 문제는 창을 닫고, 자리가 나면 이어서 연다)
       인스턴스 칸: 사용 = 자리 받음 · 대기 n = 자리 순서 · 분석 = 자리 없이 첨부를 보는 중
  I    인스턴스 문제 맞음/아님 (자동 판단이 틀렸을 때)
  t    정렬 바꾸기 (머리글을 눌러도 됨)

전체
  c    설정: 동시 풀이 창 수, 동시 인스턴스 문제 수
  +/-  동시 풀이 창 ±1 (0: 전체)
  ]/[  동시 인스턴스 문제 ±1 (0: 무제한)
  s    지금 문제 목록 다시 읽기
  q    검사기 종료 (풀이 창은 계속 돈다)

풀이 창에서 돌아오기: tmux 창 목록 (기본 Ctrl-b w) → checker"""


class MenuScreen(ModalScreen[str | None]):
    """What can be done with one challenge; the ones that cannot apply now are greyed out with the reason."""

    CSS = """
    MenuScreen { align: center middle; }
    #menu { width: 86; max-width: 95%; height: auto; border: round $accent; background: $surface; }
    """
    BINDINGS = [Binding("escape,q,space,m", "dismiss_none", "닫기")] + [
        Binding(key, f"pick('{action}')", label, show=False) for action, key, label, _ in ACTIONS if key != "enter"]

    def __init__(self, name: str, refusals: dict[str, str | None]):
        super().__init__()
        self.name_text, self.refusals = name, refusals

    def compose(self) -> ComposeResult:
        pad = lambda text, width: text + " " * max(1, width - cell_len(text))  # Korean letters take two cells
        options = [Option(Text.assemble((pad(key, 7), "bold"), pad(label, 20), (self.refusals.get(action) or about, "dim")),
                          id=action, disabled=bool(self.refusals.get(action))) for action, key, label, about in ACTIONS]
        menu = OptionList(*options, id="menu")
        menu.border_title = self.name_text
        yield menu

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_pick(self, action: str) -> None:
        if not self.refusals.get(action):
            self.dismiss(action)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen):
    """The live limits, changed with ←/→; each change applies at once and is kept across restarts."""

    CSS = """
    SettingsScreen { align: center middle; }
    #settings { width: 64; max-width: 95%; height: auto; border: round $accent; background: $surface; }
    #settings-help { width: 64; max-width: 95%; height: auto; padding: 0 1; background: $surface; color: $text-muted; }
    """
    BINDINGS = [Binding("left,minus", "change(-1)", "줄이기"), Binding("right,plus,equals_sign", "change(1)", "늘리기"),
                Binding("escape,q,c", "close", "닫기")]

    def __init__(self, app: CheckerApp):
        super().__init__()
        self.owner = app

    def compose(self) -> ComposeResult:
        menu = OptionList(*self.options(), id="settings")
        menu.border_title = "설정"
        yield menu
        yield Static("←/→ 로 바꿈 · 바로 적용, 다시 실행해도 유지 · 열린 창은 그대로", id="settings-help")
        yield Footer()

    def options(self) -> list[Option]:
        opts = self.owner.checker.opts
        row = lambda label, value: f"{label}{' ' * (22 - cell_len(label))}◀ {value} ▶"
        return [Option(row("동시 풀이 창", opts.max_parallel or "전체"), id="parallel"),
                Option(row("동시 인스턴스 문제", opts.max_instances or "무제한"), id="instances")]

    def action_change(self, delta: int) -> None:
        menu = self.query_one(OptionList)
        which = menu.highlighted_option.id if menu.highlighted_option else "parallel"
        checker = self.owner.checker

        def work() -> None:
            message = checker.set_parallel(delta) if which == "parallel" else checker.set_instances(delta)
            self.owner.wake.set()
            try:
                self.owner.call_from_thread(self.refresh_options, message)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def refresh_options(self, message: str) -> None:
        menu = self.query_one(OptionList)
        keep = menu.highlighted
        menu.clear_options()
        menu.add_options(self.options())
        menu.highlighted = keep
        self.owner.notify(message)

    def action_close(self) -> None:
        self.dismiss()


class CheckerApp(App):
    TITLE = "ctfh2 검사기"
    CSS = """
    #head { height: auto; padding: 0 1; }
    DataTable { height: 1fr; }
    #detail { height: auto; max-height: 6; padding: 0 1; border-top: solid $accent; }
    """
    BINDINGS = [
        Binding("enter", "jump", "창으로 이동", priority=True),
        Binding("space,m", "menu", "문제 메뉴"),
        Binding("o", "notes", "노트"),
        Binding("c", "settings", "설정"),
        Binding("s", "rescan", "지금 재스캔"),
        Binding("t", "sort_next", "정렬"),
        Binding("question_mark", "help", "도움말"),
        Binding("q", "quit", "검사기 종료"),
        Binding("n", "nudge", "재촉", show=False),
        Binding("r", "restart", "다시 열기", show=False),
        Binding("d", "done", "해결 표시", show=False),
        Binding("p", "hold", "보류", show=False),
        Binding("i", "pick", "인스턴스 우선 지정", show=False),
        Binding("I", "instance", "인스턴스 문제 맞음/아님", show=False),
        Binding("plus,equals_sign", "parallel(1)", "창 +1", show=False),
        Binding("minus", "parallel(-1)", "창 -1", show=False),
        Binding("right_square_bracket", "instances(1)", "인스턴스 +1", show=False),
        Binding("left_square_bracket", "instances(-1)", "인스턴스 -1", show=False),
        Binding("ctrl+c", "quit", "검사기 종료", show=False, priority=True),
    ]

    def __init__(self, checker: Checker):
        super().__init__()
        self.checker = checker
        self.stop, self.wake = threading.Event(), threading.Event()
        self.sort: tuple[str, bool] | None = None  # (column, descending); None keeps the board's grouping

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.set_columns()
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
        if self.sort:
            rows = sorted(rows, key=SORT_KEYS[self.sort[0]], reverse=self.sort[1])
        text = Text()
        # What needs the user comes first, then the counts, then what the checker is doing.
        alert = lambda line: line.startswith(("!!", "직접 확인"))
        for line in [line for line in head if alert(line)] + ([error] if error else []):
            text.append(line + "\n", style="bold red" if line.startswith("!!") or line is error else "bold yellow")
        for i, line in enumerate(head):
            if not alert(line):
                text.append(line + "\n", style="bold" if i == 1 else "dim" if i > 1 else "")
        text.append("Space 문제 메뉴 · c 설정 · ? 도움말 · 풀이 창에서 돌아오기: Ctrl-b w → checker", style="dim")
        self.query_one("#head", Static).update(text)
        # Change the table in place: clearing it would throw away where the user had scrolled to.
        table = self.query_one(DataTable)
        keep = self.selected()
        self.rows = {row["name"]: row for row in rows}
        present = {key.value for key in table.rows}
        for name in present - self.rows.keys():
            table.remove_row(name)
        for row in rows:
            if row["name"] not in present:
                table.add_row(*self.cells(row), key=row["name"])
                continue
            for (column, _), cell in zip(COLUMNS, self.cells(row)):
                if table.get_cell(row["name"], column) != cell:
                    table.update_cell(row["name"], column, cell, update_width=True)
        order = {name: i for i, name in enumerate(self.rows)}
        table.sort("name", key=lambda cell: order[cell.plain])
        if keep in self.rows:
            table.move_cursor(row=table.get_row_index(keep), scroll=False)
        self.show_detail()

    @staticmethod
    def cells(row: dict) -> list[Text]:
        style = STYLES.get(row["state"], "")
        finished = row["group"] == "✓"
        return [Text(row["group"], style=style), Text(row["name"], style=style + (" dim" if finished else "")),
                Text(row["category"], style="dim"),
                Text(("★" if row.get("picked") else "") + row.get("queue", ""), justify="center",
                     style="bold cyan" if row.get("picked") else "cyan"),
                Text(row["label"], style=style),
                Text(row["tokens"], justify="right", style="dim" if finished else ""),
                Text(row["elapsed"], justify="right", style="dim" if finished else ""),
                Text(row["activity"], style="dim" if finished or "서버 대기" in row["activity"] else ""),
                Text(row["reason"] if len(row["reason"]) <= 48 else row["reason"][:47] + "…",
                     style="dim" if finished else "")]

    def set_columns(self) -> None:
        """Headers with ▲/▼ on the sorted column; clicking a header sorts by it."""
        table = self.query_one(DataTable)
        table.clear(columns=True)
        for key, title in COLUMNS:
            arrow = (" ▼" if self.sort[1] else " ▲") if self.sort and self.sort[0] == key else ""
            table.add_column(title + arrow, key=key)

    def sort_by(self, column: str | None) -> None:
        """Same column again flips the order, and a third time returns to the board's grouping."""
        first = column in DESCENDING_FIRST
        if column not in SORT_KEYS:
            self.sort = None
        elif not self.sort or self.sort[0] != column:
            self.sort = (column, first)
        elif self.sort[1] == first:
            self.sort = (column, not first)
        else:
            self.sort = None
        self.set_columns()
        if getattr(self.checker, "last_view", None):
            self.show(self.checker.last_view)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self.sort_by(event.column_key.value)

    def action_sort_next(self) -> None:
        """Keyboard: step through the columns, each in its first order, then back to the grouping."""
        keys = list(SORT_KEYS)
        current = keys.index(self.sort[0]) if self.sort else -1
        self.sort = None
        self.sort_by(keys[current + 1] if current + 1 < len(keys) else None)

    def show_detail(self) -> None:
        """Below the table: the selected challenge in full, since the table cuts long notes short."""
        row = getattr(self, "rows", {}).get(self.selected() or "")
        text = Text()
        if row:
            text.append(f"{row['name']}", style="bold")
            queue = f" · 인스턴스 {'우선 ' if row.get('picked') else ''}{row.get('queue', '')}" if row.get("instance") else ""
            text.append(f"  {row['category'] or '-'}{queue} · {row['label']}"
                        f" · 토큰 {row['tokens']} · 시간 {row['elapsed']}\n", style="dim")
            if row["activity"]:
                text.append(f"활동: {row['activity']}\n")
            if row["reason"]:
                text.append(f"메모: {row['reason']}")
        self.query_one("#detail", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_detail()

    def selected(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # Row actions belong to the table; leave keys to the notes view while it is open.
        if action != "quit" and len(self.screen_stack) > 1:
            return False
        return True

    def action_menu(self) -> None:
        name = self.selected()
        if name is None:
            return
        refusals = {action: self.checker.refusal(action, name) for action in ("nudge", "restart", "done", "hold", "instance", "pick")}
        if window_name(name) not in self.checker.tmux.windows():
            refusals["jump"] = "열린 풀이 창이 없음 (다시 열기로 연다)"
        self.push_screen(MenuScreen(name, refusals), lambda action: self.run_menu(action, name))

    def run_menu(self, action: str | None, name: str) -> None:
        if action and self.selected() == name:
            getattr(self, f"action_{action}")()

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen(self))

    def action_help(self) -> None:
        self.push_screen(NotesScreen("ctfh2 검사기 · 키", HELP))

    def action_jump(self) -> None:
        name = self.selected()
        if name and not self.checker.tmux.select(window_name(name)):
            self.notify(f"{name}: 열린 창이 없습니다. r로 다시 여세요.", severity="warning")

    def action_nudge(self) -> None:
        self.request("nudge")

    def action_instance(self) -> None:
        self.request("instance")

    def action_pick(self) -> None:
        self.request("pick")

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

    def action_instances(self, delta: int) -> None:
        def work() -> None:
            message = self.checker.set_instances(delta)
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
