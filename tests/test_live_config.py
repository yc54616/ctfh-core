import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ctfh_core.live_config import NativeLaunch, codex_environment, native_launch
from ctfh_core.live_agent import CodexExecRunner
from ctfh_core.cli import main


class NativeLaunchTests(unittest.TestCase):
    def test_ordinary_account_does_not_inherit_secondary_home(self):
        with patch.dict("os.environ", {"CODEX_HOME": "/tmp/other-account",
                                    "CODEX_SQLITE_HOME": "/tmp/other-sqlite"}):
            main = codex_environment("codex")
            secondary = codex_environment("codex-secondary")
        self.assertEqual(main["CODEX_HOME"], str(Path.home() / ".codex"))
        self.assertNotIn("CODEX_SQLITE_HOME", main)
        self.assertEqual(secondary["CODEX_HOME"], "/tmp/other-account")

    def test_main_default_and_explicit_secondary_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ctfh.toml").write_text(
                '[native]\nbinary="codex"\n'
                'dangerously_bypass_approvals_and_sandbox=false\n'
                '[native.secondary]\n'
                'dangerously_bypass_approvals_and_sandbox=true\n'
            )
            self.assertEqual(native_launch(root), NativeLaunch("codex", False))
            self.assertEqual(native_launch(root, "codex"), NativeLaunch("codex", False))
            self.assertEqual(native_launch(root, "codex-secondary"), NativeLaunch("codex-secondary", True))

    def test_opt_in_profile_and_explicit_binary_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(native_launch(root), NativeLaunch())
            (root / "ctfh.toml").write_text('[native]\nbinary="codex-secondary"\ndangerously_bypass_approvals_and_sandbox=true\n')
            self.assertEqual(native_launch(root), NativeLaunch("codex-secondary", True))
            self.assertEqual(native_launch(root, "codex-secondary"), NativeLaunch("codex-secondary", True))
            self.assertEqual(native_launch(root, "custom-codex"), NativeLaunch("custom-codex", False))
            with self.assertRaisesRegex(ValueError, "연결은 제거"):
                native_launch(root, "codex-shift")
            (root / "ctfh.toml").write_text('[native]\ndangerously_bypass_approvals_and_sandbox="false"\n')
            with self.assertRaises(ValueError):
                native_launch(root)

    def test_native_engine_check_uses_selected_account_without_model_turn(self):
        with patch("shutil.which", return_value="/fixture/codex"), \
             patch("subprocess.run") as checked:
            checked.return_value.returncode = 0
            self.assertEqual(main(["live", "check-engine", "--codex", "codex"]), 0)
            self.assertEqual(checked.call_args.args[0], ["codex", "login", "status"])
            self.assertEqual(checked.call_args.kwargs["env"]["CODEX_HOME"],
                             str(Path.home() / ".codex"))
            checked.assert_called_once()


class NativeExecLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_exec_gets_browser_access_with_workspace_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            for thread in (None, "saved-thread"):
                runner = CodexExecRunner(root, root, binary="codex")
                with patch.dict("os.environ", {"XDG_RUNTIME_DIR": str(runtime)}), \
                     patch("ctfh_core.live_agent.asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           side_effect=OSError("fixture stop before spawn")) as spawn:
                    with self.assertRaises(Exception):
                        await runner.run("fixture", thread, lambda _: None)
                args = spawn.call_args.args
                self.assertEqual(args[0], "codex")
                self.assertLess(args.index("--sandbox"), args.index("exec"))
                self.assertLess(args.index("--add-dir"), args.index("exec"))
                self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
                self.assertEqual(args[args.index("--add-dir") + 1], str(runtime / "chrome-use"))
                self.assertIn("sandbox_workspace_write.network_access=true", args)
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
                self.assertEqual(spawn.call_args.kwargs["env"]["CODEX_HOME"],
                                 str(Path.home() / ".codex"))
                if thread:
                    self.assertEqual(args[args.index("exec") + 1:args.index("exec") + 3], ("resume", thread))
            self.assertTrue((runtime / "chrome-use").is_dir())

    async def test_exec_start_and_resume_receive_requested_wrapper_and_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for thread in (None, "saved-thread"):
                runner = CodexExecRunner(root, root, binary="codex-secondary", full_access=True,
                                         model="gpt-6-sol", reasoning_effort="xhigh")
                with patch("ctfh_core.live_agent.asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           side_effect=OSError("fixture stop before spawn")) as spawn:
                    with self.assertRaises(Exception):
                        await runner.run("fixture", thread, lambda _: None)
                args = spawn.call_args.args
                self.assertEqual(args[:2], ("codex-secondary", "exec"))
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
                self.assertEqual(args[args.index("-m") + 1], "gpt-6-sol")
                self.assertEqual(args[args.index("-c") + 1], 'model_reasoning_effort="xhigh"')
                self.assertNotIn("--add-dir", args)
                if thread:
                    self.assertEqual(args[2:4], ("resume", "saved-thread"))

    async def test_ordinary_solver_uses_main_home_and_full_access_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = CodexExecRunner(root, root, binary="codex", full_access=True)
            with patch.dict("os.environ", {"CODEX_HOME": "/tmp/secondary-home"}), \
                 patch("ctfh_core.live_agent.asyncio.create_subprocess_exec", new_callable=AsyncMock,
                       side_effect=OSError("fixture stop before spawn")) as spawn:
                with self.assertRaises(OSError):
                    await runner.run("fixture", None, lambda _: None)
            args = spawn.call_args.args
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
            self.assertNotIn("--sandbox", args)
            self.assertEqual(spawn.call_args.kwargs["env"]["CODEX_HOME"],
                             str(Path.home() / ".codex"))


if __name__ == "__main__":
    unittest.main()
