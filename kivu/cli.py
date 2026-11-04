"""Command-line entry point: one-shot prompt (-p) or a simple REPL.

Still to build:
- slash commands (/help, /clear)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, providers
from .agent import loop
from .messages import Message, user
from .tools.files import file_tools
from .tools.registry import Registry
from .ui import UI

SYSTEM = (
    "You are kivu, a terminal coding assistant. "
    "Use the tools to look at the user's files instead of guessing. "
    "Answer briefly."
)


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
    provider = providers.create(cfg.provider, cfg.model, cfg.api_key)
    registry = Registry(file_tools(Path(args.cwd)))
    history: list[Message] = []

    def ask(text: str) -> bool:
        start = len(history)
        history.append(user(text))
        try:
            reply = loop.run(provider, registry, SYSTEM, history, cfg.max_steps, on_tool=ui.tool)
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
