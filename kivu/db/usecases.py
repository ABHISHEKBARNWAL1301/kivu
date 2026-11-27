"""Use cases: every way the app saves or reads a user or a chat.

Each method is one transaction: it opens a session, applies the rules, commits,
and returns a schema (never a live ORM object). Rules live here, not in the
web server or the repositories:

- a new chat's folder must exist; its project defaults to the folder's name
- a chat's folder can't change once it has messages (moving it would also move the project)
- the first user message becomes the chat's title
- a user's default model must belong to their default provider
- adding or changing an item bumps the chat's updated_at, so the newest chat is listed first
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .. import config
from .enums import ItemType, Mode
from .models import Item, Thread, User, utcnow
from .repositories import ThreadRepository, UserRepository
from .schemas import (
    ItemAdded,
    ItemIn,
    ThreadCreate,
    ThreadOut,
    ThreadUpdate,
    UserOut,
    UserUpdate,
)

NEW_TITLE = "New chat"
TITLE_CHARS = 60


class DomainError(ValueError):
    """A request the rules don't allow. The web server maps the subclasses to 400/404/409."""


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


class Invalid(DomainError):
    pass


class _UseCases:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @contextmanager
    def _tx(self) -> Iterator[Session]:
        """One transaction: commit on success, roll back on any error."""
        with self.sessions.begin() as session:
            yield session


# -- users --


class UserUseCases(_UseCases):
    def get_or_create_default(self) -> UserOut:
        """The local user. Made on first run; kivu has one user until it has sign-in."""
        with self._tx() as s:
            users = UserRepository(s)
            user = users.first() or users.add(User())
            return UserOut.model_validate(user)

    def get(self, user_id: int) -> UserOut:
        with self._tx() as s:
            return UserOut.model_validate(self._user(s, user_id))

    def update(self, user_id: int, changes: UserUpdate) -> UserOut:
        with self._tx() as s:
            user = self._user(s, user_id)
            fields = changes.model_dump(exclude_none=True)
            provider = fields.get("default_provider", user.default_provider)
            model = fields.get("default_model", user.default_model)
            if model and provider and model not in config.PROVIDERS[provider]["models"]:
                raise Invalid(f"{model} isn't a {provider} model")
            for key, value in fields.items():
                setattr(user, key, value)
            s.flush()
            return UserOut.model_validate(user)

    @staticmethod
    def _user(s: Session, user_id: int) -> User:
        user = UserRepository(s).get(user_id)
        if user is None:
            raise NotFound(f"no user {user_id}")
        return user


# -- threads --


class ThreadUseCases(_UseCases):
    def create(self, user_id: int, new: ThreadCreate) -> ThreadOut:
        folder = _folder(new.folder)
        with self._tx() as s:
            if UserRepository(s).get(user_id) is None:
                raise NotFound(f"no user {user_id}")
            thread = ThreadRepository(s).add(
                Thread(
                    id=uuid.uuid4().hex[:12],
                    user_id=user_id,
                    title=NEW_TITLE,
                    folder=str(folder),
                    project=folder.name or str(folder),
                    provider=new.provider,
                    model=new.model,
                    mode=new.mode,
                )
            )
            return _out(thread, with_items=False)

    def get(self, thread_id: str) -> ThreadOut:
        with self._tx() as s:
            return _out(self._thread(s, thread_id, with_items=True))

    def list_for_user(self, user_id: int) -> list[ThreadOut]:
        """Every chat of the user with its items, newest first."""
        with self._tx() as s:
            return [_out(t) for t in ThreadRepository(s).list_for_user(user_id)]

    def update(self, thread_id: str, changes: ThreadUpdate) -> ThreadOut:
        with self._tx() as s:
            threads = ThreadRepository(s)
            thread = self._thread(s, thread_id)
            fields = changes.model_dump(exclude_none=True)
            if "folder" in fields:
                if threads.count_items(thread_id):
                    raise Conflict("a chat's folder can't change once it has messages")
                folder = _folder(fields.pop("folder"))
                thread.folder = str(folder)
                fields.setdefault("project", folder.name or str(folder))
            for key, value in fields.items():
                setattr(thread, key, value)
            s.flush()
            return _out(thread, with_items=False)

    def delete(self, thread_id: str) -> None:
        with self._tx() as s:
            ThreadRepository(s).delete(self._thread(s, thread_id))  # its items go too (cascade)

    def add_item(self, thread_id: str, item: dict[str, Any]) -> ItemAdded:
        """Append to the end of the chat. The first user message names the chat."""
        checked = ItemIn.model_validate(item).model_dump(mode="json")
        with self._tx() as s:
            threads = ThreadRepository(s)
            thread = self._thread(s, thread_id)
            seq = threads.next_seq(thread_id)
            if seq == 0 and checked["type"] == ItemType.USER and thread.title == NEW_TITLE:
                thread.title = _title(checked.get("text", "")) or NEW_TITLE
            threads.add_item(Item(thread_id=thread_id, seq=seq, type=checked["type"], data=checked))
            thread.updated_at = utcnow()
            return ItemAdded(seq=seq, title=thread.title)

    def update_item(self, thread_id: str, seq: int, item: dict[str, Any]) -> None:
        """Replace an item in place, e.g. an approval once it has its answer. Its type can't change."""
        checked = ItemIn.model_validate(item).model_dump(mode="json")
        with self._tx() as s:
            threads = ThreadRepository(s)
            row = threads.get_item(thread_id, seq)
            if row is None:
                raise NotFound(f"no item {seq} in chat {thread_id}")
            if row.type != checked["type"]:
                raise Invalid(f"item {seq} is a {row.type}, not a {checked['type']}")
            row.data = checked
            self._thread(s, thread_id).updated_at = utcnow()

    def import_legacy_json(self, user_id: int, folder: Path) -> int:
        """Load chats the first web UI saved as JSON files. Runs only while there are no chats."""
        if not folder.is_dir():
            return 0
        with self._tx() as s:
            if ThreadRepository(s).any():
                return 0
        count = 0
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                with self._tx() as s:
                    threads = ThreadRepository(s)
                    threads.add(
                        Thread(
                            id=data["id"],
                            user_id=user_id,
                            title=data.get("title") or NEW_TITLE,
                            folder=data["folder"],
                            project=data.get("project") or Path(data["folder"]).name,
                            provider=data["provider"],
                            model=data["model"],
                            mode=Mode(data.get("mode", Mode.ASK)),
                        )
                    )
                    for seq, raw in enumerate(data.get("items", [])):
                        item = ItemIn.model_validate(raw).model_dump(mode="json")
                        threads.add_item(Item(thread_id=data["id"], seq=seq, type=item["type"], data=item))
                count += 1
            except (ValueError, KeyError, SQLAlchemyError) as exc:
                print(f"skipping {path.name}: {exc}")
        return count

    @staticmethod
    def _thread(s: Session, thread_id: str, with_items: bool = False) -> Thread:
        thread = ThreadRepository(s).get(thread_id, with_items=with_items)
        if thread is None:
            raise NotFound(f"no chat {thread_id}")
        return thread


def _folder(path: str) -> Path:
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise Invalid(f"not a folder: {folder}")
    return folder


def _title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:TITLE_CHARS]


def _out(thread: Thread, with_items: bool = True) -> ThreadOut:
    """with_items=False skips loading the items (a new chat has none; an update doesn't need them)."""
    return ThreadOut(
        id=thread.id,
        user_id=thread.user_id,
        title=thread.title,
        folder=thread.folder,
        project=thread.project,
        provider=thread.provider,
        model=thread.model,
        mode=thread.mode,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        items=[i.data for i in thread.items] if with_items else [],
    )
