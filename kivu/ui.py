"""Terminal rendering and input: prompt, assistant text, tool activity, errors.

Still to build:
- approvals (yes / no / always)
- markdown rendering, a spinner while the model thinks
"""

from __future__ import annotations

import sys

from .messages import ToolCall, ToolResult

# ANSI colors only when writing to a real terminal (not a pipe or file).
_COLOR = sys.stdout.isatty()


def _style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


class UI:
    def banner(self, version: str, provider: str, model: str, cwd: str) -> None:
        print(_style(f"kivu {version}", "1;36"), _style(f"{provider}/{model} · {cwd} · ctrl-d to quit", "2"))

    def prompt(self) -> str:
        return input(_style("> ", "1;32")).strip()

    def info(self, text: str) -> None:
        print(_style(text, "2"))

    def ask(self, label: str, default: str = "") -> str:
        hint = _style(f" [{default}]", "2") if default else ""
        return input(f"{label}{hint}: ").strip() or default

    def choose(self, label: str, options: list[str], default: str) -> str:
        for i, option in enumerate(options, 1):
            print(f"  {i}. {option}")
        while True:
            answer = self.ask(label, default)
            if answer in options:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.error(f"pick one of: {', '.join(options)}")

    def assistant(self, text: str) -> None:
        print(text)

    def tool(self, call: ToolCall, result: ToolResult) -> None:
        status = "error" if result.is_error else f"{len(result.output)} chars"
        print(_style(f"  · {call.name}({call.arguments}) -> {status}", "2"))

    def error(self, text: str) -> None:
        print(_style(f"error: {text}", "31"), file=sys.stderr)
