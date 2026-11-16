import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kivu.mcp_client import McpManager, ServerSpec, load_specs, tool_name
from kivu.messages import ToolCall
from kivu.sandbox.approval import Approver
from kivu.tools.registry import Registry

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_server.py")


class Answers:
    """Stands in for the UI's approval prompt: replies with the given answers in order."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def approve(self, title: str, detail: str) -> str:
        self.asked.append(title)
        return self.answers.pop(0)


def spec(**kwargs) -> ServerSpec:
    return ServerSpec("fix", sys.executable, [SERVER], **kwargs)


class ConfigTest(unittest.TestCase):
    def test_load_specs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = root / "user.json"
            user.write_text(json.dumps({"mcpServers": {
                "mine": {"command": "uv", "args": ["run", "x"], "allow": ["*"]},
                "off": {"command": "uv", "disabled": True},
                "remote": {"url": "https://example.com/mcp"},
            }}))
            (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
                "repo": {"command": "npx", "args": ["thing"], "allow": ["*"], "env": {"A": "1"}},
            }}))
            specs, problems = load_specs(root, user)
            by_name = {s.name: s for s in specs}
            self.assertEqual(sorted(by_name), ["mine", "repo"])
            self.assertEqual(by_name["mine"].allow, ["*"])
            self.assertFalse(by_name["mine"].from_project)
            self.assertTrue(by_name["repo"].from_project)
            self.assertEqual(by_name["repo"].allow, [])  # a repo can't grant itself permissions
            self.assertEqual(len(problems), 1)
            self.assertIn("remote", problems[0])

    def test_missing_and_broken_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(load_specs(root, root / "none.json"), ([], []))
            (root / ".mcp.json").write_text("{not json")
            specs, problems = load_specs(root, root / "none.json")
            self.assertEqual(specs, [])
            self.assertEqual(len(problems), 1)

    def test_tool_name(self) -> None:
        self.assertEqual(tool_name("lav-data", "mongo.find"), "mcp__lav_data__mongo_find")
        self.assertEqual(len(tool_name("s", "x" * 100)), 64)


class ServerTest(unittest.IsolatedAsyncioTestCase):
    """Runs on one event loop per test, like a kivu session: servers and calls share it."""

    async def start(self, approver: Approver, *specs: ServerSpec) -> tuple[McpManager, Registry]:
        manager = McpManager(Path(self.tmp.name), approver)
        self.addAsyncCleanup(manager.close)
        await manager.start(list(specs))
        return manager, Registry(manager.tools)

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    async def test_lists_and_calls_tools(self) -> None:
        answers = Answers("n", "a")
        manager, reg = await self.start(Approver(answers), spec())
        self.assertEqual(manager.failed, {})
        self.assertEqual(manager.connected, {"fix": 2})
        self.assertIn("Call add for sums.", manager.system_notes())
        add = next(t for t in reg.specs() if t.name == "mcp__fix__add")
        self.assertEqual(add.description, "Add two numbers.")
        self.assertEqual(sorted(add.parameters["properties"]), ["a", "b"])

        declined = await reg.run(ToolCall("1", "mcp__fix__add", {"a": 2, "b": 3}))
        self.assertTrue(declined.is_error)
        self.assertEqual((await reg.run(ToolCall("2", "mcp__fix__add", {"a": 2, "b": 3}))).output, "5")
        self.assertEqual((await reg.run(ToolCall("3", "mcp__fix__add", {"a": 4, "b": 4}))).output, "8")  # "always"
        self.assertEqual(len(answers.asked), 2)

    async def test_allowed_tools_skip_approval_and_errors_come_back(self) -> None:
        answers = Answers()
        _, reg = await self.start(Approver(answers), spec(allow=["*"]))
        self.assertEqual((await reg.run(ToolCall("1", "mcp__fix__add", {"a": 1, "b": 1}))).output, "2")
        failed = await reg.run(ToolCall("2", "mcp__fix__fail", {}))
        self.assertTrue(failed.is_error)
        self.assertIn("fail", failed.output)
        self.assertEqual(answers.asked, [])

    async def test_project_server_starts_without_asking(self) -> None:
        answers = Answers()
        manager, _ = await self.start(Approver(answers), spec(from_project=True))
        self.assertEqual(manager.connected, {"fix": 2})
        self.assertEqual(answers.asked, [])

    async def test_broken_server_is_reported_and_others_still_work(self) -> None:
        broken = ServerSpec("broken", sys.executable, ["-c", "raise SystemExit(3)"])
        missing = ServerSpec("missing", "kivu-no-such-command")
        manager, reg = await self.start(Approver(auto=True), broken, missing, spec())
        self.assertEqual(sorted(manager.failed), ["broken", "missing"])
        self.assertEqual(manager.connected, {"fix": 2})
        self.assertEqual((await reg.run(ToolCall("1", "mcp__fix__add", {"a": 1, "b": 2}))).output, "3")

    async def test_calls_fail_cleanly_after_close(self) -> None:
        manager, reg = await self.start(Approver(auto=True), spec())
        await manager.close()
        result = await reg.run(ToolCall("1", "mcp__fix__add", {"a": 1, "b": 2}))
        self.assertTrue(result.is_error)
        self.assertIn("stopped", result.output)


class RunnerTest(unittest.TestCase):
    def test_session_survives_between_runs_of_a_runner(self) -> None:
        # Like the CLI: start in one runner.run, call in later ones, with the loop idle between.
        with TemporaryDirectory() as tmp, asyncio.Runner() as runner:
            manager = McpManager(Path(tmp), Approver(auto=True))
            runner.run(manager.start([spec()]))
            reg = Registry(manager.tools)
            self.assertEqual(runner.run(reg.run(ToolCall("1", "mcp__fix__add", {"a": 1, "b": 2}))).output, "3")
            self.assertEqual(runner.run(reg.run(ToolCall("2", "mcp__fix__add", {"a": 3, "b": 4}))).output, "7")
            runner.run(manager.close())


if __name__ == "__main__":
    unittest.main()
