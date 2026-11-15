"""Register tools and describe them to the model.

A tool's function can be sync or async. Async ones are awaited; sync ones run
directly on the event loop, which is fine for quick local work (files, prompts).
Anything that waits on the network or a process should be async.

Still to build:
- validate arguments against the schema before running
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..messages import ToolCall, ToolResult

MAX_OUTPUT_CHARS = 20_000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments
    fn: Callable[..., str | Awaitable[str]]


class Registry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[Tool]:
        return list(self._tools.values())

    async def run(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, call.name, f"unknown tool: {call.name}", is_error=True)
        try:
            output = tool.fn(**call.arguments)
            if inspect.isawaitable(output):
                output = await output
        except Exception as exc:  # the model reads the error and can try again
            return ToolResult(call.id, call.name, f"{type(exc).__name__}: {exc}", is_error=True)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n[output clipped]"
        return ToolResult(call.id, call.name, output)
