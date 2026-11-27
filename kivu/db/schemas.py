"""Pydantic schemas: what goes into a use case (…Create / …Update) and what comes out (…Out).

Inputs are strict (unknown fields are rejected, strings are trimmed). Outputs are
built from the ORM models, so callers never hold a live database object.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import config
from .enums import ItemType, Mode, Theme

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckProvider(Input):
    """Mixin: a `provider` / `default_provider` field must be one config.PROVIDERS knows."""

    @field_validator("provider", "default_provider", check_fields=False)
    @classmethod
    def _known_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in config.PROVIDERS:
            raise ValueError(f"unknown provider {value!r} (known: {', '.join(config.PROVIDERS)})")
        return value


# -- users --


class UserUpdate(CheckProvider):
    """Fields left out (None) are not changed."""

    name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=255)
    default_provider: str | None = None
    default_model: str | None = Field(None, max_length=100)
    theme: Theme | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value and not EMAIL.match(value):
            raise ValueError("not an email address")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    default_provider: str | None
    default_model: str | None
    theme: Theme


# -- threads --


class ThreadCreate(CheckProvider):
    folder: str = Field(min_length=1)
    provider: str
    model: str = Field(min_length=1, max_length=100)
    mode: Mode = Mode.ASK


class ThreadUpdate(CheckProvider):
    """Fields left out (None) are not changed. folder can only change while the chat is empty."""

    title: str | None = Field(None, min_length=1, max_length=200)
    project: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = None
    model: str | None = Field(None, min_length=1, max_length=100)
    mode: Mode | None = None
    folder: str | None = Field(None, min_length=1)


class ItemIn(BaseModel):
    """One chat item. Only `type` is fixed; the rest depends on the type (text, args, answer…)."""

    model_config = ConfigDict(extra="allow")

    type: ItemType


class ThreadOut(BaseModel):
    id: str
    user_id: int
    title: str
    folder: str
    project: str
    provider: str
    model: str
    mode: Mode
    created_at: datetime
    updated_at: datetime
    items: list[dict[str, Any]] = []


class ItemAdded(BaseModel):
    seq: int
    title: str  # the thread's title, which the first message sets
