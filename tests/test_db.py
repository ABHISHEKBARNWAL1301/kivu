"""Use cases in kivu.db, run against SQLite so they need no Postgres."""

from __future__ import annotations

import json
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kivu.db import Conflict, Invalid, ItemType, Mode, NotFound, Theme, ThreadUseCases, UserUseCases, init_db, make_engine, make_sessions
from kivu.db.schemas import ThreadCreate, ThreadUpdate, UserUpdate


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'kivu.db'}")  # turns on foreign keys
    init_db(engine)
    sessions = make_sessions(engine)
    users, threads = UserUseCases(sessions), ThreadUseCases(sessions)
    return users, threads, users.get_or_create_default()


def new_chat(threads, user, folder, **kw):
    return threads.create(user.id, ThreadCreate(folder=str(folder), provider="gemini", model="gemini-flash-latest", **kw))


# -- users --


def test_default_user_is_made_once(db):
    users, _, user = db
    assert users.get_or_create_default().id == user.id
    assert (user.name, user.theme) == ("", "system")


def test_update_user_changes_only_given_fields(db):
    users, _, user = db
    users.update(user.id, UserUpdate(name="  Ada  ", email="ada@example.com"))
    updated = users.update(user.id, UserUpdate(theme="light"))
    assert (updated.name, updated.email, updated.theme) == ("Ada", "ada@example.com", "light")


@pytest.mark.parametrize(
    "fields",
    [{"theme": "blue"}, {"email": "not-an-email"}, {"default_provider": "nope"}, {"nickname": "x"}, {"name": "x" * 101}],
)
def test_bad_user_input_is_rejected(fields):
    with pytest.raises(ValidationError):
        UserUpdate.model_validate(fields)


def test_default_model_must_belong_to_provider(db):
    users, _, user = db
    with pytest.raises(Invalid):
        users.update(user.id, UserUpdate(default_provider="openai", default_model="gemini-pro-latest"))
    ok = users.update(user.id, UserUpdate(default_provider="gemini", default_model="gemini-pro-latest"))
    assert ok.default_model == "gemini-pro-latest"


def test_unknown_user(db):
    users, threads, _ = db
    with pytest.raises(NotFound):
        users.update(999, UserUpdate(name="x"))


# -- threads --


def test_create_thread_takes_project_from_folder(db, tmp_path):
    _, threads, user = db
    folder = tmp_path / "myproj"
    folder.mkdir()
    chat = new_chat(threads, user, folder)
    assert (chat.title, chat.project, chat.folder, chat.mode, chat.items) == ("New chat", "myproj", str(folder), "ask", [])


def test_create_thread_needs_a_real_folder(db, tmp_path):
    _, threads, user = db
    with pytest.raises(Invalid):
        new_chat(threads, user, tmp_path / "missing")


def test_first_user_message_names_the_chat(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    first = threads.add_item(chat.id, {"type": "user", "text": "\n  Fix the login bug\nthen run tests"})
    second = threads.add_item(chat.id, {"type": "user", "text": "something else"})
    assert (first.seq, second.seq) == (0, 1)
    assert first.title == second.title == "Fix the login bug"


def test_items_keep_order_and_fields(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    threads.add_item(chat.id, {"type": "user", "text": "hi"})
    threads.add_item(chat.id, {"type": "tool", "name": "read_file", "args": {"path": "a.py"}, "output": "x", "error": False})
    threads.add_item(chat.id, {"type": "assistant", "text": "done"})
    items = threads.get(chat.id).items
    assert [i["type"] for i in items] == ["user", "tool", "assistant"]
    assert items[1]["args"] == {"path": "a.py"}


def test_unknown_item_type_is_rejected(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    with pytest.raises(ValidationError):
        threads.add_item(chat.id, {"type": "banana"})


def test_answering_an_approval_updates_it_in_place(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    item = {"type": "approval", "id": "a1", "title": "edit x", "detail": "", "answer": None}
    seq = threads.add_item(chat.id, item).seq
    threads.update_item(chat.id, seq, item | {"answer": "y"})
    assert threads.get(chat.id).items == [item | {"answer": "y"}]
    with pytest.raises(Invalid):
        threads.update_item(chat.id, seq, {"type": "user", "text": "sneaky"})
    with pytest.raises(NotFound):
        threads.update_item(chat.id, 5, item)


def test_folder_locks_once_chat_has_messages(db, tmp_path):
    _, threads, user = db
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    chat = new_chat(threads, user, a)
    moved = threads.update(chat.id, ThreadUpdate(folder=str(b)))
    assert (moved.folder, moved.project) == (str(b), "b")
    threads.add_item(chat.id, {"type": "user", "text": "hi"})
    with pytest.raises(Conflict):
        threads.update(chat.id, ThreadUpdate(folder=str(a)))


def test_update_thread_fields(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    out = threads.update(chat.id, ThreadUpdate(title="Renamed", project="Work", mode="auto", model="gemini-pro-latest"))
    assert (out.title, out.project, out.mode, out.model) == ("Renamed", "Work", "auto", "gemini-pro-latest")
    with pytest.raises(ValidationError):
        ThreadUpdate.model_validate({"mode": "yolo"})
    with pytest.raises(ValidationError):
        ThreadUpdate.model_validate({"user_id": 2})  # can't move a chat to someone else


def test_newest_chat_is_listed_first(db, tmp_path):
    _, threads, user = db
    old = new_chat(threads, user, tmp_path)
    new = new_chat(threads, user, tmp_path)
    time.sleep(0.01)
    threads.add_item(old.id, {"type": "user", "text": "bump"})  # activity makes it newest
    assert [t.id for t in threads.list_for_user(user.id)] == [old.id, new.id]


def test_delete_removes_items_too(db, tmp_path):
    _, threads, user = db
    chat = new_chat(threads, user, tmp_path)
    threads.add_item(chat.id, {"type": "user", "text": "hi"})
    threads.delete(chat.id)
    with pytest.raises(NotFound):
        threads.get(chat.id)
    with pytest.raises(NotFound):
        threads.add_item(chat.id, {"type": "user", "text": "hi"})


def test_import_legacy_json_once(db, tmp_path):
    _, threads, user = db
    legacy = tmp_path / "threads"
    legacy.mkdir()
    (legacy / "abc.json").write_text(
        json.dumps(
            {
                "id": "abc",
                "title": "Old chat",
                "folder": str(tmp_path),
                "project": "old",
                "provider": "gemini",
                "model": "gemini-flash-latest",
                "items": [{"type": "user", "text": "hi"}, {"type": "assistant", "text": "hello"}],
            }
        )
    )
    (legacy / "broken.json").write_text("{not json")
    assert threads.import_legacy_json(user.id, legacy) == 1
    assert [i["text"] for i in threads.get("abc").items] == ["hi", "hello"]
    assert threads.import_legacy_json(user.id, legacy) == 0  # not twice


# -- enums --


def test_enums_are_stored_as_their_values(db, tmp_path):
    users, threads, user = db
    users.update(user.id, UserUpdate(theme=Theme.DARK))
    chat = new_chat(threads, user, tmp_path, mode=Mode.AUTO)
    threads.add_item(chat.id, {"type": ItemType.USER, "text": "hi"})
    with threads.sessions() as s:
        row = s.execute(text("SELECT u.theme, t.mode, i.type FROM users u JOIN threads t ON t.user_id = u.id JOIN items i ON i.thread_id = t.id")).one()
    assert tuple(row) == ("dark", "auto", "user")
    assert threads.get(chat.id).mode is Mode.AUTO
    assert users.get(user.id).theme == "dark"  # StrEnum: equal to its value


def test_database_rejects_values_outside_the_enum(db):
    _, threads, user = db
    with pytest.raises(IntegrityError), threads.sessions.begin() as s:
        s.execute(text("UPDATE users SET theme = 'blue' WHERE id = :id"), {"id": user.id})
