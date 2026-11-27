"""Connecting: the engine (a connection pool) and the session factory.

By default the data lives in SQLite at ~/.config/kivu/kivu.db: nothing to install or start.
Set KIVU_DATABASE_URL (in the environment or a .env file, see .env.example) to use
something else, e.g. the Postgres in docker-compose.yml.
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .. import config
from .models import Base

DEFAULT_PATH = config.CONFIG_PATH.parent / "kivu.db"


class DatabaseUnavailable(RuntimeError):
    pass


def database_url() -> str:
    # A real environment variable wins over .env (override=False).
    load_dotenv(find_dotenv(usecwd=True), override=False)
    url = os.getenv("KIVU_DATABASE_URL")
    if not url:
        DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DEFAULT_PATH}"
    if url.startswith(("postgresql://", "postgres://")):  # use the psycopg 3 driver
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")  # off by default; needed for ON DELETE CASCADE
    cur.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
    cur.execute("PRAGMA busy_timeout = 5000")  # wait for a lock instead of failing
    cur.close()


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        engine = create_engine(url)
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    kwargs: dict = {"pool_pre_ping": True}  # survives a container restart
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": 5}
    return create_engine(url, **kwargs)


def make_sessions(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after their use case commits
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Check the connection and create missing tables. Doesn't alter existing ones."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        hint = (
            "start it with `docker compose up -d`"
            if engine.dialect.name == "postgresql"
            else "check the path and permissions"
        )
        raise DatabaseUnavailable(
            f"can't reach the database at "
            f"{engine.url.render_as_string(hide_password=True)}; {hint}"
        ) from exc
    Base.metadata.create_all(engine)
