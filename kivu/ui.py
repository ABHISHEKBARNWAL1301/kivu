"""Terminal rendering and input, built on Rich: prompt, replies, tool activity, approvals, errors.

Model replies are rendered as Markdown. Everything else (tool output, file paths,
commands) is printed as plain Text, so stray * or [ ] can't be read as formatting.

Still to build:
- stream replies as they arrive (rich.live.Live)
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from .messages import ToolCall, ToolResult

PLAN_MARKS = {"done": ("✓", "green"), "in_progress": ("▸", "yellow"), "pending": ("○", "dim")}


class UI:
    def __init__(self) -> None:
        self.out = Console(highlight=False)
        self.err = Console(stderr=True, highlight=False)

    def banner(self, version: str, provider: str, model: str, cwd: str) -> None:
        self.out.print(Text.assemble((f"kivu {version}", "bold cyan"), (f"  {provider}/{model} · {cwd} · ctrl-d to quit", "dim")))

    def prompt(self) -> str:
        return self.out.input("[bold green]>[/] ").strip()

    @contextmanager
    def thinking(self) -> Iterator[None]:
        with self.out.status("thinking…", spinner="dots"):
            yield

    def info(self, text: str) -> None:
        self.out.print(Text(text, style="dim"))

    def ask(self, label: str, default: str = "") -> str:
        prompt = Text(label)
        if default:
            prompt.append(f" [{default}]", style="dim")
        prompt.append(": ")
        return self.out.input(prompt).strip() or default

    def choose(self, label: str, options: list[str], default: str) -> str:
        for i, option in enumerate(options, 1):
            self.out.print(Text.assemble((f"  {i}. ", "dim"), option))
        while True:
            answer = self.ask(label, default)
            if answer in options:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.error(f"pick one of: {', '.join(options)}")

    def assistant(self, text: str) -> None:
        self.out.print(Markdown(text, code_theme="monokai"))

    def tool(self, call: ToolCall, result: ToolResult) -> None:
        args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
        args = args if len(args) <= 100 else args[:97] + "..."  # write_file content can be huge
        line = Text.assemble(("  · ", "dim"), (call.name, "bold blue"), (f"({args})", "dim"))
        if result.is_error:
            line.append(f"  ✗ {(result.output.splitlines() or [''])[0][:80]}", style="red")
        else:
            line.append(f"  {len(result.output)} chars", style="dim")
        self.out.print(line)

    def approve(self, title: str, detail: str) -> str:
        if not sys.stdin.isatty():
            return "n"  # nobody to ask (piped input); run with --yes to allow
        self.out.print(Text(f"? {title}", style="bold yellow"))
        if detail:
            lexer = "diff" if detail.startswith("---") else "bash"
            self.out.print(Padding(Syntax(detail, lexer, theme="monokai", background_color="default"), (0, 0, 0, 2)))
        while True:
            answer = self.out.input("  allow? [bold]y[/]es / [bold]n[/]o / [bold]a[/]lways this session: ")
            if (answer := answer.strip().lower()[:1]) in ("y", "n", "a"):
                return answer

    def plan(self, items: list[dict]) -> None:
        for item in items:
            mark, style = PLAN_MARKS.get(item.get("status", ""), ("○", "dim"))
            self.out.print(Text.assemble(("  ", ""), (mark, style), (" " + item.get("step", ""), style)))

    def ask_user(self, question: str) -> str | None:
        if not sys.stdin.isatty():
            return None
        self.out.print(Text(f"? {question}", style="bold yellow"))
        return self.out.input("  [bold green]>[/] ").strip()

    def error(self, text: str) -> None:
        self.err.print(Text(f"error: {text}", style="red"))
