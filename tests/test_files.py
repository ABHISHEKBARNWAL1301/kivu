import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kivu.messages import ToolCall
from kivu.sandbox.approval import Approver
from kivu.tools.files import file_tools
from kivu.tools.registry import Registry


class Answers:
    """Stands in for the UI's approval prompt: replies with the given answers in order."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def approve(self, title: str, detail: str) -> str:
        self.asked.append(title)
        return self.answers.pop(0)


class FileToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("one\ntwo\nthree\n")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.py").write_text("def hello():\n    return 1\n")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "c.py").write_text("def hello(): pass\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_tool(self, name: str, approver: Approver | None = None, **args):
        reg = Registry(file_tools(self.root, approver or Approver(auto=True)))
        return asyncio.run(reg.run(ToolCall("1", name, args)))

    def test_read_with_line_numbers_and_paging(self) -> None:
        out = self.run_tool("read_file", path="a.txt", offset=2, limit=1).output
        self.assertEqual(out, "    2\ttwo\n[lines 2-2 of 3; pass offset=3 to read more]")

    def test_read_accepts_float_numbers(self) -> None:
        out = self.run_tool("read_file", path="a.txt", offset=3.0, limit=5.0).output
        self.assertEqual(out, "    3\tthree")

    def test_list(self) -> None:
        self.assertEqual(self.run_tool("list_directory").output, ".git/\nsub/\na.txt")

    def test_glob_skips_git(self) -> None:
        self.assertEqual(self.run_tool("glob", pattern="*.py").output, "sub/b.py")

    def test_grep(self) -> None:
        self.assertEqual(self.run_tool("grep", pattern=r"def \w+").output, "sub/b.py:1: def hello():")
        self.assertEqual(self.run_tool("grep", pattern="two", glob="*.py").output, "no matches")

    def test_cannot_escape_workspace(self) -> None:
        result = self.run_tool("read_file", path="../../etc/passwd")
        self.assertTrue(result.is_error)
        self.assertIn("outside the workspace", result.output)

    def test_write_new_file(self) -> None:
        result = self.run_tool("write_file", path="new/c.txt", content="hi\n")
        self.assertEqual(result.output, "wrote 1 lines to new/c.txt")
        self.assertEqual((self.root / "new" / "c.txt").read_text(), "hi\n")

    def test_edit_replaces_exactly_once(self) -> None:
        self.run_tool("edit_file", path="a.txt", old_text="two", new_text="2")
        self.assertEqual((self.root / "a.txt").read_text(), "one\n2\nthree\n")

    def test_edit_rejects_missing_and_ambiguous_text(self) -> None:
        (self.root / "d.txt").write_text("x x")
        self.assertIn("not found", self.run_tool("edit_file", path="a.txt", old_text="nope", new_text="").output)
        self.assertIn("matches 2 places", self.run_tool("edit_file", path="d.txt", old_text="x", new_text="y").output)

    def test_declined_write_changes_nothing(self) -> None:
        result = self.run_tool("edit_file", Approver(Answers("n")), path="a.txt", old_text="two", new_text="2")
        self.assertTrue(result.is_error)
        self.assertIn("declined", result.output)
        self.assertEqual((self.root / "a.txt").read_text(), "one\ntwo\nthree\n")

    def test_always_stops_asking(self) -> None:
        answers = Answers("a")
        reg = Registry(file_tools(self.root, Approver(answers)))
        asyncio.run(reg.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "1"})))
        asyncio.run(reg.run(ToolCall("2", "write_file", {"path": "y.txt", "content": "2"})))
        self.assertEqual(answers.asked, ["create x.txt"])
        self.assertTrue((self.root / "y.txt").exists())

    def test_unknown_tool_is_an_error_result(self) -> None:
        result = asyncio.run(Registry().run(ToolCall("1", "nope", {})))
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
