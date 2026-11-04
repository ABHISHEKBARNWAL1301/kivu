"""Register tools and describe them to the model.

Still to build:
- validate arguments against the schema before running
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..messages import ToolCall, ToolResult

MAX_OUTPUT_CHARS = 20_000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments
    fn: Callable[..., str]


class Registry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[Tool]:
        return list(self._tools.values())

    def run(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, call.name, f"unknown tool: {call.name}", is_error=True)
        try:
            output = tool.fn(**call.arguments)
        except Exception as exc:  # the model reads the error and can try again
            return ToolResult(call.id, call.name, f"{type(exc).__name__}: {exc}", is_error=True)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n[output clipped]"
        return ToolResult(call.id, call.name, output)
