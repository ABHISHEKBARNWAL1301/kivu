import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kivu.messages import ToolCall
from kivu.sandbox.approval import Approver
from kivu.tools.registry import Registry
from kivu.tools.shell import is_safe, shell_tools
from kivu.tools.tasks import task_tools


class Answers:
    """Stands in for the UI's approval prompt: replies with the given answers in order."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def approve(self, title: str, detail: str) -> str:
        self.asked.append(title)
        return self.answers.pop(0)


class ShellTest(unittest.TestCase):
    def test_is_safe(self) -> None:
        self.assertTrue(is_safe("ls -la"))
        self.assertTrue(is_safe("git status"))
        self.assertFalse(is_safe("git push"))
        self.assertFalse(is_safe("rm -rf build"))
        self.assertFalse(is_safe("ls; rm -rf build"))
        self.assertFalse(is_safe("git log > out.txt"))

    def test_safe_command_runs_without_asking(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "f.txt").write_text("")
            answers = Answers()
            reg = Registry(shell_tools(Path(tmp), Approver(answers)))
            self.assertEqual(asyncio.run(reg.run(ToolCall("1", "run_shell", {"command": "ls"}))).output, "exit code 0\nf.txt")
            self.assertEqual(answers.asked, [])

    def test_other_commands_need_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            reg = Registry(shell_tools(Path(tmp), Approver(Answers("n", "y"))))
            declined = asyncio.run(reg.run(ToolCall("1", "run_shell", {"command": "echo hi"})))
            self.assertTrue(declined.is_error)
            allowed = asyncio.run(reg.run(ToolCall("2", "run_shell", {"command": "echo hi; exit 3"})))
            self.assertEqual(allowed.output, "exit code 3\nhi")

    def test_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            reg = Registry(shell_tools(Path(tmp), Approver(auto=True)))
            result = asyncio.run(reg.run(ToolCall("1", "run_shell", {"command": "sleep 5", "timeout_seconds": 1})))
            self.assertTrue(result.is_error)
            self.assertIn("timed out", result.output)

    def test_timeout_stops_child_processes_too(self) -> None:
        with TemporaryDirectory() as tmp:
            reg = Registry(shell_tools(Path(tmp), Approver(auto=True)))
            start = time.monotonic()
            # the background sleep keeps the output pipe open unless it's killed as well
            result = asyncio.run(reg.run(ToolCall("1", "run_shell", {"command": "sleep 30 & wait", "timeout_seconds": 1})))
            self.assertTrue(result.is_error)
            self.assertLess(time.monotonic() - start, 10)


class FakeTaskUI:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.plans: list[list[dict]] = []

    def plan(self, items: list[dict]) -> None:
        self.plans.append(items)

    def ask_user(self, question: str) -> str | None:
        return self.answer


class TaskToolsTest(unittest.TestCase):
    def test_update_plan(self) -> None:
        ui = FakeTaskUI(None)
        reg = Registry(task_tools(ui))
        items = [{"step": "read code", "status": "done"}, {"step": "fix bug", "status": "in_progress"}]
        self.assertEqual(asyncio.run(reg.run(ToolCall("1", "update_plan", {"items": items}))).output, "plan updated")
        self.assertEqual(ui.plans, [items])
        bad = asyncio.run(reg.run(ToolCall("2", "update_plan", {"items": [{"step": "x", "status": "maybe"}]})))
        self.assertTrue(bad.is_error)

    def test_ask_user(self) -> None:
        self.assertEqual(asyncio.run(Registry(task_tools(FakeTaskUI("blue"))).run(ToolCall("1", "ask_user", {"question": "?"}))).output, "blue")
        away = asyncio.run(Registry(task_tools(FakeTaskUI(None))).run(ToolCall("1", "ask_user", {"question": "?"})))
        self.assertIn("isn't available", away.output)


if __name__ == "__main__":
    unittest.main()
