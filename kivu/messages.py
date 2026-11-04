"""Provider-neutral conversation types shared by every module.

Each provider converts to and from these, so the loop never sees a vendor format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False


Part = Text | ToolCall | ToolResult


@dataclass
class Message:
    role: Literal["user", "assistant"]
    parts: list[Part]
    # The provider's own copy of this message. Some vendors (Gemini's thought
    # signatures) need it sent back unchanged; other providers ignore it.
    raw: Any = field(default=None, repr=False)

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, Text))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [p for p in self.parts if isinstance(p, ToolCall)]


def user(text: str) -> Message:
    return Message("user", [Text(text)])


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Response:
    message: Message
    usage: Usage = field(default_factory=Usage)
