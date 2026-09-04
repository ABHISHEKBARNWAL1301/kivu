from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import __version__
from .agent import KivuAgent
from .ui import UI


DEFAULT_MODEL = "gemini-3.5-flash"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kivu", description="Small Gemini terminal agent")
    parser.add_argument("-p", "--prompt", help="run one prompt and exit")
    parser.add_argument("-C", "--cwd", default=".", help="starting directory")
    parser.add_argument("-m", "--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("-y", "--yes", action="store_true", help="approve risky shell commands")
    parser.add_argument("--version", action="version", version=f"kivu {__version__}")
    return parser


def _help(ui: UI) -> None:
    ui.raw(
        "/cwd PATH  change working directory\n"
        "/clear     clear model conversation\n"
        "/help      show commands\n"
        "/exit      exit\n"
        "!COMMAND   run a shell command directly"
    )


def _direct_shell(agent: KivuAgent, command: str, ui: UI) -> None:
    result = agent.toolbox.run_shell(command)
    stdout = result.get("stdout")
    stderr = result.get("stderr")
    if isinstance(stdout, str):
        ui.raw(stdout.rstrip())
    if isinstance(stderr, str):
        ui.raw(stderr.rstrip())


def main() -> None:
    args = _parser().parse_args()
    ui = UI()

    if not os.getenv("GEMINI_API_KEY"):
        ui.error("GEMINI_API_KEY is not set")
        raise SystemExit(2)

    try:
        cwd = Path(args.cwd).expanduser().resolve()
        agent = KivuAgent(args.model, cwd, ui, approve_all=args.yes)
    except Exception as exc:
        ui.error(str(exc))
        raise SystemExit(2) from exc

    if args.prompt:
        try:
            with ui.thinking():
                answer = agent.ask(args.prompt)
            ui.assistant(answer)
        except KeyboardInterrupt:
            ui.info("cancelled")
        except Exception as exc:
            ui.error(str(exc))
            raise SystemExit(1) from exc
        return

    ui.banner(args.model, agent.toolbox.cwd)
    while True:
        try:
            prompt = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            ui.info("bye")
            return

        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return
        if prompt == "/help":
            _help(ui)
            continue
        if prompt == "/clear":
            agent.reset()
            ui.info("conversation cleared")
            continue
        if prompt.startswith("/cwd "):
            result = agent.toolbox.change_directory(prompt[5:].strip())
            if "error" not in result:
                agent.reset()
                ui.info("conversation cleared for the new working directory")
            continue
        if prompt.startswith("!"):
            _direct_shell(agent, prompt[1:].strip(), ui)
            continue

        try:
            with ui.thinking():
                answer = agent.ask(prompt)
            ui.assistant(answer)
        except KeyboardInterrupt:
            ui.info("cancelled")
        except Exception as exc:
            ui.error(str(exc))


if __name__ == "__main__":
    main()

