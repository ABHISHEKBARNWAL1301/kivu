"""Postgres storage: the user's profile and chat history.

Start the database with `docker compose up -d` (see docker-compose.yml).
KIVU_DATABASE_URL overrides where it connects.

Tables (made on first connect):
- profiles: who you are, your default model and theme. One row for now.
API keys are not here: they stay in keys.json, which the CLI reads too.
- threads:  one per chat: folder, project, model, approval mode.
- items:    everything shown in a chat, in order (messages, tool calls,
            approvals, plans), as JSON.

One connection, shared by the web server's threads behind a lock. Fine for a
single local user; swap in a pool if that changes.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DEFAULT_URL = "postgresql://kivu:kivu@127.0.0.1:5433/kivu"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id               serial PRIMARY KEY,
    name             text NOT NULL DEFAULT '',
    email            text NOT NULL DEFAULT '',
    default_provider text,
    default_model    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS theme text NOT NULL DEFAULT 'system';

CREATE TABLE IF NOT EXISTS threads (
    id         text PRIMARY KEY,
    profile_id integer NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    title      text NOT NULL,
    folder     text NOT NULL,
    project    text NOT NULL,
    provider   text NOT NULL,
    model      text NOT NULL,
    mode       text NOT NULL DEFAULT 'ask',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS threads_profile_updated ON threads (profile_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS items (
    thread_id  text NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    type       text NOT NULL,
    data       jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, seq)
);
"""

THREAD_COLS = ("id", "title", "folder", "project", "provider", "model", "mode")
PROFILE_COLS = ("name", "email", "default_provider", "default_model", "theme")


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.getenv("KIVU_DATABASE_URL") or DEFAULT_URL
        self.lock = threading.RLock()
        self.conn: psycopg.Connection | None = None
        with self.lock:
            self._conn().execute(SCHEMA)

    def _conn(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            try:
                self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row, connect_timeout=5)
            except psycopg.OperationalError as exc:
                raise SystemExit(f"can't reach Postgres at {self.url}: start it with `docker compose up -d`\n{exc}") from None
        return self.conn

    def _run(self, sql: str, params: Any = None) -> list[dict]:
        with self.lock:
            try:
                cur = self._conn().execute(sql, params)
            except psycopg.OperationalError:  # dropped connection (container restart): reconnect once
                self.conn = None
                cur = self._conn().execute(sql, params)
            return cur.fetchall() if cur.description else []

    # -- profile --

    def profile(self) -> dict:
        """The local profile; made on first use."""
        rows = self._run("SELECT * FROM profiles ORDER BY id LIMIT 1")
        if not rows:
            rows = self._run("INSERT INTO profiles DEFAULT VALUES RETURNING *")
        return _profile(rows[0])

    def update_profile(self, profile_id: int, fields: dict) -> dict:
        fields = {k: v for k, v in fields.items() if k in PROFILE_COLS}
        if fields:
            sets = ", ".join(f"{k} = %({k})s" for k in fields)
            self._run(f"UPDATE profiles SET {sets}, updated_at = now() WHERE id = %(id)s", fields | {"id": profile_id})
        return _profile(self._run("SELECT * FROM profiles WHERE id = %s", (profile_id,))[0])

    # -- threads and their items --

    def threads(self, profile_id: int) -> list[dict]:
        """Every thread with its items, in the shape the web server keeps in memory."""
        threads = self._run(
            "SELECT *, extract(epoch FROM created_at) AS created, extract(epoch FROM updated_at) AS updated "
            "FROM threads WHERE profile_id = %s",
            (profile_id,),
        )
        items: dict[str, list] = {}
        for row in self._run(
            "SELECT i.thread_id, i.data FROM items i JOIN threads t ON t.id = i.thread_id "
            "WHERE t.profile_id = %s ORDER BY i.thread_id, i.seq",
            (profile_id,),
        ):
            items.setdefault(row["thread_id"], []).append(row["data"])
        return [
            {k: t[k] for k in THREAD_COLS} | {"created": float(t["created"]), "updated": float(t["updated"]), "items": items.get(t["id"], [])}
            for t in threads
        ]

    def insert_thread(self, profile_id: int, data: dict) -> None:
        self._run(
            "INSERT INTO threads (id, profile_id, title, folder, project, provider, model, mode, created_at, updated_at) "
            "VALUES (%(id)s, %(profile_id)s, %(title)s, %(folder)s, %(project)s, %(provider)s, %(model)s, %(mode)s, "
            "to_timestamp(%(created)s), to_timestamp(%(updated)s))",
            {k: data[k] for k in THREAD_COLS} | {"profile_id": profile_id, "created": data["created"], "updated": data["updated"]},
        )

    def update_thread(self, data: dict) -> None:
        self._run(
            "UPDATE threads SET title = %(title)s, folder = %(folder)s, project = %(project)s, provider = %(provider)s, "
            "model = %(model)s, mode = %(mode)s, updated_at = to_timestamp(%(updated)s) WHERE id = %(id)s",
            {k: data[k] for k in THREAD_COLS} | {"updated": data["updated"]},
        )

    def delete_thread(self, thread_id: str) -> None:
        self._run("DELETE FROM threads WHERE id = %s", (thread_id,))  # items go with it (cascade)

    def put_item(self, thread_id: str, seq: int, item: dict) -> None:
        """Add an item, or replace it (an approval that just got its answer)."""
        self._run(
            "INSERT INTO items (thread_id, seq, type, data) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (thread_id, seq) DO UPDATE SET data = EXCLUDED.data",
            (thread_id, seq, item["type"], Jsonb(item)),
        )

    def import_json(self, profile_id: int, folder: Path) -> int:
        """Load chats the first web UI saved as JSON files, once. Returns how many."""
        if not folder.is_dir() or self._run("SELECT 1 FROM threads LIMIT 1"):
            return 0
        count = 0
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                data.setdefault("mode", "ask")
                self.insert_thread(profile_id, data)
                for seq, item in enumerate(data.get("items", [])):
                    self.put_item(data["id"], seq, item)
                count += 1
            except (ValueError, KeyError, psycopg.Error) as exc:
                print(f"skipping {path.name}: {exc}")
        return count


def _profile(row: dict) -> dict:
    return {"id": row["id"]} | {k: row[k] for k in PROFILE_COLS}
