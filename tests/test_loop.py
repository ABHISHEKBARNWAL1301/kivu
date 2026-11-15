import asyncio
import unittest

from kivu.agent import loop
from kivu.messages import Message, Response, Text, ToolCall, ToolResult, Usage, user
from kivu.tools.registry import Registry, Tool


class ScriptedProvider:
    """Replies with the given messages in order, and records what it was sent."""

    name = "fake"
    model = "fake"

    def __init__(self, *replies: Message) -> None:
        self.replies = list(replies)
        self.seen: list[int] = []

    async def complete(self, system: str, messages: list[Message], tools: list[Tool]) -> Response:
        self.seen.append(len(messages))
        return Response(self.replies.pop(0), Usage(0, 0))


def call(id: str, name: str, **args) -> Message:
    return Message("assistant", [ToolCall(id, name, args)])


async def shout(text: str) -> str:
    await asyncio.sleep(0)
    return text.upper()


def count(text: str) -> str:
    return str(len(text))


TOOLS = Registry([
    Tool("shout", "", {"type": "object"}, shout),
    Tool("count", "", {"type": "object"}, count),
])


class LoopTest(unittest.TestCase):
    def test_runs_sync_and_async_tools_until_a_text_answer(self) -> None:
        provider = ScriptedProvider(
            call("1", "shout", text="hi"),
            call("2", "count", text="hello"),
            Message("assistant", [Text("done")]),
        )
        history = [user("go")]
        seen: list[tuple[ToolCall, ToolResult]] = []
        reply = asyncio.run(loop.run(provider, TOOLS, "", history, on_tool=lambda c, r: seen.append((c, r))))
        self.assertEqual(reply.text, "done")
        self.assertEqual([r.output for _, r in seen], ["HI", "5"])
        self.assertEqual(provider.seen, [1, 3, 5])  # each call sees the results so far
        self.assertEqual([m.role for m in history], ["user", "assistant", "user", "assistant", "user", "assistant"])

    def test_step_limit(self) -> None:
        provider = ScriptedProvider(*[call(str(i), "count", text="x") for i in range(3)])
        with self.assertRaises(loop.StepLimitReached):
            asyncio.run(loop.run(provider, TOOLS, "", [user("go")], max_steps=3))


if __name__ == "__main__":
    unittest.main()
