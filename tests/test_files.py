import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kivu.messages import ToolCall
from kivu.tools.files import file_tools
from kivu.tools.registry import Registry


class FileToolsTest(unittest.TestCase):
    def test_read_and_list(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("hi")
            (Path(tmp) / "sub").mkdir()
            reg = Registry(file_tools(Path(tmp)))
            self.assertEqual(reg.run(ToolCall("1", "read_file", {"path": "a.txt"})).output, "hi")
            self.assertEqual(reg.run(ToolCall("2", "list_directory", {})).output, "sub/\na.txt")

    def test_cannot_escape_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            reg = Registry(file_tools(Path(tmp)))
            result = reg.run(ToolCall("1", "read_file", {"path": "../../etc/passwd"}))
            self.assertTrue(result.is_error)
            self.assertIn("outside the workspace", result.output)

    def test_unknown_tool_is_an_error_result(self) -> None:
        result = Registry().run(ToolCall("1", "nope", {}))
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
