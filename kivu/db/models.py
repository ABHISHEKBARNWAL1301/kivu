"""Database tables, as SQLAlchemy ORM models.

- users:   who you are, your default model and theme. One row for now.
- threads: one per chat: the folder it works in, project label, model, approval mode.
- items:   everything shown in a chat, in order (messages, tool calls, approvals,
           plans, errors). The item itself is JSON; `type` is copied out so it can
           be queried and checked.

API keys are not stored here: they stay in keys.json, which the CLI reads too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import ItemType, Mode, Theme


def enum_column(enum: type[StrEnum], constraint: str) -> Enum:
    """VARCHAR plus a CHECK on the enum's values, not a native Postgres enum type,
    so adding a value later doesn't need ALTER TYPE. Stores values ("dark"), not
    member names ("DARK")."""
    return Enum(
        enum,
        name=constraint,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    # JSONB on Postgres, plain JSON elsewhere (SQLite in tests)
    type_annotation_map = {dict[str, Any]: JSON().with_variant(JSONB(), "postgresql")}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Timestamps:
    # Set in Python (not only by the server) so the values are readable right after a flush.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class User(Timestamps, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    default_provider: Mapped[str | None] = mapped_column(String(50))
    default_model: Mapped[str | None] = mapped_column(String(100))
    theme: Mapped[Theme] = mapped_column(
        enum_column(Theme, "users_theme_valid"),
        default=Theme.SYSTEM,
        server_default=Theme.SYSTEM.value,
    )

    threads: Mapped[list[Thread]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Thread(Timestamps, Base):
    __tablename__ = "threads"
    __table_args__ = (Index("threads_user_updated", "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    folder: Mapped[str] = mapped_column(Text)
    project: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    mode: Mapped[Mode] = mapped_column(
        enum_column(Mode, "threads_mode_valid"),
        default=Mode.ASK,
        server_default=Mode.ASK.value,
    )

    user: Mapped[User] = relationship(back_populates="threads")
    items: Mapped[list[Item]] = relationship(
        back_populates="thread",
        order_by="Item.seq",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Item(Base):
    __tablename__ = "items"

    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # position, from 0
    type: Mapped[ItemType] = mapped_column(enum_column(ItemType, "items_type_valid"))
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    thread: Mapped[Thread] = relationship(back_populates="items")
