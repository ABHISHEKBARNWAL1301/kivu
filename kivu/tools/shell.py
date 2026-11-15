"""Shell tool: run_shell(command, timeout).

Read-only commands on SAFE_PREFIXES run without asking; everything else needs approval.
"Always" approves that exact command for the rest of the session.

Still to build:
- move the safe/ask decision into sandbox/policy.py
- OS-level isolation (macOS sandbox-exec, or a container)
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path

from ..sandbox.approval import Approver
from .registry import Tool

SAFE_PREFIXES = [
    ["ls"],
    ["pwd"],
    ["git", "status"],
    ["git", "diff"],
    ["git", "log"],
    ["git", "show"],
    ["git", "branch"],
]
# Any of these can chain or redirect commands, so a "safe" prefix no longer means safe.
SHELL_SYNTAX = set(";&|<>`$\n")


def is_safe(command: str) -> bool:
    if SHELL_SYNTAX & set(command):
        return False
    words = command.split()
    return any(words[: len(prefix)] == prefix for prefix in SAFE_PREFIXES)


def shell_tools(root: Path, approver: Approver) -> list[Tool]:
    root = root.resolve()

    async def run_shell(command: str, timeout_seconds: int = 120) -> str:
        if not is_safe(command):
            approver.check(f"shell:{command}", "run shell command", command)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # a command waiting for input would hang the agent
            start_new_session=True,  # its own process group, so we can stop everything it started
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=int(timeout_seconds))
        except TimeoutError:
            raise TimeoutError(f"command timed out after {timeout_seconds}s") from None
        finally:
            if proc.returncode is None:  # timed out or cancelled (ctrl-c): don't leave it running
                # Kill the whole group: killing only the shell leaves its children
                # holding the output pipe, and wait() would block until they finish.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        output = (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()
        return f"exit code {proc.returncode}\n{output}" if output else f"exit code {proc.returncode}"

    return [
        Tool(
            "run_shell",
            "Run a shell command in the workspace root, e.g. tests, git, installing packages. "
            "No interactive input. Use grep/glob/read_file to look at files instead of cat or find.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "description": "default 120"},
                },
                "required": ["command"],
            },
            run_shell,
        ),
    ]
