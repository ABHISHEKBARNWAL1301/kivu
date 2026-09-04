from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.prompt import Confirm


class UI:
    def __init__(self) -> None:
        self.console = Console(highlight=False)

    def banner(self, model: str, cwd: Path) -> None:
        self.console.print(f"[bold cyan]kivu[/]  [dim]{escape(model)} · {escape(str(cwd))}[/]")
        self.console.print("[dim]/help for commands · Ctrl-D to exit[/]")

    def prompt(self) -> str:
        return self.console.input("\n[bold cyan]you[/] [dim]›[/] ")

    @contextmanager
    def thinking(self) -> Iterator[None]:
        with self.console.status("[dim]thinking[/]", spinner="dots"):
            yield

    def assistant(self, text: str) -> None:
        self.console.print("\n[bold green]kivu[/]")
        self.console.print(Markdown(text or "(no response)"))

    def tool_call(self, name: str, detail: str) -> None:
        self.console.print(f"[bold yellow]{escape(name)}[/] [dim]{escape(detail)}[/]")

    def tool_result(self, detail: str, ok: bool = True) -> None:
        color = "green" if ok else "red"
        self.console.print(f"[{color}]{escape(detail)}[/]")

    def error(self, message: str) -> None:
        self.console.print(f"[bold red]error:[/] {escape(message)}")

    def info(self, message: str) -> None:
        self.console.print(f"[dim]{escape(message)}[/]")

    def raw(self, text: str) -> None:
        if text:
            self.console.print(text, markup=False, highlight=False, soft_wrap=True)

    def approve(self, command: str) -> bool:
        self.console.print(f"[bold red]risky command[/] [dim]{escape(command)}[/]")
        return Confirm.ask("Run it?", default=False, console=self.console)


class QuietUI:
    """UI used by callers that only need tool return values."""

    def tool_call(self, name: str, detail: str) -> None:
        pass

    def tool_result(self, detail: str, ok: bool = True) -> None:
        pass

    def approve(self, command: str) -> bool:
        return False

