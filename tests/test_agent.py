from __future__ import annotations

import tempfile
import unittest

from kivu.agent import KivuAgent
from kivu.ui import QuietUI


class Response:
    text = "done"


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> Response:
        self.messages.append(message)
        return Response()


class FakeChats:
    def __init__(self) -> None:
        self.created: list[tuple[str, object]] = []
        self.chat = FakeChat()

    def create(self, model: str, config: object) -> FakeChat:
        self.created.append((model, config))
        return self.chat


class FakeClient:
    def __init__(self) -> None:
        self.chats = FakeChats()


class AgentTests(unittest.TestCase):
    def test_chat_is_persistent_and_resettable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            agent = KivuAgent("test-model", directory, QuietUI(), client=client)
            self.assertEqual(agent.ask("work"), "done")
            self.assertEqual(client.chats.chat.messages, ["work"])
            agent.reset()
            self.assertEqual(len(client.chats.created), 2)


if __name__ == "__main__":
    unittest.main()
