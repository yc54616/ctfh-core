import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ctfh_core import native
from ctfh_core.cli import main

SITE_SOLVED = native.site_solved  # the real one; Base replaces it so tests never drive Chrome


class FakeTmux:
    session = "ctfh-test"

    def __init__(self):
        self.open, self.sent, self.screens = {}, [], {}

    def exists(self):
        return True

    def windows(self):
        return set(self.open)

    def new_window(self, name, cwd, argv):
        self.open[name] = argv

    def kill(self, name):
        self.open.pop(name, None)

    def select(self, name):
        self.selected = name
        return name in self.open

    def send(self, name, text):
        self.sent.append((name, text))

    def capture(self, name):
        return self.screens.get(name, "")


class FakeRunner:
    def __init__(self, **answers):
        self.answers = {name: list(values) for name, values in answers.items()}
        self.calls = []

    def __call__(self, opts, cwd, prompt, schema, work, name, timeout=3600, show=None, redact=()):
        self.calls.append((name, Path(cwd), prompt))
        if show is not None:
            show(f"{name} 진행")
        value = self.answers[name].pop(0)
        return value(cwd) if callable(value) else value


def make_challenge(root: Path, slug: str, solved=False) -> Path:
    chal = root / "challenges" / slug
    (chal / "files").mkdir(parents=True)
    (chal / "challenge.md").write_text(f"# {slug}\n")
    (chal / "challenge.json").write_text(json.dumps({"name": slug, "solved": solved}))
    return chal


def write_solution(chal: Path, flag="FLAG{abc}", reproduce=True):
    printed = 'print("FLAG{" + "abc" + "}")' if reproduce else 'print("nothing")'
    (chal / "solve.py").write_text(printed + "\n")
    (chal / "flag.txt").write_text(flag + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "contest"
        self.contest = native.init_contest("https://ctf.example/", self.root)
        self.home = Path(self.temp.name) / "codex-home"
        for target, value in (("codex_home", self.home), ("rollout_homes", [self.home]),
                              ("_meminfo", 32 << 30), ("set_memory_slice", None),
                              ("site_solved", None), ("probe_endpoint", "unknown"),
                              ("api_reachable", True)):
            patcher = patch(f"ctfh_core.native.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def checker(self, runner=None, **options):
        options.setdefault("tick", 0)
        opts = native.Options(**options)
        checker = native.Checker(self.contest, opts, FakeTmux(), runner or FakeRunner())
        self.addCleanup(checker.pool.shutdown, wait=True)
        return checker

    def settle(self, checker, rounds=6):
        for _ in range(rounds):
            checker.tick()
            for future in list(checker.jobs.values()):
                future.result(timeout=30)
        checker.tick()

    def status(self, slug):
        return native.load_status(self.root / "challenges" / slug)


class LayoutTests(Base):
    def test_names_and_contest_files(self):
        self.assertEqual(native.contest_slug("https://CTF.Example.com/play/"), "ctf-example-com-play")
        self.assertEqual(native.window_name("123"), "c123")
        self.assertEqual(native.session_name(Path("/x/Dreamhack 2026")), "ctfh-dreamhack-2026")
        self.assertIn('url = "https://ctf.example/"', (self.root / "contest.toml").read_text())
        (self.root / "contest.toml").write_text('url = "https://ctf.example/"\nflag_regex = "FLAG\\\\{.+\\\\}"\n')
        native.init_contest("https://other.example/", self.root)
        self.assertEqual(self.contest.flag_regex(), "FLAG\\{.+\\}")

    def test_generated_rules_are_refreshed_but_user_rules_kept(self):
        chal = make_challenge(self.root, "pwn")
        (chal / "AGENTS.md").write_text("# 문제 풀이 규칙 (ctfh2 native)\n예전 규칙\n")
        native.ensure_challenge(self.contest, chal)
        self.assertIn("challenge.json의 name과 같은지", (chal / "AGENTS.md").read_text())
        (chal / "AGENTS.md").write_text("# 내 규칙\n")
        native.ensure_challenge(self.contest, chal)
        self.assertEqual((chal / "AGENTS.md").read_text(), "# 내 규칙\n")

    def test_rescan_uses_low_effort_but_first_collect_keeps_full_effort(self):
        efforts = []
        def collector(opts, cwd, *rest, **kw):
            efforts.append((opts.model, opts.effort))
            if not (cwd / "challenges" / "web-1").exists():
                make_challenge(cwd, "web-1")
            return {"login": "ok", "challenges": [], "notes": ""}
        checker = self.checker(runner=collector, effort="xhigh")
        checker.job_collect()  # nothing collected yet: login may be needed
        checker.job_collect()
        self.assertEqual(efforts, [("gpt-6-sol", "xhigh"), ("gpt-6-luna", "low")])

    def test_collect_writes_site_info_and_normalizes_folders(self):
        def collector(cwd):
            make_challenge(cwd, "web-1")
            odd = cwd / "challenges" / "Web Easy!"
            odd.mkdir()
            (odd / "challenge.md").write_text("# odd\n")
            (cwd / "challenges" / "no-md").mkdir()
            return {"login": "ok", "platform": "CTFd", "flag_regex": "FLAG\\{.+\\}",
                    "submit_hint": "modal", "challenges": [], "notes": ""}
        runner = FakeRunner(collect=[collector])
        native.collect(self.contest, native.Options(), runner)
        self.assertEqual([c.name for c in self.contest.challenges()], ["web-1", "web-easy"])
        self.assertEqual(self.contest.flag_regex(), "FLAG\\{.+\\}")
        self.assertEqual(self.status("web-1")["state"], "pending")
        self.assertIn("[검사기 작업]", (self.root / "challenges" / "web-1" / "AGENTS.md").read_text())
        name, cwd, prompt = runner.calls[0]
        self.assertEqual((name, cwd), ("collect", self.contest.root))
        self.assertIn("needs_user", prompt)
        self.assertNotIn("secret-fill", prompt)


class VerifyTests(Base):
    def setUp(self):
        super().setUp()
        self.chal = make_challenge(self.root, "pwn")

    def test_reproduced_flag_passes_and_log_hides_flag(self):
        write_solution(self.chal)
        result = native.verify_flag(self.chal, "FLAG\\{[a-z]+\\}", 30)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.flag_sha256, native.flag_hash("FLAG{abc}"))
        self.assertIn("<FLAG>", (self.chal / ".ctfh-check" / "rerun.log").read_text())
        self.assertNotIn("FLAG{abc}", (self.chal / ".ctfh-check" / "rerun.log").read_text())

    def test_rejections(self):
        self.assertIn("flag.txt", native.verify_flag(self.chal, "", 30).reason)
        (self.chal / "flag.txt").write_text("a\nb\n")
        self.assertIn("한 줄", native.verify_flag(self.chal, "", 30).reason)
        write_solution(self.chal, flag="nope")
        self.assertIn("정규식", native.verify_flag(self.chal, "FLAG\\{.+\\}", 30).reason)
        write_solution(self.chal, reproduce=False)
        self.assertIn("같은 flag가 나오지", native.verify_flag(self.chal, "", 30).reason)
        (self.chal / "solve.py").write_text('print("FLAG{abc}")\n')
        self.assertIn("그대로", native.verify_flag(self.chal, "", 30).reason)
        (self.chal / "solve.py").write_text("import time\ntime.sleep(30)\n")
        started = time.time()
        self.assertIn("끝나지 않았습니다", native.verify_flag(self.chal, "", 1).reason)
        self.assertLess(time.time() - started, 10)


class CodexCommandTests(unittest.TestCase):
    def test_interactive_and_exec_argv(self):
        opts = native.Options(model="gpt-6-sol", effort="xhigh")
        argv = native.codex_argv(opts, Path("/c"), "go", interactive=True)
        self.assertEqual(argv, ["codex", "--no-daemon", "--dangerously-bypass-approvals-and-sandbox", "-C", "/c",
                                "-m", "gpt-6-sol", "-c", 'model_reasoning_effort="xhigh"', "go"])
        argv = native.codex_argv(native.Options(binary="codex-secondary"), Path("/c"), "p", interactive=False,
                                 schema=Path("/s.json"), output=Path("/o.json"))
        self.assertEqual(argv[:3], ["codex-secondary", "exec", "--skip-git-repo-check"])
        self.assertNotIn("--no-daemon", argv)
        self.assertEqual(argv[-5:], ["--output-schema", "/s.json", "-o", "/o.json", "p"])
        self.assertEqual(native.env_prefix("codex-secondary"), [])
        self.assertEqual(native.env_prefix("codex")[:3], ["env", "-u", "CODEX_SQLITE_HOME"])

    def test_options_round_trip_for_checker_window(self):
        opts = native.Options(binary="codex-secondary", model="m", effort="high", max_parallel=2,
                              submit=False, rescan_seconds=300)
        argv = opts.to_args()
        self.assertIn("--no-submit", argv)
        self.assertEqual(argv[argv.index("--rescan-minutes") + 1], "5")
        self.assertEqual(argv[argv.index("--rescan-thinking") + 1], "low")
        self.assertEqual(argv[argv.index("--rescan-model") + 1], "gpt-6-luna")
        argv = native.Options(rescan_model=None, rescan_effort=None).to_args()
        self.assertEqual((argv[argv.index("--rescan-model") + 1], argv[argv.index("--rescan-thinking") + 1]), ("same", "same"))
        self.assertEqual((argv[argv.index("--submit-thinking") + 1], argv[argv.index("--review-thinking") + 1]),
                         ("low", "medium"))



class StreamTests(unittest.TestCase):
    def test_exec_progress_is_shown_logged_and_flag_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            fake = temp / "codex"
            events = [
                {"type": "item.started", "item": {"type": "command_execution",
                                                  "command": "/usr/bin/zsh -lc 'rtk proxy chrome-use open https://x'"}},
                {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                                    "aggregated_output": "FLAG{abc}\nok\n"}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "문제 5개를 찾았다 FLAG{abc}"}},
                {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000,
                                                     "output_tokens": 30}},
            ]
            script = "import json, sys\nargs = sys.argv\n"
            script += f"for e in {events!r}: print(json.dumps(e), flush=True)\n"
            script += "open(args[args.index('-o') + 1], 'w').write(json.dumps({'ok': True}))\n"
            fake.write_text("#!" + shutil.which("python3") + "\n" + script)
            fake.chmod(0o755)
            shown = []
            result = native.run_codex_json(native.Options(binary=str(fake)), temp, "p", {}, temp / "w", "collect",
                                           show=shown.append, redact=("FLAG{abc}",))
            self.assertEqual(result, {"ok": True})
            self.assertEqual(shown[0], "$ chrome-use open https://x")
            self.assertEqual(shown[1], "  └ exit 0 · 2줄")
            self.assertIn("› 문제 5개를 찾았다 <FLAG>", shown)
            self.assertIn("토큰: 입력 1,200 (캐시 1,000) · 출력 30", shown)
            for name in ("collect.log", "collect.jsonl"):
                self.assertNotIn("FLAG{abc}", (temp / "w" / name).read_text())


class RolloutTests(unittest.TestCase):
    def test_secondary_rollouts_are_searched_in_shared_history(self):
        homes = native.rollout_homes("codex-secondary")
        self.assertEqual(homes, [Path.home() / ".codex-secondary", Path.home() / ".codex"])


    def test_find_cli_rollout_and_read_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            home, cwd = Path(temp), Path("/contest/challenges/pwn")
            day = home / "sessions" / time.strftime("%Y/%m/%d")
            day.mkdir(parents=True)
            def write(name, source, events):
                path = day / f"rollout-{name}.jsonl"
                lines = [{"type": "session_meta", "payload": {"cwd": str(cwd), "source": source}}]
                lines += [{"type": "event_msg", "payload": event} for event in events]
                path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
                return path
            solver = write("a", "cli", [
                {"type": "task_started"},
                {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 1234}}},
                {"type": "task_complete"}])
            time.sleep(0.01)
            write("b", "exec", [{"type": "task_started"}])
            found = native.find_rollout(cwd, home, time.time() - 5)
            self.assertEqual(found, solver)
            info = native.rollout_info(found)
            self.assertEqual((info["tokens"], info["idle"]), (1234, True))
            with open(solver, "a") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
            self.assertFalse(native.rollout_info(solver)["idle"])
            self.assertIsNone(native.find_rollout(Path("/elsewhere"), home, time.time() - 5))


class CheckerFlowTests(Base):
    def test_launch_respects_parallel_limit(self):
        for slug in ("a", "b", "c"):
            make_challenge(self.root, slug)
        checker = self.checker(max_parallel=2)
        checker.tick()
        self.assertEqual(checker.tmux.windows(), {"a", "b"})
        argv = checker.tmux.open["a"]
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(argv[argv.index("-C") + 1], str(self.root / "challenges" / "a"))
        self.assertEqual(self.status("c")["state"], "pending")
        checker.tmux.kill("a")
        native.mark_done(self.root, ["a"])
        checker.tick()
        self.assertEqual(checker.tmux.windows(), {"b", "c"})
        self.assertEqual(self.status("a")["state"], "user_solved")

    def test_default_opens_every_challenge(self):
        for slug in ("a", "b", "c", "d", "e", "f"):
            make_challenge(self.root, slug)
        checker = self.checker()
        self.assertEqual(checker.opts.max_parallel, 0)
        checker.tick()
        self.assertEqual(checker.tmux.windows(), {"a", "b", "c", "d", "e", "f"})

    def test_verify_submit_review_done(self):
        chal = make_challenge(self.root, "web")
        runner = FakeRunner(submit=[{"result": "accepted", "message": "Correct"}],
                            review=[{"writeup_ok": True, "matches_solve": True, "expected_approach": "x",
                                     "stage": "n/a", "issues": []}])
        checker = self.checker(runner)
        checker.tick()
        write_solution(chal)
        (chal / "writeup.md").write_text("# web\n")
        self.settle(checker)
        status = self.status("web")
        self.assertEqual(status["state"], "done")
        self.assertEqual([c[0] for c in runner.calls], ["submit", "review"])
        self.assertEqual(runner.calls[0][1], chal / ".ctfh-check")
        self.assertEqual(status["submissions"][0]["result"], "accepted")
        self.assertNotIn("FLAG{abc}", json.dumps(status))
        self.assertNotIn("web", checker.tmux.windows())
        self.assertTrue(any("정답 처리됨" in text for _, text in checker.tmux.sent))

    def test_bad_flag_is_sent_back_and_wrong_flag_is_never_resubmitted(self):
        chal = make_challenge(self.root, "rev")
        runner = FakeRunner(submit=[{"result": "wrong", "message": "Incorrect"}])
        checker = self.checker(runner)
        checker.tick()
        write_solution(chal, reproduce=False)
        self.settle(checker)
        self.assertEqual(self.status("rev")["state"], "needs_fix")
        self.assertTrue(checker.tmux.sent[-1][1].startswith("[검사기] flag 검증 실패"))
        write_solution(chal)
        self.settle(checker)
        self.assertEqual(self.status("rev")["state"], "needs_fix")
        os.utime(chal / "flag.txt", ns=(time.time_ns() + 10**9,) * 2)
        self.settle(checker)
        self.assertIn("오답 처리된 flag", self.status("rev")["reason"])
        self.assertEqual([c[0] for c in runner.calls], ["submit"])

    def test_interrupted_submit_is_not_replayed_until_user_approves(self):
        chal = make_challenge(self.root, "misc")
        write_solution(chal)
        native.save_status(chal, {"state": "submitting", "flag_sha256": native.flag_hash("FLAG{abc}"),
                                  "submissions": [{"sha256": native.flag_hash("FLAG{abc}"), "result": "pending"}]})
        runner = FakeRunner(submit=[{"result": "accepted", "message": ""}])
        checker = self.checker(runner)
        self.settle(checker)
        self.assertEqual(self.status("misc")["state"], "unknown")
        self.assertEqual(runner.calls, [])
        native.approve(self.root, "misc")
        self.settle(checker)
        self.assertEqual(self.status("misc")["state"], "accepted")
        self.assertEqual(len(self.status("misc")["submissions"]), 2)

    def test_login_lost_during_submit_retries_later(self):
        chal = make_challenge(self.root, "crypto")
        runner = FakeRunner(submit=[{"result": "needs_user", "message": "login"}])
        checker = self.checker(runner)
        checker.tick()
        write_solution(chal)
        self.settle(checker)
        status = self.status("crypto")
        self.assertEqual(status["state"], "verified")
        self.assertEqual(status["submissions"], [])
        self.assertGreater(status["retry_after"], time.time())

    def test_site_solved_and_no_submit_mode(self):
        make_challenge(self.root, "solved-elsewhere", solved=True)
        chal = make_challenge(self.root, "local")
        runner = FakeRunner(review=[{"writeup_ok": False, "matches_solve": True, "expected_approach": "x",
                                     "stage": "leak", "issues": ["단계 설명 부족"]}])
        checker = self.checker(runner, submit=False)
        checker.tick()
        self.assertEqual(self.status("solved-elsewhere")["state"], "site_solved")
        write_solution(chal)
        (chal / "writeup.md").write_text("# local\n")
        self.settle(checker)
        self.assertEqual(self.status("local")["state"], "verified")
        self.assertEqual([c[0] for c in runner.calls], ["review"])
        self.assertIn("단계 설명 부족", checker.tmux.sent[-1][1])

    def test_refused_session_is_blocked_not_nudged_until_model_changes(self):
        chal = make_challenge(self.root, "kernel")
        checker = self.checker(binary="codex-secondary", model="gpt-6-astra", idle_seconds=0)
        checker.tick()
        argv = checker.tmux.open["kernel"]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-6-astra")
        day = self.home / "sessions" / time.strftime("%Y/%m/%d")
        day.mkdir(parents=True)
        rollout = day / "rollout-x.jsonl"
        rollout.write_text("\n".join(json.dumps(line) for line in [
            {"type": "session_meta", "payload": {"cwd": str(chal), "source": "cli"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}}]) + "\n")
        old = time.time() - 30
        os.utime(rollout, (old, old))
        checker.tmux.screens["kernel"] = "ⓘ This content can’t be shown\n  Apply for Daybreak: https://openai.com/form/"
        checker.tick()
        self.assertEqual(self.status("kernel")["state"], "blocked")
        self.assertIn("gpt-6-astra", self.status("kernel")["reason"])
        self.assertEqual(checker.tmux.sent, [])
        self.assertNotIn("kernel", checker.tmux.windows())
        checker.tick()
        self.assertNotIn("kernel", checker.tmux.windows())  # same model: no retry loop
        retry = self.checker(binary="codex-secondary")  # default model
        retry.tick()
        argv = retry.tmux.open["kernel"]
        self.assertEqual(argv[argv.index("-m") + 1], native.DEFAULT_MODEL)
        self.assertEqual(self.status("kernel")["state"], "solving")
        self.assertNotIn("reason", self.status("kernel"))

    def idle_rollout(self, chal):
        day = self.home / "sessions" / time.strftime("%Y/%m/%d")
        day.mkdir(parents=True, exist_ok=True)
        rollout = day / f"rollout-{chal.name}.jsonl"
        rollout.write_text("\n".join(json.dumps(line) for line in [
            {"type": "session_meta", "payload": {"cwd": str(chal), "source": "cli"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}}]) + "\n")
        old = time.time() - 30
        os.utime(rollout, (old, old))

    def test_board_shows_stalled_solvers(self):
        a, b = make_challenge(self.root, "a"), make_challenge(self.root, "b")
        make_challenge(self.root, "c")
        checker = self.checker(idle_seconds=600, nudge_limit=2)
        checker.tick()
        self.idle_rollout(a)
        status = self.status("b")
        status["nudges"] = 2
        native.save_status(b, status)
        checker.tmux.kill("b")
        checker.tick()
        rows = {line.split()[0]: line for line in checker.board().splitlines()[2:]}
        self.assertRegex(rows["a"], r"\d+초째 멈춤 \(재촉 0/2\)")
        self.assertIn("창 없음 (재시작 한도)", rows["b"])
        self.assertIn("시작 중", rows["c"])
        self.assertEqual(native._ago(3725), "1시간 2분")

    def append_rollout(self, chal, *payloads):
        rollout = self.home / "sessions" / time.strftime("%Y/%m/%d") / f"rollout-{chal.name}.jsonl"
        with open(rollout, "a") as handle:
            for payload in payloads:
                handle.write(json.dumps({"type": "event_msg", "payload": payload}) + "\n")

    def test_offline_pauses_nudges_and_wakes_windows_when_back(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker(idle_seconds=0)
        checker.tick()
        self.idle_rollout(chal)
        with patch("ctfh_core.native.api_reachable", return_value=False):
            checker.last_outage_check = 0
            checker.tick()
            checker.tick()
            self.assertEqual(checker.outage, native.OFFLINE)
            self.assertFalse(checker.tmux.sent)  # no nudge while every window is cut off
            self.assertIn("!! 일시정지", checker.board())
        self.assertEqual(self.status("heap").get("nudges", 0), 0)
        checker.last_outage_check = 0
        checker.tick()
        self.assertIsNone(checker.outage)
        self.assertIn("계속 진행", checker.tmux.sent[0][1])

    def test_usage_limit_pauses_until_a_probe_turn_runs(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker(idle_seconds=600)
        checker.tick()
        self.idle_rollout(chal)
        self.append_rollout(chal, {"type": "token_count", "info": None, "rate_limits": {
            "primary": {"used_percent": 100.0, "resets_at": time.time() + 3600},
            "credits": {"has_credits": False, "unlimited": False}}})
        checker.tick()
        checker.last_outage_check = 0
        checker.tick()
        self.assertIn("사용 한도", checker.outage)
        self.assertFalse(checker.tmux.sent)
        checker.last_probe = 0
        checker.tick()
        self.assertEqual(len(checker.tmux.sent), 1)  # one window tries a turn to see if the limit lifted

    def test_contest_end_holds_unsolved_windows_and_counts_down(self):
        from datetime import datetime, timedelta, timezone
        make_challenge(self.root, "heap")
        chal = make_challenge(self.root, "writeup")
        checker = self.checker()
        checker.tick()
        status = self.status("writeup")
        status["state"] = "accepted"
        native.save_status(chal, status)
        config = self.root / "contest.toml"
        template = config.read_text()
        soon = datetime.now(timezone.utc) + timedelta(hours=2, minutes=5)
        config.write_text(template.replace('ends_at = ""', f'ends_at = "{soon.isoformat()}"'))
        self.assertIn("대회 종료까지 2시간", checker.board())
        config.write_text(template.replace('ends_at = ""', f'ends_at = "{(soon - timedelta(hours=3)).isoformat()}"'))
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "hold")
        self.assertNotIn(native.window_name("heap"), checker.tmux.windows())
        self.assertEqual(self.status("writeup")["state"], "accepted")  # still finishing its writeup
        self.assertIn("대회 종료됨", checker.board())
        self.assertIn("풀이 창을 다시 열었습니다", checker.act("restart", "heap"))
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "solving")  # stopped once; r still works after the end

    def test_local_test_flag_is_not_submitted(self):
        import zipfile
        chal = make_challenge(self.root, "pwn")
        self.assertIsNone(native.fake_flag_reason(chal, "FLAG{fake_flag}"))  # file-only: the files may hold it
        (chal / "remote.txt").write_text("nc host 1337\n")
        self.assertIn("가짜", native.fake_flag_reason(chal, "FLAG{this_is_fake}"))
        with zipfile.ZipFile(chal / "files" / "dist.zip", "w") as archive:
            archive.writestr("deploy/flag", "FLAG{l0cal_d3ploy_v4lue}\n")
        self.assertIn("배포 파일", native.fake_flag_reason(chal, "FLAG{l0cal_d3ploy_v4lue}"))
        self.assertIsNone(native.fake_flag_reason(chal, "FLAG{r3al_s3rv3r_v4lue}"))
        write_solution(chal, flag="FLAG{fake_flag}")
        result = native.verify_flag(chal, "", 30)
        self.assertFalse(result.ok)
        self.assertIn("가짜", result.reason)

    def test_keys_on_finished_challenge_are_refused_up_front(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker()
        checker.tick()
        self.assertIsNone(checker.refusal("hold", "heap"))
        native.save_status(chal, {**self.status("heap"), "state": "done"})
        self.assertIn("이미 끝난 문제", checker.refusal("done", "heap"))
        self.assertIn("done 상태라 할 수 없습니다", checker.refusal("hold", "heap"))

    def test_no_progress_in_notes_hands_solver_to_user(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker(idle_seconds=0, nudge_limit=5)
        checker.tick()
        self.idle_rollout(chal)
        notes = chal / "work" / "NOTES.md"
        notes.parent.mkdir(exist_ok=True)
        notes.write_text("leak found\n")
        checker.tick()  # nudge 1 records the notes
        notes.write_text("leak found\nrop chain\n")
        checker.tick()  # nudge 2: progress
        checker.tick()  # nudge 3: notes unchanged once
        self.assertEqual((self.status("heap")["state"], self.status("heap")["nudges"]), ("solving", 3))
        checker.tick()  # unchanged twice in a row: stop nudging
        status = self.status("heap")
        self.assertEqual((status["state"], status["nudges"]), ("manual", 3))
        self.assertIn("진전 없음", status["reason"])
        self.assertIn("직접 확인 필요 (manual 1개): heap", checker.board())

    def test_nudge_limit_hands_over_and_user_typing_resumes(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker(idle_seconds=0)
        self.assertEqual(checker.opts.nudge_limit, 3)
        checker.tick()
        self.idle_rollout(chal)
        self.append_rollout(chal, {"type": "token_count", "info": None, "rate_limits": {
            "primary": {"used_percent": 42.0, "resets_at": 1792048281}}})
        status = self.status("heap")
        status["nudges"] = 3
        native.save_status(chal, status)
        checker.tick()
        status = self.status("heap")
        self.assertEqual(status["state"], "manual")
        self.assertIn("재촉 한도 3회", status["reason"])
        self.assertIn("Codex 주간 사용량 42%", checker.board())
        self.assertNotIn("!! Codex", checker.board())
        self.assertRegex(checker.board(), r"heap +manual .*수동 대기")
        self.append_rollout(chal, {"type": "task_started"})  # the user typed into the window
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "solving")
        self.append_rollout(chal, {"type": "task_complete"})
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "manual")

    def test_user_nudge_and_restart_do_not_spend_the_nudge_limit(self):
        chal = make_challenge(self.root, "heap")
        make_challenge(self.root, "done-one", solved=True)
        checker = self.checker()
        checker.tick()
        status = self.status("heap")
        status.update(state="manual", reason="재촉 한도 3회 도달: 직접 확인 필요", nudges=3)
        native.save_status(chal, status)
        self.assertEqual(checker.act("nudge", "heap"), "heap: 재촉했습니다.")
        self.assertIn("다른 접근", checker.tmux.sent[-1][1])
        status = self.status("heap")
        self.assertEqual((status["state"], status["nudges"], status.get("reason")), ("solving", 3, None))
        checker.tmux.kill(native.window_name("heap"))
        self.assertIn("열린 창이 없습니다", checker.act("nudge", "heap"))
        self.assertEqual(checker.act("restart", "heap"), "heap: 풀이 창을 다시 열었습니다.")
        self.assertIn(native.window_name("heap"), checker.tmux.windows())
        self.assertEqual(self.status("heap")["nudges"], 3)
        self.assertIn("이미 열려", checker.act("restart", "heap"))
        self.assertIn("할 수 없습니다", checker.act("restart", "done-one"))
        self.assertIn("보류했습니다", checker.act("hold", "heap"))
        self.assertNotIn(native.window_name("heap"), checker.tmux.windows())
        checker.tick()  # a held challenge is not reopened automatically
        self.assertNotIn(native.window_name("heap"), checker.tmux.windows())
        self.assertRegex(checker.board(), r"heap +hold .*보류 \(r로 재개\)")
        self.assertIn("풀이 창을 다시 열었습니다", checker.act("restart", "heap"))
        self.assertEqual(self.status("heap")["state"], "solving")

    def test_remote_flag_that_changes_per_run_is_replaced(self):
        chal = make_challenge(self.root, "pwn")
        (chal / "remote.txt").write_text("nc host 1337\n")
        (chal / "solve.py").write_text('print("FLAG{" + "n3w_1nstanc3" + "}")\n')
        (chal / "flag.txt").write_text("FLAG{0ld_1nstanc3}\n")
        result = native.verify_flag(chal, r"FLAG\{[^}]+\}", 30)
        self.assertTrue(result.ok and result.rewritten)
        self.assertEqual((chal / "flag.txt").read_text().strip(), "FLAG{n3w_1nstanc3}")
        self.assertNotIn("n3w_1nstanc3", (chal / ".ctfh-check" / "rerun.log").read_text())
        (chal / "remote.txt").write_text("")  # a local challenge must reproduce exactly
        (chal / "flag.txt").write_text("FLAG{0ld_1nstanc3}\n")
        self.assertFalse(native.verify_flag(chal, r"FLAG\{[^}]+\}", 30).ok)
        (chal / "solve.py").write_text('print("<FLAG>")\n')
        self.assertFalse(native.verify_flag(chal, "", 30).ok)

    def test_rerun_against_a_closed_instance_waits_for_the_server(self):
        chal = make_challenge(self.root, "pwn")
        (chal / "remote.txt").write_text("nc host 1337\n")
        write_solution(chal, reproduce=False)
        checker = self.checker()
        with patch.object(checker, "remote_state", return_value=("down", ["host:1337"])):
            checker.job_check(chal, (chal / "flag.txt").stat().st_mtime_ns)
        status = self.status("pwn")
        self.assertTrue(status.get("remote_down"))
        self.assertTrue(status["reason"].startswith(native.REMOTE_DOWN_REASON))

    def test_crashed_checker_comes_back_but_q_ends_it(self):
        import subprocess
        runs = Path(self.temp.name) / "runs"
        script = f'echo x >> {runs}; [ $(wc -l < {runs}) -ge 2 ]'  # crash once, then quit normally
        subprocess.run(native.restart_loop(["sh", "-c", script], delay=0), stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=30)
        self.assertEqual(len(runs.read_text().split()), 2)
        runs.unlink()
        subprocess.run(native.restart_loop(["sh", "-c", f"echo x >> {runs}; exit 3"], delay=0),
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertEqual(len(runs.read_text().split()), 5)  # a checker that cannot start stops retrying

    def test_skipped_category_is_held_and_report_counts_every_session(self):
        chal = make_challenge(self.root, "geo")
        (chal / "challenge.json").write_text(json.dumps({"name": "geo", "category": "OSINT", "points": 100}))
        make_challenge(self.root, "heap")
        checker = self.checker(skip_categories=("osint",))
        checker.tick()
        self.assertEqual(self.status("geo")["state"], "hold")
        self.assertEqual(self.status("heap")["state"], "solving")
        self.assertIn("--skip-category", checker.opts.to_args())
        checker.spent = {"heap": {"a.jsonl": 1000, "b.jsonl": 500}}
        checker.write_report()
        report = (self.root / ".ctfh" / "report.md").read_text()
        self.assertIn("| OSINT | 1 | 0 | 0 | - |", report)
        self.assertIn("| heap | - | - | solving | 1,500 |", report)
        checker.act("restart", "geo")
        self.assertEqual(self.status("geo")["state"], "solving")

    def test_submit_and_review_run_with_their_own_effort(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker(effort="xhigh")
        seen = []
        with patch("ctfh_core.native.submit", side_effect=lambda contest, chal, opts, runner:
                   seen.append(("submit", opts.model, opts.effort)) or {"result": "unknown", "message": ""}), \
             patch("ctfh_core.native.review", side_effect=lambda chal, opts, runner:
                   seen.append(("review", opts.model, opts.effort)) or {}):
            checker.job_submit(chal)
            checker.job_review(chal, 0)
        self.assertEqual(seen, [("submit", "gpt-6-sol", "low"), ("review", "gpt-6-sol", "medium")])

    def test_live_limit_and_handled_end_survive_a_checker_restart(self):
        from datetime import datetime, timedelta, timezone
        make_challenge(self.root, "heap")
        checker = self.checker()
        checker.tick()
        checker.set_parallel(2)
        config = self.root / "contest.toml"
        ended = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        config.write_text(config.read_text().replace('ends_at = ""', f'ends_at = "{ended}"'))
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "hold")
        checker.act("restart", "heap")  # keep practicing after the end
        again = self.checker()
        self.assertEqual(again.opts.max_parallel, 2)
        again.tick()
        self.assertEqual(self.status("heap")["state"], "solving")  # the end is not handled twice
        self.assertEqual(self.checker(max_parallel=5).opts.max_parallel, 5)  # a new setting wins over the live one

    def test_limited_slots_open_easiest_first_and_limit_changes_live(self):
        for slug, solves in (("hard", 5), ("easy", 50), ("unknown", None)):
            chal = make_challenge(self.root, slug)
            (chal / "challenge.json").write_text(json.dumps({"name": slug, "solved": False, "solves": solves}))
        checker = self.checker(max_parallel=1)
        checker.tick()
        self.assertEqual(checker.tmux.windows(), {native.window_name("easy")})
        self.assertIn("동시 창 1", checker.board())
        self.assertEqual(checker.set_parallel(1), "동시 풀이 창: 2")
        checker.tick()
        self.assertIn(native.window_name("hard"), checker.tmux.windows())
        self.assertEqual(checker.set_parallel(-5), "동시 풀이 창: 전체")
        checker.tick()
        self.assertEqual(len(checker.tmux.windows()), 3)

    def test_contest_rules_reach_solver_rules_and_board(self):
        chal = make_challenge(self.root, "heap")
        native.ensure_challenge(self.contest, chal)
        self.assertIn("(규칙 페이지를 찾지 못함)", (chal / "AGENTS.md").read_text())
        self.contest.state_dir.mkdir(parents=True, exist_ok=True)
        (self.contest.state_dir / "site.json").write_text(json.dumps({"rules": "[금지] AI 사용 금지"}))
        native.ensure_challenge(self.contest, chal)  # generated rules refresh when the site rules change
        self.assertIn("[금지] AI 사용 금지", (chal / "AGENTS.md").read_text())
        self.assertIn("!! 대회 규칙: [금지] AI 사용 금지", self.checker().board())

    def test_rescan_key_starts_one_collect_now(self):
        make_challenge(self.root, "heap")
        started = __import__("threading").Event()
        def collector(*args, **kwargs):
            started.wait(5)
            return {"login": "ok", "challenges": [], "notes": ""}
        checker = self.checker(runner=collector, rescan_seconds=3600)
        self.assertIn("gpt-6-luna", checker.rescan_now())
        self.assertEqual(checker.rescan_now(), "이미 재수집 중입니다.")
        started.set()
        checker.jobs["__collect__"].result(timeout=5)
        self.assertIn("재수집을 시작", checker.rescan_now())
        checker.jobs["__collect__"].result(timeout=5)

    def test_marking_done_closes_the_window_and_sticks(self):
        chal = make_challenge(self.root, "heap")
        checker = self.checker()
        checker.tick()
        self.assertEqual(checker.act("done", "heap"), "heap: 해결로 표시하고 풀이 창을 닫았습니다.")
        self.assertTrue((chal / "USER_SOLVED").exists())
        self.assertNotIn(native.window_name("heap"), checker.tmux.windows())
        checker.tick()
        self.assertEqual(self.status("heap")["state"], "user_solved")
        self.assertNotIn(native.window_name("heap"), checker.tmux.windows())
        self.assertIn("이미 끝난", checker.act("done", "heap"))

    def test_dashboard_puts_manual_first_and_reopens_selected_row(self):
        from textual.widgets import DataTable
        from ctfh_core.native_tui import CheckerApp, NotesScreen
        make_challenge(self.root, "aaa")
        chal = make_challenge(self.root, "zzz")
        (chal / "work").mkdir()
        (chal / "work" / "NOTES.md").write_text("leak at 0x40\n")
        checker = self.checker()
        checker.tick()
        status = self.status("zzz")
        status.update(state="manual", nudges=3)
        native.save_status(chal, status)
        checker.tmux.kill(native.window_name("zzz"))

        async def drive():
            app = CheckerApp(checker)
            async with app.run_test(size=(140, 30)) as pilot:
                table = app.query_one(DataTable)
                for _ in range(50):
                    await pilot.pause(0.1)
                    if table.row_count == 2:
                        break
                self.assertEqual([table.get_row_at(i)[0].plain for i in range(2)], ["zzz", "aaa"])
                self.assertIn("직접 확인 필요 (manual 1개): zzz", str(app.query_one("#head").render()))
                await pilot.press("o")
                self.assertIsInstance(app.screen, NotesScreen)
                await pilot.press("r")  # ignored while the notes are open
                await pilot.press("escape")
                await pilot.press("r")
                for _ in range(50):
                    await pilot.pause(0.1)
                    if native.window_name("zzz") in checker.tmux.windows():
                        break
                await pilot.press("enter")
                self.assertEqual(checker.tmux.selected, native.window_name("zzz"))
                await pilot.press("d")
                await pilot.press("n")  # declined: nothing changes
                await pilot.press("d")
                await pilot.press("y")
                for _ in range(50):
                    await pilot.pause(0.1)
                    if self.status("zzz")["state"] == "user_solved":
                        break
                await pilot.pause(0.3)
                await pilot.press("d")  # finished now: refused before any question
                self.assertEqual(len(app.screen_stack), 1)

        asyncio.run(drive())
        status = self.status("zzz")
        self.assertEqual((status["state"], status["nudges"]), ("user_solved", 3))
        self.assertNotIn(native.window_name("zzz"), checker.tmux.windows())

    def test_remote_endpoints_come_from_challenge_notes_and_remote_txt(self):
        chal = make_challenge(self.root, "web")
        (chal / "challenge.md").write_text("접속: nc 203.0.113.5 31337\nhttp://chal.example.org:8080/\n"
                                           "문제 페이지 https://ctf.example:443/challenges\n")
        (chal / "work").mkdir()
        (chal / "work" / "NOTES.md").write_text("로컬 테스트 127.0.0.1:5000, localhost:8000\n")
        (chal / "remote.txt").write_text("https://web.example.org/\n")
        self.assertEqual(native.remote_endpoints(chal, "ctf.example"),
                         [("web.example.org", 443), ("203.0.113.5", 31337), ("chal.example.org", 8080)])

    def test_closed_server_waits_quietly_and_wakes_when_open(self):
        chal = make_challenge(self.root, "pwn")
        (chal / "challenge.md").write_text("# pwn\nnc 203.0.113.5 31337\n")
        checker = self.checker(idle_seconds=0, nudge_limit=1)
        checker.tick()
        status = self.status("pwn")
        status["nudges"] = 1  # limit already used up: the wake-up must still come
        native.save_status(chal, status)
        self.idle_rollout(chal)
        with patch("ctfh_core.native.probe_endpoint", return_value="closed"):
            for _ in range(3):
                checker.tick()
        status = self.status("pwn")
        self.assertTrue(status["remote_down"])
        self.assertIn("203.0.113.5:31337", status["reason"])
        self.assertEqual(len(checker.tmux.sent), 1)
        self.assertIn("서버가 닫혀", checker.tmux.sent[0][1])
        checker.probes.clear()
        with patch("ctfh_core.native.probe_endpoint", return_value="open"):
            checker.tick()
        status = self.status("pwn")
        self.assertNotIn("remote_down", status)
        self.assertNotIn("reason", status)
        self.assertEqual(status["nudges"], 0)
        self.assertIn("서버가 다시 열렸다: 203.0.113.5:31337", checker.tmux.sent[-1][1])

    def test_fixed_solve_py_is_rechecked_without_touching_flag(self):
        chal = make_challenge(self.root, "rev")
        checker = self.checker(FakeRunner(submit=[{"result": "accepted", "message": "Correct"}]))
        checker.tick()
        write_solution(chal, reproduce=False)
        self.settle(checker)
        self.assertEqual(self.status("rev")["state"], "needs_fix")
        (chal / "solve.py").write_text('print("FLAG{" + "abc" + "}")\n')
        self.settle(checker)
        self.assertEqual(self.status("rev")["state"], "needs_fix")  # still being edited
        old = time.time() - 60
        os.utime(chal / "solve.py", (old, old))
        self.settle(checker)
        self.assertEqual(self.status("rev")["state"], "accepted")

    def test_site_solved_poll_closes_window(self):
        chal = make_challenge(self.root, "heap")
        (chal / "challenge.json").write_text(json.dumps({"id": 7, "name": "Heap Note", "solved": False}))
        checker = self.checker()
        checker.tick()
        self.assertIn("heap", checker.tmux.windows())
        checker.solved_probe = lambda contest: [("7", "Heap Note")]
        checker.last_solved_poll = 0
        self.settle(checker)
        self.assertEqual(self.status("heap")["state"], "site_solved")
        self.assertNotIn("heap", checker.tmux.windows())
        other = make_challenge(self.root, "baby-rev")
        self.assertEqual(native.mark_site_solved(self.contest, set(), {"Baby Rev"}), ["baby-rev"])
        self.assertTrue(json.loads((other / "challenge.json").read_text())["solved"])

    def test_ctfd_solved_list_is_read_through_chrome(self):
        native._write_atomic(self.contest.state_dir / "site.json", json.dumps({"platform": "CTFd"}))
        answer = subprocess.CompletedProcess([], 0, 'eval @ https://ctf.example/\n'
                                             '{"success":true,"solved":[["7","Heap Note"]]}\n', "")
        with patch("ctfh_core.native.subprocess.run", return_value=answer) as run:
            self.assertEqual(SITE_SOLVED(self.contest), [("7", "Heap Note")])
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["chrome-use", "--session", "ctfh-status-contest", "eval"])
        self.assertIn("https://ctf.example/api/v1/challenges", argv[4])
        native._write_atomic(self.contest.state_dir / "site.json", json.dumps({"platform": "picoCTF"}))
        self.assertIsNone(SITE_SOLVED(self.contest))

    def test_solver_window_runs_under_memory_cap(self):
        make_challenge(self.root, "fuzz")
        with patch("ctfh_core.native.shutil.which", return_value="/usr/bin/systemd-run"), \
                patch("ctfh_core.native.set_memory_slice") as slice_limit:
            checker = self.checker(memory_max="auto")
            checker.tick()
        slice_limit.assert_called_once_with("32112M")  # 98% of 32G, shared by every solver window
        argv = checker.tmux.open["fuzz"]
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--scope", "--quiet"])
        self.assertIn("--slice=ctfh.slice", argv)
        self.assertFalse(any(arg.startswith("MemoryMax=") for arg in argv))
        self.assertIn("OOMPolicy=continue", argv)
        self.assertLess(argv.index("OOMPolicy=continue"), argv.index("--dangerously-bypass-approvals-and-sandbox"))
        self.assertEqual(native.memory_limit("28g"), "28G")
        self.assertIsNone(native.memory_limit("off"))
        with self.assertRaises(ValueError):
            native.memory_limit("lots")
        make_challenge(self.root, "plain")
        with patch("ctfh_core.native.shutil.which", return_value="/usr/bin/systemd-run"):
            off = self.checker(memory_max="off")
            off.tick()
        self.assertNotIn("systemd-run", off.tmux.open["plain"])

    def test_low_memory_delays_new_windows_without_lowering_parallel(self):
        for slug in ("a", "b"):
            make_challenge(self.root, slug)
        checker = self.checker(max_parallel=20)
        low = {"MemTotal": 32 << 30, "MemAvailable": 500 << 20}  # under 2% free
        with patch("ctfh_core.native._meminfo", side_effect=low.get):
            checker.tick()
            self.assertEqual(checker.tmux.windows(), set())
            self.assertIn("메모리 부족", checker.board())
        checker.tick()
        self.assertEqual(checker.tmux.windows(), {"a", "b"})
        self.assertNotIn("메모리 부족", checker.board())

    def test_exited_solver_is_restarted_with_backoff_and_limit(self):
        make_challenge(self.root, "heap")
        checker = self.checker(nudge_limit=1)
        checker.tick()
        checker.tmux.kill("heap")
        checker.tick()
        self.assertNotIn("heap", checker.tmux.windows())  # launched under a minute ago
        status = self.status("heap")
        status["launched_ts"] = time.time() - 120
        native.save_status(self.root / "challenges" / "heap", status)
        checker.tick()
        self.assertIn("heap", checker.tmux.windows())
        self.assertEqual(self.status("heap")["nudges"], 1)
        checker.tmux.kill("heap")
        status = self.status("heap")
        status["launched_ts"] = time.time() - 120
        native.save_status(self.root / "challenges" / "heap", status)
        checker.tick()
        self.assertNotIn("heap", checker.tmux.windows())


class SecretFillTests(unittest.TestCase):
    def test_fill_checks_permissions_and_host_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "contest"
            native.init_contest("https://ctf.example/", root)
            creds = Path(temp) / "credentials.toml"
            creds.write_text('["ctf.example"]\nusername = "me"\npassword = "s3cret-pw"\n')
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, stdout=f'"{page}"', stderr="")

            with patch("ctfh_core.native.CREDENTIALS", creds), \
                    patch("ctfh_core.native.subprocess.run", side_effect=fake_run):
                os.chmod(creds, 0o644)
                with self.assertRaisesRegex(ValueError, "chmod 600"):
                    native.secret_fill(root, "password", "@e3", "ctfh-collect")
                os.chmod(creds, 0o600)
                page = "https://evil.example/login"
                with self.assertRaisesRegex(ValueError, "채우지 않습니다"):
                    native.secret_fill(root, "password", "@e3", "ctfh-collect")
                page = "https://ctf.example/login"
                native.secret_fill(root, "password", "@e3", "ctfh-collect")
            argv, kwargs = calls[-1]
            self.assertEqual(argv, ["chrome-use", "fill", "@e3", "--from-env", "CTFH_SECRET",
                                    "--session", "ctfh-collect"])
            self.assertEqual(kwargs["env"]["CTFH_SECRET"], "s3cret-pw")
            self.assertTrue(all("s3cret-pw" not in " ".join(a) for a, _ in calls))


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxTests(unittest.TestCase):
    def test_paste_reaches_window_program(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "got.txt"
            tmux = native.Tmux(f"ctfh-test-{uuid.uuid4().hex[:8]}")
            reader = ["python3", "-c", f"import sys; open({str(out)!r}, 'w').write(sys.stdin.readline())"]
            tmux.new_session("checker", Path(temp), ["sleep", "30"])
            try:
                tmux.new_window("pwn-1", Path(temp), reader)
                self.assertEqual(tmux.windows(), {"checker", "pwn-1"})
                time.sleep(0.5)
                tmux.send("pwn-1", "[검사기] hello 'quoted' $HOME")
                deadline = time.time() + 10
                while not out.exists() and time.time() < deadline:
                    time.sleep(0.1)
                self.assertEqual(out.read_text().strip(), "[검사기] hello 'quoted' $HOME")
                tmux.new_window("web-10", Path(temp), ["sleep", "30"])
                self.assertTrue(tmux.select("web-10"))
                self.assertFalse(tmux.select("web"))
                tmux.kill("web")  # exact names only: no prefix match
                self.assertIn("web-10", tmux.windows())
                tmux.kill("web-10")
                self.assertNotIn("web-10", tmux.windows())
            finally:
                subprocess.run(["tmux", "kill-session", "-t", f"={tmux.session}"], capture_output=True)


class CliRouteTests(unittest.TestCase):
    def test_url_routes_to_native_flow_with_options(self):
        with patch("ctfh_core.native.go", return_value=0) as go:
            self.assertEqual(main(["https://ctf.example", "--secondary", "--thinking", "high",
                                   "--max-parallel", "3", "--no-submit", "--no-attach"]), 0)
            site, directory, opts = go.call_args.args
            self.assertEqual(site, "https://ctf.example")
            self.assertEqual((opts.binary, opts.effort, opts.max_parallel, opts.submit),
                             ("codex-secondary", "high", 3, False))
            self.assertFalse(go.call_args.kwargs["attach"])
            self.assertEqual(main(["--model", "gpt-6-sol", "--dir", "/tmp/x"]), 0)
            self.assertEqual(go.call_args.args[1], "/tmp/x")
            self.assertEqual(go.call_args.args[2].model, "gpt-6-sol")

    def test_done_and_status_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "contest"
            native.init_contest("https://ctf.example/", root)
            make_challenge(root, "a")
            self.assertEqual(main(["done", "a", "--dir", str(root)]), 0)
            self.assertTrue((root / "challenges" / "a" / "USER_SOLVED").exists())
            self.assertEqual(main(["done", "zzz", "--dir", str(root)]), 2)
            self.assertEqual(main(["status", "--dir", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
