from __future__ import annotations

from pathlib import Path
from typing import Protocol

from google import genai
from google.genai import types

from .tools import Toolbox
from .ui import UI


class Chat(Protocol):
    def send_message(self, message: str) -> object: ...


class KivuAgent:
    def __init__(
        self,
        model: str,
        cwd: str | Path,
        ui: UI,
        approve_all: bool = False,
        client: object | None = None,
    ) -> None:
        self.model = model
        self.ui = ui
        self.toolbox = Toolbox(cwd, ui, approve_all=approve_all)
        self.client = client or genai.Client()
        self.chat: Chat
        self.reset()

    def _system_prompt(self) -> str:
        return f"""You are Kivu, a concise terminal agent operating on the user's computer.

                Current working directory: {self.toolbox.cwd}

                You can read, write, and edit text files, inspect directories, change the persistent working directory, and run shell commands. Use run_shell for mv, cp, mkdir, searching, git, tests, and other terminal programs.

                Rules:
                - Act on the user's request instead of only describing commands.
                - Inspect relevant files before changing them.
                - Prefer read_file, write_file, and edit_file for text files.
                - Use change_directory when later tool calls should run somewhere else; shell `cd` does not persist.
                - Never claim an action succeeded unless its tool result says it succeeded.
                - When a tool fails, inspect the error and try a reasonable correction.
                - Keep final responses short and state what changed, important results, and any remaining issue.
                """

    def reset(self) -> None:
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt(),
            tools=self.toolbox.functions(),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=10,
            ),
        )
        self.chat = self.client.chats.create(model=self.model, config=config)  # type: ignore[attr-defined]

    def ask(self, prompt: str) -> str:
        response = self.chat.send_message(prompt)
        text = getattr(response, "text", None)
        return text if isinstance(text, str) and text else "(no text response)"

