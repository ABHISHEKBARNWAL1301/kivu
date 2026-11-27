"""Data access: read and write rows inside a session the caller owns. No rules here
(those live in usecases.py), and no commits (the use case commits once at the end).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import Item, Thread, User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, user_id: int) -> User | None:
        return self.s.get(User, user_id)

    def first(self) -> User | None:
        return self.s.scalars(select(User).order_by(User.id).limit(1)).first()

    def add(self, user: User) -> User:
        self.s.add(user)
        self.s.flush()  # fills in id and defaults
        return user


class ThreadRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, thread_id: str, with_items: bool = False) -> Thread | None:
        options = [selectinload(Thread.items)] if with_items else []
        return self.s.get(Thread, thread_id, options=options)

    def list_for_user(self, user_id: int) -> Sequence[Thread]:
        query = (
            select(Thread)
            .where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc())
            .options(selectinload(Thread.items))
        )
        return self.s.scalars(query).all()

    def any(self) -> bool:
        return self.s.scalars(select(Thread.id).limit(1)).first() is not None

    def add(self, thread: Thread) -> Thread:
        self.s.add(thread)
        self.s.flush()
        return thread

    def delete(self, thread: Thread) -> None:
        self.s.delete(thread)

    # -- items --

    def count_items(self, thread_id: str) -> int:
        return self.s.scalar(select(func.count()).select_from(Item).where(Item.thread_id == thread_id)) or 0

    def next_seq(self, thread_id: str) -> int:
        return self.s.scalar(select(func.coalesce(func.max(Item.seq) + 1, 0)).where(Item.thread_id == thread_id)) or 0

    def get_item(self, thread_id: str, seq: int) -> Item | None:
        return self.s.get(Item, (thread_id, seq))

    def add_item(self, item: Item) -> Item:
        self.s.add(item)
        self.s.flush()
        return item
