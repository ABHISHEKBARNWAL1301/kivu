"""Storage for users and chats: Postgres through SQLAlchemy.

Layers, from the outside in:
- usecases.py:     what the app calls. One transaction each; the rules live here.
- schemas.py:      Pydantic inputs (…Create / …Update) and outputs (…Out).
- repositories.py: queries, inside a session the use case owns.
- models.py:       the tables.
- enums.py:        fixed choices (Theme, Mode, ItemType), shared by models and schemas.
- engine.py:       connection pool, sessions, creating tables.

    engine = make_engine(); init_db(engine)
    sessions = make_sessions(engine)
    users, threads = UserUseCases(sessions), ThreadUseCases(sessions)
"""

from .engine import DatabaseUnavailable, init_db, make_engine, make_sessions
from .enums import ItemType, Mode, Theme
from .usecases import (
    Conflict,
    DomainError,
    Invalid,
    NotFound,
    ThreadUseCases,
    UserUseCases,
)

__all__ = [
    "Conflict",
    "DatabaseUnavailable",
    "DomainError",
    "Invalid",
    "ItemType",
    "Mode",
    "NotFound",
    "Theme",
    "ThreadUseCases",
    "UserUseCases",
    "init_db",
    "make_engine",
    "make_sessions",
]
