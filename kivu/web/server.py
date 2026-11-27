"""A small local web server for the kivu browser UI (standard library HTTP + kivu.db).

- Each chat (thread) has a folder it works in, a model, and a project label
  (the folder's name unless you rename it). Chats and the user's profile are
  saved through kivu.db's use cases (Postgres; start it with `docker compose up -d`).
- This module keeps the live side: running turns, waiting prompts, long-polls.
- A turn runs on its own worker thread with its own event loop, so approvals
  and ask_user can block there until you answer in the browser.
- The browser long-polls GET /api/threads/<id>?after=<version> to see updates.
- Listens on 127.0.0.1 only: the agent can run shell commands.

Still to build:
- MCP servers (the CLI has them; the web UI starts without)
- stream replies as they arrive
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from .. import config, providers
from ..agent import loop
from ..cli import SYSTEM
from ..db import Conflict, DatabaseUnavailable, NotFound, ThreadUseCases, UserUseCases, init_db, make_engine, make_sessions
from ..db.schemas import ThreadCreate, ThreadOut, ThreadUpdate, UserUpdate
from ..messages import Message, Text, ToolCall, ToolResult, user
from ..sandbox.approval import Approver
from ..tools.files import file_tools
from ..tools.registry import Registry
from ..tools.shell import shell_tools
from ..tools.tasks import task_tools

STATIC = Path(__file__).parent / "static"
LEGACY_THREADS_DIR = config.CONFIG_PATH.parent / "threads"  # where the first web UI saved chats
TOOL_OUTPUT_CHARS = 4000  # how much tool output a chat keeps for display
WAIT_SECONDS = 25  # long-poll timeout
MAX_STEPS = config.Config.max_steps

WEB_SYSTEM = SYSTEM.replace("in the user's terminal", "for the user") + "\n- Format replies as Markdown."


class Chat:
    """One conversation: what the browser shows (data) plus what the model sees (history)."""

    def __init__(self, data: dict, threads: ThreadUseCases) -> None:
        self.data = data
        self.threads = threads
        self.history: list[Message] = []
        for seq, item in enumerate(data["items"]):  # after a restart, the model gets the text back (not tool calls)
            if item["type"] == "user":
                self.history.append(user(item["text"]))
            elif item["type"] == "assistant":
                self.history.append(Message("assistant", [Text(item["text"])]))
            elif item.get("answer", "") is None:  # a prompt left open when the server stopped
                item["answer"] = "n" if item["type"] == "approval" else ""
                threads.update_item(self.id, seq, item)
        self.approver = Approver(self, auto=data.get("mode") == "auto")
        self.cond = threading.Condition()
        self.version = 0
        self.running = False
        self.waiting: dict[str, threading.Event] = {}
        self.task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def id(self) -> str:
        return self.data["id"]

    # -- state shared with the browser --

    def changed(self) -> None:
        """Wake the browsers long-polling this chat."""
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def apply(self, out: ThreadOut) -> None:
        """Take the saved fields from a use case's result (not items: this side keeps those)."""
        with self.cond:
            self.data.update({k: v for k, v in thread_data(out).items() if k != "items"})
        self.approver.auto = self.data["mode"] == "auto"
        self.changed()

    def add(self, item: dict) -> dict:
        added = self.threads.add_item(self.id, item)  # saved first, so the seq here matches the database
        with self.cond:
            self.data["items"].append(item)
            self.data["title"] = added.title
            self.data["updated"] = time.time()
        self.changed()
        return item

    def summary(self) -> dict:
        d = self.data
        return {k: d.get(k) for k in ("id", "title", "folder", "project", "provider", "model", "created", "updated")} | {
            "running": self.running
        }

    def full(self) -> dict:
        with self.cond:
            return self.summary() | {"mode": self.data.get("mode", "ask"), "items": list(self.data["items"]), "version": self.version}

    def wait(self, after: int) -> None:
        with self.cond:
            self.cond.wait_for(lambda: self.version != after, timeout=WAIT_SECONDS)

    # -- prompts the agent waits on (called from the worker thread) --

    def _prompt(self, item: dict) -> dict:
        item["id"] = uuid.uuid4().hex[:8]
        event = threading.Event()
        self.waiting[item["id"]] = event
        self.add(item)
        event.wait()
        self.waiting.pop(item["id"], None)
        return item

    def approve(self, title: str, detail: str) -> str:  # Approver's Asker
        return self._prompt({"type": "approval", "title": title, "detail": detail, "answer": None})["answer"]

    def ask_user(self, question: str) -> str | None:  # TaskUI
        answer = self._prompt({"type": "question", "text": question, "answer": None})["answer"]
        return answer or None

    def plan(self, items: list[dict]) -> None:  # TaskUI
        self.add({"type": "plan", "items": items})

    def answer(self, prompt_id: str, answer: str) -> bool:
        for seq, item in enumerate(self.data["items"]):
            if item.get("id") == prompt_id and item.get("answer") is None:
                item["answer"] = answer
                self.threads.update_item(self.id, seq, item)
                self.changed()
                if prompt_id in self.waiting:
                    self.waiting[prompt_id].set()
                return True
        return False

    # -- a turn --

    def send(self, text: str) -> None:
        if self.running:
            raise Conflict("this chat is still working")
        if not text.strip():
            raise ValueError("empty message")
        self.add({"type": "user", "text": text})  # the first one also names the chat
        self.running = True
        self.changed()
        threading.Thread(target=self._turn, args=(text,), daemon=True).start()

    def _turn(self, text: str) -> None:
        start = len(self.history)
        self.history.append(user(text))
        try:
            reply = asyncio.run(self._run())
            self.add({"type": "assistant", "text": reply.text or "(no text in reply)"})
        except asyncio.CancelledError:
            del self.history[start:]  # drop the half-finished turn so the next one starts clean
            self.add({"type": "error", "text": "Stopped."})
        except BaseException as exc:  # noqa: BLE001 - shown in the chat
            del self.history[start:]
            self.add({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        finally:
            self.running = False
            self.task = self.loop = None
            self.changed()

    async def _run(self) -> Message:
        d = self.data
        key = config.api_key(d["provider"])
        if not key:
            raise RuntimeError(f'no key for {d["provider"]}: add "{d["provider"]}": "..." to {config.KEYS_PATH}')
        provider = providers.create(d["provider"], d["model"], key)
        root = Path(d["folder"])
        registry = Registry(file_tools(root, self.approver) + shell_tools(root, self.approver) + task_tools(self))
        system = WEB_SYSTEM + f"\n\nYou are working in {root}."
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        return await loop.run(provider, registry, system, self.history, MAX_STEPS, on_tool=self._tool)

    def _tool(self, call: ToolCall, result: ToolResult) -> None:
        output = result.output if len(result.output) <= TOOL_OUTPUT_CHARS else result.output[:TOOL_OUTPUT_CHARS] + "\n[clipped]"
        self.add({"type": "tool", "name": call.name, "args": call.arguments, "output": output, "error": result.is_error})

    def stop(self) -> None:
        for item in self.data["items"]:  # let any blocked prompt return first
            if item.get("answer", "") is None:
                self.answer(item["id"], "n" if item["type"] == "approval" else "")
        if self.loop and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)


def thread_data(out: ThreadOut) -> dict:
    """The shape the browser gets: use case output with epoch-second timestamps."""
    data = out.model_dump(exclude={"created_at", "updated_at", "user_id"})
    return data | {"created": out.created_at.timestamp(), "updated": out.updated_at.timestamp()}


class Store:
    """Live chats in memory, saved through the use cases."""

    def __init__(self, users: UserUseCases, threads: ThreadUseCases) -> None:
        self.users = users
        self.threads = threads
        self.lock = threading.Lock()
        self.profile = users.get_or_create_default().model_dump()
        if imported := threads.import_legacy_json(self.profile["id"], LEGACY_THREADS_DIR):
            print(f"imported {imported} chats from {LEGACY_THREADS_DIR} (the files are left as they were)")
        self.chats: dict[str, Chat] = {}
        for out in threads.list_for_user(self.profile["id"]):
            self.chats[out.id] = Chat(thread_data(out), threads)

    def update_profile(self, body: dict) -> dict:
        self.profile = self.users.update(self.profile["id"], UserUpdate.model_validate(body)).model_dump()
        return self.profile

    def create(self, body: dict) -> Chat:
        out = self.threads.create(self.profile["id"], ThreadCreate.model_validate(body))
        chat = Chat(thread_data(out), self.threads)
        with self.lock:
            self.chats[chat.id] = chat
        return chat

    def update(self, chat: Chat, body: dict) -> Chat:
        chat.apply(self.threads.update(chat.id, ThreadUpdate.model_validate(body)))
        return chat

    def delete(self, chat_id: str) -> None:
        self.threads.delete(chat_id)
        with self.lock:
            chat = self.chats.pop(chat_id)
        chat.stop()

    def state(self) -> dict:
        chats = sorted(self.chats.values(), key=lambda c: c.data["updated"], reverse=True)
        folders: list[str] = []
        for chat in chats:
            if chat.data["folder"] not in folders:
                folders.append(chat.data["folder"])
        saved = config.read_file()
        provider = self.profile["default_provider"] or saved.get("provider", "gemini")
        model = self.profile["default_model"] or saved.get("model") or config.PROVIDERS[provider]["models"][0]
        return {
            "profile": self.profile,
            "threads": [c.summary() for c in chats],
            "folders": folders[:8],
            "home": str(Path.home()),
            "models": [
                {
                    "provider": name,
                    "models": info["models"],
                    "ready": bool(config.api_key(name)) and name in IMPLEMENTED,
                    "supported": name in IMPLEMENTED,
                    "key": _mask(config.api_key(name)),
                    "key_url": info["key_url"],
                }
                for name, info in config.PROVIDERS.items()
            ],
            "default": {"provider": provider, "model": model},
        }


IMPLEMENTED = {"gemini"}  # providers.create() knows these


def _mask(key: str | None) -> str | None:
    """Enough of a key to recognise it; the full key never goes to the browser."""
    return None if not key else ("•" * 6 + key[-4:] if len(key) > 8 else "•" * 6)


def list_dirs(path: str | None) -> dict:
    base = Path(path or Path.home()).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"not a folder: {base}")
    try:
        dirs = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except PermissionError:
        dirs = []
    return {"path": str(base), "parent": str(base.parent) if base.parent != base else None, "dirs": dirs}


class Handler(BaseHTTPRequestHandler):
    store: Store

    def log_message(self, fmt: str, *args) -> None:  # quiet: only errors
        if args and str(args[1]).startswith(("4", "5")) and args[1] != "404":
            super().log_message(fmt, *args)

    # -- helpers --

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def local_only(self) -> bool:
        # Blocks other sites (and DNS rebinding) from driving the agent through your browser.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        ok_host = host in ("127.0.0.1", "localhost")
        ok_origin = origin is None or urlparse(origin).hostname in ("127.0.0.1", "localhost")
        if not (ok_host and ok_origin):
            self.send_json({"error": "forbidden"}, 403)
        return ok_host and ok_origin

    def fail(self, exc: Exception) -> None:
        """Turn a use case or validation error into a JSON error the UI shows."""
        if isinstance(exc, ValidationError):
            msg = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'body'}: {e['msg']}" for e in exc.errors())
            return self.send_json({"error": msg}, 400)
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400
        self.send_json({"error": str(exc)}, status)

    def chat(self, chat_id: str) -> Chat | None:
        chat = self.store.chats.get(chat_id)
        if chat is None:
            self.send_json({"error": "no such chat"}, 404)
        return chat

    # -- routes --

    def do_GET(self) -> None:
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        query = parse_qs(url.query)
        if parts[0] != "api":
            return self.static(url.path)
        if not self.local_only():
            return
        try:
            if parts[1:] == ["state"]:
                return self.send_json(self.store.state())
            if parts[1:] == ["fs"]:
                return self.send_json(list_dirs(query.get("path", [None])[0]))
            if len(parts) == 3 and parts[1] == "threads" and (chat := self.chat(parts[2])):
                if "after" in query:
                    chat.wait(int(query["after"][0]))
                return self.send_json(chat.full())
        except ValueError as exc:
            return self.fail(exc)
        if len(parts) != 3:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if not self.local_only():
            return
        try:
            data = self.body()
            if parts == ["api", "threads"]:
                return self.send_json(self.store.create(data).full(), 201)
            if len(parts) == 4 and parts[1] == "threads" and (chat := self.chat(parts[2])):
                action = parts[3]
                if action == "messages":
                    chat.send(data["text"])
                elif action == "answer":
                    if not chat.answer(data["id"], data["answer"]):
                        return self.send_json({"error": "nothing to answer"}, 409)
                elif action == "stop":
                    chat.stop()
                else:
                    return self.send_json({"error": "not found"}, 404)
                return self.send_json(chat.full())
        except (ValueError, KeyError) as exc:
            return self.fail(exc)
        if len(parts) != 4:
            self.send_json({"error": "not found"}, 404)

    def do_PATCH(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if not self.local_only():
            return
        if parts == ["api", "profile"]:
            try:
                return self.send_json(self.store.update_profile(self.body()))
            except ValueError as exc:
                return self.fail(exc)
        if len(parts) == 3 and parts[:2] == ["api", "keys"]:  # {"key": "..."}; empty removes it
            if parts[2] not in config.PROVIDERS:
                return self.send_json({"error": f"unknown provider: {parts[2]}"}, 400)
            key = str(self.body().get("key") or "").strip()
            config.save_key(parts[2], key or None)
            return self.send_json({"provider": parts[2], "key": _mask(config.api_key(parts[2]))})
        if len(parts) != 3 or parts[:2] != ["api", "threads"]:
            return self.send_json({"error": "not found"}, 404)
        if not (chat := self.chat(parts[2])):
            return
        try:
            self.send_json(self.store.update(chat, self.body()).full())
        except ValueError as exc:
            self.fail(exc)

    def do_DELETE(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if self.local_only() and len(parts) == 3 and self.chat(parts[2]):
            try:
                self.store.delete(parts[2])
            except ValueError as exc:
                return self.fail(exc)
            self.send_json({"ok": True})

    def static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        file = (STATIC / name).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            return self.send_json({"error": "not found"}, 404)
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
        body = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", types.get(file.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m kivu.web")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = parser.parse_args()
    try:
        engine = make_engine()
        init_db(engine)
    except DatabaseUnavailable as exc:
        raise SystemExit(str(exc)) from None
    sessions = make_sessions(engine)
    Handler.store = Store(UserUseCases(sessions), ThreadUseCases(sessions))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"kivu web on {url}  (ctrl-c to quit)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
