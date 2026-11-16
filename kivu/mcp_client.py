"""MCP client: start the servers listed in mcp.json and offer their tools to the model.

Config has the same shape as Claude Code's .mcp.json, so entries can be copied over:

    {"mcpServers": {"lav-data": {"command": "uv", "args": ["run", "lav-data-mcp"],
                                 "env": {"KEY": "$FROM_YOUR_SHELL"},
                                 "allow": ["list_sources"], "timeout": 120, "disabled": false}}}

Servers from both files start without asking.
- ~/.config/kivu/mcp.json is yours: "allow" lists the tools that run without approval
  (["*"] for all of them).
- <workspace>/.mcp.json comes with the repo, so kivu ignores its "allow": a repo can't
  grant itself permissions.

Every other MCP tool call needs approval, because kivu can't know what the tool changes.
Tools are named mcp__<server>__<tool>.

Each server is held open by its own task on kivu's event loop (the CLI's asyncio.Runner),
and tool calls await the session directly. The tasks pause between turns while the loop
isn't running, and pick up again on the next one.

Still to build:
- HTTP servers ("url" entries) and OAuth
- a way to switch servers on and off during a session
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from . import __version__
from .config import CONFIG_PATH
from .sandbox.approval import Approver
from .tools.registry import Tool

USER_PATH = CONFIG_PATH.parent / "mcp.json"
PROJECT_FILE = ".mcp.json"
LOG_DIR = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "kivu" / "mcp"
CONNECT_TIMEOUT = 30
MAX_NAME = 64  # the limit on function names at Gemini and OpenAI
MAX_DESCRIPTION = 2000
MAX_INSTRUCTIONS = 2000


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    allow: list[str] = field(default_factory=list)
    timeout: float = 120
    from_project: bool = False


def load_specs(
    root: Path, user_path: Path | None = None
) -> tuple[list[ServerSpec], list[str]]:
    """Read both config files. Returns the servers to start and the problems found."""
    specs: dict[str, ServerSpec] = {}
    problems: list[str] = []
    for path, from_project in (
        (user_path or USER_PATH, False),
        (root / PROJECT_FILE, True),
    ):
        try:
            entries = json.loads(path.read_text()).get("mcpServers", {})
        except FileNotFoundError:
            continue
        except (ValueError, AttributeError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        for name, entry in entries.items():
            if entry.get("disabled"):
                specs.pop(name, None)
            elif not entry.get("command"):
                problems.append(
                    f'{name}: only stdio servers (with a "command") are supported so far'
                )
            else:
                env = entry.get("env")
                specs[name] = ServerSpec(
                    name=name,
                    command=os.path.expandvars(entry["command"]),
                    args=[
                        os.path.expandvars(str(arg)) for arg in entry.get("args", [])
                    ],
                    env={k: os.path.expandvars(str(v)) for k, v in env.items()}
                    if env
                    else None,
                    allow=[] if from_project else list(entry.get("allow", [])),
                    timeout=float(entry.get("timeout", 120)),
                    from_project=from_project,
                )
    return list(specs.values()), problems


@dataclass
class _Server:
    spec: ServerSpec
    ready: asyncio.Future  # resolves to (tools, instructions)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    session: ClientSession | None = None
    task: asyncio.Task | None = None


class McpManager:
    def __init__(self, root: Path, approver: Approver) -> None:
        self.root = root.resolve()
        self.approver = approver
        self.tools: list[Tool] = []
        self.instructions: dict[
            str, str
        ] = {}  # server name -> what it says about its tools
        self.connected: dict[str, int] = {}  # server name -> number of tools
        self.failed: dict[str, str] = {}  # server name -> why
        self._servers: list[_Server] = []

    async def start(self, specs: list[ServerSpec]) -> None:
        """Start the servers side by side and wait until each is ready or has failed."""
        loop = asyncio.get_running_loop()
        for spec in specs:
            server = _Server(spec, ready=loop.create_future())
            # A task of its own, not part of the caller's: it has to outlive this call,
            # and ctrl-c during a turn cancels only the turn.
            server.task = asyncio.create_task(
                self._serve(server), name=f"mcp:{spec.name}"
            )
            self._servers.append(server)
        await asyncio.gather(*(self._wait_ready(server) for server in self._servers))

    async def _wait_ready(self, server: _Server) -> None:
        name = server.spec.name
        try:
            found, instructions = await asyncio.wait_for(server.ready, CONNECT_TIMEOUT)
        except TimeoutError:
            server.task.cancel()
            self.failed[name] = (
                f"no answer after {CONNECT_TIMEOUT}s (log: {_log_path(name)})"
            )
            return
        except Exception as exc:
            self.failed[name] = f"{_describe(exc)} (log: {_log_path(name)})"
            return
        self.connected[name] = len(found)
        if instructions:
            self.instructions[name] = instructions[:MAX_INSTRUCTIONS]
        self.tools.extend(self._wrap(server, tool) for tool in found)

    def system_notes(self) -> str:
        """Text to add to the system prompt: each server's own instructions."""
        notes = [
            f"\n\nMCP server '{name}' says about its tools:\n{text}"
            for name, text in self.instructions.items()
        ]
        return "".join(notes)

    async def close(self) -> None:
        """Ask each server to stop, and cancel the ones that don't within 5s."""
        servers, self._servers = self._servers, []
        for server in servers:
            server.stop.set()
        for server in servers:
            try:
                await asyncio.wait_for(server.task, 5)  # cancels the task on timeout
            except (Exception, asyncio.CancelledError):
                pass

    async def _serve(self, server: _Server) -> None:
        """Hold one server open. The SDK needs it opened and closed from the same task."""
        spec = server.spec
        params = StdioServerParameters(
            command=spec.command, args=spec.args, env=spec.env, cwd=self.root
        )
        info = types.Implementation(name="kivu", version=__version__)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            # The server's stderr goes to a log file so it can't scribble over the UI.
            with open(_log_path(spec.name), "w") as log:
                async with stdio_client(params, errlog=log) as (read, write):
                    async with ClientSession(read, write, client_info=info) as session:
                        started = await session.initialize()
                        found: list[types.Tool] = []
                        cursor = None
                        while True:
                            page = await session.list_tools(
                                params=types.PaginatedRequestParams(cursor=cursor)
                            )
                            found.extend(page.tools)
                            cursor = page.next_cursor
                            if not cursor:
                                break
                        server.session = session
                        server.ready.set_result(
                            (found, getattr(started, "instructions", None))
                        )
                        await server.stop.wait()
        except Exception as exc:
            if not server.ready.done():
                server.ready.set_exception(exc)
        except asyncio.CancelledError:
            if not server.ready.done():
                server.ready.set_exception(
                    ConnectionError("stopped before it was ready")
                )
            raise
        finally:
            server.session = None

    def _wrap(self, server: _Server, tool: types.Tool) -> Tool:
        spec = server.spec
        free = "*" in spec.allow or tool.name in spec.allow

        async def call(**arguments: Any) -> str:
            if not free:
                self.approver.check(
                    f"mcp:{spec.name}:{tool.name}",
                    f"call MCP tool {spec.name} · {tool.name}",
                    json.dumps(arguments, indent=2, default=str) if arguments else "",
                )
            if server.session is None:
                raise ConnectionError(
                    f"MCP server '{spec.name}' has stopped (log: {_log_path(spec.name)})"
                )
            # ctrl-c cancels this await, which cancels the request too
            result = await server.session.call_tool(
                tool.name, arguments, read_timeout_seconds=spec.timeout
            )
            if not isinstance(result, types.CallToolResult):
                raise RuntimeError(
                    "the server asked for more input, which kivu doesn't support yet"
                )
            text = _text(result)
            if result.is_error:
                raise RuntimeError(text or "the tool reported an error")
            return text or "(no output)"

        return Tool(
            name=tool_name(spec.name, tool.name),
            description=(tool.description or tool.title or "")[:MAX_DESCRIPTION],
            parameters=tool.input_schema or {"type": "object", "properties": {}},
            fn=call,
        )


def tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"mcp__{server}__{tool}")[:MAX_NAME]


def _text(result: types.CallToolResult) -> str:
    chunks = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            chunks.append(block.text)
        else:
            chunks.append(f"[{block.type} content left out: kivu only reads text]")
    if not chunks and result.structured_content is not None:
        chunks.append(json.dumps(result.structured_content, indent=2, default=str))
    return "\n".join(chunks)


def _log_path(name: str) -> Path:
    return LOG_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".log")


def _describe(exc: BaseException) -> str:
    """Errors from the SDK arrive wrapped in groups; show the one that matters."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"
