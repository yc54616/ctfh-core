from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import DataTable, Input, RichLog, Select, Static

from ctfh_core.cli import main
from ctfh_core.live_store import LiveStore
from ctfh_core.live_tui import ContestDashboard, ModelSettings


class TerminalDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_modal_saves_model_and_thinking_for_the_contest(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
            app = ContestDashboard(
                state=state, site_url="https://ctf.example", codex_binary="codex",
                full_access=False, engine="codex", max_workers=None,
                max_turns=None, auto_start=False,
            )
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("s")
                self.assertIsInstance(app.screen, ModelSettings)
                app.screen.query_one("#settings-model", Input).value = "gpt-6-sol"
                app.screen.query_one("#settings-effort", Select).value = "xhigh"
                await pilot.click("#settings-save")
                await pilot.pause()
                self.assertIn("gpt-6-sol · xhigh",
                              str(app.query_one("#banner", Static).render()))
            with LiveStore(state, create=False) as store:
                self.assertEqual(store.contest()["model"], "gpt-6-sol")
                self.assertEqual(store.contest()["reasoning_effort"], "xhigh")

    async def test_small_terminal_keeps_problem_log_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example", None, None)
                discovery = store.begin_turn(role="discovery")
                store.finish_turn(discovery, {
                    "outcome": "progress", "note": "found", "submissions": [], "work": [],
                    "challenges": [{"key": "one", "title": "One", "url": "https://ctf.example/one",
                                    "state": "open", "site_evidence": ""}],
                })
                solver = store.begin_turn("one", role="solver")
                store.record_token_usage(solver, 1200, 200, 34)
            app = ContestDashboard(
                state=state, site_url="https://ctf.example", codex_binary="codex",
                full_access=False, engine="codex", max_workers=None,
                max_turns=None, auto_start=False,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                self.assertGreaterEqual(app.query_one("#detail-events", RichLog).region.height, 4)
                self.assertIn("1.2K", app.export_screenshot())

    async def test_login_handoff_requires_resume_key(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example/challenges", None, None)
            app = ContestDashboard(
                state=state, site_url="https://ctf.example/challenges",
                codex_binary="codex-secondary", full_access=True, engine="codex",
                max_workers=None, max_turns=None,
            )
            with patch("ctfh_core.live_tui.check_local_commands"), \
                    patch("ctfh_core.live_tui.open_contest_browser"), \
                    patch("ctfh_core.live_tui.run_live_contest", new_callable=AsyncMock) as run:
                run.side_effect = [
                    {"state": "needs_human", "solved_count": 0, "challenge_count": 0},
                    {"state": "idle", "solved_count": 0, "challenge_count": 0},
                ]
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertEqual(app.phase, "로그인 대기")
                    self.assertEqual(run.await_count, 1)
                    await pilot.press("r")
                    await pilot.pause()
                    self.assertEqual(run.await_count, 2)
                    self.assertFalse(run.await_args_list[0].kwargs["resume_handoff"])
                    self.assertTrue(run.await_args_list[1].kwargs["resume_handoff"])

    async def test_headless_dashboard_lists_all_problems_and_marks_one_done(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with LiveStore(state) as store:
                store.ensure_contest("https://ctf.example/challenges", None, None)
                turn = store.begin_turn(role="discovery")
                store.finish_turn(turn, {
                    "outcome": "progress", "note": "two found", "submissions": [], "work": [],
                    "challenges": [
                        {"key": key, "title": title, "url": f"https://ctf.example/{key}",
                         "state": "open", "site_evidence": ""}
                        for key, title in (("a", "Alpha"), ("b", "Beta"))
                    ],
                })
                alpha = store.begin_turn("a", role="solver")
                store.record_event(alpha, "agent.message", "Alpha 상세 분석")
                store.record_event(alpha, "tool.started", "chrome-use open")
                store.record_token_usage(alpha, 1234, 1000, 56)
                beta = store.begin_turn("b", role="solver")
                store.record_event(beta, "agent.message", "Beta 상세 분석")
            app = ContestDashboard(
                state=state, site_url="https://ctf.example/challenges",
                codex_binary="codex", full_access=False, engine="codex",
                max_workers=None, max_turns=None, auto_start=False,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                table = app.query_one("#challenges", DataTable)
                self.assertEqual(table.row_count, 2)
                self.assertIn("Alpha", str(table.get_row("a")))
                self.assertIn("1.3K", str(table.get_row("a")))
                self.assertIn("—", str(table.get_row("b")))
                self.assertIn("완료 0/2", str(app.query_one("#banner", Static).render()))
                self.assertIn("토큰 1.3K", str(app.query_one("#banner", Static).render()))
                self.assertIn("입력 1,234 (캐시 1,000) · 출력 56",
                              str(app.query_one("#detail", Static).render()))
                summary_log = app.query_one("#events", RichLog)
                detail_log = app.query_one("#detail-events", RichLog)
                self.assertNotIn("Alpha 상세 분석", str(summary_log.lines))
                self.assertIn("Alpha 상세 분석", str(detail_log.lines))
                self.assertNotIn("Beta 상세 분석", str(detail_log.lines))
                await pilot.press("d")
                await pilot.pause()
                with LiveStore(state, create=False) as store:
                    self.assertEqual(store.summary()["solved_count"], 1)
                    self.assertEqual(store.conn.execute(
                        "SELECT state FROM challenges WHERE challenge_key='b'"
                    ).fetchone()[0], "open")
                self.assertIn("완료", str(table.get_row("a")))
                self.assertIn("문제 완료", str(summary_log.lines))
                await pilot.click(table, offset=(5, 2))
                await pilot.pause()
                self.assertEqual(app.selected_key, "b")
                self.assertIn("Beta 상세 분석", str(detail_log.lines))
                self.assertNotIn("Alpha 상세 분석", str(detail_log.lines))


class TerminalRouteTests(unittest.TestCase):
    def test_tty_uses_dashboard_and_plain_uses_line_output(self):
        with patch("ctfh_core.cli.sys.stdin") as stdin, \
                patch("ctfh_core.cli.sys.stdout") as stdout, \
                patch("ctfh_core.live_tui.run_terminal_dashboard", new_callable=AsyncMock) as tui, \
                patch("ctfh_core.live_start.start_contest", new_callable=AsyncMock) as plain:
            stdin.isatty.return_value = True
            stdout.isatty.return_value = True
            tui.return_value = {"state": "idle"}
            plain.return_value = {"state": "idle"}
            self.assertEqual(main(["start", "https://ctf.example/challenges", "--model", "gpt-6-sol",
                                   "--thinking", "xhigh"]), 0)
            tui.assert_awaited_once()
            self.assertEqual(tui.await_args.kwargs["model"], "gpt-6-sol")
            self.assertEqual(tui.await_args.kwargs["reasoning_effort"], "xhigh")
            self.assertEqual(main(["start", "https://ctf.example/challenges", "--plain",
                                   "--reasoning-effort", "high"]), 0)
            plain.assert_awaited_once()
            self.assertEqual(plain.await_args.kwargs["reasoning_effort"], "high")
