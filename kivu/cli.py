"""Command-line entry point: parse args, load config, start the REPL or a one-shot run.

To build:
- flags: -p/--prompt (one-shot), -C/--cwd, -m/--model, --provider, -y/--yes
- REPL with slash commands (/help, /clear, /exit)
- wire config -> provider -> tools -> sandbox -> agent loop -> ui
"""

from . import __version__


def main() -> None:
    print(f"kivu {__version__}: nothing wired up yet")
