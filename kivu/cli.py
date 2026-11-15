"""Command-line entry point: one-shot prompt (-p) or a simple REPL.

Still to build:
- slash commands (/help, /clear)
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import sys
from pathlib import Path

from . import __version__, config, providers
from .agent import loop
from .messages import Message, Response, user
from .providers.base import Provider
from .sandbox.approval import Approver
from .tools.files import file_tools
from .tools.registry import Registry, Tool
from .tools.shell import shell_tools
from .tools.tasks import task_tools
from .ui import UI

SYSTEM = """You are kivu, a coding assistant working in the user's terminal, inside their project.

- Look before you answer: use glob, grep and read_file instead of guessing.
- Change files with edit_file; use write_file only for new files or full rewrites.
- After changing code, run the tests or the code with run_shell and fix what fails.
- For tasks with several steps, keep a plan with update_plan.
- If the request is unclear, ask with ask_user instead of guessing.
- If the user declines an action, don't retry it; ask what they want instead.
- Answer briefly."""


class Spinner:
    """Wraps a provider to show a spinner during each model call.

    Only around the model call, not the whole loop, so it never covers an approval prompt.
    """

    def __init__(self, inner: Provider, ui: UI) -> None:
        self.inner = inner
        self.ui = ui
        self.name = inner.name
        self.model = inner.model

    async def complete(self, system: str, messages: list[Message], tools: list[Tool]) -> Response:
        with self.ui.thinking():
            return await self.inner.complete(system, messages, tools)


def setup(ui: UI) -> None:
    """Pick provider and model, then save them. Keys come from keys.json."""
    current = config.read_file()
    names = list(config.PROVIDERS)
    provider = ui.choose("Provider", names, current.get("provider", names[0]))
    info = config.PROVIDERS[provider]
    models = info["models"]
    saved = current.get("model") if current.get("provider") == provider else None
    model = ui.choose("Model", models, saved if saved in models else models[0])
    config.save(provider, model)
    if not config.api_key(provider):
        ui.error(f'no key for {provider} yet: add "{provider}": "..." to {config.KEYS_PATH}')
        ui.info(f"Get one at {info['key_url']}")
    ui.info("Saved. Run `kivu setup` any time to switch.\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kivu")
    parser.add_argument("command", nargs="?", choices=["setup"], help="setup: pick provider, model and key")
    parser.add_argument("-p", "--prompt", help="run one prompt and exit")
    parser.add_argument("-C", "--cwd", default=".", help="workspace root")
    parser.add_argument("--provider")
    parser.add_argument("-m", "--model")
    parser.add_argument("-y", "--yes", action="store_true", help="approve every write and command without asking")
    parser.add_argument("--version", action="version", version=f"kivu {__version__}")
    args = parser.parse_args()

    ui = UI()
    cfg = config.load(args.provider, args.model)
    if args.command == "setup" or cfg is None:
        if cfg is None and not sys.stdin.isatty():
            raise SystemExit("kivu isn't set up yet: run `kivu setup` in a terminal")
        setup(ui)
        if args.command == "setup":
            return
        cfg = config.load(args.provider, args.model)
        assert cfg is not None
    print(f"Using {cfg.provider}/{cfg.model} (max_steps={cfg.max_steps})")
    provider = Spinner(providers.create(cfg.provider, cfg.model, cfg.api_key), ui)
    root = Path(args.cwd)
    approver = Approver(ui, auto=args.yes)
    registry = Registry(file_tools(root, approver) + shell_tools(root, approver) + task_tools(ui))
    history: list[Message] = []
    # One event loop for the whole session: each turn runs on it, and async clients
    # (the Gemini SDK's) stay bound to the same loop between turns.
    runner = asyncio.Runner()
    atexit.register(runner.close)

    def ask(text: str) -> bool:
        start = len(history)
        history.append(user(text))
        try:
            reply = runner.run(loop.run(provider, registry, SYSTEM, history, cfg.max_steps, on_tool=ui.tool))
        except KeyboardInterrupt:
            del history[start:]  # drop the half-finished turn so the next one starts clean
            ui.error("cancelled")
            return False
        except Exception as exc:
            del history[start:]
            ui.error(f"{type(exc).__name__}: {exc}")
            return False
        ui.assistant(reply.text or "(no text in reply)")
        return True

    if args.prompt:
        raise SystemExit(0 if ask(args.prompt) else 1)

    ui.banner(__version__, cfg.provider, cfg.model, str(Path(args.cwd).resolve()))
    while True:
        try:
            text = ui.prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text:
            ask(text)
