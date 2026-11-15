"""Human-in-the-loop: ask the user before an action changes something.

Answers: y = once, n = deny, a = always for this kind of action, this session.
auto=True (the --yes flag) approves everything without asking.
"""

from __future__ import annotations

from typing import Protocol


class Asker(Protocol):
    def approve(self, title: str, detail: str) -> str: ...  # returns "y", "n" or "a"


class Approver:
    def __init__(self, ui: Asker | None = None, auto: bool = False) -> None:
        self.ui = ui
        self.auto = auto
        self.always: set[str] = set()

    def check(self, key: str, title: str, detail: str = "") -> None:
        """Return if allowed; raise PermissionError if the user says no.

        key groups actions for "always", e.g. "write" or "shell:git push".
        """
        if self.auto or key in self.always:
            return
        answer = self.ui.approve(title, detail) if self.ui else "n"
        if answer == "a":
            self.always.add(key)
        elif answer != "y":
            raise PermissionError("the user declined this action")
