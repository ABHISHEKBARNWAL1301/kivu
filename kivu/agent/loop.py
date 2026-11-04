from __future__ import annotations

from collections.abc import Callable

from ..messages import Message, ToolCall, ToolResult
from ..providers.base import Provider
from ..tools.registry import Registry


class StepLimitReached(Exception):
    pass


def run(
    provider: Provider,
    registry: Registry,
    system: str,
    history: list[Message],
    max_steps: int = 20,
    on_tool: Callable[[ToolCall, ToolResult], None] = lambda call, result: None,
) -> Message:
    """Run until the model answers without calling a tool. Appends to history in place."""
    for _ in range(max_steps):
        reply = provider.complete(system, history, registry.specs()).message
        history.append(reply)
        if not reply.tool_calls:
            return reply
        results = []
        for call in reply.tool_calls:
            result = registry.run(call)
            on_tool(call, result)
            results.append(result)
        history.append(Message("user", results))
    raise StepLimitReached(f"stopped after {max_steps} steps")
