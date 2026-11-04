"""The interface every provider implements.

Still to build:
- map vendor errors to a small set kivu understands (auth, rate limit, server, bad request)
- optional: streaming
"""

from __future__ import annotations

from typing import Protocol

from ..messages import Message, Response
from ..tools.registry import Tool


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[Message], tools: list[Tool]) -> Response: ...
