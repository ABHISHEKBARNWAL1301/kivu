from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kivu.tools import Toolbox, _clip


class FakeUI:
    def __init__(self, approve: bool = False) -> None:
        self.should_approve = approve
        self.calls: list[tuple[str, str]] = []
        self.results: list[tuple[str, bool]] = []

    def tool_call(self, name: str, detail: str) -> None:
        self.calls.append((name, detail))

    def tool_result(self, detail: str, ok: bool = True) -> None:
        self.results.append((detail, ok))

    def approve(self, command: str) -> bool:
        return self.should_approve


class ToolboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ui = FakeUI()
        self.tools = Toolbox(self.root, self.ui)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_write_read_and_edit(self) -> None:
        written = self.tools.write_file("notes/a.txt", "one\ntwo\nthree\n")
        self.assertEqual(written["bytes_written"], 14)

        read = self.tools.read_file("notes/a.txt", offset=2, limit=1)
        self.assertEqual(read["content"], "two")
        self.assertTrue(read["truncated"])

        edited = self.tools.edit_file("notes/a.txt", "two", "second")
        self.assertEqual(edited["replacements"], 1)
        self.assertEqual((self.root / "notes/a.txt").read_text(), "one\nsecond\nthree\n")

    def test_edit_requires_unique_match(self) -> None:
        (self.root / "a.txt").write_text("same same")
        result = self.tools.edit_file("a.txt", "same", "new")
        self.assertIn("not unique", result["error"])

    def test_change_directory_persists_for_shell(self) -> None:
        child = self.root / "child"
        child.mkdir()
        self.tools.change_directory("child")
        result = self.tools.run_shell("pwd")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(Path(result["stdout"].strip()).resolve(), child.resolve())

    def test_risky_shell_command_is_denied(self) -> None:
        result = self.tools.run_shell("rm file.txt")
        self.assertEqual(result["error"], "cancelled by user")

    def test_list_directory(self) -> None:
        (self.root / "dir").mkdir()
        (self.root / "dir/file.txt").write_text("x")
        result = self.tools.list_directory(".", depth=2)
        self.assertIn("dir/", result["content"])
        self.assertIn("file.txt", result["content"])

    def test_clip_keeps_shell_tail(self) -> None:
        clipped, changed = _clip("abcdef", limit=3, tail=True)
        self.assertTrue(changed)
        self.assertTrue(clipped.endswith("def"))


if __name__ == "__main__":
    unittest.main()
