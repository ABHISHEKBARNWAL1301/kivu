"""Fixed choices, shared by the tables (models.py) and the inputs/outputs (schemas.py).

StrEnum, so a member equals its value ("dark" == Theme.DARK) and goes to JSON as a plain string.
"""

from __future__ import annotations

from enum import StrEnum


class Theme(StrEnum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class Mode(StrEnum):
    """How a chat handles edits and commands."""

    ASK = "ask"  # ask before each one
    AUTO = "auto"  # approve without asking


class ItemType(StrEnum):
    """What one entry in a chat is."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    APPROVAL = "approval"
    QUESTION = "question"
    PLAN = "plan"
    ERROR = "error"
